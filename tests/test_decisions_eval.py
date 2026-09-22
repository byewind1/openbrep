"""D0 evaluation harness tests: pure metrics, gates, schema, backends (no network).

The module under test lives in ``scripts/`` and is loaded through importlib, the
same pattern the obr7 launcher tests use. Every backend here is a fake: the LLM
baseline is injected as a callable and the TypeSafe client as a stub, so the
suite never touches the network or the user's configuration.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _offline_typesafe_sdk(monkeypatch):
    """Keep the suite hermetic: the optional typesafe-sdk is never required.

    The TypeSafe backend imports ``Choice`` from the SDK at call time; CI installs
    only the declared extras, so every test runs against this stub (whether or not
    the real SDK happens to be installed on the developer's machine).
    """

    module = types.ModuleType("typesafe_sdk")

    @dataclass
    class Choice:
        instructions: str
        criteria: dict

    module.Choice = Choice
    module.TypeSafeClient = object
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)


def _load_module():
    import sys

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "decisions_eval", root / "scripts" / "decisions_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


d0 = _load_module()


class _FakeTypeSafeAnswer:
    def __init__(self, choice: str, confidence: float, probabilities: dict[str, float]):
        self.choice = choice
        self.confidence = confidence
        self.probabilities = probabilities


class _FakeUsage:
    input_tokens = 466
    output_tokens = 46


class _FakeTypeSafeResponse:
    def __init__(self, label: str, confidence: float = 0.94, model: str = "jev-test"):
        self.answers = {
            d0.QUESTION_ID: _FakeTypeSafeAnswer(
                label, confidence, {label: confidence, "NONE": round(1.0 - confidence, 2)}
            )
        }
        self.model = model
        self.usage = _FakeUsage()


class _FakeTypeSafeClient:
    """Records the request and answers from a script."""

    def __init__(self, answers: dict[str, str], error: Exception | None = None):
        self._answers = answers
        self._error = error
        self.calls: list[dict] = []

    def system_one(self, *, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, "kwargs": kwargs})
        if self._error is not None:
            raise self._error
        label = self._answers.get(state["message"], "NONE")
        return _FakeTypeSafeResponse(label)


# ── schema, ids, privacy ─────────────────────────────────────────────────────

def test_sample_id_is_stable_and_content_addressed():
    assert d0.sample_id("把漏窗做法存成技能") == d0.sample_id("  把漏窗做法存成技能  ")
    assert d0.sample_id("a") != d0.sample_id("b")
    assert len(d0.sample_id("x")) == 12


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("看看 /Users/ren/project/3d.gdl 这段", "absolute path"),
        ("Authorization: Bearer abc123", "bearer token"),
        ("api_key = 'test-placeholder-value'", "api key assignment"),
        ("用 sk-abcdef123456 这个 key", "provider key"),
        ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "jwt"),
        ("token 0123456789abcdef0123456789abcdef", "long hex secret"),
    ],
)
def test_sensitive_text_is_rejected(text, reason):
    assert d0.sensitive_reason(text) == reason


def test_normal_instruction_is_not_flagged_and_metadata_is_derived():
    record = d0.make_record("把旋转楼梯的层板做法存成技能，参数名用 A/B/ZZYZX")

    assert d0.sensitive_reason(record.text) is None
    assert record.has_cjk is True
    assert {"参数", "旋转楼梯", "层板"} <= set(record.gdl_terms)
    assert d0.validate_record(record)  # label missing -> reported, not raised


def test_validate_record_reports_each_rule():
    good = d0.make_record("生成一个方块", label="NONE", labeler="human")
    assert d0.validate_record(good) == []

    unlabeled = d0.make_record("生成一个方块")
    assert any("label missing" in issue for issue in d0.validate_record(unlabeled))

    bad_label = d0.make_record("生成一个方块", label="SKILLISH", labeler="human")
    assert any("not in" in issue for issue in d0.validate_record(bad_label))

    no_labeler = d0.make_record("生成一个方块", label="NONE")
    assert any("labeler" in issue for issue in d0.validate_record(no_labeler))

    sensitive = d0.make_record(
        "看看 /Users/ren/secret/3d.gdl", label="NONE", labeler="human"
    )
    assert any("sensitive" in issue for issue in d0.validate_record(sensitive))


def test_dataset_stats_counts_labels_languages_and_verification():
    records = [
        d0.make_record("把漏窗做法存成技能", label="CREATE_SKILL", labeler="human"),
        d0.make_record("现在有哪些技能", label="LIST_SKILLS", labeler="human"),
        d0.make_record("生成一个层板构件", label="NONE", labeler="human"),
        d0.make_record("block 参数怎么写", label="NONE", labeler="assistant-draft"),
        d0.make_record("no label yet"),
    ]

    stats = d0.dataset_stats(records)

    assert stats["total"] == 5
    assert stats["labels"] == {"CREATE_SKILL": 1, "LIST_SKILLS": 1, "NONE": 2}
    assert stats["chinese"] == 4
    assert stats["gdl_terms"] >= 1
    assert stats["unlabeled"] == 1
    assert stats["human_verified"] is False  # the draft record breaks verification


# ── candidate building ───────────────────────────────────────────────────────

def _write_trace(directory: Path, index: int, text: str, intent: str = "CHAT") -> None:
    (directory / f"t_{index:03d}.json").write_text(
        json.dumps({"input_summary": text, "intent": intent}), encoding="utf-8"
    )


def test_build_candidates_filters_dedupes_and_is_deterministic(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    _write_trace(traces, 1, "生成一个旋转楼梯")
    _write_trace(traces, 2, "生成一个旋转楼梯")  # duplicate text
    _write_trace(traces, 3, "看看 /Users/ren/project/secret.gdl")
    _write_trace(traces, 4, "把漏窗做法存成技能")
    _write_trace(traces, 5, "no intent field", intent="")

    diagnostics: dict = {}
    first = d0.build_candidates(
        traces, per_intent=10, seed=1, include_seed_samples=False, stats_out=diagnostics
    )
    second = d0.build_candidates(traces, per_intent=10, seed=1, include_seed_samples=False)

    texts = sorted(record.text for record in first)
    assert texts == ["把漏窗做法存成技能", "生成一个旋转楼梯"]
    assert diagnostics["skipped_sensitive"] == 1
    assert diagnostics["corpus_duplicate_texts"] == 1
    # Must not shadow the dataset-scope counter read by the unique_samples gate.
    assert "duplicate_texts" not in diagnostics
    assert diagnostics["available_per_intent"] == {"CHAT": 2}
    assert [record.id for record in first] == [record.id for record in second]
    assert all(record.label is None for record in first)


def test_build_candidates_respects_the_per_intent_cap(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    for index in range(8):
        _write_trace(traces, index, f"第 {index} 条不同的指令")

    records = d0.build_candidates(
        traces, per_intent=3, seed=5, include_seed_samples=False
    )

    assert len(records) == 3


def test_seed_samples_cover_the_boundary_classes():
    records = d0.build_candidates("traces-do-not-exist", include_seed_samples=True)

    texts = [record.text for record in records]
    assert len(records) == len(d0._SEED_SAMPLES)
    assert any("列出" in text for text in texts)
    assert any("存成" in text for text in texts)
    assert any("旋转楼梯" in text for text in texts)


def test_draft_labels_mark_themselves_as_not_verdict_grade():
    records = [
        d0.make_record("把漏窗的做法存成技能"),
        d0.make_record("现在有哪些技能？"),
        d0.make_record("生成一个方块"),
    ]

    labeled = d0.draft_labels(records)

    assert [record.label for record in labeled] == ["CREATE_SKILL", "LIST_SKILLS", "NONE"]
    assert all(record.labeler == "assistant-draft" for record in labeled)
    assert all("human verification" in record.notes for record in labeled)


def test_record_id_is_derived_from_the_stored_text():
    """A truncated record must not look like a distinct sample from its own text."""

    long_text = "把漏窗的做法存成技能 " + "细节 " * 200
    record = d0.make_record(long_text)

    assert len(record.text) == d0._MAX_TEXT_CHARS
    assert record.id == d0.sample_id(record.text)
    # Two long texts sharing the first 400 chars collapse to one sample.
    other = d0.make_record(long_text + "另一段结尾")
    assert other.id == record.id


def test_dataset_writers_refuse_paths_inside_a_git_worktree(tmp_path):
    worktree = tmp_path / "repo"
    (worktree / ".git").mkdir(parents=True)

    with pytest.raises(SystemExit):
        d0.assert_not_in_git_worktree(worktree / "dataset.jsonl")

    outside = tmp_path / "elsewhere" / "dataset.jsonl"
    d0.assert_not_in_git_worktree(outside)  # must not raise


# ── backends ─────────────────────────────────────────────────────────────────

def test_rule_backend_labels_and_is_deterministic():
    backend = d0.RuleBackend()

    assert backend.classify("把这段做法存成技能").label == "CREATE_SKILL"
    assert backend.classify("现在有哪些技能").label == "LIST_SKILLS"
    assert backend.classify("生成一个旋转楼梯").label == "NONE"


def test_llm_backend_uses_the_injected_classifier_and_maps_unknown_to_none():
    calls: list[str] = []

    def classify(text: str) -> str:
        calls.append(text)
        return "list_skills"  # lower case on purpose

    backend = d0.LLMBackend(classify=classify)
    result = backend.classify("有哪些技能")

    assert calls == ["有哪些技能"]
    assert result.label == "LIST_SKILLS"
    assert result.confidence is None  # the production classifier has no probability

    assert d0.LLMBackend(classify=lambda _text: "maybe").classify("x").label == "NONE"


def test_llm_backend_reports_errors_instead_of_raising():
    def boom(_text: str) -> str:
        raise RuntimeError("upstream down")

    result = d0.LLMBackend(classify=boom).classify("你好")

    assert result.label is None
    assert "upstream down" in result.error


def test_typesafe_backend_sends_the_frozen_question_and_parses_the_answer():
    client = _FakeTypeSafeClient({"把漏窗做法存成技能": "CREATE_SKILL"})
    backend = d0.TypeSafeBackend(client=client)

    result = backend.classify("把漏窗做法存成技能")

    assert result.label == "CREATE_SKILL"
    assert result.confidence == 0.94
    assert result.probabilities == {"CREATE_SKILL": 0.94, "NONE": 0.06}
    assert result.input_tokens == 466 and result.output_tokens == 46
    call = client.calls[0]
    assert call["state"] == {"message": "把漏窗做法存成技能"}
    question = call["questions"][d0.QUESTION_ID]
    assert question.instructions == d0.QUESTION_INSTRUCTIONS
    assert set(question.criteria) == set(d0.LABELS)


def test_backend_identity_reports_what_actually_ran():
    """The report's provenance block must not be decorative."""

    assert d0.LLMBackend(classify=lambda _text: "NONE", model="m1").identity() == "m1"
    assert d0.RuleBackend().identity() is None

    # The fake declares model="jev-test" the way the SDK reports response.model.
    backend = d0.TypeSafeBackend(client=_FakeTypeSafeClient({"把漏窗做法存成技能": "CREATE_SKILL"}))
    assert backend.identity() is None          # nothing observed before the call
    backend.classify("把漏窗做法存成技能")

    assert backend.identity() == "jev-test"


def test_baseline_override_only_counts_as_an_override_when_it_changes_the_model():
    assert d0.baseline_override_of(None, "prod") is None
    assert d0.baseline_override_of("prod", "prod") is None      # pinning is not overriding
    assert d0.baseline_override_of("weaker", "prod") == "weaker"
    assert d0.baseline_override_of("weaker", None) == "weaker"  # unknown production
    assert d0.production_model() == d0.production_model()       # resolvable, never raises


def test_cmd_run_records_provenance_and_refuses_an_overridden_baseline(tmp_path):
    """The CLI is the documented entry point: its wiring needs its own test."""

    records = [
        d0.make_record(f"生成第 {index} 个方块", label="NONE", labeler="human")
        for index in range(3)
    ]
    dataset = tmp_path / "hand.jsonl"
    d0.write_jsonl(dataset, records)

    def run(*extra: str) -> dict:
        args = d0.build_parser().parse_args(
            [
                "--data-dir",
                str(tmp_path / "data"),
                "run",
                "--dataset",
                str(dataset),
                "--backends",
                "rule",
                *extra,
            ]
        )
        assert d0.cmd_run(args) == 0
        report = sorted((tmp_path / "data" / "reports").glob("*/report.json"))[-1]
        return json.loads(report.read_text(encoding="utf-8"))

    overridden = run("--baseline-model", "some-weaker-model")
    rows = {row["gate"]: row["status"] for row in overridden["gates"]["dataset_gates"]}
    assert rows["production_baseline"] == "FAIL"
    assert any("overridden" in reason for reason in overridden["gates"]["reasons"])
    assert overridden["config"]["baseline_model"] == "some-weaker-model"
    assert overridden["config"]["backend_identity"] == {"rule": None}

    plain = run()
    rows = {row["gate"]: row["status"] for row in plain["gates"]["dataset_gates"]}
    assert rows["production_baseline"] == "PASS"
    assert plain["config"]["baseline_model"] is None


def test_typesafe_backend_reports_errors_instead_of_raising():
    backend = d0.TypeSafeBackend(client=_FakeTypeSafeClient({}, error=TimeoutError("slow")))

    result = backend.classify("你好")

    assert result.label is None
    assert "TimeoutError" in result.error


def test_laya_backend_reports_the_missing_extra():
    try:
        d0.LayaBackend()
    except RuntimeError as exc:
        assert "laya not installed" in str(exc)
    else:  # pragma: no cover - only when the optional extra is installed
        assert d0.LayaBackend().classify("x").label is None


def test_build_backends_reports_unavailable_ones_without_failing():
    backends, unavailable = d0.build_backends(
        ["rule", "laya", "typesafe"], typesafe_client=_FakeTypeSafeClient({})
    )

    assert [backend.name for backend in backends] == ["rule", "typesafe"]
    assert "laya" in unavailable


def test_evaluate_backend_resolves_failures_through_the_fallback():
    records = [d0.make_record("把漏窗做法存成技能") for _ in range(4)]
    primary = d0.TypeSafeBackend(client=_FakeTypeSafeClient({}, error=TimeoutError("down")))
    fallback = d0.LLMBackend(classify=lambda _text: "CREATE_SKILL")

    results = d0.evaluate_backend(primary, records, fallback=fallback)

    assert all(result.label == "CREATE_SKILL" for result in results)
    assert all(result.fallback_used for result in results)
    assert all("TimeoutError" in result.error for result in results)


def test_failure_injection_marks_fallbacks_and_keeps_labels():
    records = [d0.make_record(f"样本 {index}") for index in range(20)]
    primary = d0.RuleBackend()
    fallback = d0.LLMBackend(classify=lambda _text: "NONE")

    results = d0.evaluate_backend(
        primary, records, fallback=fallback, failure_rate=0.5
    )

    assert sum(1 for result in results if result.fallback_used) > 0
    assert all(result.label in d0.LABELS for result in results)


# ── metrics ──────────────────────────────────────────────────────────────────

def test_metrics_compute_confusion_rates_and_latency_percentiles():
    records = [
        d0.make_record("技能一", label="CREATE_SKILL", labeler="human"),
        d0.make_record("技能二", label="LIST_SKILLS", labeler="human"),
        d0.make_record("普通一", label="NONE", labeler="human"),
        d0.make_record("普通二", label="NONE", labeler="human"),
    ]
    results = [
        d0.BackendResult(label="CREATE_SKILL", latency_ms=10.0),
        d0.BackendResult(label="NONE", latency_ms=20.0),  # misses one positive
        d0.BackendResult(label="CREATE_SKILL", latency_ms=30.0),  # false skill
        d0.BackendResult(label="NONE", latency_ms=40.0),
    ]

    metrics = d0.compute_metrics(records, results)

    assert metrics["confusion"]["CREATE_SKILL"]["CREATE_SKILL"] == 1
    assert metrics["confusion"]["NONE"]["CREATE_SKILL"] == 1
    assert metrics["positive_recall"] == 0.5
    assert metrics["positive_miss_rate"] == 0.5
    assert metrics["false_skill_rate"] == 0.5
    assert metrics["per_label"]["LIST_SKILLS"]["recall"] == 0.0
    assert metrics["per_label"]["CREATE_SKILL"]["precision"] == 0.5
    assert metrics["latency_p50_ms"] == 25.0
    assert metrics["error_rate"] == 0.0


def test_metrics_count_errors_fallbacks_and_missing_labels():
    records = [
        d0.make_record("a", label="NONE", labeler="human"),
        d0.make_record("b", label="NONE", labeler="human"),
    ]
    results = [
        d0.BackendResult(label=None, error="boom", latency_ms=5.0),
        d0.BackendResult(label="NONE", latency_ms=7.0, fallback_used=True, error="boom"),
    ]

    metrics = d0.compute_metrics(records, results)

    assert metrics["unknown_or_failed"] == 1
    assert metrics["scored"] == 1
    assert metrics["fallback_used"] == 1
    # Both records carried an error (one unresolved, one resolved by fallback),
    # and failures must stay visible even when no label came out.
    assert metrics["error_rate"] == 1.0
    assert metrics["error_samples"] == ["boom"]
    assert metrics["false_skill_rate"] == 0.0


def test_expected_calibration_error_matches_a_hand_computed_case():
    # 4 predictions at 0.9 confidence, all correct -> gap 0.1
    assert d0.expected_calibration_error([(0.9, True)] * 4, bins=5) == pytest.approx(0.1)
    # 4 predictions at 0.9 confidence, all wrong -> gap 0.9
    assert d0.expected_calibration_error([(0.9, False)] * 4, bins=5) == pytest.approx(0.9)
    assert d0.expected_calibration_error([]) is None


def test_percentile_interpolates_between_neighbours():
    assert d0.percentile([], 0.5) == 0.0
    assert d0.percentile([4.0], 0.95) == 4.0
    assert d0.percentile([0.0, 10.0], 0.5) == 5.0
    assert d0.percentile([0.0, 10.0, 20.0], 1.0) == 20.0


# ── gates ────────────────────────────────────────────────────────────────────

def _fingerprint() -> str:
    """The fingerprint shared by the synthetic stats/metrics helpers."""

    return d0.dataset_fingerprint([d0.make_record("合成样本")])


def _stats(total: int, chinese: int, gdl: int, *, human: bool = True) -> dict:
    return {
        "total": total,
        "chinese": chinese,
        "gdl_terms": gdl,
        "labels": {"CREATE_SKILL": 1, "LIST_SKILLS": 1, "NONE": 1},
        "unlabeled": 0,
        "labelers": ["human"] if human else ["assistant-draft"],
        "human_verified": human,
        "duplicate_ids": 0,
        "duplicate_texts": 0,
        # Real stats always carry this; the report writer requires it.
        "fingerprint": _fingerprint(),
    }


def _metrics(
    recall: float,
    false_skill: float,
    *,
    error_rate: float = 0.0,
    fallback_used: int = 0,
    total: int = 400,
) -> dict:
    """Metric shape as produced by compute_metrics (healthy backend by default)."""

    return {
        "fingerprint": _fingerprint(),
        "positive_recall": recall,
        "positive_miss_rate": round(1 - recall, 4),
        "false_skill_rate": false_skill,
        "latency_p50_ms": 1.0,
        "latency_p95_ms": 2.0,
        "calibration_ece": None,
        "error_rate": error_rate,
        "error_samples": [],
        "fallback_used": fallback_used,
        "total": total,
        "scored": total,
        "unknown_or_failed": 0,
    }


def test_gates_refuse_a_verdict_when_the_dataset_is_too_small():
    result = d0.evaluate_gates(
        _stats(10, 5, 1),
        {
            "llm": _metrics(0.8, 0.1, total=10),
            "typesafe": _metrics(0.9, 0.05, total=10),
        },
    )

    assert result["verdict"] == "INSUFFICIENT"
    assert [row["status"] for row in result["dataset_gates"]] == [
        "FAIL",
        "FAIL",
        "FAIL",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "NOT_PROBED",
    ]
    assert any("dataset gates" in reason for reason in result["reasons"])


def test_unlabeled_dataset_is_never_reported_as_human_verified():
    stats = d0.dataset_stats([d0.make_record("生成一个方块"), d0.make_record("生成一个柱")])

    assert stats["unlabeled"] == 2
    assert stats["human_verified"] is False
    assert d0.dataset_stats([])["human_verified"] is False


def test_gates_block_a_partially_labeled_dataset():
    stats = _stats(400, 200, 80)
    stats["unlabeled"] = 3
    stats["human_verified"] = False

    result = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)})

    assert result["verdict"] == "INSUFFICIENT"
    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["all_labeled"] == "FAIL"


