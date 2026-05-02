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
    do_compile_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
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
    st.markdown("#### 1. 打开")
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
        else:
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
        else:
            st.info(msg)


def _render_compile_section(
    st,
    proj: HSFProject,
    *,
    do_compile_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
    save_revision_fn: Callable[[HSFProject, str, str | None], tuple[bool, str]],
) -> None:
    with st.expander("2. 编译 GSM 输出", expanded=True):
        gsm_name_input = st.text_input(
            "GSM 输出名称",
            value=st.session_state.pending_gsm_name or proj.name,
            placeholder="输出 GSM 名称（不含扩展名）",
            help="编译输出文件名",
        )
        st.session_state.pending_gsm_name = gsm_name_input
        if st.button(
            "🔧  编  译  GSM",
            type="primary",
            width="stretch",
            help="将当前所有脚本编译为 ArchiCAD .gsm 对象",
            disabled=st.session_state.agent_running,
        ):
            with st.spinner("编译中..."):
                success, result_msg = do_compile_fn(
                    proj,
                    gsm_name_input or proj.name,
                    "(toolbar compile)",
                )
            st.session_state.compile_result = (success, result_msg)
            if success:
                if st.session_state.get("revision_auto_snapshot", True):
                    _ok, msg = save_revision_fn(
                        proj,
                        f"Compile {gsm_name_input or proj.name}",
                        gsm_name_input or proj.name,
                    )
                    st.session_state.revision_notice = msg
                st.toast("✅ 编译成功", icon="🏗️")
            st.rerun()

        if st.session_state.compile_result is not None:
            ok, msg = st.session_state.compile_result
            if ok:
                st.success(msg)
            else:
                st.error(msg)
