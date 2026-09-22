#!/usr/bin/env python3
"""D0 offline decision evaluation for the ``skill_intent`` site.

Scope discipline (plan: "模型目录验证先行开发实施任务", section D0):

- **Evaluation only.** Nothing in production imports this module, and
  ``openbrep/decisions.py`` must not be created until a backend passes the
  pre-registered gates below on a *human-verified* dataset.
- **Data stays local.** Candidates, labeled sets and reports are written under
  ``~/.openbrep/decisions/`` (override with ``--data-dir``). The writers refuse
  any path inside a git work tree so user text can never be committed.
- **Labels are the gate.** Records carry ``labeler``; a verdict is only issued
  for fully human-verified datasets. Draft labels yield ``PROVISIONAL`` and can
  never produce ``GO``.
- **Pre-registered thresholds.** ``GATES`` and ``QUESTION`` are frozen before
  the first measurement; changing them requires a documented reason in the
  commit message (same rule as the benchmark baseline).

Site under test: whether a message wants to create a reusable *skill*, list the
existing ones, or neither — the three-way decision that currently costs one LLM
round trip per CHAT message (``SkillCreator.classify_intent``).

Backends: ``rule`` (deterministic prefilter), ``llm`` (the current production
classifier, i.e. the baseline), ``typesafe`` (Choice question over the hosted
System One API), ``laya`` (local model, only if importable).

Metric readings fixed in advance (so results cannot be reinterpreted later):

- ``positive_recall`` — share of skill requests (CREATE_SKILL/LIST_SKILLS) the
  backend finds; the gate is >= 0.9x the LLM baseline.
- ``false_skill_rate`` — share of non-skill messages that wrongly open the skill
  wizard ("NONE 假阳性"); the gate is <= the LLM baseline.
- ``positive_miss_rate`` — the complement of recall, reported for symmetry.

Usage::

    python scripts/decisions_eval.py build-candidates --per-intent 120
    python scripts/decisions_eval.py draft-labels          # assistant draft
    python scripts/decisions_eval.py run --backends rule,llm,typesafe
    python scripts/decisions_eval.py run --backends rule,llm --failure-injection 0.2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

LABELS: tuple[str, ...] = ("CREATE_SKILL", "LIST_SKILLS", "NONE")
POSITIVE_LABELS: tuple[str, ...] = ("CREATE_SKILL", "LIST_SKILLS")
HUMAN_LABELERS: tuple[str, ...] = ("human",)

DEFAULT_DATA_DIR = Path("~/.openbrep/decisions")

# Pre-registered gates. Frozen before the first measurement.
GATES: dict[str, Any] = {
    "min_total": 300,
    "min_chinese": 100,
    "min_gdl_terms": 50,
    "positive_recall_ratio_vs_llm": 0.9,
    "false_skill_rate_not_worse_than_llm": True,
}

# The exact TypeSafe question. Frozen with the gates: changing it invalidates
# recorded measurements, so it is part of the pre-registration.
QUESTION_ID = "skill_intent"
QUESTION_INSTRUCTIONS = (
    "这条用户消息想让 GDL 建模工作台做什么？只选一个选项。"
    "技能（skill）指项目级可复用的规范、模板或做法；生成/修改构件、解释脚本、"
    "排查报错、闲聊都不算。"
)
QUESTION_CRITERIA: dict[str, str] = {
    "CREATE_SKILL": (
        "用户想把一段经验、规范或做法固化成可复用技能，或让 AI 学习项目文档。"
        "例：「把刚才那套漏窗的做法存成技能」「按这份规范做，以后都这样」"
    ),
    "LIST_SKILLS": (
        "用户想查看、列出、选择或管理已有技能。"
        "例：「现在有哪些技能」「列出技能」「删掉那个技能」"
    ),
    "NONE": (
        "其他一切：生成或修改 GDL 构件、解释脚本、排查编译/运行错误、参数微调、"
        "普通问答、打招呼、与技能无关的闲聊"
    ),
}

_MAX_TEXT_CHARS = 400
_SEED_SAMPLES = 12

_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("absolute path", re.compile(r"(?:^|\s)(?:/Users/|/home/|/var/|[A-Za-z]:\\\\)")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+\S")),
    ("api key assignment", re.compile(r"(?i)\bapi[_-]?key\b\s*[:=]")),
    ("provider key", re.compile(r"\b(?:sk|sapi|oc|zk|dk)-[A-Za-z0-9]{8,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}")),
    ("long hex secret", re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE)),
)

# Documented deterministic prefilter — a baseline, not ground truth. Kept
# deliberately simple so its precision/recall are interpretable.
_RULE_CREATE = ("技能", "skill", "模板", "复用", "规范", "错题", "记下来", "沉淀", "做法存")
_RULE_LIST = (
    "列出技能", "有哪些技能", "技能列表", "查看技能", "技能管理", "list skill", "show skill",
)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

_GDL_TERMS: tuple[str, ...] = (
    "gdl", "hsf", "gsm", "paramlist", "script", "3d.gdl", "2d.gdl", "vl.gdl", "ui.gdl",
    "参数", "构件", "脚本", "层板", "直棂", "漏窗", "旋转楼梯", "斗拱", "扶手", "踢面",
    "blocK", "prism", "revolve", "hotspot", "material", "材质", "笔号", "线型",
)


# ── record schema and dataset plumbing ───────────────────────────────────────

@dataclass
class Record:
    """One evaluation sample. ``text`` is the only user-derived content kept."""

    id: str
    text: str
    label: str | None = None
    labeler: str | None = None
    source: str = ""
    intent: str = ""
    has_cjk: bool = False
    gdl_terms: list[str] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Record":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


def sample_id(text: str) -> str:
    """Stable, non-reversible id for a sample text."""

    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def sensitive_reason(text: str) -> str | None:
    """Return why a text must not enter the dataset, or None when it is safe."""

    for name, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            return name
    return None


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def gdl_term_hits(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({term for term in _GDL_TERMS if term.lower() in lowered})


def make_record(
    text: str,
    *,
    label: str | None = None,
    labeler: str | None = None,
    source: str = "",
    intent: str = "",
    notes: str = "",
) -> Record:
    cleaned = " ".join(str(text or "").split())
    return Record(
        id=sample_id(cleaned),
        text=cleaned[:_MAX_TEXT_CHARS],
        label=label,
        labeler=labeler,
        source=source,
        intent=intent,
        has_cjk=has_cjk(cleaned),
        gdl_terms=gdl_term_hits(cleaned),
        notes=notes,
    )


def validate_record(record: Record) -> list[str]:
    """Schema + hygiene errors for one record (never raises)."""

    problems: list[str] = []
    if not record.id or len(record.id) != 12:
        problems.append("id must be a 12-char sample id")
    if not record.text.strip():
        problems.append("text is empty")
    if record.label is None:
        problems.append("label missing")
    elif record.label not in LABELS:
        problems.append(f"label {record.label!r} not in {LABELS}")
    elif not record.labeler:
        problems.append("labeled record needs a labeler")
    reason = sensitive_reason(record.text)
    if reason:
        problems.append(f"text looks sensitive ({reason})")
    if len(record.text) > _MAX_TEXT_CHARS:
        problems.append("text exceeds the recorded cap")
    return problems


def load_dataset(path: str | Path) -> list[Record]:
    records: list[Record] = []
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(Record.from_json(json.loads(line)))
    return records


def write_jsonl(path: str | Path, records: Iterable[Record]) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
    return target


def assert_not_in_git_worktree(path: str | Path) -> None:
    """Refuse to write user text inside a git work tree (privacy guard)."""

    target = Path(path).expanduser().resolve()
    for candidate in (target, *target.parents):
        if (candidate / ".git").exists():
            raise SystemExit(
                f"refusing to write dataset/report inside a git work tree: {target}"
            )


def dataset_stats(records: Sequence[Record]) -> dict[str, Any]:
    labels = {label: 0 for label in LABELS}
    for record in records:
        if record.label in labels:
            labels[record.label] += 1
    labelers = sorted({record.labeler or "unlabeled" for record in records})
    unlabeled = sum(1 for record in records if not record.label)
    return {
        "total": len(records),
        "labels": labels,
        "chinese": sum(1 for record in records if record.has_cjk),
        "gdl_terms": sum(1 for record in records if record.gdl_terms),
        "unlabeled": unlabeled,
        "labelers": labelers,
        # "Verified" means every record carries a label from a human labeler; an
        # empty or partially labeled set must never read as verified.
        "human_verified": bool(records)
        and unlabeled == 0
        and all((record.labeler or "") in HUMAN_LABELERS for record in records),
        "duplicate_ids": len(records) - len({record.id for record in records}),
    }


# ── candidate building ───────────────────────────────────────────────────────

def build_candidates(
    traces_dir: str | Path,
    *,
    per_intent: int = 120,
    seed: int = 20260922,
    include_seed_samples: bool = True,
    stats_out: dict[str, Any] | None = None,
) -> list[Record]:
    """Sample real chat/creation instructions into an unlabeled candidate set.

    Only ``input_summary`` (already capped at 200 chars by the tracer) is read;
    texts that look like paths or credentials are dropped, duplicates collapse
    by content hash, and sampling is deterministic for a given seed. Extraction
    diagnostics land in ``stats_out`` when a dict is passed.
    """

    rng = random.Random(seed)
    buckets: dict[str, dict[str, str]] = {}
    skipped_sensitive = 0
    duplicate_texts = 0
    for path in sorted(Path(traces_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        text = str(data.get("input_summary") or "").strip()
        intent = str(data.get("intent") or "")
        if not text or intent not in {"CHAT", "CREATE", "MODIFY", "DEBUG", "IMAGE", "REPAIR"}:
            continue
        if sensitive_reason(text):
            skipped_sensitive += 1
            continue
        bucket = buckets.setdefault(intent, {})
        identity = sample_id(text)
        if identity in bucket:
            duplicate_texts += 1
        bucket[identity] = text

    records: list[Record] = []
    sampled: dict[str, int] = {}
    for intent, texts in sorted(buckets.items()):
        ids = sorted(texts)
        rng.shuffle(ids)
        selected = ids[:per_intent]
        sampled[intent] = len(selected)
        for sample in selected:
            records.append(make_record(texts[sample], source=f"trace:{intent}", intent=intent))

    if include_seed_samples:
        for text, source, notes in _SEED_SAMPLES:
            records.append(make_record(text, source=source, notes=notes))
        sampled["seed"] = len(_SEED_SAMPLES)

    unique: dict[str, Record] = {}
    for record in records:
        unique.setdefault(record.id, record)
    result = sorted(unique.values(), key=lambda record: (record.source, record.id))
    if stats_out is not None:
        stats_out.update(
            {
                "traces_dir": str(traces_dir),
                "sampled_per_intent": sampled,
                "skipped_sensitive": skipped_sensitive,
                "duplicate_texts": duplicate_texts,
                "available_per_intent": {k: len(v) for k, v in sorted(buckets.items())},
                "deduplicated": len(records) - len(result),
            }
        )
    return result


# Curated boundary samples (written for evaluation, not taken from user data).
_SEED_SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("把刚才那套漏窗的做法存成一个技能，以后同项目复用", "seed", "clear create"),
    ("这段处理直棂间距的写法很好，帮我固化成规范", "seed", "create via 规范"),
    ("以后所有脚本都按这份文档的命名规则来，先把它记下来", "seed", "implicit create"),
    ("把这些踩过的坑整理成错题本，之后生成时参考", "seed", "create via 错题"),
    ("现在有哪些技能？", "seed", "clear list"),
    ("列出当前项目的技能列表", "seed", "clear list"),
    ("把之前的技能列出来看看", "seed", "list with typo"),
    ("生成一个旋转楼梯，层高 3000，18 级踏步", "seed", "create object (none)"),
    ("把 shelf_count 改成 5", "seed", "param modify (none)"),
    ("这个脚本为什么编译不过？错误是缺少 ENDIF", "seed", "debug (none)"),
    ("解释一下 3d.gdl 里 BLOCK 后面的三个参数是什么意思", "seed", "explain (none)"),
    ("你好，你能做什么？", "seed", "chat (none)"),
)

_DRAFT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LIST_SKILLS", ("列出技能", "有哪些技能", "技能列表", "查看技能", "技能管理", "list skill")),
    (
        "CREATE_SKILL",
        (
            "存成技能", "存成一个技能", "固化成", "记下来", "做成技能", "错题本",
            "以后都这样", "复用", "规范", "沉淀", "learn this", "save as skill",
        ),
    ),
)

def draft_labels(records: Sequence[Record], *, labeler: str = "assistant-draft") -> list[Record]:
    """Assistant pre-labeling. Never sufficient for a verdict on its own."""

    labeled: list[Record] = []
    for record in records:
        label = "NONE"
        lowered = record.text.lower()
        for candidate, needles in _DRAFT_RULES:
            if any(needle in lowered for needle in needles):
                label = candidate
                break
        labeled.append(
            Record(
                **{
                    **record.to_json(),
                    "label": label,
                    "labeler": labeler,
                    "notes": "draft labels: require human verification before any verdict",
                }
            )
        )
    return labeled


# ── backends ─────────────────────────────────────────────────────────────────

@dataclass
class BackendResult:
    label: str | None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    latency_ms: float = 0.0
    error: str = ""
    fallback_used: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None


class Backend:
    """A decision backend: one text in, one label (+ optional certainty) out."""

    name = "backend"

    def classify(self, text: str) -> BackendResult:  # pragma: no cover - interface
        raise NotImplementedError


class RuleBackend(Backend):
    name = "rule"

    def classify(self, text: str) -> BackendResult:
        lowered = text.lower()
        for label, needles in (("LIST_SKILLS", _RULE_LIST), ("CREATE_SKILL", _RULE_CREATE)):
            if any(needle in lowered for needle in needles):
                return BackendResult(label=label, confidence=1.0, probabilities={label: 1.0})
        return BackendResult(label="NONE", confidence=1.0, probabilities={"NONE": 1.0})


class LLMBackend(Backend):
    """Wraps the current production classifier (``SkillCreator.classify_intent``).

    That method answers with a bare label and swallows exceptions into ``NONE``,
    so it has no calibrated probability: ``confidence`` stays ``None`` and the
    backend is excluded from calibration metrics.
    """

    name = "llm"

    def __init__(
        self, classify: Callable[[str], str] | None = None, model: str | None = None
    ) -> None:
        self._classify = classify
        self._model = model
        self._creator = None

    def _ensure(self) -> Callable[[str], str]:
        if self._classify is None:
            if self._creator is None:
                from openbrep.config import GDLAgentConfig
                from openbrep.llm import LLMAdapter
                from openbrep.skill_creator import SkillCreator

                config = GDLAgentConfig.load()
                llm_config = config.llm
                if self._model:
                    # Overriding the baseline model changes what "current LLM"
                    # means: valid for plumbing dry-runs, wrong for a verdict.
                    # The definitive D0 run must use the production default.
                    llm_config = replace(llm_config, model=self._model)
                # Same skills directory as the pipeline resolves for production.
                project_root = Path(__file__).resolve().parent.parent
                self._creator = SkillCreator(
                    LLMAdapter(llm_config),
                    skills_dir=str(project_root / "skills"),
                )
            self._classify = self._creator.classify_intent
        return self._classify

    def classify(self, text: str) -> BackendResult:
        started = time.perf_counter()
        try:
            label = str(self._ensure()(text) or "NONE").strip().upper()
        except Exception as exc:  # noqa: BLE001 — evaluation must not abort on a backend error
            return BackendResult(
                label=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )
        if label not in LABELS:
            label = "NONE"
        return BackendResult(label=label, latency_ms=(time.perf_counter() - started) * 1000)


class TypeSafeBackend(Backend):
    """Hosted System One Choice question (state = the message only)."""

    name = "typesafe"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._owns_client = client is None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from typesafe_sdk import TypeSafeClient

            if not os.environ.get("TYPESAFE_API_KEY"):
                raise RuntimeError("TYPESAFE_API_KEY is not set")
            self._client = TypeSafeClient()
        return self._client

    @staticmethod
    def build_question() -> Any:
        from typesafe_sdk import Choice

        return Choice(instructions=QUESTION_INSTRUCTIONS, criteria=dict(QUESTION_CRITERIA))

    def classify(self, text: str) -> BackendResult:
        started = time.perf_counter()
        try:
            client = self._ensure_client()
            response = client.system_one(
                state={"message": text},
                questions={QUESTION_ID: self.build_question()},
                **({"model": self._model} if self._model else {}),
            )
            answer = response.answers[QUESTION_ID]
            label = str(answer.choice).strip().upper()
            probabilities = {
                str(key).upper(): float(value)
                for key, value in dict(answer.probabilities).items()
            }
            usage = getattr(response, "usage", None)
        except Exception as exc:  # noqa: BLE001 — evaluation must not abort on a backend error
            return BackendResult(
                label=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )
        if label not in LABELS:
            label = "NONE"
        return BackendResult(
            label=label,
            confidence=float(answer.confidence),
            probabilities=probabilities,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


class LayaBackend(Backend):
    """Local decision model; reports unavailable when the optional extra is absent."""

    name = "laya"

    def __init__(self) -> None:
        try:
            import laya  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"laya not installed ({type(exc).__name__})") from exc

    def classify(self, text: str) -> BackendResult:  # pragma: no cover - extra not installed
        return BackendResult(
            label=None,
            error=(
                "laya backend wiring is intentionally deferred until the extra "
                "is a real dependency"
            ),
        )


def build_backends(
    names: Sequence[str],
    *,
    classify: Callable[[str], str] | None = None,
    typesafe_client: Any | None = None,
    baseline_model: str | None = None,
) -> tuple[list[Backend], dict[str, str]]:
    """Instantiate the requested backends; unavailable ones come back as reasons."""

    backends: list[Backend] = []
    unavailable: dict[str, str] = {}
    for name in names:
        if name == "rule":
            backends.append(RuleBackend())
        elif name == "llm":
            backends.append(LLMBackend(classify=classify, model=baseline_model))
        elif name == "typesafe":
            backends.append(TypeSafeBackend(client=typesafe_client))
        elif name == "laya":
            try:
                backends.append(LayaBackend())
            except RuntimeError as exc:
                unavailable["laya"] = str(exc)
        else:
            raise SystemExit(f"unknown backend: {name}")
    return backends, unavailable


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate_backend(
    backend: Backend,
    records: Sequence[Record],
    *,
    fallback: Backend | None = None,
    failure_rate: float = 0.0,
    rng: random.Random | None = None,
) -> list[BackendResult]:
    """Run one backend over labeled records, resolving errors through a fallback."""

    generator = rng or random.Random(20260922)
    results: list[BackendResult] = []
    for record in records:
        if failure_rate > 0 and generator.random() < failure_rate:
            result = BackendResult(label=None, error="injected failure")
        else:
            result = backend.classify(record.text)
        if result.label is None and fallback is not None:
            fallback_result = fallback.classify(record.text)
            result = BackendResult(
                label=fallback_result.label,
                latency_ms=result.latency_ms + fallback_result.latency_ms,
                error=result.error,
                fallback_used=True,
            )
        results.append(result)
    return results


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def expected_calibration_error(
    pairs: Sequence[tuple[float, bool]], bins: int = 5
) -> float | None:
    """ECE over (confidence, correct) pairs; None when nothing is calibrated."""

    if not pairs:
        return None
    total = len(pairs)
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        bucket = [
            (confidence, correct)
            for confidence, correct in pairs
            if low <= confidence < high or (index == bins - 1 and confidence == 1.0)
        ]
        if not bucket:
            continue
        mean_confidence = sum(confidence for confidence, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        error += (len(bucket) / total) * abs(mean_confidence - accuracy)
    return error


def compute_metrics(records: Sequence[Record], results: Sequence[BackendResult]) -> dict[str, Any]:
    """Confusion matrix, per-class precision/recall, wizard rates, latency, ECE."""

    confusion = {gold: {pred: 0 for pred in LABELS} for gold in LABELS}
    errors = 0
    latencies: list[float] = []
    calibration: list[tuple[float, bool]] = []
    fallbacks = 0
    unknown = 0
    error_samples: list[str] = []
    input_tokens = 0
    output_tokens = 0
    for record, result in zip(records, results):
        if result.error:
            errors += 1
            if len(error_samples) < 3 and result.error not in error_samples:
                error_samples.append(result.error[:160])
        if result.label is None:
            unknown += 1
            continue
        if result.fallback_used:
            fallbacks += 1
        latencies.append(result.latency_ms)
        input_tokens += result.input_tokens or 0
        output_tokens += result.output_tokens or 0
        if record.label in LABELS:
            confusion[record.label][result.label] += 1
        if result.confidence is not None:
            calibration.append((result.confidence, result.label == record.label))

    def _safe_div(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    per_label: dict[str, Any] = {}
    for label in LABELS:
        true_positive = confusion[label][label]
        predicted = sum(confusion[gold][label] for gold in LABELS)
        actual = sum(confusion[label].values())
        per_label[label] = {
            "support": actual,
            "precision": _safe_div(true_positive, predicted),
            "recall": _safe_div(true_positive, actual),
            "f1": _safe_div(
                2 * true_positive,
                2 * true_positive + (predicted - true_positive) + (actual - true_positive),
            ),
        }

    positives = sum(per_label[label]["support"] for label in POSITIVE_LABELS)
    positive_hits = sum(confusion[label][label] for label in POSITIVE_LABELS)
    negatives = per_label["NONE"]["support"]
    false_skills = sum(confusion["NONE"][label] for label in POSITIVE_LABELS)
    total = len(records)
    return {
        "confusion": confusion,
        "per_label": per_label,
        "total": total,
        "scored": sum(1 for result in results if result.label is not None),
        "unknown_or_failed": unknown,
        "error_rate": round(errors / total, 4) if total else None,
        "error_samples": error_samples,
        "fallback_used": fallbacks,
        "positive_recall": _safe_div(positive_hits, positives),
        "positive_miss_rate": _safe_div(positives - positive_hits, positives),
        "false_skill_rate": _safe_div(false_skills, negatives),
        "latency_p50_ms": round(percentile(latencies, 0.5), 1),
        "latency_p95_ms": round(percentile(latencies, 0.95), 1),
        "calibration_ece": (
            round(ece, 4)
            if (ece := expected_calibration_error(calibration)) is not None
            else None
        ),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


SANITY_PROBES: tuple[tuple[str, str], ...] = (
    ("把刚才那套漏窗的做法存成一个技能，以后复用", "CREATE_SKILL"),
    ("现在有哪些技能？", "LIST_SKILLS"),
    ("生成一个旋转楼梯，层高 3000", "NONE"),
)


def probe_backend(backend: Backend) -> dict[str, Any]:
    """Sanity-check a backend on unambiguous samples before trusting its metrics.

    ``SkillCreator.classify_intent`` swallows failures into ``NONE``, so a
    backend with broken credentials would otherwise look like a weak classifier
    rather than an unconfigured one. Any miss here makes the run INSUFFICIENT.
    """

    misses: list[dict[str, str]] = []
    for text, expected in SANITY_PROBES:
        result = backend.classify(text)
        if result.label is None:
            misses.append({"text_id": sample_id(text), "expected": expected, "observed": "ERROR"})
        elif result.label != expected:
            misses.append(
                {"text_id": sample_id(text), "expected": expected, "observed": result.label}
            )
    return {"ok": not misses, "misses": misses}


def evaluate_gates(
    stats: dict[str, Any],
    metrics_by_backend: dict[str, dict[str, Any]],
    *,
    sanity: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply the pre-registered gates and return a verdict.

    ``INSUFFICIENT`` blocks ``GO`` whenever the dataset misses the pre-registered
    minimums or the labels are not human-verified; ``NO-GO`` means the dataset was
    adequate and no backend cleared every gate.
    """

    rows: list[dict[str, Any]] = [
        {
            "gate": "min_total",
            "requirement": f">= {GATES['min_total']}",
            "observed": stats["total"],
            "status": "PASS" if stats["total"] >= GATES["min_total"] else "FAIL",
        },
        {
            "gate": "min_chinese",
            "requirement": f">= {GATES['min_chinese']}",
            "observed": stats["chinese"],
            "status": "PASS" if stats["chinese"] >= GATES["min_chinese"] else "FAIL",
        },
        {
            "gate": "min_gdl_terms",
            "requirement": f">= {GATES['min_gdl_terms']}",
            "observed": stats["gdl_terms"],
            "status": "PASS" if stats["gdl_terms"] >= GATES["min_gdl_terms"] else "FAIL",
        },
        {
            "gate": "all_labeled",
            "requirement": "0 unlabeled",
            "observed": stats["unlabeled"],
            "status": "PASS" if stats["unlabeled"] == 0 else "FAIL",
        },
    ]
    baseline = metrics_by_backend.get("llm")
    per_backend: dict[str, dict[str, Any]] = {}
    for name, metrics in metrics_by_backend.items():
        checks: list[dict[str, Any]] = []
        if name == "llm":
            checks.append(
                {
                    "gate": "baseline",
                    "requirement": "reference row",
                    "observed": {
                        "positive_recall": metrics["positive_recall"],
                        "false_skill_rate": metrics["false_skill_rate"],
                    },
                    "status": "REFERENCE",
                }
            )
        else:
            recall_ratio = None
            if baseline and metrics["positive_recall"] is not None and baseline["positive_recall"]:
                recall_ratio = metrics["positive_recall"] / baseline["positive_recall"]
            checks.append(
                {
                    "gate": "positive_recall_ratio_vs_llm",
                    "requirement": f">= {GATES['positive_recall_ratio_vs_llm']}",
                    "observed": round(recall_ratio, 4) if recall_ratio is not None else None,
                    "status": (
                        "PASS"
                        if recall_ratio is not None
                        and recall_ratio >= GATES["positive_recall_ratio_vs_llm"]
                        else "FAIL"
                    ),
                }
            )
            backend_fsr = metrics["false_skill_rate"]
            baseline_fsr = baseline["false_skill_rate"] if baseline else None
            if backend_fsr is not None and baseline_fsr is not None:
                ok = metrics["false_skill_rate"] <= baseline["false_skill_rate"]
            else:
                ok = False
            checks.append(
                {
                    "gate": "false_skill_rate_not_worse_than_llm",
                    "requirement": "<= llm baseline",
                    "observed": {
                        "backend": metrics["false_skill_rate"],
                        "llm": baseline["false_skill_rate"] if baseline else None,
                    },
                    "status": "PASS" if ok else "FAIL",
                }
            )
        per_backend[name] = {
            "checks": checks,
            "passed": all(check["status"] in {"PASS", "REFERENCE"} for check in checks),
        }

    sanity = sanity or {}
    baseline_sanity = sanity.get("llm")
    rows.append(
        {
            "gate": "baseline_sanity",
            "requirement": "llm baseline classifies the unambiguous probes",
            "observed": "not probed" if baseline_sanity is None else baseline_sanity["misses"],
            "status": (
                "NOT_PROBED"
                if baseline_sanity is None
                else ("PASS" if baseline_sanity["ok"] else "FAIL")
            ),
        }
    )
    dataset_ok = all(row["status"] in {"PASS", "NOT_PROBED"} for row in rows)
    labels_ok = stats["human_verified"] and stats["unlabeled"] == 0
    # Only a non-baseline backend can carry a GO: the "llm" row is the reference
    # the others are compared against.
    candidates = {name: entry for name, entry in per_backend.items() if name != "llm"}
    if not dataset_ok or not labels_ok:
        verdict = "INSUFFICIENT"
    elif any(entry["passed"] for entry in candidates.values()):
        verdict = "GO"
    else:
        verdict = "NO-GO"
    reasons: list[str] = []
    if not dataset_ok:
        reasons.append("dataset gates not satisfied")
    if baseline_sanity is not None and not baseline_sanity["ok"]:
        reasons.append(
            "llm baseline failed the sanity probes (likely broken credentials); "
            "metrics are not comparable"
        )
    if not stats["human_verified"]:
        reasons.append(f"labels not human-verified (labelers: {stats['labelers']})")
    if dataset_ok and labels_ok and not candidates:
        reasons.append("no comparable backend requested (only the llm baseline)")
    return {
        "dataset_gates": rows,
        "sanity": sanity,
        "per_backend": per_backend,
        "dataset_ok": dataset_ok,
        "labels_human_verified": stats["human_verified"],
        "verdict": verdict,
        "reasons": reasons,
    }