def test_gates_refuse_a_verdict_when_labels_are_not_human_verified():
    result = d0.evaluate_gates(
        _stats(400, 200, 80, human=False),
        {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)},
    )

    assert result["verdict"] == "INSUFFICIENT"
    assert any("human-verified" in reason for reason in result["reasons"])
    statuses = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert statuses == {
        "min_total": "PASS",
        "min_chinese": "PASS",
        "min_gdl_terms": "PASS",
        "all_labeled": "PASS",
        "unique_samples": "PASS",
        "metrics_match_dataset": "PASS",
        "production_baseline": "PASS",
        "baseline_sanity": "NOT_PROBED",
    }


def test_gates_pass_only_when_recall_and_false_skill_rate_clear_the_baseline():
    passing = d0.evaluate_gates(
        _stats(400, 200, 80),
        {"llm": _metrics(0.8, 0.10), "typesafe": _metrics(0.76, 0.05)},
    )
    assert passing["verdict"] == "GO"
    assert passing["per_backend"]["typesafe"]["passed"] is True

    weak_recall = d0.evaluate_gates(
        _stats(400, 200, 80),
        {"llm": _metrics(0.8, 0.10), "typesafe": _metrics(0.60, 0.05)},
    )
    assert weak_recall["verdict"] == "NO-GO"
    checks = {
        check["gate"]: check["status"]
        for check in weak_recall["per_backend"]["typesafe"]["checks"]
    }
    assert checks["positive_recall_ratio_vs_llm"] == "FAIL"
    assert checks["false_skill_rate_not_worse_than_llm"] == "PASS"

    worse_wizard = d0.evaluate_gates(
        _stats(400, 200, 80),
        {"llm": _metrics(0.8, 0.10), "typesafe": _metrics(0.9, 0.20)},
    )
    assert worse_wizard["verdict"] == "NO-GO"


