"""
openbrep Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import re
import os
import time
import logging
import json
import csv
import hashlib
import hmac
import string
import subprocess
import uuid
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

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
from openbrep.paramlist_builder import build_paramlist_xml
from openbrep.compiler import MockHSFCompiler, HSFCompiler, CompileResult
from openbrep.validator import GDLValidator
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
from ui import revision_controller as ui_revision_controller
from ui import chat_controller as ui_chat_controller
from ui import tapir_controller as ui_tapir_controller
from ui import tapir_views as ui_tapir_views
from ui import vision_controller as ui_vision_controller
from ui import gdl_checks as ui_gdl_checks
from ui import session_defaults as ui_session_defaults
from ui.views import chat_panel as ui_chat_panel
from ui.views import editor_panel as ui_editor_panel
from ui.views import parameter_panel as ui_parameter_panel
from ui.views import preview_views as ui_preview_views
from ui.views import project_tools_panel as ui_project_tools_panel
from ui.views import sidebar_panel as ui_sidebar_panel
from ui.views import workspace_tools_panel as ui_workspace_tools_panel
from ui import knowledge_access as ui_knowledge_access

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

ui_session_defaults.ensure_session_defaults(
    st.session_state,
    work_dir_default=str(Path.home() / "openbrep-workspace"),
)



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
    return ui_knowledge_access._license_file(work_dir)


def _has_streamlit_runtime_context() -> bool:
    """Return True only when the app is running under `streamlit run`."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _empty_license_record() -> dict:
    return ui_knowledge_access._empty_license_record()


def _load_license(work_dir: str) -> dict:
    return ui_knowledge_access._load_license(work_dir)


def _save_license(work_dir: str, data: dict) -> None:
    if not _has_streamlit_runtime_context():
        return
    ui_knowledge_access._save_license(work_dir, data)


def _load_pro_public_key(root: Path):
    return ui_knowledge_access._load_pro_public_key(root)


def _urlsafe_b64decode(data: str) -> bytes:
    return ui_knowledge_access._urlsafe_b64decode(data)


def _urlsafe_b64encode(data: bytes) -> str:
    return ui_knowledge_access._urlsafe_b64encode(data)


def _canonical_license_payload(payload: dict) -> bytes:
    return ui_knowledge_access._canonical_license_payload(payload)


def _normalize_license_record(payload: dict, signature_b64: str) -> dict:
    return ui_knowledge_access._normalize_license_record(payload, signature_b64)


def _verify_license_payload(payload: dict, signature_b64: str) -> tuple[bool, str, dict | None]:
    return ui_knowledge_access._verify_license_payload(payload, signature_b64, root=Path(__file__).parent.parent)


def _decode_signed_license_code(code: str) -> tuple[bool, str, dict | None]:
    return ui_knowledge_access._decode_signed_license_code(code, root=Path(__file__).parent.parent)


def _verify_pro_code(code: str) -> tuple[bool, str, dict | None]:
    return ui_knowledge_access._verify_pro_code(code, root=Path(__file__).parent.parent)


def _license_record_is_active(data: dict) -> tuple[bool, str, dict | None]:
    return ui_knowledge_access._license_record_is_active(data, root=Path(__file__).parent.parent)


def _verify_pro_package(unpacked_dir: Path) -> tuple[bool, str, dict | None]:
    return ui_knowledge_access._verify_pro_package(unpacked_dir, root=Path(__file__).parent.parent)


def _license_matches_package(license_record: dict, package_manifest: dict) -> tuple[bool, str]:
    return ui_knowledge_access._license_matches_package(license_record, package_manifest)


def _import_pro_knowledge_zip(file_bytes: bytes, filename: str, work_dir: str) -> tuple[bool, str]:
    return ui_knowledge_access._import_pro_knowledge_zip(
        file_bytes,
        filename,
        work_dir,
        root=Path(__file__).parent.parent,
    )


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

    # 自定义 provider 的模型匹配（兼容字符串与 {alias, model}）
    for _pcfg in _custom_providers:
        for _entry in iter_custom_provider_model_entries(_pcfg):
            _alias = str(_entry.get("alias", "") or "").lower()
            _model = str(_entry.get("model", "") or "").lower()
            if m and m in {_alias, _model}:
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


def _sync_llm_top_level_fields_for_model(cfg: GDLAgentConfig, model: str) -> bool:
    if not cfg or not model:
        return False

    changed = False
    model_name = str(model).strip()
    if not model_name:
        return False

    if cfg.llm.model != model_name:
        cfg.llm.model = model_name
        changed = True

    return changed


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



