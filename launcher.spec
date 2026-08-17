# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Mail Lobster launcher.

Build with:
    pyinstaller launcher.spec --noconfirm --clean

The `excludes` list below is critical: openai 库会传递依赖 huggingface_hub,
而 huggingface_hub 在 PyInstaller 6.x 的扫描下会拖入 torch/cv2/datasets/
transformers 等大库（这些库我们项目完全不会用）。显式 exclude 后打包时间
通常从 ~10 分钟降到 ~3 分钟，EXE 体积从 ~1.4GB 降到 ~80MB。
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

# Modules that PyInstaller scans transitively but our project never uses.
# Keep this list minimal — over-excluding can break real imports.
EXCLUDES = [
    # Heavy ML/data science stacks pulled in by openai → huggingface_hub
    "torch",
    "torchvision",
    "torchaudio",
    "transformers",
    "datasets",
    "huggingface_hub",
    "tokenizers",
    "safetensors",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    # CV / image processing (pulled by some openai transitive deps)
    "cv2",
    "PIL",
    "Pillow",
    "skimage",
    # Async web servers (we only use requests)
    "fastapi",
    "uvicorn",
    "starlette",
    # Apache Arrow (pulled by various ML libs)
    "pyarrow",
    # Test frameworks
    "pytest",
    "unittest",
    "test",
    # Misc unused stdlib wrappers
    "tkinter",
    "pydoc",
    "doctest",
]

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
        "seen_emails_store",
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
    excludes=EXCLUDES,
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