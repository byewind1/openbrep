"""
openbrep Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import os
import time
import logging
import json
import csv
import hashlib
import hmac
import string
import subprocess
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
from openbrep.paramlist_builder import build_paramlist_xml
from openbrep.compiler import CompileResult, MockHSFCompiler
from openbrep.validator import GDLValidator
try:
    from openbrep.config import ALL_MODELS, VISION_MODELS, REASONING_MODELS, GDLAgentConfig, iter_custom_provider_model_entries
    _MODEL_CONSTANTS_OK = True
except ImportError:
    ALL_MODELS = []
    VISION_MODELS = set()
    REASONING_MODELS = set()
    _MODEL_CONSTANTS_OK = False
from openbrep.elicitation_agent import ElicitationAgent, ElicitationState
from openbrep import __version__ as OPENBREP_VERSION
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, build_generation_result_plan
from openbrep.runtime.router import IntentRouter
from ui import actions as ui_actions
from ui import state as ui_state
from ui import view_models as ui_view_models
from ui import preview_controller as ui_preview_controller
from ui import project_service as ui_project_service
from ui import revision_controller as ui_revision_controller
from ui import chat_controller as ui_chat_controller
from ui import tapir_controller as ui_tapir_controller
from ui import tapir_views as ui_tapir_views
from ui import vision_controller as ui_vision_controller
from ui import gdl_checks as ui_gdl_checks
from ui import generation_service as ui_generation_service
from ui import generation_controls as ui_generation_controls
from ui import chat_helpers as ui_chat_helpers
from ui import app_shell as ui_app_shell
from ui import config_service as ui_config_service
from ui import feedback_service as ui_feedback_service
from ui import license_service as ui_license_service
from ui import object_naming as ui_object_naming
from ui import project_snapshot as ui_project_snapshot
from ui import runtime_service as ui_runtime_service
from ui import session_defaults as ui_session_defaults
from ui.views import chat_panel as ui_chat_panel
from ui.views import editor_panel as ui_editor_panel
from ui.views import parameter_panel as ui_parameter_panel
from ui.views import preview_views as ui_preview_views
from ui.views import project_tools_panel as ui_project_tools_panel
from ui.views import sidebar_panel as ui_sidebar_panel
from ui.views import welcome_panel as ui_welcome_panel
from ui.views import workspace_tools_panel as ui_workspace_tools_panel
from ui import knowledge_access as ui_knowledge_access

logger = logging.getLogger(__name__)
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
st_ace, _ACE_AVAILABLE = ui_app_shell.load_streamlit_ace()
go, _PLOTLY_AVAILABLE = ui_app_shell.load_plotly_graph_objects()

get_bridge, errors_to_chat_message, _TAPIR_IMPORT_OK = ui_app_shell.load_tapir_bridge()


ui_app_shell.configure_page(st)


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
    return ui_generation_controls.generation_stop_label(st.session_state)



def _render_generation_controls() -> None:
    ui_generation_controls.render_generation_controls(st, st.session_state)



def _guarded_event_update(status_ph, generation_id: str, method_name: str, message: str) -> None:
    ui_generation_controls.guarded_event_update(
        st.session_state,
        status_ph,
        generation_id,
        method_name,
        message,
    )



def _generation_cancelled_message() -> str:
    return ui_generation_controls.generation_cancelled_message()



def _consume_generation_result(generation_id: str) -> bool:
    return ui_generation_controls.consume_generation_result(st.session_state, generation_id)



def _capture_last_project_snapshot(label: str) -> None:
    ui_project_snapshot.capture_last_project_snapshot(
        st.session_state,
        label,
        deepcopy_fn=deepcopy,
    )


def _restore_last_project_snapshot() -> tuple[bool, str]:
    return ui_project_snapshot.restore_last_project_snapshot(
        st.session_state,
        bump_main_editor_version_fn=_bump_main_editor_version,
        deepcopy_fn=deepcopy,
    )


def _finalize_generation(generation_id: str, status: str) -> bool:
    return ui_generation_controls.finalize_generation(st.session_state, generation_id, status)



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


def _has_streamlit_runtime_context() -> bool:
    """Return True only when the app is running under `streamlit run`."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _license_service() -> ui_license_service.LicenseService:
    return ui_license_service.LicenseService(
        root=Path(__file__).parent.parent,
        has_runtime_context_fn=_has_streamlit_runtime_context,
    )


