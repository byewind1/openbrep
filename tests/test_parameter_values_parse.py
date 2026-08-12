from __future__ import annotations

"""P11：vl.gdl VALUES 枚举解析单测。

覆盖：字符串枚举 / 数值枚举 / 布尔 0,1 / RANGE / 无 vl.gdl / 解析失败兜底 /
注释与引号内逗号 / 同一参数后声明覆盖先声明 / 快照 payload 字段透传。
"""

from pathlib import Path

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.workbench.project_parameter_service import parse_values_declarations
from openbrep.workbench_api import WorkbenchSession


# ── parse_values_declarations 纯函数 ─────────────────────────


def test_parse_string_enum_keeps_order_and_type():
    vl = 'VALUES "pattern_type" "直棂", "井字", "菱花"'
    result = parse_values_declarations(vl)

    assert result["pattern_type"]["options"] == ["直棂", "井字", "菱花"]
    assert result["pattern_type"]["range"] is None


def test_parse_numeric_enum_keeps_int_and_float_types():
    vl = 'VALUES "shelf_count" 3, 5, 7\nVALUES "step" 0.5, 1.0, 1.5'
    result = parse_values_declarations(vl)

    assert result["shelf_count"]["options"] == [3, 5, 7]
    assert result["shelf_count"]["range"] is None
    assert result["step"]["options"] == [0.5, 1.0, 1.5]


def test_parse_boolean_zero_one_enum():
    vl = 'VALUES "show_in_3d" 0, 1'
    result = parse_values_declarations(vl)

    assert result["show_in_3d"]["options"] == [0, 1]
    assert result["show_in_3d"]["range"] is None


def test_parse_range_two_and_three_numbers():
    vl = 'VALUES "A" RANGE [0.30, 3.00]\nVALUES "B" RANGE [0.1, 2.0, 0.05]'
    result = parse_values_declarations(vl)

    assert result["A"]["range"] == [0.3, 3.0]
    assert result["A"]["options"] is None
    assert result["B"]["range"] == [0.1, 2.0, 0.05]


def test_parse_empty_vl_returns_empty_map():
    assert parse_values_declarations("") == {}
    assert parse_values_declarations(None) == {}


def test_parse_failure_lines_are_skipped_not_fatal():
    # 半截 RANGE / 无值声明 / 非 VALUES 行：不得抛异常，不得产出垃圾条目
    vl = (
        "! comment only\n"
        "VALUES \"broken\" RANGE [a\n"
        "VALUES \"empty\"\n"
        "IF x THEN\n"
        "  LOCK \"inner_frame_width\"\n"
        "ENDIF\n"
    )
    result = parse_values_declarations(vl)

    assert result == {}


def test_parse_ignores_comments_and_quoted_commas():
    vl = (
        '! header comment\n'
        'VALUES "label" "a,b", "c" ! trailing comment\n'
        'VALUES "x" 1, 2 ! trailing'
    )
    result = parse_values_declarations(vl)

    assert result["label"]["options"] == ["a,b", "c"]
    assert result["x"]["options"] == [1, 2]


def test_parse_later_declaration_wins():
    vl = 'VALUES "p" 1, 2\nVALUES "p" 9'
    result = parse_values_declarations(vl)

    assert result["p"]["options"] == [9]


def test_parse_real_lattice_vl_shape():
    """漏窗项目的真实 vl.gdl 形状（内容快照，不触碰项目目录）。"""
    vl = """! 漏窗 Parameter 脚本
VALUES "pattern_type" "直棂", "井字", "菱花"

VALUES "A" RANGE [0.30, 3.00]
VALUES "bar_gap" RANGE [0.02, 0.50]
VALUES "show_in_3d" 0, 1

IF x THEN
  LOCK "inner_frame_width"
ENDIF
"""
    result = parse_values_declarations(vl)

    assert result["pattern_type"]["options"] == ["直棂", "井字", "菱花"]
    assert result["A"]["range"] == [0.3, 3.0]
    assert result["show_in_3d"]["options"] == [0, 1]
    assert "inner_frame_width" not in result


# ── 快照 payload 透传（集成） ────────────────────────────────


def make_lattice_session(tmp_path: Path) -> WorkbenchSession:
    project = HSFProject.create_new("Lattice", str(tmp_path))
    project.parameters.append(
        GDLParameter(name="pattern_type", type_tag="String", description="纹样", value="直棂")
    )
    project.set_script(
        ScriptType.PARAM,
        'VALUES "pattern_type" "直棂", "井字", "菱花"\nVALUES "A" RANGE [0.30, 3.00]\n',
    )
    project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    result = session.load_hsf_directory(str(project.root))
    assert result["ok"] is True
    return session


def test_snapshot_parameters_carry_options_and_range(tmp_path):
    session = make_lattice_session(tmp_path)

    snapshot = session.snapshot()
    by_name = {param["name"]: param for param in snapshot["parameters"]}

    assert by_name["pattern_type"]["options"] == ["直棂", "井字", "菱花"]
    assert by_name["pattern_type"]["range"] is None
    assert by_name["A"]["range"] == [0.3, 3.0]
    assert by_name["A"]["options"] is None


def test_snapshot_parameters_without_vl_have_null_fields(tmp_path):
    project = HSFProject.create_new("Plain", str(tmp_path))
    project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    assert session.load_hsf_directory(str(project.root))["ok"] is True

    snapshot = session.snapshot()
    by_name = {param["name"]: param for param in snapshot["parameters"]}

    assert by_name["A"]["options"] is None
    assert by_name["A"]["range"] is None


def test_snapshot_parameters_with_corrupt_vl_have_null_fields(tmp_path):
    project = HSFProject.create_new("Corrupt", str(tmp_path))
    project.set_script(ScriptType.PARAM, "VALUES broken RANGE [x\n")
    project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    assert session.load_hsf_directory(str(project.root))["ok"] is True

    snapshot = session.snapshot()
    by_name = {param["name"]: param for param in snapshot["parameters"]}

    assert by_name["A"]["options"] is None
    assert by_name["A"]["range"] is None


def test_add_parameter_response_includes_values_fields(tmp_path):
    session = make_lattice_session(tmp_path)
    session.project.set_script(
        ScriptType.PARAM,
        session.project.get_script(ScriptType.PARAM) + 'VALUES "finish" "清漆", "哑光"\n',
    )

    result = session.route(
        "POST",
        "/api/project/parameters",
        {"name": "finish", "type_tag": "String", "value": "哑光"},
    )

    assert result["ok"] is True
    assert result["added"]["name"] == "finish"
    assert result["added"]["options"] == ["清漆", "哑光"]
    assert result["added"]["range"] is None
