"""错误模式收割器（Phase 2b）合同测试。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from openbrep.error_harvest import (
    _is_noise,
    build_candidates,
    harvest_error_lessons,
    harvest_global_error_lessons,
    harvest_traces,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个用例默认隔离 HOME：~/.openbrep/error_lessons.jsonl 指向 tmp 空目录。

    E2 起 build_candidates 会读取全局错题本；开发者本机若存在真实
    ~/.openbrep/error_lessons.jsonl（E1 真机沉淀），未隔离的用例会把真实条目
    混进候选、结果依赖机器状态。需要全局源的用例显式往 fake_home 写文件。
    """
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_global_lessons(fake_home: Path, lines: list[str]) -> Path:
    """向 fake_home 写入全局错题本，返回文件路径。"""
    global_dir = fake_home / ".openbrep"
    global_dir.mkdir(parents=True, exist_ok=True)
    fp = global_dir / "error_lessons.jsonl"
    fp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return fp


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


# ── E2：copilot 全局错题本源接入 ──────────────────────────────────


def test_harvest_global_error_lessons_reads_home_file(fake_home: Path) -> None:
    _write_global_lessons(fake_home, [
        json.dumps({"raw_excerpt": "Error in 3D script, line 42", "count": 7}),
    ])
    counter = harvest_global_error_lessons()
    assert counter == Counter({"Error in 3D script, line 42": 7})


def test_harvest_global_error_lessons_missing_file_returns_empty(fake_home: Path) -> None:
    assert harvest_global_error_lessons() == Counter()


def test_global_error_lessons_enter_candidates(fake_home: Path, tmp_path: Path) -> None:
    _write_global_lessons(fake_home, [
        json.dumps({"raw_excerpt": "Error in 3D script, line 12: Missing END", "count": 5}),
    ])
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    candidates = build_candidates(
        trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["sources"] == ["global_lessons"]
    assert candidate["count"] == 5
    assert candidate["review"] == "pending"
    assert candidate["suggested_fix_hint"]


def test_build_candidates_merges_traces_and_global_lessons_by_fingerprint(
    fake_home: Path, tmp_path: Path
) -> None:
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _write_trace(trace_dir, "t1", error="Error in 3D script, line 12: Missing END")
    _write_trace(trace_dir, "t2", error="Error in 3D script, line 99: Missing END")
    _write_global_lessons(fake_home, [
        json.dumps({"raw_excerpt": "Error in 3D script, line 42: Missing END", "count": 3}),
    ])

    candidates = build_candidates(
        trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    # 跨源同指纹合并：count 累加（traces 2 + global 3）、sources 并集（排序）
    assert candidate["count"] == 5
    assert candidate["sources"] == ["global_lessons", "traces"]
    # example_excerpt 保留先见者（traces 先聚合）
    assert candidate["example_excerpt"] == "Error in 3D script, line 12: Missing END"


def test_build_candidates_merges_workdir_and_global_lessons_sources(
    fake_home: Path, tmp_path: Path
) -> None:
    # workdir 收窄到 proj/：fake_home 位于 tmp_path 之下，workdir 若用 tmp_path
    # 会把全局错题本也 rglob 进来造成重复计权
    lessons_dir = tmp_path / "proj" / ".openbrep" / "memory" / "learnings"
    lessons_dir.mkdir(parents=True)
    (lessons_dir / "error_lessons.jsonl").write_text(
        json.dumps({"raw_excerpt": "Error in 3D script, line 12: Missing END", "count": 2})
        + "\n",
        encoding="utf-8",
    )
    _write_global_lessons(fake_home, [
        json.dumps({"raw_excerpt": "Error in 3D script, line 42: Missing END", "count": 4}),
    ])
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    candidates = build_candidates(
        trace_dir=trace_dir, workdir=tmp_path / "proj", min_count=2
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["count"] == 6
    assert candidate["sources"] == ["global_lessons", "workdir_lessons"]


def test_global_lessons_file_missing_does_not_affect_other_sources(
    fake_home: Path, tmp_path: Path
) -> None:
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _write_trace(trace_dir, "t1", error="Error in 3D script, line 12: Missing END")
    _write_trace(trace_dir, "t2", error="Error in 3D script, line 99: Missing END")
    # fake_home 下无 .openbrep/error_lessons.jsonl → 全局源静默为空
    candidates = build_candidates(
        trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2
    )
    assert len(candidates) == 1
    assert candidates[0]["count"] == 2
    assert candidates[0]["sources"] == ["traces"]


def test_global_error_lessons_skips_corrupt_lines(fake_home: Path, tmp_path: Path) -> None:
    _write_global_lessons(fake_home, [
        json.dumps({"raw_excerpt": "Error in 3D script, line 12: Missing END", "count": 2}),
        "this-is-not-json{",
        json.dumps({"raw_excerpt": "Error in 3D script, line 42: Missing END", "count": 3}),
    ])
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    candidates = build_candidates(
        trace_dir=trace_dir, workdir=tmp_path / "nonexistent", min_count=2
    )
    assert len(candidates) == 1
    assert candidates[0]["count"] == 5
    assert candidates[0]["sources"] == ["global_lessons"]


def test_workdir_lessons_sources_tagged(fake_home: Path, tmp_path: Path) -> None:
    lessons_dir = tmp_path / "proj" / ".openbrep" / "memory" / "learnings"
    lessons_dir.mkdir(parents=True)
    (lessons_dir / "error_lessons.jsonl").write_text(
        json.dumps({"raw_excerpt": "Error in 3D script, line 12: Missing END", "count": 4})
        + "\n",
        encoding="utf-8",
    )
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    candidates = build_candidates(trace_dir=trace_dir, workdir=tmp_path, min_count=2)
    assert len(candidates) == 1
    assert candidates[0]["sources"] == ["workdir_lessons"]
    assert candidates[0]["count"] == 4