def _empty_license_record() -> dict:
    return _license_service().empty_record()


def _load_license(work_dir: str) -> dict:
    return _license_service().load(work_dir)


def _save_license(work_dir: str, data: dict) -> None:
    _license_service().save(work_dir, data)


def _verify_pro_code(code: str) -> tuple[bool, str, dict | None]:
    return _license_service().verify_pro_code(code)


def _license_record_is_active(data: dict) -> tuple[bool, str, dict | None]:
    return _license_service().record_is_active(data)


def _import_pro_knowledge_zip(file_bytes: bytes, filename: str, work_dir: str) -> tuple[bool, str]:
    return _license_service().import_pro_knowledge_zip(file_bytes, filename, work_dir)


# ── Load config.toml defaults ──────────────────────────

_config = None
_config_defaults = {}
_provider_keys: dict = {}   # {provider: api_key}
_custom_providers: list = []  # [{base_url, models, api_key, protocol, name}]


def _get_reloadable_model_list() -> list[str]:
    return ui_config_service.available_models(_config, _custom_providers, ALL_MODELS)


def _reload_config_globals(update_session_state: bool = False) -> None:
    global _config, _config_defaults, _provider_keys, _custom_providers

    state = ui_config_service.load_runtime_config(Path(__file__).parent.parent)
    _config = state.config
    _config_defaults = state.defaults
    _provider_keys = state.provider_keys
    _custom_providers = state.custom_providers

    if not update_session_state:
        return

    ui_config_service.refresh_session_model_keys(
        st.session_state,
        config=_config,
        defaults=_config_defaults,
        provider_keys=_provider_keys,
        custom_providers=_custom_providers,
        builtin_models=ALL_MODELS,
    )


try:
    _reload_config_globals()
except Exception:
    pass


def _key_for_model(model: str) -> str:
    return ui_config_service.key_for_model(model, _provider_keys, _custom_providers)


def _sync_llm_top_level_fields_for_model(cfg: GDLAgentConfig, model: str) -> bool:
    return ui_config_service.sync_llm_top_level_fields_for_model(cfg, model)


def _is_archicad_running() -> bool:
    return ui_app_shell.is_archicad_running()


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

def _save_feedback(msg_idx: int, rating: str, content: str, comment: str = "") -> None:
    ui_feedback_service.save_feedback(st.session_state.work_dir, msg_idx, rating, content, comment)


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
    return ui_runtime_service.build_compiler(compiler_mode, converter_path)

def get_llm():
    return ui_runtime_service.build_llm(
        model_name=model_name,
        api_key=api_key,
        api_base=api_base,
        assistant_settings=st.session_state.get("assistant_settings", ""),
        custom_providers=_custom_providers,
    )

def load_knowledge(task_type: str = "all"):
    return ui_knowledge_access.load_knowledge(
        task_type,
        work_dir=st.session_state.work_dir,
        pro_unlocked=st.session_state.get("pro_unlocked", False),
        project_root=Path(__file__).parent.parent,
    )

def load_skills():
    return ui_runtime_service.load_skills(
        project_root=Path(__file__).parent.parent,
        work_dir=st.session_state.work_dir,
    )

def _extract_gsm_name_candidate(text: str) -> str:
    return ui_view_models.extract_gsm_name_candidate(text)



def _stamp_script_header(script_label: str, content: str, revision: int) -> str:
    return ui_view_models.stamp_script_header(script_label, content, revision)


