"""MCP 工具契约层（openbrep.mcp_tools）契约测试。

只读工具：load_project / compile_hsf / semantic_verify；
证据/导入工具：render_evidence / import_source；
mutation 工具：apply_edit（draft/apply）/ rollback。
契约：dict in / dict out；异常不穿透；统一错误形态；trace_id = mcp-YYYYMMDD-NNNN。
无网络、无 LLM、无 LP_XMLConverter（compile 走 mock，gsm 导入断言 converter_unavailable）。
"""

import json
import re
from pathlib import Path

import pytest

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.mcp_tools import (
    apply_edit,
    compile_hsf,
    deprecate_skill,
    import_source,
    list_skills,
    load_project,
    propose_skill,
    render_evidence,
    reuse_skill,
    rollback,
    semantic_verify,
    verify_skill,
)
from openbrep.revisions import get_latest_revision_id
from openbrep.skills_loader import SkillsLoader

TRACE_RE = re.compile(r"^mcp-\d{8}-\d{4}$")


def _make_project(tmp_path, name="Shelf"):
    project = HSFProject.create_new(name, str(tmp_path))
    hsf_dir = project.save_to_disk()
    return project, hsf_dir


def test_load_project_returns_full_profile(tmp_path):
    project, hsf_dir = _make_project(tmp_path)
    result = load_project(str(hsf_dir))
    assert result["ok"] is True
    assert result["name"] == "Shelf"
    assert result["parameter_count"] == 3  # A / B / ZZYZX
    assert result["scripts_present"] == ["SCRIPT_3D"]
    assert result["ac_version"] == 46
    assert result["latest_revision_id"] is None
    assert TRACE_RE.match(result["trace_id"])


def test_load_project_missing_path_returns_unified_error(tmp_path):
    result = load_project(str(tmp_path / "does-not-exist"))
    assert result["ok"] is False
    assert result["error"]["code"] == "project_not_found"
    assert "message" in result["error"]
    assert TRACE_RE.match(result["trace_id"])


def test_compile_hsf_mock_mode_succeeds_on_valid_project(tmp_path):
    _project, hsf_dir = _make_project(tmp_path)
    result = compile_hsf(str(hsf_dir), mode="mock")
    assert result["ok"] is True
    assert result["mode"] == "mock"
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["errors"] == []
    assert result["output_path"].endswith(".gsm")
    assert not result["output_path"].startswith(str(hsf_dir))
    assert TRACE_RE.match(result["trace_id"])


def test_compile_hsf_missing_libpartdata_returns_unified_error(tmp_path):
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    result = compile_hsf(str(bad_dir), mode="mock")
    assert result["ok"] is False
    assert result["error"]["code"] == "project_not_found"
    assert TRACE_RE.match(result["trace_id"])


def test_semantic_verify_detects_bbox_mismatch_as_blocking(tmp_path):
    project = HSFProject.create_new("Mismatch", str(tmp_path))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 3, 3, 3\n"
    hsf_dir = project.save_to_disk()
    result = semantic_verify(str(hsf_dir))
    assert result["ok"] is True
    assert result["passed"] is False
    bbox = [i for i in result["issues"] if i["check_type"] == "bbox_mismatch"]
    assert bbox
    assert all(i["blocking"] for i in bbox)


def test_semantic_verify_well_formed_project_passes(tmp_path):
    _project, hsf_dir = _make_project(tmp_path)  # BLOCK A, B, ZZYZX == 1,1,1
    result = semantic_verify(str(hsf_dir))
    assert result["ok"] is True
    assert result["passed"] is True


def test_all_tools_return_unified_error_shape_for_missing_path(tmp_path):
    missing = str(tmp_path / "nope")
    for tool in (load_project, compile_hsf, semantic_verify):
        result = tool(missing)
        assert result["ok"] is False
        assert set(result["error"]) == {"code", "message"}
        assert result["error"]["code"] == "project_not_found"
        assert result["error"]["message"]
        assert TRACE_RE.match(result["trace_id"])


# ── render_evidence ──────────────────────────────────────


