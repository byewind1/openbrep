"""ST03：ST02 delivery_source → 工作台展示契约（纯映射，不改 prompt）。

Consumer 规则：
- verified_change：展示 before→after；
- partial_change：展示已改文件与未完成原因，无成功总绿勾；
- snapshot_failed：区分「检查通过 / 版本失败」；
- 旧记录缺 delivery_source：显示「旧记录，未关联」；
- 声称修改但无 diff：明确「未产生源码变化」，不显示已修复。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from openbrep.runtime.delivery_finalizer import (
    ERR_EPOCH_CHANGED,
    ERR_EXISTING_AFTER_INVALID,
    ERR_NOT_REVISIONABLE,
    ERR_SNAPSHOT_WRITE_FAILED,
    ERR_SOURCE_CHANGED_AFTER_VERIFICATION,
    STATE_FAILED_NO_CHANGE,
    STATE_PARTIAL_CHANGE,
    STATE_SNAPSHOT_FAILED,
    STATE_UNCHANGED,
    STATE_VERIFIED_CHANGE,
    STATES,
    DeliverySource,
)

STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"
STATUS_NO_CHANGE = "no_change"
STATUS_FAILED = "failed"
STATUS_SNAPSHOT_FAILED = "snapshot_failed"
STATUS_UNLINKED = "unlinked"

ERROR_CODE_REASONS: dict[str, str] = {
    ERR_SNAPSHOT_WRITE_FAILED: "after 版本快照写盘失败",
    ERR_SOURCE_CHANGED_AFTER_VERIFICATION: "验证完成后源被外部修改，拒绝绑定 after",
    ERR_EPOCH_CHANGED: "任务执行期间项目已切换，拒绝写入新项目的版本",
    ERR_EXISTING_AFTER_INVALID: "已有 after 快照无效或指纹不一致",
    ERR_NOT_REVISIONABLE: "项目尚未保存为 HSF 目录，无法创建版本快照",
    "after_fingerprint_mismatch": "after 指纹与验证后源不一致",
}

PARTIAL_DEFAULT_REASON = "任务中断或验证未完成，存在部分修改；after 版本未创建"
FAILED_NO_CHANGE_REASON = "任务中断，未产生源码变化"
UNCHANGED_CLAIMED_REASON = "请求了修改，但未检测到源码差异"
UNCHANGED_EXPLAIN_REASON = "本次为解释/检查，未修改源码"
SNAPSHOT_CHECK_PASSED_REASON = "检查通过，但版本快照失败"
SNAPSHOT_CHECK_FAILED_REASON = "检查未通过，且版本快照失败"


def _reason_for_error_code(error_code: str | None) -> str:
    if not error_code:
        return ""
    return ERROR_CODE_REASONS.get(error_code, str(error_code))


def unlinked_delivery_presentation() -> dict[str, Any]:
    """旧质量/任务记录缺 delivery_source 时的展示态。"""
    return {
        "state": None,
        "status": STATUS_UNLINKED,
        "unlinked": True,
        "headline": "旧记录，未关联交付版本",
        "reason": "该记录产生于 delivery_source 契约之前，无法定位 before/after",
        "show_success_badge": False,
        "show_before_after": False,
        "show_changed_files": False,
        "can_recover": False,
        "can_continue": False,
        "can_view_diff": False,
        "recover_revision_id": None,
        "before_revision_id": None,
        "after_revision_id": None,
        "changed_files": [],
        "run_id": None,
        "error_code": None,
        "check_status": "unknown",
        "version_status": None,
        "original_instruction": None,
        "continued_from": None,
    }


def build_delivery_presentation(
    delivery_source: Mapping[str, Any] | DeliverySource | None,
    *,
    intent: str = "",
    claimed_change: bool | None = None,
    verification_passed: bool | None = None,
    original_instruction: str | None = None,
    continued_from: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把 ST02 delivery_source 映射为工作台展示字段。"""
    if isinstance(delivery_source, DeliverySource):
        ds = delivery_source
    else:
        ds = DeliverySource.from_dict(delivery_source if isinstance(delivery_source, Mapping) else None)
    if ds is None or ds.state not in STATES:
        return unlinked_delivery_presentation()

    intent_l = (intent or "").strip().lower()
    if claimed_change is None:
        # 明确修改意图（非 explain/chat）默认视为「声称会改」
        claimed_change = intent_l in {"modify", "debug", "repair", "create", "image"}
    if verification_passed is None:
        # ST02 路径：verified_change 隐含检查通过；partial/failed 未知
        verification_passed = ds.state == STATE_VERIFIED_CHANGE

    check_status = "passed" if verification_passed else "failed"
    if verification_passed is None:
        check_status = "unknown"
    version_status = ds.snapshot_status or None
    files = list(ds.changed_files or [])

    base: dict[str, Any] = {
        "state": ds.state,
        "status": STATUS_FAILED,
        "unlinked": False,
        "headline": "",
        "reason": "",
        "show_success_badge": False,
        "show_before_after": False,
        "show_changed_files": bool(files),
        "can_recover": bool(ds.before_revision_id),
        "can_continue": False,
        "can_view_diff": bool(ds.before_revision_id and (ds.after_revision_id or files)),
        "recover_revision_id": ds.before_revision_id,
        "before_revision_id": ds.before_revision_id,
        "after_revision_id": ds.after_revision_id,
        "changed_files": files,
        "run_id": ds.run_id or None,
        "error_code": ds.error_code,
        "check_status": check_status,
        "version_status": version_status,
        "original_instruction": original_instruction or None,
        "continued_from": dict(continued_from) if continued_from else None,
    }

    if ds.state == STATE_VERIFIED_CHANGE:
        base.update(
            status=STATUS_COMPLETED,
            headline="已交付修改",
            reason="验证通过并绑定 after 版本",
            show_success_badge=True,
            show_before_after=bool(ds.before_revision_id and ds.after_revision_id),
            can_recover=bool(ds.before_revision_id),
            can_continue=False,
            can_view_diff=bool(ds.before_revision_id and ds.after_revision_id),
            check_status="passed",
        )
        return base

    if ds.state == STATE_UNCHANGED:
        reason = UNCHANGED_CLAIMED_REASON if claimed_change else UNCHANGED_EXPLAIN_REASON
        headline = "未产生源码变化" if claimed_change else "仅解释/检查，未修改源码"
        base.update(
            status=STATUS_NO_CHANGE,
            headline=headline,
            reason=reason,
            show_success_badge=False,
            show_before_after=False,
            can_recover=False,
            # 修改请求无 diff：允许用户继续描述，不把「继续」当独立重试
            can_continue=bool(claimed_change),
            can_view_diff=False,
        )
        return base

    if ds.state == STATE_PARTIAL_CHANGE:
        reason = _reason_for_error_code(ds.error_code) or PARTIAL_DEFAULT_REASON
        base.update(
            status=STATUS_INCOMPLETE,
            headline="未完成，存在部分修改",
            reason=reason,
            show_success_badge=False,
            show_before_after=False,
            show_changed_files=bool(files) or bool(ds.before_revision_id),
            can_recover=bool(ds.before_revision_id),
            can_continue=True,
            can_view_diff=bool(ds.before_revision_id),
        )
        return base

    if ds.state == STATE_FAILED_NO_CHANGE:
        reason = _reason_for_error_code(ds.error_code) or FAILED_NO_CHANGE_REASON
        base.update(
            status=STATUS_FAILED,
            headline="任务失败，未产生源码变化",
            reason=reason,
            show_success_badge=False,
            show_before_after=False,
            can_recover=bool(ds.before_revision_id),
            can_continue=bool(original_instruction),
            can_view_diff=False,
        )
        return base

    # snapshot_failed：区分检查结果与版本结果
    if verification_passed:
        reason = _reason_for_error_code(ds.error_code) or SNAPSHOT_CHECK_PASSED_REASON
        headline = "检查通过，版本快照失败"
        check_status = "passed"
    else:
        reason = _reason_for_error_code(ds.error_code) or SNAPSHOT_CHECK_FAILED_REASON
        headline = "检查未通过，版本快照失败"
        check_status = "failed"
    base.update(
        status=STATUS_SNAPSHOT_FAILED,
        headline=headline,
        reason=reason,
        show_success_badge=False,
        show_before_after=False,
        show_changed_files=bool(files),
        can_recover=bool(ds.before_revision_id),
        can_continue=bool(original_instruction) or bool(ds.before_revision_id),
        can_view_diff=bool(ds.before_revision_id),
        check_status=check_status,
        version_status=version_status or "failed",
    )
    return base