def _extract_object_name(text: str) -> str:
    return ui_object_naming.extract_object_name(text)


# ── Welcome / Onboarding Panel ────────────────────────────

def show_welcome():
    ui_welcome_panel.render_welcome(st, handle_unified_import_fn=_handle_unified_import)


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
    service = ui_generation_service.GenerationService(
        session_state=st.session_state,
        pipeline_class=TaskPipeline,
        config_loader_fn=GDLAgentConfig.load,
        build_generation_result_plan_fn=build_generation_result_plan,
        begin_generation_state_fn=_begin_generation_state,
        is_active_generation_fn=_is_active_generation,
        should_accept_generation_result_fn=lambda _state, generation_id: _consume_generation_result(generation_id),
        finish_generation_state_fn=lambda _state, generation_id, status: _finalize_generation(generation_id, status),
        generation_cancelled_message_fn=_generation_cancelled_message,
        trim_history_fn=lambda history: _trim_history_for_image(history, limit=4),
        is_debug_intent_fn=_is_debug_intent,
        get_debug_mode_fn=_get_debug_mode,
        is_explainer_intent_fn=_is_explainer_intent,
        is_modify_bridge_prompt_fn=_is_modify_bridge_prompt,
        is_post_clarification_prompt_fn=_is_post_clarification_prompt,
        apply_generation_plan_fn=_apply_generation_plan,
        build_generation_reply_fn=_build_generation_reply,
        logger=logger,
    )
    return service.run_agent_generate(
        user_input,
        proj,
        status_col,
        gsm_name=gsm_name,
        auto_apply=auto_apply,
        debug_image_b64=debug_image_b64,
        debug_image_mime=debug_image_mime,
    )


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


def _project_service() -> ui_project_service.ProjectService:
    return ui_project_service.ProjectService(
        session_state=st.session_state,
        compiler_mode=compiler_mode,
        get_compiler_fn=get_compiler,
        mock_compiler_class=MockHSFCompiler,
        parse_gdl_source_fn=parse_gdl_source,
        load_project_from_disk_fn=lambda path: HSFProject.load_from_disk(path),
        reset_tapir_p0_state_fn=_reset_tapir_p0_state,
        bump_main_editor_version_fn=_bump_main_editor_version,
        import_gsm_override_fn=import_gsm,
        reset_revision_ui_state_fn=ui_revision_controller.reset_revision_ui_state,
        reload_libraries_after_compile_fn=lambda: ui_tapir_controller.reload_libraries_after_compile(
            tapir_import_ok=_TAPIR_IMPORT_OK,
            get_bridge_fn=get_bridge,
        ),
    )


def do_compile(proj: HSFProject, gsm_name: str, instruction: str = "") -> tuple:
    return _project_service().do_compile(proj, gsm_name, instruction)


def import_gsm(gsm_bytes: bytes, filename: str) -> tuple:
    return _project_service().import_gsm(gsm_bytes, filename)


def _handle_hsf_directory_load(project_dir: str) -> tuple[bool, str]:
    return _project_service().handle_hsf_directory_load(project_dir)



def _finalize_loaded_project(proj: HSFProject, msg: str, pending_gsm_name: str) -> tuple[bool, str]:
    return _project_service().finalize_loaded_project(proj, msg, pending_gsm_name)



def _handle_unified_import(uploaded_file) -> tuple[bool, str]:
    return _project_service().handle_unified_import(uploaded_file)


_strip_md_fences = ui_view_models.strip_md_fences
_classify_code_blocks = ui_view_models.classify_code_blocks
_extract_gdl_from_text = ui_view_models.extract_gdl_from_text


_EXPLAINER_FOLLOWUP_MODIFY_PATTERNS = ui_view_models._EXPLAINER_FOLLOWUP_MODIFY_PATTERNS


