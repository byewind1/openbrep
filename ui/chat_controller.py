from __future__ import annotations

from typing import Callable

from openbrep.learning import ErrorLearningStore, looks_like_error_report

from ui.chat_render import render_assistant_block, render_user_bubble
from ui.generation_service import FORCE_GENERATE_PREFIX


CHAT_ROUTE_AUTO = "自动"
CHAT_ROUTE_FORCE_GENERATE = "强制生成"
CHAT_ROUTE_FORCE_DEBUG = "强制调试"


def pop_chat_runtime_state(
    *,
    session_state,
    has_image_input: bool,
) -> dict:
    return {
        "redo_input": session_state.pop("_redo_input", None),
        "pending_bridge_idx": session_state.pop("_pending_bridge_idx", None),
        "active_debug_mode": session_state.get("_debug_mode_active"),
        "tapir_trigger": session_state.pop("tapir_test_trigger", False),
        "tapir_selection_trigger": session_state.pop("tapir_selection_trigger", False),
        "tapir_highlight_trigger": session_state.pop("tapir_highlight_trigger", False),
        "tapir_load_params_trigger": session_state.pop("tapir_load_params_trigger", False),
        "tapir_apply_params_trigger": session_state.pop("tapir_apply_params_trigger", False),
        "has_image_input": bool(has_image_input),
    }


def handle_tapir_test_trigger(
    *,
    tapir_trigger: bool,
    tapir_import_ok: bool,
    get_bridge_fn: Callable[[], object],
    errors_to_chat_message_fn: Callable[[list], str],
    session_state,
) -> tuple[bool, bool]:
    if not (tapir_trigger and tapir_import_ok):
        return False, False

    import streamlit as st

    bridge = get_bridge_fn()
    proj_for_tapir = session_state.project
    with st.spinner("🏗️ 触发 Archicad 重新加载库，等待渲染..."):
        reload_ok, gdl_errors = bridge.reload_and_capture(
            timeout=6.0,
            project=proj_for_tapir,
        )

    if reload_ok:
        error_msg = errors_to_chat_message_fn(gdl_errors)
        session_state.chat_history.append({
            "role": "assistant",
            "content": error_msg,
        })
        if gdl_errors:
            auto_debug = "[DEBUG:editor] 请根据以上 Archicad 报错修复脚本"
            session_state.chat_history.append({
                "role": "user",
                "content": auto_debug,
            })
            session_state["_auto_debug_input"] = auto_debug
        return True, True

    st.toast("❌ Archicad 连接失败，请确认 Archicad 正在运行", icon="⚠️")
    return True, False


def handle_tapir_selection_trigger(
    *,
    tapir_selection_trigger: bool,
    tapir_import_ok: bool,
    tapir_sync_selection_fn: Callable[[], tuple[bool, str]],
    session_state,
) -> tuple[bool, bool]:
    if not (tapir_selection_trigger and tapir_import_ok):
        return False, False

    import streamlit as st

    ok, msg = tapir_sync_selection_fn()
    if ok:
        if session_state.get("tapir_selected_guids"):
            st.toast(f"✅ {msg}", icon="🧭")
        else:
            st.warning("未选中对象")
    else:
        st.error(f"❌ {msg}")
    return True, True


def handle_tapir_highlight_trigger(
    *,
    tapir_highlight_trigger: bool,
    tapir_import_ok: bool,
    tapir_highlight_selection_fn: Callable[[], tuple[bool, str]],
) -> tuple[bool, bool]:
    if not (tapir_highlight_trigger and tapir_import_ok):
        return False, False

    import streamlit as st

    ok, msg = tapir_highlight_selection_fn()
    if ok:
        st.toast(f"✅ {msg}", icon="🎯")
    else:
        st.error(f"❌ {msg}")
    return True, True


