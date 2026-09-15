# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：把 OpenBrep 后端冻结成单文件 sidecar（obr7-backend）。

产物由 Tauri 作为 externalBin 打进桌面安装包，运行时不再需要系统 Python。
入口是 scripts/obr7.py（FROZEN 分支：进程内 API + 服务打包的 frontend/dist）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

block_cipher = None
root = Path.cwd()


def add_knowledge_free(src_dir: Path, out: list[tuple[str, str]]):
    """Include knowledge but exclude paid/private tiers (e.g. ccgdl_dev_doc)."""
    if not src_dir.exists():
        return
    for p in src_dir.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        if "ccgdl_dev_doc" in rel.parts:
            continue
        out.append((str(p), str(rel.parent)))


datas: list[tuple[str, str]] = []
add_knowledge_free(root / "knowledge", datas)
for item, target in [
    ("skills", "skills"),
    ("frontend/dist", "frontend/dist"),
    ("config.example.toml", "."),
]:
    p = root / item
    if p.exists():
        datas.append((str(p), target))

# openbrep 包内非 .py 数据（data/ prompts/ vision/schemas/ public_keys/ 等）
datas += collect_data_files("openbrep")
# litellm 运行时按名称读取模型价格 JSON 等数据文件
datas += collect_data_files("litellm")
datas += copy_metadata("litellm")
datas += copy_metadata("mcp")

hiddenimports = [
    *collect_submodules("openbrep"),
    *collect_submodules("litellm"),
    *collect_submodules("mcp"),
    # tiktoken discovers encodings through the separately packaged
    # ``tiktoken_ext`` plugin namespace.  PyInstaller cannot reliably infer
    # this dynamic import, so keep the registry and OpenAI encodings in the
    # frozen sidecar explicitly.
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "click",
    "rich",
    "typer",
    "toml",
    "tomllib",
    "yaml",
    "PIL",
]

a = Analysis(
    ["scripts/obr7.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Streamlit 时代遗留（已退役，双保险）
        "streamlit", "streamlit_ace", "plotly",
        # 开发工具
        "pytest", "ruff",
        # litellm 可选供应商链上的重型二进制依赖（本机 site-packages 有就会被
        # collect_submodules 卷进来；openbrep 核心链路均不需要它们）
        "torch", "bitsandbytes", "transformers", "accelerate", "sentencepiece",
        "cv2", "onnxruntime", "pyarrow", "llvmlite", "numba", "av",
        "scipy", "pandas", "matplotlib", "sklearn", "hf_xet", "grpc",
        "numpy", "sympy", "triton",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="obr7-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # sidecar 需要可用的 stdout/stderr（OBR7_READY_URL 握手 + 日志）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
