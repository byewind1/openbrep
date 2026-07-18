"""错误模式收割器（Phase 2b）合同测试。"""

from __future__ import annotations

import json
from pathlib import Path

from openbrep.error_harvest import (
    _is_noise,
    build_candidates,
    harvest_error_lessons,
    harvest_traces,
)


def _write_trace(trace_dir: Path, name: str, **fields) -> None:
    base = {
        "task_id": name,
        "intent": "CREATE",
        "success": False,
        "error": None,
        "compile_error_excerpt": None,
    }
    base.update(fields)
    (trace_dir / f"{name}.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")


def test_noise_filter() -> None:
    assert _is_noise("expected string or bytes-like object, got 'MagicMock'")
    assert _is_noise("litellm.InternalServerError: something")
    assert _is_noise("LLM 配置错误：请检查 config.toml")
    assert not _is_noise("Error in 3D script, line 12: Missing END")


def test_harvest_traces_filters_noise_and_counts(tmp_path: Path) -> None:
    _write_trace(tmp_path, "t1", error="Error in 3D script, line 12: Missing END")
    _write_trace(tmp_path, "t2", error="Error in 3D script, line 12: Missing END")
    _write_trace(tmp_path, "t3", error="'>' not supported between instances of 'MagicMock' and 'int'")
    _write_trace(tmp_path, "t4", compile_error_excerpt="ENDIF expected at line 5")

    counter = harvest_traces(tmp_path)
    assert counter["Error in 3D script, line 12: Missing END"] == 2
    assert counter["ENDIF expected at line 5"] == 1
    assert len(counter) == 2  # MagicMock 被过滤


def test_harvest_error_lessons_weighted_by_count(tmp_path: Path) -> None:
    lessons_dir = tmp_path / "proj" / ".openbrep" / "memory" / "learnings"
    lessons_dir.mkdir(parents=True)
    lesson = {"raw_excerpt": "Error in 3D script, line 42", "count": 7}
    (lessons_dir / "error_lessons.jsonl").write_text(
        json.dumps(lesson, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    counter = harvest_error_lessons(tmp_path)
    assert counter["Error in 3D script, line 42"] == 7


def test_build_candidates_merges_variants_by_fingerprint(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    # 行号不同的同类错误应合并为一个候选（fingerprint 归一化行号/数字）
    _write_trace(trace_dir, "t1", error="Error in 3D script, line 12: Missing END")
    _write_trace(trace_dir, "t2", error="Error in 3D script, line 99: Missing END")

    candidates = build_candidates(trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2)
    assert len(candidates) == 1
    assert candidates[0]["count"] == 2
    assert candidates[0]["review"] == "pending"
    assert candidates[0]["suggested_fix_hint"]


def test_build_candidates_min_count_threshold(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _write_trace(trace_dir, "t1", error="Some one-off GDL script failure X")
    candidates = build_candidates(trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2)
    assert candidates == []


# ── tracer compile_error_excerpt（收割原料字段）──────────


def test_tracer_compile_error_excerpt() -> None:
    from openbrep.runtime.tracer import _compile_error_excerpt

    class _FakeCompile:
        def __init__(self, success, errors=None, stderr=""):
            self.success = success
            self.errors = errors or []
            self.stderr = stderr

    assert _compile_error_excerpt(None) is None
    assert _compile_error_excerpt(_FakeCompile(True)) is None
    r = _compile_error_excerpt(_FakeCompile(False, errors=["ENDIF expected at line 5"]))
    assert r == "ENDIF expected at line 5"
    r = _compile_error_excerpt(_FakeCompile(False, stderr="boom " * 200))
    assert r is not None and len(r) <= 500
