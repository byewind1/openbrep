"""detect_reserved_param_misuse + verification 集成的合同测试。

保留名被误用到错误维度角色（高度塞进 A/B，或 ZZYZX 当宽度/深度）时：
- detect_reserved_param_misuse 必须检出 semantic_bug
- build_verification_report 必须出现 blocking 的 reserved_param_semantic_bug check
- report.passed 必须为 False（S2 门禁随之生效）
"""

from __future__ import annotations

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.naming_alignment import (
    ReservedConflict,
    detect_reserved_param_misuse,
)
from openbrep.verification import build_verification_report


def _project(tmp_path, params=(), script_3d="") -> HSFProject:
    proj = HSFProject.create_new("misuse_test", work_dir=str(tmp_path))
    proj.parameters = list(params)
    if script_3d:
        proj.scripts[ScriptType.SCRIPT_3D] = script_3d
    return proj


def _p(name: str) -> GDLParameter:
    return GDLParameter(name=name, type_tag="Length", value="1.0")


# ── 1. 高度语义赋给 A → semantic_bug ──


def test_height_stuffed_into_A_detected(tmp_path):
    proj = _project(tmp_path, [_p("A"), _p("B")], "BLOCK A, B, A\n")
    conflicts = detect_reserved_param_misuse(proj)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.expected_name == "ZZYZX"
    assert c.reserved_name == "A"
    assert c.severity == "semantic_bug"


def test_height_stuffed_into_A_via_ADDZ_detected(tmp_path):
    proj = _project(tmp_path, [_p("A"), _p("B")], "ADDZ A\nBLOCK 0.1, 0.1, 0.1\n")
    conflicts = detect_reserved_param_misuse(proj)
    assert any(c.reserved_name == "A" and c.severity == "semantic_bug" for c in conflicts)


# ── 2. 高度语义赋给 B → semantic_bug ──


def test_height_stuffed_into_B_detected(tmp_path):
    proj = _project(tmp_path, [_p("A"), _p("B")], "ADD 0, 0, B\nBLOCK A, 0.02, B\n")
    conflicts = detect_reserved_param_misuse(proj)
    assert len(conflicts) >= 1
    assert conflicts[0].expected_name == "ZZYZX"
    assert conflicts[0].reserved_name == "B"
    assert conflicts[0].severity == "semantic_bug"


# ── 3. ZZYZX 被当宽度用 → semantic_bug ──


def test_zzyzx_misused_as_width_detected(tmp_path):
    proj = _project(tmp_path, [_p("ZZYZX"), _p("B")], "BLOCK ZZYZX, B, 0.02\n")
    conflicts = detect_reserved_param_misuse(proj)
    assert any(
        c.expected_name == "A" and c.reserved_name == "ZZYZX" and c.severity == "semantic_bug"
        for c in conflicts
    )


# ── 4. 正常使用 → 不误报 ──


def test_normal_usage_no_conflict(tmp_path):
    proj = _project(
        tmp_path,
        [_p("A"), _p("B"), _p("ZZYZX")],
        "ADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nBLOCK A, B, ZZYZX\n",
    )
    assert detect_reserved_param_misuse(proj) == []


def test_pipe_od_style_width_usage_not_flagged(tmp_path):
    """A 出现在宽度角色位是正常占用，不是 bug。"""
    proj = _project(tmp_path, [_p("A"), _p("B")], "BLOCK A, B, 0.02\n")
    assert detect_reserved_param_misuse(proj) == []


# ── 5. 空项目不崩溃 ──


def test_empty_project_returns_empty(tmp_path):
    proj = _project(tmp_path, [], "")
    assert detect_reserved_param_misuse(proj) == []


# ── 6. verification 集成：blocking check + passed=False + 说人话 detail ──


def test_verification_report_marks_semantic_bug_blocking(tmp_path):
    conflict = ReservedConflict(
        expected_name="ZZYZX",
        reserved_name="A",
        role_in_script="BLOCK 第 3 维参数位",
        severity="semantic_bug",
    )
    report = build_verification_report(
        intent="CREATE",
        user_input="做一个梁",
        reserved_conflicts=[conflict],
    )
    checks = [c for c in report.checks if c.check_type == "reserved_param_semantic_bug"]
    assert len(checks) == 1
    assert checks[0].status.value == "fail"
    assert report.passed is False
    detail = checks[0].detail
    assert "高度" in detail and "A 在 ArchiCAD 中是宽度" in detail
    assert "建议" in detail


def test_verification_report_ignores_non_bug_conflicts(tmp_path):
    conflict = ReservedConflict(
        expected_name="pipe_od",
        reserved_name="A",
        role_in_script="BLOCK 第 1 维参数位",
        severity="blocked",
    )
    report = build_verification_report(
        intent="CREATE",
        user_input="做弯头",
        reserved_conflicts=[conflict],
    )
    assert not any(c.check_type == "reserved_param_semantic_bug" for c in report.checks)
    assert report.passed is True


# ── 7. pipeline 端到端：CREATE 产物的误用进报告且 success=False ──


def test_pipeline_create_surfaces_reserved_bug(tmp_path):
    from unittest.mock import MagicMock, patch
    from openbrep.compiler import CompileResult
    from openbrep.config import GDLAgentConfig
    from openbrep.llm import LLMResponse
    from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
    from openbrep.semantic_verifier import SemanticVerificationResult

    cfg = GDLAgentConfig()
    pipeline = TaskPipeline(config=cfg, trace_dir="/tmp")
    mock_llm = MagicMock()
    # LLM 生成的脚本把高度塞进了 A（BLOCK A, B, A）
    mock_llm.generate.return_value = LLMResponse(
        content="[FILE: scripts/3d.gdl]\nBLOCK A, B, A\nEND\n"
                "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n",
        model="mock", usage={}, finish_reason="stop",
    )
    pipeline._make_llm = lambda req: mock_llm
    compiler_mock = MagicMock()
    compiler_mock.hsf2libpart.return_value = CompileResult(
        success=True, stdout="", stderr="", mode="mock",
        output_path="/tmp/t.gsm", exit_code=0,
    )
    pipeline._make_compiler = lambda: compiler_mock

    project = HSFProject.create_new("misuse_e2e", work_dir=str(tmp_path))
    project.parameters = [_p("A"), _p("B")]
    with patch(
        "openbrep.semantic_verifier.verify_semantics",
        return_value=SemanticVerificationResult(passed=True),
    ):
        result = pipeline.execute(TaskRequest(
            user_input="做一个构件", intent="CREATE",
            project=project, work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))

    bug_checks = [
        c for c in result.verification["checks"]
        if c["check_type"] == "reserved_param_semantic_bug"
    ]
    assert len(bug_checks) == 1
    assert bug_checks[0]["status"] == "fail"
    assert result.success is False  # S2 门禁自动生效