def _normalize_converter_path(raw_path: str) -> str:
    cleaned = (raw_path or "").strip().strip('"').strip("'")
    if sys.platform.startswith("win"):
        return cleaned
    return cleaned.replace("\\\\", "/").replace("\\", "/")


with st.sidebar:
    _sidebar_payload = ui_sidebar_panel.render_sidebar(
        st,
        openbrep_version=OPENBREP_VERSION,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        all_models=ALL_MODELS,
        config=_config,
        config_defaults=_config_defaults,
        custom_providers=_custom_providers,
        iter_custom_provider_model_entries_fn=iter_custom_provider_model_entries,
        is_generation_locked_fn=_is_generation_locked,
        is_archicad_running_fn=_is_archicad_running,
        render_generation_controls_fn=_render_generation_controls,
        has_streamlit_runtime_context_fn=_has_streamlit_runtime_context,
        load_license_fn=_load_license,
        license_record_is_active_fn=_license_record_is_active,
        save_license_fn=_save_license,
        empty_license_record_fn=_empty_license_record,
        verify_pro_code_fn=_verify_pro_code,
        import_pro_knowledge_zip_fn=_import_pro_knowledge_zip,
        normalize_converter_path_fn=_normalize_converter_path,
        reload_config_globals_fn=_reload_config_globals,
        build_model_source_state_fn=_build_model_source_state,
        resolve_selected_model_fn=_resolve_selected_model,
        sync_llm_top_level_fields_for_model_fn=_sync_llm_top_level_fields_for_model,
        key_for_model_fn=_key_for_model,
        collect_custom_model_aliases_fn=_collect_custom_model_aliases,
        should_persist_assistant_settings_fn=_should_persist_assistant_settings,
        reset_tapir_p0_state_fn=_reset_tapir_p0_state,
        bump_main_editor_version_fn=lambda: _bump_main_editor_version(),
    )
    work_dir = _sidebar_payload["work_dir"]
    compiler_mode = _sidebar_payload["compiler_mode"]
    converter_path = _sidebar_payload["converter_path"]
    model_name = _sidebar_payload["model_name"]
    api_key = _sidebar_payload["api_key"]
    api_base = _sidebar_payload["api_base"]
    max_retries = _sidebar_payload["max_retries"]


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
    return ui_knowledge_access.load_knowledge(
        task_type,
        work_dir=st.session_state.work_dir,
        pro_unlocked=st.session_state.get("pro_unlocked", False),
        project_root=Path(__file__).parent.parent,
    )

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


def _pop_chat_runtime_state(has_image_input: bool) -> dict:
    return ui_chat_controller.pop_chat_runtime_state(
        session_state=st.session_state,
        has_image_input=has_image_input,
    )


def _handle_tapir_test_trigger(tapir_trigger: bool) -> tuple[bool, bool]:
    return ui_chat_controller.handle_tapir_test_trigger(
        tapir_trigger=tapir_trigger,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        get_bridge_fn=get_bridge,
        errors_to_chat_message_fn=errors_to_chat_message,
        session_state=st.session_state,
    )


def _handle_tapir_selection_trigger(tapir_selection_trigger: bool) -> tuple[bool, bool]:
    return ui_chat_controller.handle_tapir_selection_trigger(
        tapir_selection_trigger=tapir_selection_trigger,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        tapir_sync_selection_fn=_tapir_sync_selection,
        session_state=st.session_state,
    )


def _handle_tapir_highlight_trigger(tapir_highlight_trigger: bool) -> tuple[bool, bool]:
    return ui_chat_controller.handle_tapir_highlight_trigger(
        tapir_highlight_trigger=tapir_highlight_trigger,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        tapir_highlight_selection_fn=_tapir_highlight_selection,
    )


def _handle_tapir_load_params_trigger(tapir_load_params_trigger: bool) -> tuple[bool, bool]:
    return ui_chat_controller.handle_tapir_load_params_trigger(
        tapir_load_params_trigger=tapir_load_params_trigger,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        tapir_load_selected_params_fn=_tapir_load_selected_params,
        session_state=st.session_state,
    )


def _handle_tapir_apply_params_trigger(tapir_apply_params_trigger: bool) -> tuple[bool, bool]:
    return ui_chat_controller.handle_tapir_apply_params_trigger(
        tapir_apply_params_trigger=tapir_apply_params_trigger,
        tapir_import_ok=_TAPIR_IMPORT_OK,
        tapir_apply_param_edits_fn=_tapir_apply_param_edits,
    )


def _apply_chat_anchor_pending(anchor_pending) -> bool:
    return ui_chat_controller.apply_chat_anchor_pending(
        anchor_pending=anchor_pending,
        session_state=st.session_state,
        rerun_fn=st.rerun,
    )


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


