"""MCP 工具契约层（openbrep.mcp_tools）契约测试。

只读工具：load_project / compile_hsf / semantic_verify；
证据/导入工具：render_evidence / import_source。
契约：dict in / dict out；异常不穿透；统一错误形态；trace_id = mcp-YYYYMMDD-NNNN。
无网络、无 LLM、无 LP_XMLConverter（compile 走 mock，gsm 导入断言 converter_unavailable）。
"""

import re
from pathlib import Path

import pytest

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.mcp_tools import (
    compile_hsf,
    import_source,
    load_project,
    render_evidence,
    semantic_verify,
)

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
    assert TRACE_RE.match(result["trace_id"])


def test_import_source_gsm_converter_unavailable_when_missing(tmp_path, monkeypatch):
    class FakeCompiler:
        converter_path = "/fake/LP_XMLConverter"

        @property
        def is_available(self):
            return False

    monkeypatch.setattr("openbrep.mcp_tools.HSFCompiler", FakeCompiler)
    src = Path(__file__).resolve().parents[1] / "examples" / "closet.gsm"
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
