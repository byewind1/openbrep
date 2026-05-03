from __future__ import annotations

import base64
from typing import Callable

from ui.chat_render import render_assistant_block, render_user_bubble
from ui.chat_history_actions import (
    build_chat_record_entries,
    sanitize_hsf_name,
    suggest_hsf_name_from_chat_record,
)
from ui.project_activity import is_project_activity_message


def render_chat_panel(
    st,
    *,
    is_generation_locked_fn: Callable[[object], bool],
    build_chat_script_anchors_fn: Callable[[list[dict]], list[dict]],
    extract_gsm_name_candidate_fn: Callable[[str], str | None],
    thumb_image_bytes_fn: Callable[[str], bytes | None],
    copyable_chat_text_fn: Callable[[dict], str],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
    extract_gdl_from_text_fn: Callable[[str], dict],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    create_project_fn: Callable[[str], object],
    validate_chat_image_size_fn: Callable[[bytes, str], str | None],
) -> dict:
    st.markdown("### AI 助手（生成与调试）")
    _render_header(st)
    _render_chat_record_browser(
        st,
        extract_gdl_from_text_fn=extract_gdl_from_text_fn,
        extract_gsm_name_candidate_fn=extract_gsm_name_candidate_fn,
        capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
        apply_scripts_to_project_fn=apply_scripts_to_project_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
        create_project_fn=create_project_fn,
        copy_text_to_system_clipboard_fn=copy_text_to_system_clipboard_fn,
    )
    _render_history_anchors(st, build_chat_script_anchors_fn=build_chat_script_anchors_fn)
    _render_chat_history(
        st,
        thumb_image_bytes_fn=thumb_image_bytes_fn,
        copyable_chat_text_fn=copyable_chat_text_fn,
        copy_text_to_system_clipboard_fn=copy_text_to_system_clipboard_fn,
        is_bridgeable_explainer_message_fn=is_bridgeable_explainer_message_fn,
    )
    _render_adopt_dialog(
        st,
        extract_gdl_from_text_fn=extract_gdl_from_text_fn,
        capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
        apply_scripts_to_project_fn=apply_scripts_to_project_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
    )
    live_output = st.empty()
    active_debug_mode = _render_route_controls(st)
    payload = _read_chat_input(st, is_generation_locked_fn=is_generation_locked_fn)
    payload["live_output"] = live_output
    payload["active_debug_mode"] = active_debug_mode
    _decode_chat_attachment(st, payload, validate_chat_image_size_fn=validate_chat_image_size_fn)
    return payload


def _render_header(st) -> None:
    st.caption("描述需求，AI 自动创建 GDL 对象写入编辑器")


def _render_chat_record_browser(
    st,
    *,
    extract_gdl_from_text_fn: Callable[[str], dict],
    extract_gsm_name_candidate_fn: Callable[[str], str | None],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    create_project_fn: Callable[[str], object],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
) -> None:
    entries = build_chat_record_entries(st.session_state.chat_history, classify_code_blocks_fn=extract_gdl_from_text_fn)
    if not entries:
        return

    with st.expander(f"聊天记录（{len(entries)}）", expanded=False):
        with st.container(height=220, border=True):
            for entry in reversed(entries):
                idx = entry["index"]
                summary = entry["summary"]
                role_label = entry["role_label"]
                cols = st.columns([5, 1])
                with cols[0]:
                    st.caption(f"{role_label} · {summary}")
                with cols[1]:
                    if st.button("打开", key=f"chat_record_open_{idx}", width="stretch"):
                        st.session_state.chat_record_open_idx = idx
                if idx > 0:
                    st.divider()

    if st.session_state.get("chat_record_open_idx") is not None:
        _render_chat_record_dialog(
            st,
            extract_gdl_from_text_fn=extract_gdl_from_text_fn,
            extract_gsm_name_candidate_fn=extract_gsm_name_candidate_fn,
            capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
            apply_scripts_to_project_fn=apply_scripts_to_project_fn,
            bump_main_editor_version_fn=bump_main_editor_version_fn,
            create_project_fn=create_project_fn,
            copy_text_to_system_clipboard_fn=copy_text_to_system_clipboard_fn,
        )


