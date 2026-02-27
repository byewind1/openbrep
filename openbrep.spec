# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None
root = Path.cwd()

added = []
for item in ["ui", "openbrep", "knowledge", "skills", "config.example.toml", "README.md", "README.zh-CN.md"]:
    p = root / item
    if p.exists():
        target = item if p.is_dir() else "."
        added.append((str(p), target))


a = Analysis(
    ['packaging/openbrep_launcher.py'],
    pathex=[str(root)],
    binaries=[],
    datas=added,
    hiddenimports=[
        'streamlit',
        'streamlit.web.cli',
        'click',
        'rich',
        'tomli',
        'tomllib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenBrep',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OpenBrep',
)