def handle_tapir_load_params_trigger(
    *,
    tapir_load_params_trigger: bool,
    tapir_import_ok: bool,
    tapir_load_selected_params_fn: Callable[[], tuple[bool, str]],
    session_state,
) -> tuple[bool, bool]:
    if not (tapir_load_params_trigger and tapir_import_ok):
        return False, False

    import streamlit as st

    ok, msg = tapir_load_selected_params_fn()
    if ok:
        if session_state.get("tapir_last_error"):
            st.warning(session_state.tapir_last_error)
        st.toast(f"✅ {msg}", icon="📥")
    else:
        st.error(f"❌ {msg}")
    return True, True


def handle_tapir_apply_params_trigger(
    *,
    tapir_apply_params_trigger: bool,
    tapir_import_ok: bool,
    tapir_apply_param_edits_fn: Callable[[], tuple[bool, str]],
) -> tuple[bool, bool]:
    if not (tapir_apply_params_trigger and tapir_import_ok):
        return False, False

    import streamlit as st

    ok, msg = tapir_apply_param_edits_fn()
    if ok:
        st.toast(f"✅ {msg}", icon="📤")
    else:
        st.error(f"❌ {msg}")
    return True, True


def resolve_bridge_input(
    *,
    pending_bridge_idx,
    user_input: str | None,
    history: list[dict],
    has_project: bool,
    build_modify_bridge_prompt_fn: Callable[[dict], str],
    maybe_build_followup_bridge_input_fn: Callable[[str, list[dict], bool], str | None],
) -> str | None:
    if pending_bridge_idx is not None:
        bridge_msg = history[pending_bridge_idx]
        return build_modify_bridge_prompt_fn(bridge_msg)
    if user_input:
        return maybe_build_followup_bridge_input_fn(
            user_input=user_input,
            history=history,
            has_project=has_project,
        )
    return None


def resolve_effective_input(
    *,
    active_debug_mode,
    user_input: str | None,
    has_image_input: bool,
    auto_debug_input: str | None,
    bridge_input: str | None,
    redo_input: str | None,
) -> tuple[str | None, bool, bool]:
    should_clear_debug_mode = False
    should_toast_missing_debug_text = False

    if active_debug_mode and user_input:
        dbg_prefix = f"[DEBUG:{active_debug_mode}]"
        return f"{dbg_prefix} {user_input.strip()}", True, False

    if active_debug_mode and user_input == "" and not has_image_input:
        should_toast_missing_debug_text = True
        return auto_debug_input or bridge_input or redo_input, False, True

    return auto_debug_input or bridge_input or redo_input or user_input, False, False


def resolve_image_route_mode(
    *,
    route_pick: str,
    active_debug_mode,
    joined_text: str,
    vision_name: str,
    detect_image_task_mode_fn: Callable[[str, str], str],
) -> str:
    if route_pick == "强制调试":
        return "debug"
    if route_pick == "强制生成":
        return "generate"
    if active_debug_mode:
        return "debug"
    return detect_image_task_mode_fn(joined_text, vision_name)


def build_image_user_display(vision_name: str, route_mode: str, joined_text: str) -> str:
    route_tag = "🧩 Debug" if route_mode == "debug" else "🧱 生成"
    return f"🖼️ `{vision_name}` · {route_tag}" + (f"  \n{joined_text}" if joined_text else "")


def maybe_record_conversation_error_learning(session_state, text: str | None) -> bool:
    if not text or not looks_like_error_report(text):
        return False
    try:
        project = session_state.get("project")
        project_name = getattr(project, "name", "") if project is not None else ""
        work_dir = session_state.get("work_dir", "./workdir")
        ErrorLearningStore(work_dir).record_error(
            text,
            source="conversation_error_fragment",
            project_name=project_name,
            instruction="用户在聊天中提供的脚本错误提示，后续生成需避免同类问题。",
        )
        session_state["learning_notice"] = "已加入错题本"
        return True
    except Exception:
        return False


