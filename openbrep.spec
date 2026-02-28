# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
from PyInstaller.utils.hooks import copy_metadata

block_cipher = None
root = Path.cwd()
edition = os.environ.get("OBR_EDITION", "free").strip().lower() or "free"


def add_path(src: Path, target: str, out: list[tuple[str, str]]):
    if src.exists():
        out.append((str(src), target))


def add_knowledge_free(src_dir: Path, out: list[tuple[str, str]]):
    """Include knowledge but exclude paid/private tiers (e.g. ccgdl_dev_doc)."""
    if not src_dir.exists():
        return
    for p in src_dir.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        rel_parts = rel.parts
        if "ccgdl_dev_doc" in rel_parts:
            continue
        # keep only regular docs for free tier
        out.append((str(p), str(rel.parent)))


added = []
for item in ["ui", "openbrep", "skills", "config.example.toml", "README.md", "README.zh-CN.md"]:
    p = root / item
    if p.exists():
        target = item if p.is_dir() else "."
        added.append((str(p), target))

knowledge_dir = root / "knowledge"

# Include package metadata required by importlib.metadata at runtime
added.extend(copy_metadata("streamlit"))
added.extend(copy_metadata("streamlit_ace"))
if edition == "pro":
    add_path(knowledge_dir, "knowledge", added)
else:
    add_knowledge_free(knowledge_dir, added)


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
