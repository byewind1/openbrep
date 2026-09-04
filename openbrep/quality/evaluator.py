"""质量档案求值器（纯函数，observer-only）。

``build_quality_record(request, result, ...)`` 只读 TaskResult / 请求上下文里
**已算过**的信号（verification dict、compile_result、metadata.execution、
semantic_repair），绝不重跑编译/预览/语义验证，绝不调 LLM。

纪律（AC-G1-2）：任何子段异常 → 对应字段记 ``unavailable``，函数整体
never raises——已完成的交付绝不允许被观测层拖垮。
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openbrep.quality.schema import INSTRUCTION_MAX_CHARS, QualityRecord

logger = logging.getLogger(__name__)

SCORE_PROFILE = "quality-v1"

# 语义验证 sweep 口径标注（AC-G1-3：如实记录现状口径，本单不改扰动逻辑）：
# semantic_verifier.DEFAULT_SWEEP_MAX_PARAMS=12、数值 +50% 单向扰动、String 跳过。
_SWEEP_METHOD = (
    "semantic_verifier param sweep（≤12 个数值参数，+50% 单向扰动，"
    "Boolean 0/1 翻转，String 跳过）"
)
# sweep issue 在 verification dict 里的可识别标记（我们自己生成的稳定文案）：
# errors_caught 条目带 "[<check_type>]" 前缀；remaining_risks 只存 detail 文本。
_SWEEP_MARKERS = {
    "sweep_unresponsive": "几何完全无变化",
    "sweep_mesh_vanished": "几何完全消失",
    "sweep_preview_error": "扫描时预览异常",
}


def project_ref_for(project_root: Any) -> dict:
    """隐私纪律（AC-G1-6）：只存 sha256 前 12 位路径哈希 + 目录名，不存完整路径。"""
    root = Path(project_root)
    resolved = str(root.expanduser().resolve())
    return {
        "path_hash": hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12],
        "name": root.name,
    }


def _truncate(text: Any, limit: int = INSTRUCTION_MAX_CHARS) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[:limit]


# ── 终态映射（AC-G0-2：从既有信号确定性推出，每条规则注明信号来源）──

def map_outcome(request: Any, result: Any) -> str:
    """TaskResult + 请求上下文 → 终态枚举。规则按优先级短路，来源见逐条注释。"""
    metadata = getattr(result, "metadata", None) or {}
    execution = metadata.get("execution") or {}

    # cancelled —— 信号：agent loop / codex bridge 取消分支写入的
    # execution.cancelled 结构化标志；或请求级取消标志 should_cancel() 收尾时仍为真。
    if execution.get("cancelled"):
        return "cancelled"
    should_cancel = getattr(request, "should_cancel", None)
    if callable(should_cancel):
        try:
            if should_cancel():
                return "cancelled"
        except Exception:
            pass

    # timeout —— 信号：codex 桥 turn finish_reason=="timeout" 汇总进 execution.timeout。
    if execution.get("timeout"):
        return "timeout"

    # infrastructure_error —— 信号 1：result.error 非空。error 只在异常早退 /
    # provider 不可用等基础设施失败时设置，质量门禁失败不走 error 字段。
    if getattr(result, "error", None):
        return "infrastructure_error"
    # infrastructure_error —— 信号 2：编译因编译器缺失未运行（SKIPPED_NO_COMPILER），
    # 不得误记为 gate_fail。
    if _compile_skipped_no_compiler(result):
        return "infrastructure_error"

    # budget_exhausted —— 信号：agent loop / codex bridge 预算计数耗尽标志。
    if execution.get("budget_exhausted"):
        return "budget_exhausted"

    # not_evaluable —— 信号：CHAT 意图（无交付物）；或确认门等待
    # （awaiting_confirmation / awaiting_extraction_confirmation），交付未发生。
    intent = (getattr(result, "intent", "") or getattr(request, "intent", "") or "").upper()
    if intent == "CHAT":
        return "not_evaluable"
    if metadata.get("awaiting_confirmation") or metadata.get("awaiting_extraction_confirmation"):
        return "not_evaluable"

    if not getattr(result, "success", False):
        # not_evaluable —— 信号：失败了但零评估证据（无 verification、无编译、无脚本）。
        if (
            getattr(result, "verification", None) is None
            and getattr(result, "compile_result", None) is None
            and not getattr(result, "scripts", None)
        ):
            return "not_evaluable"
        # gate_fail —— 信号：success=False（verification report 未过 / 编译门禁失败）。
        return "gate_fail"

    # completed —— 信号：success=True 且无上述终态。
    return "completed"


def _compile_skipped_no_compiler(result: Any) -> bool:
    """编译未运行且原因是编译器缺失（SKIPPED_NO_COMPILER 标记）。"""
    verification = getattr(result, "verification", None)
    if not isinstance(verification, dict):
        return False
    for check in verification.get("checks") or []:
        if check.get("check_type") == "compile":
            return check.get("status") == "not_run" and str(
                check.get("detail") or ""
            ).startswith("SKIPPED_NO_COMPILER")
    return False


# ── 三轴提取 ──────────────────────────────────────────────

def _delivery(result: Any) -> dict:
    verification = getattr(result, "verification", None)
    compile_result = getattr(result, "compile_result", None)

    # compile：mode 取 compile_result.mode（real/mock），未运行记 not_run
    if compile_result is not None:
        compile_block = {
            "status": "pass" if compile_result.success else "fail",
            "mode": compile_result.mode or "real",
        }
    else:
        compile_block = {"status": "not_run", "mode": "not_run"}

    if not isinstance(verification, dict):
        # 无 verification（CHAT / 早退）：compile 以 compile_result 为准，其余不可得
        if compile_result is None:
            return {"status": "unavailable", "reason": "no_verification"}
        return {
            "status": compile_block["status"],
            "compile": compile_block,
            "static": {"status": "unavailable", "reason": "no_verification"},
            "semantic": {"status": "unavailable", "reason": "no_verification"},
        }

    checks = verification.get("checks") or []
    # 若 verification 里有 compile 检查但 compile_result 缺失（如门禁补跑前），
    # 以检查状态补齐 status；mode 仍记 not_run。
    for check in checks:
        if check.get("check_type") == "compile":
            if compile_result is None:
                compile_block["status"] = check.get("status") or "not_run"
            break

    static_fails = [
        c for c in checks if c.get("check_type") == "static" and c.get("status") == "fail"
    ]
    static_block = {
        "status": "fail" if static_fails else ("pass" if checks else "not_run"),
        "errors": len(static_fails),
        "warnings": len(verification.get("warnings_caught") or []),
    }

    semantic_block: dict = {"status": "not_run", "blocking": 0}
    for check in checks:
        if check.get("check_type") != "semantic":
            continue
        semantic_block["status"] = check.get("status") or "unknown"
        if check.get("status") == "fail":
            # blocking 计数：detail 形如 "N 个问题"（verification.py 聚合格式）
            match = re.match(r"\s*(\d+)", str(check.get("detail") or ""))
            semantic_block["blocking"] = int(match.group(1)) if match else 1
        break

    return {
        "status": "pass" if verification.get("passed") else "fail",
        "compile": compile_block,
        "static": static_block,
        "semantic": semantic_block,
    }


def _artifact_quality(result: Any) -> dict:
    """只落已有信号（AC-G1-3）：不新增任何几何计算。"""
    verification = getattr(result, "verification", None)
    texts: list[str] = []
    semantic_ran = False
    if isinstance(verification, dict):
        texts = list(verification.get("errors_caught") or []) + list(
            verification.get("remaining_risks") or []
        )
        semantic_ran = any(
            c.get("check_type") == "semantic" for c in verification.get("checks") or []
        )
    sweep_counts = {
        name: sum(1 for t in texts if marker in t)
        for name, marker in _SWEEP_MARKERS.items()
    }
    if semantic_ran:
        parametricity = {
            "status": "measured",
            "score": None,      # 响应率评分属 G2/§3.3，本单不出分
            "coverage": None,
            "method": _SWEEP_METHOD,
            "sweep_issues": sweep_counts,
        }
    else:
        parametricity = {
            "status": "unavailable",
            "score": None,
            "reason": "semantic_not_run",
        }
    return {
        "requirements": {"status": "not_applicable", "score": None, "coverage": None},
        "parametricity": parametricity,
        "dimension_contract": {"status": "unavailable", "score": None, "reason": "no_contract"},
        "cross_script": {"status": "not_applicable", "score": None, "coverage": None},
        "topology": {"status": "unavailable", "score": None, "reason": "no_geometry_metrics"},
    }


def _execution_cost(result: Any, elapsed_sec: float) -> dict:
    metadata = getattr(result, "metadata", None) or {}
    execution = metadata.get("execution") or {}
    repair = getattr(result, "semantic_repair", None) or {}
    return {
        # llm_calls/tool_calls：agent loop / codex bridge 写入真实计数；
        # 未埋点路径为 None（unavailable），禁止解析文本反推。
        "llm_calls": execution.get("llm_calls"),
        "tool_calls": execution.get("tool_calls"),
        "repair_rounds": int(repair.get("attempted", 0) or 0),
        "elapsed_sec": round(float(elapsed_sec), 3),
        "timeout": bool(execution.get("timeout", False)),
        "budget_exhausted": bool(execution.get("budget_exhausted", False)),
    }


_REPO_ROOT = Path(__file__).resolve().parents[2]
_commit_cache: Optional[str] = None


def repo_commit() -> Optional[str]:
    """当前代码 commit（best-effort，进程内缓存一次）。"""
    global _commit_cache
    if _commit_cache is not None:
        return _commit_cache or None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        _commit_cache = proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        _commit_cache = ""
    return _commit_cache or None


def _provenance(result: Any, context: dict) -> dict:
    metadata = getattr(result, "metadata", None) or {}
    object_plan = getattr(result, "object_plan", None) or {}
    model_route: list[str] = []
    if context.get("model"):
        model_route.append(str(context["model"]))
    # Auto 路由记录实际 effective model（D6 元数据），不只记最终选择
    codex_effective = metadata.get("codex_effective")
    if isinstance(codex_effective, dict):
        effective = codex_effective.get("model") or codex_effective.get("effective_model")
        if effective and str(effective) not in model_route:
            model_route.append(str(effective))
    knowledge_sources = object_plan.get("knowledge_sources") or []
    return {
        "commit": context.get("commit"),
        "score_profile": SCORE_PROFILE,
        "model_route": model_route,
        "knowledge_snapshot": ",".join(str(s) for s in knowledge_sources) or None,
        "learning_snapshot": None,
        "before_revision": metadata.get("before_revision_id") or None,
        "after_revision": context.get("after_revision"),
    }


def build_quality_record(
    request: Any,
    result: Any,
    *,
    run_id: str,
    elapsed_sec: float,
    project_root: Any,
    context: Optional[dict] = None,
) -> QualityRecord:
    """TaskResult + 请求上下文 → QualityRecord。never raises（分段降级 unavailable）。"""
    context = dict(context or {})

    try:
        outcome = map_outcome(request, result)
    except Exception:  # 映射自身出错也不许拖垮观测
        logger.warning("quality evaluator: outcome mapping failed", exc_info=True)
        outcome = "not_evaluable"

    try:
        delivery = _delivery(result)
    except Exception:
        logger.warning("quality evaluator: delivery extraction failed", exc_info=True)
        delivery = {"status": "unavailable", "reason": "evaluator_error"}

    try:
        artifact_quality = _artifact_quality(result)
    except Exception:
        logger.warning("quality evaluator: artifact_quality extraction failed", exc_info=True)
        artifact_quality = {"status": "unavailable", "reason": "evaluator_error"}

    try:
        execution_cost = _execution_cost(result, elapsed_sec)
    except Exception:
        logger.warning("quality evaluator: execution_cost extraction failed", exc_info=True)
        execution_cost = {"status": "unavailable", "reason": "evaluator_error"}

    try:
        provenance = _provenance(result, context)
    except Exception:
        logger.warning("quality evaluator: provenance extraction failed", exc_info=True)
        provenance = {"score_profile": SCORE_PROFILE, "status": "unavailable"}

    return QualityRecord(
        run_id=run_id,
        intent=(getattr(result, "intent", "") or getattr(request, "intent", "") or ""),
        outcome=outcome,
        project_ref=project_ref_for(project_root),
        instruction_summary=_truncate(getattr(request, "user_input", "")),
        ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        delivery=delivery,
        artifact_quality=artifact_quality,
        execution_cost=execution_cost,
        provenance=provenance,
    )
