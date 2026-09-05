"""Reflector：从质量账本确定性选出「值得反思」的任务 run（G4，observer-only）。

设计稿 G4：蒸馏输入从「全量 feedback 事件聚类」升级为「带证据的失败 + 同类
高低分对照」。本模块只做**确定性选择**——不调 LLM、不做任何评分（score_profile_v1
未定，本单不发明评分），低分口径用版本化常量 REFLECTOR_V1 声明。

候选规则（v1 口径，AC-G4-1）：
- outcome ∈ {gate_fail, budget_exhausted, timeout}（失败三态，见 schema OUTCOMES）；
- 或 artifact_quality 任一 **measured** 轴存在 issues（cross_script.issues /
  parametricity.sweep_issues 为 v1 认可的 issue 载体，见 AXIS_ISSUE_FIELDS_V1）；
- ``unavailable`` / ``not_applicable`` 轴一律不算 issues（n/a 纪律：不可观测
  不是失败证据）；
- completed 且零 issues → 不进候选，但可作对照（contrast）。

对照配对（v1）：同 intent（归一大写比较）最近的 completed 且零 measured-issues
记录；有同 project path_hash 的记录时只在该子集里取最近；无对照 → null，
候选仍成立。

watermark：按「已处理 run_id 集合」增量。select 的返回值 new_watermark 在调用方
（feedback_distill.distill_quality_records）蒸馏成功后才持久化，路径
``<work_dir>/.openbrep/memory/learnings/reflector_watermark.json``（原子写）；
同一批候选不会被二次选中。二次运行零新候选是 AC 契约。

候选输出只**引用**证据：check 失败摘要（check_type + 截断 detail）、measured 轴
issues 引用、before/after revision id——绝不复制脚本内容；instruction_summary
沿用 schema 的 ≤120 字符上限。

纯函数纪律：本模块选择逻辑绝不 raise——坏记录（字段缺失/类型错）逐条跳过，
整体异常 → 返回空候选 + 原 watermark 前进语义（见 select 的兜底）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from openbrep.learning import LEARNINGS_DIR
from openbrep.quality.schema import INSTRUCTION_MAX_CHARS

logger = logging.getLogger(__name__)

# ── 版本化口径常量（v1，改口径 = 新版本常量，不原地改语义） ─────────────

REFLECTOR_V1: dict[str, Any] = {
    # outcome 失败三态：交付但门禁未过 / 预算耗尽 / 超时（infrastructure_error
    # 与 cancelled 是环境/人为终态，不是「该反思的失败」，v1 不进候选）。
    "failure_outcomes": ("gate_fail", "budget_exhausted", "timeout"),
    # measured 轴内的 issue 载体字段名：list（逐条）或 dict（marker→count）。
    "axis_issue_fields": ("issues", "sweep_issues"),
    # issues 摘要每条上限条数（evidence 只引用代表性条目；issue_count 是全量）。
    "evidence_issue_cap": 20,
}

# 单条 check/detail 截断上限（evidence 摘要用；指令摘要沿用 schema ≤120）。
CHECK_DETAIL_MAX_CHARS = 240
# 候选输出中 issue 摘要单条 detail 截断上限。
ISSUE_DETAIL_MAX_CHARS = 200
# watermark 文件（相对 learnings 目录，与 distilled_lessons.jsonl 同目录）。
REFLECTOR_WATERMARK_FILE = "reflector_watermark.json"
# watermark dict 里已处理 run_id 集合的键。
WATERMARK_RUN_IDS_KEY = "processed_run_ids"


# ── 字段形状（v1：记录来自 quality/schema.py QualityRecord.to_dict） ─────

def _str(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, (str, int, float)) else default


def _axis_issue_items(axis_name: str, axis: Any) -> list[dict[str, Any]]:
    """把一个 measured 轴的 issue 载体展开为 [{check_type, detail}]。

    只认 axis 的 status == "measured"；其余（unavailable / not_applicable /
    未知状态）一律返回 []（n/a 纪律）。轴形状异常返回 []（不 raise）。
    """
    if not isinstance(axis, dict):
        return []
    if axis.get("status") != "measured":
        return []
    items: list[dict[str, Any]] = []
    for field in REFLECTOR_V1["axis_issue_fields"]:
        payload = axis.get(field)
        if isinstance(payload, list):
            for index, entry in enumerate(payload):
                if isinstance(entry, dict):
                    kind = _str(entry.get("kind") or entry.get("check_type"), "")
                    detail = entry.get("detail")
                    if isinstance(detail, (dict, list)):
                        try:
                            detail = json.dumps(detail, ensure_ascii=False, default=str)
                        except Exception:
                            detail = ""
                    items.append({
                        "check_type": kind or f"{axis_name}.issue[{index}]",
                        "detail": _str(detail),
                    })
                elif isinstance(entry, (str, int, float)):
                    items.append({
                        "check_type": f"{axis_name}.issue[{index}]",
                        "detail": _str(entry),
                    })
        elif isinstance(payload, dict):
            for marker, count in payload.items():
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    n = 0
                if n > 0:
                    items.append({
                        "check_type": _str(marker, f"{axis_name}.sweep"),
                        "detail": f"{n} 次",
                    })
    return items


def _record_issues(record: dict) -> list[dict[str, Any]]:
    """记录的全部 measured 轴 issue 摘要（全量，不做条数截断）。"""
    issues: list[dict[str, Any]] = []
    artifact = record.get("artifact_quality")
    if isinstance(artifact, dict):
        for axis_name in sorted(artifact.keys()):
            issues.extend(_axis_issue_items(axis_name, artifact.get(axis_name)))
    return issues


def _truncate(text: Any, limit: int) -> str:
    s = _str(text)
    return s if len(s) <= limit else s[:limit]


def _check_failures(record: dict) -> list[dict[str, Any]]:
    """check 失败摘要：delivery 三件套（compile/static/semantic）+ outcome 补充。

    只引用记录里已有的信号（delivery / execution_cost），不重算不猜测。
    顺序固定、detail 截断，输出确定。
    """
    checks: list[dict[str, Any]] = []
    delivery = record.get("delivery")
    if isinstance(delivery, dict):
        if delivery.get("status") == "fail":
            checks.append({"check_type": "delivery", "detail": "验证门禁未过"})
        compile_block = delivery.get("compile")
        if isinstance(compile_block, dict) and compile_block.get("status") == "fail":
            checks.append({
                "check_type": "compile",
                "detail": f"编译失败（mode={compile_block.get('mode') or 'unknown'}）",
            })
        static_block = delivery.get("static")
        if isinstance(static_block, dict) and static_block.get("status") == "fail":
            checks.append({
                "check_type": "static",
                "detail": f"{int(static_block.get('errors') or 0)} 个静态错误 / "
                          f"{int(static_block.get('warnings') or 0)} 个警告",
            })
        semantic_block = delivery.get("semantic")
        if isinstance(semantic_block, dict) and semantic_block.get("status") == "fail":
            checks.append({
                "check_type": "semantic",
                "detail": f"{int(semantic_block.get('blocking') or 0)} 个阻断问题",
            })
    outcome = _str(record.get("outcome"))
    cost = record.get("execution_cost")
    if not isinstance(cost, dict):
        cost = {}
    if outcome == "budget_exhausted":
        checks.append({
            "check_type": "budget_exhausted",
            "detail": f"工具预算耗尽（llm_calls={cost.get('llm_calls')}, "
                      f"tool_calls={cost.get('tool_calls')}, "
                      f"repair_rounds={int(cost.get('repair_rounds') or 0)}）",
        })
    elif outcome == "timeout":
        checks.append({
            "check_type": "timeout",
            "detail": f"执行超时（elapsed_sec={cost.get('elapsed_sec')}）",
        })
    elif outcome == "gate_fail" and not checks:
        checks.append({"check_type": "outcome", "detail": "交付但验证/编译门禁未过（无细化信号）"})
    return [
        {
            "check_type": _str(c.get("check_type"), "unknown"),
            "detail": _truncate(c.get("detail"), CHECK_DETAIL_MAX_CHARS),
        }
        for c in checks
    ]


def _revisions(record: dict) -> dict[str, Any]:
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return {"before_revision": None, "after_revision": None}
    return {
        "before_revision": provenance.get("before_revision") or None,
        "after_revision": provenance.get("after_revision") or None,
    }


def _run_sort_key(record: dict) -> tuple[str, str]:
    """记录时间序：ts（ISO 可字符串比较）优先，缺失用 run_id 兜底。"""
    ts = _str(record.get("ts"))
    run_id = _str(record.get("run_id"))
    return (ts, run_id) if ts else ("", run_id)


def _is_zero_issue(record: dict) -> bool:
    return not _record_issues(record)


# ── 主选择器（纯函数，绝不 raise） ─────────────────────────

def select_reflection_candidates(
    scan_root: Any,
    watermark: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从 ``scan_root`` 的质量账本里选出值得反思的候选 + 增量后的新 watermark。

    返回 (candidates, new_watermark)：
    - candidates：按 run_id 排序的候选列表（形状见模块 docstring）；
    - new_watermark：``{processed_run_ids: [...]}``，= 旧集合 ∪ 本次全部候选
      run_id（已处理即不再浮现）；调用方蒸馏成功后才持久化。

    watermark 缺省 = 空（首次全量）。坏记录逐条跳过；整体异常兜底
    （空候选 + 仅旧集合的 watermark），绝不 raise。
    """
    try:
        processed = _processed_set(watermark)
        records = _load_records_guarded(scan_root)
        # run_id 去重（跨项目同 id 不可能，防御性保留先见者）。
        by_id: dict[str, dict] = {}
        for record in records:
            run_id = _str(record.get("run_id"))
            if run_id and run_id not in by_id:
                by_id[run_id] = record
        all_records = list(by_id.values())

        candidates: list[dict[str, Any]] = []
        for record in all_records:
            run_id = _str(record.get("run_id"))
            if not run_id or run_id in processed:
                continue
            try:
                outcome = _str(record.get("outcome"))
                outcome_fail = outcome in REFLECTOR_V1["failure_outcomes"]
                issues = _record_issues(record)
                if not outcome_fail and not issues:
                    continue  # completed 零 issues：只作对照
                candidate = {
                    "run_id": run_id,
                    "project_name": _record_project_name(record),
                    "path_hash": _record_path_hash(record),
                    "intent": _str(record.get("intent"), "unknown"),
                    "outcome": outcome,
                    "instruction_summary": _truncate(
                        record.get("instruction_summary"), INSTRUCTION_MAX_CHARS
                    ),
                    "ts": _str(record.get("ts")),
                    "evidence": {
                        "check_failures": _check_failures(record),
                        "issues": [
                            {
                                "check_type": _truncate(
                                    item.get("check_type"), CHECK_DETAIL_MAX_CHARS
                                ),
                                "detail": _truncate(item.get("detail"), ISSUE_DETAIL_MAX_CHARS),
                            }
                            for item in issues[: int(REFLECTOR_V1["evidence_issue_cap"])]
                        ],
                        "issue_count": len(issues),
                    },
                    "revisions": _revisions(record),
                    "contrast_run_id": None,
                }
                candidates.append(candidate)
            except Exception:
                logger.warning("quality reflector: skip malformed record %s", run_id, exc_info=True)

        contrast_by_intent = _build_contrast_pools(all_records)
        for candidate in candidates:
            candidate["contrast_run_id"] = _pick_contrast(candidate, contrast_by_intent)

        candidates.sort(key=lambda c: _str(c.get("run_id")))
        new_processed = sorted(set(processed) | {_str(c.get("run_id")) for c in candidates})
        return candidates, {WATERMARK_RUN_IDS_KEY: new_processed}
    except Exception:
        logger.warning("quality reflector: selection failed (best-effort)", exc_info=True)
        try:
            fallback = sorted(_processed_set(watermark))
        except Exception:
            fallback = []
        return [], {WATERMARK_RUN_IDS_KEY: fallback}


