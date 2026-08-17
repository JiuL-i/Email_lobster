#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件龙虾 (Mail Lobster) - IMAP 转发版 v2
功能：
  - 守护进程模式：全天持续监控，每 15 分钟检查一次
  - AI 智能分析：DeepSeek（可切换）分析邮件内容（两级分类 + 平台识别）
  - 即时推送：仅 0级 / 1级 立即通知
  - 周期报表：每个总结周期一个 Excel，追加写入
  - 定时总结：每天 4 次（07:30 / 11:30 / 17:00 / 21:00）

依赖：pip install requests openpyxl openai
"""

import json
import os
import re
import socket
import ssl
import email
import email.message
import email.utils
import email.header
import logging
import sys
import time
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import imaplib
import requests
from logging.handlers import RotatingFileHandler
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill, Font, Alignment

from ai_analyzer import AIAnalyzer
from single_instance import (
    AlreadyRunningError,
    LockUnavailableError,
    SingleInstanceLock,
    lock_path_for_script,
    requires_instance_lock,
)
from seen_emails_store import SeenEmailsStore

# ===== 配置日志 =====
# 日志轮转：单个文件最大 5MB，保留 3 个备份（共 ~15MB）
_rotating_handler = RotatingFileHandler(
    'mail_lobster_imap.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        _rotating_handler
    ]
)
logger = logging.getLogger(__name__)

CONFIG_FILE = 'config_imap_forward.json'


class MailLobsterIMAP:
    """邮件龙虾 IMAP 转发版 v2 — 守护进程 + AI + 定时总结"""

    def __init__(self, config_path: str = CONFIG_FILE):
        self.config = self._load_config(config_path)
        self.ai = AIAnalyzer(self.config.get('ai', {}))

        # Persistent seen-email store (tracks which messages have been
        # fetched, pushed in summaries, and notified via private channels).
        # Resolve the state directory in this priority order:
        #   1. MAIL_LOBSTER_APP_ROOT env var (set by the GUI launcher when
        #      spawning the daemon subprocess, so the EXE's state dir is
        #      known regardless of cwd or __file__ weirdness in PyInstaller)
        #   2. CWD-relative state/ (works when the daemon is started from
        #      the installation directory)
        #   3. Path(__file__).parent / state/ (works when running from
        #      source)
        try:
            from pathlib import Path as _Path
            import os as _os
            _app_root = _os.environ.get("MAIL_LOBSTER_APP_ROOT")
            if _app_root:
                _state_dir = _Path(_app_root) / "state"
            elif _Path("state").exists():
                _state_dir = _Path("state").resolve()
            else:
                _state_dir = _Path(__file__).resolve().parent / "state"
            self.seen_store = SeenEmailsStore(_state_dir / "seen_emails.jsonl")
        except Exception:
            self.seen_store = None

        # 级别 0-6 对应的 Excel 行颜色
        self.level_colors = {
            0: PatternFill(start_color="FF4444", end_color="FF4444", fill_type="solid"),  # 深红 - 紧急店铺问题
            1: PatternFill(start_color="FF8C00", end_color="FF8C00", fill_type="solid"),  # 橙色 - 重要店铺问题
            2: PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid"),  # 金色 - 链接/日常业务
            3: PatternFill(start_color="FFFFCC", end_color="FFFFCC", fill_type="solid"),  # 浅黄 - 客服来信
            4: PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),  # 浅绿 - 日常管理
            5: PatternFill(start_color="D6EAF8", end_color="D6EAF8", fill_type="solid"),  # 浅蓝 - 系统通知
            6: PatternFill(start_color="E8DAEF", end_color="E8DAEF", fill_type="solid"),  # 浅紫 - 活动/广告
        }

        self.level_names = {
            0: '0级-紧急', 1: '1级-重要', 2: '2级-日常', 3: '3级-客服',
            4: '4级-管理', 5: '5级-通知', 6: '6级-无关'
        }

        # 通知推送中的级别颜色（HTML用）
        self.level_html_colors = {
            0: {'bg': '#FFE0E0', 'border': '#CC0000', 'text': '#CC0000', 'emoji': '🔴', 'label': '紧急'},
            1: {'bg': '#FFF0E0', 'border': '#FF6600', 'text': '#FF6600', 'emoji': '🟠', 'label': '重要'},
            2: {'bg': '#FFF8E0', 'border': '#CC9900', 'text': '#CC9900', 'emoji': '🟡', 'label': '日常'},
            3: {'bg': '#E8F4FD', 'border': '#3399CC', 'text': '#3399CC', 'emoji': '🔵', 'label': '客服'},
            4: {'bg': '#E8F5E9', 'border': '#4CAF50', 'text': '#4CAF50', 'emoji': '🟢', 'label': '管理'},
            5: {'bg': '#ECEFF1', 'border': '#78909C', 'text': '#78909C', 'emoji': '⚪', 'label': '通知'},
            6: {'bg': '#F3E5F5', 'border': '#9C27B0', 'text': '#9C27B0', 'emoji': '🟣', 'label': '无关'},
        }

        # 平台名称映射
        self.platform_names = {
            'WMS': '沃尔玛', 'AMZ': '亚马逊', 'TEM': 'Temu',
            'SHO': 'Shopee', 'TKB': 'TikTok', 'OTH': '其他'
        }

        # 平台代码规范化：所有别名统一到标准代码（WMT→WMS 等）
        self.platform_normalize = {
            'WMT': 'WMS', 'WALMART': 'WMS',
            'AMAZON': 'AMZ',
            'TEMU': 'TEM',
            'SHOPEE': 'SHO',
            'TIKTOK': 'TKB',
        }

        # ★ 发件人域名 → 平台映射（用于从原始发件人判断平台归属）
        self.sender_platform_map = {
            # 亚马逊
            'amazon.com': 'AMZ', 'amazon.co.uk': 'AMZ', 'amazon.de': 'AMZ',
            'amazon.co.jp': 'AMZ', 'amazon.ca': 'AMZ', 'amazon.com.au': 'AMZ',
            'amazon.it': 'AMZ', 'amazon.es': 'AMZ', 'amazon.fr': 'AMZ',
            'amazon.ae': 'AMZ', 'amazon.sg': 'AMZ', 'amazon.in': 'AMZ',
            'amazon.com.mx': 'AMZ', 'amazon.com.br': 'AMZ', 'amazon.nl': 'AMZ',
            'amazon.pl': 'AMZ', 'amazon.se': 'AMZ', 'amazon.com.tr': 'AMZ',
            'sellercentral.amazon': 'AMZ',
            # 沃尔玛
            'walmart.com': 'WMS', 'walmart.ca': 'WMS',
            'seller.walmart.com': 'WMS', 'marketplace.walmart.com': 'WMS',
            # Temu
            'temu.com': 'TEM', 'temumall.com': 'TEM',
            # Shopee
            'shopee.com': 'SHO', 'shopee.cn': 'SHO', 'shopee.sg': 'SHO',
            'shopee.com.br': 'SHO', 'shopee.ph': 'SHO', 'shopee.tw': 'SHO',
            # TikTok
            'tiktok.com': 'TKB', 'tiktokshop.com': 'TKB',
            # OZON
            'ozon.ru': 'OZN', 'ozon.com': 'OZN',
            'ozon-seller.com': 'OZN', 'seller.ozon.com': 'OZN',
            'global.ozon.com': 'OZN', 'mail.ozon.com': 'OZN',
            'no-reply.ozon.com': 'OZN',
        }

        # ★ 物流/服务商域名和关键词 — 出现这些说明是服务商邮件，不代表新平台
        self.logistics_domains = {
            'yunexpress.com', 'ytexpress.com',                    # 云途
            'jtbchina.com', 'jtexpress.com', 'jtexpress',        # 极兔
            'sf-express.com', 'sfexpress.com',                   # 顺丰国际
            'yanwen.com', 'yanwen',                              # 燕文
            '4px.com', '递四方',                                 # 递四方
            'winit.com',                                         # 万邑通
            'chukou1.com',                                       # 出口易
            'cjlogistics.com',                                   # CJ 大韩通运
            'postnl.nl', 'postnl',                               # PostNL
            'royalmail.com', 'royal mail',                       # Royal Mail
            'usps.com', 'usps',                                  # USPS
            'fedex.com', 'fedex',                                # FedEx
            'ups.com', 'ups',                                    # UPS
            'dhl.com', 'dhl',                                    # DHL
        }

        # ★ 真正的电商竞品平台代码（只有这些才走 other_platform 路由）
        self.ecommerce_platforms = {
            'TEM', 'SHO', 'TKB', 'OZN',                         # Temu/Shopee/TikTok/OZON
        }

        # 周期追踪
        self.current_cycle_emails: List[Dict] = []   # 当前周期收集的所有邮件
        self.current_cycle_type: str = ''             # 当前周期对应的总结时间（如 '07:30'）
        self.current_cycle_date: str = ''             # 周期起始日期
        self.last_check_time: Optional[datetime] = None
        self.last_summary_time: Optional[datetime] = None

        # ★ 配置热更新
        self._config_path = config_path
        self._config_mtime = 0.0
        try:
            self._config_mtime = Path(config_path).stat().st_mtime
        except OSError:
            pass

        # 飞书自建应用缓存
        self._feishu_token: str = ''
        self._feishu_token_expire: float = 0
        self._feishu_user_cache: Dict[str, str] = {}  # 飞书邮箱 → open_id

        # 确保报表目录存在
        self._ensure_report_dirs()

        # 初始化周期
        self._update_cycle()

        logger.info("🦞 邮件龙虾 v2 初始化完成")

    def _load_config(self, config_path: str) -> Dict:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            imap_cfg = config.get('imap', {})
            if '你的QQ邮箱' in imap_cfg.get('email', '你的QQ邮箱'):
                raise ValueError("请先在 config_imap_forward.json 中填写 IMAP 信息")
            logger.info(f"✅ 配置文件加载成功: {config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"❌ 配置文件不存在: {config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"❌ 配置文件格式错误: {e}")
            raise

    def _ensure_report_dirs(self):
        reports_cfg = self.config.get('reports', {})
        Path(reports_cfg.get('cycle_dir', 'reports/cycles')).mkdir(parents=True, exist_ok=True)
        Path(reports_cfg.get('summary_dir', 'reports/summaries')).mkdir(parents=True, exist_ok=True)
        # 心跳目录：OpenClaw 端探活使用（仅在启用心跳时创建）
        hb_cfg = self.config.get('heartbeat', {})
        if hb_cfg.get('enabled', False):
            Path(hb_cfg.get('path', 'state/heartbeat')).parent.mkdir(parents=True, exist_ok=True)

    def _check_config_reload(self):
        """检查配置文件是否被修改，若是则热更新。
        每分钟在守护循环中被调用。只重新加载变化的部分，不影响当前周期数据。
        包含竞态保护：如果读到半写文件（JSON 解析失败），会短暂重试。
        """
        try:
            mtime = Path(self._config_path).stat().st_mtime
        except OSError:
            return
        if mtime <= self._config_mtime:
            return  # 文件未修改
        logger.info(f"🔄 检测到配置文件已更新，正在热加载...")

        # 竞态保护：最多重试 3 次，间隔 0.5s（应对记事本/备份脚本正在写入的情况）
        new_config = None
        for attempt in range(3):
            try:
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    new_config = json.load(f)
                break  # 读取成功
            except json.JSONDecodeError:
                if attempt < 2:
                    logger.debug(f"⚠️ 配置文件可能正在写入，{0.5}s 后重试...")
                    time.sleep(0.5)
                else:
                    logger.error(f"❌ 配置文件格式错误（重试 3 次后放弃），跳过加载")
                    return
            except Exception as e:
                logger.error(f"❌ 配置热加载失败: {e}")
                return

        # 基本校验：必须有 imap 段才算合法配置
        if 'imap' not in new_config:
            logger.warning("⚠️ 新配置缺少 imap 段，跳过加载")
            return

        # 用新配置替换（原子操作，避免半加载状态）
        self.config = new_config
        self._config_mtime = mtime
        # 重新初始化 AI（API key / model / 提示词可能变了）
        self.ai = AIAnalyzer(self.config.get('ai', {}))
        # ★ M5修复：热更新时重置飞书缓存（app_id/secret 可能变了）
        self._feishu_token = ''
        self._feishu_token_expire = 0
        self._feishu_user_cache = {}
        logger.info("✅ 配置热加载完成")

    def _write_heartbeat(self):
        """写入心跳 — 由 OpenClaw 端（WSL）看门狗读取以判定 daemon 是否存活。
        每 60s 在主循环中被调用一次。目录/文件失败不影响主循环。
        """
        hb_cfg = self.config.get('heartbeat', {})
        if not hb_cfg.get('enabled', False):
            return
        try:
            hb_path = Path(hb_cfg.get('path', 'state/heartbeat'))
            hb_path.parent.mkdir(parents=True, exist_ok=True)
            # 内容写 ISO 时间戳（带时区），方便人工调试也可被脚本读
            hb_path.write_text(datetime.now().isoformat(timespec='seconds'),
                               encoding='utf-8')
        except Exception as e:
            logger.debug(f"心跳写入失败（可忽略）: {e}")

    # ================================================================== #
    #                        周期 & 调度管理                               #
    # ================================================================== #

    def _get_summary_slots(self) -> list:
        """获取配置的总结时段列表"""
        schedule = self.config.get('schedule', {})
        summaries = schedule.get('summaries', ['07:30', '11:30', '17:00', '21:00'])
        slots = []
        for s in summaries:
            h, m = map(int, s.split(':'))
            slots.append({'time': s, 'minutes': h * 60 + m})
        return sorted(slots, key=lambda x: x['minutes'])

    def _slot_name(self, slot_time: str) -> str:
        """根据时间返回时段名称"""
        h, m = map(int, slot_time.split(':'))
        if h < 10:
            return '早间'
        elif h < 14:
            return '午间'
        elif h < 19:
            return '傍晚'
        else:
            return '夜间'

    def _is_other_platform(self, platform: str) -> bool:
        """判断平台是否为「其他平台」（非 WMS/AMZ）"""
        return platform not in ('WMS', 'AMZ')

    def _should_show_source(self, emails_data: List[Dict]) -> bool:
        """判断是否应在总结中显示来源邮箱（全部为其他平台邮件时显示）"""
        if not emails_data:
            return False
        return all(self._is_other_platform(e.get('platform', 'OTH')) for e in emails_data)

    def _detect_platform_from_sender(self, sender: str, body: str, subject: str = '') -> Optional[str]:
        """
        从原始发件人地址、邮件正文、主题中检测平台归属。
        优先级: 发件人域名 → 发件人显示名关键词 → 正文/主题强关键词
        返回: 'WMS' / 'AMZ' / 'TEM' / ... / None（无法判断）
        """
        sender_lower = (sender or '').lower()
        body_lower = (body or '').lower()[:2000]
        subject_lower = (subject or '').lower()

        # 1) 提取发件人邮箱域名，查 sender_platform_map
        addr_match = re.search(r'[\w.+-]+@([\w.-]+\.\w+)', sender_lower)
        if addr_match:
            domain = addr_match.group(1)
            # 精确匹配 → 逐步缩短子域
            parts = domain.split('.')
            for i in range(len(parts)):
                check = '.'.join(parts[i:])
                if check in self.sender_platform_map:
                    return self.sender_platform_map[check]

        # 2) 发件人显示名关键词
        sender_name_keywords = {
            'amazon seller': 'AMZ', 'amazon.com': 'AMZ', '亚马逊': 'AMZ',
            'amazon marketplace': 'AMZ', 'amazon brand': 'AMZ',
            'walmart marketplace': 'WMS', 'walmart seller': 'WMS',
            '沃尔玛': 'WMS', 'walmart.com': 'WMS',
            'ozon': 'OZN',
        }
        for kw, plat in sender_name_keywords.items():
            if kw in sender_lower:
                return plat

        # 3) 主题 / 正文强关键词（仅在内容明确时使用）
        content = f"{subject_lower} {body_lower[:500]}"
        content_keywords = {
            'sellercentral.amazon': 'AMZ', 'seller central': 'AMZ',
            'amazon fba': 'AMZ', 'amazon fbm': 'AMZ',
            'amazon.com/': 'AMZ', '亚马逊卖家': 'AMZ',
            'walmart marketplace': 'WMS', 'walmart seller center': 'WMS',
            'wfs ': 'WMS', 'walmart fulfillment': 'WMS',
            '沃尔玛卖家': 'WMS', '沃尔玛 marketplace': 'WMS',
            'ozon seller': 'OZN', 'ozon global': 'OZN',
            'ozon.com': 'OZN', 'ozon.ru': 'OZN',
            'fbp仓库': 'OZN', 'ozon fbp': 'OZN',
        }
        for kw, plat in content_keywords.items():
            if kw in content:
                return plat

        return None

    def _verify_is_competing_platform(self, platform: str, sender: str, body: str, subject: str = '') -> bool:
        """
        验证某邮件是否来自真正的电商竞品平台（而非物流/服务商）。
        返回 True → 路由给 other_platform handler
        返回 False → 保留给原 owner
        """
        if platform in ('WMS', 'AMZ'):
            return False  # 主平台不走其他平台路由

        # 提取发件人域名
        sender_lower = (sender or '').lower()
        addr_match = re.search(r'[\w.+-]+@([\w.-]+\.\w+)', sender_lower)
        domain = addr_match.group(1) if addr_match else ''

        # 检查是否为物流/服务商
        for ld in self.logistics_domains:
            if ld in domain or ld in sender_lower:
                return False
            # 也检查 body 前 500 字符（服务商来信常在正文中提及自己）
            if ld in (body or '').lower()[:500]:
                # 但还要看正文是否同时提到了电商平台
                for ep_kw in ['amazon', 'walmart', '亚马逊', '沃尔玛']:
                    if ep_kw in body.lower()[:1000]:
                        return False  # 物流邮件但内容是关于 AMZ/WMS → 保留

        # 检查 AI 判定的平台是否为真正的电商平台
        if platform in self.ecommerce_platforms:
            return True

        # 发件人域名直接匹配到某电商平台
        if domain:
            parts = domain.split('.')
            for i in range(len(parts)):
                check = '.'.join(parts[i:])
                if check in self.sender_platform_map:
                    return True

        # 兜底：platform == 'OTH' 且无明显电商平台特征 → 保守保留给原 owner
        if platform == 'OTH':
            # 检查主题 + 正文中是否提到了真正的电商竞品
            subject_lower = (subject or '').lower()
            body_lower = (body or '').lower()[:1000]
            search_text = f"{subject_lower} {body_lower}"
            ecom_keywords = ['temu', 'shopee', 'tiktok shop', 'ozon', 'lazada']
            for kw in ecom_keywords:
                if kw in search_text:
                    return True
            return False  # 无电商平台特征 → 保留

        return False

    def _get_sister_owner(self, owner: str, target_platform: str) -> Optional[str]:
        """
        从 config 的 sister_owner_mapping 中查找 sister owner。
        如 owner='店铺A_AMZ' + target_platform='WMS' → 返回 '店铺A_WMT'
        """
        sister_mapping = self.config.get('sister_owner_mapping', [])
        if not sister_mapping:
            return None

        # 找到 owner 所属的 business group
        for group in sister_mapping:
            owners = group.get('owners', {})
            if owner in owners.values():
                # 找到了所属 group，返回 target_platform 对应的 owner
                sister = owners.get(target_platform, '')
                if sister and sister != owner:
                    return sister
        return None

    def _content_fingerprint(self, record: Dict) -> str:
        """
        生成邮件内容指纹，用于去重。
        同一封邮件从不同邮箱转发过来，clean_subject / from / body 内容一致，指纹相同。
        """
        subj = (record.get('subject') or '').strip().lower()
        from_addr = (record.get('from') or '').strip().lower()
        preview = (record.get('preview') or '').strip().lower()
        return f"{subj}|{from_addr}|{preview}"

    def _is_logistics_email(self, sender: str, body: str, subject: str = '') -> bool:
        """
        判断邮件是否与物流/服务商相关（非电商平台）。
        检查发件人域名 + 主题/正文中的物流关键词。
        """
        sender_lower = (sender or '').lower()
        subject_lower = (subject or '').lower()
        body_lower = (body or '').lower()[:2000]
        content = f"{subject_lower} {body_lower}"

        # 物流/服务商关键词（中英文）
        logistics_keywords = [
            '物流', '快递', '货运', '运单', '发货通知', '物流跟踪',
            'tracking number', 'tracking update', 'shipment status',
            'logistics', 'courier', 'freight', 'cargo',
            'yunexpress', '云途', 'ytexpress',
            '4px', '递四方', '4px.com',
            'yanwen', '燕文',
            'sf-express', '顺丰国际',
            'winit', '万邑通',
            'chukou1', '出口易',
            'dhl', 'fedex', 'ups', 'usps',
            'postnl', 'royal mail',
            'jtexpress', '极兔',
            'cainiao', '菜鸟',
        ]
        # 电商关键词（出现这些则不是纯物流邮件）
        ecom_keywords = [
            'listing', 'asin', '店铺', '店铺暂停', 'account suspended',
            'policy violation', 'product safety', 'compliance',
            '退款', '退货', 'refund', 'chargeback',
            '广告', 'advertising', 'campaign', 'promotion',
            '库存', 'inventory', 'fba', 'wfs',
            # 电商平台关键词（含物流内容的平台通知不应误判为纯物流邮件）
            'ozon', 'fbp仓库', 'fbp', '商品卡片', '销售佣金', '转化率',
            'temu', 'shopee', 'tiktok', 'ozon global', 'ozon seller',
            '卖家', 'warehouse', 'fulfillment by',
        ]

        # 检查物流关键词
        has_logistics = any(kw in content for kw in logistics_keywords)
        if not has_logistics:
            return False

        # 有物流关键词，但如果没有电商关键词 → 纯物流邮件
        has_ecom = any(kw in content for kw in ecom_keywords)
        return not has_ecom

    def _get_weekly_emails(self) -> List[Dict]:
        """从 reports/cycles/ 下近 7 天的所有周期 Excel 读取邮件（汇总整周数据）"""
        cycle_dir = Path(self.config.get('reports', {}).get('cycle_dir', 'reports/cycles'))
        if not cycle_dir.exists():
            return []

        cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        cutoff_ts = cutoff_date  # 文件名中的日期字符串
        emails = []
        seen_fingerprints = set()  # 跨文件去重（同一邮件可能在不同周期文件中）

        # 扫描周期目录下所有 slot_*.xlsx 文件
        excel_files = sorted(cycle_dir.glob('slot_*.xlsx'))
        for filepath in excel_files:
            # 从文件名提取日期: slot_0730_2026-08-01.xlsx → 2026-08-01
            fname = filepath.name
            parts = fname.replace('.xlsx', '').split('_')
            if len(parts) < 3:
                continue
            file_date = parts[-1]  # 最后一段是日期
            if file_date < cutoff_ts:
                continue  # 超过 7 天的文件跳过

            try:
                wb = load_workbook(filepath, read_only=True)
                ws = wb.active
                for row in range(2, (ws.max_row or 1) + 1):
                    date_val = ws.cell(row=row, column=1).value
                    if date_val is None:
                        continue
                    # ★ S3修复：统一日期格式后比较
                    if hasattr(date_val, 'strftime'):
                        date_str = date_val.strftime('%Y-%m-%d')
                    else:
                        date_str = str(date_val)[:10]
                    if not date_str or date_str < cutoff_date:
                        continue

                    # 去重指纹
                    subj = str(ws.cell(row=row, column=10).value or '').strip().lower()
                    sender = str(ws.cell(row=row, column=9).value or '').strip().lower()
                    preview = str(ws.cell(row=row, column=13).value or '').strip().lower()[:100]
                    fp = f"{subj}|{sender}|{preview}"
                    if fp in seen_fingerprints:
                        continue
                    seen_fingerprints.add(fp)

                    emails.append({
                        'date': str(date_val or ''),
                        'time': str(ws.cell(row=row, column=2).value or ''),
                        'source_email': str(ws.cell(row=row, column=3).value or ''),
                        'owner': str(ws.cell(row=row, column=4).value or ''),
                        'level': self._safe_level(ws.cell(row=row, column=5).value),
                        'sub_category': str(ws.cell(row=row, column=6).value or '其他'),
                        'platform': self.platform_normalize.get(
                            str(ws.cell(row=row, column=7).value or 'OTH'),
                            str(ws.cell(row=row, column=7).value or 'OTH')),
                        'category': str(ws.cell(row=row, column=8).value or ''),
                        'from': str(ws.cell(row=row, column=9).value or ''),
                        'subject': str(ws.cell(row=row, column=10).value or ''),
                        'ai_summary': str(ws.cell(row=row, column=11).value or ''),
                        'ai_action': str(ws.cell(row=row, column=12).value or ''),
                        'preview': str(ws.cell(row=row, column=13).value or ''),
                    })
                wb.close()
            except Exception as e:
                logger.error(f"❌ 读取周期文件 {filepath.name} 失败: {e}")

        logger.info(f"📊 周总结数据源：{len(excel_files)} 个周期文件，{len(emails)} 封邮件（近7天）")
        return emails

    def _read_cycle_emails(self, filepath: Path, seen_fingerprints: set) -> List[Dict]:
        """读取单个周期 Excel 文件中的邮件记录（按指纹跨文件去重）"""
        emails = []
        try:
            wb = load_workbook(filepath, read_only=True)
            ws = wb.active
            for row in range(2, (ws.max_row or 1) + 1):
                date_val = ws.cell(row=row, column=1).value
                if date_val is None:
                    continue
                record = {
                    'date': str(date_val or ''),
                    'time': str(ws.cell(row=row, column=2).value or ''),
                    'source_email': str(ws.cell(row=row, column=3).value or ''),
                    'owner': str(ws.cell(row=row, column=4).value or ''),
                    'level': self._safe_level(ws.cell(row=row, column=5).value),
                    'sub_category': str(ws.cell(row=row, column=6).value or '其他'),
                    'platform': self.platform_normalize.get(
                        str(ws.cell(row=row, column=7).value or 'OTH'),
                        str(ws.cell(row=row, column=7).value or 'OTH')),
                    'category': str(ws.cell(row=row, column=8).value or ''),
                    'from': str(ws.cell(row=row, column=9).value or ''),
                    'subject': str(ws.cell(row=row, column=10).value or ''),
                    'ai_summary': str(ws.cell(row=row, column=11).value or ''),
                    'ai_action': str(ws.cell(row=row, column=12).value or ''),
                    'preview': str(ws.cell(row=row, column=13).value or ''),
                }
                fp = self._content_fingerprint(record)
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)
                emails.append(record)
            wb.close()
        except Exception as e:
            logger.error(f"❌ 读取周期文件 {filepath.name} 失败: {e}")
        return emails

    def _get_daily_brief_emails(self) -> List[Dict]:
        """
        读取 17:00 简报的数据源：昨天 17:00 ~ 今天 17:00 的全部邮件（24 小时，所有店铺）。
        数据来源：昨天 17:00 之后的周期文件 + 今天 17:00 及之前的周期文件，
        并合并内存中的当前周期邮件（兜底周期文件尚未写入的情况）。
        """
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        brief_end_minutes = 17 * 60  # 17:00

        # slot > 17:00（如 21:00）取昨天的文件；slot <= 17:00 取今天的文件
        target_files = []
        cycle_dir = Path(self.config.get('reports', {}).get('cycle_dir', 'reports/cycles'))
        for slot in self._get_summary_slots():
            date_str = yesterday if slot['minutes'] > brief_end_minutes else today
            filepath = cycle_dir / f"slot_{slot['time'].replace(':', '')}_{date_str}.xlsx"
            if filepath.exists():
                target_files.append(filepath)

        emails = []
        seen_fingerprints = set()
        for filepath in sorted(target_files):
            emails.extend(self._read_cycle_emails(filepath, seen_fingerprints))

        # 合并内存中的当前周期邮件（同轮去重）
        for record in self.current_cycle_emails:
            fp = self._content_fingerprint(record)
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            emails.append(record)

        logger.info(f"📊 17:00 简报数据源：{len(target_files)} 个周期文件，{len(emails)} 封邮件（24h）")
        return emails

    def _format_summary_period(self, summary_time: str, time_range: str, platform_name: str = '') -> str:
        """生成总结头部：如 '7月20日 早' 或 '7月20日 全天'"""
        date_str = self._format_summary_date(time_range)
        # 全平台日总结（21:00）或 time_range 中包含全平台标记
        if '全平台' in (platform_name or '') or '全平台' in (time_range or ''):
            return f"{date_str} 全天"
        period = self._slot_name(summary_time)
        period_short = period[0] if period else ''
        return f"{date_str} {period_short}"

    def _format_summary_date(self, time_range: str) -> str:
        """从 time_range 提取结束日期，转为 '7月20日' 格式
        使用时间范围的结束日期（即总结实际发送的日期），而非开始日期。
        例如 07:30 早间总结范围为 '昨天 21:00 ~ 今天 07:30'，应显示今天的日期。
        """
        try:
            # 取 ~ 后面的结束时间
            end_part = time_range.split('~')[-1].strip().split(' ')[0]
            parts = end_part.split('-')
            if len(parts) == 3:
                return f"{int(parts[1])}月{int(parts[2])}日"
        except (IndexError, ValueError):
            pass
        return ''

    def _extract_owner_platforms(self, owner: str) -> List[str]:
        """
        从负责人名称中提取平台代码（已统一规范化）。
        示例：
          '店铺A_AMZ'        → ['AMZ']
          '店铺A_WMT/AMZ'    → ['WMS', 'AMZ']   # WMT 自动规范为 WMS
          '店铺B_WMS'        → ['WMS']
          '深圳跨平台WALMART/AMAZON' → ['WMS', 'AMZ']  # 全称也识别
          '未分配'             → ['WMS', 'AMZ', 'TEM', 'SHO', 'TKB', 'OTH']（全部）
        """
        if not owner or owner == '未分配':
            return ['WMS', 'AMZ', 'TEM', 'SHO', 'TKB', 'OTH']

        found = set()
        # 同时匹配 2-8 位大写字母，以识别 WALMART / AMAZON 等全称
        for m in re.finditer(r'[A-Z]{2,8}', owner):
            code = m.group()
            # 先用规范化映射（WMT→WMS, AMAZON→AMZ 等），再检查是否为已知平台
            normalized = self.platform_normalize.get(code, code)
            if normalized in self.platform_names:
                found.add(normalized)

        return list(found) if found else ['WMS', 'AMZ', 'TEM', 'SHO', 'TKB', 'OTH']

    def _build_summary_text(self, summary_time: str, emails_data: List[Dict],
                             time_range: str = '', platform_name: str = '',
                             show_source: bool = False) -> str:
        """
        生成新格式总结纯文本。
        格式：平台 + 店铺 + 日期时段 + 级别颜色emoji + 极简文字。
        show_source=True 时，每条邮件标注来源邮箱（其他平台通知使用）。
        """
        # 提取店铺名（owner 字段，如 "店铺A_AMZ"）
        owner = emails_data[0].get('owner', '') if emails_data else ''
        # 从邮件数据推导平台显示名（而非依赖 platform_name 参数），确保与过滤后的内容一致
        unique_plats = list(dict.fromkeys(e.get('platform', 'OTH') for e in emails_data))
        plat_display_names = [self.platform_names.get(p, p) for p in unique_plats]
        plat_short = '/'.join(plat_display_names)
        header_line = f"{plat_short} {owner}".strip() if (plat_short or owner) else ''
        period_line = self._format_summary_period(summary_time, time_range, platform_name)
        total = len(emails_data)

        lines = []
        if header_line:
            lines.append(header_line)
        lines.append(period_line)
        lines.append(f"共{total}封")
        lines.append('')

        # 按级别统计
        level_count = {}
        for e in emails_data:
            lv = self._safe_level(e.get('level', 6))
            level_count[lv] = level_count.get(lv, 0) + 1

        # 0-2级：逐封列出主题（其他平台邮件标注来源邮箱）
        for lv in range(3):
            color = self.level_html_colors.get(lv, self.level_html_colors[6])
            cnt = level_count.get(lv, 0)
            lines.append(f"{color['emoji']} {lv}级 x{cnt}")
            if cnt > 0:
                idx = 1
                for e in emails_data:
                    if self._safe_level(e.get('level', 6)) == lv:
                        subj = e.get('subject', '')[:60]
                        line = f"{idx}. {subj}"
                        if show_source:
                            line += f" ← {e.get('source_email', '')}"
                        lines.append(line)
                        idx += 1
            lines.append('')

        # 3-6级：统一归入「其他」
        low_total = sum(level_count.get(lv, 0) for lv in range(3, 7))

        if low_total > 0:
            lines.append(f"其他 x{low_total}")
        else:
            lines.append('')

        return '\n'.join(lines)

    def _build_other_platform_daily_summary(self, summary_time: str, emails_data: List[Dict],
                                             time_range: str, handler: str) -> str:
        """
        构建「其他平台」21:00 日总结（带来源邮箱）。
        格式与普通总结相同，但每条邮件标注来源邮箱，方便负责人溯源。
        """
        if not emails_data:
            return ''

        # 平台标签：列出所有涉及的非 WMS/AMZ 平台
        plat_names = sorted(set(
            self.platform_names.get(e.get('platform', ''), e.get('platform', ''))
            for e in emails_data
        ))
        plat_label = '/'.join(plat_names) if plat_names else '其他平台'

        header_line = f"{plat_label} {handler}"
        period_line = self._format_summary_period(summary_time, time_range, '全平台日总结')
        total = len(emails_data)

        lines = [f"【21:00 日总结】{header_line}", period_line, f"共{total}封", '']

        level_count = {}
        for e in emails_data:
            lv = self._safe_level(e.get('level', 6))
            level_count[lv] = level_count.get(lv, 0) + 1

        # 0-2级：逐封列出主题 + 来源邮箱
        for lv in range(3):
            color = self.level_html_colors.get(lv, self.level_html_colors[6])
            cnt = level_count.get(lv, 0)
            lines.append(f"{color['emoji']} {lv}级 x{cnt}")
            if cnt > 0:
                idx = 1
                for e in emails_data:
                    if self._safe_level(e.get('level', 6)) == lv:
                        subj = e.get('subject', '')[:50]
                        src = e.get('source_email', '')
                        lines.append(f"{idx}. {subj} ← {src}")
                        idx += 1
            lines.append('')

        # 3-6级统一归入「其他」
        low_total = sum(level_count.get(lv, 0) for lv in range(3, 7))
        if low_total > 0:
            lines.append(f"其他 x{low_total}")
        else:
            lines.append('')

        return '\n'.join(lines)

    def get_current_cycle_type(self) -> str:
        """根据当前时间判断处于哪个总结周期，返回下一个总结时间点"""
        now = datetime.now()
        now_minutes = now.hour * 60 + now.minute
        slots = self._get_summary_slots()

        for slot in slots:
            if now_minutes < slot['minutes']:
                return slot['time']

        # 超过最后一个时段，返回第一个时段（次日）
        return slots[0]['time']

    def _update_cycle(self):
        """检查是否需要切换到新周期，如果是则重置"""
        new_type = self.get_current_cycle_type()
        today = datetime.now().strftime('%Y-%m-%d')

        if new_type != self.current_cycle_type or today != self.current_cycle_date:
            if self.current_cycle_type and self.current_cycle_emails:
                logger.info(f"📊 周期切换: {self.current_cycle_type} → {new_type}")
            self.current_cycle_type = new_type
            self.current_cycle_date = today
            self.current_cycle_emails = []

    def get_cycle_excel_path(self) -> Path:
        """获取当前周期的 Excel 文件路径"""
        cycle_dir = self.config.get('reports', {}).get('cycle_dir', 'reports/cycles')
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"slot_{self.current_cycle_type.replace(':', '')}_{date_str}.xlsx"
        return Path(cycle_dir) / filename

    def get_summary_time_range(self, summary_time: str) -> Tuple[str, str]:
        """获取某个总结时段覆盖的时间范围，返回 (起始时间, 结束时间)"""
        slots = self._get_summary_slots()
        slot_minutes = None
        prev_slot_time = None

        for i, slot in enumerate(slots):
            if slot['time'] == summary_time:
                slot_minutes = slot['minutes']
                if i == 0:
                    # 第一个时段，从昨天最后一个时段开始
                    prev_slot_time = slots[-1]['time']
                else:
                    prev_slot_time = slots[i - 1]['time']
                break

        if slot_minutes is None:
            return ('', '')

        today = datetime.now().strftime('%Y-%m-%d')
        end_time = f"{today} {summary_time}"

        # 计算起始时间
        prev_h, prev_m = map(int, prev_slot_time.split(':'))
        prev_minutes = prev_h * 60 + prev_m

        if prev_minutes >= slot_minutes:
            # 前一天
            from datetime import timedelta
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            start_time = f"{yesterday} {prev_slot_time}"
        else:
            start_time = f"{today} {prev_slot_time}"

        return (start_time, end_time)

    def should_run_summary(self) -> Optional[str]:
        """检查是否到达总结时间，返回时间点字符串（如 '07:30'），否则 None"""
        now = datetime.now()
        slots = self._get_summary_slots()
        now_minutes = now.hour * 60 + now.minute

        for slot in slots:
            if abs(now_minutes - slot['minutes']) <= 3:  # ★ M3修复：窗口扩大到3分钟
                # 避免同一时间重复触发
                if self.last_summary_time:
                    elapsed = (now - self.last_summary_time).total_seconds()
                    if elapsed < 300:  # 5 分钟内不重复
                        continue
                return slot['time']

        return None

    # ================================================================== #
    #                          IMAP 连接                                  #
    # ================================================================== #

    def connect_imap(self) -> imaplib.IMAP4_SSL:
        imap_cfg = self.config['imap']
        host = imap_cfg['host']
        port = imap_cfg.get('port', 993)
        email_addr = imap_cfg['email']
        password = imap_cfg['password']

        max_attempts = 3
        base_delay = 3  # 秒：3 → 6 → 12

        for attempt in range(1, max_attempts + 1):
            logger.info(f"🔌 连接 IMAP: {host}:{port}（第 {attempt}/{max_attempts} 次）")
            try:
                # 创建自定义 SSL 上下文，兼容139邮箱等使用旧加密套件的服务器
                ssl_context = ssl.create_default_context()
                ssl_context.set_ciphers('DEFAULT:@SECLEVEL=1')
                mail = imaplib.IMAP4_SSL(host, port, ssl_context=ssl_context, timeout=30)
                mail.login(email_addr, password)
                logger.info(f"✅ IMAP 登录成功: {email_addr}")
                return mail
            except (imaplib.IMAP4.error, ConnectionResetError, ConnectionRefusedError,
                    socket.timeout, OSError) as e:
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"⚠️ IMAP 连接失败（{type(e).__name__}: {e}），{delay}s 后重试...")
                    time.sleep(delay)
                else:
                    logger.error(f"❌ IMAP 连接失败（重试 {max_attempts} 次后放弃）: {e}")
                    raise

    # ================================================================== #
    #                          邮件头解析                                  #
    # ================================================================== #

    def decode_header_value(self, header_value: str) -> str:
        if not header_value:
            return ''
        parts = []
        try:
            for part, charset in decode_header(header_value):
                if isinstance(part, bytes):
                    for enc in [charset or 'utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
                        try:
                            parts.append(part.decode(enc))
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    else:
                        parts.append(part.decode('utf-8', errors='replace'))
                else:
                    parts.append(str(part))
        except Exception:
            return header_value
        return ''.join(parts)

    def parse_sender(self, from_header: str) -> str:
        if not from_header:
            return ''
        decoded = self.decode_header_value(from_header)
        match = re.match(r'^\s*(.+?)\s*<(.+?)>\s*$', decoded)
        if match:
            name = match.group(1).strip().strip('"')
            addr = match.group(2).strip()
            return f"{name} <{addr}>" if name else addr
        return decoded.strip()

    def parse_email_date(self, date_header: str) -> Tuple[str, str]:
        if not date_header:
            return ('', '')
        try:
            dt = parsedate_to_datetime(date_header)
            return (dt.strftime('%Y-%m-%d'), dt.strftime('%H:%M'))
        except Exception:
            try:
                return (date_header[:10], date_header[11:16])
            except Exception:
                return ('', '')

    # ================================================================== #
    #                   源邮箱识别 & 原始主题提取                           #
    # ================================================================== #

    def _parse_srs_email(self, srs_str: str) -> str:
        """
        解析 SRS (Sender Rewriting Scheme) 编码的邮箱地址。

        SRS 格式示例:
          user_a+srs=xxxx=yyyy=qq.com=forwarder@outlook.com
          → 源邮箱: user_a@outlook.com

        原理: SRS 把原始发送者 info 编码到 +srs=...= 部分，
              域名 @outlook.com 部分保持不变。
        """
        if not srs_str:
            return ''

        # 先提取纯邮箱地址（去掉显示名）
        match = re.search(r'[\w.+=-]+@[\w.-]+', srs_str)
        if not match:
            return ''

        addr = match.group(0)
        local, domain = addr.rsplit('@', 1)

        if '+srs=' in local.lower():
            # SRS 编码: 取 +srs= 之前的部分 + @域名
            source_local = local.split('+')[0]
            return f"{source_local}@{domain}".lower()

        # 非 SRS 格式，直接返回
        return addr.lower()

    def _extract_addr(self, header_value: str) -> str:
        """从邮件头值中提取纯邮箱地址（处理 'Name <email>' 格式）"""
        if not header_value:
            return ''
        match = re.search(r'[\w.+=-]+@[\w.-]+', header_value)
        return match.group(0).lower() if match else ''

    def extract_source_email(self, msg: email.message.Message) -> str:
        """
        从邮件头中提取源邮箱地址（哪个 Outlook 邮箱转发的）。

        识别优先级：
          1. X-MS-Exchange-Inbox-Rules-Loop → 直接是源邮箱（最干净）
          2. Sender → 可能含 SRS 编码，需要解析
          3. X-QQ-CSender → QQ 处理后的 Sender，含 SRS
          4. 返回空字符串（回退到前缀匹配）
        """
        # 优先级1：X-MS-Exchange-Inbox-Rules-Loop
        rules_loop = msg.get('X-MS-Exchange-Inbox-Rules-Loop', '')
        if rules_loop:
            addr = rules_loop.strip().lower()
            if '@' in addr:
                logger.info(f"  🔍 源邮箱识别 [Rules-Loop]: {addr}")
                return addr

        # 优先级2：Sender（标准邮件头）
        sender = msg.get('Sender', '')
        if sender:
            addr = self._parse_srs_email(sender)
            if addr:
                logger.info(f"  🔍 源邮箱识别 [Sender]: {addr}")
                return addr

        # 优先级3：X-QQ-CSender（QQ 邮箱处理后的 Sender）
        qq_sender = msg.get('X-QQ-CSender', '')
        if qq_sender:
            addr = self._parse_srs_email(qq_sender)
            if addr:
                logger.info(f"  🔍 源邮箱识别 [X-QQ-CSender]: {addr}")
                return addr

        # 优先级4：Resent-From（转发/转寄标准头）
        resent_from = msg.get('Resent-From', '')
        if resent_from:
            addr = self._extract_addr(resent_from)
            if addr:
                logger.info(f"  🔍 源邮箱识别 [Resent-From]: {addr}")
                return addr

        # 优先级5：X-Forwarded-For（转发原始收件人）
        xff = msg.get('X-Forwarded-For', '')
        if xff:
            addr = self._extract_addr(xff)
            if addr:
                logger.info(f"  🔍 源邮箱识别 [X-Forwarded-For]: {addr}")
                return addr

        # 优先级6：检查 Received 链中是否包含已配置的邮箱地址
        received_headers = msg.get_all('Received', [])
        for header in received_headers:
            header_lower = header.lower()
            # 在 Received 头中查找已配置的邮箱
            for mb in self.config.get('mailbox_mapping', {}).get('mailboxes', []):
                mb_email = mb.get('source_email', '').lower()
                if mb_email and mb_email in header_lower:
                    logger.info(f"  🔍 源邮箱识别 [Received链]: {mb_email}")
                    return mb_email

        # 优先级7：检查 From 头是否匹配已配置的邮箱（直接发送场景）
        from_header = msg.get('From', '')
        if from_header:
            from_addr = self._extract_addr(from_header)
            if from_addr:
                for mb in self.config.get('mailbox_mapping', {}).get('mailboxes', []):
                    mb_email = mb.get('source_email', '').lower()
                    if mb_email and mb_email == from_addr:
                        logger.info(f"  🔍 源邮箱识别 [From头匹配]: {from_addr}")
                        return from_addr

        # 未识别到，打印所有邮件头用于调试
        logger.warning(f"  ⚠️ 无法识别源邮箱，当前邮件头信息：")
        for key in msg.keys():
            val = msg[key]
            if len(str(val)) > 200:
                val = str(val)[:200] + '...'
            logger.warning(f"     {key}: {val}")

        return ''

    def extract_thread_topic(self, msg: email.message.Message) -> str:
        """
        从 Thread-Topic 头提取原始邮件主题。

        Thread-Topic 是 Outlook 在邮件创建时设定的主题，
        转发时不会添加 "转发: " 前缀，也不含我们设置的前缀。
        这是最干净的原始主题。
        """
        thread_topic = msg.get('Thread-Topic', '')
        if thread_topic:
            decoded = self.decode_header_value(thread_topic).strip()
            if decoded:
                return decoded
        return ''

    def match_source_email(self, source_email: str) -> Optional[Dict]:
        """
        根据源邮箱地址匹配配置中的 mailbox 条目。

        参数:
            source_email: 从邮件头提取的源邮箱地址

        返回:
            匹配到的 mailbox 配置，None 表示未匹配
        """
        if not source_email:
            return None

        mailboxes = self.config.get('mailbox_mapping', {}).get('mailboxes', [])

        for mb in mailboxes:
            configured_email = mb.get('source_email', '').strip().lower()
            if configured_email and configured_email == source_email.strip().lower():
                return mb

        return None

    # ================================================================== #
    #                       前缀解析（兜底方案）                             #
    # ================================================================== #

    def parse_prefix(self, subject: str) -> Tuple[Optional[Dict], str]:
        """去掉 '转发: ' 等前缀后，匹配来源邮箱前缀"""
        mailboxes = self.config.get('mailbox_mapping', {}).get('mailboxes', [])

        # 去掉转发自动添加的前缀
        forward_prefixes = ['转发: ', '转发：', 'Fwd: ', 'FWD: ', 'FW: ', '转寄: ', '转寄：']
        clean = subject
        for fwd in forward_prefixes:
            if clean.startswith(fwd):
                clean = clean[len(fwd):].strip()
                break

        # 匹配邮箱来源前缀
        for mb in mailboxes:
            prefix = mb.get('prefix', '')
            if not prefix:
                continue  # 没有配置前缀的跳过（新版配置用源邮箱头识别，不需要前缀）
            if clean.startswith(prefix):
                return (mb, clean[len(prefix):].strip())
            if clean.startswith(prefix + ' '):
                return (mb, clean[len(prefix) + 1:].strip())
            # 容错：中英文方括号互换
            for alt in [prefix.replace('[', '【').replace(']', '】'),
                        prefix.replace('【', '[').replace('】', ']')]:
                if clean.startswith(alt):
                    return (mb, clean[len(alt):].strip())

        return (None, subject)

    # ================================================================== #
    #                        正文提取 & 分类                                #
    # ================================================================== #

    def extract_body(self, msg: email.message.Message, max_length: int = 2000) -> str:
        body_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get('Content-Disposition', ''))
                if 'attachment' in cd or ct not in ('text/plain', 'text/html'):
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    for enc in [part.get_content_charset() or 'utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
                        try:
                            body_parts.append(payload.decode(enc))
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                for enc in [msg.get_content_charset() or 'utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
                    try:
                        body_parts.append(payload.decode(enc))
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue

        body = '\n'.join(body_parts)
        # ★ 智能 HTML 提取：保留有价值的文本
        # 1. 保留 img 的 alt/title 属性文本
        body = re.sub(r'<img[^>]*\balt="([^"]+)"[^>]*>', r'[\1]', body, flags=re.IGNORECASE)
        body = re.sub(r'<img[^>]*\btitle="([^"]+)"[^>]*>', r'[\1]', body, flags=re.IGNORECASE)
        # 2. 标记其余图片位置
        body = re.sub(r'<img[^>]*>', '[图片]', body, flags=re.IGNORECASE)
        # 3. 保留 a 标签的文本内容
        body = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', body, flags=re.DOTALL | re.IGNORECASE)
        # 4. <br>/<p>/<div> 转空格/换行
        body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'</?(?:p|div|tr|li|h[1-6])[^>]*>', '\n', body, flags=re.IGNORECASE)
        # 5. 删除其余所有标签
        body = re.sub(r'<[^>]+>', ' ', body)
        # 6. 清理多余空白
        body = re.sub(r'[ \t]+', ' ', body)
        body = re.sub(r'\n\s*\n', '\n', body)
        body = body.strip()
        return body[:max_length]

    def classify_by_keywords(self, subject: str, body: str) -> Dict:
        """关键词分类（AI 不可用时的兜底方案），返回 0-7 数字级别"""
        text = (subject + " " + (body or "")).lower()
        for rule in self.config['rules']:
            for keyword in rule['keywords']:
                if keyword.lower() in text:
                    return {
                        "name": rule['name'],
                        "level": rule['level'],
                        "sub_category": rule.get('sub_category', '其他'),
                        "category": rule['category'] if 'category' in rule else rule['name']
                    }
        return {"name": "其他", "level": 6, "sub_category": "其他", "category": "other"}

    def classify_email(self, subject: str, body: str, sender: str = '') -> Dict:
        """分类邮件：优先 AI，失败则回退到关键词"""
        # 尝试 AI 分析
        ai_result = self.ai.analyze(subject, body, sender)
        if ai_result:
            return {
                "name": ai_result.get('category', '其他'),
                "level": int(ai_result.get('level', 6)),
                "sub_category": ai_result.get('sub_category', '其他'),
                "platform": ai_result.get('platform', 'OTH'),
                "category": ai_result.get('category', 'other'),
                "ai_summary": ai_result.get('summary', ''),
                "ai_action": ai_result.get('action', ''),
                "ai_reason": ai_result.get('reason', ''),
            }

        # 回退到关键词
        result = self.classify_by_keywords(subject, body)
        result['platform'] = 'OTH'  # 关键词模式无法识别平台
        result['ai_summary'] = ''
        result['ai_action'] = ''
        result['ai_reason'] = ''
        return result

    # ================================================================== #
    #                       获取 & 处理邮件                                #
    # ================================================================== #

    def fetch_emails(self) -> List[Dict]:
        """通过 IMAP 获取并处理邮件"""
        mail = self.connect_imap()
        emails_data = []
        seen_fingerprints = set()  # ★ 同轮去重

        try:
            status, _ = mail.select('INBOX')
            if status != 'OK':
                logger.error("❌ 无法打开收件箱")
                return emails_data

            settings = self.config['settings']
            if settings.get('only_unread', True):
                criteria = '(UNSEEN)'
            else:
                days_back = settings.get('days_back', 1)
                since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
                criteria = f'(SINCE {since_date})'

            status, messages = mail.search(None, criteria)
            if status != 'OK':
                return emails_data

            email_ids = messages[0].split()
            if not email_ids:
                logger.info("📭 没有新邮件")
                return emails_data

            max_emails = settings.get('max_emails', 200)
            email_ids = email_ids[-max_emails:]
            logger.info(f"📬 找到 {len(email_ids)} 封邮件")

            for eid in email_ids:
                try:
                    status, data = mail.fetch(eid, '(RFC822)')
                    if status != 'OK' or not data or not data[0]:
                        continue

                    msg = email.message_from_bytes(data[0][1])
                    raw_subject = self.decode_header_value(msg.get('Subject', ''))
                    sender = self.parse_sender(msg.get('From', ''))
                    date_str, time_str = self.parse_email_date(msg.get('Date', ''))
                    body = self.extract_body(msg)

                    # ★ 源邮箱识别：优先从邮件头提取，兜底用主题前缀
                    source_email_header = self.extract_source_email(msg)
                    mailbox_config = self.match_source_email(source_email_header)

                    # 获取干净的主题：优先 Thread-Topic（无转发前缀），兜底用 Subject
                    thread_topic = self.extract_thread_topic(msg)

                    if mailbox_config:
                        # ✅ 从邮件头成功识别源邮箱
                        owner = mailbox_config['owner']
                        source_email = mailbox_config['source_email']
                        # 使用 Thread-Topic（如果有），否则去掉 Subject 中的"转发:"前缀
                        if thread_topic:
                            clean_subject = thread_topic
                        else:
                            _, clean_subject = self.parse_prefix(raw_subject)
                    else:
                        # 回退：尝试主题前缀匹配
                        prefix_mb, prefix_subject = self.parse_prefix(raw_subject)
                        if prefix_mb:
                            mailbox_config = prefix_mb
                            owner = prefix_mb['owner']
                            source_email = prefix_mb['source_email']
                            clean_subject = prefix_subject
                        else:
                            # 完全无法识别来源
                            unknown_action = self.config.get('mailbox_mapping', {}).get('unknown_prefix_action', 'skip')
                            if unknown_action == 'skip':
                                logger.debug(f"  ⏭️ 跳过（无法识别来源）: {raw_subject[:50]}")
                                continue
                            owner = self.config['settings'].get('default_owner', '未分配')
                            source_email = source_email_header or '未知来源'
                            clean_subject = thread_topic or raw_subject

                    # 如果 Thread-Topic 存在，用它作为分析主题（更干净）
                    analysis_subject = thread_topic if thread_topic else clean_subject

                    # 分类（AI + 关键词兜底）— 使用 analysis_subject（Thread-Topic 优先）
                    category = self.classify_email(analysis_subject, body, sender)
                    logger.info(f"  📧 [L{category['level']}] [{owner}] {clean_subject[:50]}")

                    record = {
                        'date': date_str,
                        'time': time_str,
                        'source_email': source_email,
                        'owner': owner,
                        'level': category['level'],
                        'platform': self.platform_normalize.get(
                            category.get('platform', 'OTH'),
                            category.get('platform', 'OTH')
                        ),
                        'category': category['name'],
                        'from': sender,
                        'subject': clean_subject,
                        'preview': body[:100].replace('\n', ' ').strip() if body else '(无预览)',
                        'ai_summary': category.get('ai_summary', ''),
                        # RFC 822 Message-ID, stripped of angle brackets. Used as
                        # the durable seen-email key so we don't re-fetch or
                        # double-push the same message after a restart.
                        'msg_id': (msg.get('Message-ID', '') or '').strip().strip('<>'),
                        'ai_action': category.get('ai_action', ''),
                        'email_id': eid.decode() if isinstance(eid, bytes) else str(eid),
                    }

                    # ★ 去重：同一轮检查中，完全相同的邮件只保留一份
                    fp = self._content_fingerprint(record)
                    if fp in seen_fingerprints:
                        logger.info(f"  ⏭️ 重复邮件，跳过: {clean_subject[:50]}")
                        continue
                    seen_fingerprints.add(fp)

                    # ★ ① 发件人平台检测（校正 AI 对平台误判为 OTH 的情况）
                    detected_platform = self._detect_platform_from_sender(sender, body, raw_subject)
                    if detected_platform and record['platform'] not in ('WMS', 'AMZ'):
                        old_plat = record['platform']
                        record['platform'] = detected_platform
                        logger.info(f"  🔍 发件人平台校正: {old_plat} → {detected_platform} (sender={sender[:50]})")

                    # ★ ①b 物流/服务商检测：正文含物流关键词且无电商关键词 → 保留原 owner，不走分流
                    is_logistics = self._is_logistics_email(sender, body, raw_subject)

                    # ★ ② 双平台 owner 分流（睿宝 AMZ/WMT、诚俊 AMZ/WMT 等）
                    #    物流邮件不分流，保留给原始 owner
                    if not is_logistics:
                        original_owner = record['owner']
                        sister = self._get_sister_owner(original_owner, record['platform'])
                        if sister and sister != original_owner:
                            record['owner'] = sister
                            logger.info(f"  🔀 双平台分流: {original_owner} → {sister} (platform={record['platform']})")
                    else:
                        logger.info(f"  📦 物流/服务商邮件，保留原 owner [{record['owner']}]")

                    # ★ ③ 其他平台路由（仅真正的电商竞品才转走，物流/服务商保留原 owner）
                    other_cfg = self.config.get('other_platform', {})
                    if other_cfg.get('enabled', False) and record['platform'] not in ('WMS', 'AMZ'):
                        if not is_logistics and self._verify_is_competing_platform(record['platform'], sender, body, clean_subject):
                            handler = other_cfg.get('owner', '').strip()
                            if handler:
                                record['owner'] = handler
                                logger.info(f"  🔀 其他平台 [{record['platform']}] 路由给 [{handler}]")
                        else:
                            logger.info(f"  ✅ 物流/服务商邮件，保留原 owner [{record['owner']}] (platform={record['platform']})")

                    emails_data.append(record)

                    # ★ 0级、1级和2级邮件立即推送
                    # 其他平台邮件使用 other_platform.notify_levels；AMZ/WMS 使用 feishu_bot.notify_levels
                    if other_cfg.get('enabled', False) and record['platform'] not in ('WMS', 'AMZ'):
                        notify_levels = other_cfg.get('notify_levels', [0, 1, 2])
                    else:
                        notify_levels = self.config.get('feishu_bot', {}).get('notify_levels', [0, 1, 2])
                    level_val = category['level'] if isinstance(category['level'], int) else 6
                    if level_val in notify_levels or (isinstance(level_val, int) and level_val <= 2):
                        # 飞书通知：群聊走 webhook，私聊走自建应用，both 两者都发
                        feishu_content = self._build_feishu_email_notification(record, category)
                        channel = self._get_owner_channel(record['owner'])
                        # 无法识别负责人 / 不在私聊映射中的 → 兜底发群聊
                        feishu_ids = self._get_feishu_ids_for_owner(record['owner'])
                        if channel == 'private' and not feishu_ids:
                            channel = 'group'
                            logger.debug(f"  ⏭️ 负责人 [{record['owner']}] 未在飞书私聊映射中，兜底发群聊")
                        if channel == 'both':
                            self.send_feishu_bot(feishu_content)
                            if feishu_ids:
                                self.send_feishu_private(feishu_content, record['owner'])
                        elif channel == 'group':
                            self.send_feishu_bot(feishu_content)
                        else:
                            self.send_feishu_private(feishu_content, record['owner'])

                    # 标记已读
                    if settings.get('mark_as_read', True):
                        try:
                            mail.store(eid, '+FLAGS', '\\Seen')
                        except Exception:
                            pass

                    # 记录到 seen_emails_store（防止重启后重复抓取 / 漏汇总）
                    if self.seen_store is not None:
                        try:
                            msg_id = (record.get('msg_id') or record.get('message_id') or '').strip()
                            if msg_id:
                                self.seen_store.mark_seen(
                                    record.get('source_email') or '',
                                    msg_id,
                                    subject=record.get('subject', ''),
                                    owner=record.get('owner', ''),
                                    level=record.get('level', 6),
                                    platform=record.get('platform', ''),
                                    category=record.get('sub_category') or record.get('category', ''),
                                    notify_owner=record.get('notify_owner', '') or record.get('owner', ''),
                                )
                        except Exception:
                            pass

                except Exception as e:
                    logger.error(f"❌ 处理邮件出错: {e}", exc_info=True)
                    continue

        finally:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass

        return emails_data

    # ================================================================== #
    #                         Excel 保存                                   #
    # ================================================================== #

    def save_to_cycle_excel(self, emails_data: List[Dict]):
        """追加写入当前周期的 Excel 文件"""
        if not emails_data:
            return

        filepath = self.get_cycle_excel_path()
        is_new_file = not filepath.exists()

        try:
            if filepath.exists():
                wb = load_workbook(filepath)
                ws = wb.active
            else:
                wb = Workbook()
                ws = wb.active
                ws.title = '邮件记录'
                headers = ['日期', '时间', '来源邮箱', '负责人', '级别', '子类别', '平台', '分类',
                           '发件人', '主题', 'AI摘要', '行动建议', '预览']
                for col, h in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=col, value=h)
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

            start_row = ws.max_row + 1

            for i, data in enumerate(emails_data):
                row = start_row + i
                ws.cell(row=row, column=1, value=data['date'])
                ws.cell(row=row, column=2, value=data['time'])
                ws.cell(row=row, column=3, value=data['source_email'])
                ws.cell(row=row, column=4, value=data['owner'])
                ws.cell(row=row, column=5, value=data['level'])
                ws.cell(row=row, column=6, value=data.get('sub_category', '其他'))
                ws.cell(row=row, column=7, value=data.get('platform', 'OTH'))
                ws.cell(row=row, column=8, value=data['category'])
                ws.cell(row=row, column=9, value=data['from'])
                ws.cell(row=row, column=10, value=data['subject'])
                ws.cell(row=row, column=11, value=data.get('ai_summary', ''))
                ws.cell(row=row, column=12, value=data.get('ai_action', ''))
                ws.cell(row=row, column=13, value=data['preview'])

                level = data['level']
                if level in self.level_colors:
                    for col in range(1, 14):
                        ws.cell(row=row, column=col).fill = self.level_colors[level]

            # ★ M7修复：只在创建新文件时设置列宽
            if is_new_file:
                widths = {'A': 12, 'B': 10, 'C': 26, 'D': 12, 'E': 10, 'F': 12,
                          'G': 8, 'H': 14, 'I': 28, 'J': 38, 'K': 30, 'L': 25, 'M': 40}
                for col_letter, w in widths.items():
                    ws.column_dimensions[col_letter].width = w

            # ★ S2修复：原子写入
            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp(dir=filepath.parent, suffix='.xlsx')
            os.close(tmp_fd)
            try:
                wb.save(tmp_path)
                os.replace(tmp_path, filepath)
            except Exception:
                try: os.unlink(tmp_path)
                except OSError: pass
                raise
            finally:
                try: wb.close()
                except: pass
            logger.info(f"✅ 周期报表更新: {filepath.name} (+{len(emails_data)} 条)")

        except Exception as e:
            logger.error(f"❌ 周期报表保存失败: {e}", exc_info=True)

    def save_summary_excel(self, summary_type: str, emails_data: List[Dict], summary_text: str):
        """保存总结报告 Excel"""
        if not emails_data:
            return

        summary_dir = self.config.get('reports', {}).get('summary_dir', 'reports/summaries')
        date_str = datetime.now().strftime('%Y-%m-%d_%H-%M')
        # summary_type 现在是总结时间字符串（如 '07:30'），用 _slot_name 获取中文名称
        slot_name = self._slot_name(summary_type) if ':' in str(summary_type) else summary_type
        type_cn = f'{slot_name}总结'
        filepath = Path(summary_dir) / f"{date_str}_{type_cn}.xlsx"

        try:
            wb = Workbook()

            # Sheet 1: 总结概览
            ws1 = wb.active
            ws1.title = '总结概览'
            ws1.cell(row=1, column=1, value=f"📊 {type_cn} - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            ws1.cell(row=1, column=1).font = Font(bold=True, size=14)
            ws1.cell(row=3, column=1, value="总结报告")
            ws1.cell(row=3, column=1).font = Font(bold=True, size=12)

            for i, line in enumerate(summary_text.split('\n'), 4):
                ws1.cell(row=i, column=1, value=line)

            ws1.column_dimensions['A'].width = 80

            # Sheet 2: 邮件明细
            ws2 = wb.create_sheet('邮件明细')
            headers = ['日期', '时间', '来源邮箱', '负责人', '级别', '子类别', '平台', '分类', '发件人', '主题', 'AI摘要', '行动建议']
            for col, h in enumerate(headers, 1):
                cell = ws2.cell(row=1, column=col, value=h)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

            for i, data in enumerate(emails_data):
                row = i + 2
                ws2.cell(row=row, column=1, value=data['date'])
                ws2.cell(row=row, column=2, value=data['time'])
                ws2.cell(row=row, column=3, value=data['source_email'])
                ws2.cell(row=row, column=4, value=data['owner'])
                ws2.cell(row=row, column=5, value=data['level'])
                ws2.cell(row=row, column=6, value=data.get('sub_category', '其他'))
                ws2.cell(row=row, column=7, value=data.get('platform', 'OTH'))
                ws2.cell(row=row, column=8, value=data['category'])
                ws2.cell(row=row, column=9, value=data['from'])
                ws2.cell(row=row, column=10, value=data['subject'])
                ws2.cell(row=row, column=11, value=data.get('ai_summary', ''))
                ws2.cell(row=row, column=12, value=data.get('ai_action', ''))

                level = data['level']
                if level in self.level_colors:
                    for col in range(1, 13):
                        ws2.cell(row=row, column=col).fill = self.level_colors[level]

            for col_letter, w in {'A': 12, 'B': 10, 'C': 26, 'D': 12, 'E': 10, 'F': 12, 'G': 8, 'H': 14, 'I': 28, 'J': 38, 'K': 30, 'L': 25}.items():
                ws2.column_dimensions[col_letter].width = w

            wb.save(filepath)
            logger.info(f"✅ 总结报表保存: {filepath}")

        except Exception as e:
            logger.error(f"❌ 总结报表保存失败: {e}", exc_info=True)

    def _safe_level(self, val) -> int:
        """安全地获取级别数字（0-6）"""
        if isinstance(val, int):
            return min(val, 6)
        try:
            v = int(val)
            return min(v, 6)
        except (ValueError, TypeError):
            return 6

    def _get_owner_channel(self, owner: str) -> str:
        """获取负责人的通知渠道：'group'（群聊）、'private'（私聊）或 'both'（两者都发）"""
        mailboxes = self.config.get('mailbox_mapping', {}).get('mailboxes', [])
        for mb in mailboxes:
            if mb.get('owner', '') == owner and mb.get('notify_channel'):
                return mb['notify_channel']
        # 默认渠道
        return self.config.get('mailbox_mapping', {}).get('default_channel', 'private')

    def _get_level_keywords(self, emails_data: List[Dict], level: int) -> str:
        """获取某个级别中出现的所有子类别（去重），用于在总结中显示关键词标签"""
        cats = []
        seen = set()
        for e in emails_data:
            if self._safe_level(e.get('level', 6)) == level:
                cat = e.get('category', '') or e.get('sub_category', '')
                if cat and cat not in seen:
                    seen.add(cat)
                    cats.append(cat)
        return '、'.join(cats) if cats else ''

    # ================================================================== #
    #                       飞书群机器人通知                                #
    # ================================================================== #

    def send_feishu_bot(self, content: str, msg_type: str = 'interactive') -> bool:
        """
        发送消息到飞书群机器人。

        参数:
            content: 消息内容
            msg_type: 'interactive'（卡片，推荐）/ 'text'（纯文本）/ 'rich_text'（富文本）

        返回:
            是否发送成功
        """
        feishu_cfg = self.config.get('feishu_bot', {})
        if not feishu_cfg.get('enabled', False):
            return False

        webhook_url = feishu_cfg.get('webhook_url', '')
        if not webhook_url or '你的机器人key' in webhook_url:
            logger.warning("⚠️ 飞书群机器人 Webhook 未配置")
            return False

        try:
            # 构建请求体
            if msg_type == 'interactive':
                # 卡片消息（最美观，推荐）
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": content
                            }
                        ]
                    }
                }
            elif msg_type == 'rich_text':
                # 富文本消息
                payload = {
                    "msg_type": "post",
                    "content": {
                        "post": {
                            "zh_cn": {
                                "title": "邮件通知",
                                "content": [[{"tag": "text", "text": content}]]
                            }
                        }
                    }
                }
            else:
                # 纯文本
                payload = {
                    "msg_type": "text",
                    "content": {"text": content}
                }

            # 如果配置了签名密钥，添加签名
            secret = feishu_cfg.get('secret', '')
            if secret:
                import hashlib
                import hmac
                import base64
                timestamp = str(int(time.time()))
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(
                    string_to_sign.encode('utf-8'),
                    digestmod=hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                payload['timestamp'] = timestamp
                payload['sign'] = sign

            resp = requests.post(webhook_url, json=payload, timeout=15)
            result = resp.json()

            if result.get("code") == 0 or result.get("StatusCode") == 0:
                logger.info(f"✅ 飞书群机器人推送成功")
                return True
            else:
                logger.error(f"❌ 飞书群机器人推送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"❌ 飞书群机器人推送异常: {e}")
            return False

    def _build_feishu_email_notification(self, record: Dict, category: Dict) -> str:
        """构建飞书群机器人的单封邮件通知（Markdown + emoji 颜色）"""
        lv = self._safe_level(category.get('level', 6))
        color = self.level_html_colors.get(lv, self.level_html_colors[6])
        level_display = self.level_names.get(lv, f"{lv}级")
        sub_cat = category.get('sub_category', '其他')

        ai_part = ''
        if record.get('ai_summary'):
            ai_part = f"""
