from openbrep.contracts.object_spec import Requirement
from openbrep.contracts.requirement_execution import execute_requirements


def test_requirements_execute_independently_and_keep_requirement_identity():
    requirements = [
        Requirement("r.compile", "compile", check_id="compile", strength="required"),
        Requirement("r.semantic", "semantic", check_id="semantic", params={"tolerance": 0.1}, strength="required"),
    ]
    evaluation = execute_requirements(
        requirements,
        {
            "source_fingerprint": "src-a",
            "checks": {
                "compile": {"status": "pass", "reason": "compiled"},
                "semantic": {"status": "fail", "reason": "bbox mismatch"},
            },
        },
    )
    assert evaluation.status == "failed"
    assert evaluation.required_total == 2
    assert evaluation.required_passed == 1
    assert [r.requirement_id for r in evaluation.results] == ["r.compile", "r.semantic"]
    assert evaluation.results[0].source_fingerprint == "src-a"
    assert evaluation.results[1].reason == "bbox mismatch"


def test_unknown_missing_and_stale_required_evidence_never_pass():
    requirements = [Requirement("r.missing", "required", check_id="compile", strength="required")]
    missing = execute_requirements(requirements, {"source_fingerprint": "src-a"})
    assert missing.status == "incomplete"
    assert missing.results[0].status == "not_run"

    stale = execute_requirements(
        requirements,
        {"source_fingerprint": "src-b", "source_stale": True,
         "checks": {"compile": {"status": "pass"}}},
    )
    assert stale.status == "stale"
    assert stale.required_passed == 0
    assert stale.results[0].status == "unverified"


def test_empty_requirements_do_not_claim_all_standards_passed():
    evaluation = execute_requirements([], {})
    assert evaluation.status == "not_applicable"


def test_verification_report_can_project_requirement_completion():
    from openbrep.verification import build_verification_report

    report = build_verification_report(
        intent="MODIFY",
        requirements=[Requirement("r.host", "load in Archicad", check_id="compile", strength="required")],
        requirement_context={"checks": {"compile": {"status": "not_run", "reason": "LP unavailable"}}},
    )
    payload = report.to_dict()
    assert payload["requirement_evaluation"]["status"] == "incomplete"
    assert payload["requirements_passed"] is False
    assert payload["passed"] is True  # legacy gate retains its established meaning
    assert payload["requirement_evaluation"]["results"][0]["requirement_id"] == "r.host"
    assert "LP unavailable" in report.requirement_evaluation["results"][0]["reason"]