def persist_new_chat_messages(session_state, start_index: int) -> int:
    try:
        messages = list(session_state.get("chat_history", []))[start_index:]
        if not messages:
            return 0
        project = session_state.get("project")
        project_name = getattr(project, "name", "") if project is not None else ""
        work_dir = session_state.get("work_dir", "./workdir")
        stored = ErrorLearningStore(work_dir).append_chat_messages(
            messages,
            project_name=project_name,
            source="ui_chat",
        )
        if stored:
            record_history = list(session_state.get("chat_record_history") or [])
            record_history.extend(messages)
            session_state.chat_record_history = record_history
            session_state.chat_record_history_loaded_work_dir = work_dir
        return stored
    except Exception:
        return 0


def run_normal_text_path(
    *,
    effective_input: str,
    redo_input,
    bridge_input,
    session_state,
    api_key: str,
    model_name: str,
    route_main_input_fn: Callable[[str, bool, bool], tuple[str, str]],
    live_output,
    chat_respond_fn: Callable[[str, list, object], str],
    should_skip_elicitation_fn: Callable[[str, str | None], bool],
    create_project_fn: Callable[[str], object],
    has_any_script_content_fn: Callable[[object], bool],
    run_agent_generate_fn: Callable[[str, object, object, str, bool], str],
    handle_elicitation_route_fn: Callable[[str, str], tuple[str, bool]],
    markdown_fn: Callable[[str], None],
    info_fn: Callable[[str], None],
    build_assistant_chat_message_fn: Callable[[str, str, bool, str], dict],
) -> tuple[bool, bool, str | None]:
    if not effective_input:
        return False, False, None

    if not (redo_input or bridge_input):
        session_state.chat_history.append({"role": "user", "content": effective_input})
    user_input = effective_input
    generation_input = user_input
    route_pick = _chat_route_mode(session_state)

    if not api_key and "ollama" not in model_name:
        err = "❌ 请在左侧边栏填入 API Key 后再试。"
        session_state.chat_history.append({"role": "assistant", "content": err})
        return True, True, None

    msg = None
    intent = "CHAT"
    session_state.agent_running = True
    try:
        intent, gdl_obj_name = route_main_input_fn(
            user_input,
            project_loaded=bool(session_state.project),
            has_image=False,
        )
        if route_pick == CHAT_ROUTE_FORCE_DEBUG:
            intent = "DEBUG"
            if not generation_input.startswith("[DEBUG:"):
                generation_input = f"[DEBUG:editor] {generation_input}"
        elif route_pick == CHAT_ROUTE_FORCE_GENERATE and intent in ("CHAT", "DEBUG"):
            intent = "MODIFY" if session_state.project else "CREATE"
            generation_input = f"{FORCE_GENERATE_PREFIX} {generation_input}"

        with live_output.container():
            import streamlit as st

            render_user_bubble(st, user_input)
            if intent == "CHAT":
                msg = chat_respond_fn(
                    user_input,
                    session_state.chat_history[:-1],
                    None,
                )
                render_assistant_block(st, msg)
            else:
                should_skip_elicitation = should_skip_elicitation_fn(user_input, intent)
                if should_skip_elicitation:
                    proj_current = session_state.project
                    if not proj_current:
                        fallback_name = gdl_obj_name or session_state.pending_gsm_name or "untitled"
                        proj_current = create_project_fn(fallback_name)
                        session_state.project = proj_current
                        session_state.pending_gsm_name = fallback_name
                        session_state.script_revision = 0

                    effective_gsm = session_state.pending_gsm_name or proj_current.name
                    msg = run_agent_generate_fn(
                        generation_input,
                        proj_current,
                        st.container(),
                        effective_gsm,
                        True,
                    )
                    render_assistant_block(st, msg)
                else:
                    elicitation_msg, eliciting = handle_elicitation_route_fn(user_input, gdl_obj_name)
                    if eliciting:
                        msg = elicitation_msg
                        render_assistant_block(st, msg)
                    else:
                        if not session_state.project:
                            new_proj = create_project_fn(gdl_obj_name)
                            session_state.project = new_proj
                            session_state.pending_gsm_name = gdl_obj_name
                            session_state.script_revision = 0
                            info_fn(f"📁 已初始化项目 `{gdl_obj_name}`")

                        proj_current = session_state.project
                        effective_gsm = session_state.pending_gsm_name or proj_current.name
                        msg = run_agent_generate_fn(
                            generation_input,
                            proj_current,
                            st.container(),
                            effective_gsm,
                            True,
                        )
                        render_assistant_block(st, msg)

        session_state.chat_history.append(
            build_assistant_chat_message_fn(
                content=msg,
                intent=intent,
                has_project=bool(session_state.get("project")),
                source_user_input=user_input,
            )
        )
        return True, True, None
    finally:
        session_state.agent_running = False


