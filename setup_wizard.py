#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件龙虾 v2 - 首次设置向导
引导新用户完成所有配置，生成 config_imap_forward.json
"""

import json
import os
from pathlib import Path

CONFIG_FILE = 'config_imap_forward.json'

# ANSI colors for terminal
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RED = '\033[91m'
BOLD = '\033[1m'
RESET = '\033[0m'

TOTAL_STEPS = 5


def print_banner():
    print()
    print(f"{CYAN}{'=' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  🦞 邮件龙虾 v2 - 首次设置向导{RESET}")
    print(f"{CYAN}{'=' * 60}{RESET}")
    print()
    print(f"  欢迎使用邮件龙虾！本向导将引导您完成初始配置。")
    print(f"  整个过程大约需要 5 分钟。")
    print()
    print(f"  {YELLOW}配置内容：{RESET}")
    print(f"    1. 汇总邮箱 IMAP 设置（接收转发邮件的邮箱）")
    print(f"    2. 来源邮箱映射（Outlook 邮箱 → 负责人，程序自动识别）")
    print(f"    3. AI 智能分析（可选，DeepSeek / 通义千问 / Moonshot）")
    print(f"    4. 飞书群机器人（可选，通知推送到飞书群聊）")
    print(f"    5. 飞书自建应用（可选，通知私聊推送给负责人）")
    print()
    print(f"  {YELLOW}功能特性：{RESET}")
    print(f"    - 每 5 分钟检查邮箱")
    print(f"    - 每天 4 次总结（07:30 / 11:30 / 17:00 / 21:00）")
    print(f"    - 7 级分类体系：")
    print(f"      0级: 立即处理（关店/暂停/POA）")
    print(f"      1级: 有时间处理（质检/下架/KYC）")
    print(f"      2级: 链接级/日常业务（退款/付款/货件）")
    print(f"      3级: 客服来信")
    print(f"      4级: 日常管理信息")
    print(f"      5级: 系统通知（出单/发货）")
    print(f"      6级: 活动推荐/广告推销/不重要")
    print(f"    - 平台识别：WMS(沃尔玛) / AMZ(亚马逊) / TEM / SHO / TKB")
    print()
    print(f"  {CYAN}程序会自动从转发邮件头识别源邮箱，无需手动设置前缀！{RESET}")
    print()


def input_with_default(prompt, default=''):
    """带默认值的输入"""
    if default:
        result = input(f"  {prompt} [{YELLOW}{default}{RESET}]: ").strip()
        return result if result else default
    else:
        return input(f"  {prompt}: ").strip()


def confirm(prompt, default=True):
    """确认输入"""
    suffix = "Y/n" if default else "y/N"
    result = input(f"  {prompt} [{YELLOW}{suffix}{RESET}]: ").strip().lower()
    if not result:
        return default
    return result in ('y', 'yes', '是')


def step_imap():
    """步骤1：汇总邮箱 IMAP 配置"""
    print(f"\n{GREEN}{'─' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  📧 步骤 1/{TOTAL_STEPS}：汇总邮箱 IMAP 设置{RESET}")
    print(f"{GREEN}{'─' * 60}{RESET}")
    print()
    print(f"  请提供一个用于 {BOLD}接收所有 Outlook 转发邮件{RESET} 的汇总邮箱。")
    print(f"  推荐：QQ 邮箱、139邮箱、163 邮箱（需要开启 IMAP 并获取授权码）")
    print()
    print(f"  {YELLOW}如何获取 QQ 邮箱授权码：{RESET}")
    print(f"    QQ邮箱 → 设置 → 账户 → POP3/IMAP/SMTP服务 → 开启")
    print(f"    → 按提示发短信 → 获得授权码（不是QQ密码！）")
    print()

    presets = {
        '1': ('QQ邮箱', 'imap.qq.com', 993),
        '2': ('139邮箱', 'imap.139.com', 993),
        '3': ('163邮箱', 'imap.163.com', 993),
        '4': ('Gmail', 'imap.gmail.com', 993),
        '5': ('自定义', '', 993),
    }

    print(f"  选择邮箱类型：")
    for key, (name, host, port) in presets.items():
        print(f"    {key}. {name}" + (f" ({host})" if host else ""))
    print()

    choice = input_with_default("请选择 (1-5)", "1")
    name, host, port = presets.get(choice, presets['1'])

    if choice == '5':
        host = input_with_default("  IMAP 服务器地址", "imap.example.com")
        port = int(input_with_default("  IMAP 端口", "993"))

    print()
    email_addr = input_with_default(f"  {name}地址", "your_email@qq.com")
    password = input_with_default(f"  授权码（不是登录密码）", "")

    while not password:
        print(f"  {RED}⚠️ 授权码不能为空{RESET}")
        password = input_with_default(f"  授权码", "")

    return {
        'host': host,
        'port': port,
        'email': email_addr,
        'password': password,
    }


def step_mailboxes():
    """步骤2：来源邮箱映射"""
    print(f"\n{GREEN}{'─' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  📬 步骤 2/{TOTAL_STEPS}：来源邮箱映射{RESET}")
    print(f"{GREEN}{'─' * 60}{RESET}")
    print()
    print(f"  配置每个需要监控的 {BOLD}Outlook 邮箱{RESET} 及其负责人。")
    print()
    print(f"  {CYAN}程序会自动从转发邮件的头部识别源邮箱，无需设置主题前缀！{RESET}")
    print(f"  只需填写 Outlook 邮箱地址和负责人即可。")
    print()
    print(f"  例如：user_a@outlook.com → 负责人 A")
    print(f"         user_b@outlook.com → 负责人 B")
    print()

    mailboxes = []
    i = 1
    while True:
        print(f"  {YELLOW}--- 邮箱 #{i} ---{RESET}")
        source_email = input_with_default(f"  Outlook 邮箱地址", "")
        owner = input_with_default(f"  负责人姓名", "")

        print(f"  {CYAN}通知渠道：group = 飞书群聊推送，private = 飞书私聊推送{RESET}")
        channel = input_with_default(f"  通知渠道 (group/private)", "group")
        if channel not in ('group', 'private'):
            channel = 'group'

        print(f"  {CYAN}(可选) 转发前缀 — 程序能自动识别源邮箱，前缀仅作备用{RESET}")
        prefix = input_with_default(f"  转发前缀（直接回车跳过）", "")

        if source_email and owner:
            entry = {
                'source_email': source_email,
                'owner': owner,
                'notify_channel': channel,
            }
            if prefix:
                entry['prefix'] = prefix
            mailboxes.append(entry)
            ch_display = "群聊" if channel == "group" else "私聊"
            if prefix:
                print(f"  {GREEN}✅ 已添加: {source_email} ({owner}) [{ch_display}]，前缀: {prefix}{RESET}")
            else:
                print(f"  {GREEN}✅ 已添加: {source_email} ({owner}) [{ch_display}]{RESET}")
        else:
            print(f"  {RED}⚠️ 邮箱地址和负责人不能为空，请重新填写{RESET}")
            continue

        i += 1
        print()
        if not confirm("  是否继续添加更多邮箱？", default=False):
            break
        print()

    if not mailboxes:
        print(f"  {RED}⚠️ 至少需要配置一个来源邮箱{RESET}")
        mailboxes.append({
            'prefix': '[负责人]',
            'source_email': 'example@outlook.com',
            'owner': '负责人',
        })

    return mailboxes


def step_ai():
    """步骤3：AI 配置"""
    print(f"\n{GREEN}{'─' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  🤖 步骤 3/{TOTAL_STEPS}：AI 智能分析（可选）{RESET}")
    print(f"{GREEN}{'─' * 60}{RESET}")
    print()
    print(f"  AI 分析邮件内容，自动识别 7 级分类和平台来源。")
    print()
    print(f"  {YELLOW}费用参考：DeepSeek 约 1 元/1000封邮件{RESET}")
    print()

    if not confirm("  是否启用 AI 分析？", default=False):
        return {'enabled': False}

    presets = {
        '1': ('DeepSeek', 'https://api.deepseek.com/v1', 'deepseek-chat'),
        '2': ('通义千问', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus'),
        '3': ('Moonshot', 'https://api.moonshot.cn/v1', 'moonshot-v1-8k'),
    }

    print()
    print(f"  选择 AI 服务：")
    for key, (name, url, model) in presets.items():
        print(f"    {key}. {name}")
    print()

    choice = input_with_default("请选择 (1-3)", "1")
    name, base_url, model = presets.get(choice, presets['1'])

    print()
    print(f"  {YELLOW}如何获取 API Key：{RESET}")
    if 'deepseek' in base_url:
        print(f"    访问 https://platform.deepseek.com → 注册 → API Keys → 创建")
    elif 'aliyuncs' in base_url:
        print(f"    访问 https://dashscope.console.aliyun.com → 获取 API Key")
    elif 'moonshot' in base_url:
        print(f"    访问 https://platform.moonshot.cn → 注册 → 获取 API Key")

    print()
    api_key = input_with_default(f"  {name} API Key", "")

    return {
        'enabled': bool(api_key),
        'provider': name.lower(),
        'base_url': base_url,
        'api_key': api_key if api_key else '你的API_Key',
        'model': model,
    }


def step_feishu_app(mailboxes):
    """步骤5：飞书自建应用配置（私聊推送）"""
    # 从步骤2收集的所有负责人
    owners = []
    seen = set()
    for mb in mailboxes:
        o = mb.get('owner', '')
        if o and o not in seen:
            owners.append(o)
            seen.add(o)

    print(f"\n{GREEN}{'─' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  🤖 步骤 5/{TOTAL_STEPS}：飞书自建应用（可选，私聊推送）{RESET}")
    print(f"{GREEN}{'─' * 60}{RESET}")
    print()
    print(f"  飞书企业自建应用可以向负责人发送 {BOLD}私聊消息{RESET}。")
    print(f"  需要在飞书开放平台创建应用，获取 app_id 和 app_secret。")
    print()
    print(f"  {YELLOW}创建步骤：{RESET}")
    print(f"    1. 访问 https://open.feishu.cn/app")
    print(f"    2. 创建企业自建应用")
    print(f"    3. 开通权限：contact:user.id:readonly、contact:user.email:readonly、im:message:send_as_bot")
    print(f"    4. 发布应用，获取 App ID 和 App Secret")
    print()

    if not confirm("  是否配置飞书自建应用？", default=False):
        return {'enabled': False}

    print()
    app_id = input_with_default("  App ID", "")
    app_secret = input_with_default("  App Secret", "")

    if not app_id or not app_secret:
        print(f"  {YELLOW}App ID 或 App Secret 未填写，飞书自建应用暂不启用{RESET}")
        return {'enabled': False}

    # 为每个负责人收集飞书标识（邮箱或手机号）
    print()
    print(f"  {CYAN}请为每个负责人填写飞书邮箱或手机号（格式：+8613800138000）{RESET}")
    print(f"  {CYAN}直接回车跳过该负责人（跳过 = 不发私聊）{RESET}")
    owner_mapping = {}
    for owner in owners:
        print()
        feishu_id = input_with_default(f"  {owner} 的飞书邮箱或手机号（回车跳过）", "")
        if feishu_id:
            owner_mapping[owner] = feishu_id
            print(f"  {GREEN}✅ {owner} → {feishu_id}{RESET}")
        else:
            print(f"  {YELLOW}⏭️ {owner} 已跳过{RESET}")

    if not owner_mapping:
        print(f"  {YELLOW}未配置任何负责人映射，飞书自建应用暂不启用{RESET}")
        return {'enabled': False}

    return {
        'enabled': True,
        'app_id': app_id,
        'app_secret': app_secret,
        'owner_mapping': owner_mapping,
        'notify_levels': [0, 1, 2],
    }


def step_feishu():
    """步骤4：飞书群机器人配置"""
    print(f"\n{GREEN}{'─' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  💬 步骤 4/{TOTAL_STEPS}：飞书群机器人（可选）{RESET}")
    print(f"{GREEN}{'─' * 60}{RESET}")
    print()
    print(f"  将邮件通知推送到飞书群聊，团队成员都能看到。")
    print()

    if not confirm("  是否启用飞书群机器人？", default=False):
        return {'enabled': False}

    print()
    print(f"  {YELLOW}如何添加飞书群机器人：{RESET}")
    print(f"    1. 打开飞书，进入要接收通知的群聊")
    print(f"    2. 群设置 → 群机器人 → 添加机器人 → 自定义机器人")
    print(f"    3. 复制 Webhook URL（格式：https://open.feishu.cn/open-apis/bot/v2/hook/xxx）")
    print()

    webhook_url = input_with_default("  Webhook URL", "")

    if not webhook_url or 'hook' not in webhook_url:
        print(f"  {YELLOW}Webhook URL 格式不正确，飞书功能暂不启用（后续可在配置文件中修改）{RESET}")
        return {'enabled': False}

    secret = input_with_default("  签名校验密钥（可选，直接回车跳过）", "")

    return {
        'enabled': True,
        'webhook_url': webhook_url,
        'secret': secret,
        'notify_levels': [0, 1, 2],
    }


def show_summary(config):
    """显示配置摘要"""
    print(f"\n{CYAN}{'=' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  📋 配置摘要{RESET}")
    print(f"{CYAN}{'=' * 60}{RESET}")
    print()
    print(f"  目标邮箱: {config['imap']['email']}")
    print(f"  IMAP 服务器: {config['imap']['host']}:{config['imap']['port']}")
    print()
    print(f"  来源邮箱映射（自动从邮件头识别）:")
    for mb in config['mailbox_mapping']['mailboxes']:
        prefix_info = f"，前缀: {mb['prefix']}" if mb.get('prefix') else ""
        print(f"    {mb['source_email']} → {mb['owner']}{prefix_info}")
    print()
    ai = config.get('ai', {})
    if ai.get('enabled'):
        print(f"  AI 分析: ✅ {ai.get('provider', 'deepseek')} ({ai.get('model', '')})")
    else:
        print(f"  AI 分析: ❌ 未启用（使用关键词分类）")

    fb = config.get('feishu_bot', {})
    if fb.get('enabled'):
        print(f"  飞书群通知: ✅ 已启用（0级+1级+2级即时推送）")
    else:
        print(f"  飞书群通知: ❌ 未启用")

    fa = config.get('feishu_app', {})
    if fa.get('enabled'):
        mapping_count = len(fa.get('owner_mapping', {}))
        print(f"  飞书私聊: ✅ 已启用（{mapping_count} 位负责人）")
    else:
        print(f"  飞书私聊: ❌ 未启用")

    print()
    print(f"  检查间隔: {config['daemon']['check_interval_minutes']} 分钟")
    summaries = config.get('schedule', {}).get('summaries', [])
    if summaries:
        print(f"  总结时间: {' / '.join(summaries)}")
    print()
    print(f"  分类体系 (0-6级):")
    print(f"    0级: 立即处理 → 店铺关停 / POA申诉 / 重大Case")
    print(f"    1级: 有时间处理 → 系统质检 / 商品下架 / KYC认证")
    print(f"    2级: 链接级/日常 → 退款退货 / 资金付款 / 货件到仓")
    print(f"    3级: 客服来信")
    print(f"    4级: 日常管理信息")
    print(f"    5级: 系统通知 → 出单 / 发货")
    print(f"    6级: 活动推荐 / 广告推销 / 不重要")
    print()


def build_config(imap_cfg, mailboxes, ai_cfg, feishu_cfg, feishu_app_cfg):
    """构建完整配置文件"""
    # 为每个邮箱添加 notify_channel 字段
    for mb in mailboxes:
        if 'notify_channel' not in mb:
            mb['notify_channel'] = 'group'

    return {
        'auth_type': 'imap_forward',
        'imap': imap_cfg,
        'mailbox_mapping': {
            'mailboxes': mailboxes,
            'unknown_prefix_action': 'default_owner',
            'default_channel': 'private',
        },
        'ai': ai_cfg,
        'feishu_bot': feishu_cfg,
        'feishu_app': feishu_app_cfg,
        'rules': [
            {
                "name": "店铺关闭/暂停",
                "level": 0,
                "sub_category": "店铺关停",
                "keywords": ["账户停用", "account suspended", "账户暂停", "account deactivated", "资金冻结", "frozen", "店铺被封", "关店", "暂停业务", "停发货", "停止销售", "selling privileges removed", "deactivated"]
            },
            {
                "name": "写POA",
                "level": 0,
                "sub_category": "POA申诉",
                "keywords": ["plan of action", "POA", "整改计划", "行动计划", "corrective action", "reinstatement", "appeal", "申诉"]
            },
            {
                "name": "重大Case",
                "level": 0,
                "sub_category": "重大Case",
                "keywords": ["重大case", "major case", "紧急处理", "account health", "policy violation", "account at risk"]
            },
            {
                "name": "质检/CPC",
                "level": 1,
                "sub_category": "系统质检",
                "keywords": ["质检", "quality check", "CPC", "产品安全", "product safety", "compliance review", "合规审查"]
            },
            {
                "name": "商品下架",
                "level": 1,
                "sub_category": "商品下架",
                "keywords": ["listing removed", "下架", "listing deactivated", "商品移除", "removed from sale", "不可售", "inactive", "不合规", "non-compliant"]
            },
            {
                "name": "KYC/税务认证",
                "level": 1,
                "sub_category": "认证审核",
                "keywords": ["verify your identity", "身份验证", "identity verification", "KYC", "税务验证", "tax information", "seller verification", "认证", "verification"]
            },
            {
                "name": "系统业务调整",
                "level": 2,
                "sub_category": "系统调整",
                "keywords": ["系统调整", "system update", "政策变更", "policy update", "业务调整", "platform change"]
            },
            {
                "name": "资金付款",
                "level": 2,
                "sub_category": "资金付款",
                "keywords": ["付款", "payment", "结算", "settlement", "资金", "余额", "balance", "disbursement"]
            },
            {
                "name": "货件到仓",
                "level": 2,
                "sub_category": "货件到仓",
                "keywords": ["货件到仓", "shipment received", "入库", "checked in", "warehouse", "到仓"]
            },
            {
                "name": "链接合规Case",
                "level": 2,
                "sub_category": "链接合规",
                "keywords": ["链接合规", "listing compliance", "ASIN合规", "商品合规", "listing warning"]
            },
            {
                "name": "系统提醒",
                "level": 2,
                "sub_category": "系统提醒",
                "keywords": ["系统提醒", "notice", "提醒", "notification", "请注意", "action needed"]
            },
            {
                "name": "退款退货",
                "level": 2,
                "sub_category": "退款退货",
                "keywords": ["退款", "退货", "refund", "return request", "A-to-Z", "索赔", "claim", "chargeback"]
            },
            {
                "name": "客服来信",
                "level": 3,
                "sub_category": "客服来信",
                "keywords": ["客服", "customer service", "buyer message", "咨询", "complaint", "投诉", "customer inquiry", "客户来信"]
            },
            {
                "name": "日常管理",
                "level": 4,
                "sub_category": "日常管理",
                "keywords": ["管理", "management", "日常", "运营报告", "performance", "业务报告"]
            },
            {
                "name": "出单通知",
                "level": 5,
                "sub_category": "出单通知",
                "keywords": ["新订单", "new order", "订单确认", "order confirmed", "出单"]
            },
            {
                "name": "发货通知",
                "level": 5,
                "sub_category": "发货通知",
                "keywords": ["已发货", "shipped", "物流", "tracking", "运单", "dispatched", "发货"]
            },
            {
                "name": "活动推荐/广告推销",
                "level": 6,
                "sub_category": "活动推荐",
                "keywords": ["活动", "promotion", "推荐", "recommend", "学习", "learning", "培训", "training", "webinar", "广告", "advertisement", "推销", "marketing email", "服务商", "service provider", "推广", "subscribe"]
            }
        ],
        'daemon': {
            'enabled': True,
            'check_interval_minutes': 5,
        },
        'schedule': {
            'summaries': ['07:30', '11:30', '17:00', '21:00'],
        },
        'reports': {
            'cycle_dir': 'reports/cycles',
            'summary_dir': 'reports/summaries',
        },
        'excel': {
            'filename': 'emails.xlsx',
            'sheet_name': '邮件记录',
        },
        'settings': {
            'only_unread': True,
            'mark_as_read': True,
            'days_back': 1,
            'max_emails': 200,
            'default_owner': '未分配',
        }
    }


def main():
    # 检查是否已有配置
    if Path(CONFIG_FILE).exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # 检查是否为模板配置（包含占位符）
            imap_email = config.get('imap', {}).get('email', '')
            if 'your_email' not in imap_email and '你的' not in imap_email:
                print(f"\n  {GREEN}✅ 检测到已有配置文件: {CONFIG_FILE}{RESET}")
                if not confirm("  是否重新配置？（当前配置将被覆盖）", default=False):
                    print(f"\n  保留当前配置，退出向导。")
                    return
        except Exception:
            pass

    print_banner()

    # 步骤1：IMAP
    imap_cfg = step_imap()

    # 步骤2：邮箱映射
    mailboxes = step_mailboxes()

    # 步骤3：AI
    ai_cfg = step_ai()

    # 步骤4：飞书群机器人
    feishu_cfg = step_feishu()

    # 步骤5：飞书自建应用
    feishu_app_cfg = step_feishu_app(mailboxes)

    # 构建配置
    config = build_config(imap_cfg, mailboxes, ai_cfg, feishu_cfg, feishu_app_cfg)

    # 显示摘要
    show_summary(config)

    if not confirm("  确认保存配置？", default=True):
        print(f"\n  {YELLOW}已取消，配置未保存。{RESET}")
        return

    # 保存配置
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n  {GREEN}✅ 配置已保存到: {CONFIG_FILE}{RESET}")
    print()
    print(f"  {CYAN}{'─' * 60}{RESET}")
    print(f"  {BOLD}🚀 接下来：{RESET}")
    print()
    print(f"    1. 在每个 Outlook 邮箱中设置转发规则（只需转发，无需改主题）")
    print(f"    2. 双击 {YELLOW}run_daemon.bat{RESET} 启动实时监控（每 5 分钟检查）")
    print(f"    3. 双击 {YELLOW}run_summary.bat{RESET} 手动触发总结")
    if feishu_app_cfg.get('enabled'):
        print(f"    4. 运行 {YELLOW}python mail_lobster_imap.py --test-feishu-app{RESET} 测试私聊推送")
    print()
    print(f"  {CYAN}{'─' * 60}{RESET}")
    print()
    print(f"  {YELLOW}总结时间：{RESET}07:30 / 11:30 / 17:00 / 21:00")
    print(f"  {YELLOW}即时通知：{RESET}0级 + 1级 + 2级 邮件自动推送")
    print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {YELLOW}⚠️ 设置已取消{RESET}\n")
    except Exception as e:
        print(f"\n  {RED}❌ 设置出错: {e}{RESET}\n")

    input("\n  按回车键退出...")
