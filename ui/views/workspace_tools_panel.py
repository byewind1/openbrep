from __future__ import annotations

import re
from typing import Callable

from openbrep.hsf_project import HSFProject
from openbrep.learning import ErrorLearningStore
from ui.project_activity import is_project_activity_message
from ui.proposed_preview_controller import clear_pending_preview_state


PROJECT_ACTIVITY_MAX_HEIGHT = 180


def render_workspace_tools_panel(
    st,
    proj: HSFProject,
    *,
    tapir_import_ok: bool,
    get_bridge_fn: Callable[[], object],
    script_map: list[tuple[object, str, str]],
    check_gdl_script_fn: Callable[[str, str], list[str]],
    reset_tapir_p0_state_fn: Callable[[], None],
    bump_main_editor_version_fn: Callable[[], int],
) -> None:
    st.markdown("### 验证与联动")
    _render_project_action_buttons(
        st,
        proj,
        script_map=script_map,
        check_gdl_script_fn=check_gdl_script_fn,
    )
    _render_tapir_controls(st, tapir_import_ok=tapir_import_ok, get_bridge_fn=get_bridge_fn)
    _render_memory_privacy_panel(st)
    _render_log_dialog(st)
    _render_clear_confirmation(
        st,
        reset_tapir_p0_state_fn=reset_tapir_p0_state_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
    )


def render_preview_workbench(
    st,
    proj: HSFProject,
    *,
    run_preview_fn: Callable[[HSFProject, str], tuple[bool, str]],
    render_preview_2d_fn: Callable[[object], None],
    render_preview_3d_fn: Callable[[object], None],
) -> None:
    st.markdown("### 预览")
    preview_2d, preview_3d = st.columns(2)
    with preview_2d:
        if st.button("👁️ 预览 2D", width="stretch", help="运行 2D 子集解释并显示图形"):
            ok, msg = run_preview_fn(proj, "2d")
            if ok:
                st.toast(msg, icon="✅")
            else:
                st.error(msg)

    with preview_3d:
        if st.button("🧊 预览 3D", width="stretch", help="运行 3D 子集解释并显示图形"):
            ok, msg = run_preview_fn(proj, "3d")
            if ok:
                st.toast(msg, icon="✅")
            else:
                st.error(msg)

    _render_preview_panel(st, render_preview_2d_fn=render_preview_2d_fn, render_preview_3d_fn=render_preview_3d_fn)
    _render_project_activity_log(st)


def _render_tapir_controls(st, *, tapir_import_ok: bool, get_bridge_fn: Callable[[], object]) -> None:
    st.divider()
    st.markdown("#### Archicad 实机联动")
    if not tapir_import_ok:
        st.caption("未安装 Tapir Python 依赖，实机联动不可用。")
        return

    bridge = get_bridge_fn()
    tapir_ok = bridge.is_available()
    if not tapir_ok:
        st.caption("⚪ Archicad 未运行或 Tapir 未安装，跳过实时测试")
        return

    ac_col1, ac_col2 = st.columns([2, 3])
    with ac_col1:
        if st.button(
            "🏗️ 在 Archicad 中测试",
            width="stretch",
            help="触发 Archicad 重新加载库，捕获 GDL 运行期错误回传到 chat",
        ):
            st.session_state.tapir_test_trigger = True
            st.rerun()
    with ac_col2:
        st.caption("✅ Archicad + Tapir 已连接")

    p0_b1, p0_b2, p0_b3, p0_b4 = st.columns(4)
    with p0_b1:
        if st.button("读取选中", width="stretch", help="同步 Archicad 当前选中的库对象"):
            st.session_state.tapir_selection_trigger = True
            st.rerun()
    with p0_b2:
        if st.button("高亮对象", width="stretch", help="在 Archicad 中高亮当前对象"):
            st.session_state.tapir_highlight_trigger = True
            st.rerun()
    with p0_b3:
        if st.button("读参数", width="stretch", help="从 Archicad 读取当前对象参数"):
            st.session_state.tapir_load_params_trigger = True
            st.rerun()
    with p0_b4:
        can_apply = bool(st.session_state.get("tapir_selected_params"))
        if st.button("写参数", width="stretch", disabled=not can_apply, help="把参数工作台中的值写回 Archicad"):
            st.session_state.tapir_apply_params_trigger = True
            st.rerun()


