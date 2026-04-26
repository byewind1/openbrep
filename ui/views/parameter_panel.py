from __future__ import annotations

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.paramlist_builder import build_paramlist_xml, validate_paramlist


def render_parameter_panel(st, proj: HSFProject) -> None:
    with st.expander("ℹ️ 参数说明"):
        st.markdown(
            "**参数列表** — GDL 对象的可调参数。\n\n"
            "- **Type**: `Length` / `Integer` / `Boolean` / `Material` / `String`\n"
            "- **Name**: 代码中引用的变量名（camelCase，如 `iShelves`）\n"
            "- **Value**: 默认值\n"
            "- **Fixed**: 勾选后用户无法在 ArchiCAD 中修改"
        )

    param_data = [
        {
            "Type": param.type_tag,
            "Name": param.name,
            "Value": param.value,
            "Description": param.description,
            "Fixed": "✓" if param.is_fixed else "",
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


def _render_manual_parameter_form(st, proj: HSFProject) -> None:
    with st.expander("➕ 手动添加参数"):
        type_col, name_col, value_col, desc_col = st.columns(4)
        with type_col:
            param_type = st.selectbox(
                "Type",
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
            param_name = st.text_input("Name", value="bNewParam")
        with value_col:
            param_value = st.text_input("Value", value="0")
        with desc_col:
            param_desc = st.text_input("Description")

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
