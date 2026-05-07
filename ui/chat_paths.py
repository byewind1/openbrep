from __future__ import annotations

from typing import Callable

from ui.chat_render import render_assistant_block, render_user_bubble
from ui.generation_service import FORCE_GENERATE_PREFIX


CHAT_ROUTE_AUTO = "自动"
CHAT_ROUTE_FORCE_GENERATE = "强制生成"
CHAT_ROUTE_FORCE_DEBUG = "强制调试"


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