def _processed_set(watermark: Optional[dict[str, Any]]) -> set[str]:
    if not isinstance(watermark, dict):
        return set()
    raw = watermark.get(WATERMARK_RUN_IDS_KEY)
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw if isinstance(item, (str, int, float))}


def _load_records_guarded(scan_root: Any) -> list[dict]:
    """load_records 的防御包装：任何异常 → 空列表（坏账本不拖垮选择）。"""
    try:
        from openbrep.quality.store import load_records

        return load_records(scan_root)
    except Exception:
        logger.warning("quality reflector: scan_root read failed (skip)", exc_info=True)
        return []


def _record_project_name(record: dict) -> str:
    ref = record.get("project_ref")
    if isinstance(ref, dict):
        return _str(ref.get("name"))
    return ""


def _record_path_hash(record: dict) -> str:
    ref = record.get("project_ref")
    if isinstance(ref, dict):
        return _str(ref.get("path_hash"))
    return ""


def _build_contrast_pools(records: list[dict]) -> dict[str, list[dict]]:
    """同 intent（大写归一）的 completed 零 issues 记录池，按时间正序。"""
    pools: dict[str, list[dict]] = {}
    for record in records:
        if _str(record.get("outcome")) != "completed" or not _is_zero_issue(record):
            continue
        intent = _str(record.get("intent"), "unknown").upper()
        pools.setdefault(intent, []).append(record)
    for pool in pools.values():
        pool.sort(key=_run_sort_key)
    return pools