def run_vision_path(
    *,
    has_image_input: bool,
    vision_mime: str | None,
    vision_name: str | None,
    user_input: str | None,
    active_debug_mode,
    vision_b64: str,
    session_state,
    api_key: str,
    model_name: str,
    resolve_image_route_mode_fn: Callable[[str, object, str, str], str],
    build_image_user_display_fn: Callable[[str, str, str], str],
    live_output,
    create_project_fn: Callable[[str], object],
    has_any_script_content_fn: Callable[[object], bool],
    thumb_image_bytes_fn: Callable[[str], bytes | None],
    image_fn: Callable[..., None],
    markdown_fn: Callable[[str], None],
    run_vision_generate_fn: Callable[[str, str, str, object, object, bool], str],
    run_agent_generate_with_debug_image_fn: Callable[[str, object, object, str, bool, str, str], str],
) -> tuple[bool, bool, str | None]:
    if not has_image_input:
        return False, False, None

    final_mime = vision_mime or "image/jpeg"
    final_name = vision_name or "image"
    joined_text = (user_input or "").strip()
    route_pick = _chat_route_mode(session_state)
    route_mode = resolve_image_route_mode_fn(route_pick, active_debug_mode, joined_text, final_name)
    user_display = build_image_user_display_fn(final_name, route_mode, joined_text)

    session_state.chat_history.append({
        "role": "user",
        "content": user_display,
        "image_b64": vision_b64,
        "image_mime": final_mime,
        "image_name": final_name,
    })

    if not api_key and "ollama" not in model_name:
        err = "❌ 请在左侧边栏填入 API Key 后再试。"
        session_state.chat_history.append({"role": "assistant", "content": err})
        return True, True, None

    session_state.agent_running = True
    try:
        if not session_state.project:
            vname = final_name.rsplit(".", 1)[0] or "vision_object"
            vproj = create_project_fn(vname)
            session_state.project = vproj
            session_state.pending_gsm_name = vname
            session_state.script_revision = 0

        proj_v = session_state.project

        with live_output.container():
            import streamlit as st

            img_bytes = thumb_image_bytes_fn(vision_b64)
            render_user_bubble(st, user_display, image_bytes=img_bytes)
            if route_mode == "generate":
                msg = run_vision_generate_fn(
                    vision_b64,
                    final_mime,
                    joined_text,
                    proj_v,
                    st.container(),
                    True,
                )
            else:
                debug_req = joined_text or "请根据这张截图定位并修复当前项目中的问题。"
                if not debug_req.startswith("[DEBUG:"):
                    debug_req = f"[DEBUG:editor] {debug_req}"
                msg = run_agent_generate_with_debug_image_fn(
                    debug_req,
                    proj_v,
                    st.container(),
                    session_state.pending_gsm_name or proj_v.name,
                    True,
                    vision_b64,
                    final_mime,
                )
            render_assistant_block(st, msg)

        session_state.chat_history.append({"role": "assistant", "content": msg})
        return True, True, None
    finally:
        session_state.agent_running = False


def _chat_route_mode(session_state) -> str:
    return session_state.get("chat_route_mode") or session_state.get("chat_image_route_mode", CHAT_ROUTE_AUTO)


