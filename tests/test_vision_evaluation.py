from math import inf

import pytest

from benchmark.vision_evaluation import StageGate, aggregate_vision_runs


def _run(run_id, **overrides):
    record = {
        "run_id": run_id,
        "skill_id": "cabinet",
        "skill_version": "1.2.0",
        "stage": "H1",
        "split": "holdout",
        "object_id": "cabinet-01",
        "channel": "ordinary",
        "outcome": "passed",
        "required_unknown_count": 0,
        "human_interventions": 0,
        "elapsed_ms": 1000,
        "tokens": 500,
        "cost_usd": 0.01,
    }
    record.update(overrides)
    return record


def test_keeps_skill_stage_channel_and_split_denominators_separate():
    result = aggregate_vision_runs(
        [
            _run("r1"),
            _run("r2", outcome="unknown", required_unknown_count=2),
            _run("r3", channel="codex"),
            _run("r4", stage="H2"),
            _run("r5", split="development", object_id="cabinet-dev"),
            _run("r6", skill_id="lattice_window", object_id="window-01"),
        ]
    )

    assert len(result["reports"]) == 5
    holdout = next(
        row for row in result["reports"]
        if row["skill_id"] == "cabinet" and row["channel"] == "ordinary"
        and row["stage"] == "H1" and row["split"] == "holdout"
    )
    assert (holdout["runs"], holdout["passed"], holdout["unknown"]) == (2, 1, 1)
    assert holdout["pass_rate"] == 0.5
    assert holdout["unknown_rate"] == 0.5
    assert holdout["required_unknown"] == 2
    assert holdout["gate_status"] == "uncalibrated"


def test_declared_gate_fails_required_unknown_even_when_rates_pass():
    result = aggregate_vision_runs(
        [_run("r1", required_unknown_count=1)],
        gates={("cabinet", "1.2.0", "H1"): StageGate(1.0, 0.0)},
        expected_objects={("cabinet", "1.2.0", "H1", "ordinary", "holdout"): ["cabinet-01"]},
    )

    assert result["reports"][0]["gate_status"] == "failed"


def test_declared_gate_passes_only_with_skill_owned_thresholds():
    result = aggregate_vision_runs(
        [_run("r1"), _run("r2", object_id="cabinet-02")],
        gates={("cabinet", "1.2.0", "H1"): StageGate(1.0, 0.0)},
        expected_objects={
            ("cabinet", "1.2.0", "H1", "ordinary", "holdout"): ["cabinet-01", "cabinet-02"]
        },
    )

    report = result["reports"][0]
    assert report["gate_status"] == "passed"
    assert report["objects"] == 2
    assert report["elapsed_ms_total"] == 2000
    assert report["tokens_total"] == 1000
    assert report["cost_usd_total"] == 0.02


def test_missing_manifest_case_stays_in_coverage_and_cannot_pass_gate():
    result = aggregate_vision_runs(
        [_run("r1")],
        gates={("cabinet", "1.2.0", "H1"): StageGate(0.0, 1.0)},
        expected_objects={
            ("cabinet", "1.2.0", "H1", "ordinary", "holdout"): ["cabinet-01", "cabinet-02"]
        },
    )

    report = result["reports"][0]
    assert report["missing_objects"] == ["cabinet-02"]
    assert report["coverage_complete"] is False
    assert report["gate_status"] == "uncalibrated"


def test_entirely_unrun_manifest_group_is_reported_as_missing():
    result = aggregate_vision_runs(
        [],
        expected_objects={
            ("cabinet", "1.2.0", "H1", "ordinary", "holdout"): ["cabinet-01"]
        },
    )

    report = result["reports"][0]
    assert report["runs"] == 0
    assert report["pass_rate"] is None
    assert report["missing_objects"] == ["cabinet-01"]
    assert report["gate_status"] == "uncalibrated"


def test_rejects_runs_for_objects_absent_from_the_frozen_manifest():
    with pytest.raises(ValueError, match="objects absent from manifest"):
        aggregate_vision_runs(
            [_run("r1")],
            expected_objects={
                ("cabinet", "1.2.0", "H1", "ordinary", "holdout"): ["cabinet-02"]
            },
        )


@pytest.mark.parametrize("field, value", [("tokens", -1), ("cost_usd", inf)])
def test_rejects_invalid_cost_and_usage_measurements(field, value):
    with pytest.raises(ValueError, match="must be"):
        aggregate_vision_runs([_run("r1", **{field: value})])


def test_rejects_duplicate_run_ids_and_object_split_leakage():
    with pytest.raises(ValueError, match="duplicate run_id"):
        aggregate_vision_runs([_run("same"), _run("same", object_id="cabinet-02")])

    with pytest.raises(ValueError, match="appears in both holdout and development"):
        aggregate_vision_runs(
            [_run("r1"), _run("r2", split="development")]
        )


@pytest.mark.parametrize(
    "record, message",
    [
        (_run("r1", split="contract"), "split must be development or holdout"),
        (_run("r1", stage="H4"), "unsupported evaluation stage"),
        (_run("r1", outcome="skipped"), "unsupported outcome"),
        ({"run_id": "r1"}, "missing required fields"),
    ],
)
def test_rejects_records_that_cannot_support_a_valid_evaluation(record, message):
    with pytest.raises(ValueError, match=message):
        aggregate_vision_runs([record])