def test_gates_reject_a_backend_that_only_inherits_the_baseline():
    """A backend that fails on everything must not be scored as the baseline."""

    result = d0.evaluate_gates(
        _stats(400, 200, 80),
        {
            "llm": _metrics(0.8, 0.10),
            # Identical numbers to the baseline - exactly what fallback yields.
            "typesafe": _metrics(0.8, 0.10, error_rate=1.0, fallback_used=400),
        },
    )

    assert result["verdict"] == "NO-GO"
    checks = {
        check["gate"]: check["status"]
        for check in result["per_backend"]["typesafe"]["checks"]
    }
    assert checks["backend_answers_for_itself"] == "FAIL"
    assert checks["positive_recall_ratio_vs_llm"] == "PASS"
    assert result["per_backend"]["typesafe"]["passed"] is False


def test_gates_reject_duplicate_samples():
    stats = _stats(400, 200, 80)
    stats["duplicate_ids"] = 297

    result = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)})

    assert result["verdict"] == "INSUFFICIENT"
    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["unique_samples"] == "FAIL"


def test_unscored_records_keep_the_recall_denominator_honest():
    records = [
        d0.make_record("技能一", label="CREATE_SKILL", labeler="human"),
        d0.make_record("技能二", label="CREATE_SKILL", labeler="human"),
        d0.make_record("普通", label="NONE", labeler="human"),
    ]
    results = [
        d0.BackendResult(label="CREATE_SKILL", latency_ms=1.0),
        d0.BackendResult(label=None, error="boom", latency_ms=1.0),  # failed on a positive
        d0.BackendResult(label="NONE", latency_ms=1.0),
    ]

    metrics = d0.compute_metrics(records, results)

    assert metrics["confusion"]["CREATE_SKILL"]["UNSCORED"] == 1
    assert metrics["per_label"]["CREATE_SKILL"]["support"] == 2
    assert metrics["positive_recall"] == 0.5  # not 1.0
    assert metrics["unknown_or_failed"] == 1


