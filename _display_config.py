# -*- coding: utf-8 -*-
"""显示配置摘要 - 由 first_run.bat 调用"""
import json
import sys
import os

try:
    with open('config_imap_forward.json', 'r', encoding='utf-8') as f:
        c = json.load(f)
    email = c.get('imap', {}).get('email', '')
    owner_count = len(c.get('mailbox_mapping', {}).get('mailboxes', []))
    ai_on = c.get('ai', {}).get('enabled', False)
    feishu_bot = c.get('feishu_bot', {}).get('enabled', False)
    feishu_app = c.get('feishu_app', {}).get('enabled', False)
    summaries = c.get('schedule', {}).get('summaries', ['07:30', '11:30', '17:00', '21:00'])
    heartbeat = c.get('heartbeat', {}).get('enabled', False)
    other = c.get('other_platform', {}).get('enabled', False)
    other_owner = c.get('other_platform', {}).get('owner', '')
    sister_groups = c.get('sister_owner_mapping', [])

    # 统计渠道分布
    channels = {}
    for mb in c.get('mailbox_mapping', {}).get('mailboxes', []):
        ch = mb.get('notify_channel', 'private')
        channels[ch] = channels.get(ch, 0) + 1

    print(f'  汇总邮箱: {email}')
    print(f'  邮箱映射: {owner_count} 个业务邮箱')
    ch_str = ', '.join(f'{k}: {v}' for k, v in channels.items())
    print(f'  渠道分布: {ch_str}')
    print(f'  AI 分析:  {"已启用" if ai_on else "未启用"}')
    print(f'  飞书群聊: {"已启用" if feishu_bot else "未启用"}')
    print(f'  飞书私聊: {"已启用" if feishu_app else "未启用"}')
    if other:
        print(f'  其他平台路由: {other_owner} (仅电商竞品，物流/服务商保留原owner)')
    else:
        print(f'  其他平台路由: 未启用')
    if sister_groups:
        sister_names = [g.get('business', '?') for g in sister_groups]
        print(f'  双平台分流: {len(sister_groups)} 组 ({", ".join(sister_names)})')
    else:
        print(f'  双平台分流: 未配置')
    print(f'  同轮去重: 已启用 (相同内容只推送一次)')
    print(f'  心跳检测: {"已启用" if heartbeat else "未启用"}')
    print(f'  总结时段: {" / ".join(summaries)}')
    print(f'  21:00 日总结: 全平台全owner汇总发群聊')
    print(f'  周总结:   周六 17:00 (与当日傍晚总结同时触发)')

    # 检查备份状态
    backup_dir = 'backup'
    if os.path.isdir(backup_dir):
        backups = [f for f in os.listdir(backup_dir) if f.startswith('config_') and f.endswith('.json')]
        if backups:
            latest = sorted(backups)[-1]
            print(f'  配置备份: {len(backups)} 个 (最近: {latest})')
        else:
            print(f'  配置备份: 暂无')
    else:
        print(f'  配置备份: 暂无')

    # 提示词自进化状态
    rules_file = 'prompts/learned_rules.txt'
    if os.path.isfile(rules_file):
        try:
            with open(rules_file, 'r', encoding='utf-8') as rf:
                rules_text = rf.read()
            version = 'v0'
            rule_count = 0
            for line in rules_text.splitlines():
                if line.startswith('# 版本: v'):
                    version = line.split('v')[-1].strip()
                if line.strip() and not line.startswith('#') and line.strip()[0].isdigit():
                    rule_count += 1
            print(f'  提示词规则: v{version} ({rule_count} 条学习规则)')
        except Exception:
            print(f'  提示词规则: 未初始化')
    else:
        print(f'  提示词规则: 未初始化')

    # 误分类报告
    error_file = 'state/misclassifications.jsonl'
    if os.path.isfile(error_file):
        try:
            unprocessed = 0
            total = 0
            with open(error_file, 'r', encoding='utf-8') as ef:
                for line in ef:
                    line = line.strip()
                    if line:
                        e = json.loads(line)
                        total += 1
                        if not e.get('processed', False):
                            unprocessed += 1
            print(f'  误分类报错: {unprocessed} 条待处理 / {total} 条总计')
        except Exception:
            print(f'  误分类报错: 无法读取')
    else:
        print(f'  误分类报错: 暂无')

except Exception as e:
    print(f'  [警告] 无法读取配置: {e}')
