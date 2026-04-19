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
import logging
import json
import csv
import hashlib
import hmac
import string
import zipfile
import shutil
import subprocess
import uuid
import binascii
from copy import deepcopy
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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
from openbrep.paramlist_builder import build_paramlist_xml, validate_paramlist
from openbrep.compiler import MockHSFCompiler, HSFCompiler, CompileResult
from openbrep.gdl_previewer import Preview2DResult, Preview3DResult, preview_2d_script, preview_3d_script
from openbrep.validator import GDLValidator
from openbrep.knowledge import KnowledgeBase
try:
    from openbrep.config import ALL_MODELS, VISION_MODELS, REASONING_MODELS, model_to_provider, GDLAgentConfig, iter_custom_provider_model_entries
    _MODEL_CONSTANTS_OK = True
except ImportError:
    ALL_MODELS = []
    VISION_MODELS = set()
    REASONING_MODELS = set()
    _MODEL_CONSTANTS_OK = False
from openbrep.elicitation_agent import ElicitationAgent, ElicitationState
from openbrep.skills_loader import SkillsLoader
from openbrep import __version__ as OPENBREP_VERSION
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, build_generation_result_plan
from openbrep.runtime.router import IntentRouter
from ui import actions as ui_actions
from ui import state as ui_state
from ui import view_models as ui_view_models
from ui import preview_controller as ui_preview_controller
from ui import project_io as ui_project_io
from ui import chat_controller as ui_chat_controller
from ui import tapir_controller as ui_tapir_controller
from ui import tapir_views as ui_tapir_views

logger = logging.getLogger(__name__)
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024

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
if "generation_status" not in st.session_state:
    st.session_state.generation_status = "idle"
if "active_generation_id" not in st.session_state:
    st.session_state.active_generation_id = None
if "generation_cancel_requested" not in st.session_state:
    st.session_state.generation_cancel_requested = False
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
if "last_project_snapshot" not in st.session_state:
    st.session_state.last_project_snapshot = None
if "last_project_snapshot_meta" not in st.session_state:
    st.session_state.last_project_snapshot_meta = {}
if "last_project_snapshot_label" not in st.session_state:
    st.session_state.last_project_snapshot_label = ""
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
if "assistant_settings" not in st.session_state:
    st.session_state.assistant_settings = ""
if "elicitation_agent" not in st.session_state:
    st.session_state.elicitation_agent = None
if "elicitation_state" not in st.session_state:
    st.session_state.elicitation_state = ElicitationState.IDLE.value



def _new_generation_id() -> str:
    return ui_state.new_generation_id()



def _begin_generation_state(state) -> str:
    return ui_state.begin_generation_state(state)



def _request_generation_cancel(state, generation_id: str) -> bool:
    return ui_state.request_generation_cancel(state, generation_id)



def _is_generation_locked(state) -> bool:
    return ui_state.is_generation_locked(state)



def _is_active_generation(state, generation_id: str) -> bool:
    return ui_state.is_active_generation(state, generation_id)



def _should_accept_generation_result(state, generation_id: str) -> bool:
    return ui_state.should_accept_generation_result(state, generation_id)



def _finish_generation_state(state, generation_id: str, status: str) -> bool:
    return ui_state.finish_generation_state(state, generation_id, status)



def _generation_stop_label() -> str:
    return "停止生成中..." if st.session_state.get("generation_status") == "cancelling" else "停止生成"



def _render_generation_controls() -> None:
    if not _is_generation_locked(st.session_state):
        return
    generation_id = st.session_state.get("active_generation_id")
    st.warning("AI 正在生成中。涉及工程状态的操作已临时锁定。")
    if st.button(_generation_stop_label(), key="stop_generation", width='stretch'):
        if _request_generation_cancel(st.session_state, generation_id):
            st.info("已请求停止当前生成，正在等待本轮调用安全结束。")
            st.rerun()



def _guarded_event_update(status_ph, generation_id: str, method_name: str, message: str) -> None:
    if not _is_active_generation(st.session_state, generation_id):
        return
    getattr(status_ph, method_name)(message)



def _generation_cancelled_message() -> str:
    return "⏹️ 本轮生成已取消，未写入编辑器。"



def _consume_generation_result(generation_id: str) -> bool:
    return _should_accept_generation_result(st.session_state, generation_id)



