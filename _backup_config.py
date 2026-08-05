# -*- coding: utf-8 -*-
"""配置备份与恢复 — 由 first_run.bat 调用
用法:
  python _backup_config.py backup    → 备份当前配置
  python _backup_config.py list      → 列出所有备份
  python _backup_config.py restore   → 恢复到最近一次备份
  python _backup_config.py restore <filename> → 恢复到指定备份
"""
import os
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path('backup')
CONFIG_FILE = 'config_imap_forward.json'


def backup():
    """备份当前配置"""
    if not os.path.exists(CONFIG_FILE):
        print(f'  [!] 配置文件 {CONFIG_FILE} 不存在')
        return False

    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'config_{timestamp}.json'
    backup_path = BACKUP_DIR / backup_name

    shutil.copy2(CONFIG_FILE, backup_path)

    # 读取配置摘要
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    email = cfg.get('imap', {}).get('email', '')
    owners = len(cfg.get('mailbox_mapping', {}).get('mailboxes', []))
    sister = len(cfg.get('sister_owner_mapping', []))

    print(f'  ✅ 备份成功: {backup_name}')
    print(f'     邮箱: {email}  负责人: {owners}  双平台组: {sister}')

    # 保留最近 20 个备份
    backups = sorted(BACKUP_DIR.glob('config_*.json'))
    while len(backups) > 20:
        oldest = backups.pop(0)
        oldest.unlink()
        print(f'  [清理] 删除旧备份: {oldest.name}')

    return True


def list_backups():
    """列出所有备份"""
    if not BACKUP_DIR.exists():
        print('  (暂无备份)')
        return

    backups = sorted(BACKUP_DIR.glob('config_*.json'), reverse=True)
    if not backups:
        print('  (暂无备份)')
        return

    print(f'  共有 {len(backups)} 个备份:\n')
    for i, bp in enumerate(backups, 1):
        size = bp.stat().st_size
        mtime = datetime.fromtimestamp(bp.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        marker = ' ← 最近' if i == 1 else ''
        print(f'    {i:2d}. {bp.name}  ({size} bytes, {mtime}){marker}')


def restore(target=None):
    """恢复到指定备份或最近一次备份"""
    if not BACKUP_DIR.exists():
        print('  [!] 没有可用的备份')
        return False

    backups = sorted(BACKUP_DIR.glob('config_*.json'), reverse=True)
    if not backups:
        print('  [!] 没有可用的备份')
        return False

    if target:
        # 恢复指定备份
        target_path = BACKUP_DIR / target
        if not target_path.exists():
            # 尝试按编号
            try:
                idx = int(target) - 1
                if 0 <= idx < len(backups):
                    target_path = backups[idx]
                else:
                    print(f'  [!] 编号 {target} 无效 (共 {len(backups)} 个备份)')
                    return False
            except ValueError:
                print(f'  [!] 备份文件不存在: {target}')
                return False
    else:
        target_path = backups[0]

    # 先备份当前配置（防止恢复后想反悔）
    if os.path.exists(CONFIG_FILE):
        pre_restore = BACKUP_DIR / f'pre_restore_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        shutil.copy2(CONFIG_FILE, pre_restore)
        print(f'  [安全] 恢复前自动备份当前配置: {pre_restore.name}')

    shutil.copy2(target_path, CONFIG_FILE)
    print(f'  ✅ 已恢复到: {target_path.name}')

    # 显示恢复后的摘要
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    email = cfg.get('imap', {}).get('email', '')
    owners = len(cfg.get('mailbox_mapping', {}).get('mailboxes', []))
    sister = len(cfg.get('sister_owner_mapping', []))
    summaries = cfg.get('schedule', {}).get('summaries', [])
    print(f'     邮箱: {email}')
    print(f'     负责人: {owners}  双平台组: {sister}')
    print(f'     总结时段: {", ".join(summaries)}')

    return True


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if len(sys.argv) < 2:
        print('用法:')
        print('  python _backup_config.py backup     备份当前配置')
        print('  python _backup_config.py list       列出所有备份')
        print('  python _backup_config.py restore    恢复到最近备份')
        print('  python _backup_config.py restore N  恢复到第 N 个备份')
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == 'backup':
        backup()
    elif cmd == 'list':
        list_backups()
    elif cmd == 'restore':
        target = sys.argv[2] if len(sys.argv) > 2 else None
        restore(target)
    else:
        print(f'  [!] 未知命令: {cmd}')
        sys.exit(1)