def test_error_samples_keep_only_the_kind():
    """Reports must not carry sample text, even when a backend echoes it."""

    assert d0.redact_error_sample("RuntimeError: boom") == "RuntimeError: [message withheld]"
    assert d0.redact_error_sample("HTTP 400 Bad Request") == "error: [message withheld]"
    assert d0.redact_error_sample(
        "TypeSafeBadRequestError: upstream rejected 把漏窗的做法存成技能"
    ) == "TypeSafeBadRequestError: [message withheld]"
    assert d0.redact_error_sample("failed for /Users/ren/project/3d.gdl") == (
        "error: [message withheld]"
    )
    # A CJK sentence has no spaces: a "first token" rule would leak all of it.
    assert d0.redact_error_sample("把漏窗的做法存成技能，参数字段复用") == (
        "error: [message withheld]"
    )
    # An identifier-shaped string can be the sample text itself, not an error kind.
    assert d0.redact_error_sample("PrismBlockSecret: leaked") == "error: [message withheld]"
    assert d0.redact_error_sample("HTTP_503: upstream unavailable") == (
        "HTTP_503: [message withheld]"
    )
    assert d0.redact_error_sample("") == ""
    assert len(d0.redact_error_sample("x" * 500)) <= 90


def test_content_duplicates_block_the_verdict_even_with_distinct_ids():
    """Ids may come from another tool; the gate must judge the text itself."""

    records = [
        d0.Record(
            id=f"{index:012d}",
            text="把漏窗的做法存成技能" if index % 2 else "现在有哪些技能？",
            label="CREATE_SKILL" if index % 2 else "LIST_SKILLS",
            labeler="human",
        )
        for index in range(300)
    ]
    stats = d0.dataset_stats(records)

    assert stats["duplicate_ids"] == 0
    assert stats["duplicate_texts"] == 298

    result = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)})
    rows = {row["gate"]: row for row in result["dataset_gates"]}
    assert rows["unique_samples"]["status"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"


def test_dataset_fingerprint_binds_the_verdict_to_one_dataset():
    """A verdict must be quotable only for the exact dataset it was computed on."""

    base = [
        d0.make_record(f"把层板数改成 {index}", label="NONE", labeler="human") for index in range(3)
    ]

    fingerprint = d0.dataset_fingerprint(base)
    assert fingerprint == d0.dataset_fingerprint(list(base))
    assert d0.dataset_stats(base)["fingerprint"] == fingerprint
    # Reordering, relabeling or editing the text yields a different dataset.
    assert d0.dataset_fingerprint(list(reversed(base))) != fingerprint
    relabeled = [base[0], d0.make_record(base[1].text, label="CREATE", labeler="human"), base[2]]
    assert d0.dataset_fingerprint(relabeled) != fingerprint


def test_composition_gates_ignore_fabricated_metadata():
    """Chinese/GDL counts must come from the text, not from stored flags."""

    records = [
        d0.Record(
            id=f"{index:012d}",
            text=f"please refactor the shelf component number {index}",
            label="NONE",
            labeler="human",
            has_cjk=True,          # fabricated
            gdl_terms=["gdl"],     # fabricated
        )
        for index in range(300)
    ]

    stats = d0.dataset_stats(records)

    assert stats["chinese"] == 0
    assert stats["gdl_terms"] == 0
    result = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)})
    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["min_chinese"] == "FAIL"
    assert rows["min_gdl_terms"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"


def test_report_persists_only_redacted_error_samples(tmp_path):
    metrics = {"llm": _metrics(0.8, 0.1)}
    metrics["llm"]["error_samples"] = [
        "TimeoutError: upstream echoed 把漏窗的做法存成技能",
        "failed for /Users/ren/project/3d.gdl",
    ]

    target = d0.write_report(
        tmp_path / "reports",
        stats=_stats(400, 200, 80),
        metrics_by_backend=metrics,
        gate_result=d0.evaluate_gates(_stats(400, 200, 80), metrics),
        unavailable={},
        config={},
    )
    payload = (target / "report.json").read_text(encoding="utf-8")

    assert "把漏窗的做法存成技能" not in payload
    assert "/Users/ren" not in payload
    assert "message withheld" in payload


def test_report_writer_refuses_paths_inside_a_git_worktree(tmp_path):
    worktree = tmp_path / "repo"
    (worktree / ".git").mkdir(parents=True)

    with pytest.raises(SystemExit):
        d0.write_report(
            worktree / "reports",
            stats=_stats(1, 1, 0),
            metrics_by_backend={"llm": _metrics(1.0, 0.0)},
            gate_result=d0.evaluate_gates(_stats(1, 1, 0), {"llm": _metrics(1.0, 0.0)}),
            unavailable={},
            config={},
        )


def test_gates_bind_the_metrics_to_the_evaluated_dataset():
    """Numbers from another dataset must not produce a verdict for this one."""

    stats = _stats(400, 200, 80)
    result = d0.evaluate_gates(
        stats,
        {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05, total=399)},
    )

    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["metrics_match_dataset"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"
    assert any("dataset gates" in reason for reason in result["reasons"])


def test_gates_never_issue_a_verdict_without_a_measured_candidate():
    """No measurement means no verdict: neither GO nor a measured NO-GO."""

    stats = _stats(400, 200, 80)

    nothing_measured = d0.evaluate_gates(stats, {})
    assert nothing_measured["verdict"] == "INSUFFICIENT"
    assert any("no backend was measured" in reason for reason in nothing_measured["reasons"])

    baseline_only = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1)})
    assert baseline_only["verdict"] == "INSUFFICIENT"
    assert any("no comparable backend" in reason for reason in baseline_only["reasons"])