def _apply_chat_record_to_editor(
    *,
    st,
    msg_idx: int,
    extracted: dict,
    hsf_name: str,
    create_project_fn: Callable[[str], object],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    save_as_hsf: bool,
) -> tuple[bool, str]:
    if not extracted:
        return False, "未找到可注入的代码块"

    project = st.session_state.get("project")
    safe_name = sanitize_hsf_name(hsf_name, fallback="chat_hsf")
    if save_as_hsf or project is None:
        if project is not None:
            capture_last_project_snapshot_fn("聊天记录保存")
        project = create_project_fn(safe_name)
        st.session_state.project = project
        st.session_state.pending_gsm_name = safe_name
        st.session_state.script_revision = 0
    else:
        capture_last_project_snapshot_fn("聊天记录注入")

    applied_scripts, applied_params = apply_scripts_to_project_fn(project, extracted)
    if save_as_hsf:
        project.save_to_disk()
    bump_main_editor_version_fn()
    st.session_state.chat_record_open_idx = None
    st.session_state.adopted_msg_index = msg_idx
    label = "保存并注入" if save_as_hsf else "已注入编辑器"
    return True, f"✅ {label}：{applied_scripts} 个脚本，{applied_params} 组参数"


def _render_chat_record_dialog(
    st,
    *,
    extract_gdl_from_text_fn: Callable[[str], dict],
    extract_gsm_name_candidate_fn: Callable[[str], str | None],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    create_project_fn: Callable[[str], object],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
) -> None:
    idx = st.session_state.get("chat_record_open_idx")
    if idx is None:
        return
    history = st.session_state.get("chat_history", [])
    if not (0 <= idx < len(history)):
        st.session_state.chat_record_open_idx = None
        return

    msg = history[idx]
    record_role = "用户" if msg.get("role") == "user" else "助手"
    record_text = st.session_state.get(f"chat_record_text_{idx}") or str(msg.get("content") or "")
    extracted = extract_gdl_from_text_fn(record_text)
    suggested_name = suggest_hsf_name_from_chat_record(
        history,
        idx,
        extract_gsm_name_candidate_fn=extract_gsm_name_candidate_fn,
    )
    name_key = f"chat_record_hsf_name_{idx}"
    if not st.session_state.get(name_key):
        st.session_state[name_key] = suggested_name

    @st.dialog(f"📂 聊天记录 · {record_role}")
    def _dialog(msg_idx: int) -> None:
        edited_text = st.text_area(
            "记录内容",
            value=record_text,
            height=220,
            key=f"chat_record_text_{msg_idx}",
            label_visibility="visible",
        )
        edited_extracted = extract_gdl_from_text_fn(edited_text)
        st.text_input(
            "HSF 名称",
            value=sanitize_hsf_name(st.session_state.get(name_key, suggested_name)),
            key=name_key,
            help="保存到 HSF 文件夹时使用的名称",
        )
        st.caption("代码块提取")
        if edited_extracted:
            for path, code in edited_extracted.items():
                st.text_area(
                    path,
                    value=code,
                    height=180,
                    key=f"chat_record_code_{msg_idx}_{path}",
                )
        else:
            st.info("这条记录里没有可识别的代码块")

        action_cols = st.columns([1, 1, 1])
        with action_cols[0]:
            if st.button("📋 复制全文", width="stretch"):
                ok, copy_msg = copy_text_to_system_clipboard_fn(edited_text)
                if ok:
                    st.toast(copy_msg, icon="✅")
                else:
                    st.warning(copy_msg)
        with action_cols[1]:
            if st.button("📥 注入编辑器", type="primary", width="stretch"):
                ok, msg_text = _apply_chat_record_to_editor(
                    st=st,
                    msg_idx=msg_idx,
                    extracted=edited_extracted,
                    hsf_name=st.session_state.get(name_key, suggested_name),
                    create_project_fn=create_project_fn,
                    capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
                    apply_scripts_to_project_fn=apply_scripts_to_project_fn,
                    bump_main_editor_version_fn=bump_main_editor_version_fn,
                    save_as_hsf=False,
                )
                if ok:
                    st.toast(msg_text, icon="📥")
                    st.rerun()
                else:
                    st.error(msg_text)
        with action_cols[2]:
            if st.button("💾 保存为 HSF", width="stretch"):
                ok, msg_text = _apply_chat_record_to_editor(
                    st=st,
                    msg_idx=msg_idx,
                    extracted=edited_extracted,
                    hsf_name=st.session_state.get(name_key, suggested_name),
                    create_project_fn=create_project_fn,
                    capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
                    apply_scripts_to_project_fn=apply_scripts_to_project_fn,
                    bump_main_editor_version_fn=bump_main_editor_version_fn,
                    save_as_hsf=True,
                )
                if ok:
                    st.toast(msg_text, icon="💾")
                    st.rerun()
                else:
                    st.error(msg_text)

        if st.button("关闭", width="stretch"):
            st.session_state.chat_record_open_idx = None
            st.rerun()

    _dialog(idx)