def _should_show_copyable_chat_content(message: dict) -> bool:
    return message.get("role") == "assistant" and bool((message.get("content") or "").strip())


def _copyable_chat_text(message: dict) -> str:
    if not _should_show_copyable_chat_content(message):
        return ""
    return str(message.get("content") or "")


def _copy_text_to_system_clipboard(text: str) -> tuple[bool, str]:
    payload = (text or "").strip()
    if not payload:
        return False, "当前消息无可复制内容"

    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=payload, text=True, check=True, timeout=2)
            return True, "已复制本条回复"
        if sys.platform.startswith("linux"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=payload, text=True, check=True, timeout=2)
            return True, "已复制本条回复"
        if sys.platform.startswith("win"):
            subprocess.run(["clip"], input=payload, text=True, check=True, timeout=2)
            return True, "已复制本条回复"
        return False, "当前系统暂不支持自动复制"
    except Exception:
        return False, "复制失败，请重试"


def _copy_to_clipboard(text: str, key: str) -> None:
    _ = key


def _detect_image_task_mode(user_text: str, image_name: str = "") -> str:
    return ui_view_models.detect_image_task_mode(
        user_text,
        image_name=image_name,
        has_project=bool(st.session_state.get("project")),
    )


# ── Vision generate ───────────────────────────────────────────────────────────

def run_vision_generate(
    image_b64: str,
    image_mime: str,
    extra_text: str,
    proj: HSFProject,
    status_col,
    auto_apply: bool = True,
) -> str:
    return ui_vision_controller.run_vision_generate(
        image_b64=image_b64,
        image_mime=image_mime,
        extra_text=extra_text,
        proj=proj,
        status_col=status_col,
        auto_apply=auto_apply,
        session_state=st.session_state,
        logger=logger,
        get_llm_fn=get_llm,
        begin_generation_state_fn=_begin_generation_state,
        guarded_event_update_fn=_guarded_event_update,
        consume_generation_result_fn=_consume_generation_result,
        finalize_generation_fn=_finalize_generation,
        generation_cancelled_message_fn=_generation_cancelled_message,
        classify_code_blocks_fn=_classify_code_blocks,
        apply_generation_result_fn=_apply_generation_result,
        classify_vision_error_fn=_classify_vision_error,
        error_fn=st.error,
    )


def check_gdl_script(content: str, script_type: str = "") -> list:
    return ui_gdl_checks.check_gdl_script(content, script_type)


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


def _render_preview_2d(data) -> None:
    ui_preview_views.render_preview_2d(st, data, plotly_available=_PLOTLY_AVAILABLE, go=go if _PLOTLY_AVAILABLE else None)


def _render_preview_3d(data) -> None:
    ui_preview_views.render_preview_3d(st, data, plotly_available=_PLOTLY_AVAILABLE, go=go if _PLOTLY_AVAILABLE else None)


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
        strict=bool(st.session_state.get("preview_strict", False)),
        unknown_command_policy=str(st.session_state.get("preview_unknown_command_policy", "warn") or "warn"),
        quality=str(st.session_state.get("preview_quality", "fast") or "fast"),
    )


# ══════════════════════════════════════════════════════════
#  Main Layout: Project tools | Editor | AI assistant
# ══════════════════════════════════════════════════════════
#  Layout: Project tools (left) | Editor (main) | AI assistant (right)
# ══════════════════════════════════════════════════════════

col_left, col_mid, col_right = st.columns([22, 48, 30], gap="small")


# ── Main workspace columns ────────────────────────────────

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
        ui_project_tools_panel.render_project_tools_panel(
            st,
            proj_now,
            is_generation_locked_fn=lambda: _is_generation_locked(st.session_state),
            handle_unified_import_fn=_handle_unified_import,
            handle_hsf_directory_load_fn=_handle_hsf_directory_load,
            do_compile_fn=lambda project, gsm_name, instruction: do_compile(
                project,
                gsm_name=gsm_name,
                instruction=instruction,
            ),
            save_revision_fn=ui_revision_controller.save_current_project_revision,
            restore_revision_fn=lambda project, revision_id: ui_revision_controller.restore_project_revision(
                project,
                revision_id,
                session_state=st.session_state,
                load_project_from_disk_fn=HSFProject.load_from_disk,
                reset_tapir_p0_state_fn=_reset_tapir_p0_state,
                bump_main_editor_version_fn=_bump_main_editor_version,
            ),
        )

        ui_workspace_tools_panel.render_workspace_tools_panel(
            st,
            proj_now,
            tapir_import_ok=_TAPIR_IMPORT_OK,
            get_bridge_fn=get_bridge,
            script_map=_SCRIPT_MAP,
            check_gdl_script_fn=check_gdl_script,
            run_preview_fn=_run_preview,
            render_preview_2d_fn=_render_preview_2d,
            render_preview_3d_fn=_render_preview_3d,
            reset_tapir_p0_state_fn=_reset_tapir_p0_state,
            bump_main_editor_version_fn=_bump_main_editor_version,
        )