def _capture_last_project_snapshot(label: str) -> None:
    proj = st.session_state.get("project")
    if proj is None:
        return
    st.session_state.last_project_snapshot = {
        "project": deepcopy(proj),
        "pending_gsm_name": st.session_state.get("pending_gsm_name", ""),
        "script_revision": int(st.session_state.get("script_revision", 0)),
    }
    st.session_state.last_project_snapshot_label = label
    st.session_state.last_project_snapshot_meta = {
        "label": label,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def _restore_last_project_snapshot() -> tuple[bool, str]:
    snap = st.session_state.get("last_project_snapshot")
    if not snap:
        return (False, "❌ 没有可恢复的上一次 AI 写入")

    st.session_state.project = deepcopy(snap["project"])
    st.session_state.pending_gsm_name = snap.get("pending_gsm_name", "")
    st.session_state.script_revision = int(snap.get("script_revision", 0))
    st.session_state.pending_diffs = {}
    st.session_state.pending_ai_label = ""
    st.session_state.preview_2d_data = None
    st.session_state.preview_3d_data = None
    st.session_state.preview_warnings = []
    st.session_state.preview_meta = {"kind": "", "timestamp": ""}
    _bump_main_editor_version()
    label = st.session_state.get("last_project_snapshot_label") or "AI 写入"
    st.session_state.last_project_snapshot = None
    st.session_state.last_project_snapshot_meta = {}
    st.session_state.last_project_snapshot_label = ""
    return (True, f"✅ 已撤销上次 {label}")


def _finalize_generation(generation_id: str, status: str) -> bool:
    return _finish_generation_state(st.session_state, generation_id, status)



def _apply_generation_plan(plan, proj: HSFProject, gsm_name: str | None, already_applied: bool = False) -> tuple[str, list[str]]:
    return ui_actions.apply_generation_plan(
        plan,
        proj,
        gsm_name,
        st.session_state,
        _capture_last_project_snapshot,
        _apply_scripts_to_project,
        _bump_main_editor_version,
        already_applied=already_applied,
    )



def _apply_generation_result(cleaned: dict, proj: HSFProject, gsm_name: str | None, auto_apply: bool, already_applied: bool = False) -> tuple[str, list[str]]:
    return ui_actions.apply_generation_result(
        cleaned,
        proj,
        gsm_name,
        auto_apply,
        st.session_state,
        _capture_last_project_snapshot,
        _apply_scripts_to_project,
        _bump_main_editor_version,
        already_applied=already_applied,
    )



def _build_generation_reply(plain_text: str, result_prefix: str = "", code_blocks: list[str] | None = None) -> str:
    return ui_view_models.build_generation_reply(plain_text, result_prefix, code_blocks)



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


def _empty_license_record() -> dict:
    return ui_view_models.empty_license_record()


def _load_license(work_dir: str) -> dict:
    fp = _license_file(work_dir)
    if not fp.exists():
        return _empty_license_record()
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _empty_license_record()


def _save_license(work_dir: str, data: dict) -> None:
    fp = _license_file(work_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_pro_public_key(root: Path):
    key_file = root / "openbrep" / "public_keys" / "pro_public.pem"
    if not key_file.exists():
        raise FileNotFoundError(f"缺少公钥文件：{key_file}")
    return serialization.load_pem_public_key(key_file.read_bytes())


def _urlsafe_b64decode(data: str) -> bytes:
    return ui_view_models.urlsafe_b64decode(data)



def _urlsafe_b64encode(data: bytes) -> str:
    return ui_view_models.urlsafe_b64encode(data)



def _canonical_license_payload(payload: dict) -> bytes:
    return ui_view_models.canonical_license_payload(payload)



def _normalize_license_record(payload: dict, signature_b64: str) -> dict:
    return ui_view_models.normalize_license_record(payload, signature_b64)


def _verify_license_payload(payload: dict, signature_b64: str) -> tuple[bool, str, dict | None]:
    expire_date = str(payload.get("expire_date", "")).strip()
    if expire_date:
        try:
            if datetime.now().date() > datetime.strptime(expire_date, "%Y-%m-%d").date():
                return False, "授权码已过期", None
        except ValueError:
            return False, "授权日期格式无效", None

    try:
        public_key = _load_pro_public_key(Path(__file__).parent.parent)
        signature = _urlsafe_b64decode(signature_b64)
        public_key.verify(
            signature,
            _canonical_license_payload(payload),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except FileNotFoundError as e:
        return False, str(e), None
    except (ValueError, binascii.Error):
        return False, "授权码格式错误", None
    except InvalidSignature:
        return False, "授权签名无效", None
    except Exception as e:
        return False, f"授权校验失败: {e}", None

    return True, "授权码有效", _normalize_license_record(payload, signature_b64)


def _decode_signed_license_code(code: str) -> tuple[bool, str, dict | None]:
    return ui_view_models.decode_signed_license_code(
        code,
        verify_license_payload=_verify_license_payload,
    )



def _verify_pro_code(code: str) -> tuple[bool, str, dict | None]:
    return ui_view_models.verify_pro_code(
        code,
        decode_signed_license_code_fn=_decode_signed_license_code,
    )



def _license_record_is_active(data: dict) -> tuple[bool, str, dict | None]:
    return ui_view_models.license_record_is_active(
        data,
        verify_license_payload=_verify_license_payload,
    )


def _verify_pro_package(unpacked_dir: Path) -> tuple[bool, str, dict | None]:
    manifest_path = unpacked_dir / "manifest.json"
    signature_path = unpacked_dir / "signature.sig"
    docs_dir = unpacked_dir / "docs"

    if not manifest_path.exists() or not signature_path.exists():
        return False, "知识包缺少 manifest.json 或 signature.sig", None
    if not docs_dir.exists() or not docs_dir.is_dir():
        return False, "知识包缺少 docs 目录", None

    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        return False, "知识包 manifest 无法解析", None

    if not isinstance(manifest, dict):
        return False, "知识包 manifest 格式错误", None

    required_fields = ["buyer_id", "plan", "issued_at"]
    missing = [field for field in required_fields if not str(manifest.get(field, "")).strip()]
    if missing:
        return False, f"知识包 manifest 缺少字段：{', '.join(missing)}", None

    try:
        public_key = _load_pro_public_key(Path(__file__).parent.parent)
        public_key.verify(
            signature_path.read_bytes(),
            manifest_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except FileNotFoundError as e:
        return False, str(e), None
    except InvalidSignature:
        return False, "知识包签名无效", None
    except Exception as e:
        return False, f"知识包验签失败：{e}", None

    expire_date = str(manifest.get("expire_date", "")).strip()
    if expire_date:
        try:
            if datetime.now().date() > datetime.strptime(expire_date, "%Y-%m-%d").date():
                return False, "知识包已过期", None
        except ValueError:
            return False, "知识包日期格式无效", None

    return True, "知识包验签通过", manifest


def _license_matches_package(license_record: dict, package_manifest: dict) -> tuple[bool, str]:
    return ui_view_models.license_matches_package(license_record, package_manifest)


def _import_pro_knowledge_zip(file_bytes: bytes, filename: str, work_dir: str) -> tuple[bool, str]:
    if not filename.lower().endswith((".zip", ".obrk")):
        return False, "仅支持 .zip 或 .obrk 知识包"

    license_record = _load_license(work_dir)
    if not bool(license_record.get("pro_unlocked", False)):
        return False, "请先激活 Pro 后再导入知识包"

    ok, msg, normalized_license = _license_record_is_active(license_record)
    if not ok or normalized_license is None:
        _save_license(work_dir, _empty_license_record())
        return False, f"请先重新激活 Pro：{msg}"

    _save_license(work_dir, normalized_license)

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
        ok, msg, manifest = _verify_pro_package(unpacked)
        if not ok or manifest is None:
            return False, f"❌ 导入失败：{msg}"

        ok, msg = _license_matches_package(normalized_license, manifest)
        if not ok:
            return False, f"❌ 导入失败：{msg}"

        docs_dir = unpacked / "docs"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        for item in docs_dir.iterdir():
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

_config = None
_config_defaults = {}
_provider_keys: dict = {}   # {provider: api_key}
_custom_providers: list = []  # [{base_url, models, api_key, protocol, name}]


def _get_reloadable_model_list() -> list[str]:
    models: list[str] = []
    if _config is not None:
        return [str(model) for model in _config.get_available_models()]

    for provider in _custom_providers:
        for model in provider.get("models", []) or []:
            model_str = str(model)
            if model_str not in models:
                models.append(model_str)

    for model in ALL_MODELS:
        model_str = str(model)
        if model_str not in models:
            models.append(model_str)

    return models


def _reload_config_globals(update_session_state: bool = False) -> None:
    global _config, _config_defaults, _provider_keys, _custom_providers

    from openbrep.config import GDLAgentConfig
    import sys as _sys, os as _os
    if _sys.version_info >= (3, 11):
        import tomllib as _tomllib
    else:
        import tomli as _tomllib   # type: ignore

    _config = None
    _config_defaults = {}
    _provider_keys = {}
    _custom_providers = []

    _toml_path = _os.path.join(_os.path.dirname(__file__), "..", "config.toml")
    if _os.path.exists(_toml_path):
        with open(_toml_path, "rb") as _f:
            _raw = _tomllib.load(_f)
        _llm_raw = _raw.get("llm", {})
        _provider_keys = _llm_raw.get("provider_keys", {})
        _custom_providers = _llm_raw.get("custom_providers", []) or []

    _config = GDLAgentConfig.load()
    _provider_keys = dict(_config.llm.provider_keys or _provider_keys)
    _custom_providers = list(_config.llm.custom_providers or _custom_providers)
    _config_defaults = {
        "llm_model": _config.llm.model,
        "compiler_path": _config.compiler.path or "",
        "assistant_settings": _config.llm.assistant_settings or "",
    }

    if not update_session_state:
        return

    existing_model_keys = dict(st.session_state.get("model_api_keys", {}))
    refreshed_model_keys = dict(existing_model_keys)
    for model in _get_reloadable_model_list():
        refreshed_model_keys[model] = _key_for_model(model) or refreshed_model_keys.get(model, "")

    st.session_state.model_api_keys = refreshed_model_keys
    st.session_state.assistant_settings = _config_defaults.get(
        "assistant_settings",
        st.session_state.get("assistant_settings", ""),
    )


try:
    _reload_config_globals()
except Exception:
    pass


def _key_for_model(model: str) -> str:
    """Pick the right API Key from provider_keys based on model name."""
    m = model.lower()

    # 自定义 provider 的模型精确匹配
    for _pcfg in _custom_providers:
        for _m in _pcfg.get("models", []) or []:
            if m == str(_m).lower():
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


def _build_assistant_settings_prompt(text: str) -> str:
    return ui_view_models.build_assistant_settings_prompt(text)



def _should_persist_assistant_settings(config_value: str, ui_value: str) -> bool:
    return ui_view_models.should_persist_assistant_settings(config_value, ui_value)


def _build_model_options(available_models: list[str], custom_providers: list[dict]) -> list[dict]:
    return ui_view_models.build_model_options(
        available_models,
        custom_providers,
        vision_models=VISION_MODELS,
        reasoning_models=REASONING_MODELS,
    )



def _resolve_selected_model(selected_label: str, options: list[dict]) -> str:
    return ui_view_models.resolve_selected_model(selected_label, options)



def _collect_custom_model_aliases(custom_providers: list[dict]) -> list[str]:
    return ui_view_models.collect_custom_model_aliases(
        custom_providers,
        iter_entries=iter_custom_provider_model_entries,
    )



def _build_custom_model_options(custom_providers: list[dict]) -> list[dict]:
    return ui_view_models.build_custom_model_options(
        custom_providers,
        iter_entries=iter_custom_provider_model_entries,
    )



def _build_model_source_state(
    builtin_models: list[str],
    custom_providers: list[dict],
    saved_model: str,
) -> dict:
    return ui_view_models.build_model_source_state(
        builtin_models,
        custom_providers,
        saved_model,
        iter_entries=iter_custom_provider_model_entries,
        vision_models=VISION_MODELS,
        reasoning_models=REASONING_MODELS,
    )



with st.sidebar:
    if not st.session_state.assistant_settings:
        st.session_state.assistant_settings = _config_defaults.get("assistant_settings", "")
    if _TAPIR_IMPORT_OK and not _is_archicad_running():
        st.sidebar.warning("⚠️ Archicad 未运行，编译和实时预览不可用")

    st.markdown('<p class="main-header">OpenBrep</p>', unsafe_allow_html=True)
    st.markdown('<p class="intro-header">用自然语言驱动 ArchiCAD GDL 库对象的创建、修改与编译。</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="sub-header">OpenBrep: Code Your Boundaries · v{OPENBREP_VERSION} · HSF-native</p>', unsafe_allow_html=True)
    _render_generation_controls()
    st.divider()

    st.subheader("📁 工作目录")
    work_dir = st.text_input("Work Directory", value=st.session_state.work_dir, label_visibility="collapsed", disabled=_is_generation_locked(st.session_state))
    st.session_state.work_dir = work_dir

    # Load persisted license when work_dir is known
    if not st.session_state.pro_license_loaded:
        _lic = _load_license(work_dir)
        if bool(_lic.get("pro_unlocked", False)):
            ok, _msg, normalized = _license_record_is_active(_lic)
            if ok and normalized is not None:
                st.session_state.pro_unlocked = True
                _save_license(work_dir, normalized)
            else:
                st.session_state.pro_unlocked = False
                _save_license(work_dir, _empty_license_record())
        else:
            st.session_state.pro_unlocked = False
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
            ok, msg, record = _verify_pro_code(pro_code_input)
            if ok and record is not None:
                st.session_state.pro_unlocked = True
                _save_license(work_dir, record)
                st.success("✅ Pro 解锁成功")
                st.rerun()
            else:
                st.error(msg)
    with c2:
        if st.button("锁回 Free", width='stretch'):
            st.session_state.pro_unlocked = False
            _save_license(work_dir, _empty_license_record())
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

    _reload_col, _status_col = st.columns([1, 2])
    with _reload_col:
        if st.button("重新加载配置", width='stretch', disabled=_is_generation_locked(st.session_state)):
            try:
                _reload_config_globals(update_session_state=True)
                st.session_state["current_model"] = _config_defaults.get("llm_model", "")
                st.session_state["reload_config_notice"] = "✅ 已从磁盘重新加载 config.toml"
                st.rerun()
            except Exception as e:
                st.warning(f"配置重载失败：{e}")
    with _status_col:
        _reload_notice = st.session_state.pop("reload_config_notice", "")
        if _reload_notice:
            st.caption(_reload_notice)

    _custom_list = _config.llm.custom_providers if _config else _custom_providers
    _model_state = _build_model_source_state(
        builtin_models=ALL_MODELS,
        custom_providers=_custom_list,
        saved_model=_config_defaults.get("llm_model", "glm-4-flash"),
    )

    _source_options = _model_state["source_options"]
    _default_source = _model_state["default_source"]
    _default_source_index = _source_options.index(_default_source) if _default_source in _source_options else 0
    _selected_source = st.selectbox(
        "来源 / Source",
        _source_options,
        index=_default_source_index,
        disabled=_is_generation_locked(st.session_state),
    )

    _active_options = _model_state["custom_options"] if _selected_source == "自定义" else _model_state["builtin_options"]
    _model_labels = [opt["label"] for opt in _active_options]
    _default_label = _model_state["default_model_label"] if _selected_source == _default_source else ""
    _default_model_index = _model_labels.index(_default_label) if _default_label in _model_labels else 0

    _selected_label = st.selectbox(
        "模型 / Model",
        _model_labels,
        index=_default_model_index,
        disabled=_is_generation_locked(st.session_state),
    )
    model_name = _resolve_selected_model(_selected_label, _active_options)
    st.session_state["current_model"] = model_name  # 供视觉检测使用

    if model_name and model_name != _config_defaults.get("llm_model", ""):
        try:
            _save_cfg_model = GDLAgentConfig.load()
            _save_cfg_model.llm.model = model_name
            _save_cfg_model.save()
            _config_defaults["llm_model"] = model_name
        except Exception as e:
            st.sidebar.warning(f"配置保存失败：{e}")

    # Load or initialize API Key for this specific model
    if model_name not in st.session_state.model_api_keys:
        # Auto-fill from config.toml provider_keys
        st.session_state.model_api_keys[model_name] = _key_for_model(model_name)

    is_custom = model_name in _collect_custom_model_aliases(_custom_list)

    if is_custom:
        st.info("此模型使用自定义代理，请在 config.toml 的 [[llm.custom_providers]] 中配置 api_key 和 base_url")
        api_key = st.session_state.model_api_keys.get(model_name, "")
    else:
        api_key = st.text_input(
            "API Key",
            value=st.session_state.model_api_keys.get(model_name, ""),
            type="password",
            help="Ollama 本地模式不需要 Key",
            disabled=_is_generation_locked(st.session_state),
        )

    # Auto-save API Key + 持久化写回 config.toml
    if api_key != st.session_state.model_api_keys.get(model_name, ""):
        st.session_state.model_api_keys[model_name] = api_key
        # 写回 config.toml
        try:
            from openbrep.config import GDLAgentConfig, model_to_provider
            _save_cfg = GDLAgentConfig.load()
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
        for _pcfg in _custom_providers:
            aliases = {str(e.get("alias", "") or "").lower() for e in iter_custom_provider_model_entries(_pcfg)}
            if m in aliases:
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
    st.subheader("AI助手设置")
    assistant_settings = st.text_area(
        "长期协作偏好",
        value=st.session_state.assistant_settings,
        height=140,
        placeholder="例如：我是 GDL 初学者，请先解释再给最小修改；我主要改已有对象；赶项目时优先给可运行结果。",
        help="长期保存。可填写你的 GDL 经验、当前使用场景、沟通方式偏好与修改边界。修改后立即影响后续聊天与生成。",
        disabled=_is_generation_locked(st.session_state),
    )
    if _should_persist_assistant_settings(_config_defaults.get("assistant_settings", ""), assistant_settings):
        st.session_state.assistant_settings = assistant_settings
        try:
            _save_cfg3 = GDLAgentConfig.load()
            _save_cfg3.llm.assistant_settings = assistant_settings
            _save_cfg3.save()
            _config_defaults["assistant_settings"] = assistant_settings
        except Exception as e:
            st.sidebar.warning(f"配置保存失败：{e}")
    else:
        st.session_state.assistant_settings = assistant_settings

    st.divider()

    # Project quick reset
    if st.session_state.project:
        if st.button("🗑️ 清除项目", width='stretch', disabled=_is_generation_locked(st.session_state)):
            _keep_work_dir  = st.session_state.work_dir
            _keep_api_keys  = st.session_state.model_api_keys
            _keep_chat      = st.session_state.chat_history   # preserve chat
            _keep_assistant_settings = st.session_state.assistant_settings
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
            st.session_state.assistant_settings = _keep_assistant_settings
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
    return ui_tapir_controller.tapir_sync_selection(
        tapir_import_ok=_TAPIR_IMPORT_OK,
        get_bridge_fn=get_bridge,
        session_state=st.session_state,
        now_text_fn=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _tapir_highlight_selection() -> tuple[bool, str]:
    return ui_tapir_controller.tapir_highlight_selection(
        tapir_import_ok=_TAPIR_IMPORT_OK,
        get_bridge_fn=get_bridge,
        session_state=st.session_state,
    )


def _tapir_load_selected_params() -> tuple[bool, str]:
    return ui_tapir_controller.tapir_load_selected_params(
        tapir_import_ok=_TAPIR_IMPORT_OK,
        get_bridge_fn=get_bridge,
        session_state=st.session_state,
    )


def _tapir_apply_param_edits() -> tuple[bool, str]:
    return ui_tapir_controller.tapir_apply_param_edits(
        tapir_import_ok=_TAPIR_IMPORT_OK,
        get_bridge_fn=get_bridge,
        session_state=st.session_state,
    )


def _render_tapir_inspector_panel() -> None:
    return ui_tapir_views.render_tapir_inspector_panel(
        session_state=st.session_state,
        caption_fn=st.caption,
        warning_fn=st.warning,
        info_fn=st.info,
        markdown_fn=st.markdown,
        code_fn=st.code,
        json_fn=st.json,
    )


def _render_tapir_param_workbench_panel() -> None:
    return ui_tapir_views.render_tapir_param_workbench_panel(
        session_state=st.session_state,
        info_fn=st.info,
        expander_fn=st.expander,
        text_input_fn=st.text_input,
    )


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
    import logging
    config = LLMConfig(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
        temperature=0.2,
        max_tokens=4096,
        assistant_settings=st.session_state.get("assistant_settings", ""),
        custom_providers=_custom_providers,
    )
    resolved_base = config.resolve_api_base()
    resolved_key = config.resolve_api_key()
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
    return ui_view_models.versioned_gsm_path(proj_name, work_dir, revision=revision)



def _max_existing_gsm_revision(proj_name: str, work_dir: str) -> int:
    return ui_view_models.max_existing_gsm_revision(proj_name, work_dir)



def _safe_compile_revision(proj_name: str, work_dir: str, requested_revision: int) -> int:
    return ui_view_models.safe_compile_revision(proj_name, work_dir, requested_revision)


def _derive_gsm_name_from_filename(filename: str) -> str:
    return ui_view_models.derive_gsm_name_from_filename(filename)



def _extract_gsm_name_candidate(text: str) -> str:
    return ui_view_models.extract_gsm_name_candidate(text)



def _stamp_script_header(script_label: str, content: str, revision: int) -> str:
    return ui_view_models.stamp_script_header(script_label, content, revision)


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

_GDL_KEYWORDS = ui_view_models.GDL_KEYWORDS

_CHAT_ONLY_PATTERNS = ui_view_models.CHAT_ONLY_PATTERNS


def _is_gdl_intent(text: str) -> bool:
    return ui_view_models.is_gdl_intent(text)


def _is_pure_chat(text: str) -> bool:
    return ui_view_models.is_pure_chat(text)

def _route_main_input(text: str, project_loaded: bool = False, has_image: bool = False) -> tuple[str, str]:
    """Return pipeline intent plus extracted object name for the main chat box."""
    obj_name = _extract_object_name(text)
    intent = IntentRouter().classify(
        text,
        has_project=project_loaded,
        has_image=has_image,
    )
    return intent, obj_name


def classify_and_extract(text: str, llm, project_loaded: bool = False) -> tuple:
    """Compatibility wrapper for older tests/callers."""
    intent, obj_name = _route_main_input(text, project_loaded=project_loaded, has_image=False)
    return ui_view_models.classify_and_extract_result(intent, obj_name)


def chat_respond(user_input: str, history: list, llm) -> str:
    """Deprecated compatibility wrapper; main chat path should use TaskPipeline."""
    pipeline = TaskPipeline(trace_dir="./traces")
    pipeline.config = GDLAgentConfig.load()
    request_kwargs = ui_view_models.build_chat_respond_request_kwargs(
        user_input,
        project=st.session_state.get("project"),
        work_dir=st.session_state.get("work_dir", "./workdir"),
        trimmed_history=_trim_history_for_image(history, limit=6),
        assistant_settings=st.session_state.get("assistant_settings", ""),
    )
    result = pipeline.execute(TaskRequest(**request_kwargs))
    return result.plain_text if result.success else f"❌ {result.error}"


# ── Script Map (module-level, shared by agent + editor) ───
_SCRIPT_MAP = [
    (ScriptType.SCRIPT_3D, "scripts/3d.gdl",  "3D"),
    (ScriptType.SCRIPT_2D, "scripts/2d.gdl",  "2D"),
    (ScriptType.MASTER,    "scripts/1d.gdl",  "Master"),
    (ScriptType.PARAM,     "scripts/vl.gdl",  "Param"),
    (ScriptType.UI,        "scripts/ui.gdl",  "UI"),
    (ScriptType.PROPERTIES,"scripts/pr.gdl",  "Properties"),
]


def _make_elicitation_llm_caller():
    llm = get_llm()

    def _caller(messages):
        raw = llm.generate(messages)
        return raw.content if hasattr(raw, "content") else str(raw)

    return _caller



def _ensure_elicitation_agent() -> ElicitationAgent:
    agent = st.session_state.get("elicitation_agent")
    if agent is None:
        agent = ElicitationAgent(llm_caller=_make_elicitation_llm_caller())
        st.session_state.elicitation_agent = agent
    elif agent.llm_caller is None:
        agent.llm_caller = _make_elicitation_llm_caller()
    st.session_state.elicitation_state = agent.state.value
    return agent



def _is_positive_confirmation(text: str) -> bool:
    return ui_view_models.is_positive_confirmation(text)


def _is_negative_confirmation(text: str) -> bool:
    return ui_view_models.is_negative_confirmation(text)


def _is_modify_or_check_intent(text: str) -> bool:
    raw = (text or "").strip().lower()
    return ui_view_models.is_modify_or_check_intent(text, is_debug_intent=_is_debug_intent(raw))


_INTENT_CLARIFY_ACTION_LABELS = ui_view_models._INTENT_CLARIFY_ACTION_LABELS

_EXPLAINER_KEYWORDS = {
    "这是什么对象", "解释一下", "详细讲讲", "详细说说", "展开说说",
    "全面分析", "具体一点", "代码分析", "逻辑分析", "命令分析",
    "分析脚本", "3d 和 2d", "各负责什么", "分别控制什么",
    "控制什么", "负责什么", "有什么作用", "起什么作用", "什么意思",
}


def _is_explainer_intent(text: str) -> bool:
    return ui_view_models.is_explainer_intent(
        text,
        is_post_clarification_prompt=_is_post_clarification_prompt,
        is_debug_intent=_is_debug_intent,
        is_modify_or_check_intent=_is_modify_or_check_intent,
        explainer_keywords=_EXPLAINER_KEYWORDS,
    )


def _should_clarify_intent(text: str, has_project: bool, history: list[dict]) -> bool:
    raw = (text or "").strip()
    return ui_view_models.should_clarify_intent(
        raw,
        has_project=has_project,
        is_modify_bridge_prompt=_is_modify_bridge_prompt,
        has_followup_bridge=bool(_maybe_build_followup_bridge_input(raw, history, has_project)),
        is_post_clarification_prompt=_is_post_clarification_prompt,
        is_debug_intent=_is_debug_intent,
        is_explainer_intent=_is_explainer_intent,
    )



def _build_intent_clarification_message(recommended_option: str) -> str:
    return ui_view_models.build_intent_clarification_message(recommended_option)



def _maybe_build_intent_clarification(user_input: str, has_project: bool, history: list[dict]) -> dict | None:
    return ui_view_models.maybe_build_intent_clarification(
        user_input,
        should_clarify_intent=lambda text: _should_clarify_intent(text, has_project, history),
        build_intent_clarification_message=_build_intent_clarification_message,
    )



def _build_post_clarification_input(original_user_input: str, option: str) -> str:
    return ui_view_models.build_post_clarification_input(original_user_input, option)



def _consume_intent_clarification_choice(user_input: str, pending: dict | None) -> str | None:
    return ui_view_models.consume_intent_clarification_choice(user_input, pending)



def _clear_pending_intent_clarification() -> None:
    ui_view_models.clear_pending_intent_clarification(st.session_state)



def _should_start_elicitation(user_input: str) -> bool:
    return ui_view_models.should_start_elicitation(user_input)


def _make_generation_project(gdl_obj_name: str) -> HSFProject:
    new_proj = HSFProject.create_new(gdl_obj_name, work_dir=st.session_state.work_dir)
    st.session_state.project = new_proj
    st.session_state.pending_gsm_name = gdl_obj_name
    st.session_state.script_revision = 0
    return new_proj



def _should_skip_elicitation_for_gdl_request(text: str, intent: str | None = None) -> bool:
    return ui_view_models.should_skip_elicitation_for_gdl_request_from_text(
        text,
        intent=intent,
        project_loaded=bool(st.session_state.get("project")),
        route_main_input=_route_main_input,
    )



def _handle_elicitation_route(user_input: str, gdl_obj_name: str) -> tuple[str, bool]:
    ea = _ensure_elicitation_agent()

    if ea.state == ElicitationState.HANDOFF and ea.spec is not None:
        spec = ea.spec
        instruction = spec.to_instruction()
        logging.debug(f"Elicitation handoff instruction: {instruction[:200]}")
        object_name = spec.object_name
        ea.reset()
        st.session_state.elicitation_state = ea.state.value
        if not st.session_state.project:
            _make_generation_project(gdl_obj_name or object_name or "elicited_object")
            st.info(f"📁 已初始化项目 `{st.session_state.pending_gsm_name}`")
        proj_current = st.session_state.project
        _has_any_script = any(proj_current.get_script(s) for s, _, _ in _SCRIPT_MAP)
        effective_gsm = st.session_state.pending_gsm_name or proj_current.name
        return run_agent_generate(
            instruction,
            proj_current,
            st.container(),
            gsm_name=effective_gsm,
            auto_apply=not _has_any_script,
        ), False

    if ea.state == ElicitationState.SPEC_READY:
        if _is_positive_confirmation(user_input):
            spec = ea.confirm(True)
            st.session_state.elicitation_state = ea.state.value
            if spec is None:
                return "❌ 规格确认失败，请重试。", True
            return _handle_elicitation_route(user_input, spec.object_name)
        if _is_negative_confirmation(user_input):
            ea.confirm(False)
            st.session_state.elicitation_state = ea.state.value
            reply, _ = ea.respond(user_input)
            st.session_state.elicitation_state = ea.state.value
            return reply, True
        return ea._format_spec_summary(), True

    if ea.state == ElicitationState.ELICITING:
        reply, _ = ea.respond(user_input)
        st.session_state.elicitation_state = ea.state.value
        return reply, True

    if _should_start_elicitation(user_input):
        reply = ea.start(user_input)
        st.session_state.elicitation_state = ea.state.value
        return reply, True

    return "", False



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
_DEBUG_KEYWORDS = ui_view_models.DEBUG_KEYWORDS

# Archicad GDL 错误格式特征
_ARCHICAD_ERROR_PATTERN = ui_view_models._DEBUG_INTENT_ARCHICAD_ERROR_PATTERN


def _is_debug_intent(text: str) -> bool:
    return ui_view_models.is_debug_intent(text)


def _get_debug_mode(text: str) -> str:
    return ui_view_models.get_debug_mode(text)


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
    logging.debug(f"run_agent_generate called, instruction: {user_input[:100]}")
    generation_id = _begin_generation_state(st.session_state)
    status_ph = status_col.empty()
    debug_mode = (
        _is_debug_intent(user_input)
        and not _is_explainer_intent(user_input)
        and not _is_post_clarification_prompt(user_input)
    )
    debug_type = _get_debug_mode(user_input)  # 'editor' | 'keyword'

    def on_event(event_type, data):
        if not _is_active_generation(st.session_state, generation_id):
            return
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            mode_tag = f" [Debug:{debug_type}]" if debug_mode else ""
            _guarded_event_update(status_ph, generation_id, "info", f"🔍 分析中{mode_tag}... 脚本: {', '.join(scripts)}")
        elif event_type == "attempt":
            _guarded_event_update(status_ph, generation_id, "info", "🧠 调用 AI...")
        elif event_type == "llm_response":
            _guarded_event_update(status_ph, generation_id, "info", f"✏️ 收到 {data['length']} 字符，解析中...")
        elif event_type == "validate":
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])
            if errors:
                _guarded_event_update(status_ph, generation_id, "error", f"❌ 发现 {len(errors)} 个错误，AI 自动修复中...")
            elif warnings:
                _guarded_event_update(status_ph, generation_id, "warning", f"⚠️ 发现 {len(warnings)} 条建议，已附在结果中")
            else:
                _guarded_event_update(status_ph, generation_id, "success", "✅ 校验通过")
        elif event_type == "rewrite":
            round_num = data.get("round", 2)
            _guarded_event_update(status_ph, generation_id, "info", f"🔄 第 {round_num} 轮修复中...")
        elif event_type == "cancelled":
            _guarded_event_update(status_ph, generation_id, "warning", "⏹️ 正在停止当前生成...")
        elif event_type == "compile_result":
            if data.get("success"):
                _guarded_event_update(status_ph, generation_id, "success", "✅ 编译验证通过")
            elif data.get("error"):
                _guarded_event_update(status_ph, generation_id, "warning", "⚠️ 编译验证失败，已附在结果中")
        elif event_type == "status":
            _guarded_event_update(status_ph, generation_id, "info", data.get("message", ""))
        elif event_type == "vision_analysis_done":
            component = data.get("component_type", "")
            label = f"「{component}」" if component and component != "未知构件" else ""
            _guarded_event_update(status_ph, generation_id, "info", f"🖼️ 图像分析完成{label}，正在生成 GDL…")

    try:
        # Pass recent chat history for multi-turn context (last 6 messages, skip heavy code blocks)
        recent_history = _trim_history_for_image([
            m for m in st.session_state.chat_history[-8:]
            if m["role"] in ("user", "assistant")
        ])

        pipeline_project = proj if auto_apply else deepcopy(proj)
        if debug_mode:
            intent = "REPAIR"
        elif _is_modify_bridge_prompt(user_input):
            intent = "MODIFY"
        elif _is_post_clarification_prompt(user_input):
            if "本次确认目标：先快速解释脚本结构" in user_input:
                intent = "CHAT"
            else:
                intent = "MODIFY"
        elif _is_explainer_intent(user_input):
            intent = "CHAT"
        elif any(pipeline_project.get_script(st) for st in ScriptType):
            intent = "MODIFY"
        else:
            intent = "CREATE"

        logger.info(
            "pipeline generate route=%s image_name=%s has_project=%s prompt_len=%d",
            intent.lower(),
            "inline-image" if debug_image_b64 else "none",
            bool(proj),
            len(user_input or ""),
        )

        pipeline = TaskPipeline(trace_dir="./traces")
        pipeline.config = GDLAgentConfig.load()
        request = TaskRequest(
            user_input=user_input,
            intent=intent,
            project=pipeline_project,
            work_dir=st.session_state.work_dir,
            gsm_name=gsm_name or pipeline_project.name,
            output_dir=str(Path(st.session_state.work_dir) / "output"),
            assistant_settings=st.session_state.get("assistant_settings", ""),
            on_event=on_event,
            history=recent_history,
            last_code_context=None,
            should_cancel=lambda: not _should_accept_generation_result(st.session_state, generation_id),
            image_b64=debug_image_b64,
            image_mime=debug_image_mime,
        )

        result = pipeline.execute(request)
        if not result.success:
            status_ph.empty()
            _finalize_generation(generation_id, "failed")
            return f"❌ **错误**: {result.error}"

        if not _consume_generation_result(generation_id):
            status_ph.empty()
            _finalize_generation(generation_id, "cancelled")
            return _generation_cancelled_message()

        status_ph.empty()
        result_prefix = ""
        code_blocks: list[str] = []
        plan = build_generation_result_plan(result, auto_apply=auto_apply, gsm_name=gsm_name)
        if plan.has_changes:
            if auto_apply and result.project is not None:
                if proj is not result.project:
                    st.session_state.project = result.project
                    proj = result.project
                result_prefix, code_blocks = _apply_generation_plan(plan, proj, gsm_name, already_applied=True)
            else:
                result_prefix, code_blocks = _apply_generation_plan(plan, proj, gsm_name, already_applied=False)
        _finalize_generation(generation_id, "completed")
        return _build_generation_reply(result.plain_text, result_prefix, code_blocks)

    except Exception as e:
        status_ph.empty()
        _finalize_generation(generation_id, "failed")
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
    return ui_project_io.do_compile(
        proj,
        gsm_name,
        instruction,
        session_state=st.session_state,
        safe_compile_revision_fn=_safe_compile_revision,
        versioned_gsm_path_fn=_versioned_gsm_path,
        get_compiler_fn=get_compiler,
        compiler_mode=compiler_mode,
    )


def import_gsm(gsm_bytes: bytes, filename: str) -> tuple:
    """
    Decompile GSM → HSF → HSFProject via LP_XMLConverter libpart2hsf.
    Returns (project | None, message).
    """
    return ui_project_io.import_gsm(
        gsm_bytes,
        filename,
        get_compiler_fn=get_compiler,
        mock_compiler_class=MockHSFCompiler,
        work_dir=st.session_state.work_dir,
    )


def _normalize_pasted_path(raw_path: str) -> str:
    return ui_view_models.normalize_pasted_path(raw_path)


def _handle_hsf_directory_load(project_dir: str) -> tuple[bool, str]:
    return ui_project_io.handle_hsf_directory_load(
        project_dir,
        normalize_pasted_path_fn=_normalize_pasted_path,
        load_project_from_disk_fn=lambda path: HSFProject.load_from_disk(path),
        finalize_loaded_project_fn=_finalize_loaded_project,
    )



def _finalize_loaded_project(proj: HSFProject, msg: str, pending_gsm_name: str) -> tuple[bool, str]:
    return ui_actions.finalize_loaded_project(
        proj,
        msg,
        pending_gsm_name,
        st.session_state,
        _reset_tapir_p0_state,
        _bump_main_editor_version,
    )



def _handle_unified_import(uploaded_file) -> tuple[bool, str]:
    """
    Single entry point for importing any GDL-related file.
    - .gsm           → LP_XMLConverter decompile → HSFProject
    - .gdl / .txt    → parse_gdl_source text parse → HSFProject
    Updates session_state.project, pending_gsm_name, editor_version.
    Returns (success, message).
    """
    return ui_project_io.handle_unified_import(
        uploaded_file,
        import_gsm_fn=import_gsm,
        parse_gdl_source_fn=parse_gdl_source,
        derive_gsm_name_from_filename_fn=_derive_gsm_name_from_filename,
        finalize_loaded_project_fn=_finalize_loaded_project,
    )


def _strip_md_fences(code: str) -> str:
    return ui_view_models.strip_md_fences(code)


def _classify_code_blocks(text: str) -> dict:
    return ui_view_models.classify_code_blocks(text)


def _extract_gdl_from_text(text: str) -> dict:
    return ui_view_models.extract_gdl_from_text(text)


_EXPLAINER_FOLLOWUP_MODIFY_PATTERNS = ui_view_models._EXPLAINER_FOLLOWUP_MODIFY_PATTERNS


def _is_bridgeable_explainer_message(message: dict) -> bool:
    return ui_view_models.is_bridgeable_explainer_message(message)



def _is_explainer_followup_modify_request(text: str) -> bool:
    return ui_view_models.is_explainer_followup_modify_request(text)



def _find_latest_bridgeable_explainer_message(history: list[dict]) -> dict | None:
    return ui_view_models.find_latest_bridgeable_explainer_message(history)



def _build_modify_bridge_prompt(message: dict, fallback_request: str = "") -> str:
    return ui_view_models.build_modify_bridge_prompt(message, fallback_request=fallback_request)



def _maybe_build_followup_bridge_input(user_input: str, history: list[dict], has_project: bool) -> str | None:
    return ui_view_models.maybe_build_followup_bridge_input(user_input, history, has_project)


def _resolve_bridge_input(pending_bridge_idx, user_input: str | None, history: list[dict], has_project: bool) -> str | None:
    return ui_chat_controller.resolve_bridge_input(
        pending_bridge_idx=pending_bridge_idx,
        user_input=user_input,
        history=history,
        has_project=has_project,
        build_modify_bridge_prompt_fn=_build_modify_bridge_prompt,
        maybe_build_followup_bridge_input_fn=_maybe_build_followup_bridge_input,
    )



def _is_modify_bridge_prompt(text: str) -> bool:
    return ui_view_models.is_modify_bridge_prompt(text)



def _is_post_clarification_prompt(text: str) -> bool:
    return ui_view_models.is_post_clarification_prompt(text)



def _resolve_effective_input(active_debug_mode, user_input: str | None, has_image_input: bool, auto_debug_input: str | None, bridge_input: str | None, redo_input: str | None) -> tuple[str | None, bool, bool]:
    return ui_chat_controller.resolve_effective_input(
        active_debug_mode=active_debug_mode,
        user_input=user_input,
        has_image_input=has_image_input,
        auto_debug_input=auto_debug_input,
        bridge_input=bridge_input,
        redo_input=redo_input,
    )



def _resolve_image_route_mode(route_pick: str, active_debug_mode, joined_text: str, vision_name: str) -> str:
    return ui_chat_controller.resolve_image_route_mode(
        route_pick=route_pick,
        active_debug_mode=active_debug_mode,
        joined_text=joined_text,
        vision_name=vision_name,
        detect_image_task_mode_fn=_detect_image_task_mode,
    )



def _build_image_user_display(vision_name: str, route_mode: str, joined_text: str) -> str:
    return ui_chat_controller.build_image_user_display(vision_name, route_mode, joined_text)



def _run_normal_text_path(effective_input: str, redo_input, bridge_input, live_output, api_key: str, model_name: str) -> tuple[bool, bool, str | None]:
    return ui_chat_controller.run_normal_text_path(
        effective_input=effective_input,
        redo_input=redo_input,
        bridge_input=bridge_input,
        session_state=st.session_state,
        api_key=api_key,
        model_name=model_name,
        route_main_input_fn=_route_main_input,
        live_output=live_output,
        chat_respond_fn=chat_respond,
        should_skip_elicitation_fn=_should_skip_elicitation_for_gdl_request,
        create_project_fn=lambda name: HSFProject.create_new(name, work_dir=st.session_state.work_dir),
        has_any_script_content_fn=lambda proj: ui_actions.has_any_script_content(proj, _SCRIPT_MAP),
        run_agent_generate_fn=run_agent_generate,
        handle_elicitation_route_fn=_handle_elicitation_route,
        markdown_fn=st.markdown,
        info_fn=st.info,
        build_assistant_chat_message_fn=_build_assistant_chat_message,
    )



def _run_vision_path(has_image_input: bool, vision_mime: str | None, vision_name: str | None, user_input: str | None, active_debug_mode, vision_b64: str, live_output, api_key: str, model_name: str) -> tuple[bool, bool, str | None]:
    return ui_chat_controller.run_vision_path(
        has_image_input=has_image_input,
        vision_mime=vision_mime,
        vision_name=vision_name,
        user_input=user_input,
        active_debug_mode=active_debug_mode,
        vision_b64=vision_b64,
        session_state=st.session_state,
        api_key=api_key,
        model_name=model_name,
        resolve_image_route_mode_fn=_resolve_image_route_mode,
        build_image_user_display_fn=_build_image_user_display,
        live_output=live_output,
        create_project_fn=lambda name: HSFProject.create_new(name, work_dir=st.session_state.work_dir),
        has_any_script_content_fn=lambda proj: ui_actions.has_any_script_content(proj, _SCRIPT_MAP),
        thumb_image_bytes_fn=_thumb_image_bytes,
        image_fn=st.image,
        markdown_fn=st.markdown,
        run_vision_generate_fn=run_vision_generate,
        run_agent_generate_with_debug_image_fn=lambda req, proj, status_col, gsm_name, auto_apply, image_b64, image_mime: run_agent_generate(
            req,
            proj,
            status_col,
            gsm_name=gsm_name,
            auto_apply=auto_apply,
            debug_image_b64=image_b64,
            debug_image_mime=image_mime,
        ),
    )



def _build_assistant_chat_message(content: str, intent: str, has_project: bool, source_user_input: str) -> dict:
    return ui_view_models.build_assistant_chat_message(content, intent, has_project, source_user_input)


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
    return ui_view_models.build_chat_script_anchors(history)


def _classify_vision_error(exc: Exception) -> str:
    return ui_view_models.classify_vision_error(exc)


def _validate_chat_image_size(raw_bytes: bytes, image_name: str) -> str | None:
    return ui_view_models.validate_chat_image_size(raw_bytes, image_name, MAX_CHAT_IMAGE_BYTES)


def _trim_history_for_image(history: list[dict], limit: int = 4) -> list[dict]:
    return ui_view_models.trim_history_for_image(history, limit=limit)


def _thumb_image_bytes(image_b64: str) -> bytes | None:
    return ui_view_models.thumb_image_bytes(image_b64)


def _detect_image_task_mode(user_text: str, image_name: str = "") -> str:
    return ui_view_models.detect_image_task_mode(
        user_text,
        image_name=image_name,
        has_project=bool(st.session_state.get("project")),
    )


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
    generation_id = _begin_generation_state(st.session_state)
    status_ph = status_col.empty()
    try:
        llm = get_llm()
        logger.info(
            "vision generate start route=generate image_mime=%s has_project=%s prompt_len=%d",
            image_mime,
            bool(proj),
            len(extra_text or ""),
        )
        _guarded_event_update(status_ph, generation_id, "info", "🖼️ AI 正在解析图片...")

        user_text = extra_text.strip() if extra_text else "请分析这张图片，生成对应的 GDL 脚本。"
        resp = llm.generate_with_image(
            text_prompt=user_text,
            image_b64=image_b64,
            image_mime=image_mime,
            system_prompt=_VISION_SYSTEM_PROMPT,
        )
        if not _consume_generation_result(generation_id):
            status_ph.empty()
            _finalize_generation(generation_id, "cancelled")
            return _generation_cancelled_message()

        status_ph.empty()
        raw_text = resp.content
        extracted = _classify_code_blocks(raw_text)

        if extracted:
            result_prefix, _ = _apply_generation_result(extracted, proj, None, auto_apply)
            _finalize_generation(generation_id, "completed")
            return result_prefix + raw_text

        _finalize_generation(generation_id, "completed")
        return f"🖼️ **图片分析完成**（未检测到 GDL 代码块，AI 可能只给了文字分析）\n\n{raw_text}"

    except Exception as exc:
        status_ph.empty()
        _finalize_generation(generation_id, "failed")
        logger.warning("vision generate failed error=%s", exc.__class__.__name__)
        err_msg = _classify_vision_error(exc)
        st.error(err_msg)
        return f"❌ {err_msg}"


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
    return ui_view_models.to_float(raw)



def _preview_param_values(proj: HSFProject) -> dict[str, float]:
    return ui_view_models.preview_param_values(proj)



def _dedupe_keep_order(items: list[str]) -> list[str]:
    return ui_view_models.dedupe_keep_order(items)


def _collect_preview_prechecks(proj: HSFProject, target: str) -> list[str]:
    return ui_preview_controller.collect_preview_prechecks(
        proj,
        target,
        check_gdl_script_fn=check_gdl_script,
        validator_factory=GDLValidator,
        dedupe_keep_order_fn=_dedupe_keep_order,
        script_type_2d=ScriptType.SCRIPT_2D,
        script_type_3d=ScriptType.SCRIPT_3D,
    )


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
    return ui_preview_controller.run_preview(
        proj,
        target,
        sync_visible_editor_buffers_fn=_sync_visible_editor_buffers,
        editor_version=int(st.session_state.get("editor_version", 0)),
        preview_param_values_fn=_preview_param_values,
        collect_preview_prechecks_fn=_collect_preview_prechecks,
        dedupe_keep_order_fn=_dedupe_keep_order,
        set_preview_2d_data_fn=lambda data: st.session_state.__setitem__("preview_2d_data", data),
        set_preview_3d_data_fn=lambda data: st.session_state.__setitem__("preview_3d_data", data),
        set_preview_warnings_fn=lambda warns: st.session_state.__setitem__("preview_warnings", warns),
        set_preview_meta_fn=lambda meta: st.session_state.__setitem__("preview_meta", meta),
        script_type_2d=ScriptType.SCRIPT_2D,
        script_type_3d=ScriptType.SCRIPT_3D,
    )


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
                disabled=_is_generation_locked(st.session_state),
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

            hsf_dir_input = st.text_input(
                "HSF 项目目录",
                value="",
                placeholder="/path/to/YourHSFProject",
                key="editor_hsf_dir",
                disabled=_is_generation_locked(st.session_state),
            )
            if st.button(
                "载入 HSF 项目",
                key="editor_load_hsf",
                disabled=_is_generation_locked(st.session_state),
                use_container_width=True,
            ):
                ok, _hsf_msg = _handle_hsf_directory_load(hsf_dir_input)
                if ok:
                    st.rerun()
                else:
                    st.error(_hsf_msg)

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
                st.markdown(_msg["content"])
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
                        _is_bridgeable = _is_bridgeable_explainer_message(_msg)
                        if _has_code:
                            _msg_raw = _msg.get("content", "")
                            _has_full_suite = (
                                "scripts/3d.gdl" in _msg_raw
                                and "paramlist.xml" in _msg_raw
                            )
                            if _has_full_suite:
                                _is_adopted = st.session_state.adopted_msg_index == _i
                                _adopt_label = "✅ 已采用" if _is_adopted else "📥 采用这套"
                                if st.button(_adopt_label, key=f"adopt_{_i}", width='stretch'):
                                    st.session_state["_pending_adopt_idx"] = _i
                        elif _is_bridgeable:
                            if st.button("🪄 按此说明修改", key=f"bridge_modify_{_i}", width='stretch'):
                                st.session_state["_pending_bridge_idx"] = _i
                                st.rerun()
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
                            _capture_last_project_snapshot("聊天代码采纳")
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
            _pac1, _pac2, _pac3 = st.columns([1.2, 1, 1.6])
            with _pac1:
                if st.button("✅ 写入", type="primary", width='stretch',
                             key="chat_pending_apply"):
                    _proj = st.session_state.project
                    if _proj:
                        _capture_last_project_snapshot("AI 确认写入")
                        sc, pc = _apply_scripts_to_project(_proj, _pd)
                        _ok_parts = []
                        if sc: _ok_parts.append(f"{sc} 个脚本")
                        if pc: _ok_parts.append(f"{pc} 个参数")
                        _bump_main_editor_version()
                        st.toast(f"✅ 已写入 {'、'.join(_ok_parts)}", icon="✏️")
                    st.session_state.pending_diffs    = {}
                    st.session_state.pending_ai_label = ""
                    st.rerun()
            with _pac3:
                _undo_disabled = not bool(st.session_state.get("last_project_snapshot"))
                if st.button("↩ 撤销上次 AI 写入", width='stretch', key="chat_last_ai_undo", disabled=_undo_disabled):
                    ok, msg = _restore_last_project_snapshot()
                    if ok:
                        st.toast(msg, icon="↩")
                    else:
                        st.error(msg)
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
            disabled=_is_generation_locked(st.session_state),
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
                    _vision_size_error = _validate_chat_image_size(
                        _raw_bytes,
                        getattr(_img, "name", "image") or "image",
                    )
                    if _vision_size_error:
                        st.session_state.chat_history.append({"role": "assistant", "content": f"❌ {_vision_size_error}"})
                        st.error(_vision_size_error)
                        st.rerun()
                    _vision_b64 = base64.b64encode(_raw_bytes).decode()
                    _vision_mime = getattr(_img, "type", "") or "image/jpeg"
                    _vision_name = getattr(_img, "name", "") or "image"

    # ══════════════════════════════════════════════════════════
    #  Chat handler (outside columns — session state + rerun)
    # ══════════════════════════════════════════════════════════

    _redo_input                = st.session_state.pop("_redo_input", None)
    _pending_bridge_idx        = st.session_state.pop("_pending_bridge_idx", None)
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
    _bridge_input = _resolve_bridge_input(
        pending_bridge_idx=_pending_bridge_idx,
        user_input=user_input,
        history=st.session_state.get("chat_history", []),
        has_project=bool(st.session_state.get("project")),
    )

    # Debug模式：仅用户主动发送时触发，不自动构造空输入消息
    effective_input, _clear_debug_mode, _toast_missing_debug_text = _resolve_effective_input(
        active_debug_mode=_active_dbg,
        user_input=user_input,
        has_image_input=_has_image_input,
        auto_debug_input=_auto_debug_input,
        bridge_input=_bridge_input,
        redo_input=_redo_input,
    )
    if _clear_debug_mode:
        st.session_state["_debug_mode_active"] = None
    if _toast_missing_debug_text:
        st.toast("请输入问题描述后再发送，或直接描述你看到的现象", icon="💬")

    # 在用户消息中提取物件名作为 GSM 名称候选（仅当当前为空）
    if user_input and not (st.session_state.pending_gsm_name or "").strip():
        _gsm_candidate = _extract_gsm_name_candidate(user_input)
        if _gsm_candidate:
            st.session_state.pending_gsm_name = _gsm_candidate

    # ── Vision path: attachment on chat_input ────────────────────────────────────
    if _has_image_input:
        _handled, _should_rerun, _err_msg = _run_vision_path(
            _has_image_input,
            _vision_mime,
            _vision_name,
            user_input,
            _active_dbg,
            _vision_b64,
            live_output,
            api_key,
            model_name,
        )
        if _err_msg:
            st.error(_err_msg)
        if _handled and _should_rerun:
            st.rerun()

    # ── Normal text path ─────────────────────────────────────────────────────────
    elif effective_input:
        _handled, _should_rerun, _err_msg = _run_normal_text_path(
            effective_input,
            _redo_input,
            _bridge_input,
            live_output,
            api_key,
            model_name,
        )
        if _err_msg:
            st.error(_err_msg)
        if _handled and _should_rerun:
            st.rerun()


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
        '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
        f'OpenBrep v{OPENBREP_VERSION} · HSF-native · Code Your Boundaries ·'
        '<a href="https://github.com/byewind1/openbrep">GitHub</a>'
        '</p>',
        unsafe_allow_html=True,
    )
