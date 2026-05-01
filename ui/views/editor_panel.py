from __future__ import annotations

from typing import Callable

from ui.proposed_preview_controller import clear_pending_preview_state

from openbrep.hsf_project import HSFProject


SCRIPT_HELP = {
    "scripts/3d.gdl": (
        "**3D 脚本** — 三维几何体定义，ArchiCAD 3D 窗口中显示的实体。\n\n"
        "- 使用 `PRISM_`、`BLOCK`、`SPHERE`、`CONE`、`REVOLVE` 等命令建模\n"
        "- `ADD` / `DEL` 管理坐标系变换，必须成对出现\n"
        "- `FOR` / `NEXT` 循环用于重复构件（如格栅、层板）\n"
        "- **最后一行必须是 `END`**，否则编译失败"
    ),
    "scripts/2d.gdl": (
        "**2D 脚本** — 平面图符号，ArchiCAD 楼层平面图中显示的线条。\n\n"
        "- **必须包含** `PROJECT2 3, 270, 2`（最简投影）或自定义 2D 线条\n"
        "- 不写或留空会导致平面图中对象不可见"
    ),
    "scripts/1d.gdl": (
        "**Master 脚本** — 主控脚本，所有脚本执行前最先运行。\n\n"
        "- 全局变量初始化、参数联动逻辑\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/vl.gdl": (
        "**Param 脚本** — 参数验证脚本，参数值变化时触发。\n\n"
        "- 参数范围约束、派生参数计算\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/ui.gdl": (
        "**UI 脚本** — 自定义参数界面，ArchiCAD 对象设置对话框控件布局。\n\n"
        "- 不写则 ArchiCAD 自动生成默认参数列表界面"
    ),
    "scripts/pr.gdl": (
        "**Properties 脚本** — BIM 属性输出，定义 IFC 属性集和构件属性。\n\n"
        "- 不做 BIM 数据输出可留空"
    ),
}


def render_script_editor_panel(
    st,
    proj: HSFProject,
    *,
    script_map: list[tuple[object, str, str]],
    editor_version: int,
    ace_available: bool,
    st_ace_fn,
    main_editor_state_key_fn: Callable[[str, int], str],
    fullscreen_editor_dialog_fn: Callable[[object, str, str], None],
) -> None:
    st.markdown("### GDL 脚本编辑")
    script_tabs = st.tabs([label for _, _, label in script_map])

    for tab, (script_type, fpath, label) in zip(script_tabs, script_map):
        with tab:
            help_col, fullscreen_col = st.columns([6, 1])
            with help_col:
                with st.expander(f"ℹ️ {label} 脚本说明"):
                    st.markdown(SCRIPT_HELP.get(fpath, ""))
            with fullscreen_col:
                if st.button("⛶", key=f"fs_{fpath}_v{editor_version}", help="全屏编辑", width="stretch"):
                    fullscreen_editor_dialog_fn(script_type, fpath, label)

            current_code = proj.get_script(script_type) or ""
            editor_key = main_editor_state_key_fn(fpath, editor_version)

            if ace_available:
                raw_ace = st_ace_fn(
                    value=current_code,
                    language="fortran",
                    theme="monokai",
                    height=280,
                    font_size=13,
                    tab_size=2,
                    show_gutter=True,
                    show_print_margin=False,
                    wrap=False,
                    key=editor_key,
                )
                pending_keys = st.session_state.get("_ace_pending_main_editor_keys", set())
                if editor_key in pending_keys and current_code and raw_ace in (None, ""):
                    new_code = current_code
                else:
                    if editor_key in pending_keys and (raw_ace is not None or not current_code):
                        pending_keys.discard(editor_key)
                        st.session_state._ace_pending_main_editor_keys = pending_keys
                    new_code = raw_ace if raw_ace is not None else current_code
            else:
                new_code = st.text_area(
                    label,
                    value=current_code,
                    height=280,
                    key=editor_key,
                    label_visibility="collapsed",
                ) or ""

            if new_code != current_code:
                proj.set_script(script_type, new_code)
                _clear_preview_state(st.session_state)


def _clear_preview_state(session_state) -> None:
    session_state.preview_2d_data = None
    session_state.preview_3d_data = None
    session_state.preview_warnings = []
    session_state.preview_meta = {"kind": "", "timestamp": ""}
    clear_pending_preview_state(session_state)
