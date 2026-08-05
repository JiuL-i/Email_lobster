# -*- coding: utf-8 -*-
"""17:00 全平台简报群聊推送测试（使用模拟店铺名，不含真实店铺信息）

用法：
    python _test_brief_send.py          # 实际推送到飞书群聊
    python _test_brief_send.py --dry    # 只打印内容，不发送
"""
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
    try:
        lobster = MailLobsterIMAP()
    except FileNotFoundError:
        if not dry_run:
            print("❌ 缺少 config_imap_forward.json，无法实际推送（dry 模式可离线预览）")
            sys.exit(1)
        # dry 模式：简报构建不依赖配置，跳过 __init__ 即可
        lobster = object.__new__(MailLobsterIMAP)

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