def test_render_evidence_reports_bbox_and_declared_dims_match(tmp_path):
    project = HSFProject.create_new("EvidenceBox", str(tmp_path))
    for name, value in (("A", "0.5"), ("B", "0.4"), ("ZZYZX", "0.3")):
        project.get_parameter(name).value = value
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 0.5, 0.4, 0.3\n"
    hsf_dir = project.save_to_disk()

    result = render_evidence(str(hsf_dir))

    assert result["ok"] is True
    assert result["bbox"] is not None
    for actual, expected in zip(result["bbox"]["size"], [0.5, 0.4, 0.3]):
        assert actual == pytest.approx(expected, abs=1e-6)
    assert result["mesh_stats"]["mesh_count"] == 1
    assert result["mesh_stats"]["vertex_count"] >= 4
    assert result["declared_dims"] == {"A": 0.5, "B": 0.4, "ZZYZX": 0.3}
    assert result["bbox_vs_declared"]["match"] is True
    assert result["sweep"] == []
    assert TRACE_RE.match(result["trace_id"])


def test_render_evidence_sweep_reports_bbox_response(tmp_path):
    project = HSFProject.create_new("SweepBox", str(tmp_path))
    project.get_parameter("A").value = "2.0"  # 非 boolean-ish，扰动走缩放而非翻转
    hsf_dir = project.save_to_disk()
    result = render_evidence(str(hsf_dir), sweep_params=["A"])
    assert result["ok"] is True
    assert len(result["sweep"]) == 1
    entry = result["sweep"][0]
    assert entry["param"] == "A"
    assert entry["base_value"] == 2.0
    assert entry["bbox_response"] is not None
    assert entry["bbox_response"]["size"][0] == pytest.approx(3.0, abs=1e-6)
    assert entry["passed"] is True


