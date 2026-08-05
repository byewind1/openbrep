"""MCP 工具契约层（openbrep.mcp_tools）契约测试。

只读工具：load_project / compile_hsf / semantic_verify。
契约：dict in / dict out；异常不穿透；统一错误形态；trace_id = mcp-YYYYMMDD-NNNN。
无网络、无 LLM、无 LP_XMLConverter（compile 走 mock）。
"""

import re

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.mcp_tools import compile_hsf, load_project, semantic_verify

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
