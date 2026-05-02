from __future__ import annotations

from typing import Callable

from openbrep.hsf_project import HSFProject
from ui.views import revision_panel


def render_project_tools_panel(
    st,
    proj: HSFProject,
    *,
    is_generation_locked_fn: Callable[[], bool],
    handle_hsf_directory_load_fn: Callable[[str], tuple[bool, str]],
    browse_and_open_project_file_fn: Callable[[], tuple[bool, str]],
    browse_and_load_hsf_directory_fn: Callable[[], tuple[bool, str]],
    choose_compile_output_dir_fn: Callable[[], str | None] | None,
    do_compile_fn: Callable[[HSFProject, str, str, str | None], tuple[bool, str]],
    save_revision_fn: Callable[[HSFProject, str, str | None], tuple[bool, str]],
    restore_revision_fn: Callable[[HSFProject, str], tuple[bool, str]],
) -> None:
    st.markdown("### 项目与输出")
    _render_project_input_section(
        st,
        is_generation_locked_fn=is_generation_locked_fn,
        handle_hsf_directory_load_fn=handle_hsf_directory_load_fn,
        browse_and_open_project_file_fn=browse_and_open_project_file_fn,
        browse_and_load_hsf_directory_fn=browse_and_load_hsf_directory_fn,
    )
    _render_compile_section(
        st,
        proj,
        choose_compile_output_dir_fn=choose_compile_output_dir_fn,
        do_compile_fn=do_compile_fn,
        save_revision_fn=save_revision_fn,
    )

    revision_panel.render_revision_panel(
        st,
        proj,
        is_generation_locked_fn=is_generation_locked_fn,
        save_revision_fn=save_revision_fn,
        restore_revision_fn=restore_revision_fn,
    )


def _render_project_input_section(
    st,
    *,
    is_generation_locked_fn: Callable[[], bool],
    handle_hsf_directory_load_fn: Callable[[str], tuple[bool, str]],  # kept for app/test compatibility
    browse_and_open_project_file_fn: Callable[[], tuple[bool, str]],
    browse_and_load_hsf_directory_fn: Callable[[], tuple[bool, str]],
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


def _render_compile_section(
    st,
    proj: HSFProject,
    *,
    choose_compile_output_dir_fn: Callable[[], str | None] | None,
    do_compile_fn: Callable[[HSFProject, str, str, str | None], tuple[bool, str]],
    save_revision_fn: Callable[[HSFProject, str, str | None], tuple[bool, str]],
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
            if st.session_state.get("revision_auto_snapshot", True):
                _ok, msg = save_revision_fn(
                    proj,
                    f"Compile {compile_name}",
                    compile_name,
                )
                st.session_state.revision_notice = msg
            st.toast("✅ 编译成功", icon="🏗️")
        st.rerun()

    if st.session_state.compile_result is not None:
        ok, msg = st.session_state.compile_result
        if not ok:
            st.error(msg)
