"""GDL source parser（openbrep.gdl_parser）契约测试。

覆盖参数注释块 → GDLParameter、脚本分节拆分、以及"零静默"：任何丢失 /
无法解析 / 识别不了的内容都必须进 warnings（带行号）。
"""

from pathlib import Path

from openbrep.gdl_parser import (
    gdl_source_has_sections,
    parse_gdl_file_with_warnings,
    parse_gdl_source,
    parse_gdl_source_with_warnings,
)
from openbrep.hsf_project import ScriptType

REPO = Path(__file__).resolve().parents[1]


def test_parse_bookshelf_parameters_and_sections():
    project, warnings = parse_gdl_file_with_warnings(str(REPO / "examples" / "Bookshelf.gdl"))
    assert warnings == []

    # 10 个参数，类型映射正确
    assert len(project.parameters) == 10
    by_name = {p.name: p for p in project.parameters}
    assert by_name["nShelves"].type_tag == "Integer"
    assert by_name["nShelves"].value == "4"
    assert by_name["hasBack"].type_tag == "Boolean"
    assert by_name["frameMat"].type_tag == "Material"
    assert by_name["frameMat"].value == "Wood - Oak"

    # 分节拆分：MASTER 内容进 master，2D/3D 各归其位
    assert ScriptType.MASTER in project.scripts
    assert ScriptType.SCRIPT_2D in project.scripts
    assert ScriptType.SCRIPT_3D in project.scripts
    master = project.get_script(ScriptType.MASTER)
    script_3d = project.get_script(ScriptType.SCRIPT_3D)
    assert "IF A < 0.30" in master
    assert "IF A < 0.30" not in script_3d
    assert "BLOCK A, B, thickness" in script_3d


def test_parse_unparseable_param_line_warns_with_line_number():
    src = (
        "! ====\n"
        "! A           Length    0.80    书架宽度\n"
        "! nShelves    Integer\n"
        "! B           Length    0.30    书架深度\n"
        "! ====\n"
        "! 3D SCRIPT\n"
        "BLOCK A, B, ZZYZX\n"
    )
    project, warnings = parse_gdl_source_with_warnings(src, "T")
    assert any("第 3 行" in w and "nShelves" in w for w in warnings)
    assert "nShelves" not in {p.name for p in project.parameters}
    assert {"A", "B"} <= {p.name for p in project.parameters}


def test_parse_duplicate_param_warns():
    src = (
        "! ====\n"
        "! A   Length   0.80   宽\n"
        "! A   Length   0.90   重复\n"
        "! ====\n"
        "! 3D SCRIPT\n"
        "BLOCK A, B, ZZYZX\n"
    )
    _project, warnings = parse_gdl_source_with_warnings(src, "T")
    assert any("重复" in w and "A" in w for w in warnings)


def test_parse_unrecognized_section_banner_warns():
    src = (
        "! ====\n"
        "! GLOBALS SCRIPT\n"
        "! ====\n"
        "! 3D SCRIPT\n"
        "BLOCK A, B, ZZYZX\n"
    )
    _project, warnings = parse_gdl_source_with_warnings(src, "T")
    assert any("GLOBALS SCRIPT" in w for w in warnings)


def test_parse_code_outside_sections_warns_with_line_number():
    src = "SOME_COMMAND\n! ====\n! 3D SCRIPT\nBLOCK A, B, ZZYZX\n"
    _project, warnings = parse_gdl_source_with_warnings(src, "T")
    assert any("第 1 行" in w for w in warnings)


def test_parse_no_sections_keeps_whole_file_in_3d_without_warnings():
    src = "BLOCK A, B, ZZYZX\nADDZ 1\n"
    project, warnings = parse_gdl_source_with_warnings(src, "T")
    assert warnings == []
    assert project.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nADDZ 1\n"
    # 向后兼容：parse_gdl_source 仍返回 HSFProject
    assert parse_gdl_source(src, "T") is not None


def test_gdl_source_has_sections_detects_banners():
    assert gdl_source_has_sections("! ====\n! 3D SCRIPT\nBLOCK A, B, ZZYZX\n") is True
    assert gdl_source_has_sections("BLOCK A, B, ZZYZX\n") is False