def _render_project_action_buttons(
    st,
    proj: HSFProject,
    *,
    script_map: list[tuple[object, str, str]],
    check_gdl_script_fn: Callable[[str, str], list[str]],
) -> None:
    st.markdown("#### 本地验证")
    meta_1, meta_2, meta_3, meta_4 = st.columns([1.2, 1.0, 1.0, 1.0])

    with meta_1:
        if st.button("🔍 脚本检查", width="stretch", help="检查当前所有 GDL 脚本的常见语法问题"):
            check_all_ok = True
            for script_type, fpath, label in script_map:
                content = proj.get_script(script_type)
                if not content:
                    continue
                script_key = fpath.replace("scripts/", "").replace(".gdl", "")
                for issue in check_gdl_script_fn(content, script_key):
                    if issue.startswith("✅"):
                        st.success(f"{label}: {issue}")
                    else:
                        st.warning(f"{label}: {issue}")
                        check_all_ok = False
            if check_all_ok:
                st.success("✅ 所有脚本语法正常")

    with meta_2:
        if st.button("🗑️ 重置项目", width="stretch", help="重置项目：脚本、参数、日志全清，保留设置"):
            st.session_state.confirm_clear = True

    with meta_3:
        if st.button("📋 编译日志", width="stretch"):
            st.session_state["_show_log_dialog"] = True

    with meta_4:
        if st.button(
            "🧠 整理错题本",
            width="stretch",
            help="把当前工作区错题记录整理成后续生成会注入的自我提示",
        ):
            result = ErrorLearningStore(st.session_state.work_dir).summarize_to_skill(
                project_name=proj.name,
            )
            if result.ok:
                st.success(result.message)
                st.caption(str(result.path))
            else:
                st.info(result.message)


def _render_log_dialog(st) -> None:
    @st.dialog("📋 编译日志")
    def show_log_dialog():
        if not st.session_state.compile_log:
            st.info("暂无编译记录")
        else:
            for entry in reversed(st.session_state.compile_log):
                icon = "✅" if entry["success"] else "❌"
                st.markdown(f"**{icon} {entry['project']}** — {entry.get('instruction','')}")
                st.code(entry["message"], language="text")
                st.divider()
        if st.button("清除日志"):
            st.session_state.compile_log = []
            st.session_state.compile_result = None
            st.rerun()

    if st.session_state.get("_show_log_dialog"):
        st.session_state["_show_log_dialog"] = False
        show_log_dialog()


def _render_clear_confirmation(
    st,
    *,
    reset_tapir_p0_state_fn: Callable[[], None],
    bump_main_editor_version_fn: Callable[[], int],
) -> None:
    if not st.session_state.get("confirm_clear"):
        return

    st.warning("⚠️ 将重置项目（脚本、参数、编译日志），聊天记录保留。确认继续？")
    confirm_col, cancel_col, _ = st.columns([1, 1, 4])
    with confirm_col:
        if st.button("✅ 确认清空", type="primary"):
            keep_work_dir = st.session_state.work_dir
            keep_api_keys = st.session_state.model_api_keys
            keep_chat = st.session_state.chat_history
            st.session_state.project = None
            st.session_state.compile_log = []
            st.session_state.compile_result = None
            st.session_state.pending_diffs = {}
            st.session_state.pending_ai_label = ""
            st.session_state.pending_gsm_name = ""
            st.session_state.script_revision = 0
            st.session_state.agent_running = False
            st.session_state._import_key_done = ""
            st.session_state.confirm_clear = False
            st.session_state.preview_2d_data = None
            st.session_state.preview_3d_data = None
            st.session_state.preview_warnings = []
            st.session_state.preview_meta = {"kind": "", "timestamp": ""}
            clear_pending_preview_state(st.session_state)
            reset_tapir_p0_state_fn()
            bump_main_editor_version_fn()
            st.session_state.work_dir = keep_work_dir
            st.session_state.model_api_keys = keep_api_keys
            st.session_state.chat_history = keep_chat
            st.toast("🗑️ 已重置项目（脚本、参数、日志），聊天记录保留", icon="✅")
            st.rerun()
    with cancel_col:
        if st.button("❌ 取消"):
            st.session_state.confirm_clear = False
            st.rerun()


