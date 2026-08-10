# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Mail Lobster launcher.

Build with:
    pyinstaller launcher.spec --noconfirm --clean
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT_ROOT = Path(os.path.abspath(SPECPATH))
ICON = str(PROJECT_ROOT / "assets" / "lobster.ico")
ICON_ARG = ["--icon", ICON] if Path(ICON).exists() else []

datas = []
prompts_dir = PROJECT_ROOT / "prompts"
if prompts_dir.exists():
    datas.append((str(prompts_dir), "prompts"))
assets_dir = PROJECT_ROOT / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "ai_analyzer",
        "mailbox_admin",
        "single_instance",
        "mail_lobster_imap",
        "setup_wizard",
        "_report_error",
        "_prompt_optimizer",
        "_backup_config",
        "_config_sister",
        "_display_config",
        "gui.app",
        "gui.dialogs",
        "gui.icon",
        "gui.widgets",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mail_lobster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if Path(ICON).exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="mail_lobster",
)