def _pick_contrast(candidate: dict, pools: dict[str, list[dict]]) -> Optional[str]:
    """对照选择（v1）：同 intent；有同 path_hash 的子集时只在该子集取最近。"""
    pool = pools.get(_str(candidate.get("intent"), "unknown").upper())
    if not pool:
        return None
    same_project = [r for r in pool if _record_path_hash(r) == _str(candidate.get("path_hash"))]
    pool = same_project if same_project else pool
    return _str(pool[-1].get("run_id")) or None


# ── watermark 持久化（原子写，best-effort） ───────────────

def reflector_watermark_path(work_dir: Any) -> Path:
    return Path(work_dir) / LEARNINGS_DIR / REFLECTOR_WATERMARK_FILE


def load_watermark(work_dir: Any) -> dict[str, Any]:
    """读 reflector watermark；缺失/坏 JSON → 空集合（静默）。"""
    try:
        data = json.loads(reflector_watermark_path(work_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get(WATERMARK_RUN_IDS_KEY), list):
            return {
                WATERMARK_RUN_IDS_KEY: [
                    str(item) for item in data[WATERMARK_RUN_IDS_KEY]
                    if isinstance(item, (str, int, float))
                ]
            }
    except Exception:
        pass
    return {WATERMARK_RUN_IDS_KEY: []}


def save_watermark(work_dir: Any, watermark: dict[str, Any]) -> bool:
    """原子写 watermark（tmp + replace）；失败 best-effort 返回 False。"""
    try:
        path = reflector_watermark_path(work_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(watermark, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as exc:
        logger.warning("quality reflector save_watermark failed (best-effort): %s", exc)
        return False
