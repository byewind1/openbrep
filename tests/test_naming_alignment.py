"""openbrep/naming_alignment.py 的合同测试。

覆盖：精确匹配跳过 / 同义词重命名 / 保留名五条规则 / 词法扫描器的
注释、字符串、子串保护 / semantic_bug 分级 / dry_run / missing_concepts。
"""

from __future__ import annotations

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.naming_alignment import (
    align_parameter_names,
    replace_identifier,
)


def _project(tmp_path, params=(), script_3d="") -> HSFProject:
    proj = HSFProject.create_new("align_test", work_dir=str(tmp_path))
    proj.parameters = list(params)
    if script_3d:
        proj.scripts[ScriptType.SCRIPT_3D] = script_3d
    return proj


def _p(name: str) -> GDLParameter:
    return GDLParameter(name=name, type_tag="Length", value="1.0")


# ── 1. 精确匹配跳过 ──


def test_exact_match_produces_no_rename(tmp_path):
    proj = _project(tmp_path, [_p("n_shelves")], "FOR i = 1 TO n_shelves\nNEXT i\n")
    result = align_parameter_names(proj, {"n_shelves": None})
    assert result.renamed == []
    assert proj.parameters[0].name == "n_shelves"


# ── 2. 同义词重命名生效 + occurrences 计数 ──


def test_synonym_rename_with_occurrence_count(tmp_path):
    script = (
        "FOR i = 1 TO shelf_count\n"
        "  ADDZ i * shelf_thickness\n"
        "  BLOCK A, B, shelf_thickness\n"
        "  DEL 1\n"
        "NEXT i\n"
    )
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("ZZYZX"), _p("shelf_count"), _p("shelf_thickness")],
        script,
    )
    result = align_parameter_names(proj, {"n_shelves": None, "shelf_thk": None})

    renames = {(r.from_name, r.to_name): r.occurrences for r in result.renamed}
    assert ("shelf_count", "n_shelves") in renames
    assert ("shelf_thickness", "shelf_thk") in renames
    assert renames[("shelf_thickness", "shelf_thk")] == 2

    names = [p.name for p in proj.parameters]
    assert "n_shelves" in names and "shelf_thk" in names
    assert "shelf_count" not in names
    assert "n_shelves" in proj.scripts[ScriptType.SCRIPT_3D]
    assert "shelf_thk" in proj.scripts[ScriptType.SCRIPT_3D]


# ── 3. 保留名不作为重命名的源 ──


def test_reserved_names_never_renamed_as_source(tmp_path):
    proj = _project(
        tmp_path,
        [_p("A"), _p("wall_thk")],
        "BLOCK A, wall_thk, ZZYZX\n",
    )
    # pipe_od 名称匹配不到任何非保留名 → 查角色证据 → A 在 BLOCK 第 1 维
    result = align_parameter_names(proj, {"pipe_od": None})

    assert result.renamed == []
    assert [p.name for p in proj.parameters] == ["A", "wall_thk"]
    assert len(result.reserved_conflicts) == 1
    conflict = result.reserved_conflicts[0]
    assert conflict.expected_name == "pipe_od"
    assert conflict.reserved_name == "A"
    assert conflict.severity == "blocked"  # pipe_od 非高度语义 ↔ A 宽度语义
    assert result.has_semantic_bug is False


# ── 4. 非保留名 → 保留名允许（C→ZZYZX，C15 场景）──


def test_plain_to_reserved_allowed_via_role(tmp_path):
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("C")],
        "BLOCK A, B, C\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})

    assert [(r.from_name, r.to_name) for r in result.renamed] == [("C", "ZZYZX")]
    names = [p.name for p in proj.parameters]
    assert names == ["A", "B", "ZZYZX"]
    assert "BLOCK A, B, ZZYZX" in proj.scripts[ScriptType.SCRIPT_3D]


# ── 5. 规则 5：目标保留名已存在 → 跳过 ──


def test_rename_to_reserved_skipped_when_target_exists(tmp_path):
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("ZZYZX"), _p("C")],
        "BLOCK A, B, C\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})
    # ZZYZX 已存在 → 精确匹配分支，C 不会被重命名
    assert result.renamed == []
    assert "C" in [p.name for p in proj.parameters]


# ── 6/7/8. 词法扫描器的保护区域 ──


def test_comment_region_not_touched(tmp_path):
    code = "BLOCK shelf_count, B, ZZYZX ! shelf_count 是层数\n"
    new_code, n = replace_identifier(code, "shelf_count", "n_shelves")
    assert n == 1
    assert "BLOCK n_shelves" in new_code
    assert "! shelf_count 是层数" in new_code


def test_string_literal_not_touched(tmp_path):
    """旧语义已改：VALUES 字符串是参数引用，会被替换；此处验证计数涵盖两处。"""
    code = 'VALUES "shelf_count" 1, 2, 3\nx = shelf_count\n'
    new_code, n = replace_identifier(code, "shelf_count", "n_shelves")
    assert n == 2
    assert 'VALUES "n_shelves"' in new_code
    assert "x = n_shelves" in new_code


def test_prose_string_not_touched(tmp_path):
    code = 'PRINT "shelf_count 是层数"\n'
    new_code, n = replace_identifier(code, "shelf_count", "n_shelves")
    assert n == 0
    assert new_code == code


