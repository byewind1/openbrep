"""Deterministic routing for evidence-bound repair candidates.

The policy only returns a proposal. It never calls an LLM, mutates an HSF, or
widens the approved scope; a caller must enforce RunControl and commit gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

RepairAction = Literal["reextract", "replan", "modify", "observe", "stop"]
ApprovalMode = Literal["auto", "control"]

_ACTION_BY_LAYER: dict[str, RepairAction] = {
    "recognition": "reextract",
    "plan": "replan",
    "implementation": "modify",
    "rendering": "observe",
    "unobservable": "observe",
    "reference": "stop",
    "unknown": "stop",
}
_PRIORITY = {"reextract": 0, "replan": 1, "modify": 2, "observe": 3, "stop": 4}


@dataclass(frozen=True)
class RepairContext:
    run_id: str
    source_fingerprint: str
    plan_id: str
    approved_target_ids: frozenset[str]
    remaining_budget: int
    approval_mode: ApprovalMode = "auto"
    allow_observation: bool = True
    allow_reextract: bool = True
    allow_replan: bool = True


@dataclass(frozen=True)
class RepairDecision:
    action: RepairAction
    reason: str
    run_id: str
    source_fingerprint: str
    plan_id: str
    review_id: str
    finding_ids: tuple[str, ...] = ()
    target_ids: tuple[str, ...] = ()
    deferred_finding_ids: tuple[str, ...] = ()
    remaining_budget: int = 0
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_repair(report: Mapping[str, Any], context: RepairContext) -> RepairDecision:
    """Select one bounded repair direction from a persisted visual review.

    The report is rejected when it is stale, detached from the active run/Plan,
    or cites frames outside its own frozen manifest. Only failed findings are
    repair candidates; unknowns can request another observation but never imply
    failure or pass. A single action is selected per decision to retain a clear
    causal chain and make every later run a fresh evidence boundary.
    """

    review_id = str(report.get("review_id") or "")
    common = {
        "run_id": context.run_id,
        "source_fingerprint": context.source_fingerprint,
        "plan_id": context.plan_id,
        "review_id": review_id,
        "remaining_budget": max(0, int(context.remaining_budget)),
    }

    def stop(reason: str, *, approval: bool = False) -> RepairDecision:
        return RepairDecision(action="stop", reason=reason, requires_approval=approval, **common)

    if report.get("run_id") != context.run_id:
        return stop("review_run_mismatch")
    if report.get("source_fingerprint") != context.source_fingerprint:
        return stop("review_source_stale")
    if report.get("plan_id") != context.plan_id:
        return stop("review_plan_stale", approval=context.approval_mode == "control")
    if context.remaining_budget <= 0:
        return stop("repair_budget_exhausted")

    manifest = report.get("frame_manifest")
    if not isinstance(manifest, list):
        return stop("review_manifest_missing")
    frame_ids = {str(item.get("frame_id")) for item in manifest if isinstance(item, Mapping)}
    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        return stop("review_findings_missing")
    candidates: list[tuple[int, str, str, str]] = []
    for finding in raw_findings:
        if not isinstance(finding, Mapping):
            return stop("review_finding_invalid")
        outcome = str(finding.get("outcome") or "")
        if outcome not in {"pass", "fail", "unknown"}:
            return stop("review_outcome_invalid")
        evidence = finding.get("evidence")
        if not isinstance(evidence, list):
            return stop("review_evidence_invalid")
        if outcome == "fail" and (not evidence or any(
            not isinstance(item, Mapping) or str(item.get("frame_id") or "") not in frame_ids
            for item in evidence
        )):
            return stop("failed_finding_unbound_evidence")
        if outcome not in {"fail", "unknown"}:
            continue
        target_id = str(finding.get("target_id") or "")
        finding_id = str(finding.get("finding_id") or "")
        if not target_id or not finding_id:
            return stop("review_finding_identity_missing")
        layer = str(finding.get("failure_layer") or "unknown")
        action = "observe" if outcome == "unknown" else _ACTION_BY_LAYER.get(layer, "stop")
        candidates.append((_PRIORITY[action], finding_id, target_id, action))

    if not candidates:
        return stop("no_repair_candidate")
    if any(item[3] == "stop" for item in candidates):
        return stop("finding_has_no_safe_repair_route")
    candidates.sort()
    _, _, _, action = candidates[0]
    if action == "stop":
        return stop("no_safe_repair_route")
    selected = [item for item in candidates if item[3] == action]
    finding_ids = tuple(sorted({item[1] for item in selected}))
    target_ids = tuple(sorted({item[2] for item in selected}))
    outside_scope = sorted(set(target_ids) - context.approved_target_ids)
    if outside_scope:
        decision = stop("repair_exceeds_approved_scope", approval=True)
        return RepairDecision(
            **{**decision.to_dict(), "finding_ids": finding_ids, "target_ids": tuple(outside_scope)}
        )
    if action == "observe" and not context.allow_observation:
        return stop("observation_unavailable")
    if action == "reextract" and not context.allow_reextract:
        return stop("reextraction_unavailable")
    if action == "replan" and not context.allow_replan:
        return stop("replanning_unavailable")

    return RepairDecision(
        action=action,
        reason=f"evidence_routes_to_{action}",
        finding_ids=finding_ids,
        target_ids=target_ids,
        deferred_finding_ids=tuple(sorted(item[1] for item in candidates if item[3] != action)),
        remaining_budget=max(0, context.remaining_budget - 1),
        requires_approval=context.approval_mode == "control" and action in {"reextract", "replan"},
        **{key: value for key, value in common.items() if key != "remaining_budget"},
    )
