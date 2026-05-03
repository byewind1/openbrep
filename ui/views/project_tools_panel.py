from __future__ import annotations

from pathlib import Path
from typing import Callable

from openbrep.hsf_project import HSFProject


def render_project_tools_panel(
    st,
    proj: HSFProject,
    *,
    is_generation_locked_fn: Callable[[], bool],
    handle_hsf_directory_load_fn: Callable[[str], tuple[bool, str]],
    browse_and_open_project_file_fn: Callable[[], tuple[bool, str]],
    browse_and_load_hsf_directory_fn: Callable[[], tuple[bool, str]],
    choose_hsf_save_parent_dir_fn: Callable[[], str | None],
    save_hsf_project_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
    choose_compile_output_dir_fn: Callable[[], str | None] | None,
    do_compile_fn: Callable[[HSFProject, str, str, str | None], tuple[bool, str]],
) -> None:
    st.markdown("### 项目与输出")
    _render_project_input_section(
        st,
        proj=proj,
        is_generation_locked_fn=is_generation_locked_fn,
        handle_hsf_directory_load_fn=handle_hsf_directory_load_fn,
        browse_and_open_project_file_fn=browse_and_open_project_file_fn,
        browse_and_load_hsf_directory_fn=browse_and_load_hsf_directory_fn,
        choose_hsf_save_parent_dir_fn=choose_hsf_save_parent_dir_fn,
        save_hsf_project_fn=save_hsf_project_fn,
    )
    _render_compile_section(
        st,
        proj,
        choose_compile_output_dir_fn=choose_compile_output_dir_fn,
        do_compile_fn=do_compile_fn,
    )


def _render_project_input_section(
    st,
    *,
    proj: HSFProject,
    is_generation_locked_fn: Callable[[], bool],
    handle_hsf_directory_load_fn: Callable[[str], tuple[bool, str]],  # kept for app/test compatibility
    browse_and_open_project_file_fn: Callable[[], tuple[bool, str]],
    browse_and_load_hsf_directory_fn: Callable[[], tuple[bool, str]],
    choose_hsf_save_parent_dir_fn: Callable[[], str | None],
    save_hsf_project_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
) -> None:
    if st.button(
        "📄 打开文件",
        key="editor_open_project_file",
        disabled=is_generation_locked_fn(),
        width="stretch",
        help="支持 .gdl / .txt / .gsm 文件",
    ):
        ok, msg = browse_and_open_project_file_fn()
        if ok:
            st.rerun()
        elif msg.startswith("❌"):
            st.error(msg)
        elif msg:
            st.info(msg)

    if st.button(
        "📂 打开 HSF 项目",
        key="editor_open_hsf_project",
        disabled=is_generation_locked_fn(),
        width="stretch",
        help="选择 HSF 项目目录",
    ):
        ok, msg = browse_and_load_hsf_directory_fn()
        if ok:
            st.rerun()
        elif msg.startswith("❌"):
            st.error(msg)
        elif msg:
            st.info(msg)

    _render_hsf_save_section(
        st,
        proj=st.session_state.get("project") or proj,
        is_generation_locked_fn=is_generation_locked_fn,
        choose_hsf_save_parent_dir_fn=choose_hsf_save_parent_dir_fn,
        save_hsf_project_fn=save_hsf_project_fn,
    )


def _render_hsf_save_section(
    st,
    *,
    proj: HSFProject,
    is_generation_locked_fn: Callable[[], bool],
    choose_hsf_save_parent_dir_fn: Callable[[], str | None],
    save_hsf_project_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
) -> None:
    st.subheader("💾 HSF 保存")
    active_source_dir = str(st.session_state.get("active_hsf_source_dir", "") or "").strip()
    if active_source_dir:
        st.caption(f"当前 HSF 源目录：`{active_source_dir}`")

    save_col, save_as_col = st.columns(2)
    with save_col:
        if st.button(
            "保存 HSF",
            key="hsf_save_button",
            width="stretch",
            disabled=is_generation_locked_fn(),
        ):
            _save_or_open_hsf_save_as(st, proj, save_hsf_project_fn=save_hsf_project_fn)
    with save_as_col:
        if st.button(
            "另存为 HSF",
            key="hsf_save_as_button",
            width="stretch",
            disabled=is_generation_locked_fn(),
        ):
            _open_hsf_save_dialog(st, proj, mode="save_as")

    _render_hsf_save_dialog(
        st,
        proj=proj,
        choose_hsf_save_parent_dir_fn=choose_hsf_save_parent_dir_fn,
        save_hsf_project_fn=save_hsf_project_fn,
    )


