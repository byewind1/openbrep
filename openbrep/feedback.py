"""项目级 LLM 反馈信号采集（只采集，不分析）。

把各路径的 LLM 反馈信号统一写入项目级 ``<project_root>/.openbrep/feedback.jsonl``，
一行一条 JSON 事件：

    {ts, kind, project, summary, detail, revision_id?, trace_id?}

纪律：
- 只采集：本模块不分析、不聚类、不读回；任何聚合/统计逻辑都不在这里。
- best-effort：目录自动建；任何异常只 ``logger.warning`` 绝不抛出；
  项目未落盘（不是 HSF 目录结构）直接跳过。
- 调用点都是"加一行式调用"，不改动任何判定逻辑，写失败绝不影响主流程。
- summary 自动截断到 200 字；detail.instruction 自动截断到 100 字；
  不存储 API key / 密钥等敏感信息（调用方负责不传入）。

kind 枚举：
- compile_failure         完成门禁最终未过且编译失败
- semantic_blocking       语义验证存在 blocking issue
- semantic_repair_outcome 语义修复闭环跑了 ≥1 轮
- dsl_fallback            参数级修改 DSL 回落 LLM 路径（带 reason）
- patch_failure           局部编辑匹配 0 次 / 多次
- plan_rejected           用户拒绝了修改计划
- skill_proposal_outcome  技能提案结果（后续任务使用，仅定义）
- skill_injection_outcome 技能注入任务结局（pass/fail，驱动 fail_count 治理）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openbrep.revisions import is_hsf_project_dir

logger = logging.getLogger(__name__)

# kind 枚举（skill_proposal_outcome / skill_injection_outcome 由 GUI 侧写入）
FEEDBACK_KINDS: frozenset[str] = frozenset({
    "compile_failure",
    "semantic_blocking",
    "semantic_repair_outcome",
    "dsl_fallback",
    "patch_failure",
    "plan_rejected",
    "skill_proposal_outcome",
    "skill_injection_outcome",
})

# 字段长度纪律
SUMMARY_MAX_CHARS = 200          # summary ≤ 200 字
INSTRUCTION_MAX_CHARS = 100      # detail.instruction 截断 100 字
_DETAIL_STRING_MAX_CHARS = 500   # detail 内其他字符串字段的兜底上限


def truncate(text: Any, limit: int) -> str:
    """把任意值压成字符串并截断到 limit 字符（best-effort，绝不抛出）。"""
    try:
        s = str(text or "")
    except Exception:
        s = ""
    if len(s) <= limit:
        return s
    return s[:limit]


def _sanitize_detail(detail: Any) -> Optional[dict]:
    """把 detail 规整成可 JSON 序列化的 dict；null/非法的返回 None。

    - dict：instruction 字段截断到 100 字，其余字符串兜底 500 字；
    - 其他类型：包一层 {"value": ...}；无法序列化则丢弃。
    """
    if detail is None:
        return None
    if not isinstance(detail, dict):
        try:
            json.dumps(detail)
        except (TypeError, ValueError):
            return None
        return {"value": truncate(detail, _DETAIL_STRING_MAX_CHARS)}
    cleaned: dict[str, Any] = {}
    for key, value in detail.items():
        if key == "instruction":
            cleaned[key] = truncate(value, INSTRUCTION_MAX_CHARS)
        elif isinstance(value, str):
            cleaned[key] = truncate(value, _DETAIL_STRING_MAX_CHARS)
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[key] = value
        else:
            # list/dict 等复合值：原样保留，交给 json.dumps 判定
            try:
                json.dumps(value)
                cleaned[key] = value
            except (TypeError, ValueError):
                cleaned[key] = truncate(value, _DETAIL_STRING_MAX_CHARS)
    return cleaned


def append_feedback(project_root: Any, event: dict[str, Any]) -> bool:
    """追加一条反馈事件到 ``<project_root>/.openbrep/feedback.jsonl``。

    event 字段（写盘时会补 ts/project 并做截断）：
    - kind: FEEDBACK_KINDS 之一（必填）
    - summary: 人类可读摘要，自动截断 200 字（必填，空串也允许）
    - detail: dict，instruction 字段自动截断 100 字（可选）
    - revision_id / trace_id: 可选的关联标识（可选）

    返回 True = 已写入；False = 跳过（项目未落盘/未知 kind）或写入失败。
    任何异常只 ``logger.warning``，绝不抛出，绝不影响主流程。
    """
    try:
        root = Path(project_root)
        if not is_hsf_project_dir(root):
            logger.warning("feedback: project not on disk, skip: %s", root)
            return False
        if not isinstance(event, dict):
            logger.warning("feedback: event must be a dict, got %r", type(event).__name__)
            return False
        kind = event.get("kind")
        if kind not in FEEDBACK_KINDS:
            logger.warning("feedback: unknown kind %r, skip", kind)
            return False
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "project": truncate(event.get("project"), 120) or root.name,
            "summary": truncate(event.get("summary"), SUMMARY_MAX_CHARS),
        }
        detail = _sanitize_detail(event.get("detail"))
        if detail:
            record["detail"] = detail
        for key in ("revision_id", "trace_id"):
            value = event.get(key)
            if value:
                record[key] = truncate(value, 120)
        feedback_path = root / ".openbrep" / "feedback.jsonl"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with feedback_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # best-effort：任何失败只降级为 warning
        logger.warning("append_feedback failed (best-effort): %s", exc)
        return False
