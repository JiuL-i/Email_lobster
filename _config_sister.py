# -*- coding: utf-8 -*-
"""双平台映射关系配置 — 由 first_run.bat 调用
支持查看、添加、删除 sister_owner_mapping 配置
"""
import json
import os
import sys

CONFIG_FILE = 'config_imap_forward.json'


def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_all_owners(cfg):
    """列出所有 owner 及其平台"""
    mailboxes = cfg.get('mailbox_mapping', {}).get('mailboxes', [])
    owners = {}
    for mb in mailboxes:
        owner = mb['owner']
        if owner not in owners:
            owners[owner] = set()
        # 从 owner 名称中提取平台
        import re
        for m in re.finditer(r'[A-Z]{2,8}', owner):
            code = m.group()
            plat_map = {'WMT': 'WMS', 'WALMART': 'WMS', 'AMAZON': 'AMZ'}
            owners[owner].add(plat_map.get(code, code))
    return owners


def show_current(cfg):
    """显示当前 sister_owner_mapping"""
    mapping = cfg.get('sister_owner_mapping', [])
    if not mapping:
        print('\n  当前没有双平台映射配置。\n')
        return

    print(f'\n  当前双平台映射 ({len(mapping)} 组):')
    print('  ' + '-' * 50)
    for i, group in enumerate(mapping, 1):
        biz = group.get('business', '?')
        owners = group.get('owners', {})
        parts = [f'{plat}: {name}' for plat, name in owners.items()]
        print(f'  {i}. {biz}: {", ".join(parts)}')
    print('  ' + '-' * 50)
    print()


def add_mapping(cfg):
    """添加新的双平台映射"""
    owners = list_all_owners(cfg)
    print('\n  所有负责人:')
    owner_list = sorted(owners.keys())
    for i, owner in enumerate(owner_list, 1):
        plats = ', '.join(sorted(owners[owner])) if owners[owner] else '?'
        print(f'    {i:2d}. {owner}  [{plats}]')

    print('\n  请输入要配对的第一个负责人编号 (或输入 0 取消):')
    try:
        idx1 = int(input('  > ')) - 1
        if idx1 < 0:
            return False
        owner1 = owner_list[idx1]
    except (ValueError, IndexError):
        print('  [!] 无效输入')
        return False

    print(f'\n  已选择: {owner1}')
    print(f'  请输入要配对的第二个负责人编号:')
    try:
        idx2 = int(input('  > ')) - 1
        if idx2 < 0 or idx2 == idx1:
            print('  [!] 无效输入或不能选择自己')
            return False
        owner2 = owner_list[idx2]
    except (ValueError, IndexError):
        print('  [!] 无效输入')
        return False

    # 确定各自的平台
    plat1 = list(owners[owner1])[0] if owners[owner1] else ''
    plat2 = list(owners[owner2])[0] if owners[owner2] else ''

    print(f'\n  配对: {owner1} [{plat1}] + {owner2} [{plat2}]')

    # 输入业务名称
    biz_name = input('  请输入业务/企业名称 (如: 睿宝): ').strip()
    if not biz_name:
        print('  [!] 名称不能为空')
        return False

    # 构建映射
    mapping = cfg.get('sister_owner_mapping', [])
    new_group = {
        'business': biz_name,
        'owners': {
            plat1: owner1,
            plat2: owner2,
        }
    }
    mapping.append(new_group)
    cfg['sister_owner_mapping'] = mapping
    save_config(cfg)

    print(f'\n  ✅ 已添加: {biz_name} → {plat1}:{owner1}, {plat2}:{owner2}')
    return True


def remove_mapping(cfg):
    """删除双平台映射"""
    mapping = cfg.get('sister_owner_mapping', [])
    if not mapping:
        print('\n  当前没有可删除的映射。\n')
        return False

    show_current(cfg)
    print('  请输入要删除的编号 (或输入 0 取消):')
    try:
        idx = int(input('  > ')) - 1
        if idx < 0:
            return False
        if idx >= len(mapping):
            print('  [!] 编号超出范围')
            return False
    except (ValueError, IndexError):
        print('  [!] 无效输入')
        return False

    removed = mapping.pop(idx)
    cfg['sister_owner_mapping'] = mapping
    save_config(cfg)
    print(f'\n  ✅ 已删除: {removed.get("business", "?")}')
    return True


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.exists(CONFIG_FILE):
        print('  [!] 配置文件不存在')
        return

    cfg = load_config()

    while True:
        show_current(cfg)
        print('  操作:')
        print('    1. 添加双平台映射')
        print('    2. 删除双平台映射')
        print('    3. 返回主菜单')
        print()
        choice = input('  请选择 (1-3): ').strip()

        if choice == '1':
            add_mapping(cfg)
            cfg = load_config()  # 重新加载
        elif choice == '2':
            remove_mapping(cfg)
            cfg = load_config()
        elif choice == '3':
            break
        else:
            print('  [!] 无效选择')

        print()


if __name__ == '__main__':
    main()