def _render_memory_privacy_panel(st) -> None:
    st.divider()
    st.markdown("#### 记忆与隐私")

    store = ErrorLearningStore(st.session_state.work_dir)
    status = store.memory_status()
    st.caption(
        "OpenBrep 会把聊天记录、错题本和整理后的 Skill 保存在当前工作区，"
        "用于后续生成时避开已发生过的问题。"
    )
    st.caption(f"保存位置：`{status.memory_root}`")

    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("聊天", status.chat_count)
    metric_2.metric("错题", status.lesson_count)
    metric_3.metric("Skill", "已生成" if status.has_learned_skill else "未生成")

    if st.session_state.get("confirm_clear_memory"):
        st.warning("确认清除当前工作区记忆？HSF 项目、编译产物和版本快照不会删除。")
        ok_col, cancel_col, _ = st.columns([1, 1, 3])
        with ok_col:
            if st.button("确认清除记忆", type="primary", key="clear_memory_confirm_button"):
                before = store.clear_memory()
                st.session_state.confirm_clear_memory = False
                st.toast(
                    f"已清除记忆：聊天 {before.chat_count} 条，错题 {before.lesson_count} 条",
                    icon="✅",
                )
                st.rerun()
        with cancel_col:
            if st.button("取消", key="clear_memory_cancel_button"):
                st.session_state.confirm_clear_memory = False
                st.rerun()
        return

    disabled = (
        status.chat_count == 0
        and status.lesson_count == 0
        and not status.has_learned_skill
        and status.total_bytes == 0
    )
    if st.button(
        "清除工作区记忆",
        width="stretch",
        disabled=disabled,
        help="删除当前工作区的持久化聊天、错题本和整理后的 Skill，不影响项目源文件。",
    ):
        st.session_state.confirm_clear_memory = True
        st.rerun()


def _render_preview_panel(
    st,
    *,
    render_preview_2d_fn: Callable[[object], None],
    render_preview_3d_fn: Callable[[object], None],
) -> None:
    preview_meta = st.session_state.get("preview_meta") or {}
    preview_kind = preview_meta.get("kind", "")
    preview_time = preview_meta.get("timestamp", "")
    title = f"最新预览：{preview_kind} · {preview_time}" if preview_kind else "预览面板（2D / 3D）"
    has_preview = bool(
        st.session_state.get("preview_2d_data")
        or st.session_state.get("preview_3d_data")
        or st.session_state.get("preview_warnings")
    )

    with st.expander(title, expanded=has_preview):
        tab_specs = ["2D", "3D", "Warnings"]
        if str(preview_kind).upper() == "3D":
            tab_specs = ["3D", "2D", "Warnings"]
        tabs = dict(zip(tab_specs, st.tabs(tab_specs)))
        with tabs["2D"]:
            render_preview_2d_fn(st.session_state.get("preview_2d_data"))
        with tabs["3D"]:
            render_preview_3d_fn(st.session_state.get("preview_3d_data"))
        with tabs["Warnings"]:
            _render_preview_warnings(st)


def _render_preview_warnings(st) -> None:
    warnings = st.session_state.get("preview_warnings") or []
    if not warnings:
        st.caption("暂无 warning。")
        return

    for warning in warnings:
        text = re.sub(r"^line\s+(\d+):", r"3d.gdl:L\1", str(warning))
        st.warning(text)


def _render_project_activity_log(st) -> None:
    entries = _project_activity_entries(st.session_state)
    if not entries:
        return

    with st.container(height=PROJECT_ACTIVITY_MAX_HEIGHT, border=True):
        st.caption("项目导入 / 加载记录")
        for idx, entry in enumerate(reversed(entries)):
            timestamp = str(entry.get("timestamp", "")).strip()
            message = str(entry.get("message", "")).strip()
            if timestamp:
                st.caption(timestamp)
            st.markdown(message)
            if idx < len(entries) - 1:
                st.divider()


def _project_activity_entries(session_state) -> list[dict]:
    entries = list(session_state.get("project_activity_log") or [])
    legacy_messages = [
        {"timestamp": "", "message": str(msg.get("content", ""))}
        for msg in session_state.get("chat_history", [])
        if msg.get("role", "assistant") == "assistant"
        and is_project_activity_message(msg.get("content", ""))
    ]
    return (legacy_messages + entries)[-20:]