def test_values_string_reference_renamed(tmp_path):
    """VALUES/LOCK 按字符串引用参数名——整体等于旧名时必须跟着改名。"""
    code = 'VALUES "shelf_count" RANGE [2, 20]\nLOCK "shelf_thk"\n'
    new_code, n = replace_identifier(code, "shelf_count", "n_shelves")
    assert n == 1
    assert 'VALUES "n_shelves" RANGE [2, 20]' in new_code
    assert 'LOCK "shelf_thk"' in new_code


def test_values_reference_renamed_in_project(tmp_path):
    """端到端：vl.gdl（VALUES 脚本）里的字符串引用必须与 paramlist 同步。"""
    proj = _project(
        tmp_path,
        [_p("A"), _p("shelf_count")],
        "FOR i = 1 TO shelf_count\nNEXT i\n",
    )
    proj.scripts[ScriptType.PARAM] = 'VALUES "shelf_count" RANGE [2, 20]\n'
    result = align_parameter_names(proj, {"n_shelves": None})
    assert [(r.from_name, r.to_name) for r in result.renamed] == [("shelf_count", "n_shelves")]
    assert 'VALUES "n_shelves"' in proj.scripts[ScriptType.PARAM]


def test_longer_identifier_substring_not_touched(tmp_path):
    code = "shelf_count_max = 2\ny = shelf_count\n"
    new_code, n = replace_identifier(code, "shelf_count", "n_shelves")
    assert n == 1
    assert "shelf_count_max = 2" in new_code
    assert "y = n_shelves" in new_code


# ── 9. 大小写不敏感替换（BLADE_DEPTH → blade_depth）──


def test_case_insensitive_rename(tmp_path):
    proj = _project(
        tmp_path,
        [_p("BLADE_DEPTH")],
        "BLOCK A, blade_depth, 0.02\n",
    )
    result = align_parameter_names(proj, {"blade_depth": None})
    assert [(r.from_name, r.to_name) for r in result.renamed] == [("BLADE_DEPTH", "blade_depth")]
    assert "BLOCK A, blade_depth, 0.02" in proj.scripts[ScriptType.SCRIPT_3D]
    assert proj.parameters[0].name == "blade_depth"


# ── 10. reserved_conflict 的 semantic_bug 分级（C16/C19 场景：B 被当高度用）──


def test_semantic_bug_when_reserved_misused_as_height(tmp_path):
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("n_panels")],
        "ADD 0, 0, B\nBLOCK A, 0.02, B\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})

    assert result.renamed == []  # B 是保留名，不能作为源
    assert result.has_semantic_bug is True
    conflict = result.reserved_conflicts[0]
    assert conflict.expected_name == "ZZYZX"
    assert conflict.reserved_name == "B"
    assert conflict.severity == "semantic_bug"


# ── 11. dry_run 不修改 project ──


def test_dry_run_does_not_mutate(tmp_path):
    proj = _project(tmp_path, [_p("shelf_count")], "FOR i = 1 TO shelf_count\nNEXT i\n")
    before = proj.scripts[ScriptType.SCRIPT_3D]
    result = align_parameter_names(proj, {"n_shelves": None}, dry_run=True)

    assert [(r.from_name, r.to_name) for r in result.renamed] == [("shelf_count", "n_shelves")]
    assert proj.parameters[0].name == "shelf_count"
    assert proj.scripts[ScriptType.SCRIPT_3D] == before


# ── 12. missing_concepts 正确记录 ──


def test_missing_concept_recorded(tmp_path):
    proj = _project(tmp_path, [_p("A")], "BLOCK A, 0.02, 0.02\n")
    result = align_parameter_names(proj, {"col_hw": None, "col_bf": None})
    assert sorted(result.missing_concepts) == ["col_bf", "col_hw"]
    assert result.renamed == []


# ── 13. 规则 2 闸门：有语义的名字不能推断成高度（C19 事故回归）──


def test_role_gate_rejects_semantic_name_for_height(tmp_path):
    """blade_thickness 出现在 BLOCK 第 3 维也不准改名为 ZZYZX。"""
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("blade_thickness")],
        "ADD 0, 0, blade_thickness\nBLOCK A, B, blade_thickness\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})

    assert result.renamed == []
    assert "blade_thickness" in [p.name for p in proj.parameters]
    assert any("语义非高度" in s for s in result.skipped)
    assert "ZZYZX" in result.missing_concepts


def test_role_gate_allows_bare_letter_for_height(tmp_path):
    """裸名单字母（无语义）出现在高度角色位仍允许改名（C15 场景保持）。"""
    proj = _project(tmp_path, [_p("A"), _p("B"), _p("C")], "BLOCK A, B, C\n")
    result = align_parameter_names(proj, {"ZZYZX": None})
    assert [(r.from_name, r.to_name) for r in result.renamed] == [("C", "ZZYZX")]


def test_role_gate_rejects_multiletter_code_for_height(tmp_path):
    """多字母短码（tf=t_flange 事故）即使出现在高度角色位也不准改名。"""
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("tf")],
        "BLOCK A, B, tf\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})
    assert result.renamed == []
    assert "tf" in [p.name for p in proj.parameters]
    assert "ZZYZX" in result.missing_concepts


def test_role_gate_allows_height_semantic_name(tmp_path):
    """高度语义名（base_height）允许改名为 ZZYZX。"""
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("base_height")],
        "BLOCK A, B, base_height\n",
    )
    result = align_parameter_names(proj, {"ZZYZX": None})
    assert [(r.from_name, r.to_name) for r in result.renamed] == [("base_height", "ZZYZX")]