def test_compute_metrics_carries_the_dataset_fingerprint():
    """The production metrics must emit it, not just the test helper."""

    records = [d0.make_record("生成一个方块", label="NONE", labeler="human")]
    metrics = d0.compute_metrics(records, [])

    assert metrics["fingerprint"] == d0.dataset_fingerprint(records)
    assert metrics["fingerprint"] == d0.dataset_stats(records)["fingerprint"]


def test_binding_gate_needs_a_present_fingerprint():
    """Two missing fingerprints must not read as a match (None == None)."""

    stats = _stats(400, 200, 80)
    stats.pop("fingerprint")
    metrics = {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)}
    for entry in metrics.values():
        entry.pop("fingerprint")

    result = d0.evaluate_gates(stats, metrics)

    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["metrics_match_dataset"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"


def test_gates_bind_the_metrics_to_the_dataset_fingerprint():
    """Same row count is not enough: the numbers must describe this dataset."""

    stats = _stats(400, 200, 80)
    foreign = _metrics(0.9, 0.05)
    foreign["fingerprint"] = d0.dataset_fingerprint(
        [d0.make_record("另一个数据集", label="NONE", labeler="human")]
    )

    result = d0.evaluate_gates(stats, {"llm": _metrics(0.8, 0.1), "typesafe": foreign})

    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["metrics_match_dataset"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"


def test_gates_refuse_a_verdict_on_an_unhealthy_baseline():
    """A fallback-inherited baseline is no reference: GO would be vacuous."""

    result = d0.evaluate_gates(
        _stats(400, 200, 80),
        {
            "llm": _metrics(0.8, 0.1, error_rate=1.0, fallback_used=320),
            "typesafe": _metrics(0.9, 0.05),
        },
    )

    assert result["verdict"] == "INSUFFICIENT"
    assert any("baseline is unhealthy" in reason for reason in result["reasons"])


def test_gates_refuse_a_verdict_when_the_baseline_was_overridden():
    result = d0.evaluate_gates(
        _stats(400, 200, 80),
        {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)},
        baseline_override="deepseek-chat",
    )

    rows = {row["gate"]: row["status"] for row in result["dataset_gates"]}
    assert rows["production_baseline"] == "FAIL"
    assert result["verdict"] == "INSUFFICIENT"
    assert any("overridden" in reason for reason in result["reasons"])


def test_no_go_verdicts_state_why():
    """A rejection must name the checks that failed."""

    weak = _metrics(0.5, 0.1)
    result = d0.evaluate_gates(_stats(400, 200, 80), {"llm": _metrics(0.8, 0.1), "typesafe": weak})

    assert result["verdict"] == "NO-GO"
    assert any("typesafe failed" in reason for reason in result["reasons"])


def test_a_fully_labeled_draft_dataset_is_not_human_verified():
    """The labeler clause alone must block a verdict (no unlabeled short-circuit)."""

    drafts = [
        d0.make_record(f"样本 {index}", label="NONE", labeler="assistant-draft")
        for index in range(3)
    ]
    stats = d0.dataset_stats(drafts)

    assert stats["unlabeled"] == 0
    assert stats["human_verified"] is False


def test_a_failing_baseline_probe_blocks_the_verdict():
    result = d0.evaluate_gates(
        _stats(400, 200, 80),
        {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)},
        sanity={
            "llm": {
                "ok": False,
                "misses": [{"id": "x", "expected": "CREATE_SKILL", "observed": "ERROR"}],
            }
        },
    )

    assert result["verdict"] == "INSUFFICIENT"
    assert "sanity probes" in " ".join(result["reasons"])


