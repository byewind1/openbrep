"""错误模式收割器（Phase 2b）。

从两类真实运行数据中统计高频错误，半自动生成 error_patterns 候选，
供人工审核后并入 knowledge/gdl_graph.json（override 层）：

1. traces/*.json         — pipeline 运行 trace 的 error 字段（过滤测试污染与 LLM 基建错误）
2. workdir/**/error_lessons.jsonl — 错题本（含 raw_excerpt 真实报错摘录）

输出 knowledge/error_patterns_candidates.json，按出现次数降序。
候选仅供审核，不会被 GDLGraphManager 自动加载。

用法：
    python3 -m openbrep.error_harvest             # 生成候选文件并打印统计
    python3 -m openbrep.error_harvest --check     # 只打印统计，不写文件
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from openbrep.learning import (
    classify_error,
    error_fingerprint,
    guidance_for_category,
    _normalize_for_fingerprint,
)

_PROJECT_ROOT = Path(__file__).parent.parent
CANDIDATES_PATH = _PROJECT_ROOT / "knowledge" / "error_patterns_candidates.json"

# 测试污染与 LLM 基建错误标记：不是 GDL 编译错误，不进候选
_NOISE_MARKERS = (
    "MagicMock",
    "Mock",
    "<locals>",
    "is not defined",
    "litellm",
    "LLM 配置错误",
    "LLM 认证失败",
    "LLM 请求被拒绝",
    "API Key",
    "provider",
)


def _is_noise(error_text: str) -> bool:
    return any(marker in error_text for marker in _NOISE_MARKERS)


def harvest_traces(trace_dir: Path) -> Counter:
    """扫描 traces/*.json，返回 {规范化错误文本: 次数}。"""
    counter: Counter = Counter()
    if not trace_dir.is_dir():
        return counter
    for fp in trace_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for text in (data.get("error"), data.get("compile_error_excerpt")):
            if text and isinstance(text, str) and not _is_noise(text):
                counter[text.strip()] += 1
    return counter


def harvest_error_lessons(workdir: Path) -> Counter:
    """扫描 workdir 下所有错题本 jsonl，raw_excerpt 按 count 计权。"""
    counter: Counter = Counter()
    if not workdir.is_dir():
        return counter
    for fp in workdir.rglob("error_lessons.jsonl"):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                lesson = json.loads(line)
            except Exception:
                continue
            excerpt = str(lesson.get("raw_excerpt") or lesson.get("example") or "").strip()
            if excerpt and not _is_noise(excerpt):
                counter[excerpt] += int(lesson.get("count", 1) or 1)
    return counter


def build_candidates(
    trace_dir: Path = _PROJECT_ROOT / "traces",
    workdir: Path = _PROJECT_ROOT / "workdir",
    min_count: int = 2,
) -> list[dict]:
    """聚合两类数据源，产出按次数降序的候选列表。

    同一错误的不同变体（行号/路径/数字不同）通过 learning 的
    fingerprint 归一化合并。
    """
    raw_counter = harvest_traces(trace_dir)
    raw_counter.update(harvest_error_lessons(workdir))

    by_fingerprint: dict[str, dict] = {}
    for text, count in raw_counter.items():
        category = classify_error(text)
        fp = error_fingerprint(text, category)
        entry = by_fingerprint.setdefault(fp, {
            "fingerprint": fp,
            "category": category,
            "count": 0,
            "example_excerpt": text[:300],
            "suggested_pattern": _normalize_for_fingerprint(text)[:120],
            "suggested_fix_hint": guidance_for_category(category),
            "review": "pending",
        })
        entry["count"] += count

    candidates = [c for c in by_fingerprint.values() if c["count"] >= min_count]
    candidates.sort(key=lambda c: -c["count"])
    return candidates


def write_candidates(output_path: Path = CANDIDATES_PATH, **kwargs) -> list[dict]:
    candidates = build_candidates(**kwargs)
    payload = {
        "description": "错误模式候选（半自动收割，待人工审核）。审核通过的条目请手工整理进 "
                       "knowledge/gdl_graph.json 的 known_error_patterns，然后从本文件删除。",
        "candidates": candidates,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return candidates


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="错误模式收割器")
    parser.add_argument("--check", action="store_true", help="只打印统计，不写文件")
    parser.add_argument("--min-count", type=int, default=2, help="最低出现次数门槛（默认 2）")
    args = parser.parse_args()

    if args.check:
        candidates = build_candidates(min_count=args.min_count)
    else:
        candidates = write_candidates(min_count=args.min_count)
    print("=== 错误模式收割统计 ===")
    print(f"候选条目：{len(candidates)} 条（min_count={args.min_count}）")
    for c in candidates[:15]:
        print(f"  {c['count']:5d}×  [{c['category']}] {c['example_excerpt'][:60]!r}")
    if not args.check:
        print(f"已写入：{CANDIDATES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
