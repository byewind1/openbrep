from __future__ import annotations

import base64
from typing import Callable

from ui.chat_render import render_assistant_block, render_user_bubble
from ui.proposed_preview_controller import clear_pending_preview_state


IMPORT_STATUS_MAX_HEIGHT = 180


def render_chat_panel(
    st,
    *,
    script_map: list[tuple[object, str, str]],
    is_generation_locked_fn: Callable[[object], bool],
    build_chat_script_anchors_fn: Callable[[list[dict]], list[dict]],
    thumb_image_bytes_fn: Callable[[str], bytes | None],
    save_feedback_fn: Callable[[int, str, str, str], None],
    copyable_chat_text_fn: Callable[[dict], str],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
    extract_gdl_from_text_fn: Callable[[str], dict],
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    parse_paramlist_text_fn: Callable[[str], list],
    restore_last_project_snapshot_fn: Callable[[], tuple[bool, str]],
    validate_chat_image_size_fn: Callable[[bytes, str], str | None],
    check_gdl_script_fn: Callable[[str, str], list[str]] | None = None,
    run_pending_preview_fn: Callable[[object, str], tuple[bool, str]] | None = None,
    run_pending_compile_fn: Callable[[object], tuple[bool, str]] | None = None,
    render_preview_2d_fn: Callable[[object], None] | None = None,
    render_preview_3d_fn: Callable[[object], None] | None = None,
    do_compile_fn: Callable[[object, str, str], tuple[bool, str]] | None = None,
    save_revision_fn: Callable[[object, str, str | None], tuple[bool, str]] | None = None,
) -> dict:
    st.markdown("### AI 助手（生成与调试）")
    _render_header(st)
    _render_history_anchors(st, build_chat_script_anchors_fn=build_chat_script_anchors_fn)
    _render_chat_history(
        st,
        thumb_image_bytes_fn=thumb_image_bytes_fn,
        save_feedback_fn=save_feedback_fn,
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
    _render_pending_diffs(
        st,
        script_map=script_map,
        parse_paramlist_text_fn=parse_paramlist_text_fn,
        check_gdl_script_fn=check_gdl_script_fn,
        capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
        apply_scripts_to_project_fn=apply_scripts_to_project_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
        restore_last_project_snapshot_fn=restore_last_project_snapshot_fn,
        run_pending_preview_fn=run_pending_preview_fn,
        run_pending_compile_fn=run_pending_compile_fn,
        render_preview_2d_fn=render_preview_2d_fn,
        render_preview_3d_fn=render_preview_3d_fn,
        do_compile_fn=do_compile_fn,
        save_revision_fn=save_revision_fn,
    )
    live_output = st.empty()
    active_debug_mode = _render_debug_and_route_controls(st)
    payload = _read_chat_input(st, is_generation_locked_fn=is_generation_locked_fn)
    payload["live_output"] = live_output
    payload["active_debug_mode"] = active_debug_mode
    _decode_chat_attachment(st, payload, validate_chat_image_size_fn=validate_chat_image_size_fn)
    return payload


def _render_header(st) -> None:
    title_col, clear_col = st.columns([3, 1])
    with title_col:
        st.caption("描述需求，AI 自动创建 GDL 对象写入编辑器")
    with clear_col:
        if st.button("🗑️ 清空对话", width="stretch", help="清空聊天记录，不影响脚本和参数"):
            st.session_state.chat_history = []
            st.session_state.adopted_msg_index = None
            st.session_state.chat_anchor_focus = None
            st.rerun()


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
    save_feedback_fn: Callable[[int, str, str, str], None],
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
                _render_assistant_history_content(st, msg.get("content", ""))

            if role == "assistant":
                _render_assistant_message_actions(
                    st,
                    idx,
                    msg,
                    save_feedback_fn=save_feedback_fn,
                    copyable_chat_text_fn=copyable_chat_text_fn,
                    copy_text_to_system_clipboard_fn=copy_text_to_system_clipboard_fn,
                    is_bridgeable_explainer_message_fn=is_bridgeable_explainer_message_fn,
                )


def _render_assistant_history_content(st, content: str) -> None:
    if _is_project_import_status_message(content):
        with st.container(height=IMPORT_STATUS_MAX_HEIGHT, border=True):
            st.caption("项目导入 / 加载记录")
            render_assistant_block(st, content)
        return

    render_assistant_block(st, content)


def _is_project_import_status_message(content: str) -> bool:
    text = str(content or "").lstrip()
    if text.startswith(("✅ 已导入", "✅ 已加载 HSF 项目", "❌ [IMPORT_GSM]")):
        return True
    return "HSF 文件列表" in text or "源目录:" in text


def _render_assistant_message_actions(
    st,
    idx: int,
    msg: dict,
    *,
    save_feedback_fn: Callable[[int, str, str, str], None],
    copyable_chat_text_fn: Callable[[dict], str],
    copy_text_to_system_clipboard_fn: Callable[[str], tuple[bool, str]],
    is_bridgeable_explainer_message_fn: Callable[[dict], bool],
) -> None:
    like_col, dislike_col, copy_col, redo_col, action_col = st.columns([1, 1, 1, 1, 8])
    with like_col:
        if st.button("👍", key=f"like_{idx}", help="有帮助"):
            save_feedback_fn(idx, "positive", msg["content"], "")
            st.toast("已记录 👍", icon="✅")
    with dislike_col:
        if st.button("👎", key=f"dislike_{idx}", help="需改进"):
            st.session_state[f"_show_dislike_{idx}"] = True
    _render_dislike_form(st, idx, msg, save_feedback_fn=save_feedback_fn)
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
    with action_col:
        _render_assistant_primary_action(
            st,
            idx,
            msg,
            is_bridgeable_explainer_message_fn=is_bridgeable_explainer_message_fn,
        )


def _render_dislike_form(st, idx: int, msg: dict, *, save_feedback_fn: Callable[[int, str, str, str], None]) -> None:
    if not st.session_state.get(f"_show_dislike_{idx}"):
        return

    with st.container():
        feedback_text = st.text_area(
            "描述问题（可选）",
            key=f"dislike_text_{idx}",
            placeholder="哪里不对？期望的结果是什么？",
            height=80,
            label_visibility="collapsed",
        )
        submit_col, cancel_col = st.columns([1, 1])
        with submit_col:
            if st.button("📤 提交", key=f"dislike_submit_{idx}", type="primary", width="stretch"):
                save_feedback_fn(idx, "negative", msg["content"], feedback_text)
                st.session_state[f"_show_dislike_{idx}"] = False
                st.toast("已记录 👎，感谢反馈", icon="📝")
                st.rerun()
        with cancel_col:
            if st.button("取消", key=f"dislike_cancel_{idx}", width="stretch"):
                st.session_state[f"_show_dislike_{idx}"] = False
                st.rerun()


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


def _render_pending_diffs(
    st,
    *,
    script_map: list[tuple[object, str, str]],
    parse_paramlist_text_fn: Callable[[str], list],
    check_gdl_script_fn: Callable[[str, str], list[str]] | None,
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    restore_last_project_snapshot_fn: Callable[[], tuple[bool, str]],
    run_pending_preview_fn: Callable[[object, str], tuple[bool, str]] | None,
    run_pending_compile_fn: Callable[[object], tuple[bool, str]] | None,
    render_preview_2d_fn: Callable[[object], None] | None,
    render_preview_3d_fn: Callable[[object], None] | None,
    do_compile_fn: Callable[[object, str, str], tuple[bool, str]] | None,
    save_revision_fn: Callable[[object, str, str | None], tuple[bool, str]] | None,
) -> None:
    if not st.session_state.pending_diffs:
        return

    pending_diffs = st.session_state.pending_diffs
    script_count = sum(1 for key in pending_diffs if key.startswith("scripts/"))
    param_count = len(parse_paramlist_text_fn(pending_diffs.get("paramlist.xml", "")))
    covered = sorted([key for key in pending_diffs.keys() if key.startswith("scripts/") or key == "paramlist.xml"])
    all_targets = [path for _, path, _ in script_map] + ["paramlist.xml"]
    kept = [path for path in all_targets if path not in covered]
    covered_txt = "、".join(covered) if covered else "（无）"
    kept_txt = "、".join(kept) if kept else "（无）"

    st.info(
        f"⬆️ **Review：AI 提议写入 {script_count} 个脚本、{param_count} 个参数**\n"
        f"覆盖：`{covered_txt}`\n"
        f"保留：`{kept_txt}`"
    )

    static_issues = _pending_static_issues(
        pending_diffs,
        script_map=script_map,
        check_gdl_script_fn=check_gdl_script_fn,
    )
    status_col_1, status_col_2, status_col_3, status_col_4, status_col_5 = st.columns(5)
    with status_col_1:
        st.caption("Draft：待写入")
    with status_col_2:
        if static_issues:
            st.caption(f"Static：{len(static_issues)} 条提示")
        else:
            st.caption("Static：通过")
    with status_col_3:
        pending_preview_meta = st.session_state.get("pending_preview_meta") or {}
        pending_preview_kind = pending_preview_meta.get("kind", "")
        if pending_preview_kind:
            warn_count = len(st.session_state.get("pending_preview_warnings") or [])
            suffix = f"，{warn_count} 条提示" if warn_count else ""
            st.caption(f"Preview：{pending_preview_kind}{suffix}")
        else:
            st.caption("Preview：未运行")
    with status_col_4:
        pending_compile_result = st.session_state.get("pending_compile_result")
        if pending_compile_result is None:
            st.caption("Preflight：未运行")
        else:
            preflight_ok, _ = pending_compile_result
            st.caption("Preflight：通过" if preflight_ok else "Preflight：失败")
    with status_col_5:
        if st.session_state.compile_result is None:
            st.caption("Compile：未运行")
        else:
            compile_ok, _ = st.session_state.compile_result
            st.caption("Compile：通过" if compile_ok else "Compile：失败")
    if static_issues:
        st.warning("；".join(static_issues[:3]))

    _render_pending_preview_controls(
        st,
        run_pending_preview_fn=run_pending_preview_fn,
        run_pending_compile_fn=run_pending_compile_fn,
        render_preview_2d_fn=render_preview_2d_fn,
        render_preview_3d_fn=render_preview_3d_fn,
    )

    can_promote = do_compile_fn is not None
    draft_col, compile_col, promote_col = st.columns([1.0, 1.1, 1.4])
    with draft_col:
        if st.button("写入草稿", type="primary", width="stretch", key="chat_pending_apply"):
            _apply_pending_review_changes(
                st,
                pending_diffs=pending_diffs,
                script_count=script_count,
                param_count=param_count,
                capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
                apply_scripts_to_project_fn=apply_scripts_to_project_fn,
                bump_main_editor_version_fn=bump_main_editor_version_fn,
            )
            st.rerun()
    with compile_col:
        if st.button(
            "写入并编译",
            width="stretch",
            key="chat_pending_apply_compile",
            disabled=not can_promote,
        ):
            _apply_compile_and_maybe_snapshot(
                st,
                pending_diffs=pending_diffs,
                script_count=script_count,
                param_count=param_count,
                capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
                apply_scripts_to_project_fn=apply_scripts_to_project_fn,
                bump_main_editor_version_fn=bump_main_editor_version_fn,
                do_compile_fn=do_compile_fn,
                save_revision_fn=None,
            )
            st.rerun()
    with promote_col:
        if st.button(
            "写入+编译+保存版本",
            width="stretch",
            key="chat_pending_apply_compile_snapshot",
            disabled=not (can_promote and save_revision_fn is not None),
        ):
            _apply_compile_and_maybe_snapshot(
                st,
                pending_diffs=pending_diffs,
                script_count=script_count,
                param_count=param_count,
                capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
                apply_scripts_to_project_fn=apply_scripts_to_project_fn,
                bump_main_editor_version_fn=bump_main_editor_version_fn,
                do_compile_fn=do_compile_fn,
                save_revision_fn=save_revision_fn,
            )
            st.rerun()

    undo_col, _ = st.columns([1.6, 1.0])
    with undo_col:
        undo_disabled = not bool(st.session_state.get("last_project_snapshot"))
        if st.button("↩ 撤销上次 AI 写入", width="stretch", key="chat_last_ai_undo", disabled=undo_disabled):
            ok, msg = restore_last_project_snapshot_fn()
            if ok:
                st.toast(msg, icon="↩")
            else:
                st.error(msg)
            st.rerun()


def _pending_static_issues(
    pending_diffs: dict,
    *,
    script_map: list[tuple[object, str, str]],
    check_gdl_script_fn: Callable[[str, str], list[str]] | None,
) -> list[str]:
    if check_gdl_script_fn is None:
        return []

    issues: list[str] = []
    for _script_type, fpath, label in script_map:
        if fpath not in pending_diffs:
            continue
        script_key = fpath.replace("scripts/", "").replace(".gdl", "")
        for issue in check_gdl_script_fn(pending_diffs[fpath], script_key):
            if issue.startswith("✅"):
                continue
            issues.append(f"{label}: {issue}")
    return issues


def _apply_pending_review_changes(
    st,
    *,
    pending_diffs: dict,
    script_count: int,
    param_count: int,
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
) -> tuple[int, int]:
    project = st.session_state.project
    if not project:
        st.error("当前没有项目，无法写入 AI 改动。")
        return 0, 0

    capture_last_project_snapshot_fn("AI 确认写入")
    sc, pc = apply_scripts_to_project_fn(project, pending_diffs)
    bump_main_editor_version_fn()
    st.session_state.pending_diffs = {}
    st.session_state.pending_ai_label = ""
    st.session_state.compile_result = None
    clear_pending_preview_state(st.session_state)

    ok_parts = []
    if sc:
        ok_parts.append(f"{sc} 个脚本")
    if pc:
        ok_parts.append(f"{pc} 个参数")
    if not ok_parts:
        if script_count:
            ok_parts.append(f"{script_count} 个脚本")
        if param_count:
            ok_parts.append(f"{param_count} 个参数")
    st.toast(f"✅ 已写入 {'、'.join(ok_parts) if ok_parts else 'AI 改动'}", icon="✏️")
    return sc, pc


def _render_pending_preview_controls(
    st,
    *,
    run_pending_preview_fn: Callable[[object, str], tuple[bool, str]] | None,
    run_pending_compile_fn: Callable[[object], tuple[bool, str]] | None,
    render_preview_2d_fn: Callable[[object], None] | None,
    render_preview_3d_fn: Callable[[object], None] | None,
) -> None:
    if run_pending_preview_fn is None and run_pending_compile_fn is None:
        return

    preview_col_3d, preview_col_2d, compile_col, clear_col = st.columns([1.2, 1.2, 1.2, 1.0])
    with preview_col_3d:
        if st.button(
            "预览提案 3D",
            width="stretch",
            key="chat_pending_preview_3d",
            disabled=run_pending_preview_fn is None,
        ):
            ok, msg = run_pending_preview_fn(st.session_state.project, "3d")
            if ok:
                st.toast(msg, icon="🧊")
            else:
                st.error(msg)
            st.rerun()
    with preview_col_2d:
        if st.button(
            "预览提案 2D",
            width="stretch",
            key="chat_pending_preview_2d",
            disabled=run_pending_preview_fn is None,
        ):
            ok, msg = run_pending_preview_fn(st.session_state.project, "2d")
            if ok:
                st.toast(msg, icon="👁️")
            else:
                st.error(msg)
            st.rerun()
    with compile_col:
        if st.button(
            "验证提案编译",
            width="stretch",
            key="chat_pending_compile_preflight",
            disabled=run_pending_compile_fn is None,
        ):
            ok, msg = run_pending_compile_fn(st.session_state.project)
            if ok:
                st.toast("✅ 提案编译预检通过", icon="🏗️")
            else:
                st.error(msg)
            st.rerun()
    with clear_col:
        has_pending_preview = bool(
            st.session_state.get("pending_preview_2d_data")
            or st.session_state.get("pending_preview_3d_data")
            or st.session_state.get("pending_current_preview_2d_data")
            or st.session_state.get("pending_current_preview_3d_data")
            or st.session_state.get("pending_preview_warnings")
            or st.session_state.get("pending_preview_diff_summary")
            or st.session_state.get("pending_compile_result")
        )
        if st.button(
            "清除预览",
            width="stretch",
            key="chat_pending_preview_clear",
            disabled=not has_pending_preview,
        ):
            clear_pending_preview_state(st.session_state)
            st.rerun()

    _render_pending_compile_result(st)
    _render_pending_preview_panel(
        st,
        render_preview_2d_fn=render_preview_2d_fn,
        render_preview_3d_fn=render_preview_3d_fn,
    )


def _render_pending_preview_panel(
    st,
    *,
    render_preview_2d_fn: Callable[[object], None] | None,
    render_preview_3d_fn: Callable[[object], None] | None,
) -> None:
    meta = st.session_state.get("pending_preview_meta") or {}
    kind = meta.get("kind", "")
    timestamp = meta.get("timestamp", "")
    warnings = st.session_state.get("pending_preview_warnings") or []
    summary = st.session_state.get("pending_preview_diff_summary") or {}
    has_preview = bool(
        st.session_state.get("pending_preview_2d_data")
        or st.session_state.get("pending_preview_3d_data")
        or st.session_state.get("pending_current_preview_2d_data")
        or st.session_state.get("pending_current_preview_3d_data")
        or warnings
        or summary
    )
    if not has_preview:
        return

    title = f"AI 提案预览：{kind} · {timestamp}" if kind else "AI 提案预览"
    with st.expander(title, expanded=True):
        _render_pending_preview_summary(st, summary)
        current_col, proposed_col = st.columns(2)
        with current_col:
            st.caption("Current")
            _render_pending_preview_data(
                st,
                kind=kind,
                data_2d=st.session_state.get("pending_current_preview_2d_data"),
                data_3d=st.session_state.get("pending_current_preview_3d_data"),
                render_preview_2d_fn=render_preview_2d_fn,
                render_preview_3d_fn=render_preview_3d_fn,
            )
        with proposed_col:
            st.caption("Proposed")
            _render_pending_preview_data(
                st,
                kind=kind,
                data_2d=st.session_state.get("pending_preview_2d_data"),
                data_3d=st.session_state.get("pending_preview_3d_data"),
                render_preview_2d_fn=render_preview_2d_fn,
                render_preview_3d_fn=render_preview_3d_fn,
            )

        if warnings:
            st.warning("；".join(warnings[:6]))


def _render_pending_preview_data(
    st,
    *,
    kind: str,
    data_2d,
    data_3d,
    render_preview_2d_fn: Callable[[object], None] | None,
    render_preview_3d_fn: Callable[[object], None] | None,
) -> None:
    if str(kind).upper() == "3D" and render_preview_3d_fn is not None:
        render_preview_3d_fn(data_3d)
        return
    if str(kind).upper() == "2D" and render_preview_2d_fn is not None:
        render_preview_2d_fn(data_2d)
        return
    if data_3d is not None and render_preview_3d_fn is not None:
        render_preview_3d_fn(data_3d)
        return
    if data_2d is not None and render_preview_2d_fn is not None:
        render_preview_2d_fn(data_2d)
        return
    st.info("暂无预览数据。")


def _render_pending_preview_summary(st, summary: dict) -> None:
    if not summary:
        return
    if summary.get("error"):
        st.caption(f"Diff：预览失败 · {summary.get('error')}")
        return

    changed_paths = summary.get("changed_paths") or []
    delta = summary.get("delta") or {}
    delta_text = "，".join(
        f"{key} {value:+d}"
        for key, value in delta.items()
        if isinstance(value, int) and value != 0
    ) or "几何数量无变化"
    warning_delta = summary.get("warning_delta", 0)
    warning_text = f"warnings {warning_delta:+d}" if isinstance(warning_delta, int) else "warnings ?"
    paths_text = "、".join(changed_paths) if changed_paths else "（无）"
    st.caption(f"Diff：{delta_text}；{warning_text}；变更：{paths_text}")
    st.caption(
        "Verify："
        f"current {_preview_status_text(summary.get('current_status'), summary.get('current_facts'))} / "
        f"proposed {_preview_status_text(summary.get('proposed_status'), summary.get('proposed_facts'))}"
    )


def _preview_status_text(status: str | None, facts: dict | None) -> str:
    facts = facts or {}
    status_label = {
        "ok": "OK",
        "warn": "WARN",
        "empty": "EMPTY",
        "error": "ERROR",
    }.get(status or "", status or "?")
    total = facts.get("total_primitives", "?")
    warnings = facts.get("warning_count", "?")
    return f"{status_label} ({total} items, {warnings} warnings)"


def _render_pending_compile_result(st) -> None:
    result = st.session_state.get("pending_compile_result")
    if result is None:
        return
    ok, msg = result
    meta = st.session_state.get("pending_compile_meta") or {}
    compiler_mode = meta.get("compiler_mode", "")
    exit_code = meta.get("exit_code", "")
    caption = f"Preflight：{compiler_mode} · exit={exit_code}"
    if ok:
        st.success(f"{caption}\n\n{msg}")
    else:
        st.error(f"{caption}\n\n{msg}")


def _apply_compile_and_maybe_snapshot(
    st,
    *,
    pending_diffs: dict,
    script_count: int,
    param_count: int,
    capture_last_project_snapshot_fn: Callable[[str], None],
    apply_scripts_to_project_fn: Callable[[object, dict], tuple[int, int]],
    bump_main_editor_version_fn: Callable[[], int],
    do_compile_fn: Callable[[object, str, str], tuple[bool, str]] | None,
    save_revision_fn: Callable[[object, str, str | None], tuple[bool, str]] | None,
) -> None:
    project = st.session_state.project
    if project is None or do_compile_fn is None:
        st.error("当前无法编译 AI 改动。")
        return

    _apply_pending_review_changes(
        st,
        pending_diffs=pending_diffs,
        script_count=script_count,
        param_count=param_count,
        capture_last_project_snapshot_fn=capture_last_project_snapshot_fn,
        apply_scripts_to_project_fn=apply_scripts_to_project_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
    )
    gsm_name = st.session_state.get("pending_gsm_name") or getattr(project, "name", "")
    success, result_msg = do_compile_fn(project, gsm_name, "(review promote)")
    st.session_state.compile_result = (success, result_msg)
    if success:
        st.toast("✅ 编译成功", icon="🏗️")
        if save_revision_fn is not None:
            ok, msg = save_revision_fn(project, f"AI review promote {gsm_name}", gsm_name)
            st.session_state.revision_notice = msg
            if ok:
                st.toast("已保存版本", icon="✅")
            else:
                st.error(msg)
    else:
        st.error(result_msg)


def _render_debug_and_route_controls(st) -> str | None:
    debug_active = st.session_state.get("_debug_mode_active") == "editor"
    debug_label = "✖ 退出 Debug" if debug_active else "🔍 开启 Debug 编辑器"
    if st.button(
        debug_label,
        width="stretch",
        type=("primary" if debug_active else "secondary"),
        key="debug_editor_toggle_btn",
        help="开启后：下次发送将附带编辑器全部脚本+参数+语法检查报告",
    ):
        debug_active = not debug_active
        st.session_state["_debug_mode_active"] = "editor" if debug_active else None

    current_debug = "editor" if debug_active else None
    if current_debug == "editor":
        st.info("🔍 **全脚本 Debug 已激活** — 描述你观察到的问题，或直接发送让 AI 全面检查语法和逻辑")

    st.caption("📎 图片路由（仅附图消息生效）")
    st.radio(
        "图片路由",
        ["自动", "强制生成", "强制调试"],
        horizontal=True,
        key="chat_image_route_mode",
        label_visibility="collapsed",
    )
    return current_debug


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