def _save_or_open_hsf_save_as(
    st,
    proj: HSFProject,
    *,
    save_hsf_project_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
) -> None:
    active_source_dir = str(st.session_state.get("active_hsf_source_dir", "") or "").strip()
    if not active_source_dir:
        _open_hsf_save_dialog(st, proj, mode="save_as")
        return

    hsf_root = Path(active_source_dir).expanduser()
    ok, msg = save_hsf_project_fn(proj, str(hsf_root.parent), hsf_root.name)
    if ok:
        st.session_state.hsf_save_dialog_open = False
        st.session_state.hsf_save_dialog_mode = ""
        st.toast(msg, icon="💾")
    else:
        st.error(msg)


def _open_hsf_save_dialog(st, proj: HSFProject, *, mode: str) -> None:
    active_source_dir = str(st.session_state.get("active_hsf_source_dir", "") or "").strip()
    if active_source_dir:
        current_path = Path(active_source_dir)
        parent_dir = str(current_path.parent)
        folder_name = current_path.name
    else:
        parent_dir = str(st.session_state.get("work_dir", "") or "")
        folder_name = str(st.session_state.get("pending_gsm_name") or proj.name or "untitled")

    st.session_state.hsf_save_dialog_mode = mode
    st.session_state.hsf_save_parent_dir = parent_dir
    st.session_state.hsf_save_name = folder_name
    st.session_state.hsf_save_dialog_open = True


def _render_hsf_save_dialog(
    st,
    *,
    proj: HSFProject,
    choose_hsf_save_parent_dir_fn: Callable[[], str | None],
    save_hsf_project_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
) -> None:
    if not st.session_state.get("hsf_save_dialog_open"):
        return

    mode = str(st.session_state.get("hsf_save_dialog_mode", "save")).strip() or "save"
    dialog_title = "💾 保存 HSF" if mode == "save" else "📂 另存为 HSF"

    @st.dialog(dialog_title)
    def _dialog() -> None:
        active_source_dir = str(st.session_state.get("active_hsf_source_dir", "") or "").strip()
        if active_source_dir:
            st.caption(f"当前 HSF 源目录：`{active_source_dir}`")
        parent_dir = st.text_input(
            "保存到目录",
            key="hsf_save_parent_dir",
            help="选择父目录后，会在其下创建 HSF 文件夹",
        )
        choose_col, _ = st.columns([1, 2])
        with choose_col:
            if st.button("选择目录", width="stretch"):
                selected = choose_hsf_save_parent_dir_fn()
                if selected:
                    st.session_state.hsf_save_parent_dir = selected
                    st.rerun()

        folder_name = st.text_input(
            "HSF 文件夹名称",
            key="hsf_save_name",
            help="保存时创建或覆盖的 HSF 文件夹名",
        )

        action_col, cancel_col = st.columns(2)
        with action_col:
            if st.button("保存", type="primary", width="stretch"):
                ok, msg = save_hsf_project_fn(proj, parent_dir, folder_name)
                if ok:
                    st.session_state.hsf_save_dialog_open = False
                    st.session_state.hsf_save_dialog_mode = ""
                    st.toast(msg, icon="💾")
                    st.rerun()
                else:
                    st.error(msg)
        with cancel_col:
            if st.button("取消", width="stretch"):
                st.session_state.hsf_save_dialog_open = False
                st.session_state.hsf_save_dialog_mode = ""
                st.rerun()

    _dialog()


def _render_compile_section(
    st,
    proj: HSFProject,
    *,
    choose_compile_output_dir_fn: Callable[[], str | None] | None,
    do_compile_fn: Callable[[HSFProject, str, str, str | None], tuple[bool, str]],
) -> None:
    compile_name = st.session_state.pending_gsm_name or proj.name
    if compile_name and not st.session_state.pending_gsm_name:
        st.session_state.pending_gsm_name = compile_name
    if st.button(
        "🔧 编译 GSM",
        type="primary",
        width="stretch",
        help="选择输出文件夹；取消选择时使用默认 workspace/output",
        disabled=st.session_state.agent_running,
    ):
        output_dir = choose_compile_output_dir_fn() if choose_compile_output_dir_fn else None
        with st.spinner("编译中..."):
            success, result_msg = do_compile_fn(
                proj,
                compile_name,
                "(toolbar compile)",
                output_dir,
        )
        st.session_state.compile_result = (success, result_msg)
        if success:
            st.toast("✅ 编译成功", icon="🏗️")
        st.rerun()

    if st.session_state.compile_result is not None:
        ok, msg = st.session_state.compile_result
        if not ok:
            st.error(msg)