with col_mid:
    with st.container(height=820, border=False):
        ui_editor_panel.render_script_editor_panel(
            st,
            proj_now,
            script_map=_SCRIPT_MAP,
            editor_version=_ev,
            ace_available=_ACE_AVAILABLE,
            st_ace_fn=st_ace if _ACE_AVAILABLE else None,
            main_editor_state_key_fn=_main_editor_state_key,
            fullscreen_editor_dialog_fn=_fullscreen_editor_dialog,
        )
        st.divider()
        ui_parameter_panel.render_parameter_panel(
            st,
            proj_now,
            render_tapir_inspector_fn=_render_tapir_inspector_panel,
            render_tapir_param_workbench_fn=_render_tapir_param_workbench_panel,
        )


# ── Right: AI Chat panel ──────────────────────────────────

with col_right:
    with st.container(height=820, border=False):
        _chat_panel_payload = ui_chat_panel.render_chat_panel(
            st,
            script_map=_SCRIPT_MAP,
            is_generation_locked_fn=_is_generation_locked,
            build_chat_script_anchors_fn=_build_chat_script_anchors,
            thumb_image_bytes_fn=_thumb_image_bytes,
            save_feedback_fn=_save_feedback,
            copyable_chat_text_fn=_copyable_chat_text,
            copy_text_to_system_clipboard_fn=_copy_text_to_system_clipboard,
            is_bridgeable_explainer_message_fn=_is_bridgeable_explainer_message,
            extract_gdl_from_text_fn=_extract_gdl_from_text,
            capture_last_project_snapshot_fn=_capture_last_project_snapshot,
            apply_scripts_to_project_fn=_apply_scripts_to_project,
            bump_main_editor_version_fn=_bump_main_editor_version,
            parse_paramlist_text_fn=_parse_paramlist_text,
            restore_last_project_snapshot_fn=_restore_last_project_snapshot,
            validate_chat_image_size_fn=_validate_chat_image_size,
        )
        live_output = _chat_panel_payload["live_output"]
        user_input = _chat_panel_payload["user_input"]
        _vision_b64 = _chat_panel_payload["vision_b64"]
        _vision_mime = _chat_panel_payload["vision_mime"]
        _vision_name = _chat_panel_payload["vision_name"]

    # ══════════════════════════════════════════════════════════
    #  Chat handler (outside columns — session state + rerun)
    # ══════════════════════════════════════════════════════════

    _runtime = _pop_chat_runtime_state(_vision_b64 is not None)
    _redo_input = _runtime["redo_input"]
    _pending_bridge_idx = _runtime["pending_bridge_idx"]
    _active_dbg = _runtime["active_debug_mode"]
    _tapir_trigger = _runtime["tapir_trigger"]
    _tapir_selection_trigger = _runtime["tapir_selection_trigger"]
    _tapir_highlight_trigger = _runtime["tapir_highlight_trigger"]
    _tapir_load_params_trigger = _runtime["tapir_load_params_trigger"]
    _tapir_apply_params_trigger = _runtime["tapir_apply_params_trigger"]
    _has_image_input = _runtime["has_image_input"]

    # 历史锚点定位：延迟到页面末尾执行，避免打断当前LLM调用
    _anchor_pending = _runtime["anchor_pending"]


    # ── Archicad 测试：ReloadLibraries + 捕获错误注入 chat ──
    _handled, _should_rerun = _handle_tapir_test_trigger(_tapir_trigger)
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = _handle_tapir_selection_trigger(_tapir_selection_trigger)
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = _handle_tapir_highlight_trigger(_tapir_highlight_trigger)
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = _handle_tapir_load_params_trigger(_tapir_load_params_trigger)
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = _handle_tapir_apply_params_trigger(_tapir_apply_params_trigger)
    if _handled and _should_rerun:
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
    _apply_chat_anchor_pending(_anchor_pending)

    # ── Footer ────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
        f'OpenBrep v{OPENBREP_VERSION} · HSF-native · Code Your Boundaries ·'
        '<a href="https://github.com/byewind1/openbrep">GitHub</a>'
        '</p>',
        unsafe_allow_html=True,
    )
