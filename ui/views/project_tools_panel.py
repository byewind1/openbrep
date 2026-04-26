from __future__ import annotations

from typing import Callable

from openbrep.hsf_project import HSFProject
from ui.views import revision_panel


def render_project_tools_panel(
    st,
    proj: HSFProject,
    *,
    is_generation_locked_fn: Callable[[], bool],
    handle_unified_import_fn: Callable[[object], tuple[bool, str]],
    handle_hsf_directory_load_fn: Callable[[str], tuple[bool, str]],
    do_compile_fn: Callable[[HSFProject, str, str], tuple[bool, str]],
    save_revision_fn: Callable[[HSFProject, str], tuple[bool, str]],
    restore_revision_fn: Callable[[HSFProject, str], tuple[bool, str]],
) -> None:
    import_col, compile_col = st.columns([1.8, 2.2])

    with import_col:
        uploaded = st.file_uploader(
            "📂 导入 gdl / txt / gsm",
            type=["gdl", "txt", "gsm"],
            key="editor_import",
            help=".gdl/.txt → 解析脚本  |  .gsm → LP_XMLConverter 解包",
            disabled=is_generation_locked_fn(),
        )
        if uploaded:
            file_key = f"{uploaded.name}_{uploaded.size}"
            if st.session_state._import_key_done != file_key:
                ok, msg = handle_unified_import_fn(uploaded)
                if ok:
                    st.session_state._import_key_done = file_key
                    st.rerun()
                else:
                    st.error(msg)

        hsf_dir_input = st.text_input(
            "HSF 项目目录",
            value="",
            placeholder="/path/to/YourHSFProject",
            key="editor_hsf_dir",
            disabled=is_generation_locked_fn(),
        )
        if st.button(
            "载入 HSF 项目",
            key="editor_load_hsf",
            disabled=is_generation_locked_fn(),
            use_container_width=True,
        ):
            ok, msg = handle_hsf_directory_load_fn(hsf_dir_input)
            if ok:
                st.rerun()
            else:
                st.error(msg)

    with compile_col:
        gsm_name_input = st.text_input(
            "GSM名称",
            label_visibility="collapsed",
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
                    _ok, msg = save_revision_fn(proj, f"Compile {gsm_name_input or proj.name}")
                    st.session_state.revision_notice = msg
                st.toast("✅ 编译成功", icon="🏗️")
            st.rerun()

    if st.session_state.compile_result is not None:
        ok, msg = st.session_state.compile_result
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    revision_panel.render_revision_panel(
        st,
        proj,
        is_generation_locked_fn=is_generation_locked_fn,
        save_revision_fn=save_revision_fn,
        restore_revision_fn=restore_revision_fn,
    )