def attach_continue_linkage(
    presentation: dict[str, Any],
    continue_from: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """继续操作显式关联原 run 与原始指令。"""
    if not continue_from:
        return presentation
    origin_run_id = continue_from.get("origin_run_id") or continue_from.get("run_id")
    original_instruction = continue_from.get("original_instruction") or continue_from.get("instruction")
    linked = {
        "origin_run_id": origin_run_id or None,
        "original_instruction": original_instruction or None,
    }
    presentation = dict(presentation)
    presentation["continued_from"] = linked
    if original_instruction and not presentation.get("original_instruction"):
        presentation["original_instruction"] = original_instruction
    return presentation


def normalize_continue_from(payload: Any) -> Optional[dict[str, Any]]:
    """校验前端 continue_from 载荷；不合法返回 None。"""
    if not isinstance(payload, Mapping):
        return None
    origin_run_id = str(payload.get("origin_run_id") or payload.get("run_id") or "").strip()
    original_instruction = str(
        payload.get("original_instruction") or payload.get("instruction") or ""
    ).strip()
    if not origin_run_id and not original_instruction:
        return None
    out: dict[str, Any] = {}
    if origin_run_id:
        out["origin_run_id"] = origin_run_id
    if original_instruction:
        out["original_instruction"] = original_instruction
    intent = str(payload.get("intent") or "").strip()
    if intent:
        out["intent"] = intent
    return out or None


def delivery_payload_from_result(
    result: Any,
    *,
    original_instruction: str | None = None,
    continue_from: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """TaskResult → assistant.delivery 完整载荷（含原始 ST02 dict）。"""
    metadata = getattr(result, "metadata", None) or {}
    raw = metadata.get("delivery_source")
    if not isinstance(raw, Mapping):
        raw = None
    if continue_from is None:
        continue_from = normalize_continue_from(metadata.get("continue_from"))
    intent = str(getattr(result, "intent", "") or "")
    verification = getattr(result, "verification", None) or {}
    verification_passed: Optional[bool]
    if isinstance(verification, Mapping) and "passed" in verification:
        verification_passed = bool(verification.get("passed"))
    else:
        verification_passed = None
    claimed_change: Optional[bool]
    if isinstance(metadata, Mapping) and "claimed_change" in metadata:
        claimed_change = bool(metadata.get("claimed_change"))
    else:
        claimed_change = None
    presentation = build_delivery_presentation(
        raw,
        intent=intent,
        claimed_change=claimed_change,
        verification_passed=verification_passed,
        original_instruction=original_instruction
        or ((continue_from or {}).get("original_instruction") if isinstance(continue_from, Mapping) else None),
        continued_from=continue_from,
    )
    instruction = original_instruction or presentation.get("original_instruction")
    return {
        "delivery_source": dict(raw) if raw else None,
        "presentation": presentation,
        "continue_from": dict(continue_from) if continue_from else None,
        "original_instruction": instruction,
    }
