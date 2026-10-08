from __future__ import annotations

from examples.mcp_headless_workflow import run_headless_workflow
from openbrep.hsf_project import HSFProject, ScriptType


def test_headless_workflow_needs_no_model_and_labels_mock_truthfully(tmp_path):
    project = HSFProject.create_new("HeadlessExample", work_dir=str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX")
    root = project.root
    project.save_to_disk()

    result = run_headless_workflow(str(root), compile_mode="mock")

    assert result["ok"] is True
    assert result["delivery"]["compile_mode"] == "mock"
    assert result["delivery"]["compile_success"] is True
    assert result["delivery"]["compile_is_real"] is False
    assert result["delivery"]["source_fingerprint"]
    assert result["delivery"]["visual_evidence_id"].startswith("ev_")