def test_gates_treat_a_missing_baseline_as_not_passing():
    result = d0.evaluate_gates(_stats(400, 200, 80), {"typesafe": _metrics(0.9, 0.05)})

    assert result["verdict"] == "NO-GO"
    checks = {c["gate"]: c["status"] for c in result["per_backend"]["typesafe"]["checks"]}
    # The comparison gates cannot be satisfied without a reference row, while the
    # backend's own health check still passes.
    assert checks["positive_recall_ratio_vs_llm"] == "FAIL"
    assert checks["false_skill_rate_not_worse_than_llm"] == "FAIL"
    assert checks["backend_answers_for_itself"] == "PASS"


# ── report ───────────────────────────────────────────────────────────────────

def test_report_writes_markdown_and_json_with_the_verdict(tmp_path):
    stats = _stats(400, 200, 80)
    metrics = {"llm": _metrics(0.8, 0.1), "typesafe": _metrics(0.9, 0.05)}
    gates = d0.evaluate_gates(stats, metrics)

    target = d0.write_report(
        tmp_path / "reports",
        sanity={"llm": {"ok": True, "misses": []}},
        stats=stats,
        metrics_by_backend=metrics,
        gate_result=gates,
        unavailable={"laya": "laya not installed"},
        config={"backends": ["llm", "typesafe"]},
    )

    markdown = (target / "report.md").read_text(encoding="utf-8")
    payload = json.loads((target / "report.json").read_text(encoding="utf-8"))

    assert "GO" in markdown
    assert "typesafe" in markdown
    assert "laya not installed" in markdown
    assert payload["gates"]["verdict"] == "GO"
    assert payload["pre_registered"]["gates"] == d0.GATES
    assert payload["dataset"]["total"] == 400
    assert payload["dataset"]["fingerprint"] == stats["fingerprint"]
    assert stats["fingerprint"] in markdown


def test_pre_registered_constants_are_frozen():
    assert d0.GATES == {
        "min_total": 300,
        "min_chinese": 100,
        "min_gdl_terms": 50,
        "positive_recall_ratio_vs_llm": 0.9,
        "false_skill_rate_not_worse_than_llm": True,
    }
    assert set(d0.QUESTION_CRITERIA) == set(d0.LABELS)
    assert d0.LABELS == ("CREATE_SKILL", "LIST_SKILLS", "NONE")
