"""
openbrep Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import re
import os
import time
import math
import base64
import asyncio
import json
import csv
import hashlib
import hmac
import string
import zipfile
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
try:
    from streamlit_ace import st_ace
    _ACE_AVAILABLE = True
except ImportError:
    _ACE_AVAILABLE = False

try:
    import plotly.graph_objects as go
    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
from openbrep.paramlist_builder import build_paramlist_xml, validate_paramlist
from openbrep.compiler import MockHSFCompiler, HSFCompiler, CompileResult
from openbrep.core import GDLAgent, Status
from openbrep.gdl_previewer import Preview2DResult, Preview3DResult, preview_2d_script, preview_3d_script
from openbrep.validator import GDLValidator
from openbrep.knowledge import KnowledgeBase
try:
    from openbrep.config import ALL_MODELS, VISION_MODELS, REASONING_MODELS, model_to_provider
    _MODEL_CONSTANTS_OK = True
except ImportError:
    ALL_MODELS = []
    VISION_MODELS = set()
    REASONING_MODELS = set()
    _MODEL_CONSTANTS_OK = False
from openbrep.skills_loader import SkillsLoader
from openbrep import __version__ as OPENBREP_VERSION
try:
    from openbrep.tapir_bridge import get_bridge, errors_to_chat_message
    _TAPIR_IMPORT_OK = True
except ImportError:
    _TAPIR_IMPORT_OK = False


# ── Page Config ───────────────────────────────────────────

st.set_page_config(
    page_title="openbrep",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────

st.markdown("""
<style>
.stApp { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }
code, .stCodeBlock { font-family: 'SF Mono', 'Menlo', 'Monaco', monospace !important; }

