from __future__ import annotations

from typing import Callable

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.paramlist_builder import build_paramlist_xml, clean_parameter_description, validate_paramlist


def render_parameter_panel(
    st,
    proj: HSFProject,
    *,
    render_tapir_inspector_fn: Callable[[], None] | None = None,
    render_tapir_param_workbench_fn: Callable[[], None] | None = None,
) -> None:
    st.markdown("### 对象参数")
    _render_project_parameters(st, proj)
    if render_tapir_inspector_fn is not None and render_tapir_param_workbench_fn is not None:
        _render_archicad_parameter_bridge(
            st,
            render_tapir_inspector_fn=render_tapir_inspector_fn,
            render_tapir_param_workbench_fn=render_tapir_param_workbench_fn,
        )


def _render_project_parameters(st, proj: HSFProject) -> None:
    editable_count = sum(1 for param in proj.parameters if not param.is_fixed)
    fixed_count = len(proj.parameters) - editable_count
    count_col, editable_col, fixed_col = st.columns(3)
    count_col.metric("参数总数", len(proj.parameters))
    editable_col.metric("可编辑", editable_count)
    fixed_col.metric("固定", fixed_count)

    with st.expander("参数说明"):
        st.markdown(
            "**参数列表** — GDL 对象的可调参数。\n\n"
            "- **Type**: `Length` / `Integer` / `Boolean` / `Material` / `String`\n"
            "- **Name**: 代码中引用的变量名（camelCase，如 `iShelves`）\n"
            "- **Value**: 默认值\n"
            "- **Fixed**: 勾选后用户无法在 ArchiCAD 中修改"
        )

    param_data = [
        {
            "类型": param.type_tag,
            "变量名": param.name,
            "默认值": param.value,
            "说明": clean_parameter_description(param.description, param.type_tag),
            "固定": "是" if param.is_fixed else "",
        }
        for param in proj.parameters
    ]
    if param_data:
        st.dataframe(param_data, width="stretch", hide_index=True)
    else:
        st.caption("暂无参数，通过 AI 对话添加，或手动添加。")

    _render_manual_parameter_form(st, proj)
    _render_parameter_validation(st, proj)

    with st.expander("paramlist.xml 预览"):
        st.code(build_paramlist_xml(proj.parameters), language="xml")


def _render_archicad_parameter_bridge(
    st,
    *,
    render_tapir_inspector_fn: Callable[[], None] | None,
    render_tapir_param_workbench_fn: Callable[[], None] | None,
) -> None:
    if render_tapir_inspector_fn is None or render_tapir_param_workbench_fn is None:
        st.info("Archicad 参数写回未启用。")
        return

    st.caption("从 Archicad 读取当前选中对象，检查对象信息后编辑参数并写回。")
    inspect_tab, edit_tab = st.tabs(["选中对象", "参数编辑"])
    with inspect_tab:
        render_tapir_inspector_fn()
    with edit_tab:
        render_tapir_param_workbench_fn()


def _render_manual_parameter_form(st, proj: HSFProject) -> None:
    with st.expander("➕ 手动添加参数"):
        type_col, name_col, value_col, desc_col = st.columns(4)
        with type_col:
            param_type = st.selectbox(
                "类型",
                [
                    "Length",
                    "Integer",
                    "Boolean",
                    "RealNum",
                    "Angle",
                    "String",
                    "Material",
                    "FillPattern",
                    "LineType",
                    "PenColor",
                ],
            )
        with name_col:
            param_name = st.text_input("变量名", value="bNewParam")
        with value_col:
            param_value = st.text_input("默认值", value="0")
        with desc_col:
            param_desc = st.text_input("说明")

        if st.button("添加参数"):
            try:
                proj.add_parameter(GDLParameter(param_name, param_type, param_desc, param_value))
                st.success(f"✅ {param_type} {param_name}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def _render_parameter_validation(st, proj: HSFProject) -> None:
    if not st.button("🔍 验证参数"):
        return

    issues = validate_paramlist(proj.parameters)
    for issue in issues:
        st.warning(issue)
    if not issues:
        st.success("✅ 参数验证通过")