**🤖 AI 摘要**：{record['ai_summary']}"""

        return f"""**{color['emoji']} 📧 新邮件提醒**

**级别**：{color['emoji']} {level_display} **【{sub_cat}】**
**负责人**：{record['owner']}
**来源邮箱**：{record['source_email']}
**主题**：{record['subject']}
**发件人**：{record['from']}
**时间**：{record['date']} {record['time']}
**预览**：{record['preview'][:150]}
{ai_part}"""

    def _build_feishu_summary(self, summary_time: str, emails_data: List[Dict],
                               time_range: str = '',
                               platform_name: str = '') -> str:
        """构建飞书群机器人的总结通知（新格式：极简纯文字 + 级别颜色emoji）"""
        if not emails_data:
            return ''
        show_source = self._should_show_source(emails_data)
        return self._build_summary_text(summary_time, emails_data, time_range, platform_name, show_source)

    # ================================================================== #
    #                  飞书自建应用 — 私聊推送                              #
    # ================================================================== #

    def _get_feishu_token(self) -> str:
        """获取飞书 tenant_access_token（自动缓存，过期前5分钟刷新）"""
        app_cfg = self.config.get('feishu_app', {})
        if not app_cfg.get('enabled', False):
            return ''

        # 缓存未过期则直接返回
        if self._feishu_token and time.time() < self._feishu_token_expire - 300:
            return self._feishu_token

        app_id = app_cfg.get('app_id', '')
        app_secret = app_cfg.get('app_secret', '')
        if not app_id or not app_secret or '你的' in app_id:
            logger.warning("⚠️ 飞书自建应用 app_id/app_secret 未配置")
            return ''

        try:
            resp = requests.post(
                'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/',
                json={'app_id': app_id, 'app_secret': app_secret},
                timeout=10
            )
            result = resp.json()
            if result.get('code') == 0:
                self._feishu_token = result['tenant_access_token']
                self._feishu_token_expire = time.time() + result.get('expire', 7200)
                logger.info("✅ 飞书 token 获取成功")
                return self._feishu_token
            else:
                logger.error(f"❌ 飞书 token 获取失败: {result}")
                return ''
        except Exception as e:
            logger.error(f"❌ 飞书 token 请求异常: {e}")
            return ''

    def _get_feishu_open_id(self, feishu_id: str) -> str:
        """通过飞书邮箱或手机号查找用户 open_id（结果缓存）

        支持格式：
        - 邮箱：zhangsan@company.com
        - 手机号：+8613800138000（必须带国际区号）
        """
        if not feishu_id:
            return ''

        # 缓存命中
        if feishu_id in self._feishu_user_cache:
            return self._feishu_user_cache[feishu_id]

        token = self._get_feishu_token()
        if not token:
            return ''

        # 自动识别类型：以 + 开头且全是数字 → 手机号，否则 → 邮箱
        is_mobile = feishu_id.startswith('+') and feishu_id[1:].replace('-', '').isdigit()

        try:
            # 根据类型构造请求参数
            if is_mobile:
                request_body = {'mobiles': [feishu_id]}
                id_type = 'mobile'
            else:
                request_body = {'emails': [feishu_id]}
                id_type = 'email'

            resp = requests.post(
                'https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id',
                params={'user_id_type': 'open_id'},
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json=request_body,
                timeout=10
            )
            result = resp.json()
            if result.get('code') == 0:
                user_list = result.get('data', {}).get('user_list', [])
                for u in user_list:
                    # 根据类型匹配不同的字段
                    if is_mobile:
                        match_field = u.get('mobile', '') == feishu_id
                    else:
                        match_field = u.get('email', '').lower() == feishu_id.lower()

                    if match_field and u.get('user_id'):
                        open_id = u['user_id']
                        self._feishu_user_cache[feishu_id] = open_id
                        logger.info(f"✅ 飞书用户匹配: {feishu_id} ({id_type}) → {open_id[:8]}...")
                        return open_id
                logger.warning(f"⚠️ 飞书未找到用户: {feishu_id} ({id_type})")
                return ''
            else:
                logger.error(f"❌ 飞书用户查询失败: {result}")
                return ''
        except Exception as e:
            logger.error(f"❌ 飞书用户查询异常: {e}")
            return ''

    def _get_feishu_ids_for_owner(self, owner: str) -> list:
        """获取 owner 对应的所有飞书标识（支持单值字符串、逗号分隔字符串、或列表）。
        返回: ['+86xxx', ...] 或 []（未配置时）
        """
        owner_mapping = self.config.get('feishu_app', {}).get('owner_mapping', {})
        val = owner_mapping.get(owner, '')
        if not val:
            return []
        if isinstance(val, list):
            ids = []
            for v in val:
                # 列表中每项也可能用逗号分隔
                for part in str(v).split(','):
                    part = part.strip()
                    if part:
                        ids.append(part)
            return ids
        # 字符串：按逗号拆分（支持 "+86xxx,+86yyy" 格式）
        ids = []
        for part in str(val).split(','):
            part = part.strip()
            if part:
                ids.append(part)
        return ids

    def send_feishu_private(self, content, owner: str) -> bool:
        """给指定负责人发飞书私聊消息。content 可以是 str（Markdown）或 dict（完整卡片）。
        支持 owner 对应多个飞书标识（手机号/邮箱），逐个发送。"""
        app_cfg = self.config.get('feishu_app', {})
        if not app_cfg.get('enabled', False):
            return False

        feishu_ids = self._get_feishu_ids_for_owner(owner)
        if not feishu_ids:
            logger.debug(f"  ⏭️ 负责人 [{owner}] 未配置飞书账号，跳过私聊推送")
            return False

        token = self._get_feishu_token()
        if not token:
            return False

        all_ok = True
        for feishu_id in feishu_ids:
            open_id = self._get_feishu_open_id(feishu_id)
            if not open_id:
                all_ok = False
                continue

            try:
                # 兼容两种输入：dict（完整卡片）或 str（Markdown 文本）
                if isinstance(content, dict):
                    card_content = content
                else:
                    card_content = {
                        "config": {"wide_screen_mode": True},
                        "elements": [
                            {"tag": "markdown", "content": content}
                        ]
                    }
                payload = {
                    "receive_id": open_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card_content, ensure_ascii=False)
                }
                resp = requests.post(
                    'https://open.feishu.cn/open-apis/im/v1/messages',
                    params={'receive_id_type': 'open_id'},
                    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                    json=payload,
                    timeout=15
                )
                result = resp.json()
                if result.get('code') == 0:
                    logger.info(f"✅ 飞书私聊推送成功 → {owner} ({feishu_id})")
                else:
                    logger.error(f"❌ 飞书私聊推送失败 [{owner}/{feishu_id}]: {result}")
                    all_ok = False
            except Exception as e:
                logger.error(f"❌ 飞书私聊推送异常 [{owner}/{feishu_id}]: {e}")
                all_ok = False

        return all_ok

    def _build_feishu_summary_for_owner(self, summary_time: str, emails_data: List[Dict],
                                         time_range: str = '', platform_name: str = '') -> str:
        """构建给单个负责人的飞书私聊总结（只包含该负责人的邮件）"""
        # 复用现有 _build_feishu_summary 的逻辑，它已经包含完整的格式化
        return self._build_feishu_summary(summary_time, emails_data, time_range, platform_name)

    def _build_feishu_app_summary_card(self, summary_time: str, emails_data: List[Dict],
                                        time_range: str = '',
                                        platform_name: str = '',
                                        show_source: bool = False) -> Dict:
        """
        构建飞书自建应用的总结卡片消息（新格式：极简纯文字 + 级别颜色emoji）。
        show_source=True 时，每条邮件标注来源邮箱（其他平台通知使用）。
        返回一个完整的飞书卡片 dict，可直接传给 send_feishu_private。
        """
        if not emails_data:
            return {}

        # 提取店铺名，从邮件数据推导平台显示名
        owner = emails_data[0].get('owner', '')
        unique_plats = list(dict.fromkeys(e.get('platform', 'OTH') for e in emails_data))
        plat_display_names = [self.platform_names.get(p, p) for p in unique_plats]
        plat_short = '/'.join(plat_display_names)
        header_line = f"{plat_short} {owner}".strip()
        period_line = self._format_summary_period(summary_time, time_range, platform_name)
        total = len(emails_data)

        # 按级别统计
        level_count = {}
        for e in emails_data:
            lv = self._safe_level(e.get('level', 6))
            level_count[lv] = level_count.get(lv, 0) + 1

        content_parts = [f"{header_line}\n{period_line}\n共{total}封\n"]

        # 0-2级：逐封列出主题（其他平台邮件标注来源邮箱）
        for lv in range(3):
            color = self.level_html_colors.get(lv, self.level_html_colors[6])
            cnt = level_count.get(lv, 0)
            lines = [f"{color['emoji']} {lv}级 x{cnt}"]
            if cnt > 0:
                idx = 1
                for e in emails_data:
                    if self._safe_level(e.get('level', 6)) == lv:
                        subj = e.get('subject', '')[:60]
                        line = f"{idx}. {subj}"
                        if show_source:
                            line += f" ← {e.get('source_email', '')}"
                        lines.append(line)
                        idx += 1
            content_parts.append('\n'.join(lines))

        # 3-6级：统一归入「其他」
        low_total = sum(level_count.get(lv, 0) for lv in range(3, 7))

        if low_total > 0:
            content_parts.append(f"其他 x{low_total}")

        content = '\n\n'.join(content_parts)

        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {"tag": "hr"}
        ]

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{period_line} 总结"
                },
                "template": "blue"
            },
            "elements": elements
        }

    # ================================================================== #
    #                         总结流程                                     #
    # ================================================================== #

    def _build_daily_brief(self, summary_time: str, emails_data: List[Dict], time_range: str) -> str:
        """构建 17:00 群聊简化简报：仅统计 0/1/2 级，按店铺（owner）逐行列出数量，不合并"""
        key_emails = [e for e in emails_data if self._safe_level(e.get('level', 6)) <= 2]
        if not key_emails:
            return ''

        # 按 owner 分组统计各级别数量
        owner_levels = {}
        for e in key_emails:
            ow = e.get('owner', '未分配')
            lv = self._safe_level(e.get('level', 6))
            owner_levels.setdefault(ow, {})
            owner_levels[ow][lv] = owner_levels[ow].get(lv, 0) + 1

        # 有 0 级的店铺排最前，其次按 1 级、2 级数量排序
        sorted_owners = sorted(
            owner_levels,
            key=lambda ow: (-owner_levels[ow].get(0, 0),
                            -owner_levels[ow].get(1, 0),
                            -owner_levels[ow].get(2, 0), ow)
        )

        level_emoji = {0: '🔴', 1: '🟠', 2: '🟡'}
        date_str = self._format_summary_date(time_range)

        lines = [
            f"【{summary_time} 全平台简报】{date_str}",
            f"时段：{time_range}",
            '',
        ]
        totals = {0: 0, 1: 0, 2: 0}
        for ow in sorted_owners:
            parts = []
            for lv in (0, 1, 2):
                cnt = owner_levels[ow].get(lv, 0)
                if cnt:
                    parts.append(f"{level_emoji[lv]}{lv}级 x{cnt}")
                    totals[lv] += cnt
            lines.append(f"▪ {ow}：{' '.join(parts)}")

        lines.append('')
        lines.append(f"合计 {sum(totals.values())} 封（0级 {totals[0]} / 1级 {totals[1]} / 2级 {totals[2]}）")
        return '\n'.join(lines)

    def _dispatch_summary(self, emails_data: List[Dict], summary_time: str, time_range: str):
        """
        公共总结分发逻辑：按平台拆分 → 按负责人推送 → 其他平台路由 → 21:00 日总结 → 保存 Excel
        由 run_summary（守护进程定时）和 run_manual_summary（手动触发）共同调用。
        """
        if not emails_data:
            return

        # 按平台拆分邮件（WMS / AMZ / 其他）
        wms_emails = [e for e in emails_data if e.get('platform') == 'WMS']
        amz_emails = [e for e in emails_data if e.get('platform') == 'AMZ']
        other_emails = [e for e in emails_data if e.get('platform') not in ('WMS', 'AMZ')]

        platform_groups = []
        if wms_emails:
            platform_groups.append(('沃尔玛(WMS)', wms_emails))
        if amz_emails:
            platform_groups.append(('亚马逊(AMZ)', amz_emails))
        if not platform_groups and not other_emails:
            platform_groups.append(('全平台', emails_data))

        for plat_name, plat_emails in platform_groups:
            plat_code = plat_name.split('(')[-1].rstrip(')') if '(' in plat_name else ''

            # 按负责人拆分邮件，按渠道分别推送
            owner_emails = {}
            for e in plat_emails:
                ow = e.get('owner', '未分配')
                owner_emails.setdefault(ow, []).append(e)

            for ow, ow_mails in owner_emails.items():
                owner_platforms = self._extract_owner_platforms(ow)
                if plat_code and plat_code not in owner_platforms:
                    logger.debug(f"  ⏭️ {ow} 不负责 {plat_code}，跳过")
                    continue

                channel = self._get_owner_channel(ow)
                send_channels = []
                if channel == 'both':
                    send_channels = ['group', 'private']
                elif channel == 'group':
                    send_channels = ['group']
                else:
                    send_channels = ['private']

                for ch in send_channels:
                    if ch == 'group':
                        feishu_content = self._build_feishu_summary(summary_time, ow_mails, time_range, plat_name)
                        if feishu_content:
                            self.send_feishu_bot(feishu_content)
                    elif ch == 'private':
                        feishu_app_enabled = self.config.get('feishu_app', {}).get('enabled', False)
                        if feishu_app_enabled:
                            ow_card = self._build_feishu_app_summary_card(summary_time, ow_mails, time_range, plat_name)
                            if ow_card:
                                self.send_feishu_private(ow_card, ow)
                        else:
                            ow_content = self._build_feishu_summary_for_owner(summary_time, ow_mails, time_range, plat_name)
                            if ow_content:
                                self.send_feishu_private(ow_content, ow)

        # ★ 其他平台邮件总结：统一发给指定负责人（带来源邮箱）
        other_cfg = self.config.get('other_platform', {})
        if other_cfg.get('enabled', False) and other_emails:
            handler = other_cfg.get('owner', '').strip()
            if handler:
                summary_text = self._build_summary_text(
                    summary_time, other_emails, time_range, show_source=True)
                if summary_text:
                    self.send_feishu_private(summary_text, handler)
                    logger.info(f"📊 其他平台总结已发送给 [{handler}]（{len(other_emails)} 封）")

        # ★ 17:00 额外发送全平台简化简报到群聊（昨天 17:00 ~ 今天 17:00，仅 0/1/2 级数量，按店铺逐行）
        if summary_time == '17:00':
            brief_emails = self._get_daily_brief_emails()
            if not brief_emails:
                brief_emails = emails_data  # 兜底：周期文件不可用时退回当前周期数据
            today = datetime.now().strftime('%Y-%m-%d')
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            brief_range = f"{yesterday} 17:00 ~ {today} 17:00"
            brief_content = self._build_daily_brief(summary_time, brief_emails, brief_range)
            if brief_content:
                self.send_feishu_bot(brief_content)
                logger.info(f"📊 17:00 全平台简报已发送到群聊（24h 数据源）")

        # ★ 21:00 额外发送全平台日总结到群聊
        if summary_time == '21:00':
            if emails_data:
                daily_content = self._build_feishu_summary(summary_time, emails_data, time_range, '全平台日总结')
                if daily_content:
                    self.send_feishu_bot(daily_content)
                    logger.info(f"📊 21:00 全平台日总结已发送到群聊（{len(emails_data)} 封）")

        # 保存总结 Excel（包含全部邮件，不拆分）
        all_summary = self.ai.generate_summary(emails_data, '全平台', time_range)
        if not all_summary:
            all_summary = self.ai._generate_fallback_summary(emails_data, '全平台', time_range)
        self.save_summary_excel(summary_time, emails_data, all_summary)

    def run_summary(self, summary_time: str):
        """执行总结：按平台拆分 → 生成报告 → 推送 → 保存 Excel"""
        slot_name = self._slot_name(summary_time)
        start_time, end_time = self.get_summary_time_range(summary_time)
        logger.info(f"📊 开始生成 {slot_name}总结（{start_time} ~ {end_time}）...")

        emails_data = self.current_cycle_emails

        if not emails_data:
            logger.info(f"📭 {slot_name}周期内没有邮件，跳过总结")
            self.last_summary_time = datetime.now()
            return

        time_range = f"{start_time} ~ {end_time}"

        self._dispatch_summary(emails_data, summary_time, time_range)

        # 标记本轮所有已汇总邮件为已推送（按 slot）
        if self.seen_store is not None:
            try:
                for rec in emails_data:
                    msg_id = (rec.get('msg_id') or '').strip()
                    src = (rec.get('source_email') or '').strip()
                    if msg_id and src:
                        self.seen_store.mark_pushed_in_slot(
                            src, msg_id, summary_time
                        )
            except Exception as e:
                logger.debug(f"  ⚠️ mark_pushed_in_slot 失败: {e}")

        # 重置周期
        self.current_cycle_emails = []
        self.last_summary_time = datetime.now()
        self._update_cycle()

        logger.info(f"✅ {slot_name}总结完成")

        # ★ 总结完成后：检查并优化提示词
        self._run_prompt_optimization()

    def _run_prompt_optimization(self):
        """总结后自动检查并优化提示词"""
        # 检查配置是否启用自动优化
        opt_cfg = self.config.get('prompt_optimizer', {})
        if not opt_cfg.get('enabled', True) or not opt_cfg.get('auto_optimize', True):
            return

        try:
            from _prompt_optimizer import PromptOptimizer
            optimizer = PromptOptimizer(self.config)
            if optimizer.optimize():
                self.ai.reload_prompt()
                logger.info("✅ 提示词优化完成，新规则已生效")
        except ImportError:
            pass  # 优化器不可用时静默跳过
        except Exception as e:
            logger.warning(f"⚠️ 提示词优化失败（不影响主流程）: {e}")

    def _run_weekly_summary(self):
        """周六 17:00 周总结：按平台+负责人分组，统计本周各级别数量"""
        logger.info("📊 开始生成周总结...")

        weekly_emails = self._get_weekly_emails()
        if not weekly_emails:
            logger.info("📭 本周无邮件记录，跳过周总结")
            return

        # 按平台分组
        platform_groups = {}
        for e in weekly_emails:
            plat = e.get('platform', 'OTH')
            platform_groups.setdefault(plat, []).append(e)

        sent_count = 0
        for plat, plat_emails in platform_groups.items():
            plat_name = self.platform_names.get(plat, plat)

            # 按负责人分组
            owner_groups = {}
            for e in plat_emails:
                ow = e.get('owner', '未分配')
                owner_groups.setdefault(ow, []).append(e)

            for ow, ow_mails in owner_groups.items():
                # 其他平台负责人走单独通道
                other_cfg = self.config.get('other_platform', {})
                other_handler = other_cfg.get('owner', '').strip() if other_cfg.get('enabled', False) else ''
                if plat not in ('WMS', 'AMZ') and other_handler:
                    if ow != other_handler:
                        continue  # 非 handler 的不收其他平台邮件
                elif plat not in ('WMS', 'AMZ') and not other_handler:
                    continue  # 没配置 handler 就跳过其他平台

                total = len(ow_mails)
                level_count = {}
                for e in ow_mails:
                    lv = self._safe_level(e.get('level', 6))
                    level_count[lv] = level_count.get(lv, 0) + 1

                lines = [
                    f"【周总结】{plat_name} {ow}",
                    f"本周共 {total} 封",
                    '',
                    f"🔴 0级 x{level_count.get(0, 0)}",
                    f"🟠 1级 x{level_count.get(1, 0)}",
                    f"🟡 2级 x{level_count.get(2, 0)}",
                    f"其他 x{sum(level_count.get(lv, 0) for lv in range(3, 7))}",
                ]
                summary_text = '\n'.join(lines)

                # 按渠道发送：其他平台 → handler 私聊；AMZ/WMS → 按配置走群/私聊
                if plat not in ('WMS', 'AMZ') and other_handler:
                    self.send_feishu_private(summary_text, other_handler)
                else:
                    channel = self._get_owner_channel(ow)
                    feishu_ids = self._get_feishu_ids_for_owner(ow)
                    if channel == 'private' and not feishu_ids:
                        channel = 'group'
                    if channel == 'both':
                        self.send_feishu_bot(summary_text)
                        if feishu_ids:
                            self.send_feishu_private(summary_text, ow)
                    elif channel == 'group':
                        self.send_feishu_bot(summary_text)
                    else:
                        self.send_feishu_private(summary_text, ow)
                sent_count += 1

        # 保存周总结到 Excel
        try:
            summary_dir = Path(self.config.get('reports', {}).get('summary_dir', 'reports/summaries'))
            summary_dir.mkdir(parents=True, exist_ok=True)
            filename = f"weekly_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
            filepath = summary_dir / filename
            wb = Workbook()
            ws = wb.active
            ws.title = '周总结'
            ws.append(['周总结', datetime.now().strftime('%Y-%m-%d %H:%M')])
            ws.append([])
            ws.append(['平台', '负责人', '总数', '0级', '1级', '2级', '其他'])
            for plat, plat_emails in platform_groups.items():
                plat_name = self.platform_names.get(plat, plat)
                owner_groups = {}
                for e in plat_emails:
                    owner_groups.setdefault(e.get('owner', '未分配'), []).append(e)
                for ow, ow_mails in owner_groups.items():
                    lc = {}
                    for e in ow_mails:
                        lc[self._safe_level(e.get('level', 6))] = lc.get(self._safe_level(e.get('level', 6)), 0) + 1
                    ws.append([plat_name, ow, len(ow_mails),
                               lc.get(0, 0), lc.get(1, 0), lc.get(2, 0),
                               sum(lc.get(lv, 0) for lv in range(3, 7))])
            wb.save(filepath)
            logger.info(f"✅ 周总结报表保存: {filepath}")
        except Exception as e:
            logger.error(f"❌ 保存周总结 Excel 失败: {e}")

        logger.info(f"✅ 周总结完成（{sent_count} 条通知）")

    # ================================================================== #
    #                      单次检查流程                                     #
    # ================================================================== #

    def run_check(self) -> List[Dict]:
        """执行一次邮箱检查"""
        self._update_cycle()

        logger.info(f"🔍 第 {len(self.current_cycle_emails) // 10 + 1} 轮检查 "
                     f"(周期: {self.current_cycle_type}, "
                     f"已收集: {len(self.current_cycle_emails)} 封)")

        emails_data = self.fetch_emails()

        if emails_data:
            self.save_to_cycle_excel(emails_data)
            self.current_cycle_emails.extend(emails_data)
            logger.info(f"📊 本轮新增 {len(emails_data)} 封，周期累计 {len(self.current_cycle_emails)} 封")
        else:
            logger.info("📭 本轮无新邮件")

        self.last_check_time = datetime.now()
        return emails_data

    # ================================================================== #
    #                      守护进程主循环                                    #
    # ================================================================== #

    def run_daemon(self):
        """守护进程模式：持续运行"""
        daemon_cfg = self.config.get('daemon', {})
        interval = daemon_cfg.get('check_interval_minutes', 15) * 60  # 秒

        logger.info("=" * 60)
        logger.info(f"🦞 邮件龙虾 v2 守护进程启动")
        logger.info(f"   检查间隔: {interval // 60} 分钟")
        summaries = self.config.get('schedule', {}).get('summaries', ['07:30', '11:30', '21:00'])
        slot_names = [f"{self._slot_name(s)}({s})" for s in summaries]
        logger.info(f"   总结时间: {' / '.join(slot_names)}")
        logger.info(f"   AI 分析: {'✅ 已启用' if self.ai.enabled else '❌ 未启用（使用关键词分类）'}")
        logger.info("=" * 60)

        # 启动时立即执行一次
        self.run_check()

        while True:
            try:
                time.sleep(60)  # 每分钟醒来看一次

                # ★ 配置热更新检测（必须在心跳之前，以便用最新 interval）
                old_interval = interval
                self._check_config_reload()
                # 如果配置改了检查间隔，更新本地变量
                new_interval = self.config.get('daemon', {}).get('check_interval_minutes', 15) * 60
                if new_interval != old_interval:
                    interval = new_interval
                    logger.info(f"🔄 检查间隔已更新为 {interval // 60} 分钟")

                # 心跳（OpenClaw 端探活依赖此文件 mtime；每 60s 刷一次）
                self._write_heartbeat()

                # 检查邮箱（每 N 分钟）
                if self.last_check_time is None or \
                   (datetime.now() - self.last_check_time).total_seconds() >= interval:
                    self.run_check()

                # 检查是否到达总结时间
                summary_type = self.should_run_summary()
                if summary_type:
                    self.run_summary(summary_type)

                # 周六 17:00 额外触发周总结（每个自然周只发一次）
                now = datetime.now()
                # ★ M4修复：窗口扩大到5分钟，使用ISO周数防重复
                if (now.weekday() == 5 and now.hour == 17 and now.minute < 5
                        and getattr(self, '_weekly_summary_sent_week', '') != now.strftime('%G-W%V')):
                    self._run_weekly_summary()
                    self._weekly_summary_sent_week = now.strftime('%G-W%V')  # ISO周数

            except KeyboardInterrupt:
                logger.info("\n⚠️ 守护进程被用户中断")
                break
            except Exception as e:
                logger.error(f"❌ 守护进程异常: {e}", exc_info=True)
                time.sleep(60)

    # ================================================================== #
    #                         单次运行（非守护）                             #
    # ================================================================== #

    def run_once(self):
        """单次运行模式"""
        logger.info("=" * 60)
        logger.info(f"🦞 邮件龙虾 v2 单次运行 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        self.run_check()

        # 如果正好在总结时间，也执行总结
        summary_type = self.should_run_summary()
        if summary_type:
            self.run_summary(summary_type)

    # ================================================================== #
    #                         手动触发总结                                   #
    # ================================================================== #

    def run_manual_summary(self, summary_time: str = None):
        """手动触发总结（命令行 --summary 参数）"""
        # 如果没有指定时间，使用当前最近的总结时间
        if summary_time is None:
            slots = self._get_summary_slots()
            now_minutes = datetime.now().hour * 60 + datetime.now().minute
            summary_time = slots[-1]['time']  # 默认用最后一个时段
            for slot in slots:
                if slot['minutes'] > now_minutes:
                    break
                summary_time = slot['time']

        slot_name = self._slot_name(summary_time)
        start_time, end_time = self.get_summary_time_range(summary_time)
        time_range = f"{start_time} ~ {end_time}"
        logger.info(f"📊 手动触发{slot_name}总结（{time_range}）")

        # 读取当前周期 Excel 中的所有邮件
        filepath = self.get_cycle_excel_path()
        if not filepath.exists():
            logger.info("📭 当前周期无报表文件")
            return

        try:
            wb = load_workbook(filepath)
            ws = wb.active
            emails_data = []
            for row in range(2, ws.max_row + 1):
                record = {
                    'date': ws.cell(row=row, column=1).value or '',
                    'time': ws.cell(row=row, column=2).value or '',
                    'source_email': ws.cell(row=row, column=3).value or '',
                    'owner': ws.cell(row=row, column=4).value or '',
                    'level': (lambda v: int(v) if v and str(v).isdigit() else 6)(ws.cell(row=row, column=5).value),
                    'sub_category': ws.cell(row=row, column=6).value or '其他',
                    'platform': self.platform_normalize.get(
                        ws.cell(row=row, column=7).value or 'OTH',
                        ws.cell(row=row, column=7).value or 'OTH'
                    ),
                    'category': ws.cell(row=row, column=8).value or '',
                    'from': ws.cell(row=row, column=9).value or '',
                    'subject': ws.cell(row=row, column=10).value or '',
                    'ai_summary': ws.cell(row=row, column=11).value or '',
                    'ai_action': ws.cell(row=row, column=12).value or '',
                    'preview': ws.cell(row=row, column=13).value or '',
                }
                emails_data.append(record)

            if not emails_data:
                logger.info("📭 当前周期无邮件记录")
                return

            self._dispatch_summary(emails_data, summary_time, time_range)
            logger.info(f"✅ 手动{slot_name}总结完成")

        except Exception as e:
            logger.error(f"❌ 手动总结失败: {e}", exc_info=True)


# ====================================================================== #
#                             入口                                        #
# ====================================================================== #

def main():
    args = sys.argv[1:]
    lock = None

    try:
        if requires_instance_lock(args):
            mode = 'daemon' if '--once' not in args and '--summary' not in args else 'run'
            lock = SingleInstanceLock(lock_path_for_script(__file__), mode)
            lock.acquire()
            logger.info(f"🔒 已取得单实例锁 (PID {os.getpid()}, 模式: {mode})")

        lobster = MailLobsterIMAP()

        if '--test-feishu' in args:
            # 测试飞书群机器人
            logger.info("🐦 测试飞书群机器人...")
            test_content = f"""**🦞 邮件龙虾 — 飞书推送测试**

