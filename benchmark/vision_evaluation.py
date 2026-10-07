"""Stage-aware aggregation for first-party visual evaluation runs.

This module evaluates supplied run records only. It does not decide domain
truth, choose samples, or call models; those remain owned by each Domain Skill.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

STAGES = {"H1", "H2", "H3"}
SPLITS = {"development", "holdout"}
OUTCOMES = {"passed", "failed", "unknown"}


def _nonnegative_int(record: Mapping[str, Any], field: str) -> int:
    value = int(record.get(field, 0))
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _nonnegative_float(record: Mapping[str, Any], field: str) -> float:
    value = float(record.get(field, 0.0))
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return value


@dataclass(frozen=True)
class StageGate:
    """A threshold declared by the owning skill before evaluation starts."""

    min_pass_rate: float
    max_unknown_rate: float
    required_unknown_zero: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_pass_rate <= 1.0:
            raise ValueError("min_pass_rate must be between 0 and 1")
        if not 0.0 <= self.max_unknown_rate <= 1.0:
            raise ValueError("max_unknown_rate must be between 0 and 1")


def aggregate_vision_runs(
    records: Iterable[Mapping[str, Any]],
    *,
    gates: Mapping[tuple[str, str, str], StageGate] | None = None,
    expected_objects: Mapping[tuple[str, str, str, str, str], Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Aggregate runs without pooling skills, channels, stages, or data splits.

    Required record keys are ``run_id``, ``skill_id``, ``skill_version``,
    ``stage``, ``split``, ``object_id``, ``channel``, and ``outcome``. Optional
    cost/time/intervention values are summed and averaged over every run,
    including failed and unknown runs. Repeats are retained as separate runs.
    """

    gates = gates or {}
    normalized_expected: dict[tuple[str, str, str, str, str], set[str]] = {}
    for key, object_ids in (expected_objects or {}).items():
        skill_id, skill_version, stage, channel, split = key
        if stage not in STAGES or split not in SPLITS:
            raise ValueError(f"invalid expected-object group: {key}")
        expected = {str(item) for item in object_ids}
        if not expected or "" in expected:
            raise ValueError(f"expected object ids must be non-empty: {key}")
        normalized_expected[(skill_id, skill_version, stage, channel, split)] = expected
    seen_runs: set[str] = set()
    object_splits: dict[tuple[str, str, str], str] = {}
    groups: dict[tuple[str, str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)

    for index, raw in enumerate(records):
        record = dict(raw)
        required = (
            "run_id",
            "skill_id",
            "skill_version",
            "stage",
            "split",
            "object_id",
            "channel",
            "outcome",
        )
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"run record {index} missing required fields: {', '.join(missing)}")

        run_id = str(record["run_id"])
        if run_id in seen_runs:
            raise ValueError(f"duplicate run_id: {run_id}")
        seen_runs.add(run_id)

        stage = str(record["stage"]).upper()
        split = str(record["split"])
        outcome = str(record["outcome"])
        if stage not in STAGES:
            raise ValueError(f"unsupported evaluation stage: {stage}")
        if split not in SPLITS:
            raise ValueError(f"evaluation split must be development or holdout: {split}")
        if outcome not in OUTCOMES:
            raise ValueError(f"unsupported outcome: {outcome}")

        skill_id = str(record["skill_id"])
        skill_version = str(record["skill_version"])
        object_id = str(record["object_id"])
        object_key = (skill_id, skill_version, object_id)
        prior_split = object_splits.setdefault(object_key, split)
        if prior_split != split:
            raise ValueError(
                f"object {skill_id}@{skill_version}/{object_id} appears in both "
                f"{prior_split} and {split} splits"
            )

        record["stage"] = stage
        record["split"] = split
        record["outcome"] = outcome
        key = (
            skill_id,
            skill_version,
            stage,
            str(record["channel"]),
            split,
        )
        groups[key].append(record)

    reports: list[dict[str, Any]] = []
    for key, runs in sorted(groups.items()):
        skill_id, skill_version, stage, channel, split = key
        total = len(runs)
        passed = sum(run["outcome"] == "passed" for run in runs)
        failed = sum(run["outcome"] == "failed" for run in runs)
        unknown = sum(run["outcome"] == "unknown" for run in runs)
        required_unknown = sum(_nonnegative_int(run, "required_unknown_count") for run in runs)
        gate = gates.get((skill_id, skill_version, stage))
        pass_rate = passed / total
        unknown_rate = unknown / total
        expected_key = (skill_id, skill_version, stage, channel, split)
        expected = normalized_expected.get(expected_key)
        observed_objects = {str(run["object_id"]) for run in runs}
        missing_objects = sorted(expected - observed_objects) if expected is not None else []
        unexpected_objects = sorted(observed_objects - expected) if expected is not None else []
        coverage_complete = expected is not None and not missing_objects and not unexpected_objects

        if gate is None or not coverage_complete:
            gate_status = "uncalibrated"
        else:
            gate_status = "passed"
            if pass_rate < gate.min_pass_rate or unknown_rate > gate.max_unknown_rate:
                gate_status = "failed"
            if gate.required_unknown_zero and required_unknown:
                gate_status = "failed"

        reports.append(
            {
                "skill_id": skill_id,
                "skill_version": skill_version,
                "stage": stage,
                "channel": channel,
                "split": split,
                "runs": total,
                "objects": len(observed_objects),
                "expected_objects": len(expected) if expected is not None else None,
                "missing_objects": missing_objects,
                "unexpected_objects": unexpected_objects,
                "coverage_complete": coverage_complete,
                "passed": passed,
                "failed": failed,
                "unknown": unknown,
                "required_unknown": required_unknown,
                "pass_rate": pass_rate,
                "unknown_rate": unknown_rate,
                "human_interventions": sum(_nonnegative_int(run, "human_interventions") for run in runs),
                "elapsed_ms_total": sum(_nonnegative_int(run, "elapsed_ms") for run in runs),
                "tokens_total": sum(_nonnegative_int(run, "tokens") for run in runs),
                "cost_usd_total": round(sum(_nonnegative_float(run, "cost_usd") for run in runs), 8),
                "gate_status": gate_status,
                "gate": (
                    {
                        "min_pass_rate": gate.min_pass_rate,
                        "max_unknown_rate": gate.max_unknown_rate,
                        "required_unknown_zero": gate.required_unknown_zero,
                    }
                    if gate
                    else None
                ),
                "run_ids": [str(run["run_id"]) for run in runs],
            }
        )

    for key, expected in normalized_expected.items():
        if key in groups:
            observed = {str(run["object_id"]) for run in groups[key]}
            if observed - expected:
                raise ValueError(
                    f"runs contain objects absent from manifest for {key}: "
                    f"{', '.join(sorted(observed - expected))}"
                )
        else:
            skill_id, skill_version, stage, channel, split = key
            reports.append(
                {
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "stage": stage,
                    "channel": channel,
                    "split": split,
                    "runs": 0,
                    "objects": 0,
                    "expected_objects": len(expected),
                    "missing_objects": sorted(expected),
                    "unexpected_objects": [],
                    "coverage_complete": False,
                    "passed": 0,
                    "failed": 0,
                    "unknown": 0,
                    "required_unknown": 0,
                    "pass_rate": None,
                    "unknown_rate": None,
                    "human_interventions": 0,
                    "elapsed_ms_total": 0,
                    "tokens_total": 0,
                    "cost_usd_total": 0.0,
                    "gate_status": "uncalibrated",
                    "gate": None,
                    "run_ids": [],
                }
            )

    reports.sort(
        key=lambda row: (
            row["skill_id"], row["skill_version"], row["stage"], row["channel"], row["split"]
        )
    )

    return {"schema_version": 1, "reports": reports}
