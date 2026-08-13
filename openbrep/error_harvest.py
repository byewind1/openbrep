"""错误模式收割器（Phase 2b）。

从三类真实运行数据中统计高频错误，半自动生成 error_patterns 候选，
供人工审核后并入 knowledge/gdl_graph.json（override 层）：

1. traces/*.json         — pipeline 运行 trace 的 error 字段（过滤测试污染与 LLM 基建错误）
2. workdir/**/error_lessons.jsonl — 工作区错题本（含 raw_excerpt 真实报错摘录）
3. ~/.openbrep/error_lessons.jsonl — 全局错题本（E1 copilot 自动沉淀，指纹去重落盘）

候选保留 ``sources`` 来源信息（traces / workdir_lessons / global_lessons），
供审核时按来源筛选；输出 knowledge/error_patterns_candidates.json，
按出现次数降序。候选仅供审核，不会被 GDLGraphManager 自动加载。

用法：
    python3 -m openbrep.error_harvest             # 生成候选文件并打印统计
    python3 -m openbrep.error_harvest --check     # 只打印统计，不写文件
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from openbrep.learning import (
    ERROR_LESSONS_FILE,
    _normalize_for_fingerprint,
    classify_error,
    error_fingerprint,
    guidance_for_category,
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


# 逐源聚合中间结构：{错误文本: {"count": 次数, "sources": set[str]}}。
# 相比原 Counter 聚合，额外携带来源信息，供候选 sources 字段使用。
def _aggregate_text(aggregate: dict[str, dict], text: str, count: int, source: str) -> None:
    """向逐源聚合结构累加一条错误文本（同文本 count 累加、sources 并入）。"""
    entry = aggregate.get(text)
    if entry is None:
        aggregate[text] = {"count": count, "sources": {source}}
    else:
        entry["count"] += count
        entry["sources"].add(source)


def _merge_aggregate(target: dict[str, dict], incoming: dict[str, dict]) -> None:
    """把 incoming 逐源聚合并入 target（同文本 count 累加、sources 取并集）。"""
    for text, entry in incoming.items():
        existing = target.get(text)
        if existing is None:
            target[text] = {"count": entry["count"], "sources": set(entry["sources"])}
        else:
            existing["count"] += entry["count"]
            existing["sources"] |= entry["sources"]


def _harvest_traces_aggregate(trace_dir: Path) -> dict[str, dict]:
    """扫描 traces/*.json，返回逐源聚合（噪声过滤，source="traces"）。"""
    aggregate: dict[str, dict] = {}
    if not trace_dir.is_dir():
        return aggregate
    for fp in trace_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for text in (data.get("error"), data.get("compile_error_excerpt")):
            if text and isinstance(text, str) and not _is_noise(text):
                _aggregate_text(aggregate, text.strip(), 1, "traces")
    return aggregate


def _lessons_file_counter(fp: Path, source: str) -> dict[str, dict]:
    """单个错题本 jsonl 的逐源聚合：{摘录文本: {"count", "sources"}}。

    raw_excerpt 为空时落 example；按 lesson 的 count 计权；损坏行/噪声跳过。
    workdir 扫描与全局错题本源共用本函数，避免复制粘贴。
    """
    aggregate: dict[str, dict] = {}
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()
    except Exception:
        return aggregate
    for line in lines:
        if not line.strip():
            continue
        try:
            lesson = json.loads(line)
        except Exception:
            continue
        excerpt = str(lesson.get("raw_excerpt") or lesson.get("example") or "").strip()
        if not excerpt or _is_noise(excerpt):
            continue
        _aggregate_text(aggregate, excerpt, int(lesson.get("count", 1) or 1), source)
    return aggregate


def harvest_traces(trace_dir: Path) -> Counter:
    """扫描 traces/*.json，返回 {规范化错误文本: 次数}（向后兼容的 Counter 视图）。"""
    aggregate = _harvest_traces_aggregate(trace_dir)
    return Counter({text: entry["count"] for text, entry in aggregate.items()})


def harvest_error_lessons(workdir: Path) -> Counter:
    """扫描 workdir 下所有错题本 jsonl，raw_excerpt 按 count 计权（向后兼容）。"""
    aggregate: dict[str, dict] = {}
    if workdir.is_dir():
        for fp in workdir.rglob("error_lessons.jsonl"):
            _merge_aggregate(aggregate, _lessons_file_counter(fp, "workdir_lessons"))
    return Counter({text: entry["count"] for text, entry in aggregate.items()})


def _global_error_lessons_path() -> Path:
    """全局错题本路径（E1 copilot 沉淀写入；不依赖 workbench 服务层）。"""
    return Path.home() / ".openbrep" / ERROR_LESSONS_FILE


def harvest_global_error_lessons() -> Counter:
    """扫描全局错题本 ~/.openbrep/error_lessons.jsonl（E1 copilot 沉淀源）。

    文件不存在 → 空 Counter（不报错）。解析与计权同 harvest_error_lessons。
    """
    aggregate = _lessons_file_counter(_global_error_lessons_path(), "global_lessons")
    return Counter({text: entry["count"] for text, entry in aggregate.items()})


def build_candidates(
    trace_dir: Path = _PROJECT_ROOT / "traces",
    workdir: Path = _PROJECT_ROOT / "workdir",
    min_count: int = 2,
) -> list[dict]:
    """聚合三类数据源，产出按次数降序的候选列表。

    同一错误的不同变体（行号/路径/数字不同）通过 learning 的
    fingerprint 归一化合并；跨源合并时 count 累加、sources 取并集、
    example_excerpt 保留先见者。候选在既有字段基础上新增 ``sources``
    （排序后的来源列表：traces / workdir_lessons / global_lessons），
    其余字段语义与既有输出一致，min_count 过滤逻辑不变。
    """
    aggregate: dict[str, dict] = {}
    _merge_aggregate(aggregate, _harvest_traces_aggregate(trace_dir))
    if workdir.is_dir():
        for fp in workdir.rglob("error_lessons.jsonl"):
            _merge_aggregate(aggregate, _lessons_file_counter(fp, "workdir_lessons"))
    _merge_aggregate(
        aggregate,
        _lessons_file_counter(_global_error_lessons_path(), "global_lessons"),
    )

    by_fingerprint: dict[str, dict] = {}
    for text, entry in aggregate.items():
        category = classify_error(text)
        fp = error_fingerprint(text, category)
        existing = by_fingerprint.get(fp)
        if existing is None:
            existing = {
                "fingerprint": fp,
                "category": category,
                "count": 0,
                "example_excerpt": text[:300],
                "suggested_pattern": _normalize_for_fingerprint(text)[:120],
                "suggested_fix_hint": guidance_for_category(category),
                "review": "pending",
                "sources": [],
            }
            by_fingerprint[fp] = existing
        existing["count"] += entry["count"]
        existing["sources"] = sorted(set(existing["sources"]) | entry["sources"])

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