def _render_history_anchors(st, *, build_chat_script_anchors_fn: Callable[[list[dict]], list[dict]]) -> None:
    anchors = build_chat_script_anchors_fn(st.session_state.chat_history)
    if not anchors:
        return

    st.caption("🧭 历史锚点（点击快速定位）")
    anchor_cols = st.columns([1.8, 4.2, 1.2])
    with anchor_cols[0]:
        options = [anchor["label"] for anchor in anchors]
        default_idx = 0
        focus = st.session_state.get("chat_anchor_focus")
        if isinstance(focus, int):
            for idx, anchor in enumerate(anchors):
                if anchor["msg_idx"] == focus:
                    default_idx = idx
                    break
        selected = st.selectbox(
            "历史锚点",
            options,
            index=default_idx,
            label_visibility="collapsed",
            key="chat_anchor_select",
        )
    picked = next((anchor for anchor in anchors if anchor["label"] == selected), anchors[-1])
    with anchor_cols[1]:
        st.caption(f"范围: {', '.join(picked['paths'])}")
    with anchor_cols[2]:
        if st.button("📍 定位", width="stretch", key="chat_anchor_go"):
            st.session_state.chat_anchor_pending = picked["msg_idx"]


def _render_chat_history(
    st,
    *,
    thumb_image_bytes_fn: Callable[[str], bytes | None],
    copyable_chat_text_fn: Callable[[dict], str],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
) -> None:
    for idx, msg in enumerate(st.session_state.chat_history):
        is_focus = st.session_state.get("chat_anchor_focus") == idx
        if is_focus:
            st.markdown("<div style='border-top:1px dashed #38bdf8;margin:0.4rem 0;'></div>", unsafe_allow_html=True)
            st.caption("📍 当前锚点")

        role = msg.get("role", "assistant")
        if role == "assistant" and is_project_activity_message(msg.get("content", "")):
            continue

        is_user = role == "user"
        left, right = st.columns([1, 5]) if is_user else st.columns([5, 1])
        target = right if is_user else left

        with target:
            if is_user:
                img_bytes = None
                if msg.get("image_b64"):
                    img_bytes = thumb_image_bytes_fn(msg.get("image_b64", ""))
                render_user_bubble(st, msg.get("content", ""), image_bytes=img_bytes)
            else:
                render_assistant_block(st, msg.get("content", ""))

            if role == "assistant":
                _render_assistant_message_actions(
                    st,
                    idx,
                    msg,
                    copyable_chat_text_fn=copyable_chat_text_fn,
                    copy_text_to_system_clipboard_fn=copy_text_to_system_clipboard_fn,
                    is_bridgeable_explainer_message_fn=is_bridgeable_explainer_message_fn,
                )
            else:
                if st.button("打开", key=f"chat_record_inline_open_{idx}", help="查看并回放这条记录"):
                    st.session_state.chat_record_open_idx = idx


def _render_assistant_message_actions(
    st,
    idx: int,
    msg: dict,
    *,
    copyable_chat_text_fn: Callable[[dict], str],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
) -> None:
    copy_col, redo_col, open_col, action_col = st.columns([1, 1, 1, 9])
    with copy_col:
        if st.button("📋", key=f"copy_{idx}", help="复制本条回复"):
            copy_text = copyable_chat_text_fn(msg)
            ok, copy_msg = copy_text_to_system_clipboard_fn(copy_text)
            if ok:
                st.toast(copy_msg, icon="✅")
            else:
                st.warning(copy_msg)
    with redo_col:
        prev_user = next(
            (
                st.session_state.chat_history[j]["content"]
                for j in range(idx - 1, -1, -1)
                if st.session_state.chat_history[j]["role"] == "user"
            ),
            None,
        )
        if prev_user and st.button("🔄", key=f"redo_{idx}", help="重新生成"):
            st.session_state.chat_history = st.session_state.chat_history[:idx]
            st.session_state["_redo_input"] = prev_user
            st.rerun()
    with open_col:
        if st.button("📂", key=f"open_{idx}", help="打开聊天记录"):
            st.session_state.chat_record_open_idx = idx
    with action_col:
        _render_assistant_primary_action(
            st,
            idx,
            msg,
            is_bridgeable_explainer_message_fn=is_bridgeable_explainer_message_fn,
        )


