from copy import deepcopy

import pytest

from openbrep.runtime.repair_policy import RepairContext, decide_repair


def _context(**changes):
    values = {
        "run_id": "run-1",
        "source_fingerprint": "source-1",
        "plan_id": "plan-1",
        "approved_target_ids": frozenset({"target-1", "target-2"}),
        "remaining_budget": 2,
    }
    values.update(changes)
    return RepairContext(**values)


def _report(*findings):
    return {
        "review_id": "review-1",
        "run_id": "run-1",
        "source_fingerprint": "source-1",
        "plan_id": "plan-1",
        "frame_manifest": [{"frame_id": "view-front"}],
        "findings": list(findings),
    }


def _finding(fid, target, outcome, layer, evidence=None):
    return {
        "finding_id": fid,
        "target_id": target,
        "outcome": outcome,
        "failure_layer": layer,
        "evidence": evidence or [],
    }


@pytest.mark.parametrize(
    ("layer", "action"),
    [
        ("recognition", "reextract"),
        ("plan", "replan"),
        ("implementation", "modify"),
        ("rendering", "observe"),
        ("unobservable", "observe"),
    ],
)
def test_failure_layer_selects_generic_repair_route(layer, action):
    report = _report(_finding("f-1", "target-1", "fail", layer, [{"frame_id": "view-front"}]))

    decision = decide_repair(report, _context())

    assert decision.action == action
    assert decision.finding_ids == ("f-1",)
    assert decision.target_ids == ("target-1",)
    assert decision.remaining_budget == 1


def test_unknown_never_routes_to_source_mutation_and_observation_can_be_disabled():
    report = _report(_finding("f-1", "target-1", "unknown", "implementation"))

    assert decide_repair(report, _context()).action == "observe"
    blocked = decide_repair(report, _context(allow_observation=False))
    assert (blocked.action, blocked.reason) == ("stop", "observation_unavailable")

    bad_reference = _report(_finding("f-ref", "target-1", "fail", "reference", [{"frame_id": "view-front"}]))
    assert decide_repair(bad_reference, _context()).reason == "finding_has_no_safe_repair_route"


def test_stale_binding_unbound_evidence_budget_and_out_of_scope_all_stop():
    report = _report(_finding("f-1", "target-1", "fail", "implementation", [{"frame_id": "fake"}]))
    assert decide_repair(report, _context()).reason == "failed_finding_unbound_evidence"

    valid = _report(_finding("f-1", "target-1", "fail", "implementation", [{"frame_id": "view-front"}]))
    assert decide_repair(valid, _context(source_fingerprint="source-2")).reason == "review_source_stale"
    assert decide_repair(valid, _context(remaining_budget=0)).reason == "repair_budget_exhausted"
    assert decide_repair(valid, _context(approved_target_ids=frozenset())).requires_approval


def test_upstream_cause_wins_and_lower_priority_findings_are_preserved_as_deferred():
    report = _report(
        _finding("f-modify", "target-2", "fail", "implementation", [{"frame_id": "view-front"}]),
        _finding("f-plan", "target-1", "fail", "plan", [{"frame_id": "view-front"}]),
    )

    decision = decide_repair(report, _context())

    assert decision.action == "replan"
    assert decision.finding_ids == ("f-plan",)
    assert decision.deferred_finding_ids == ("f-modify",)


def test_control_mode_requires_approval_for_reextract_or_replan_only():
    plan_failure = _report(_finding("f-plan", "target-1", "fail", "plan", [{"frame_id": "view-front"}]))
    modify_failure = _report(_finding("f-mod", "target-1", "fail", "implementation", [{"frame_id": "view-front"}]))

    assert decide_repair(plan_failure, _context(approval_mode="control")).requires_approval
    assert not decide_repair(modify_failure, _context(approval_mode="control")).requires_approval


def test_report_input_is_not_mutated():
    report = _report(_finding("f-1", "target-1", "fail", "implementation", [{"frame_id": "view-front"}]))
    before = deepcopy(report)
    decide_repair(report, _context())
    assert report == before