def test_render_evidence_degrades_when_preview_crashes(tmp_path, monkeypatch):
    # 真实 previewer 对畸形脚本是降级为 warning 而非抛异常（已确认，例如
    # "BLOCK x/0, 1, 1" 产生表达式解析失败 warning、mesh 为 0）；这里用
    # monkeypatch 强制 preview_3d_script 抛异常来覆盖崩溃降级分支。
    _project, hsf_dir = _make_project(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("preview exploded")

    monkeypatch.setattr("openbrep.workbench.preview_service.preview_3d_script", _boom)
    result = render_evidence(str(hsf_dir))
    assert result["ok"] is True
    assert result["bbox"] is None
    assert result["mesh_stats"]["mesh_count"] == 0
    assert result["mesh_stats"]["warnings"]
    assert result["sweep"] == []
    assert TRACE_RE.match(result["trace_id"])


def test_render_evidence_bbox_tolerance_controls_match(tmp_path):
    # 声明 A=B=ZZYZX=1.0，几何画成 1.2,1.0,1.0 → 最大维度差 20%：
    # tolerance=0.05 时 match=False，tolerance=0.5 时 match=True；deltas 全量报告。
    project = HSFProject.create_new("TolBox", str(tmp_path))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1.2, 1.0, 1.0\n"
    hsf_dir = project.save_to_disk()

    strict = render_evidence(str(hsf_dir), tolerance=0.05)
    assert strict["ok"] is True
    assert strict["bbox_vs_declared"]["match"] is False
    assert strict["bbox_vs_declared"]["deltas"]

    loose = render_evidence(str(hsf_dir), tolerance=0.5)
    assert loose["ok"] is True
    assert loose["bbox_vs_declared"]["match"] is True
    assert loose["bbox_vs_declared"]["deltas"] == strict["bbox_vs_declared"]["deltas"]

    # 默认 tolerance=0.05（5%），与 semantic_verifier 的 0.5 无关。
    default = render_evidence(str(hsf_dir))
    assert default["bbox_vs_declared"]["match"] is False
    assert default["bbox_vs_declared"]["deltas"] == strict["bbox_vs_declared"]["deltas"]
    assert TRACE_RE.match(strict["trace_id"])


def test_render_evidence_surfaces_preview_warnings(tmp_path):
    project = HSFProject.create_new("WarnBox", str(tmp_path))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK x/0, 1, 1\n"
    hsf_dir = project.save_to_disk()
    result = render_evidence(str(hsf_dir))
    assert result["ok"] is True
    assert result["mesh_stats"]["mesh_count"] == 0
    assert any("解析失败" in w for w in result["mesh_stats"]["warnings"])


# ── import_source ────────────────────────────────────────


def test_import_source_gdl_creates_project(tmp_path):
    src = Path(__file__).resolve().parents[1] / "examples" / "Bookshelf.gdl"
    target = tmp_path / "imports"
    result = import_source(str(src), "gdl", str(target))
    assert result["ok"] is True
    assert result["warnings"] == []
    project_path = Path(result["project_path"])
    assert str(project_path).startswith(str(target))
    assert project_path.is_dir()
    loaded = load_project(str(project_path))
    assert loaded["ok"] is True
    assert loaded["name"] == "Bookshelf"
    assert loaded["parameter_count"] == 10
    assert TRACE_RE.match(result["trace_id"])


def test_import_source_gdl_parses_parameters_and_sections(tmp_path):
    src = Path(__file__).resolve().parents[1] / "examples" / "Bookshelf.gdl"
    target = tmp_path / "imports"
    result = import_source(str(src), "gdl", str(target))
    assert result["ok"] is True

    project = HSFProject.load_from_disk(str(Path(result["project_path"])))

    # 参数注释块 → 真实 GDLParameter，类型映射正确（不再静默只剩 A/B/ZZYZX）
    by_name = {p.name: p for p in project.parameters}
    assert set(by_name) >= {"A", "B", "ZZYZX", "nShelves", "thickness", "sideW",
                            "hasBack", "backThick", "frameMat", "shelfMat"}
    assert by_name["nShelves"].type_tag == "Integer"
    assert by_name["nShelves"].value == "4"
    assert by_name["hasBack"].type_tag == "Boolean"
    assert by_name["frameMat"].type_tag == "Material"

    # MASTER SCRIPT 内容进 master（1d.gdl），而不是 3D
    master = project.get_script(ScriptType.MASTER)
    script_3d = project.get_script(ScriptType.SCRIPT_3D)
    assert "IF A < 0.30" in master
    assert "IF A < 0.30" not in script_3d
    assert "BLOCK A, B, thickness" in script_3d


def test_import_source_gdl_reports_unparseable_param_lines_with_line_number(tmp_path):
    src = tmp_path / "BrokenParams.gdl"
    src.write_text(
        "! ====\n"
        "! A           Length    0.80    书架宽度\n"
        "! nShelves    Integer\n"
        "! B           Length    0.30    书架深度\n"
        "! ====\n"
        "! 3D SCRIPT\n"
        "BLOCK A, B, ZZYZX\n",
        encoding="utf-8",
    )
    result = import_source(str(src), "gdl", str(tmp_path / "imports"))
    assert result["ok"] is True
    assert any("第 3 行" in w and "nShelves" in w for w in result["warnings"])

    # 损坏行没有静默丢进项目，其余参数照常解析
    project = HSFProject.load_from_disk(str(Path(result["project_path"])))
    names = {p.name for p in project.parameters}
    assert "A" in names and "B" in names
    assert "nShelves" not in names


def test_import_source_gsm_converter_unavailable_when_missing(tmp_path, monkeypatch):
    class FakeCompiler:
        converter_path = "/fake/LP_XMLConverter"

        @property
        def is_available(self):
            return False

    monkeypatch.setattr("openbrep.mcp_tools.HSFCompiler", FakeCompiler)
    # 不依赖仓库里的 .gsm  fixture（*.gsm 在 .gitignore 中，CI 上不存在）；
    # converter 可用性检查发生在读取文件内容之前，dummy 文件即可。
    src = tmp_path / "dummy.gsm"
    src.write_bytes(b"dummy")
    result = import_source(str(src), "gsm", str(tmp_path / "imports"))
    assert result["ok"] is False
    assert result["error"]["code"] == "converter_unavailable"
    assert result["error"]["message"]
    assert TRACE_RE.match(result["trace_id"])


def test_import_source_blender_py_passes_through_unsupported_warnings(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tests" / "fixtures" / "blender" / "with_unsupported.py"
    result = import_source(str(fixture), "blender_py", str(tmp_path / "imports"))
    assert result["ok"] is True
    assert any("modifier_add" in w for w in result["warnings"])
    project_path = Path(result["project_path"])
    assert project_path.is_dir()
    loaded = load_project(str(project_path))
    assert loaded["ok"] is True
    assert TRACE_RE.match(result["trace_id"])


def test_import_source_unified_error_shape_for_bad_inputs(tmp_path):
    missing = str(tmp_path / "does-not-exist.gdl")
    result = import_source(missing, "gdl", str(tmp_path))
    assert result["ok"] is False
    assert result["error"]["code"] == "project_not_found"
    assert "message" in result["error"]
    assert TRACE_RE.match(result["trace_id"])

    src = tmp_path / "oops.gdl"
    src.write_text("BLOCK A, B, ZZYZX\n")
    bad_kind = import_source(str(src), "bogus", str(tmp_path))
    assert bad_kind["ok"] is False
    assert bad_kind["error"]["code"] == "invalid_mode"
    assert "message" in bad_kind["error"]

    bad_suffix = import_source(str(src), "blender_py", str(tmp_path))
    assert bad_suffix["ok"] is False
    assert bad_suffix["error"]["code"] == "invalid_mode"
    assert "message" in bad_suffix["error"]


# ── apply_edit / rollback（P1-c，mutation） ────────────────


def _make_editable_project(tmp_path, name="Shelf"):
    """含已知参数值与 3D 脚本的项目，返回 (root, project)。"""
    project = HSFProject.create_new(name, str(tmp_path))
    project.get_parameter("A").value = "1.5"
    project.get_parameter("B").value = "0.6"
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
    root = project.save_to_disk()
    return root, project


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(fp.relative_to(root)): fp.read_bytes() for fp in root.rglob("*") if fp.is_file()}


def test_apply_edit_draft_is_zero_persistence(tmp_path):
    root, _project = _make_editable_project(tmp_path)
    before = _tree_bytes(root)
    latest_before = get_latest_revision_id(root)

    result = apply_edit(str(root), {"type": "set_parameters", "values": {"A": 2.5}}, mode="draft")

    assert result["ok"] is True
    assert result["mode"] == "draft"
    assert result["diff"]
    assert "paramlist.xml" in result["diff"]
    assert result["compile"]["success"] is True
    assert result["compile"]["mode"] == "mock"
    assert result["verify"]["passed"] is True
    assert TRACE_RE.match(result["trace_id"])

    # 字节级零改动 + 最新 revision 不变
    assert _tree_bytes(root) == before
    assert get_latest_revision_id(root) == latest_before


def test_apply_edit_apply_parameters_persists_and_records_trace_id(tmp_path):
    root, _project = _make_editable_project(tmp_path)

    result = apply_edit(str(root), {"type": "set_parameters", "values": {"A": 2.5}}, mode="apply")

    assert result["ok"] is True
    assert result["mode"] == "apply"
    assert result["revision_id"]
    assert result["diff"]
    assert result["compile"]["success"] is True
    assert result["recent_revisions"]  # 非空，最近 5 条

    loaded = HSFProject.load_from_disk(str(root))
    assert float(loaded.get_parameter("A").value) == pytest.approx(2.5)

    manifest = json.loads(
        (root / ".openbrep" / "revisions" / result["revision_id"] / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["metadata"]["trace_id"] == result["trace_id"]
    assert manifest["metadata"]["tool_spec"]["type"] == "set_parameters"
    assert manifest["metadata"]["tool_spec"]["values"] == {"A": 2.5}
    assert TRACE_RE.match(result["trace_id"])


def test_apply_edit_apply_script_replaces_content(tmp_path):
    root, _project = _make_editable_project(tmp_path)
    new_script = "BLOCK 2, 2, 2\n"

    result = apply_edit(
        str(root),
        {"type": "set_script", "script_type": "3d", "content": new_script},
        mode="apply",
    )

    assert result["ok"] is True
    assert result["revision_id"]
    assert "scripts/3d.gdl" in result["diff"]
    loaded = HSFProject.load_from_disk(str(root))
    assert loaded.get_script(ScriptType.SCRIPT_3D) == new_script
    assert TRACE_RE.match(result["trace_id"])


def test_apply_edit_apply_then_rollback_previous_restores(tmp_path):
    root, _project = _make_editable_project(tmp_path)
    apply_result = apply_edit(str(root), {"type": "set_parameters", "values": {"A": 2.5}}, mode="apply")
    assert float(HSFProject.load_from_disk(str(root)).get_parameter("A").value) == pytest.approx(2.5)

    result = rollback(str(root), "previous")

    assert result["ok"] is True
    assert result["restored_revision"] == apply_result["revision_id"]
    assert result["new_revision_id"]
    assert TRACE_RE.match(result["trace_id"])
    # 恢复原值
    assert float(HSFProject.load_from_disk(str(root)).get_parameter("A").value) == pytest.approx(1.5)
    # 产生 trigger="rollback" 的 revision（manifest 与 recent_revisions 都能看到）
    assert result["recent_revisions"][-1]["trigger"] == "rollback"
    assert result["recent_revisions"][-1]["id"] == result["new_revision_id"]
    rollback_manifest = json.loads(
        (root / ".openbrep" / "revisions" / result["new_revision_id"] / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert rollback_manifest["trigger"] == "rollback"
    assert rollback_manifest["metadata"]["restored_from"] == apply_result["revision_id"]


def test_apply_edit_invalid_specs_return_unified_error(tmp_path):
    root, _project = _make_editable_project(tmp_path)
    bad_specs = [
        {"type": "bogus"},  # 未知 type
        {"type": "set_parameters", "values": {"nonexistent_param": 1.0}},  # 参数不存在
        {"type": "set_script", "script_type": "bad", "content": "BLOCK 1, 1, 1\n"},  # 脚本类型非法
    ]
    for spec in bad_specs:
        result = apply_edit(str(root), spec, mode="draft")
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_spec"
        assert result["error"]["message"]
        assert TRACE_RE.match(result["trace_id"])
    # 原项目目录不受非法 spec 影响
    assert float(HSFProject.load_from_disk(str(root)).get_parameter("A").value) == pytest.approx(1.5)


# ── propose_skill / verify_skill（P2-b，skill 晋升门禁） ─────


def test_propose_skill_writes_file_with_full_frontmatter_and_never_overwrites(tmp_path):
    skills_dir = tmp_path / "skills"
    result = propose_skill(
        "shelf_pattern",
        "# 书架策略\n\n## 触发关键词\n- 书架\n",
        pattern_type="box-geometry",
        source_project="Demo",
        source_trace_id="trace-1",
        skills_dir=str(skills_dir),
    )
    assert result["ok"] is True
    assert result["status"] == "proposed"
    path = Path(result["path"])
    assert path == skills_dir / "shelf_pattern.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    for field in (
        "status: proposed",
        "skill_version: 1",
        "pattern_type: box-geometry",
        "source_project: Demo",
        "source_trace_id: trace-1",
        "reuse_count: 0",
        "last_used: null",
    ):
        assert field in text
    assert "## 触发关键词" in text  # 正文保留

    # 同名再 propose → skill_exists，不覆盖
    dup = propose_skill("shelf_pattern", "其他内容", skills_dir=str(skills_dir))
    assert dup["ok"] is False
    assert dup["error"]["code"] == "skill_exists"
    assert TRACE_RE.match(dup["trace_id"])
    assert "其他内容" not in path.read_text(encoding="utf-8")

    # SkillsLoader 状态过滤端到端：proposed 不注入
    loader = SkillsLoader(str(skills_dir))
    injected = loader.get_for_task("生成一个书架")
    assert "shelf_pattern" not in injected
    assert loader.skill_names_by_status("proposed") == ["shelf_pattern"]
    assert loader.get_by_name("shelf_pattern") is None


def test_propose_skill_rejects_unsafe_names_and_non_string_content(tmp_path):
    skills_dir = tmp_path / "skills"
    for bad in ("", "   ", "a/b", "a\\b", " foo", "bar ", ".", "..", ".hidden", "README", "a:b"):
        result = propose_skill(bad, "内容", skills_dir=str(skills_dir))
        assert result["ok"] is False, bad
        assert result["error"]["code"] == "invalid_spec", bad
        assert TRACE_RE.match(result["trace_id"])
    bad_content = propose_skill("good_name", 123, skills_dir=str(skills_dir))
    assert bad_content["ok"] is False
    assert bad_content["error"]["code"] == "invalid_spec"


def test_verify_skill_with_compilable_slice_promotes_and_becomes_injectable(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_slice = {
        "params": {
            "A": 2.0, "B": 3.0, "ZZYZX": 4.0, "nShelves": 3, "hasBack": True, "label": "demo"
        },
        "scripts": {"3d": "BLOCK A, B, ZZYZX\n"},
    }
    propose_skill(
        "shelf_pattern",
        "# 书架策略\n\n## 触发关键词\n- 书架\n",
        pattern_type="box",
        source_project="Demo",
        slice=skill_slice,
        skills_dir=str(skills_dir),
    )

    result = verify_skill("shelf_pattern", skills_dir=str(skills_dir))

    assert result["ok"] is True
    assert result["gate"] == "full"
    assert result["passed"] is True
    assert result["status"] == "verified"
    assert result["evidence"]["gate"] == "full"
    assert result["evidence"]["compile"]["success"] is True
    assert result["evidence"]["compile"]["mode"] == "mock"
    assert result["evidence"]["semantic"]["passed"] is True
    assert result["evidence"]["at"]
    assert TRACE_RE.match(result["trace_id"])

    # verified_evidence 落盘 + status 翻 verified
    on_disk = (skills_dir / "shelf_pattern.md").read_text(encoding="utf-8")
    assert "status: verified" in on_disk
    assert "verified_evidence:" in on_disk
    assert "gate: full" in on_disk

    # 翻完 loader 能注入它
    loader = SkillsLoader(str(skills_dir))
    injected = loader.get_for_task("生成一个书架")
    assert "## Skill: shelf_pattern" in injected
    assert loader.skill_meta("shelf_pattern")["status"] == "verified"
    ev = loader.skill_meta("shelf_pattern")["verified_evidence"]
    assert ev["gate"] == "full"
    assert ev["compile_success"] is True
    assert ev["compile_mode"] == "mock"
    assert ev["semantic_passed"] is True


def test_verify_skill_with_broken_slice_fails_and_keeps_proposed(tmp_path):
    skills_dir = tmp_path / "skills"
    bad_slice = {
        "params": {"A": 2.0, "B": 3.0, "ZZYZX": 4.0},
        "scripts": {"3d": "FOR i = 1 TO 3\nBLOCK A, B, ZZYZX\n"},  # FOR/NEXT 不匹配 → mock 编译失败
    }
    propose_skill(
        "broken_slice_skill",
        "# 策略\n",
        pattern_type="box",
        slice=bad_slice,
        skills_dir=str(skills_dir),
    )

    result = verify_skill("broken_slice_skill", skills_dir=str(skills_dir))

    assert result["ok"] is True
    assert result["gate"] == "full"
    assert result["passed"] is False
    assert result["status"] == "proposed"
    assert result["evidence"]["compile"]["success"] is False
    assert result["evidence"]["compile"]["errors"]
    assert TRACE_RE.match(result["trace_id"])

    on_disk = (skills_dir / "broken_slice_skill.md").read_text(encoding="utf-8")
    assert "status: proposed" in on_disk
    assert "verified_evidence:" not in on_disk
    loader = SkillsLoader(str(skills_dir))
    assert loader.skill_meta("broken_slice_skill")["status"] == "proposed"
    assert loader.get_by_name("broken_slice_skill") is None


def test_verify_skill_structural_gate_requires_trigger_section_and_pattern_type(tmp_path):
    skills_dir = tmp_path / "skills"
    propose_skill(
        "strategy_skill",
        "# 策略\n\n## 触发关键词\n- 书架\n",
        pattern_type="structural-pattern",
        skills_dir=str(skills_dir),
    )
    ok_result = verify_skill("strategy_skill", skills_dir=str(skills_dir))
    assert ok_result["ok"] is True
    assert ok_result["gate"] == "structural"
    assert ok_result["passed"] is True
    assert ok_result["status"] == "verified"
    assert ok_result["evidence"]["structural"]["frontmatter_complete"] is True
    assert ok_result["evidence"]["structural"]["trigger_section"] is True
    assert ok_result["evidence"]["at"]

    # 缺触发词 → 不通过，status 保持 proposed
    propose_skill(
        "no_trigger_skill",
        "# 没有触发词小节\n",
        pattern_type="structural-pattern",
        skills_dir=str(skills_dir),
    )
    fail_result = verify_skill("no_trigger_skill", skills_dir=str(skills_dir))
    assert fail_result["ok"] is True
    assert fail_result["gate"] == "structural"
    assert fail_result["passed"] is False
    assert fail_result["status"] == "proposed"
    assert fail_result["evidence"]["structural"]["trigger_section"] is False
    assert "verified_evidence:" not in (
        skills_dir / "no_trigger_skill.md"
    ).read_text(encoding="utf-8")

    # pattern_type 为空 → frontmatter 不完整 → 不通过
    propose_skill(
        "no_pattern_skill", "# 策略\n\n## 触发关键词\n- 书架\n", skills_dir=str(skills_dir)
    )
    no_pattern = verify_skill("no_pattern_skill", skills_dir=str(skills_dir))
    assert no_pattern["ok"] is True
    assert no_pattern["passed"] is False
    assert no_pattern["evidence"]["structural"]["frontmatter_complete"] is False


def test_verify_skill_missing_returns_skill_not_found(tmp_path):
    result = verify_skill("ghost_skill", skills_dir=str(tmp_path / "skills"))
    assert result["ok"] is False
    assert result["error"]["code"] == "skill_not_found"
    assert "message" in result["error"]
    assert TRACE_RE.match(result["trace_id"])


# ── reuse_skill / list_skills / deprecate_skill（P2-c，skill 管理） ─────


def _write_skill_file(skills_dir: Path, name: str, body: str, **fields) -> Path:
    """写一个带 frontmatter 的 skill 文件（fields 覆盖默认值，用于构造状态）。"""
    skills_dir.mkdir(parents=True, exist_ok=True)
    meta = {"status": "active", "reuse_count": 0, "last_used": "null"}
    meta.update(fields)
    lines = ["---"] + [f"{k}: {v}" for k, v in meta.items()] + ["---", "", body]
    path = skills_dir / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_reuse_skill_matches_active_and_excludes_proposed(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_file(
        skills_dir,
        "create_object",
        "# 创建对象策略\n\n## 触发关键词\n- 生成\n- 创建\n",
        pattern_type="box",
        reuse_count=3,
        last_used="2026-01-01",
    )
    propose_skill(
        "shelf_pattern",
        "# 书架策略\n\n## 触发关键词\n- 书架\n",
        skills_dir=str(skills_dir),
    )

    result = reuse_skill("生成一个书架", skills_dir=str(skills_dir))

    assert result["ok"] is True
    assert result["query"] == "生成一个书架"
    names = [m["name"] for m in result["matched"]]
    assert "create_object" in names
    assert "shelf_pattern" not in names  # proposed 不参与注入
    match = next(m for m in result["matched"] if m["name"] == "create_object")
    assert match["status"] == "active"
    assert match["pattern_type"] == "box"
    assert match["reuse_count"] == 4  # 调用即计复用（P2-a 写回）
    assert "创建对象策略" in match["excerpt"]
    assert len(match["excerpt"]) <= 200
    assert "## Skill: create_object" in result["skills_text"]
    assert TRACE_RE.match(result["trace_id"])


def test_list_skills_lists_all_filters_by_status_and_rejects_invalid(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_file(
        skills_dir,
        "active_one",
        "active body",
        pattern_type="box",
        skill_version=2,
        reuse_count=5,
        last_used="2026-02-01",
        source_project="ProjA",
    )
    _write_skill_file(
        skills_dir,
        "verified_one",
        "verified body",
        status="verified",
        pattern_type="pattern",
    )
    propose_skill("proposed_one", "proposed body", skills_dir=str(skills_dir))
    deprecate_skill("active_one", skills_dir=str(skills_dir))  # → deprecated

    all_result = list_skills(skills_dir=str(skills_dir))
    assert all_result["ok"] is True
    assert all_result["total"] == 3
    by_name = {s["name"]: s for s in all_result["skills"]}
    assert by_name["active_one"]["status"] == "deprecated"
    assert by_name["active_one"]["pattern_type"] == "box"
    assert by_name["active_one"]["skill_version"] == 2
    assert by_name["active_one"]["reuse_count"] == 5
    assert by_name["active_one"]["last_used"] == "2026-02-01"
    assert by_name["active_one"]["source_project"] == "ProjA"
    assert by_name["verified_one"]["status"] == "verified"
    assert by_name["proposed_one"]["status"] == "proposed"

    active = list_skills(status="active", skills_dir=str(skills_dir))
    assert active["total"] == 0  # active_one 已被翻 deprecated
    verified = list_skills(status="verified", skills_dir=str(skills_dir))
    assert [s["name"] for s in verified["skills"]] == ["verified_one"]
    proposed = list_skills(status="proposed", skills_dir=str(skills_dir))
    assert [s["name"] for s in proposed["skills"]] == ["proposed_one"]
    deprecated = list_skills(status="deprecated", skills_dir=str(skills_dir))
    assert [s["name"] for s in deprecated["skills"]] == ["active_one"]
    assert TRACE_RE.match(all_result["trace_id"])

    bad = list_skills(status="bogus", skills_dir=str(skills_dir))
    assert bad["ok"] is False
    assert bad["error"]["code"] == "invalid_mode"
    assert "message" in bad["error"]
    assert TRACE_RE.match(bad["trace_id"])


def test_deprecate_skill_flips_status_stops_injection_and_is_idempotent(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_file(
        skills_dir,
        "create_object",
        "# 创建对象策略\n\n## 触发关键词\n- 生成\n- 创建\n",
        pattern_type="box",
    )

    result = deprecate_skill("create_object", skills_dir=str(skills_dir))
    assert result["ok"] is True
    assert result["name"] == "create_object"
    assert result["status"] == "deprecated"
    assert TRACE_RE.match(result["trace_id"])

    on_disk = (skills_dir / "create_object.md").read_text(encoding="utf-8")
    assert "status: deprecated" in on_disk

    loader = SkillsLoader(str(skills_dir))
    assert loader.skill_meta("create_object")["status"] == "deprecated"
    assert "create_object" not in loader.get_for_task("生成一个书架")
    assert loader.get_by_name("create_object") is None

    again = deprecate_skill("create_object", skills_dir=str(skills_dir))
    assert again["ok"] is True
    assert again["status"] == "deprecated"

    missing = deprecate_skill("ghost_skill", skills_dir=str(skills_dir))
    assert missing["ok"] is False
    assert missing["error"]["code"] == "skill_not_found"
    assert "message" in missing["error"]
    assert TRACE_RE.match(missing["trace_id"])


def test_skill_lifecycle_propose_verify_reuse_deprecate(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_slice = {
        "params": {"A": 2.0, "B": 3.0, "ZZYZX": 4.0},
        "scripts": {"3d": "BLOCK A, B, ZZYZX\n"},
    }
    propose_skill(
        "shelf_pattern",
        "# 书架策略\n\n## 触发关键词\n- 书架\n",
        pattern_type="box",
        source_project="Demo",
        slice=skill_slice,
        skills_dir=str(skills_dir),
    )
    verified = verify_skill("shelf_pattern", skills_dir=str(skills_dir))
    assert verified["ok"] is True
    assert verified["status"] == "verified"

    hit = reuse_skill("书架", skills_dir=str(skills_dir))
    assert hit["ok"] is True
    assert [m["name"] for m in hit["matched"]] == ["shelf_pattern"]
    assert hit["skills_text"]
    assert hit["matched"][0]["status"] == "verified"
    assert hit["matched"][0]["excerpt"]

    deprecate = deprecate_skill("shelf_pattern", skills_dir=str(skills_dir))
    assert deprecate["ok"] is True
    assert deprecate["status"] == "deprecated"

    miss = reuse_skill("书架", skills_dir=str(skills_dir))
    assert miss["ok"] is True
    assert miss["matched"] == []
    assert miss["skills_text"] == ""
    assert TRACE_RE.match(miss["trace_id"])
