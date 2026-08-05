# -*- coding: utf-8 -*-
"""17:00 全平台简报群聊推送测试（使用模拟店铺名，不含真实店铺信息）

用法：
    python _test_brief_send.py          # 实际推送到飞书群聊（读取 config_imap_forward.json）
    python _test_brief_send.py --dry    # 只打印内容，不发送
    设置环境变量 FEISHU_WEBHOOK 可覆盖配置中的 webhook（避免把密钥写入文件）
"""
import os
import sys
from mail_lobster_imap import MailLobsterIMAP

# 模拟数据：占位店铺名，不含任何真实店铺信息
MOCK_EMAILS = [
    {"owner": "测试店铺A_AMZ", "level": 0, "platform": "AMZ"},
    {"owner": "测试店铺A_AMZ", "level": 1, "platform": "AMZ"},
    {"owner": "测试店铺A_AMZ", "level": 1, "platform": "AMZ"},
    {"owner": "测试店铺A_AMZ", "level": 2, "platform": "AMZ"},
    {"owner": "测试店铺B_WMT", "level": 0, "platform": "WMS"},
    {"owner": "测试店铺B_WMT", "level": 2, "platform": "WMS"},
    {"owner": "测试店铺B_WMT", "level": 2, "platform": "WMS"},
    {"owner": "测试店铺C_AMZ", "level": 1, "platform": "AMZ"},
    {"owner": "测试店铺D_AMZ", "level": 2, "platform": "AMZ"},
]
MOCK_TIME_RANGE = "2026-08-05 07:30 ~ 2026-08-05 17:00"


def main():
    dry_run = '--dry' in sys.argv
    webhook_override = os.environ.get('FEISHU_WEBHOOK', '').strip()
    try:
        lobster = MailLobsterIMAP()
    except FileNotFoundError:
        if not dry_run and not webhook_override:
            print("❌ 缺少 config_imap_forward.json，无法实际推送（dry 模式可离线预览）")
            sys.exit(1)
        # dry 模式 / webhook 覆盖：简报构建不依赖配置文件
        lobster = object.__new__(MailLobsterIMAP)
        lobster.config = {}

    if webhook_override:
        lobster.config['feishu_bot'] = {'enabled': True, 'webhook_url': webhook_override}

    content = lobster._build_daily_brief('17:00', MOCK_EMAILS, MOCK_TIME_RANGE)
    print("——— 简报内容 ———")
    print(content)
    print("—————————————————")

    if dry_run:
        print("（dry 模式，未发送）")
        return

    if lobster.send_feishu_bot(content):
        print("✅ 已推送到飞书群聊")
    else:
        print("❌ 推送失败，请检查 config_imap_forward.json 中 feishu_bot 配置")
        sys.exit(1)


if __name__ == '__main__':
    main()