section[data-testid="stSidebar"] .stMarkdown p.main-header {
    font-family: 'SF Mono', 'Menlo', 'Courier New', monospace !important;
    font-size: 2.8rem !important;
    font-weight: 900 !important;
    text-align: center !important;
    display: block !important;
    width: 100% !important;
    white-space: nowrap;
    background: linear-gradient(135deg, #22d3ee, #34d399);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0 0 0.12rem 0 !important;
    line-height: 0.95 !important;
}
.intro-header {
    color: #cbd5e1;
    font-size: 0.92rem;
    margin-top: 0.15rem;
    margin-bottom: 0.25rem;
    line-height: 1.45;
}
.sub-header {
    color: #94a3b8;
    font-size: 0.86rem;
    margin-top: 0;
    margin-bottom: 1.2rem;
}

.welcome-card {
    background: linear-gradient(135deg, #0f172a, #1e293b);
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 2rem;
    margin: 1rem 0;
}
.step-item {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 1rem;
    padding: 0.75rem;
    background: #1e293b;
    border-radius: 8px;
    border-left: 3px solid #22d3ee;
}
.diff-current { border-left: 3px solid #475569; padding-left: 0.5rem; }
.diff-ai      { border-left: 3px solid #f59e0b; padding-left: 0.5rem; }
.diff-badge {
    display: inline-block;
    background: #f59e0b22;
    color: #f59e0b;
    border: 1px solid #f59e0b55;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.78rem;
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 4px;
}

/* ── Column gap tighten ─────────────────────────────────── */
/* Streamlit "small" gap still has padding; pull columns closer */
div[data-testid="stHorizontalBlock"] {
    gap: 1rem !important;
}
/* Subtle divider between editor and chat */
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:last-child {
    border-left: 1px solid #1e293b;
    padding-left: 0.75rem;
}
</style>
""", unsafe_allow_html=True)


# ── Session State ─────────────────────────────────────────

if "project" not in st.session_state:
    st.session_state.project = None
if "_import_key_done" not in st.session_state:
    st.session_state._import_key_done = ""   # dedup: skip re-processing same file
if "compile_log" not in st.session_state:
    st.session_state.compile_log = []
if "compile_result" not in st.session_state:
    st.session_state.compile_result = None
if "tapir_status" not in st.session_state:
    st.session_state.tapir_status = None  # None | "checking" | "ok" | "no_tapir" | "no_ac"
if "tapir_test_trigger" not in st.session_state:
    st.session_state.tapir_test_trigger = False
if "tapir_selection_trigger" not in st.session_state:
    st.session_state.tapir_selection_trigger = False
if "tapir_highlight_trigger" not in st.session_state:
    st.session_state.tapir_highlight_trigger = False
if "tapir_load_params_trigger" not in st.session_state:
    st.session_state.tapir_load_params_trigger = False
if "tapir_apply_params_trigger" not in st.session_state:
    st.session_state.tapir_apply_params_trigger = False
if "tapir_selected_guids" not in st.session_state:
    st.session_state.tapir_selected_guids = []
if "tapir_selected_details" not in st.session_state:
    st.session_state.tapir_selected_details = []
if "tapir_selected_params" not in st.session_state:
    st.session_state.tapir_selected_params = []
if "tapir_param_edits" not in st.session_state:
    st.session_state.tapir_param_edits = {}
if "tapir_last_error" not in st.session_state:
    st.session_state.tapir_last_error = ""
if "tapir_last_sync_at" not in st.session_state:
    st.session_state.tapir_last_sync_at = ""
if "adopted_msg_index" not in st.session_state:
    st.session_state.adopted_msg_index = None
if "_debug_mode_active" not in st.session_state:
    st.session_state["_debug_mode_active"] = None  # None | "editor"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "work_dir" not in st.session_state:
    st.session_state.work_dir = str(Path.home() / "openbrep-workspace")
if "agent_running" not in st.session_state:
    st.session_state.agent_running = False
if "pending_diffs" not in st.session_state:
    # AI-proposed changes awaiting user review.
    # Keys: "scripts/3d.gdl" etc. + "paramlist.xml" for parameters
    st.session_state.pending_diffs = {}
if "pending_ai_label" not in st.session_state:
    # Human-readable label shown in the confirmation banner
    st.session_state.pending_ai_label = ""
if "pending_gsm_name" not in st.session_state:
    st.session_state.pending_gsm_name = ""
if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False
if "editor_version" not in st.session_state:
    # Increment on import/clear to force text_area widget recreation (avoids stale Streamlit cache)
    st.session_state.editor_version = 0
if "_ace_pending_main_editor_keys" not in st.session_state:
    st.session_state._ace_pending_main_editor_keys = set()
if "script_revision" not in st.session_state:
    # Script revision for header/file naming; starts from v1 on first write
    st.session_state.script_revision = 0
if "model_api_keys" not in st.session_state:
    # Per-model API Key storage — pre-fill from config.toml provider_keys
    st.session_state.model_api_keys = {}
if "chat_image_route_mode" not in st.session_state:
    # 图片模式：自动 / 强制生成 / 强制调试
    st.session_state.chat_image_route_mode = "自动"
if "chat_anchor_focus" not in st.session_state:
    st.session_state.chat_anchor_focus = None
if "chat_anchor_pending" not in st.session_state:
    st.session_state.chat_anchor_pending = None
if "pro_unlocked" not in st.session_state:
    st.session_state.pro_unlocked = False
if "pro_license_loaded" not in st.session_state:
    st.session_state.pro_license_loaded = False
if "preview_2d_data" not in st.session_state:
    st.session_state.preview_2d_data = None
if "preview_3d_data" not in st.session_state:
    st.session_state.preview_3d_data = None
if "preview_warnings" not in st.session_state:
    st.session_state.preview_warnings = []
if "preview_meta" not in st.session_state:
    st.session_state.preview_meta = {"kind": "", "timestamp": ""}


def _reset_tapir_p0_state() -> None:
    """清理 Tapir P0（Inspector + Workbench）缓存。"""
    st.session_state.tapir_selection_trigger = False
    st.session_state.tapir_highlight_trigger = False
    st.session_state.tapir_load_params_trigger = False
    st.session_state.tapir_apply_params_trigger = False
    st.session_state.tapir_selected_guids = []
    st.session_state.tapir_selected_details = []
    st.session_state.tapir_selected_params = []
    st.session_state.tapir_param_edits = {}
    st.session_state.tapir_last_error = ""
    st.session_state.tapir_last_sync_at = ""


def _license_file(work_dir: str) -> Path:
    return Path(work_dir) / ".openbrep" / "license_v1.json"


def _load_license(work_dir: str) -> dict:
    fp = _license_file(work_dir)
    if not fp.exists():
        return {"pro_unlocked": False}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"pro_unlocked": False}


def _save_license(work_dir: str, data: dict) -> None:
    fp = _license_file(work_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_base36(num: int) -> str:
    chars = string.digits + string.ascii_uppercase
    if num == 0:
        return "0"
    out: list[str] = []
    n = num
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def _get_license_secret(root: Path) -> bytes:
    env = os.environ.get("OPENBREP_LICENSE_SECRET", "")
    if env:
        return env.encode("utf-8")

    key_file = root / "keys" / "license_secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip().encode("utf-8")

    return b""


def _gen_code(secret: bytes, buyer_id: str, expire_date: str, salt: str) -> str:
    payload = f"{buyer_id}|{expire_date}|{salt}".encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()[:12].upper()
    token = _to_base36(int(digest, 16)).zfill(12)[:12]
    return f"OBR-{token[0:4]}-{token[4:8]}-{token[8:12]}"


def _verify_pro_code(code: str) -> tuple[bool, str]:
    c = (code or "").strip().upper()
    if not c:
        return False, "请输入授权码"

    root = Path(__file__).parent.parent
    csv_path = root / "licenses.csv"
    secret = _get_license_secret(root)

    # Backward-compatible fallback: allowlist in env/file
    if not csv_path.exists() or not secret:
        allowed = set()
        env_codes = os.environ.get("OPENBREP_PRO_CODES", "")
        for x in env_codes.split(","):
            x = x.strip().upper()
            if x:
                allowed.add(x)

        f = Path.home() / ".openbrep" / "pro_codes.txt"
        if f.exists():
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip().upper()
                    if line and not line.startswith("#"):
                        allowed.add(line)
            except Exception:
                pass

        return (c in allowed, "授权码有效" if c in allowed else "授权码无效")

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("license_code", "").strip().upper() != c):
                    continue

                if row.get("status", "active").strip().lower() != "active":
                    return False, "授权码已失效"

                buyer_id = (row.get("buyer_id") or "").strip()
                expire_date = (row.get("expire_date") or "").strip()
                salt = (row.get("salt") or "").strip()
                if not (buyer_id and expire_date and salt):
                    return False, "授权数据不完整"

                expected = _gen_code(secret, buyer_id, expire_date, salt)
                if expected != c:
                    return False, "授权码校验失败"

                # Expire date check (YYYY-MM-DD)
                if expire_date:
                    try:
                        if datetime.now().date() > datetime.strptime(expire_date, "%Y-%m-%d").date():
                            return False, "授权码已过期"
                    except Exception:
                        pass

                return True, "授权码有效"
    except Exception as e:
        return False, f"授权校验失败: {e}"

    return False, "授权码无效"


def _import_pro_knowledge_zip(file_bytes: bytes, filename: str, work_dir: str) -> tuple[bool, str]:
    if not filename.lower().endswith((".zip", ".obrk")):
        return False, "仅支持 .zip 或 .obrk 知识包"

    target = Path(work_dir) / "pro_knowledge"
    tmp = Path(work_dir) / ".openbrep" / "tmp_pro_knowledge"
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)

        zpath = tmp / "package.zip"
        zpath.write_bytes(file_bytes)

        with zipfile.ZipFile(zpath, "r") as zf:
            zf.extractall(tmp / "unpacked")

        unpacked = tmp / "unpacked"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        for item in unpacked.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        return True, f"✅ Pro 知识包导入完成：{target}"
    except Exception as e:
        return False, f"❌ 导入失败：{e}"
    finally:
        if tmp.exists():
            try:
                shutil.rmtree(tmp)
            except Exception:
                pass


# ── Load config.toml defaults ──────────────────────────

_config_defaults = {}
_provider_keys: dict = {}   # {provider: api_key}
_custom_providers: dict = {}  # {provider: {base_url, model, api_key}}

try:
    from openbrep.config import GDLAgentConfig
    import sys as _sys, os as _os
    # Load raw TOML to get provider_keys nested table
    if _sys.version_info >= (3, 11):
        import tomllib as _tomllib
    else:
        import tomli as _tomllib   # type: ignore

    _toml_path = _os.path.join(_os.path.dirname(__file__), "..", "config.toml")
    if _os.path.exists(_toml_path):
        with open(_toml_path, "rb") as _f:
            _raw = _tomllib.load(_f)
        _llm_raw = _raw.get("llm", {})
        _provider_keys = _llm_raw.get("provider_keys", {})

        # ── 自定义 Provider (config.toml) ──
        for _pname, _pcfg in _llm_raw.items():
            if _pname in {"provider_keys", "model", "api_key", "api_base", "temperature", "max_tokens"}:
                continue
            if isinstance(_pcfg, dict):
                _custom_providers[_pname] = {
                    "base_url": str(_pcfg.get("base_url", "") or ""),
                    "model": str(_pcfg.get("model", "") or ""),
                    "api_key": str(_provider_keys.get(_pname, "") or ""),
                }

    _config = GDLAgentConfig.load()
    _config_defaults = {
        "llm_model": _config.llm.model,
        "compiler_path": _config.compiler.path or "",
    }
except Exception:
    pass


def _key_for_model(model: str) -> str:
    """Pick the right API Key from provider_keys based on model name."""
    m = model.lower()

    # 自定义 provider 的模型精确匹配
    for _pcfg in _custom_providers.values():
        if m == str(_pcfg.get("model", "")).lower():
            return str(_pcfg.get("api_key", "") or "")

    if "glm" in m:
        return _provider_keys.get("zhipu", "")
    elif "deepseek" in m and "ollama" not in m:
        return _provider_keys.get("deepseek", "")
    elif "claude" in m:
        return _provider_keys.get("anthropic", "")
    elif "gpt" in m or "o3" in m or "o1" in m:
        return _provider_keys.get("openai", "")
    elif "gemini" in m:
        return _provider_keys.get("google", "")
    return ""


def _is_archicad_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Archicad"],
            capture_output=True, timeout=1
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Sidebar Config ────────────────────────────────────────

with st.sidebar:
    if _TAPIR_IMPORT_OK and not _is_archicad_running():
        st.sidebar.warning("⚠️ Archicad 未运行，编译和实时预览不可用")

    st.markdown('<p class="main-header">OpenBrep</p>', unsafe_allow_html=True)
    st.markdown('<p class="intro-header">用自然语言驱动 ArchiCAD GDL 库对象的创建、修改与编译。</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="sub-header">OpenBrep: Code Your Boundaries · v{OPENBREP_VERSION} · HSF-native</p>', unsafe_allow_html=True)
    st.divider()

    st.subheader("📁 工作目录")
    work_dir = st.text_input("Work Directory", value=st.session_state.work_dir, label_visibility="collapsed", disabled=st.session_state.agent_running)
    st.session_state.work_dir = work_dir

    # Load persisted license when work_dir is known
    if not st.session_state.pro_license_loaded:
        _lic = _load_license(work_dir)
        st.session_state.pro_unlocked = bool(_lic.get("pro_unlocked", False))
        st.session_state.pro_license_loaded = True

    st.subheader("🔐 Pro 授权（V1）")
    if st.session_state.pro_unlocked:
        st.success("Pro 已解锁")
    else:
        st.caption("当前：Free 模式（仅基础知识库）")

    pro_code_input = st.text_input("授权码", type="password", key="pro_code_input")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("解锁 Pro", width='stretch'):
            ok, msg = _verify_pro_code(pro_code_input)
            if ok:
                st.session_state.pro_unlocked = True
                _save_license(work_dir, {
                    "pro_unlocked": True,
                    "activated_at": datetime.now().isoformat(timespec="seconds"),
                    "license_code": (pro_code_input or "").strip().upper(),
                })
                st.success("✅ Pro 解锁成功")
                st.rerun()
            else:
                st.error(msg)
    with c2:
        if st.button("锁回 Free", width='stretch'):
            st.session_state.pro_unlocked = False
            _save_license(work_dir, {"pro_unlocked": False})
            st.info("已切回 Free 模式")
            st.rerun()

    if st.session_state.pro_unlocked:
        pro_pkg = st.file_uploader("导入 Pro 知识包（.zip/.obrk）", type=["zip", "obrk"], key="pro_pkg_uploader")
        if pro_pkg is not None:
            ok, msg = _import_pro_knowledge_zip(pro_pkg.read(), pro_pkg.name, work_dir)
            if ok:
                st.success(msg)
            else:
                st.error(msg)
    else:
        st.caption("请先输入有效授权码并解锁后再导入知识包。")

    st.divider()
    st.subheader("🔧 编译器 / Compiler")

    compiler_mode = st.radio(
        "编译模式",
        ["Mock (无需 ArchiCAD)", "LP_XMLConverter (真实编译)"],
        index=1 if _config_defaults.get("compiler_path") else 0,
    )

    converter_path = ""
    if compiler_mode.startswith("LP"):
        _raw_path = st.text_input(
            "LP_XMLConverter 路径",
            value=_config_defaults.get("compiler_path", ""),
            placeholder="/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter",
            help="macOS/Linux 用正斜杠 /，Windows 用反斜杠 粘贴后自动转换",
        )
        # 自动转换 Windows 反斜杠，去除首尾空格和引号
        converter_path = _raw_path.strip().strip('"').strip("'").replace("\\\\", "/").replace("\\", "/")

    st.divider()
    st.subheader("🧠 AI 模型 / LLM")

    # 使用 config.py 里的完整模型列表
    _mo = ALL_MODELS if ALL_MODELS else [
        "glm-5", "glm-4-flash", "glm-4-flash-x", "glm-4-air", "glm-4-plus",
        "deepseek-chat", "deepseek-reasoner",
        "gpt-4o", "gpt-4o-mini", "o3-mini",
        "claude-sonnet-4-6", "claude-opus-4-6",
        "gemini/gemini-2.5-flash",
    ]

    # ── 自定义 Provider (config.toml) ──
    _custom_models = [str(v.get("model", "") or "") for v in _custom_providers.values() if str(v.get("model", "") or "")]
    if _custom_models:
        _mo = list(dict.fromkeys(_custom_models + _mo))
    # 下拉显示时加视觉/推理标注
    def _model_label(m: str) -> str:
        tags = []
        if m in VISION_MODELS:   tags.append("👁")
        if m in REASONING_MODELS: tags.append("🧠")
        return f"{m}  {''.join(tags)}" if tags else m

    _mo_labels = [_model_label(m) for m in _mo]

    default_model = _config_defaults.get("llm_model", "glm-4-flash")
    default_index = _mo.index(default_model) if default_model in _mo else 0

    _selected_label = st.selectbox("模型 / Model", _mo_labels, index=default_index, disabled=st.session_state.agent_running)
    # 反查真实model string（去掉标注）
    model_name = _mo[_mo_labels.index(_selected_label)]
    st.session_state["current_model"] = model_name  # 供视觉检测使用

    # Load or initialize API Key for this specific model
    if model_name not in st.session_state.model_api_keys:
        # Auto-fill from config.toml provider_keys
        st.session_state.model_api_keys[model_name] = _key_for_model(model_name)

    api_key = st.text_input(
        "API Key",
        value=st.session_state.model_api_keys.get(model_name, ""),
        type="password",
        help="Ollama 本地模式不需要 Key",
        disabled=st.session_state.agent_running,
    )

    # Auto-save API Key + 持久化写回 config.toml
    if api_key != st.session_state.model_api_keys.get(model_name, ""):
        st.session_state.model_api_keys[model_name] = api_key
        # 写回 config.toml
        try:
            from openbrep.config import GDLAgentConfig, model_to_provider
            _save_cfg = GDLAgentConfig.load()
            _save_cfg.llm.model = model_name
            _provider = model_to_provider(model_name)
            if _provider and api_key:
                _save_cfg.llm.provider_keys[_provider] = api_key
            _save_cfg.save()
        except Exception as e:
            st.sidebar.warning(f"配置保存失败：{e}")

    # LP_XMLConverter 路径变更时持久化写回 config.toml
    if converter_path and converter_path != _config_defaults.get("compiler_path", ""):
        try:
            from openbrep.config import GDLAgentConfig
            _save_cfg2 = GDLAgentConfig.load()
            _save_cfg2.compiler.path = converter_path
            _save_cfg2.save()
            _config_defaults["compiler_path"] = converter_path
        except Exception as e:
            st.sidebar.warning(f"配置保存失败：{e}")

    if "claude" in model_name:
        st.caption("🔑 [获取 Claude API Key →](https://console.anthropic.com/settings/keys)")
        st.caption("⚠️ API Key 需单独充值，与 Claude Pro 订阅额度无关")
    elif "glm" in model_name:
        st.caption("🔑 [获取智谱 API Key →](https://bigmodel.cn/usercenter/apikeys)")
    elif "gpt" in model_name or "o3" in model_name:
        st.caption("🔑 [获取 OpenAI API Key →](https://platform.openai.com/api-keys)")
    elif "deepseek" in model_name and "ollama" not in model_name:
        st.caption("🔑 [获取 DeepSeek API Key →](https://platform.deepseek.com/api_keys)")
    elif "gemini" in model_name:
        st.caption("🔑 [获取 Gemini API Key →](https://aistudio.google.com/apikey)")
    elif "ollama" in model_name:
        st.caption("🖥️ 本地运行，无需 Key。确保 Ollama 已启动。")

    # API Base URL — only needed for OpenAI-compatible custom endpoints
    # zai/ (GLM), deepseek/, anthropic/ are native LiteLLM providers, no api_base needed
    def _get_default_api_base(model: str) -> str:
        m = model.lower()

        # 自定义 provider 的模型精确匹配
        for _pcfg in _custom_providers.values():
            if m == str(_pcfg.get("model", "")).lower():
                return str(_pcfg.get("base_url", "") or "")

        if "ollama" in m:
            return "http://localhost:11434"
        # GLM uses zai/ native provider — no api_base
        # DeepSeek uses deepseek/ native provider — no api_base
        return ""

    default_api_base = _get_default_api_base(model_name)
    api_base = ""
    if default_api_base:
        api_base = st.text_input("API Base URL", value=default_api_base)

    max_retries = st.slider("最大重试次数", 1, 10, 5)

    st.divider()

    # Project quick reset
    if st.session_state.project:
        if st.button("🗑️ 清除项目", width='stretch'):
            _keep_work_dir  = st.session_state.work_dir
            _keep_api_keys  = st.session_state.model_api_keys
            _keep_chat      = st.session_state.chat_history   # preserve chat
            st.session_state.project          = None
            st.session_state.compile_log      = []
            st.session_state.compile_result   = None
            st.session_state.adopted_msg_index = None
            st.session_state.pending_diffs    = {}
            st.session_state.pending_ai_label = ""
            st.session_state.pending_gsm_name = ""
            st.session_state.agent_running    = False
            st.session_state._import_key_done = ""
            st.session_state.preview_2d_data  = None
            st.session_state.preview_3d_data  = None
            st.session_state.preview_warnings = []
            st.session_state.preview_meta     = {"kind": "", "timestamp": ""}
            _reset_tapir_p0_state()
            _bump_main_editor_version()
            st.session_state.work_dir         = _keep_work_dir
            st.session_state.model_api_keys   = _keep_api_keys
            st.session_state.chat_history     = _keep_chat
            st.rerun()


# ── Helper Functions ──────────────────────────────────────

import json as _json, datetime as _datetime

def _save_feedback(msg_idx: int, rating: str, content: str, comment: str = "") -> None:
    """Save 👍/👎 feedback to work_dir/feedback.jsonl (local only, not sent anywhere)."""
    try:
        feedback_path = Path(st.session_state.work_dir) / "feedback.jsonl"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _datetime.datetime.now().isoformat(),
            "rating": rating,           # "positive" | "negative"
            "msg_idx": msg_idx,
            "preview": content[:300],
            "comment": comment.strip(),
        }
        with open(feedback_path, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass   # never let feedback save break the UI


def _tapir_sync_selection() -> tuple[bool, str]:
    """同步 Archicad 当前选中对象到本地缓存。"""
    if not _TAPIR_IMPORT_OK:
        return False, "Tapir bridge 未导入"

    bridge = get_bridge()
    if not bridge.is_available():
        st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
        return False, st.session_state.tapir_last_error

    guids = bridge.get_selected_elements()
    st.session_state.tapir_selected_guids = guids
    st.session_state.tapir_last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not guids:
        st.session_state.tapir_selected_details = []
        st.session_state.tapir_selected_params = []
        st.session_state.tapir_param_edits = {}
        st.session_state.tapir_last_error = ""
        return True, "未选中对象"

    details = bridge.get_details_of_elements(guids)
    st.session_state.tapir_selected_details = details
    st.session_state.tapir_last_error = ""
    return True, f"已同步 {len(guids)} 个对象"


def _tapir_highlight_selection() -> tuple[bool, str]:
    """高亮当前已同步选中对象。"""
    if not _TAPIR_IMPORT_OK:
        return False, "Tapir bridge 未导入"

    bridge = get_bridge()
    if not bridge.is_available():
        st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
        return False, st.session_state.tapir_last_error

    guids = st.session_state.get("tapir_selected_guids") or []
    if not guids:
        return False, "请先同步选中对象"

    ok = bridge.highlight_elements(guids)
    if not ok:
        st.session_state.tapir_last_error = "高亮失败"
        return False, st.session_state.tapir_last_error

    st.session_state.tapir_last_error = ""
    return True, f"已高亮 {len(guids)} 个对象"


def _tapir_load_selected_params() -> tuple[bool, str]:
    """读取当前选中对象参数到工作台。"""
    if not _TAPIR_IMPORT_OK:
        return False, "Tapir bridge 未导入"

    bridge = get_bridge()
    if not bridge.is_available():
        st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
        return False, st.session_state.tapir_last_error

    guids = st.session_state.get("tapir_selected_guids") or []
    if not guids:
        return False, "请先同步选中对象"

    rows = bridge.get_gdl_parameters_of_elements(guids)
    if not rows:
        st.session_state.tapir_selected_params = []
        st.session_state.tapir_param_edits = {}
        st.session_state.tapir_last_error = "未读取到可编辑参数（可能包含非 GDL 元素）"
        return False, st.session_state.tapir_last_error

    selected_params = []
    edit_map = {}
    skipped = 0

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        guid = (row.get("guid") or "").strip()
        if not guid:
            element_id = row.get("elementId")
            if isinstance(element_id, dict):
                _g = element_id.get("guid")
                if isinstance(_g, str):
                    guid = _g.strip()
        if not guid:
            skipped += 1
            continue

        params = row.get("gdlParameters")
        if not isinstance(params, list):
            skipped += 1
            continue

        normalized_params = []
        for p in params:
            if not isinstance(p, dict):
                continue
            normalized_params.append(dict(p))

        if not normalized_params:
            skipped += 1
            continue

        selected_params.append({
            "guid": guid,
            "gdlParameters": normalized_params,
        })

        for p in normalized_params:
            name = p.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = f"{guid}::{name.strip()}"
            value = p.get("value")
            edit_map[key] = "" if value is None else str(value)

    st.session_state.tapir_selected_params = selected_params
    st.session_state.tapir_param_edits = edit_map

    if not selected_params:
        st.session_state.tapir_last_error = "未读取到可编辑参数（可能全为非 GDL 元素）"
        return False, st.session_state.tapir_last_error

    if skipped > 0:
        st.session_state.tapir_last_error = f"已跳过 {skipped} 个不可读取参数的元素"
        return True, f"已读取 {len(selected_params)} 个对象参数（跳过 {skipped} 个）"

    st.session_state.tapir_last_error = ""
    return True, f"已读取 {len(selected_params)} 个对象参数"


def _tapir_apply_param_edits() -> tuple[bool, str]:
    """应用工作台参数改动到 Archicad。"""
    if not _TAPIR_IMPORT_OK:
        return False, "Tapir bridge 未导入"

    bridge = get_bridge()
    if not bridge.is_available():
        st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
        return False, st.session_state.tapir_last_error

    rows = st.session_state.get("tapir_selected_params") or []
    if not rows:
        return False, "当前没有可应用的参数，请先读取参数"

    edits = st.session_state.get("tapir_param_edits") or {}
    payload_rows = []
    conversion_errors = []

    for row in rows:
        guid = (row.get("guid") or "").strip()
        params = row.get("gdlParameters")
        if not guid or not isinstance(params, list):
            continue

        out_params = []
        for p in params:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = f"{guid}::{name.strip()}"
            raw_new = edits.get(key, "")
            old_val = p.get("value")

            parsed_value = raw_new
            if isinstance(old_val, bool):
                txt = str(raw_new).strip().lower()
                if txt in {"1", "true", "yes", "on"}:
                    parsed_value = True
                elif txt in {"0", "false", "no", "off"}:
                    parsed_value = False
                else:
                    conversion_errors.append(f"{guid}::{name}（Boolean）")
                    continue
            elif isinstance(old_val, int) and not isinstance(old_val, bool):
                try:
                    parsed_value = int(str(raw_new).strip())
                except Exception:
                    conversion_errors.append(f"{guid}::{name}（Integer）")
                    continue
            elif isinstance(old_val, float):
                try:
                    parsed_value = float(str(raw_new).strip())
                except Exception:
                    conversion_errors.append(f"{guid}::{name}（RealNum）")
                    continue
            else:
                parsed_value = str(raw_new)

            out_params.append({"name": name.strip(), "value": parsed_value})

        if out_params:
            payload_rows.append({"guid": guid, "gdlParameters": out_params})

    if not payload_rows:
        if conversion_errors:
            st.session_state.tapir_last_error = f"参数转换失败：{', '.join(conversion_errors[:6])}"
            return False, st.session_state.tapir_last_error
        return False, "没有可写回的参数"

    result = bridge.set_gdl_parameters_of_elements(payload_rows)
    execution_results = []
    if isinstance(result, dict):
        maybe = result.get("executionResults")
        if isinstance(maybe, list):
            execution_results = [r for r in maybe if isinstance(r, dict)]

    if not execution_results:
        st.session_state.tapir_last_error = "Tapir 未返回执行结果"
        return False, st.session_state.tapir_last_error

    fail_idx = [i for i, r in enumerate(execution_results) if r.get("success") is not True]
    if fail_idx:
        fail_guids = []
        for idx in fail_idx:
            if idx < len(payload_rows):
                fail_guids.append(payload_rows[idx].get("guid", ""))
        fail_text = ", ".join([g for g in fail_guids if g]) or "未知对象"
        st.session_state.tapir_last_error = f"部分写回失败：{fail_text}"
        suffix = f"；参数转换失败 {len(conversion_errors)} 项" if conversion_errors else ""
        return False, st.session_state.tapir_last_error + suffix

    st.session_state.tapir_last_error = ""
    suffix = f"（另有 {len(conversion_errors)} 项转换失败已跳过）" if conversion_errors else ""
    return True, f"参数已应用到 {len(payload_rows)} 个对象{suffix}"


def _render_tapir_inspector_panel() -> None:
    """Inspector 面板：显示选中对象 GUID、详情、同步状态。"""
    guids = st.session_state.get("tapir_selected_guids") or []
    details = st.session_state.get("tapir_selected_details") or []
    last_sync = st.session_state.get("tapir_last_sync_at", "")
    last_error = st.session_state.get("tapir_last_error", "")

    if last_sync:
        st.caption(f"最近同步：{last_sync}")
    if last_error:
        st.warning(last_error)

    if not guids:
        st.info("未选中对象。")
        return

    st.markdown(f"**选中 GUID（{len(guids)}）**")
    st.code("\n".join(guids), language="text")

    st.markdown("**元素详情**")
    if details:
        st.json(details)
    else:
        st.caption("暂无元素详情。")


def _render_tapir_param_workbench_panel() -> None:
    """Parameter Workbench：显示并编辑已读取参数。"""
    rows = st.session_state.get("tapir_selected_params") or []
    if not rows:
        st.info("暂无参数数据，请先点击「读取参数」。")
        return

    edits = st.session_state.get("tapir_param_edits") or {}
    for row in rows:
        guid = (row.get("guid") or "").strip()
        params = row.get("gdlParameters")
        if not guid or not isinstance(params, list):
            continue

        with st.expander(f"对象 {guid}", expanded=False):
            for p in params:
                if not isinstance(p, dict):
                    continue
                name = p.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                key = f"{guid}::{name.strip()}"
                current_value = edits.get(key, "")
                p_type = p.get("type", "")
                label = name.strip()
                if p_type:
                    label = f"{label} ({p_type})"
                new_val = st.text_input(label, value=str(current_value), key=f"tapir_edit::{key}")
                edits[key] = new_val

    st.session_state.tapir_param_edits = edits


# ── Fullscreen editor dialog (Streamlit ≥ 1.36) ───────────
_HAS_DIALOG = hasattr(st, "dialog")

if _HAS_DIALOG:
    @st.dialog("⛶ 全屏编辑", width="large")
    def _fullscreen_editor_dialog(stype: "ScriptType", fpath: str, label: str) -> None:
        st.caption(f"**{label}** 脚本 · 全屏模式 — 编辑完成点「✅ 应用」")
        code = (st.session_state.project or HSFProject.create_new("untitled")).get_script(stype) or ""
        if _ACE_AVAILABLE:
            _raw_fs = st_ace(
                value=code, language="fortran", theme="monokai",
                height=580, font_size=14, tab_size=2,
                show_gutter=True, show_print_margin=False,
                key=f"fs_ace_{fpath}",
            )
            new_code = _raw_fs if _raw_fs is not None else code
        else:
            new_code = st.text_area("code", value=code, height=580,
                                    label_visibility="collapsed", key=f"fs_ta_{fpath}") or ""
        c1, c2 = st.columns([2, 6])
        with c1:
            if st.button("✅ 应用", type="primary", width='stretch'):
                if st.session_state.project:
                    st.session_state.project.set_script(stype, new_code)
                    _bump_main_editor_version()
                st.rerun()
        with c2:
            if st.button("❌ 取消", width='stretch'):
                st.rerun()
else:
    def _fullscreen_editor_dialog(stype, fpath, label):  # type: ignore[misc]
        st.info("全屏编辑需要 Streamlit ≥ 1.36，请升级：`pip install -U streamlit`")


def get_compiler():
    if compiler_mode.startswith("Mock"):
        return MockHSFCompiler()
    return HSFCompiler(converter_path or None)

def get_llm():
    from openbrep.config import LLMConfig
    from openbrep.llm import LLMAdapter
    config = LLMConfig(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
        temperature=0.2,
        max_tokens=32768,
    )
    return LLMAdapter(config)

def load_knowledge(task_type: str = "all"):
    # Always load bundled free knowledge first
    project_kb = Path(__file__).parent.parent / "knowledge"
    kb = KnowledgeBase(str(project_kb))
    kb.load()

    # Merge user's custom free knowledge from work_dir/knowledge
    user_kb_dir = Path(st.session_state.work_dir) / "knowledge"
    if user_kb_dir.exists() and user_kb_dir != project_kb:
        user_kb = KnowledgeBase(str(user_kb_dir))
        user_kb.load()
        kb._docs.update(user_kb._docs)

    # Pro knowledge only loads after license unlock
    if st.session_state.get("pro_unlocked", False):
        pro_kb_dir = Path(st.session_state.work_dir) / "pro_knowledge"
        if pro_kb_dir.exists():
            pro_kb = KnowledgeBase(str(pro_kb_dir))
            pro_kb.load()
            kb._docs.update(pro_kb._docs)

    return kb.get_by_task_type(task_type)

def load_skills():
    # Always load from project skills dir first
    project_sk = Path(__file__).parent.parent / "skills"
    sl = SkillsLoader(str(project_sk))
    sl.load()

    # Merge user's custom skills from work_dir
    user_sk_dir = Path(st.session_state.work_dir) / "skills"
    if user_sk_dir.exists() and user_sk_dir != project_sk:
        user_sl = SkillsLoader(str(user_sk_dir))
        user_sl.load()
        sl._skills.update(user_sl._skills)   # user custom overrides project

    return sl

def _versioned_gsm_path(proj_name: str, work_dir: str, revision: int | None = None) -> str:
    """
    Return versioned GSM path.
    - If revision is provided: use exact {proj_name}_v{revision}.gsm
    - Else: fallback to next available version by file scan.
    """
    out_dir = Path(work_dir) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if revision is not None:
        return str(out_dir / f"{proj_name}_v{revision}.gsm")

    v = 1
    while (out_dir / f"{proj_name}_v{v}.gsm").exists():
        v += 1
    return str(out_dir / f"{proj_name}_v{v}.gsm")


def _max_existing_gsm_revision(proj_name: str, work_dir: str) -> int:
    """Return max existing revision in output dir for {proj_name}_v*.gsm."""
    out_dir = Path(work_dir) / "output"
    if not out_dir.exists():
        return 0

    pat = re.compile(rf"^{re.escape(proj_name)}_v(\d+)\.gsm$", re.IGNORECASE)
    max_rev = 0
    for p in out_dir.glob(f"{proj_name}_v*.gsm"):
        m = pat.match(p.name)
        if not m:
            continue
        try:
            max_rev = max(max_rev, int(m.group(1)))
        except ValueError:
            continue
    return max_rev


def _safe_compile_revision(proj_name: str, work_dir: str, requested_revision: int) -> int:
    """Pick a non-overwriting revision, monotonic by max(existing)+1."""
    max_existing = _max_existing_gsm_revision(proj_name, work_dir)
    return max(int(requested_revision or 1), max_existing + 1)


def _derive_gsm_name_from_filename(filename: str) -> str:
    """Derive clean GSM name from imported filename.

    Rules:
    - remove extension
    - remove trailing version suffix like v1 / v2.1 / _v1 / -v2
    - remove trailing numeric suffix like _001 / -002 / 123
    """
    stem = Path(filename).stem.strip()
    if not stem:
        return ""

    name = stem
    for _ in range(3):
        before = name
        name = re.sub(r'(?i)[\s._-]*v\d+(?:\.\d+)*$', '', name).strip(" _-.")
        name = re.sub(r'[\s._-]*\d+$', '', name).strip(" _-.")
        if name == before:
            break

    return name or stem.strip(" _-.")


def _extract_gsm_name_candidate(text: str) -> str:
    """Extract object name candidate from prompt with simple regex."""
    t = (text or "").strip()
    if not t:
        return ""

    # Strip debug prefix if present
    if t.startswith("[DEBUG:") and "]" in t:
        t = t.split("]", 1)[1].strip()

    pats = [
        r'(?:生成|创建|制作|做一个|做个|建一个|建个)\s*(?:一个|个)?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
        r'(?:生成|创建|制作)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
    ]
    for p in pats:
        m = re.search(p, t)
        if m:
            return m.group(1).strip(" _-.")
    return ""


def _stamp_script_header(script_label: str, content: str, revision: int) -> str:
    """Inject/refresh first-line script version header with unified revision."""
    body = content or ""
    today = _datetime.date.today().isoformat()
    header = f"! v{revision} {today} {script_label} Script"

    lines = body.splitlines()
    if lines and re.match(r'^\!\s*v\d+\s+\d{4}-\d{2}-\d{2}\s+.+\s+Script\s*$', lines[0].strip(), re.IGNORECASE):
        lines[0] = header
        return "\n".join(lines)
    return f"{header}\n{body}" if body else header


# ── Object Name Extraction (dictionary + regex, no LLM) ──

_CN_TO_NAME = {
    # 家具
    "书架": "Bookshelf", "书柜": "Bookcase", "柜子": "Cabinet",
    "衣柜": "Wardrobe", "橱柜": "Kitchen Cabinet", "储物柜": "StorageUnit",
    "桌子": "Table", "桌": "Table", "书桌": "Desk", "餐桌": "DiningTable",
    "椅子": "Chair", "椅": "Chair", "沙发": "Sofa", "床": "Bed",
    "茶几": "CoffeeTable", "电视柜": "TVStand", "鞋柜": "ShoeRack",
    # 建筑构件
    "窗": "Window", "窗框": "WindowFrame", "窗户": "Window", "百叶窗": "Louver",
    "门": "Door", "门框": "DoorFrame", "推拉门": "SlidingDoor", "旋转门": "RevolvingDoor",
    "墙": "Wall", "墙板": "WallPanel", "隔墙": "Partition", "幕墙": "CurtainWall",
    "楼梯": "Staircase", "台阶": "StairStep", "扶手": "Handrail", "栏杆": "Railing",
    "柱": "Column", "柱子": "Column", "梁": "Beam", "板": "Slab",
    "屋顶": "Roof", "天花": "Ceiling", "地板": "Floor",
    # 设备
    "灯": "Light", "灯具": "LightFixture", "管道": "Pipe", "风管": "Duct",
    "开关": "Switch", "插座": "Outlet", "空调": "AirConditioner",
    # 景观
    "花盆": "Planter", "树": "Tree", "围栏": "Fence", "长凳": "Bench",
}

def _extract_object_name(text: str) -> str:
    """
    Extract GDL object name from user input.
    Priority: explicit English name > Chinese keyword dict > fallback.
    Zero LLM calls — instant and 100% reliable.
    """
    # 1. Check for explicit English name: "named MyShelf", "叫 MyShelf"
    for pat in [
        r'named?\s+([A-Za-z][A-Za-z0-9]{2,30})',
        r'called\s+([A-Za-z][A-Za-z0-9]{2,30})',
        r'名为\s*([A-Za-z][A-Za-z0-9]{2,30})',
        r'叫\s*([A-Za-z][A-Za-z0-9]{2,30})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)

    # 2. Chinese keyword → English CamelCase (longest match first)
    for cn, en in sorted(_CN_TO_NAME.items(), key=lambda x: len(x[0]), reverse=True):
        if cn in text:
            print(f"[name] '{cn}' → {en}")
            return en

    # 3. Pick first CamelCase English word in text (skip short junk like UI, AI, GDL)
    for word in re.findall(r'[A-Z][a-z]{2,}[A-Za-z0-9]*', text):
        if word not in {"The", "For", "And", "Not", "But", "With"}:
            return word

    return "MyObject"


# ── Welcome / Onboarding Panel ────────────────────────────

def show_welcome():
    st.markdown("""
<div class="welcome-card">
<h2 style="color:#22d3ee; margin-top:0; font-family:'JetBrains Mono';">欢迎使用 OpenBrep 🏗️</h2>
<p style="color:#94a3b8;">用自然语言驱动 ArchiCAD GDL 对象的创建与编译。无需了解 GDL 语法，直接描述需求即可。</p>
</div>
""", unsafe_allow_html=True)

    st.markdown("#### 三步快速开始")

    st.info("**① 配置 API Key**  \n在左侧边栏选择 AI 模型，填入对应 API Key。免费的智谱 GLM 可直接使用。")
    st.info("**② 开始对话**  \n在底部输入框描述你想创建的 GDL 对象，例如：  \n「创建一个宽 600mm、深 400mm 的书架，带 iShelves 参数控制层数」")
    st.info("**③ 编译输出**  \nAI 生成代码后自动触发编译。真实编译需在侧边栏配置 LP_XMLConverter 路径。Mock 模式可验证结构，无需安装 ArchiCAD。")

    st.divider()

    st.markdown("#### 或者：导入已有文件")
    uploaded_file = st.file_uploader(
        "拖入 .gdl / .txt / .gsm 文件",
        type=["gdl", "txt", "gsm"],
        help=".gdl / .txt 直接解析脚本；.gsm 需侧边栏切换为 LP 模式",
        key="welcome_upload",
    )
    if uploaded_file:
        ok, msg = _handle_unified_import(uploaded_file)
        if not ok:
            st.error(msg)
        else:
            st.rerun()

    st.divider()
    st.caption("💡 提示：第一条消息无需创建项目，直接描述需求，AI 会自动初始化。")


# ── Intent Classification ─────────────────────────────────

_GDL_KEYWORDS = [
    # 动作
    "创建", "生成", "制作", "做一个", "建一个", "写一个", "写个", "写一",
    "做个", "建个", "来个", "整个", "出一个", "出个",
    "修改", "更新", "添加", "删除", "调整", "优化", "重写", "补充",
    # 建筑/家具对象（中文）
    "书架", "柜子", "衣柜", "橱柜", "储物柜", "鞋柜", "电视柜",
    "桌子", "桌", "椅子", "椅", "沙发", "床", "茶几", "柜",
    "窗", "门", "墙", "楼梯", "柱", "梁", "板", "扶手", "栏杆",
    "屋顶", "天花", "地板", "灯", "管道",
    # 技术词
    "参数", "parameter", "script", "gdl", "gsm", "hsf",
    "compile", "编译", "build", "create", "make", "add",
    "3d", "2d", "prism", "block", "sphere", "prism_", "body",
    "project2", "rect2", "poly2",
]

# Pure chat patterns — greeting / meta questions only
_CHAT_ONLY_PATTERNS = [
    r"^(你好|hello|hi|hey|嗨|哈喽)[!！。\s]*$",
    r"^(谢谢|感谢|thanks)[!！。\s]*$",
    r"^你(是谁|能做什么|有什么功能)",
    r"^(怎么|如何|什么是).*(gdl|archicad|hsf|构件)",
]

def _is_gdl_intent(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _GDL_KEYWORDS)

def _is_pure_chat(text: str) -> bool:
    return any(re.search(p, text.strip(), re.IGNORECASE) for p in _CHAT_ONLY_PATTERNS)

def classify_and_extract(text: str, llm, project_loaded: bool = False) -> tuple:
    """
    Returns: (intent, obj_name)
    When project is already loaded, default to GDL for anything ambiguous.
    """
    obj_name = _extract_object_name(text)

    # Pure greetings / meta questions always → CHAT regardless of project state
    if _is_pure_chat(text):
        return ("CHAT", obj_name)

    # Keyword fast-path
    if _is_gdl_intent(text):
        return ("GDL", obj_name)

    # Project loaded: assume user wants to edit — treat ambiguous as GDL
    if project_loaded:
        print(f"[classify] project loaded → default GDL for: '{text[:40]}'")
        return ("GDL", obj_name)

    # No project, ambiguous → ask LLM (one word)
    try:
        resp = llm.generate([
            {
                "role": "system",
                "content": (
                    "你是意图分类器。判断用户是否想创建或修改 ArchiCAD GDL 构件。\n"
                    "只回复一个词：GDL 或 CHAT\n"
                    "GDL = 要创建/修改/编译构件\n"
                    "CHAT = 闲聊/打招呼/问用法"
                ),
            },
            {"role": "user", "content": text},
        ], max_tokens=10, temperature=0.1)

        raw = resp.content.strip().upper()
        print(f"[classify] LLM intent: '{raw}'")
        return ("GDL" if "GDL" in raw else "CHAT", obj_name)

    except Exception as e:
        print(f"[classify] exception: {e}")
        return ("CHAT", obj_name)


def chat_respond(user_input: str, history: list, llm) -> str:
    """Simple conversational response. Never outputs GDL code — that goes to the editor."""
    system_msg = {
        "role": "system",
        "content": (
            "你是 openbrep 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
            "【重要约束】绝对禁止在回复中输出任何 GDL 代码、代码块或脚本片段。"
            "如果用户想创建或修改 GDL 对象，告诉他「直接在底部输入框描述需求，AI 会自动生成并填入编辑器」。\n"
            "不要提及 ArchiCAD 内部操作（如打开 GDL 对象编辑器），因为本工具就是体外的 GDL IDE。\n"
            "回复简洁，使用中文，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
        ),
    }
    messages = [system_msg]
    # Include recent history for context (last 6 messages)
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_input})

    try:
        resp = llm.generate(messages)
        return resp.content
    except Exception as e:
        return f"❌ {str(e)}"


# ── Script Map (module-level, shared by agent + editor) ───
_SCRIPT_MAP = [
    (ScriptType.SCRIPT_3D, "scripts/3d.gdl",  "3D"),
    (ScriptType.SCRIPT_2D, "scripts/2d.gdl",  "2D"),
    (ScriptType.MASTER,    "scripts/1d.gdl",  "Master"),
    (ScriptType.PARAM,     "scripts/vl.gdl",  "Param"),
    (ScriptType.UI,        "scripts/ui.gdl",  "UI"),
    (ScriptType.PROPERTIES,"scripts/pr.gdl",  "Properties"),
]


def _main_editor_state_key(fpath: str, editor_version: int) -> str:
    prefix = "ace" if _ACE_AVAILABLE else "script"
    return f"{prefix}_{fpath}_v{editor_version}"



def _mark_main_ace_editors_pending(editor_version: int) -> None:
    if not _ACE_AVAILABLE:
        st.session_state._ace_pending_main_editor_keys = set()
        return
    st.session_state._ace_pending_main_editor_keys = {
        f"ace_{fpath}_v{editor_version}"
        for _, fpath, _ in _SCRIPT_MAP
    }


def _bump_main_editor_version() -> int:
    st.session_state.editor_version = int(st.session_state.get("editor_version", 0)) + 1
    _mark_main_ace_editors_pending(st.session_state.editor_version)
    return st.session_state.editor_version


# ── Run Agent ─────────────────────────────────────────────

# Keywords that signal debug/analysis intent → inject all scripts + allow plain-text reply
_DEBUG_KEYWORDS = {
    "debug", "fix", "error", "bug", "wrong", "issue", "broken", "fail", "crash",
    "问题", "错误", "调试", "检查", "分析", "为什么", "帮我看", "看看", "出错",
    "不对", "不行", "哪里", "原因", "解释", "explain", "why", "what", "how",
    "review", "看一下", "看下", "告诉我", "这段", "这个脚本",
}

# Archicad GDL 错误格式特征
import re as _re
_ARCHICAD_ERROR_PATTERN = _re.compile(
    r"(error|warning)\s+in\s+\w[\w\s]*script[,\s]+line\s+\d+",
    _re.IGNORECASE
)

def _is_debug_intent(text: str) -> bool:
    if text.startswith("[DEBUG:editor]"):
        return True
    # 自动识别粘贴进来的 Archicad 错误报告
    if _ARCHICAD_ERROR_PATTERN.search(text):
        return True
    t = text.lower()
    return any(kw in t for kw in _DEBUG_KEYWORDS)

def _get_debug_mode(text: str) -> str:
    """Returns 'editor' or 'keyword' (fallback)."""
    if text.startswith("[DEBUG:editor]"):
        return "editor"
    return "keyword"


def run_agent_generate(
    user_input: str,
    proj: HSFProject,
    status_col,
    gsm_name: str = None,
    auto_apply: bool = True,
    debug_image_b64: str | None = None,
    debug_image_mime: str = "image/png",
) -> str:
    """
    Unified chat+generate entry point.

    auto_apply=True  → immediately write changes to project (first creation of empty project).
    auto_apply=False → queue changes in pending_diffs; UI shows confirmation banner in chat column.

    debug_mode (intent-based) controls whether all scripts are injected into LLM context
    and whether LLM is allowed to reply with plain-text analysis in addition to code.
    """
    status_ph = status_col.empty()
    debug_mode = _is_debug_intent(user_input)
    debug_type = _get_debug_mode(user_input)  # 'editor' | 'keyword'

    def on_event(event_type, data):
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            mode_tag = f" [Debug:{debug_type}]" if debug_mode else ""
            status_ph.info(f"🔍 分析中{mode_tag}... 脚本: {', '.join(scripts)}")
        elif event_type == "attempt":
            status_ph.info("🧠 调用 AI...")
        elif event_type == "llm_response":
            status_ph.info(f"✏️ 收到 {data['length']} 字符，解析中...")
        elif event_type == "validate":
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])
            if errors:
                status_ph.error(f"❌ 发现 {len(errors)} 个错误，AI 自动修复中...")
            elif warnings:
                status_ph.warning(f"⚠️ 发现 {len(warnings)} 条建议，已附在结果中")
            else:
                status_ph.success("✅ 校验通过")
        elif event_type == "rewrite":
            round_num = data.get("round", 2)
            status_ph.info(f"🔄 第 {round_num} 轮修复中...")

    try:
        llm = get_llm()
        knowledge = load_knowledge()
        # Strip debug prefix and extract syntax report
        clean_instruction = user_input
        syntax_report = ""
        if user_input.startswith("[DEBUG:editor]"):
            _after_prefix = user_input.split("]", 1)[-1].strip()
            if "[SYNTAX CHECK REPORT]" in _after_prefix:
                _parts = _after_prefix.split("[SYNTAX CHECK REPORT]", 1)
                clean_instruction = _parts[0].strip()
                syntax_report = _parts[1].strip()
            else:
                clean_instruction = _after_prefix

        skills_text = load_skills().get_for_task(clean_instruction)

        # Pass recent chat history for multi-turn context (last 6 messages, skip heavy code blocks)
        recent_history = [
            m for m in st.session_state.chat_history[-8:]
            if m["role"] in ("user", "assistant")
        ]

        last_code_context = None

        agent = GDLAgent(llm=llm, compiler=get_compiler(), on_event=on_event)
        changes, plain_text = agent.generate_only(
            instruction=clean_instruction, project=proj,
            knowledge=knowledge, skills=skills_text,
            include_all_scripts=(debug_mode and debug_type != "last"),
            last_code_context=last_code_context,
            syntax_report=syntax_report,
            history=recent_history,
            image_b64=debug_image_b64,
            image_mime=debug_image_mime,
        )
        status_ph.empty()

        reply_parts = []

        # Plain-text analysis from LLM (debug/explanation)
        if plain_text:
            reply_parts.append(plain_text)

        # Code changes — strip fences, apply or queue for confirmation
        if changes:
            cleaned = {k: _strip_md_fences(v) for k, v in changes.items()}

            script_names = ", ".join(
                p.replace("scripts/", "").replace(".gdl", "").upper()
                for p in cleaned if p.startswith("scripts/")
            )
            has_params = "paramlist.xml" in cleaned
            param_count_preview = len(_parse_paramlist_text(cleaned.get("paramlist.xml", "")))

            code_blocks = []
            for fpath, code in cleaned.items():
                lbl = fpath.replace("scripts/", "").replace(".gdl", "").upper()
                code_blocks.append(f"**{lbl}**\n```gdl\n{code}\n```")

            label_parts = []
            if script_names:
                label_parts.append(f"脚本 [{script_names}]")
            if has_params:
                label_parts.append(f"{param_count_preview} 个参数")
            label_str = " + ".join(label_parts) if label_parts else "内容"

            if auto_apply:
                # 全新空项目：直接写入，无需确认
                sc, pc = _apply_scripts_to_project(proj, cleaned)
                _bump_main_editor_version()
                if gsm_name:
                    st.session_state.pending_gsm_name = gsm_name
                reply_parts.append(
                    f"✏️ **已写入 {label_str}** — 可直接「🔧 编译」\n\n"
                    + "\n\n".join(code_blocks)
                )
            else:
                # 已有项目修改：暂存，聊天栏内显示确认按钮
                st.session_state.pending_diffs    = cleaned
                st.session_state.pending_ai_label = label_str
                if gsm_name:
                    st.session_state.pending_gsm_name = gsm_name
                reply_parts.append(
                    f"🤖 **AI 已生成 {label_str}** — 请在下方确认是否写入编辑器。\n\n"
                    + "\n\n".join(code_blocks)
                )

        if reply_parts:
            return "\n\n---\n\n".join(reply_parts)

        return "🤔 AI 未返回代码或分析，请换一种描述方式。"

    except Exception as e:
        status_ph.empty()
        return f"❌ **错误**: {str(e)}"


def _parse_paramlist_text(text: str) -> list:
    """
    Parse 'Type Name = Value ! Description' lines → list[GDLParameter].
    Handles LLM output from [FILE: paramlist.xml] sections.
    """
    import re as _re
    _VALID_TYPES = {
        "Length", "Angle", "RealNum", "Integer", "Boolean",
        "String", "PenColor", "FillPattern", "LineType", "Material",
    }
    params = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("!") or line.startswith("#"):
            continue
        # Format: Type Name = Value  [! description]
        m = _re.match(r'(\w+)\s+(\w+)\s*=\s*(.+?)(?:\s*!\s*(.*))?$', line)
        if m:
            ptype, pname, pval, pdesc = m.groups()
            if ptype in _VALID_TYPES:
                params.append(GDLParameter(
                    pname, ptype, (pdesc or "").strip(), pval.strip().strip('"'),
                ))
    return params


def _sanitize_script_content(raw: str, fpath: str) -> str:
    """Best-effort sanitize to avoid narrative text leaking into script editors."""
    import re as _re

    text = (raw or "").strip()
    if not text:
        return ""

    # Remove fenced blocks if model leaked markdown wrappers
    text = _strip_md_fences(text)

    # If model accidentally included nested [FILE:] in content, keep only before next header
    _next_header = _re.search(r"(?m)^\s*\[FILE:\s*.+?\]\s*$", text)
    if _next_header:
        text = text[:_next_header.start()].rstrip()

    # For GDL scripts: only drop obvious markdown/prose artifacts.
    # Keep unknown commands and non-ASCII string literals to avoid accidental data loss.
    if fpath.startswith("scripts/"):
        kept = []
        _prose_prefix = _re.compile(r"^(分析|说明|原因|修复|结论|总结)\s*[:：]")
        _numbered_md = _re.compile(r"^\d+\.\s+")

        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                kept.append(ln)
                continue
            if s.startswith(("#", "##", "###", "- ", "* ", ">")):
                continue
            if _numbered_md.match(s):
                continue
            if _prose_prefix.match(s):
                continue
            kept.append(ln)
        text = "\n".join(kept).strip()

    return text


def _apply_scripts_to_project(proj: HSFProject, script_map: dict) -> tuple[int, int]:
    """
    Apply {fpath: content} dict to project.
    Handles scripts/3d.gdl etc. + paramlist.xml.
    Returns (script_count, param_count) for notification.
    """
    _label_map = {
        "scripts/3d.gdl": "3D",
        "scripts/2d.gdl": "2D",
        "scripts/1d.gdl": "Master",
        "scripts/vl.gdl": "Param",
        "scripts/ui.gdl": "UI",
        "scripts/pr.gdl": "Properties",
    }

    # 命中脚本文件即视为一次脚本更新（即便内容清洗后为空，也属于覆盖写入）
    has_script_update = any(
        fpath in script_map
        for _, fpath, _ in _SCRIPT_MAP
    )
    if has_script_update:
        st.session_state.script_revision = int(st.session_state.get("script_revision", 0)) + 1
    _rev = int(st.session_state.get("script_revision", 0))

    sc = 0
    for stype, fpath, _label in _SCRIPT_MAP:
        if fpath in script_map:
            _clean = _sanitize_script_content(script_map[fpath], fpath)
            _script_label = _label_map.get(fpath, _label)
            # 命中文件必须全覆盖写入：清洗后为空则写成真正空脚本
            _final = _stamp_script_header(_script_label, _clean, _rev) if _clean else ""
            proj.set_script(stype, _final)
            sc += 1
    pc = 0
    if "paramlist.xml" in script_map:
        new_params = _parse_paramlist_text(script_map["paramlist.xml"])
        if new_params:
            proj.parameters = new_params
            pc = len(new_params)

    if sc > 0 or pc > 0:
        st.session_state.preview_2d_data = None
        st.session_state.preview_3d_data = None
        st.session_state.preview_warnings = []
        st.session_state.preview_meta = {"kind": "", "timestamp": ""}

    return sc, pc


def do_compile(proj: HSFProject, gsm_name: str, instruction: str = "") -> tuple:
    """
    Compile current project state → versioned GSM.
    Returns (success: bool, message: str).
    """
    try:
        _requested_rev = int(st.session_state.get("script_revision", 0)) or 1
        _compile_rev = _safe_compile_revision(gsm_name or proj.name, st.session_state.work_dir, _requested_rev)
        if _compile_rev != _requested_rev:
            st.session_state.script_revision = _compile_rev
        output_gsm = _versioned_gsm_path(gsm_name or proj.name, st.session_state.work_dir, revision=_compile_rev)
        hsf_dir = proj.save_to_disk()
        result = get_compiler().hsf2libpart(str(hsf_dir), output_gsm)
        mock_tag = " [Mock]" if compiler_mode.startswith("Mock") else ""

        if result.success:
            st.session_state.compile_log.append({
                "project": proj.name, "instruction": instruction,
                "success": True, "attempts": 1, "message": "Success",
            })
            msg = f"✅ **编译成功{mock_tag}**\n\n📦 `{output_gsm}`"
            if compiler_mode.startswith("Mock"):
                msg += "\n\n⚠️ Mock 模式不生成真实 .gsm，切换 LP_XMLConverter 进行真实编译。"
            return (True, msg)
        else:
            st.session_state.compile_log.append({
                "project": proj.name, "instruction": instruction,
                "success": False, "attempts": 1, "message": result.stderr,
            })
            return (False, f"❌ **编译失败**\n\n```\n{result.stderr[:500]}\n```")
    except Exception as e:
        return (False, f"❌ **错误**: {str(e)}")


def import_gsm(gsm_bytes: bytes, filename: str) -> tuple:
    """
    Decompile GSM → HSF → HSFProject via LP_XMLConverter libpart2hsf.
    Returns (project | None, message).
    """
    import tempfile, shutil
    compiler = get_compiler()

    # Guard: must have a real compiler
    if isinstance(compiler, MockHSFCompiler):
        return (None, "❌ GSM 导入需要 LP_XMLConverter，Mock 模式不支持。请在侧边栏选择 LP 模式并指定路径。")

    # Diagnostic: report which binary will be used
    bin_path = compiler.converter_path or "(未检测到)"
    if not compiler.is_available:
        return (
            None,
            f"❌ LP_XMLConverter 未找到\n\n"
            f"检测路径: `{bin_path}`\n\n"
            f"macOS 正确路径示例:\n"
            f"`/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter`\n\n"
            f"请在侧边栏手动填写正确路径。"
        )

    tmp = Path(tempfile.mkdtemp())
    gsm_path = tmp / filename
    gsm_path.write_bytes(gsm_bytes)
    hsf_out = tmp / "hsf_out"
    hsf_out.mkdir()

    result = compiler.libpart2hsf(str(gsm_path), str(hsf_out))

    if not result.success:
        # Show full diagnostics so user can debug
        diag = result.stderr or result.stdout or "(无输出)"
        shutil.rmtree(tmp, ignore_errors=True)
        return (
            None,
            f"❌ GSM 解包失败 (exit={result.exit_code})\n\n"
            f"**Binary**: `{bin_path}`\n\n"
            f"**输出**:\n```\n{diag[:800]}\n```"
        )

    try:
        # Locate true HSF root — LP_XMLConverter output layout varies by AC version:
        #   AC 27/28 (standard): hsf_out/<LIBPARTNAME>/libpartdata.xml + scripts/
        #   AC 29 (flat):        hsf_out/libpartdata.xml + scripts/  (no named subdir)
        def _find_hsf_root(base: Path) -> Path:
            # 1. base itself has libpartdata.xml → it IS the HSF root
            if (base / "libpartdata.xml").exists():
                return base
            # 2. base itself has a scripts/ subdir → treat base as root
            if (base / "scripts").is_dir():
                return base
            # 3. one named subdir with libpartdata.xml → standard layout
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "libpartdata.xml").exists():
                    return d
            # 4. one named subdir with scripts/ → standard layout without metadata
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "scripts").is_dir():
                    return d
            # 5. last resort: first subdir (or base itself)
            subdirs = [d for d in base.iterdir() if d.is_dir()]
            return subdirs[0] if subdirs else base

        hsf_dir = _find_hsf_root(hsf_out)

        if not hsf_dir.exists():
            contents = list(hsf_out.iterdir())
            shutil.rmtree(tmp, ignore_errors=True)
            return (
                None,
                f"❌ 无法定位 HSF 根目录\n\n"
                f"hsf_out 内容: `{[str(c.name) for c in contents]}`\n\n"
                f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
            )

        # Snapshot directory tree before rmtree wipes it
        hsf_files = sorted(str(p.relative_to(hsf_dir)) for p in hsf_dir.rglob("*") if p.is_file())

        proj = HSFProject.load_from_disk(str(hsf_dir))
        # AC29 flat layout: hsf_dir == hsf_out → name is "hsf_out", use GSM stem instead
        gsm_stem = Path(filename).stem
        if proj.name in ("hsf_out", "scripts", ""):
            proj.name = gsm_stem
        proj.work_dir = Path(st.session_state.work_dir)
        proj.root = proj.work_dir / proj.name

        scripts_found = [s.value for s in proj.scripts]
        diag = (
            f"\n\n**HSF 文件列表**: `{hsf_files}`"
            f"\n**已识别脚本**: `{scripts_found}`"
        )
        return (proj, f"✅ 已导入 `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本{diag}")
    except Exception as e:
        return (None, f"❌ HSF 解析失败: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _handle_unified_import(uploaded_file) -> tuple[bool, str]:
    """
    Single entry point for importing any GDL-related file.
    - .gsm           → LP_XMLConverter decompile → HSFProject
    - .gdl / .txt    → parse_gdl_source text parse → HSFProject
    Updates session_state.project, pending_gsm_name, editor_version.
    Returns (success, message).
    """
    fname = uploaded_file.name
    ext   = Path(fname).suffix.lower()

    if ext == ".gsm":
        with st.spinner("解包 GSM..."):
            proj, msg = import_gsm(uploaded_file.read(), fname)
        if not proj:
            return (False, msg)
    else:
        # .gdl / .txt — plain text
        try:
            content = uploaded_file.read().decode("utf-8", errors="replace")
            proj = parse_gdl_source(content, Path(fname).stem)
        except Exception as e:
            return (False, f"❌ 导入失败: {e}")
        msg = f"✅ 已导入 GDL `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本"

    proj.work_dir = Path(st.session_state.work_dir)
    proj.root = proj.work_dir / proj.name
    st.session_state.project = proj
    st.session_state.pending_diffs = {}
    st.session_state.preview_2d_data = None
    st.session_state.preview_3d_data = None
    st.session_state.preview_warnings = []
    st.session_state.preview_meta = {"kind": "", "timestamp": ""}
    _import_gsm_name = _derive_gsm_name_from_filename(fname) or proj.name
    st.session_state.pending_gsm_name = _import_gsm_name
    st.session_state.script_revision = 0
    _reset_tapir_p0_state()
    _bump_main_editor_version()
    st.session_state.chat_history.append({"role": "assistant", "content": msg})
    return (True, msg)


def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that AI sometimes leaks into scripts."""
    import re as _re
    # Remove opening fence (```gdl, ```GDL, ```)
    code = _re.sub(r'^```[a-zA-Z]*\s*\n?', '', code.strip(), flags=_re.MULTILINE)
    # Remove closing fence
    code = _re.sub(r'\n?```\s*$', '', code.strip(), flags=_re.MULTILINE)
    return code.strip()


def _classify_code_blocks(text: str) -> dict:
    """
    Extract and classify GDL/paramlist code blocks from raw text.
    Returns {script_path_or_"paramlist.xml": code}.  Last block wins per type.

    Classification priority (same as _extract_gdl_from_chat):
      1. paramlist.xml  — ≥2 lines 'Type Name = Value'
      2. scripts/2d.gdl — PROJECT2 / RECT2 / POLY2
      3. scripts/vl.gdl — VALUES or LOCK (no BLOCK)
      4. scripts/1d.gdl — GLOB_ variable
      5. scripts/ui.gdl — UI_CURRENT or DEFINE STYLE
      6. scripts/3d.gdl — default
    """
    import re as _re
    collected: dict[str, str] = {}
    code_block_pat = _re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", _re.DOTALL)
    _PARAM_TYPE_RE = _re.compile(
        r'^\s*(Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material)'
        r'\s+\w+\s*=', _re.IGNORECASE | _re.MULTILINE
    )
    for m in code_block_pat.finditer(text):
        block = m.group(1).strip()
        if not block:
            continue
        block_up = block.upper()
        if len(_PARAM_TYPE_RE.findall(block)) >= 2:
            path = "paramlist.xml"
        elif _re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', block_up):
            path = "scripts/2d.gdl"
        elif _re.search(r'\bVALUES\b|\bLOCK\b', block_up) and not _re.search(r'\bBLOCK\b', block_up):
            path = "scripts/vl.gdl"
        elif _re.search(r'\bGLOB_\w+\b', block_up):
            path = "scripts/1d.gdl"
        elif _re.search(r'\bUI_CURRENT\b|\bDEFINE\s+STYLE\b|\bUI_DIALOG\b|\bUI_PAGE\b|\bUI_INFIELD\b|\bUI_OUTFIELD\b|\bUI_BUTTON\b|\bUI_GROUPBOX\b|\bUI_LISTFIELD\b|\bUI_SEPARATOR\b', block_up):
            path = "scripts/ui.gdl"
        else:
            path = "scripts/3d.gdl"
        collected[path] = block
    return collected


def _extract_gdl_from_text(text: str) -> dict:
    """Extract GDL code blocks from a single message string."""
    return _classify_code_blocks(text)


def _extract_gdl_from_chat() -> dict:
    """Scan all assistant messages in chat history; last block per type wins."""
    collected: dict[str, str] = {}
    for msg in st.session_state.get("chat_history", []):
        if msg.get("role") != "assistant":
            continue
        for path, block in _classify_code_blocks(msg.get("content", "")).items():
            collected[path] = block
    return collected


def _build_chat_script_anchors(history: list[dict]) -> list[dict]:
    """Build script anchors from assistant messages containing code blocks."""
    anchors: list[dict] = []
    rev = 1
    for i, msg in enumerate(history):
        if msg.get("role") != "assistant":
            continue
        extracted = _classify_code_blocks(msg.get("content", ""))
        if not extracted:
            continue
        script_keys = [
            p.replace("scripts/", "").replace(".gdl", "").upper()
            for p in extracted.keys()
            if p.startswith("scripts/")
        ]
        parts = []
        if script_keys:
            parts.append("/".join(script_keys))
        if "paramlist.xml" in extracted:
            parts.append("PARAM")
        scope = " + ".join(parts) if parts else "CODE"
        anchors.append({
            "rev": rev,
            "msg_idx": i,
            "label": f"r{rev} · {scope}",
            "paths": sorted(extracted.keys()),
        })
        rev += 1
    return anchors


def _thumb_image_bytes(image_b64: str) -> bytes | None:
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except Exception:
        return None


def _detect_image_task_mode(user_text: str, image_name: str = "") -> str:
    """Heuristic mode routing for unified image upload: 'debug' or 'generate'."""
    t = (user_text or "").lower()
    n = (image_name or "").lower()

    debug_tokens = [
        "debug", "error", "报错", "错误", "失败", "修复", "定位", "排查", "warning", "line ", "script",
        "screenshot", "截图", "log", "trace", "崩溃", "不显示", "异常",
    ]
    gen_tokens = [
        "生成", "创建", "建模", "构件", "参考", "外观", "照片", "photo", "reference", "design",
    ]

    if any(k in t for k in debug_tokens):
        return "debug"
    if any(k in t for k in gen_tokens):
        return "generate"

    # Filename cues fallback
    if any(k in n for k in ["screenshot", "screen", "截屏", "截图", "error", "debug", "log"]):
        return "debug"
    if any(k in n for k in ["photo", "img", "image", "参考", "模型", "design"]):
        return "generate"

    # Project already exists -> default debug for safer modification path
    if st.session_state.get("project"):
        return "debug"
    return "generate"


# ── Vision prompt ─────────────────────────────────────────────────────────────

_VISION_SYSTEM_PROMPT = """\
你是专业 GDL 建筑师，精通 ArchiCAD GDL scripting（GDL Reference v26 标准）。
用户上传了一张建筑构件/家具/设施图片，请按以下结构输出：

## 构件识别
- 类型：（书架 / 桌椅 / 门窗 / 楼梯 / 柱 / 墙面板 / 灯具 / ...）
- 几何形态：（主体形状、结构层次、细部特征，2-4句）
- 材料/表面：（可见材质，用于 Material 参数默认值）

## 参数化分析
以 GDL paramlist 格式列出所有可参数化维度，给出合理默认值（长度单位 mm，转为 m 除以 1000）：

```
Length w  = 0.9     ! 总宽度（m）
Length h  = 2.1     ! 总高度（m）
Length d  = 0.3     ! 总深度（m）
Integer n = 4       ! 重复单元数量
Material mat = "Wood"  ! 主体材质
```

## GDL 3D Script

```gdl
! [构件名称] — AI 从图片生成
! 参数：w h d n mat

MATERIAL mat

! 主体
BLOCK w, d, h

END
```

规则：
- paramlist 代码块内必须有 ≥2 行 `Type Name = value  ! 注释` 格式
- 3D Script 最后一行必须是 `END`（单独一行）
- 所有尺寸由参数驱动，禁止硬编码数字
- GDL 命令必须全大写（BLOCK / CYLIND / LINE3 / ADD / DEL / FOR / NEXT 等）
- 如有重复元素（层板/格栅/百叶）用 FOR/NEXT 循环
"""


# ── Vision generate ───────────────────────────────────────────────────────────

def run_vision_generate(
    image_b64: str,
    image_mime: str,
    extra_text: str,
    proj: HSFProject,
    status_col,
    auto_apply: bool = True,
) -> str:
    """
    Vision pipeline: image → LLM analysis → GDL extraction → pending_diffs or auto-apply.
    Reuses the same confirmation flow as run_agent_generate.
    """
    status_ph = status_col.empty()
    try:
        llm = get_llm()
        status_ph.info("🖼️ AI 正在解析图片...")

        user_text = extra_text.strip() if extra_text else "请分析这张图片，生成对应的 GDL 脚本。"
        resp = llm.generate_with_image(
            text_prompt=user_text,
            image_b64=image_b64,
            image_mime=image_mime,
            system_prompt=_VISION_SYSTEM_PROMPT,
        )
        status_ph.empty()

        raw_text = resp.content
        extracted = _classify_code_blocks(raw_text)

        if extracted:
            script_names = ", ".join(
                k.replace("scripts/", "").replace(".gdl", "").upper()
                for k in extracted if k.startswith("scripts/")
            )
            param_count = len(_parse_paramlist_text(extracted.get("paramlist.xml", "")))
            label_parts = []
            if script_names:
                label_parts.append(f"脚本 [{script_names}]")
            if param_count:
                label_parts.append(f"{param_count} 个参数")
            label_str = " + ".join(label_parts) or "内容"

            if auto_apply:
                _apply_scripts_to_project(proj, extracted)
                _bump_main_editor_version()
                prefix = f"🖼️ **图片解析完成，{label_str} 已写入编辑器** — 可直接「🔧 编译」\n\n"
            else:
                st.session_state.pending_diffs    = extracted
                st.session_state.pending_ai_label = label_str
                prefix = f"🖼️ **图片解析完成，AI 生成了 {label_str}** — 请在下方确认是否写入\n\n"

            return prefix + raw_text

        else:
            return f"🖼️ **图片分析完成**（未检测到 GDL 代码块，AI 可能只给了文字分析）\n\n{raw_text}"

    except Exception:
        status_ph.empty()
        st.error("图片分析失败，当前模型可能不支持视觉功能，请切换至 glm-4v-plus / gpt-4o / claude-sonnet-4-6")
        return "❌ 图片分析失败，当前模型可能不支持视觉功能，请切换至 glm-4v-plus / gpt-4o / claude-sonnet-4-6"


def check_gdl_script(content: str, script_type: str = "") -> list:
    """
    Basic GDL syntax check. Returns list of warning strings (empty = OK).
    Checks: IF/ENDIF, FOR/NEXT, ADD/DEL balance, END in 3D, PROJECT2 in 2D.
    """
    import re as _re
    issues = []
    if not content.strip():
        if script_type == "2d":
            issues.append("⚠️ 2D 脚本为空，必须至少包含 PROJECT2 3, 270, 2")
        return issues

    lines = content.splitlines()

    # IF/ENDIF balance (only multi-line IF: IF ... THEN at end of line)
    if_multi = sum(
        1 for l in lines
        if _re.search(r'\bIF\b', l, _re.I)
        and _re.search(r'\bTHEN\s*$', l.strip(), _re.I)
    )
    endif_count = sum(1 for l in lines if _re.match(r'\s*ENDIF\b', l, _re.I))
    if if_multi != endif_count:
        issues.append(f"⚠️ IF/ENDIF 不匹配：{if_multi} 个多行 IF，{endif_count} 个 ENDIF")

    # FOR/NEXT balance
    for_count = sum(1 for l in lines if _re.match(r'\s*FOR\b', l, _re.I))
    next_count = sum(1 for l in lines if _re.match(r'\s*NEXT\b', l, _re.I))
    if for_count != next_count:
        issues.append(f"⚠️ FOR/NEXT 不匹配：{for_count} 个 FOR，{next_count} 个 NEXT")

    # ADD/DEL balance — ADDX/ADDY/ADDZ are single-axis variants, count equally
    add_count = sum(1 for l in lines if _re.match(r'\s*ADD(X|Y|Z)?\b', l, _re.I))
    del_count = sum(1 for l in lines if _re.match(r'\s*DEL\b', l, _re.I))
    if add_count != del_count:
        issues.append(f"⚠️ ADD/DEL 不匹配：{add_count} 个 ADD/ADDX/ADDY/ADDZ，{del_count} 个 DEL")

    # Markdown fence leak — common when AI generates code in chat
    if any(l.strip().startswith("```") for l in lines):
        issues.append("⚠️ 脚本含有 ``` 标记 — AI 格式化残留，请删除所有反引号行")

    # 3D: END / subroutine RETURN check
    if script_type == "3d":
        # Detect subroutine labels:  "SubName":
        sub_label_pat = _re.compile(r'^\s*"[^"]+"\s*:')
        has_subs = any(sub_label_pat.match(l) for l in lines)

        if has_subs:
            # Main body = lines before first subroutine label
            main_body = []
            for l in lines:
                if sub_label_pat.match(l):
                    break
                main_body.append(l)
            last_main = next((l.strip() for l in reversed(main_body) if l.strip()), "")
            if not _re.match(r'^END\s*$', last_main, _re.I):
                issues.append("⚠️ 3D 主体部分（第一个子程序之前）最后一行必须是 END")

            # Each subroutine should end with RETURN (not END)
            current_sub = None
            sub_lines: list[str] = []
            for l in lines:
                if sub_label_pat.match(l):
                    if current_sub and sub_lines:
                        last_sub = next((s.strip() for s in reversed(sub_lines) if s.strip()), "")
                        if not _re.match(r'^RETURN\s*$', last_sub, _re.I):
                            issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN，不是 END")
                    current_sub = l.strip()
                    sub_lines = []
                else:
                    sub_lines.append(l)
            # Check last subroutine
            if current_sub and sub_lines:
                last_sub = next((s.strip() for s in reversed(sub_lines) if s.strip()), "")
                if not _re.match(r'^RETURN\s*$', last_sub, _re.I):
                    issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN")
        else:
            last_non_empty = next((l.strip() for l in reversed(lines) if l.strip()), "")
            if not _re.match(r'^END\s*$', last_non_empty, _re.I):
                issues.append("⚠️ 3D 脚本最后一行必须是 END")

    # 2D: must have projection
    if script_type == "2d":
        has_proj = any(
            _re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', l, _re.I)
            for l in lines
        )
        if not has_proj:
            issues.append("⚠️ 2D 脚本缺少平面投影语句（PROJECT2 / RECT2）")

    # _var 未在本脚本内赋值的中间变量（可能需在 Master 脚本中定义）
    assigned = set(_re.findall(r'\b(_[A-Za-z]\w*)\s*=', content))
    used     = set(_re.findall(r'\b(_[A-Za-z]\w*)\b', content))
    undefined = used - assigned
    if undefined:
        issues.append(
            f"ℹ️ 变量 {', '.join(sorted(undefined))} 在本脚本未赋值 — "
            "若已在 Master 脚本中定义可忽略，否则会导致 ArchiCAD 运行时不显示"
        )

    if not issues:
        issues = ["✅ 检查通过"]
    return issues


def _to_float(raw) -> float | None:
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low in {"true", "yes", "on"}:
        return 1.0
    if low in {"false", "no", "off"}:
        return 0.0
    try:
        return float(s)
    except Exception:
        return None


def _preview_param_values(proj: HSFProject) -> dict[str, float]:
    vals = {"A": 1.0, "B": 1.0, "ZZYZX": 1.0}
    for p in proj.parameters:
        v = _to_float(p.value)
        if v is None:
            continue
        vals[p.name.upper()] = v

    for key in ("A", "B", "ZZYZX"):
        if key in vals:
            continue
        gp = proj.get_parameter(key)
        if gp is not None:
            pv = _to_float(gp.value)
            if pv is not None:
                vals[key] = pv

    return vals


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _collect_preview_prechecks(proj: HSFProject, target: str) -> list[str]:
    warns: list[str] = []

    if target in {"2d", "both"}:
        for msg in check_gdl_script(proj.get_script(ScriptType.SCRIPT_2D), "2d"):
            if not msg.startswith("✅"):
                warns.append(f"[check 2D] {msg}")
    if target in {"3d", "both"}:
        for msg in check_gdl_script(proj.get_script(ScriptType.SCRIPT_3D), "3d"):
            if not msg.startswith("✅"):
                warns.append(f"[check 3D] {msg}")

    try:
        v_issues = GDLValidator().validate_all(proj)
        for issue in v_issues:
            if target == "2d" and not issue.startswith(("2d.gdl", "paramlist.xml")):
                continue
            if target == "3d" and not issue.startswith(("3d.gdl", "paramlist.xml")):
                continue
            warns.append(f"[validator] {issue}")
    except Exception as e:
        warns.append(f"[validator] 执行失败: {e}")

    return _dedupe_keep_order(warns)


def _sync_visible_editor_buffers(proj: HSFProject, editor_version: int) -> bool:
    changed = False
    pending_keys = st.session_state.get("_ace_pending_main_editor_keys") or set()
    for stype, fpath, _label in _SCRIPT_MAP:
        current_code = proj.get_script(stype) or ""
        editor_key = _main_editor_state_key(fpath, editor_version)
        if editor_key not in st.session_state:
            continue
        raw_value = st.session_state.get(editor_key)
        if raw_value is None:
            continue
        new_code = raw_value or ""
        if _ACE_AVAILABLE and editor_key in pending_keys and current_code and new_code == "":
            continue
        pending_keys.discard(editor_key)
        if new_code == current_code:
            continue
        proj.set_script(stype, new_code)
        changed = True

    st.session_state._ace_pending_main_editor_keys = pending_keys

    if changed:
        st.session_state.preview_2d_data = None
        st.session_state.preview_3d_data = None
        st.session_state.preview_warnings = []
        st.session_state.preview_meta = {"kind": "", "timestamp": ""}

    return changed


def _render_preview_2d(data: Preview2DResult) -> None:
    if not data:
        st.info("暂无 2D 预览数据。")
        return

    count = len(data.lines) + len(data.polygons) + len(data.circles) + len(data.arcs)
    if count == 0:
        st.info("2D 预览为空（脚本无可渲染几何，或命令未覆盖）。")
        return

    if not _PLOTLY_AVAILABLE:
        st.info("未安装 plotly，无法显示 2D 图形。请安装 ui 依赖后重试。")
        st.caption(f"统计：线段 {len(data.lines)}，多边形 {len(data.polygons)}，圆 {len(data.circles)}，圆弧 {len(data.arcs)}")
        return

    fig = go.Figure()

    for p1, p2 in data.lines:
        fig.add_trace(go.Scatter(
            x=[p1[0], p2[0]],
            y=[p1[1], p2[1]],
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    for poly in data.polygons:
        if len(poly) < 2:
            continue
        xs = [p[0] for p in poly] + [poly[0][0]]
        ys = [p[1] for p in poly] + [poly[0][1]]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            fill="toself",
            fillcolor="rgba(56,189,248,0.15)",
            showlegend=False,
            hoverinfo="skip",
        ))

    for cx, cy, r in data.circles:
        n = 64
        xs = [cx + r * math.cos(2.0 * math.pi * i / n) for i in range(n + 1)]
        ys = [cy + r * math.sin(2.0 * math.pi * i / n) for i in range(n + 1)]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    for cx, cy, r, a0, a1 in data.arcs:
        end = a1
        if end < a0:
            end += 360.0
        n = 48
        xs = [cx + r * math.cos(math.radians(a0 + (end - a0) * i / n)) for i in range(n + 1)]
        ys = [cy + r * math.sin(math.radians(a0 + (end - a0) * i / n)) for i in range(n + 1)]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.update_layout(
        height=420,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        xaxis={"title": "X"},
        yaxis={"title": "Y", "scaleanchor": "x", "scaleratio": 1},
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_preview_3d(data: Preview3DResult) -> None:
    if not data:
        st.info("暂无 3D 预览数据。")
        return

    if not data.meshes and not data.wires:
        st.info("3D 预览为空（脚本无可渲染几何，或命令未覆盖）。")
        return

    if not _PLOTLY_AVAILABLE:
        st.info("未安装 plotly，无法显示 3D 图形。请安装 ui 依赖后重试。")
        st.caption(f"统计：网格 {len(data.meshes)}，线框 {len(data.wires)}")
        return

    fig = go.Figure()

    for i, mesh in enumerate(data.meshes):
        hue = (i * 53) % 360
        fig.add_trace(go.Mesh3d(
            x=mesh.x,
            y=mesh.y,
            z=mesh.z,
            i=mesh.i,
            j=mesh.j,
            k=mesh.k,
            opacity=0.45,
            color=f"hsl({hue},70%,55%)",
            showscale=False,
            name=f"{mesh.name} #{i + 1}",
        ))

    for wire in data.wires:
        if len(wire) < 2:
            continue
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in wire],
            y=[p[1] for p in wire],
            z=[p[2] for p in wire],
            mode="lines",
            line={"width": 4, "color": "rgba(15,23,42,0.85)"},
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.update_layout(
        height=500,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        scene={
            "aspectmode": "data",
            "xaxis": {"title": "X"},
            "yaxis": {"title": "Y"},
            "zaxis": {"title": "Z"},
        },
    )
    st.plotly_chart(fig, use_container_width=True)


def _run_preview(proj: HSFProject, target: str) -> tuple[bool, str]:
    _sync_visible_editor_buffers(proj, int(st.session_state.get("editor_version", 0)))
    params = _preview_param_values(proj)
    pre_warns = _collect_preview_prechecks(proj, target)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if target == "2d":
            res_2d = preview_2d_script(proj.get_script(ScriptType.SCRIPT_2D), parameters=params)
            st.session_state.preview_2d_data = res_2d
            st.session_state.preview_warnings = _dedupe_keep_order([*pre_warns, *res_2d.warnings])
            st.session_state.preview_meta = {"kind": "2D", "timestamp": ts}
            return True, "✅ 2D 预览已更新"

        if target == "3d":
            res_3d = preview_3d_script(proj.get_script(ScriptType.SCRIPT_3D), parameters=params)
            st.session_state.preview_3d_data = res_3d
            st.session_state.preview_warnings = _dedupe_keep_order([*pre_warns, *res_3d.warnings])
            st.session_state.preview_meta = {"kind": "3D", "timestamp": ts}
            return True, "✅ 3D 预览已更新"

        return False, f"❌ 未知预览类型: {target}"

    except Exception as e:
        st.session_state.preview_warnings = _dedupe_keep_order([
            *pre_warns,
            f"[preview] 执行失败: {e}",
        ])
        st.session_state.preview_meta = {"kind": target.upper(), "timestamp": ts}
        return False, f"❌ 预览失败: {e}"


# ══════════════════════════════════════════════════════════
#  Main Layout: Left Chat | Right Editor
# ══════════════════════════════════════════════════════════
#  Layout: Editor (left/main) | AI Chat (right sidebar)
# ══════════════════════════════════════════════════════════

col_left, col_mid, col_right = st.columns([22, 48, 30], gap="small")


# ── Left: Code Editor (always visible) ───────────────────

_SCRIPT_HELP = {
    "scripts/3d.gdl": (
        "**3D 脚本** — 三维几何体定义，ArchiCAD 3D 窗口中显示的实体。\n\n"
        "- 使用 `PRISM_`、`BLOCK`、`SPHERE`、`CONE`、`REVOLVE` 等命令建模\n"
        "- `ADD` / `DEL` 管理坐标系变换，必须成对出现\n"
        "- `FOR` / `NEXT` 循环用于重复构件（如格栅、层板）\n"
        "- **最后一行必须是 `END`**，否则编译失败"
    ),
    "scripts/2d.gdl": (
        "**2D 脚本** — 平面图符号，ArchiCAD 楼层平面图中显示的线条。\n\n"
        "- **必须包含** `PROJECT2 3, 270, 2`（最简投影）或自定义 2D 线条\n"
        "- 不写或留空会导致平面图中对象不可见"
    ),
    "scripts/1d.gdl": (
        "**Master 脚本** — 主控脚本，所有脚本执行前最先运行。\n\n"
        "- 全局变量初始化、参数联动逻辑\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/vl.gdl": (
        "**Param 脚本** — 参数验证脚本，参数值变化时触发。\n\n"
        "- 参数范围约束、派生参数计算\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/ui.gdl": (
        "**UI 脚本** — 自定义参数界面，ArchiCAD 对象设置对话框控件布局。\n\n"
        "- 不写则 ArchiCAD 自动生成默认参数列表界面"
    ),
    "scripts/pr.gdl": (
        "**Properties 脚本** — BIM 属性输出，定义 IFC 属性集和构件属性。\n\n"
        "- 不做 BIM 数据输出可留空"
    ),
}

# ── Shared project/editor state ───────────────────────────
if not st.session_state.project:
    st.session_state.project = HSFProject.create_new(
        "untitled", work_dir=st.session_state.work_dir
    )
    st.session_state.script_revision = 0
proj_now = st.session_state.project
_ev      = st.session_state.editor_version

with col_left:
    with st.container(height=820, border=False):
        tb_import, tb_compile_top = st.columns([1.8, 2.2])

        with tb_import:
            any_upload = st.file_uploader(
                "📂 导入 gdl / txt / gsm", type=["gdl", "txt", "gsm"],
                key="editor_import",
                help=".gdl/.txt → 解析脚本  |  .gsm → LP_XMLConverter 解包",
                disabled=st.session_state.agent_running,
            )
            if any_upload:
                # Dedup: skip if this exact file was already processed this session
                _fkey = f"{any_upload.name}_{any_upload.size}"
                if st.session_state._import_key_done != _fkey:
                    ok, _imp_msg = _handle_unified_import(any_upload)
                    if ok:
                        st.session_state._import_key_done = _fkey
                        st.rerun()
                    else:
                        st.error(_imp_msg)

        with tb_compile_top:
            # GSM name input + compile button stacked in this column
            gsm_name_input = st.text_input(
                "GSM名称", label_visibility="collapsed",
                value=st.session_state.pending_gsm_name or proj_now.name,
                placeholder="输出 GSM 名称（不含扩展名）",
                help="编译输出文件名",
            )
            st.session_state.pending_gsm_name = gsm_name_input
            if st.button("🔧  编  译  GSM", type="primary", width='stretch',
                         help="将当前所有脚本编译为 ArchiCAD .gsm 对象",
                         disabled=st.session_state.agent_running):
                with st.spinner("编译中..."):
                    success, result_msg = do_compile(
                        proj_now,
                        gsm_name=gsm_name_input or proj_now.name,
                        instruction="(toolbar compile)",
                    )
                st.session_state.compile_result = (success, result_msg)
                if success:
                    st.toast("✅ 编译成功", icon="🏗️")
                st.rerun()

        if st.session_state.compile_result is not None:
            _c_ok, _c_msg = st.session_state.compile_result
            if _c_ok:
                st.success(_c_msg)
            else:
                st.error(_c_msg)

        if _TAPIR_IMPORT_OK:
            _bridge = get_bridge()
            _tapir_ok = _bridge.is_available()
            if _tapir_ok:
                _ac_col1, _ac_col2 = st.columns([2, 3])
                with _ac_col1:
                    if st.button("🏗️ 在 Archicad 中测试", width='stretch',
                                 help="触发 Archicad 重新加载库，捕获 GDL 运行期错误回传到 chat"):
                        st.session_state.tapir_test_trigger = True
                        st.rerun()
                with _ac_col2:
                    st.caption("✅ Archicad + Tapir 已连接")

                _p0_b1, _p0_b2, _p0_b3, _p0_b4 = st.columns(4)
                with _p0_b1:
                    if st.button("同步选中", width='stretch'):
                        st.session_state.tapir_selection_trigger = True
                        st.rerun()
                with _p0_b2:
                    if st.button("高亮选中", width='stretch'):
                        st.session_state.tapir_highlight_trigger = True
                        st.rerun()
                with _p0_b3:
                    if st.button("读取参数", width='stretch'):
                        st.session_state.tapir_load_params_trigger = True
                        st.rerun()
                with _p0_b4:
                    _can_apply = bool(st.session_state.get("tapir_selected_params"))
                    if st.button("应用参数", width='stretch', disabled=not _can_apply):
                        st.session_state.tapir_apply_params_trigger = True
                        st.rerun()
            else:
                st.caption("⚪ Archicad 未运行或 Tapir 未安装，跳过实时测试")

        _tb_meta_1, _tb_meta_2, _tb_meta_3 = st.columns([1.2, 1.0, 1.0])

        with _tb_meta_1:
            if st.button("🔍 全检查", width='stretch'):
                _check_all_ok = True
                for _stype, _fpath, _label in _SCRIPT_MAP:
                    _chk_content = proj_now.get_script(_stype)
                    if not _chk_content:
                        continue
                    _skey = _fpath.replace("scripts/", "").replace(".gdl", "")
                    for _iss in check_gdl_script(_chk_content, _skey):
                        if _iss.startswith("✅"):
                            st.success(f"{_label}: {_iss}")
                        else:
                            st.warning(f"{_label}: {_iss}")
                            _check_all_ok = False
                if _check_all_ok:
                    st.success("✅ 所有脚本语法正常")

        with _tb_meta_2:
            if st.button("🗑️ 清空", width='stretch', help="重置项目：脚本、参数、日志全清，保留设置"):
                st.session_state.confirm_clear = True

        with _tb_meta_3:
            if st.button("📋 日志", width='stretch'):
                st.session_state["_show_log_dialog"] = True

        _tb_prev2d, _tb_prev3d = st.columns(2)

        with _tb_prev2d:
            if st.button("👁️ 预览 2D", width='stretch', help="运行 2D 子集解释并显示图形"):
                _ok, _msg = _run_preview(proj_now, "2d")
                if _ok:
                    st.toast(_msg, icon="✅")
                else:
                    st.error(_msg)

        with _tb_prev3d:
            if st.button("🧊 预览 3D", width='stretch', help="运行 3D 子集解释并显示图形"):
                _ok, _msg = _run_preview(proj_now, "3d")
                if _ok:
                    st.toast(_msg, icon="✅")
                else:
                    st.error(_msg)

        @st.dialog("📋 编译日志")
        def _show_log_dialog():
            if not st.session_state.compile_log:
                st.info("暂无编译记录")
            else:
                for _entry in reversed(st.session_state.compile_log):
                    _icon = "✅" if _entry["success"] else "❌"
                    st.markdown(f"**{_icon} {_entry['project']}** — {_entry.get('instruction','')}")
                    st.code(_entry["message"], language="text")
                    st.divider()
            if st.button("清除日志"):
                st.session_state.compile_log = []
                st.session_state.compile_result = None
                st.rerun()

        if st.session_state.get("_show_log_dialog"):
            st.session_state["_show_log_dialog"] = False
            _show_log_dialog()

        if st.session_state.get("confirm_clear"):
            st.warning("⚠️ 将重置项目（脚本、参数、编译日志），聊天记录保留。确认继续？")
            cc1, cc2, _ = st.columns([1, 1, 4])
            with cc1:
                if st.button("✅ 确认清空", type="primary"):
                    _keep_work_dir = st.session_state.work_dir
                    _keep_api_keys = st.session_state.model_api_keys
                    _keep_chat     = st.session_state.chat_history   # preserve chat
                    st.session_state.project          = None
                    st.session_state.compile_log      = []
                    st.session_state.compile_result   = None
                    st.session_state.pending_diffs    = {}
                    st.session_state.pending_ai_label = ""
                    st.session_state.pending_gsm_name = ""
                    st.session_state.script_revision  = 0
                    st.session_state.agent_running    = False
                    st.session_state._import_key_done = ""
                    st.session_state.confirm_clear    = False
                    st.session_state.preview_2d_data  = None
                    st.session_state.preview_3d_data  = None
                    st.session_state.preview_warnings = []
                    st.session_state.preview_meta     = {"kind": "", "timestamp": ""}
                    _reset_tapir_p0_state()
                    _bump_main_editor_version()
                    st.session_state.work_dir         = _keep_work_dir
                    st.session_state.model_api_keys   = _keep_api_keys
                    st.session_state.chat_history     = _keep_chat
                    st.toast("🗑️ 已重置项目（脚本、参数、日志），聊天记录保留", icon="✅")
                    st.rerun()
            with cc2:
                if st.button("❌ 取消"):
                    st.session_state.confirm_clear = False
                    st.rerun()

        st.divider()
        _pm = st.session_state.get("preview_meta") or {}
        _pkind = _pm.get("kind", "")
        _pts = _pm.get("timestamp", "")
        _p_title = f"最新预览：{_pkind} · {_pts}" if _pkind else "预览面板（2D / 3D）"
        st.markdown(f"#### {_p_title}")

        _pv_tab_2d, _pv_tab_3d, _pv_tab_warn = st.tabs(["2D", "3D", "Warnings"])
        with _pv_tab_2d:
            _render_preview_2d(st.session_state.get("preview_2d_data"))
        with _pv_tab_3d:
            _render_preview_3d(st.session_state.get("preview_3d_data"))
        with _pv_tab_warn:
            _warns = st.session_state.get("preview_warnings") or []
            if not _warns:
                st.caption("暂无 warning。")
            else:
                for _w in _warns:
                    st.warning(_w)

with col_mid:
    with st.container(height=820, border=False):
        st.markdown("### GDL 脚本编辑")

        script_tabs = st.tabs([lbl for _, _, lbl in _SCRIPT_MAP])

        for tab, (stype, fpath, label) in zip(script_tabs, _SCRIPT_MAP):
            with tab:
                _tab_help_col, _tab_fs_col = st.columns([6, 1])
                with _tab_help_col:
                    with st.expander(f"ℹ️ {label} 脚本说明"):
                        st.markdown(_SCRIPT_HELP.get(fpath, ""))
                with _tab_fs_col:
                    if st.button("⛶", key=f"fs_{fpath}_v{_ev}",
                                 help="全屏编辑", width='stretch'):
                        _fullscreen_editor_dialog(stype, fpath, label)

                current_code = proj_now.get_script(stype) or ""
                skey = fpath.replace("scripts/", "").replace(".gdl", "")
                editor_key = _main_editor_state_key(fpath, _ev)

                if _ACE_AVAILABLE:
                    _raw_ace = st_ace(
                        value=current_code,
                        language="fortran",   # closest built-in: `!` comments + keyword structure
                        theme="monokai",
                        height=280,
                        font_size=13,
                        tab_size=2,
                        show_gutter=True,
                        show_print_margin=False,
                        wrap=False,
                        key=editor_key,
                    )
                    # 导入/程序化覆盖后，Ace 可能先回传空字符串，再完成 hydration。
                    # 在待 hydration 阶段保留 proj 中的非空脚本，避免预览前被错误清空。
                    pending_keys = st.session_state.get("_ace_pending_main_editor_keys", set())
                    if editor_key in pending_keys and current_code and _raw_ace in (None, ""):
                        new_code = current_code
                    else:
                        if editor_key in pending_keys and (_raw_ace is not None or not current_code):
                            pending_keys.discard(editor_key)
                            st.session_state._ace_pending_main_editor_keys = pending_keys
                        new_code = _raw_ace if _raw_ace is not None else current_code
                else:
                    new_code = st.text_area(
                        label, value=current_code, height=280,
                        key=editor_key, label_visibility="collapsed",
                    ) or ""  # text_area never returns None; empty string is a valid clear

                if new_code != current_code:
                    proj_now.set_script(stype, new_code)
                    st.session_state.preview_2d_data = None
                    st.session_state.preview_3d_data = None
                    st.session_state.preview_warnings = []
                    st.session_state.preview_meta = {"kind": "", "timestamp": ""}

        st.divider()

        with st.expander("ℹ️ 参数说明"):
            st.markdown(
                "**参数列表** — GDL 对象的可调参数。\n\n"
                "- **Type**: `Length` / `Integer` / `Boolean` / `Material` / `String`\n"
                "- **Name**: 代码中引用的变量名（camelCase，如 `iShelves`）\n"
                "- **Value**: 默认值\n"
                "- **Fixed**: 勾选后用户无法在 ArchiCAD 中修改"
            )
        param_data = [
            {"Type": p.type_tag, "Name": p.name, "Value": p.value,
             "Description": p.description, "Fixed": "✓" if p.is_fixed else ""}
            for p in proj_now.parameters
        ]
        if param_data:
            st.dataframe(param_data, width='stretch', hide_index=True)
        else:
            st.caption("暂无参数，通过 AI 对话添加，或手动添加。")

        with st.expander("➕ 手动添加参数"):
            pc1, pc2, pc3, pc4 = st.columns(4)
            with pc1:
                p_type = st.selectbox("Type", [
                    "Length", "Integer", "Boolean", "RealNum", "Angle",
                    "String", "Material", "FillPattern", "LineType", "PenColor",
                ])
            with pc2:
                p_name = st.text_input("Name", value="bNewParam")
            with pc3:
                p_value = st.text_input("Value", value="0")
            with pc4:
                p_desc = st.text_input("Description")
            if st.button("添加参数"):
                try:
                    proj_now.add_parameter(GDLParameter(p_name, p_type, p_desc, p_value))
                    st.success(f"✅ {p_type} {p_name}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        if st.button("🔍 验证参数"):
            issues = validate_paramlist(proj_now.parameters)
            for i in issues:
                st.warning(i)
            if not issues:
                st.success("✅ 参数验证通过")

        with st.expander("paramlist.xml 预览"):
            st.code(build_paramlist_xml(proj_now.parameters), language="xml")

        st.divider()
        st.markdown("#### Tapir P0（Inspector + Parameter Workbench）")
        _tapir_inspector_tab, _tapir_workbench_tab = st.tabs(["Inspector", "Parameter Workbench"])
        with _tapir_inspector_tab:
            _render_tapir_inspector_panel()
        with _tapir_workbench_tab:
            _render_tapir_param_workbench_panel()


# ── Right: AI Chat panel ──────────────────────────────────

with col_right:
    with st.container(height=820, border=False):
        st.markdown("### AI 助手（生成与调试）")

        _chat_title_col, _chat_clear_col = st.columns([3, 1])
        with _chat_title_col:
            st.caption("描述需求，AI 自动创建 GDL 对象写入编辑器")
        with _chat_clear_col:
            if st.button("🗑️ 清空对话", width='stretch', help="清空聊天记录，不影响脚本和参数"):
                st.session_state.chat_history = []
                st.session_state.adopted_msg_index = None
                st.session_state.chat_anchor_focus = None
                st.rerun()

        _anchors = _build_chat_script_anchors(st.session_state.chat_history)
        if _anchors:
            st.caption("🧭 历史锚点（点击快速定位）")
            _anchor_cols = st.columns([1.8, 4.2, 1.2])
            with _anchor_cols[0]:
                _opts = [a["label"] for a in _anchors]
                _default_idx = 0
                _focus = st.session_state.get("chat_anchor_focus")
                if isinstance(_focus, int):
                    for idx, a in enumerate(_anchors):
                        if a["msg_idx"] == _focus:
                            _default_idx = idx
                            break
                _sel = st.selectbox(
                    "历史锚点",
                    _opts,
                    index=_default_idx,
                    label_visibility="collapsed",
                    key="chat_anchor_select",
                )
            _picked = next((a for a in _anchors if a["label"] == _sel), _anchors[-1])
            with _anchor_cols[1]:
                st.caption(f"范围: {', '.join(_picked['paths'])}")
            with _anchor_cols[2]:
                if st.button("📍 定位", width='stretch', key="chat_anchor_go"):
                    st.session_state.chat_anchor_pending = _picked["msg_idx"]

        # Chat history with action bar on each assistant message
        for _i, _msg in enumerate(st.session_state.chat_history):
            _is_focus = st.session_state.get("chat_anchor_focus") == _i
            if _is_focus:
                st.markdown("<div style='border-top:1px dashed #38bdf8;margin:0.4rem 0;'></div>", unsafe_allow_html=True)
                st.caption("📍 当前锚点")
            with st.chat_message(_msg["role"]):
                _msg_content = _msg.get("content", "")
                _has_full_suite = (
                    "scripts/3d.gdl" in _msg_content
                    and "paramlist.xml" in _msg_content
                )
                if st.button(_adopt_label, key=f"adopt_{_i}", width='stretch'):
                                if _has_full_suite:
                                    st.session_state["_pending_adopt_idx"] = _i
                if _msg.get("image_b64"):
                    _img_bytes = _thumb_image_bytes(_msg.get("image_b64", ""))
                    if _img_bytes:
                        st.image(_img_bytes, width=240)
                if _msg["role"] == "assistant":
                    _ca, _cb, _cc, _cd, _ce = st.columns([1, 1, 1, 1, 8])
                    with _ca:
                        if st.button("👍", key=f"like_{_i}", help="有帮助"):
                            _save_feedback(_i, "positive", _msg["content"])
                            st.toast("已记录 👍", icon="✅")
                    with _cb:
                        if st.button("👎", key=f"dislike_{_i}", help="需改进"):
                            st.session_state[f"_show_dislike_{_i}"] = True
                    # 差评描述框
                    if st.session_state.get(f"_show_dislike_{_i}"):
                        with st.container():
                            _fb_text = st.text_area(
                                "描述问题（可选）",
                                key=f"dislike_text_{_i}",
                                placeholder="哪里不对？期望的结果是什么？",
                                height=80,
                                label_visibility="collapsed",
                            )
                            _fb_c1, _fb_c2 = st.columns([1, 1])
                            with _fb_c1:
                                if st.button("📤 提交", key=f"dislike_submit_{_i}", type="primary", width='stretch'):
                                    _save_feedback(_i, "negative", _msg["content"], comment=_fb_text)
                                    st.session_state[f"_show_dislike_{_i}"] = False
                                    st.toast("已记录 👎，感谢反馈", icon="📝")
                                    st.rerun()
                            with _fb_c2:
                                if st.button("取消", key=f"dislike_cancel_{_i}", width='stretch'):
                                    st.session_state[f"_show_dislike_{_i}"] = False
                                    st.rerun()
                    with _cc:
                        if st.button("📋", key=f"copy_{_i}", help="展开可复制内容"):
                            _flag = f"_showcopy_{_i}"
                            st.session_state[_flag] = not st.session_state.get(_flag, False)
                    with _cd:
                        _prev_user = next(
                            (st.session_state.chat_history[j]["content"]
                             for j in range(_i - 1, -1, -1)
                             if st.session_state.chat_history[j]["role"] == "user"),
                            None,
                        )
                        if _prev_user and st.button("🔄", key=f"redo_{_i}", help="重新生成"):
                            st.session_state.chat_history = st.session_state.chat_history[:_i]
                            st.session_state["_redo_input"] = _prev_user
                            st.rerun()
                    with _ce:
                        _has_code = "```" in _msg.get("content", "")
                        if _has_code:
                            _is_adopted = st.session_state.adopted_msg_index == _i
                            _adopt_label = "✅ 已采用" if _is_adopted else "📥 采用这套"
                            if st.button(_adopt_label, key=f"adopt_{_i}", width='stretch'):
                                

                                    st.session_state["_pending_adopt_idx"] = _i
            if st.session_state.get(f"_showcopy_{_i}", False):
                st.code(_msg["content"], language="text")

        @st.dialog("📥 采用这套代码")
        def _adopt_confirm_dialog(msg_idx):
            st.warning("将按返回文件覆盖：命中的脚本/参数全覆盖写入，未命中的部分保留不变，确认？")
            _da, _db = st.columns(2)
            with _da:
                if st.button("✅ 确认覆盖", type="primary", width='stretch'):
                    _msg_content = st.session_state.chat_history[msg_idx]["content"]
                    extracted = _extract_gdl_from_text(_msg_content)
                    if extracted:
                        # 只覆盖此消息中实际包含的脚本/参数，其余保留
                        if st.session_state.project:
                            _apply_scripts_to_project(st.session_state.project, extracted)
                        _bump_main_editor_version()
                        st.session_state.adopted_msg_index = msg_idx
                        st.session_state["_pending_adopt_idx"] = None
                        st.toast("✅ 已写入编辑器", icon="📥")
                        st.rerun()
                    else:
                        st.error("未找到可提取的代码块")
            with _db:
                if st.button("❌ 取消", width='stretch'):
                    st.session_state["_pending_adopt_idx"] = None
                    st.rerun()

        if st.session_state.get("_pending_adopt_idx") is not None:
            _adopt_confirm_dialog(st.session_state["_pending_adopt_idx"])

        if st.session_state.pending_diffs:
            _pd = st.session_state.pending_diffs
            _pn_s = sum(1 for k in _pd if k.startswith("scripts/"))
            _pn_p = len(_parse_paramlist_text(_pd.get("paramlist.xml", "")))
            _pd_parts = []
            if _pn_s: _pd_parts.append(f"{_pn_s} 个脚本")
            if _pn_p: _pd_parts.append(f"{_pn_p} 个参数")
            _pd_label = "、".join(_pd_parts) or st.session_state.pending_ai_label or "新内容"

            _covered = sorted([k for k in _pd.keys() if k.startswith("scripts/") or k == "paramlist.xml"])
            _all_targets = [p for _, p, _ in _SCRIPT_MAP] + ["paramlist.xml"]
            _kept = [p for p in _all_targets if p not in _covered]
            _covered_txt = "、".join(_covered) if _covered else "（无）"
            _kept_txt = "、".join(_kept) if _kept else "（无）"
            st.info(
                f"⬆️ **写入策略：命中文件全覆盖，未命中文件保留**\n"
                f"覆盖：`{_covered_txt}`\n"
                f"保留：`{_kept_txt}`"
            )
            _pac1, _pac2, _pac3 = st.columns([1.2, 1, 5])
            with _pac1:
                if st.button("✅ 写入", type="primary", width='stretch',
                             key="chat_pending_apply"):
                    _proj = st.session_state.project
                    if _proj:
                        sc, pc = _apply_scripts_to_project(_proj, _pd)
                        _ok_parts = []
                        if sc: _ok_parts.append(f"{sc} 个脚本")
                        if pc: _ok_parts.append(f"{pc} 个参数")
                        _bump_main_editor_version()
                        st.toast(f"✅ 已写入 {'、'.join(_ok_parts)}", icon="✏️")
                    st.session_state.pending_diffs    = {}
                    st.session_state.pending_ai_label = ""
                    st.rerun()
            with _pac2:
                if st.button("❌ 忽略", width='stretch',
                             key="chat_pending_discard"):
                    st.session_state.pending_diffs    = {}
                    st.session_state.pending_ai_label = ""
                    st.rerun()

        # Live agent output placeholder (anchored inside this column)
        live_output = st.empty()

        _dbg_active = st.session_state.get("_debug_mode_active") == "editor"
        _dbg_label = "✖ 退出 Debug" if _dbg_active else "🔍 开启 Debug 编辑器"
        if st.button(
            _dbg_label,
            width='stretch',
            type=("primary" if _dbg_active else "secondary"),
            key="debug_editor_toggle_btn",
            help="开启后：下次发送将附带编辑器全部脚本+参数+语法检查报告",
        ):
            _dbg_active = not _dbg_active
            st.session_state["_debug_mode_active"] = "editor" if _dbg_active else None

        _cur_dbg = "editor" if _dbg_active else None

        if _cur_dbg == "editor":
            st.info("🔍 **全脚本 Debug 已激活** — 描述你观察到的问题，或直接发送让 AI 全面检查语法和逻辑")

        st.caption("📎 图片路由（仅附图消息生效）")
        st.radio(
            "图片路由",
            ["自动", "强制生成", "强制调试"],
            horizontal=True,
            key="chat_image_route_mode",
            label_visibility="collapsed",
        )

        _chat_placeholder = "描述需求、提问，或搭配图片补充说明…"
        if st.session_state.agent_running:
            st.info("⏳ AI 生成中，请稍候...")
        _chat_payload = st.chat_input(
            _chat_placeholder,
            key="chat_main_input",
            accept_file=True,
            file_type=["jpg", "jpeg", "png", "webp", "gif"],
            disabled=st.session_state.agent_running,
        )

        user_input = None
        _vision_b64 = None
        _vision_mime = None
        _vision_name = None

        if isinstance(_chat_payload, str):
            user_input = _chat_payload
        elif _chat_payload is not None:
            user_input = _chat_payload.get("text", "") or ""
            _chat_files = _chat_payload.get("files", []) or []
            if _chat_files:
                _img = _chat_files[0]
                _raw_bytes = _img.read()
                if _raw_bytes:
                    _vision_b64 = base64.b64encode(_raw_bytes).decode()
                    _vision_mime = getattr(_img, "type", "") or "image/jpeg"
                    _vision_name = getattr(_img, "name", "") or "image"

    # ══════════════════════════════════════════════════════════
    #  Chat handler (outside columns — session state + rerun)
    # ══════════════════════════════════════════════════════════

    _redo_input                = st.session_state.pop("_redo_input", None)
    _active_dbg                = st.session_state.get("_debug_mode_active")
    _tapir_trigger             = st.session_state.pop("tapir_test_trigger", False)
    _tapir_selection_trigger   = st.session_state.pop("tapir_selection_trigger", False)
    _tapir_highlight_trigger   = st.session_state.pop("tapir_highlight_trigger", False)
    _tapir_load_params_trigger = st.session_state.pop("tapir_load_params_trigger", False)
    _tapir_apply_params_trigger = st.session_state.pop("tapir_apply_params_trigger", False)
    _has_image_input           = bool(_vision_b64)

    # 历史锚点定位：延迟到页面末尾执行，避免打断当前LLM调用
    _anchor_pending = st.session_state.pop("chat_anchor_pending", None)


    # ── Archicad 测试：ReloadLibraries + 捕获错误注入 chat ──
    if _tapir_trigger and _TAPIR_IMPORT_OK:
        _bridge = get_bridge()
        _proj_for_tapir = st.session_state.project
        with st.spinner("🏗️ 触发 Archicad 重新加载库，等待渲染..."):
            _reload_ok, _gdl_errors = _bridge.reload_and_capture(
                timeout=6.0,
                project=_proj_for_tapir,
            )
        if _reload_ok:
            _error_msg = errors_to_chat_message(_gdl_errors)
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": _error_msg,
            })
            if _gdl_errors:
                # 自动触发debug：把错误作为context发给LLM
                _auto_debug = f"[DEBUG:editor] 请根据以上 Archicad 报错修复脚本"
                st.session_state.chat_history.append({
                    "role": "user",
                    "content": _auto_debug,
                })
                st.session_state["_auto_debug_input"] = _auto_debug
            st.rerun()
        else:
            st.toast("❌ Archicad 连接失败，请确认 Archicad 正在运行", icon="⚠️")

    if _tapir_selection_trigger and _TAPIR_IMPORT_OK:
        _ok, _msg = _tapir_sync_selection()
        if _ok:
            if st.session_state.get("tapir_selected_guids"):
                st.toast(f"✅ {_msg}", icon="🧭")
            else:
                st.warning("未选中对象")
        else:
            st.error(f"❌ {_msg}")
        st.rerun()

    if _tapir_highlight_trigger and _TAPIR_IMPORT_OK:
        _ok, _msg = _tapir_highlight_selection()
        if _ok:
            st.toast(f"✅ {_msg}", icon="🎯")
        else:
            st.error(f"❌ {_msg}")
        st.rerun()

    if _tapir_load_params_trigger and _TAPIR_IMPORT_OK:
        _ok, _msg = _tapir_load_selected_params()
        if _ok:
            if st.session_state.get("tapir_last_error"):
                st.warning(st.session_state.tapir_last_error)
            st.toast(f"✅ {_msg}", icon="📥")
        else:
            st.error(f"❌ {_msg}")
        st.rerun()

    if _tapir_apply_params_trigger and _TAPIR_IMPORT_OK:
        _ok, _msg = _tapir_apply_param_edits()
        if _ok:
            st.toast(f"✅ {_msg}", icon="📤")
        else:
            st.error(f"❌ {_msg}")
        st.rerun()

    _auto_debug_input = st.session_state.pop("_auto_debug_input", None)

    # Debug模式：仅用户主动发送时触发，不自动构造空输入消息
    if _active_dbg and user_input:
        _dbg_prefix = f"[DEBUG:{_active_dbg}]"
        effective_input = f"{_dbg_prefix} {user_input.strip()}"
        st.session_state["_debug_mode_active"] = None
    elif _active_dbg and user_input == "" and not _has_image_input:
        st.toast("请输入问题描述后再发送，或直接描述你看到的现象", icon="💬")
        effective_input = _auto_debug_input or _redo_input
    else:
        effective_input = _auto_debug_input or _redo_input or user_input

    # 在用户消息中提取物件名作为 GSM 名称候选（仅当当前为空）
    if user_input and not (st.session_state.pending_gsm_name or "").strip():
        _gsm_candidate = _extract_gsm_name_candidate(user_input)
        if _gsm_candidate:
            st.session_state.pending_gsm_name = _gsm_candidate

    # ── Vision path: attachment on chat_input ────────────────────────────────────
    if _has_image_input:
        _vision_mime = _vision_mime or "image/jpeg"
        _vision_name = _vision_name or "image"
        _extra_text = (user_input or "").strip()
        _joined_text = _extra_text

        _route_pick = st.session_state.get("chat_image_route_mode", "自动")
        if _route_pick == "强制调试":
            _route_mode = "debug"
        elif _route_pick == "强制生成":
            _route_mode = "generate"
        else:
            _route_mode = "debug" if _active_dbg else _detect_image_task_mode(_joined_text, _vision_name)
        _route_tag = "🧩 Debug" if _route_mode == "debug" else "🧱 生成"
        _user_display = f"🖼️ `{_vision_name}` · {_route_tag}" + (f"  \n{_joined_text}" if _joined_text else "")
        st.session_state.chat_history.append({
            "role": "user",
            "content": _user_display,
            "image_b64": _vision_b64,
            "image_mime": _vision_mime,
            "image_name": _vision_name,
        })

        if not api_key and "ollama" not in model_name:
            err = "❌ 请在左侧边栏填入 API Key 后再试。"
            st.session_state.chat_history.append({"role": "assistant", "content": err})
            st.rerun()
        else:
            try:
                st.session_state.agent_running = True
                # Ensure project exists
                if not st.session_state.project:
                    _vname = Path(_vision_name).stem or "vision_object"
                    _vproj = HSFProject.create_new(_vname, work_dir=st.session_state.work_dir)
                    st.session_state.project = _vproj
                    st.session_state.pending_gsm_name = _vname
                    st.session_state.script_revision = 0

                _proj_v = st.session_state.project
                _has_any_v = any(_proj_v.get_script(s) for s, _, _ in _SCRIPT_MAP)

                with live_output.container():
                    st.chat_message("user").markdown(_user_display)
                    _img_bytes = _thumb_image_bytes(_vision_b64)
                    if _img_bytes:
                        st.image(_img_bytes, width=240)
                    with st.chat_message("assistant"):
                        if _route_mode == "generate":
                            msg = run_vision_generate(
                                image_b64=_vision_b64,
                                image_mime=_vision_mime,
                                extra_text=_joined_text,
                                proj=_proj_v,
                                status_col=st.container(),
                                auto_apply=not _has_any_v,
                            )
                        else:
                            _debug_req = _joined_text or "请根据这张截图定位并修复当前项目中的问题。"
                            if not _debug_req.startswith("[DEBUG:"):
                                _debug_req = f"[DEBUG:editor] {_debug_req}"
                            msg = run_agent_generate(
                                _debug_req,
                                _proj_v,
                                st.container(),
                                gsm_name=(st.session_state.pending_gsm_name or _proj_v.name),
                                auto_apply=not _has_any_v,
                                debug_image_b64=_vision_b64,
                                debug_image_mime=_vision_mime,
                            )
                        st.markdown(msg)

                st.session_state.chat_history.append({"role": "assistant", "content": msg})
                st.rerun()
            finally:
                st.session_state.agent_running = False

    # ── Normal text path ─────────────────────────────────────────────────────────
    elif effective_input:
        # Redo: user msg already in history; new: append it
        if not _redo_input:
            st.session_state.chat_history.append({"role": "user", "content": effective_input})
        user_input = effective_input   # alias for rest of handler

        if not api_key and "ollama" not in model_name:
            err = "❌ 请在左侧边栏填入 API Key 后再试。"
            st.session_state.chat_history.append({"role": "assistant", "content": err})
            st.rerun()
        else:
            try:
                st.session_state.agent_running = True
                llm_for_classify = get_llm()
                intent, gdl_obj_name = classify_and_extract(
                    user_input, llm_for_classify,
                    project_loaded=bool(st.session_state.project),
                )

                with live_output.container():
                    st.chat_message("user").markdown(user_input)
                    with st.chat_message("assistant"):
                        if intent == "CHAT":
                            msg = chat_respond(
                                user_input,
                                st.session_state.chat_history[:-1],
                                llm_for_classify,
                            )
                            st.markdown(msg)
                        else:
                            if not st.session_state.project:
                                new_proj = HSFProject.create_new(gdl_obj_name, work_dir=st.session_state.work_dir)
                                st.session_state.project = new_proj
                                st.session_state.pending_gsm_name = gdl_obj_name
                                st.session_state.script_revision = 0
                                st.info(f"📁 已初始化项目 `{gdl_obj_name}`")

                            proj_current = st.session_state.project
                            # 只有全新空项目（无任何脚本内容）才自动写入；
                            # 已有脚本的项目修改时显示确认按钮，防止意外覆盖。
                            _has_any_script = any(
                                proj_current.get_script(s) for s, _, _ in _SCRIPT_MAP
                            )
                            effective_gsm = st.session_state.pending_gsm_name or proj_current.name
                            msg = run_agent_generate(
                                user_input, proj_current, st.container(),
                                gsm_name=effective_gsm,
                                auto_apply=not _has_any_script,
                            )
                            st.markdown(msg)

                st.session_state.chat_history.append({"role": "assistant", "content": msg})
                st.rerun()
            finally:
                st.session_state.agent_running = False


    # 锚点定位在页面末尾触发 rerun，尽量不打断当前生成流程
    if _anchor_pending is not None:
        st.session_state.chat_anchor_focus = _anchor_pending
        try:
            _loop = asyncio.get_running_loop()
            _loop.call_soon(st.rerun)
        except RuntimeError:
            st.rerun()

    # ── Footer ────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<p style="text-align:center; color:#64748b;">'
        f'OpenBrep v{OPENBREP_VERSION} · HSF-native · Code Your Boundaries ·'
        '<a href="https://github.com/byewind1/openbrep">GitHub</a>'
        '</p>',
        unsafe_allow_html=True,
    )
