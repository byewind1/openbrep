"""质量档案趋势报告（observer-only）：分桶计数 + 百分比，无分数评比。

全局趋势由 CLI 扫描档案重建，不作为唯一事实源（设计稿 v2 §4）。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from openbrep.quality.store import load_records


def _bucket_key_model(record: dict) -> str:
    route = (record.get("provenance") or {}).get("model_route") or []
    return "+".join(str(m) for m in route) if route else "unknown"


def build_report(scan_root: Any) -> dict:
    """扫描质量档案并按 outcome / intent / model / commit 分桶。"""
    records = load_records(scan_root)
    buckets = {
        "outcome": Counter(),
        "intent": Counter(),
        "model": Counter(),
        "commit": Counter(),
    }
    for record in records:
        buckets["outcome"][str(record.get("outcome") or "unknown")] += 1
        buckets["intent"][str(record.get("intent") or "unknown")] += 1
        buckets["model"][_bucket_key_model(record)] += 1
        buckets["commit"][str((record.get("provenance") or {}).get("commit") or "unknown")] += 1
    return {
        "total": len(records),
        "buckets": {name: dict(counter) for name, counter in buckets.items()},
    }


def format_report(report: dict, *, scan_root: Any = None) -> str:
    total = report.get("total", 0)
    where = f"（扫描目录：{scan_root}）" if scan_root else ""
    if not total:
        return f"暂无质量档案{where}。执行任务后档案会写入 <项目>/.openbrep/quality/runs/。"

    lines = [f"质量档案趋势{where}：共 {total} 份记录", ""]
    titles = {
        "outcome": "按终态（outcome）",
        "intent": "按意图（intent）",
        "model": "按模型路由（model）",
        "commit": "按代码版本（commit）",
    }
    for name, title in titles.items():
        bucket = report["buckets"].get(name) or {}
        lines.append(f"{title}：")
        for key in sorted(bucket, key=lambda k: (-bucket[k], k)):
            count = bucket[key]
            lines.append(f"  {key}: {count}（{count / total * 100:.1f}%）")
        lines.append("")
    return "\n".join(lines).rstrip()