**测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**状态**：✅ 连接成功

---

如果您在飞书群聊中看到这条消息，说明飞书群机器人配置正确！
"""
            result = lobster.send_feishu_bot(test_content)
            if result:
                logger.info("✅ 飞书测试消息已发送到群聊！")
            else:
                logger.error("❌ 发送失败，请检查 config 中的 feishu_bot.webhook_url")

        elif '--test-feishu-app' in args:
            # 测试飞书自建应用私聊推送
            logger.info("🐦 测试飞书自建应用私聊推送...")
            app_cfg = lobster.config.get('feishu_app', {})
            if not app_cfg.get('enabled', False):
                logger.error("❌ 飞书自建应用未启用，请在 config 中设置 feishu_app.enabled = true")
            else:
                # 测试 token 获取
                token = lobster._get_feishu_token()
                if not token:
                    logger.error("❌ 获取 token 失败，请检查 app_id 和 app_secret")
                else:
                    logger.info("✅ Token 获取成功")
                    # 测试向每个配置的负责人发送测试消息
                    owner_mapping = app_cfg.get('owner_mapping', {})
                    if not owner_mapping:
                        logger.warning("⚠️ owner_mapping 为空，未配置负责人飞书邮箱")
                    else:
                        test_content = f"""**🦞 邮件龙虾 — 飞书私聊推送测试**

