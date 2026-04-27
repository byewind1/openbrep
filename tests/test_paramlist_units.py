from openbrep.hsf_project import GDLParameter
from openbrep.paramlist_builder import build_paramlist_xml, clean_parameter_description, validate_paramlist


def test_clean_parameter_description_removes_length_unit_markers():
    assert clean_parameter_description("总宽度（mm）", "Length") == "总宽度"
    assert clean_parameter_description("Total depth (m)", "Length") == "Total depth"


def test_build_paramlist_xml_omits_length_units_from_description():
    xml = build_paramlist_xml([
        GDLParameter("A", "Length", "总宽度（mm）", "0.9", is_fixed=True),
    ])

    assert "总宽度" in xml
    assert "mm" not in xml


def test_validate_paramlist_warns_when_length_name_contains_unit_marker():
    issues = validate_paramlist([
        GDLParameter("width_mm", "Length", "宽度", "0.9"),
    ])

    assert any("should not include unit markers" in issue for issue in issues)