def process_chat_turn(
    *,
    st,
    session_state,
    chat_payload: dict,
    api_key: str,
    model_name: str,
    resolve_bridge_input_fn: Callable[[object, str | None, list[dict], bool], str | None],
    resolve_effective_input_fn: Callable[[object, str | None, bool, str | None, str | None, str | None], tuple[str | None, bool, bool]],
    detect_gsm_name_candidate_fn: Callable[[str], str | None],
    handle_tapir_test_trigger_fn: Callable[[bool], tuple[bool, bool]],
    handle_tapir_selection_trigger_fn: Callable[[bool], tuple[bool, bool]],
    handle_tapir_highlight_trigger_fn: Callable[[bool], tuple[bool, bool]],
    handle_tapir_load_params_trigger_fn: Callable[[bool], tuple[bool, bool]],
    handle_tapir_apply_params_trigger_fn: Callable[[bool], tuple[bool, bool]],
    run_vision_path_fn: Callable[..., tuple[bool, bool, str | None]],
    run_normal_text_path_fn: Callable[..., tuple[bool, bool, str | None]],
) -> None:
    runtime = pop_chat_runtime_state(session_state=session_state, has_image_input=bool(chat_payload.get("vision_b64")))
    user_input = chat_payload.get("user_input")
    live_output = chat_payload["live_output"]
    chat_start_index = len(session_state.get("chat_history", []))
    maybe_record_conversation_error_learning(session_state, user_input)

    _handled, _should_rerun = handle_tapir_test_trigger_fn(runtime["tapir_trigger"])
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = handle_tapir_selection_trigger_fn(runtime["tapir_selection_trigger"])
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = handle_tapir_highlight_trigger_fn(runtime["tapir_highlight_trigger"])
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = handle_tapir_load_params_trigger_fn(runtime["tapir_load_params_trigger"])
    if _handled and _should_rerun:
        st.rerun()

    _handled, _should_rerun = handle_tapir_apply_params_trigger_fn(runtime["tapir_apply_params_trigger"])
    if _handled and _should_rerun:
        st.rerun()

    auto_debug_input = session_state.pop("_auto_debug_input", None)
    bridge_input = resolve_bridge_input_fn(
        runtime["pending_bridge_idx"],
        user_input,
        session_state.get("chat_history", []),
        bool(session_state.get("project")),
    )
    effective_input, clear_debug_mode, toast_missing_debug_text = resolve_effective_input_fn(
        runtime["active_debug_mode"],
        user_input,
        runtime["has_image_input"],
        auto_debug_input,
        bridge_input,
        runtime["redo_input"],
    )
    if clear_debug_mode:
        session_state["_debug_mode_active"] = None
    if toast_missing_debug_text:
        st.toast("请输入问题描述后再发送，或直接描述你看到的现象", icon="💬")

    if user_input and not (session_state.pending_gsm_name or "").strip():
        gsm_candidate = detect_gsm_name_candidate_fn(user_input)
        if gsm_candidate:
            session_state.pending_gsm_name = gsm_candidate

    if runtime["has_image_input"]:
        handled, should_rerun, err_msg = run_vision_path_fn(
            runtime["has_image_input"],
            chat_payload["vision_mime"],
            chat_payload["vision_name"],
            user_input,
            runtime["active_debug_mode"],
            chat_payload["vision_b64"],
            live_output,
            api_key,
            model_name,
        )
        if err_msg:
            st.error(err_msg)
        persist_new_chat_messages(session_state, chat_start_index)
        if handled and should_rerun:
            st.rerun()
    elif effective_input:
        handled, should_rerun, err_msg = run_normal_text_path_fn(
            effective_input,
            runtime["redo_input"],
            bridge_input,
            live_output,
            api_key,
            model_name,
        )
        if err_msg:
            st.error(err_msg)
        persist_new_chat_messages(session_state, chat_start_index)
        if handled and should_rerun:
            st.rerun()
