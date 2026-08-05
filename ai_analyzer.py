#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 邮件分析器
使用 DeepSeek（或任何 OpenAI 兼容 API）智能分析邮件内容
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ============================================================
#  硬编码默认提示词（当 prompts/ 目录不存在时作为后备）
# ============================================================
HARDCODED_DEFAULT_PROMPT = """你是一个跨境电商邮件分析助手。请分析以下邮件并返回 JSON 格式结果。

邮件信息：
- 发件人：{SENDER}
- 主题：{SUBJECT}
- 正文：{BODY}

请按以下 JSON 格式返回（只返回 JSON，不要其他内容）：
{
    "level": "从以下7级中选一个数字: 0 / 1 / 2 / 3 / 4 / 5 / 6",
    "sub_category": "用2-6个中文字概括邮件核心问题（如：商品下架、货件到仓、系统提醒、账户安全、店铺关停、退款退货）",
    "platform": "平台缩写: WMS(沃尔玛) / AMZ(亚马逊) / TEM(Temu) / SHO(Shopee) / TKB(TikTok) / OTH(其他)",
    "category": "分类名称",
    "summary": "用一句话概括邮件核心内容（20字以内）",
    "action": "建议的处理行动（15字以内）",
    "reason": "判断级别的理由（15字以内）"
}

平台识别提示：
- 沃尔玛(WMS): walmart, WFS, Walmart Marketplace, seller center
- 亚马逊(AMZ): amazon, FBA, FBM, Seller Central, A-to-Z
- Temu(TEM): temu, temumall
- Shopee(SHO): shopee, SPX
- TikTok(TKB): tiktok shop, tiktok seller

★★ 平台判断注意事项（必须遵守）★★
- 物流/货代服务商（如云途物流、递四方、燕文、万邑通、出口易、顺丰国际、DHL、UPS、FedEx 等）发来的邮件不代表新平台。应根据邮件内容中实际涉及的平台判断：如内容提及亚马逊/FBA → AMZ，提及沃尔玛/WFS → WMS。
- 支付服务商、ERP 工具、培训推广等第三方邮件同样根据内容涉及的平台归类，不要归入 OTH。
- OTH（其他）仅用于真正的电商竞品平台，如 OZON、Temu、Shopee、TikTok Shop、eBay、Lazada 等。当邮件来自这些平台的官方通知时，才使用对应的平台代码（TEM/SHO/TKB 等），无法识别具体平台时才用 OTH。

★★★ 关键防误判规则（必须严格遵守）★★★
1. 不要仅因为邮件包含 "immediate action" / "urgent" / "action required" 等措辞就判为0级！
2. 必须看邮件的实际后果：
   - 邮件说"不处理会关店/冻结资金" → 0级
   - 邮件说"订单即将自动取消"（只是单个订单）→ 2级（不影响店铺）
   - 邮件说"需要上传跟踪信息否则取消订单" → 2级（日常业务问题）
   - 邮件说"listing被下架" → 1级
   - 邮件说"店铺被暂停/suspended" → 0级
3. 银行账户/支付信息变更通知 → 1级（账户安全），不是0级！
   - "bank account information has been updated/changed/modified" → 1级-账户安全
   - "payment method changed" / "deposit method updated" → 1级-账户安全
   - 这类邮件只是告知信息被修改，不表示店铺被封或资金被冻，切勿判为0级-店铺关停
4. 识别广告/推销邮件（6级）：
   - 邮件标题/内容包含疑问句形式的营销（如"XX放宽了？""你知道吗？"）
   - 邮件来自服务商/第三方推广
   - 内容主要是介绍服务、推荐工具、培训邀请
   - 标题有"CPSC"但内容是服务商解读而非平台官方通知 → 6级
5. 区分"平台官方通知"vs"服务商解读"：
   - 官方通知（from amazon.com/walmart.com）→ 按实际内容定级
   - 服务商转发/解读 → 通常6级

7级分类标准：

【0级】立即处理的店铺级问题 — 最紧急！
  子类别：店铺关停 / POA申诉 / 重大Case
  - 店铺关停：关店、暂停业务、账户停用/suspended/deactivated、资金冻结、停发货
  - POA申诉：要求提交行动计划(Plan of Action)、整改计划、申诉材料、reinstatement
  - 重大Case：需要立即与平台交涉的重大问题

【1级】有时间处理的店铺级问题 — 重要但不紧急
  子类别：系统质检 / 商品下架 / 认证审核 / 账户安全
  - 系统质检：质检、CPC审查、产品安全、合规审查
  - 商品下架：Listing被下架、产品被移除、商品不可售、不合规
  - 认证审核：KYC认证、身份验证、税务信息验证、seller verification
  - 账户安全：银行账户/支付方式被修改、登录异常、密码变更、两步验证变更

【2级】链接级问题或日常业务
  子类别：系统调整 / 资金付款 / 货件到仓 / 链接合规 / 系统提醒 / 退款退货 / 业务调整
  - 系统调整：平台政策变更、系统业务调整
  - 资金付款：付款、结算、资金、余额、disbursement
  - 货件到仓：货件到仓、入库、shipment received
  - 链接合规：链接级别合规问题、ASIN合规
  - 系统提醒：系统通知、需要关注的提醒（包含"需要上传跟踪信息""订单即将取消"等）
  - 退款退货：退款、退货、A-to-Z索赔、chargeback
  - 业务调整：运营层面的业务调整

【3级】客服来信
  子类别：客服来信
  - 客户咨询、投诉、买家消息、产品问题询问

【4级】日常管理信息
  子类别：日常管理
  - 运营报告、管理通知、绩效信息

【5级】系统日常通知
  子类别：出单通知 / 发货通知
  - 出单通知：新订单、订单确认
  - 发货通知：已发货、物流、tracking

【6级】活动推荐 / 广告推销 / 不重要
  子类别：活动推荐 / 广告推销
  - 活动推荐：平台活动、促销推荐、学习信息、培训
  - 广告推销：推销广告、服务商推广、营销邮件
  - 第三方对平台政策的解读（非官方通知）
  - 标题含政策关键词但正文是推销内容

{LEARNED_RULES}"""