_is_bridgeable_explainer_message = ui_view_models.is_bridgeable_explainer_message
_is_explainer_followup_modify_request = ui_view_models.is_explainer_followup_modify_request
_find_latest_bridgeable_explainer_message = ui_view_models.find_latest_bridgeable_explainer_message
_build_modify_bridge_prompt = ui_view_models.build_modify_bridge_prompt
_maybe_build_followup_bridge_input = ui_view_models.maybe_build_followup_bridge_input


def _resolve_bridge_input(pending_bridge_idx, user_input: str | None, history: list[dict], has_project: bool) -> str | None:
    return ui_chat_controller.resolve_bridge_input(
        pending_bridge_idx=pending_bridge_idx,
        user_input=user_input,
        history=history,
        has_project=has_project,
        build_modify_bridge_prompt_fn=_build_modify_bridge_prompt,
        maybe_build_followup_bridge_input_fn=_maybe_build_followup_bridge_input,
    )



_is_modify_bridge_prompt = ui_view_models.is_modify_bridge_prompt
_is_post_clarification_prompt = ui_view_models.is_post_clarification_prompt



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


def _apply_chat_anchor_pending(anchor_pending, session_state=None, rerun_fn=None) -> bool:
    return ui_chat_controller.apply_chat_anchor_pending(
        anchor_pending=anchor_pending,
        session_state=session_state or st.session_state,
        rerun_fn=rerun_fn or st.rerun,
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



_build_assistant_chat_message = ui_view_models.build_assistant_chat_message


def _extract_gdl_from_chat() -> dict:
    return ui_chat_helpers.extract_gdl_from_chat(st.session_state, _classify_code_blocks)


_build_chat_script_anchors = ui_view_models.build_chat_script_anchors
_classify_vision_error = ui_view_models.classify_vision_error


def _validate_chat_image_size(raw_bytes: bytes, image_name: str) -> str | None:
    return ui_view_models.validate_chat_image_size(raw_bytes, image_name, MAX_CHAT_IMAGE_BYTES)


_trim_history_for_image = ui_view_models.trim_history_for_image
_thumb_image_bytes = ui_view_models.thumb_image_bytes
_should_show_copyable_chat_content = ui_chat_helpers.should_show_copyable_chat_content
_copyable_chat_text = ui_chat_helpers.copyable_chat_text


def _copy_text_to_system_clipboard(text: str) -> tuple[bool, str]:
    return ui_chat_helpers.copy_text_to_system_clipboard(
        text,
        platform=sys.platform,
        run_fn=subprocess.run,
    )


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
    # 聊天编排下沉到 controller，app 只负责把依赖接进去
    ui_chat_controller.process_chat_turn(
        st=st,
        session_state=st.session_state,
        chat_payload=_chat_panel_payload,
        api_key=api_key,
        model_name=model_name,
        resolve_bridge_input_fn=_resolve_bridge_input,
        resolve_effective_input_fn=_resolve_effective_input,
        detect_gsm_name_candidate_fn=_extract_gsm_name_candidate,
        handle_tapir_test_trigger_fn=_handle_tapir_test_trigger,
        handle_tapir_selection_trigger_fn=_handle_tapir_selection_trigger,
        handle_tapir_highlight_trigger_fn=_handle_tapir_highlight_trigger,
        handle_tapir_load_params_trigger_fn=_handle_tapir_load_params_trigger,
        handle_tapir_apply_params_trigger_fn=_handle_tapir_apply_params_trigger,
        run_vision_path_fn=_run_vision_path,
        run_normal_text_path_fn=_run_normal_text_path,
        apply_chat_anchor_pending_fn=_apply_chat_anchor_pending,
    )

    # ── Footer ────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
        f'OpenBrep v{OPENBREP_VERSION} · HSF-native · Code Your Boundaries ·'
        '<a href="https://github.com/byewind1/openbrep">GitHub</a>'
        '</p>',
        unsafe_allow_html=True,
    )