**测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**状态**：✅ 自建应用连接成功

---

如果您在飞书私聊中看到这条消息，说明飞书自建应用配置正确！
"""
                        for owner, email in owner_mapping.items():
                            logger.info(f"  📤 发送测试消息给 [{owner}] ({email})")
                            result = lobster.send_feishu_private(test_content, owner)
                            if result:
                                logger.info(f"  ✅ [{owner}] 发送成功")
                            else:
                                logger.error(f"  ❌ [{owner}] 发送失败")

        elif '--summary' in args:
            # 手动触发总结：可指定时间如 --summary 07:30，否则自动选择最近时段
            summary_time = None
            for arg in args:
                if ':' in arg and arg not in ('--summary',):
                    summary_time = arg
                    break
            lobster.run_manual_summary(summary_time)

        elif '--once' in args:
            # 单次运行
            lobster.run_once()

        elif '--report-error' in args:
            # 分级报错
            import subprocess
            subprocess.run([sys.executable, '-X', 'utf8', '_report_error.py'])

        else:
            # 默认：守护进程模式
            if lobster.config.get('daemon', {}).get('enabled', True):
                lobster.run_daemon()
            else:
                lobster.run_once()

    except AlreadyRunningError as e:
        owner = e.metadata.get('pid') if e.metadata else None
        detail = f"（PID {owner}）" if owner else ""
        logger.error(f"⛔ 已有实例正在运行{detail}，本次启动已取消")
    except LockUnavailableError as e:
        logger.error(f"❌ 单实例锁不可用: {e}")
    except KeyboardInterrupt:
        logger.info("\n⚠️ 用户中断")
    except Exception as e:
        logger.error(f"❌ 程序出错: {e}", exc_info=True)
    finally:
        if lock is not None:
            lock.release()
            logger.info("🔓 单实例锁已释放")


if __name__ == '__main__':
    main()