DEFAULT_SYSTEM_MESSAGE = "你是邮件分析助手，只返回 JSON 格式，不要 markdown 标记。重要：不要被邮件中的'urgent/immediate action required'等措辞误导，要看邮件的实际后果（是否影响店铺存亡）。服务商转发/解读类邮件属于广告推销（6级）。"

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class AIAnalyzer:
    """AI 邮件智能分析器"""

    def __init__(self, config: Dict):
        self.config = config
        self.enabled = config.get('enabled', False)

        if self.enabled and not HAS_OPENAI:
            logger.warning("⚠️ openai 库未安装，AI 分析不可用")
            logger.warning("   请运行: pip install openai")
            self.enabled = False

        if self.enabled:
            api_key = config.get('api_key', '')
            if not api_key or api_key.startswith('你的'):
                logger.warning("⚠️ AI API Key 未配置，将使用关键词分类")
                self.enabled = False
            else:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=config.get('base_url', 'https://api.deepseek.com/v1')
                )
                self.model = config.get('model', 'deepseek-chat')
                logger.info(f"✅ AI 分析已启用 (模型: {self.model})")

        # ★ 提示词模板加载
        self.prompt_version = 'v1'
        self.learned_rules_version = 'v0'
        self._prompt_template = HARDCODED_DEFAULT_PROMPT
        self._learned_rules = ''
        self._system_message = DEFAULT_SYSTEM_MESSAGE
        self._load_prompt_template()

    def _load_prompt_template(self):
        """从文件加载提示词模板和补充规则，失败则使用硬编码默认值"""
        prompt_path = Path('prompts/classification_prompt_v1.txt')
        if prompt_path.exists():
            try:
                self._prompt_template = prompt_path.read_text(encoding='utf-8')
                logger.debug(f"📄 已加载外部提示词模板: {prompt_path}")
            except Exception as e:
                logger.warning(f"⚠️ 读取提示词模板失败，使用默认: {e}")
                self._prompt_template = HARDCODED_DEFAULT_PROMPT

        rules_path = Path('prompts/learned_rules.txt')
        if rules_path.exists():
            try:
                self._learned_rules = rules_path.read_text(encoding='utf-8')
                # 解析版本号
                for line in self._learned_rules.splitlines():
                    if line.startswith('# 版本: v'):
                        self.learned_rules_version = line.split('v')[-1].strip()
                        break
                logger.debug(f"📄 已加载补充规则 (版本: v{self.learned_rules_version})")
            except Exception as e:
                logger.warning(f"⚠️ 读取补充规则失败: {e}")
                self._learned_rules = ''

    def reload_prompt(self):
        """热加载最新规则（优化后调用）"""
        self._load_prompt_template()
        logger.info(f"🔄 提示词已重新加载 (模板: {self.prompt_version}, 规则: v{self.learned_rules_version})")

    def analyze(self, subject: str, body: str, sender: str = '') -> Optional[Dict]:
        """
        用 AI 分析邮件内容

        返回:
            {
                "level": "0-7 数字级别",
                "sub_category": "子类别（如 平台Case/写POA/下架通知/退货退款通知/身份验证邮件/客服来信/取消订单/其他）",
                "platform": "平台缩写: WMS(沃尔玛) / AMZ(亚马逊) / TEM(Temu) / SHO(Shopee) / TKB(TikTok) / OTH(其他)",
                "category": "分类名称",
                "summary": "一句话摘要",
                "action": "建议行动",
                "reason": "判断理由"
            }
            失败时返回 None
        """
        if not self.enabled:
            return None

        try:
            # ★ 构建提示词（从外部模板替换占位符）
            prompt = self._prompt_template.replace('{SENDER}', sender or '未知')
            prompt = prompt.replace('{SUBJECT}', subject)
            prompt = prompt.replace('{BODY}', body[:2000] if body else '(无正文)')
            prompt = prompt.replace('{LEARNED_RULES}', self._learned_rules if self._learned_rules and not self._learned_rules.startswith('#') else '(暂无补充规则)')

            # ★ M8修复：API 限流重试（最多 2 次，间隔递增）
            response = None
            for attempt in range(3):
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": self._system_message},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        max_tokens=500,
                        timeout=30
                    )
                    break
                except Exception as e:
                    if attempt < 2 and '429' in str(e):
                        delay = 2 ** (attempt + 1)  # 2s, 4s
                        logger.warning(f"  ️ API 限流，{delay}s 后重试...")
                        import time
                        time.sleep(delay)
                    else:
                        raise

            content = response.choices[0].message.content.strip()

            # ★ 空内容重试（AI 偶尔返回空字符串）
            if not content:
                for retry in range(2):
                    logger.warning(f"  ⚠️ AI 返回空内容，重试 ({retry + 1}/2)...")
                    import time
                    time.sleep(1)
                    try:
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=[
                                {"role": "system", "content": self._system_message},
                                {"role": "user", "content": prompt}
                            ],
                            temperature=0.3,
                            max_tokens=500,
                            timeout=30
                        )
                        content = response.choices[0].message.content.strip()
                        if content:
                            break
                    except Exception:
                        pass

            result = self._parse_json_response(content)

            if result is None:
                logger.warning(f"  ⚠️ AI 返回格式错误，无法解析 JSON")
                return None

            # 验证返回格式
            required_keys = ['level', 'sub_category', 'platform', 'category', 'summary', 'action', 'reason']
            for key in required_keys:
                if key not in result:
                    result[key] = ''

            # 验证级别（0-6 数字）
            valid_levels = [0, 1, 2, 3, 4, 5, 6]
            try:
                result['level'] = int(result['level'])
            except (ValueError, TypeError):
                result['level'] = 6
            if result['level'] not in valid_levels:
                result['level'] = 6

            # 不再强制匹配预设列表，信任 AI 输出的四字总结词
            # 仅当 sub_category 为空时才给默认值
            valid_sub_cats = {
                0: ['店铺关停', 'POA申诉', '重大Case'],
                1: ['系统质检', '商品下架', '认证审核', '账户安全'],
                2: ['系统调整', '资金付款', '货件到仓', '链接合规', '系统提醒', '退款退货', '业务调整'],
                3: ['客服来信'],
                4: ['日常管理'],
                5: ['出单通知', '发货通知'],
                6: ['活动推荐', '广告推销']
            }
            if not result.get('sub_category') or result['sub_category'].strip() == '':
                result['sub_category'] = valid_sub_cats.get(result['level'], ['其他'])[0]
            # ★ M9修复：截断 sub_category 到 10 字符
            result['sub_category'] = result['sub_category'][:10]

            logger.info(f"  🤖 AI 分析: [L{result['level']}] [{result['sub_category']}] {result['summary'][:30]}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"  ⚠️ AI 返回格式错误: {e}")
            return None
        except Exception as e:
            logger.warning(f"  ⚠️ AI 分析失败: {e}")
            return None

    def _parse_json_response(self, content: str) -> Optional[Dict]:
        """健壮地解析 AI 返回的 JSON，处理各种格式问题"""
        # 策略 1: 清理 markdown 代码块
        if '```' in content:
            m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if m:
                content = m.group(1).strip()
            else:
                content = content.replace('```', '').strip()

        # 策略 2: 提取第一个 { 到最后一个 } 之间的内容
        start = content.find('{')
        end = content.rfind('}')
        if start >= 0 and end > start:
            json_str = content[start:end + 1]
        else:
            json_str = content

        # 尝试直接解析
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # 策略 3: 修复单引号 → 双引号
        try:
            fixed = json_str.replace("'", '"')
            return json.loads(fixed)
        except (json.JSONDecodeError, ValueError):
            pass

        # 策略 4: 用正则提取各字段值
        try:
            result = {}
            keys = ['level', 'sub_category', 'platform', 'category', 'summary', 'action', 'reason']
            # 构建引号匹配模式（避免 raw string 中 \' 的歧义）
            SQ = "'"  # 单引号
            Q = '["' + SQ + ']'       # 匹配 " 或 '
            nQ = '[^"' + SQ + ']'     # 匹配非引号字符
            for key in keys:
                # 匹配 "key": "value" 或 'key': 'value' 或 key: "value"
                pat_str = Q + '?' + re.escape(key) + Q + '?' + r'\s*[:=]\s*' + Q + '(' + nQ + '*)' + Q
                m = re.search(pat_str, json_str)
                if m:
                    result[key] = m.group(1)
                else:
                    # 尝试匹配数字值 (如 "level": 1)
                    pat_num = Q + '?' + re.escape(key) + Q + '?' + r'\s*[:=]\s*(\d+)'
                    m = re.search(pat_num, json_str)
                    if m:
                        result[key] = m.group(1)
            if result and 'level' in result:
                logger.debug(f"  🔧 正则提取 JSON: {list(result.keys())}")
                return result
        except Exception:
            pass

        # 策略 5: 逐字段容忍式提取（处理截断/换行/未闭合引号的脏 JSON）
        # 原理：每个字段独立提取，不依赖整体 JSON 语法正确。
        # 匹配模式：key 后面跟 : 号，然后吃掉所有内容直到遇到 ", "key": 或行尾。
        try:
            result = {}
            keys = ['level', 'sub_category', 'platform', 'category', 'summary', 'action', 'reason']
            for key in keys:
                # 定位 "key": 或 key: 的位置
                pat_key = re.escape(key) + r'''["']?\s*[:]\s*["']?'''
                m = re.search(pat_key, content, re.IGNORECASE)
                if not m:
                    continue
                start_pos = m.end()

                # 从 : 后面开始，找到下一个字段的起始位置（"其他key": 模式）
                # 这样就能截取当前字段的值，即使它内部有换行或引号
                next_field = re.search(
                    r''',\s*["'](?:level|sub_category|platform|category|summary|action|reason)["']?\s*[:''',
                    content[start_pos:]
                )
                if next_field:
                    raw_val = content[start_pos:start_pos + next_field.start()].strip()
                else:
                    # 没有下一个字段了，取到行尾或文件尾
                    raw_val = content[start_pos:].strip()
                    # 去掉尾部可能的 } ] 等符号
                    raw_val = re.sub(r'[\s}\]]+$', '', raw_val)

                # 清理：去掉首尾的引号、逗号、空白
                raw_val = raw_val.strip(' \t\n\r",\'')

                if key == 'level':
                    # level 只取数字
                    dm = re.search(r'\d+', raw_val)
                    if dm:
                        result[key] = dm.group(0)
                elif raw_val:
                    result[key] = raw_val

            if result and 'level' in result:
                logger.debug(f"  🔧 容忍式逐字段提取: {list(result.keys())}")
                return result
        except Exception:
            pass

        # 全部失败
        logger.warning(f"  ⚠️ JSON 解析全部失败，原始内容前100字: {content[:100]}")
        return None

    def generate_summary(self, emails_data: list, platform_name: str = '', time_range: str = '') -> str:
        """
        用 AI 生成邮件总结报告

        参数:
            emails_data: 邮件记录列表
            platform_name: 平台名称（如 "沃尔玛(WMS)"、"亚马逊(AMZ)"）
            time_range: 时间段（如 "2026-07-14 07:30 ~ 11:30"）

        返回:
            格式化的总结文本
        """
        if not self.enabled or not emails_data:
            return ''

        try:
            # 构建邮件列表文本
            email_list = []
            for i, e in enumerate(emails_data, 1):
                level = e.get('level', 7)
                if isinstance(level, str):
                    try:
                        level = int(level)
                    except (ValueError, TypeError):
                        level = 7
                email_list.append(
                    f"{i}. [L{level}] {e.get('owner', '')} | "
                    f"主题: {e.get('subject', '')} | "
                    f"发件人: {e.get('from', '')} | "
                    f"AI摘要: {e.get('ai_summary', '')}"
                )

            emails_text = '\n'.join(email_list)
            plat_info = f"平台：{platform_name}" if platform_name else "全平台"
            time_info = f"时间段：{time_range}" if time_range else ""

            prompt = f"""以下是{plat_info}在{time_info}收到的 {len(emails_data)} 封邮件记录：

{emails_text}

请生成一份简洁的总结报告，严格按以下格式输出：

---
共 {len(emails_data)} 封

[0级-紧急]（X封）
• 邮件主题1
• 邮件主题2

[1级-重要]（X封）
• 邮件主题1

[2级-日常]（X封）
• 邮件主题1
• 邮件主题2
• 邮件主题3

[3-6级] 共X封（3级:X封 4级:X封 5级:X封 6级:X封）
---

规则：
1. 0级、1级、2级：必须逐封列出邮件主题（只写主题，不写发件人等详情）
2. 3-6级：只显示各级别数量汇总，不展开具体邮件
3. 如果某个级别没有邮件，直接跳过不显示该级别
4. 级别顺序必须从0到6，数字从小到大
5. 0级最重要，6级最不重要，数字越小越重要
6. 不要写"行动建议"，不要写多余的分析，只按上面格式输出

请用中文回复，简洁明了。"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是邮件管理助手。严格按格式输出总结，不要添加额外分析。级别0最重要、6最不重要。0/1/2级列出每封邮件主题，3-6级只报数量。注意：服务商解读类邮件（标题含CPSC/KYC等但来自非官方）属于广告推销（6级）。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=1000,
                timeout=30
            )

            summary = response.choices[0].message.content.strip()
            return summary

        except Exception as e:
            logger.warning(f"  ⚠️ AI 总结生成失败: {e}")
            return self._generate_fallback_summary(emails_data, platform_name, time_range)

    def _generate_fallback_summary(self, emails_data: list, platform_name: str = '', time_range: str = '') -> str:
        """AI 不可用时的简单统计总结"""
        total = len(emails_data)
        level_count = {}
        level_names = {0: '0级-紧急', 1: '1级-重要', 2: '2级-日常', 3: '3级-客服',
                       4: '4级-管理', 5: '5级-通知', 6: '6级-无关'}
        for e in emails_data:
            level = e.get('level', 6)
            if isinstance(level, str):
                try:
                    level = int(level)
                except (ValueError, TypeError):
                    level = 6
            # Clamp to 0-6
            if level > 6:
                level = 6
            level_count[level] = level_count.get(level, 0) + 1

        lines = [f"共 {total} 封", ""]

        # 0-2级展开
        for level in range(3):
            count = level_count.get(level, 0)
            if count > 0:
                lines.append(f"[{level_names.get(level, f'{level}级')}]（{count}封）")
                for e in emails_data:
                    lv = e.get('level', 6)
                    if isinstance(lv, str):
                        try: lv = int(lv)
                        except: lv = 6
                    if lv > 6: lv = 6
                    if lv == level:
                        lines.append(f"  • {e.get('subject', '')[:50]}")
                lines.append("")

        # 3-6级汇总
        low_total = sum(level_count.get(l, 0) for l in range(3, 7))
        if low_total > 0:
            parts = [f"{l}级:{level_count.get(l, 0)}封" for l in range(3, 7) if level_count.get(l, 0) > 0]
            lines.append(f"[3-6级] 共{low_total}封（{' '.join(parts)}）")

        return '\n'.join(lines)