def _render_assistant_primary_action(
    st,
    idx: int,
    msg: dict,
    *,
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
) -> None:
    has_code = "```" in msg.get("content", "")
    is_bridgeable = is_bridgeable_explainer_message_fn(msg)
    if has_code:
        msg_raw = msg.get("content", "")
        has_full_suite = "scripts/3d.gdl" in msg_raw and "paramlist.xml" in msg_raw
        if has_full_suite:
            is_adopted = st.session_state.adopted_msg_index == idx
            adopt_label = "✅ 已采用" if is_adopted else "📥 采用这套"
            if st.button(adopt_label, key=f"adopt_{idx}", width="stretch"):
                st.session_state["_pending_adopt_idx"] = idx
    elif is_bridgeable:
        if st.button("🪄 按此说明修改", key=f"bridge_modify_{idx}", width="stretch"):
            st.session_state["_pending_bridge_idx"] = idx
            st.rerun()


def _render_adopt_dialog(
    st,
    *,
    extract_gdl_from_text_fn: Callable[[str], dict],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
) -> None:
    @st.dialog("📥 采用这套代码")
    def adopt_confirm_dialog(msg_idx):
        st.warning("将按返回文件覆盖：命中的脚本/参数全覆盖写入，未命中的部分保留不变，确认？")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("✅ 确认覆盖", type="primary", width="stretch"):
                msg_content = st.session_state.chat_history[msg_idx]["content"]
                extracted = extract_gdl_from_text_fn(msg_content)
                if extracted:
                    if st.session_state.project:
                        capture_last_project_snapshot_fn("聊天代码采纳")
                        apply_scripts_to_project_fn(st.session_state.project, extracted)
                    bump_main_editor_version_fn()
                    st.session_state.adopted_msg_index = msg_idx
                    st.session_state["_pending_adopt_idx"] = None
                    st.toast("✅ 已写入编辑器", icon="📥")
                    st.rerun()
                else:
                    st.error("未找到可提取的代码块")
        with cancel_col:
            if st.button("❌ 取消", width="stretch"):
                st.session_state["_pending_adopt_idx"] = None
                st.rerun()

    if st.session_state.get("_pending_adopt_idx") is not None:
        adopt_confirm_dialog(st.session_state["_pending_adopt_idx"])


def _render_route_controls(st) -> str | None:
    st.session_state["_debug_mode_active"] = None
    st.radio(
        "AI 模式",
        ["自动", "强制生成", "强制调试"],
        horizontal=True,
        key="chat_route_mode",
    )
    return None


def _read_chat_input(st, *, is_generation_locked_fn: Callable[[object], bool]) -> dict:
    if st.session_state.agent_running:
        st.info("⏳ AI 生成中，请稍候...")
    chat_payload = st.chat_input(
        "描述需求、提问，或搭配图片补充说明…",
        key="chat_main_input",
        accept_file=True,
        file_type=["jpg", "jpeg", "png", "webp", "gif"],
        disabled=is_generation_locked_fn(st.session_state),
    )
    return {
        "chat_payload": chat_payload,
        "user_input": chat_payload if isinstance(chat_payload, str) else None,
        "vision_b64": None,
        "vision_mime": None,
        "vision_name": None,
    }


def _decode_chat_attachment(
    st,
    payload: dict,
    *,
    validate_chat_image_size_fn: Callable[[bytes, str], str | None],
) -> None:
    chat_payload = payload["chat_payload"]
    if isinstance(chat_payload, str) or chat_payload is None:
        return

    payload["user_input"] = chat_payload.get("text", "") or ""
    chat_files = chat_payload.get("files", []) or []
    if not chat_files:
        return

    img = chat_files[0]
    raw_bytes = img.read()
    if not raw_bytes:
        return

    image_name = getattr(img, "name", "image") or "image"
    size_error = validate_chat_image_size_fn(raw_bytes, image_name)
    if size_error:
        st.session_state.chat_history.append({"role": "assistant", "content": f"❌ {size_error}"})
        st.error(size_error)
        st.rerun()

    payload["vision_b64"] = base64.b64encode(raw_bytes).decode()
    payload["vision_mime"] = getattr(img, "type", "") or "image/jpeg"
    payload["vision_name"] = image_name