# ── reporting ────────────────────────────────────────────────────────────────

def _metric_table(metrics_by_backend: dict[str, dict[str, Any]]) -> str:
    header = (
        "| backend | positive_recall | false_skill_rate | positive_miss_rate "
        "| errors | p50 ms | p95 ms | ECE |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for name, metrics in sorted(metrics_by_backend.items()):
        rows.append(
            "| {name} | {recall} | {fsr} | {miss} | {errors} | {p50} | {p95} | {ece} |".format(
                name=name,
                recall=metrics["positive_recall"],
                fsr=metrics["false_skill_rate"],
                miss=metrics["positive_miss_rate"],
                errors=metrics["error_rate"],
                p50=metrics["latency_p50_ms"],
                p95=metrics["latency_p95_ms"],
                ece=metrics["calibration_ece"],
            )
        )
    return header + "\n".join(rows)


def write_report(
    out_dir: str | Path,
    *,
    sanity: dict[str, dict[str, Any]] | None = None,
    stats: dict[str, Any],
    metrics_by_backend: dict[str, dict[str, Any]],
    gate_result: dict[str, Any],
    unavailable: dict[str, str],
    config: dict[str, Any],
) -> Path:
    target_dir = Path(out_dir).expanduser() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": stats,
        "metrics": metrics_by_backend,
        "gates": gate_result,
        "unavailable_backends": unavailable,
        "sanity": sanity,
        "config": config,
        "pre_registered": {"gates": GATES, "question": QUESTION_CRITERIA},
    }
    (target_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# D0 skill_intent 离线评测报告",
        "",
        f"生成时间：{payload['generated_at']}",
        f"结论：**{gate_result['verdict']}**",
        "",
        "## 数据集",
        "",
        f"- 总数：{stats['total']}（中文 {stats['chinese']} / GDL 术语 {stats['gdl_terms']}）",
        f"- 标签分布：{stats['labels']}",
        f"- 未标注：{stats['unlabeled']}；labelers：{stats['labelers']}",
        f"- 人工核验：{stats['human_verified']}",
        "",
        "## 指标",
        "",
        _metric_table(metrics_by_backend),
        "",
        "## 门槛",
        "",
        "| gate | requirement | observed | status |",
        "|---|---|---|---|",
    ]
    for row in gate_result["dataset_gates"]:
        lines.append(
            f"| {row['gate']} | {row['requirement']} | {row['observed']} | {row['status']} |"
        )
    for name, entry in sorted(gate_result["per_backend"].items()):
        for check in entry["checks"]:
            lines.append(
                f"| {name}:{check['gate']} | {check['requirement']} "
                f"| {check['observed']} | {check['status']} |"
            )
    if gate_result["reasons"]:
        lines += ["", "未达门槛原因：", *[f"- {reason}" for reason in gate_result["reasons"]]]
    if unavailable:
        lines += ["", "## 不可用后端", "", *[f"- {k}: {v}" for k, v in sorted(unavailable.items())]]
    lines += [
        "",
        "## 说明",
        "",
        "- 本报告只包含脱敏样本 ID、标签、预测、概率与延迟；",
        "  原始文本与本报告都只落在 `~/.openbrep/decisions/`。",
        "- 只有在**人工核验**标签的数据集上，`GO` 才成立；草稿标签的运行一律 `INSUFFICIENT`。",
        "- 成本按 `input_tokens`/`output_tokens` 记录；单价请以 TypeSafe 控制台为准。",
        "",
    ]
    (target_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return target_dir


# ── CLI ──────────────────────────────────────────────────────────────────────

def _labeled_records(records: Sequence[Record]) -> list[Record]:
    return [record for record in records if record.label]


def cmd_build_candidates(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser()
    out = Path(args.out).expanduser() if args.out else data_dir / "skill_intent.candidates.jsonl"
    assert_not_in_git_worktree(out)
    diagnostics: dict[str, Any] = {}
    records = build_candidates(
        args.traces_dir,
        per_intent=args.per_intent,
        seed=args.seed,
        include_seed_samples=not args.no_seed_samples,
        stats_out=diagnostics,
    )
    write_jsonl(out, records)
    print(f"candidates: {len(records)} -> {out}")
    print(json.dumps({**dataset_stats(records), **diagnostics}, ensure_ascii=False))
    return 0


def cmd_draft_labels(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser()
    source = (
        Path(args.dataset).expanduser()
        if args.dataset
        else data_dir / "skill_intent.candidates.jsonl"
    )
    out = Path(args.out).expanduser() if args.out else data_dir / "skill_intent.draft.jsonl"
    assert_not_in_git_worktree(out)
    records = draft_labels(load_dataset(source))
    write_jsonl(out, records)
    print(f"draft labels: {len(records)} -> {out} (labeler=assistant-draft, not verdict-grade)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser()
    dataset = (
        Path(args.dataset).expanduser()
        if args.dataset
        else data_dir / "skill_intent.draft.jsonl"
    )
    records = load_dataset(dataset)
    problems = [(record.id, issue) for record in records for issue in validate_record(record)]
    if problems:
        print("dataset validation failed:", file=sys.stderr)
        for sample, issue in problems[:20]:
            print(f"  {sample}: {issue}", file=sys.stderr)
        return 2
    stats = dataset_stats(records)
    backends, unavailable = build_backends(
        args.backends.split(","), baseline_model=args.baseline_model or None
    )
    fallback = next((b for b in backends if b.name == "llm"), None)
    metrics_by_backend: dict[str, dict[str, Any]] = {}
    print(f"running {len(records)} samples on {[b.name for b in backends]} ...")
    for backend in backends:
        results = evaluate_backend(
            backend,
            records,
            fallback=fallback if backend.name != "llm" else None,
            failure_rate=args.failure_injection,
        )
        metrics_by_backend[backend.name] = compute_metrics(records, results)
        print(
            f"  {backend.name}: recall={metrics_by_backend[backend.name]['positive_recall']} "
            f"false_skill_rate={metrics_by_backend[backend.name]['false_skill_rate']} "
            f"p95={metrics_by_backend[backend.name]['latency_p95_ms']}ms"
        )
    sanity: dict[str, dict[str, Any]] = {}
    if any(backend.name == "llm" for backend in backends):
        baseline_backend = next(backend for backend in backends if backend.name == "llm")
        sanity["llm"] = probe_backend(baseline_backend)
        print(f"  llm sanity: {'ok' if sanity['llm']['ok'] else sanity['llm']['misses']}")
    gate_result = evaluate_gates(stats, metrics_by_backend, sanity=sanity)
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else data_dir / "reports"
    target = write_report(
        out_dir,
        stats=stats,
        metrics_by_backend=metrics_by_backend,
        gate_result=gate_result,
        unavailable=unavailable,
        config={
            "dataset": str(dataset),
            "backends": [b.name for b in backends],
            "failure_injection": args.failure_injection,
            "gates": GATES,
        },
    )
    print(f"verdict: {gate_result['verdict']} -> {target}")
    for reason in gate_result["reasons"]:
        print(f"  blocked: {reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="D0 offline decision evaluation (skill_intent)")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-candidates", help="sample local traces into an unlabeled set")
    build.add_argument("--traces-dir", default="./traces")
    build.add_argument("--per-intent", type=int, default=120)
    build.add_argument("--seed", type=int, default=20260922)
    build.add_argument("--no-seed-samples", action="store_true")
    build.add_argument("--out")
    build.set_defaults(func=cmd_build_candidates)

    draft = sub.add_parser("draft-labels", help="assistant pre-labeling (not verdict-grade)")
    draft.add_argument("--dataset")
    draft.add_argument("--out")
    draft.set_defaults(func=cmd_draft_labels)

    run = sub.add_parser("run", help="evaluate backends and apply the pre-registered gates")
    run.add_argument("--dataset")
    run.add_argument("--backends", default="rule,llm,typesafe")
    run.add_argument("--failure-injection", type=float, default=0.0)
    run.add_argument(
        "--baseline-model",
        default="",
        help=(
            "override the llm baseline model (dry-runs only; "
            "a verdict needs the production default)"
        ),
    )
    run.add_argument("--out-dir")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
