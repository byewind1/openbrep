"""ST02：交付源绑定终结器（共享契约）。

任何成功源码变更可从质量记录定位到准确 after-revision；
失败不能伪造 after。pipeline 在写 trace/quality 前调用一次。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openbrep.source_fingerprint import (
    compute_revision_fingerprint,
    compute_source_fingerprint,
    fingerprints_equal,
)

logger = logging.getLogger(__name__)

DELIVERY_SOURCE_SCHEMA_VERSION = 1

# state 枚举（总控冻结契约）
STATE_VERIFIED_CHANGE = "verified_change"
STATE_UNCHANGED = "unchanged"
STATE_PARTIAL_CHANGE = "partial_change"
STATE_FAILED_NO_CHANGE = "failed_no_change"
STATE_SNAPSHOT_FAILED = "snapshot_failed"
STATES: tuple[str, ...] = (
    STATE_VERIFIED_CHANGE,
    STATE_UNCHANGED,
    STATE_PARTIAL_CHANGE,
    STATE_FAILED_NO_CHANGE,
    STATE_SNAPSHOT_FAILED,
)

SNAPSHOT_SAVED = "saved"
SNAPSHOT_SKIPPED = "skipped"
SNAPSHOT_FAILED = "failed"
SNAPSHOT_REJECTED = "rejected"
SNAPSHOT_NOT_ATTEMPTED = "not_attempted"

ERR_SNAPSHOT_WRITE_FAILED = "snapshot_write_failed"
ERR_SOURCE_CHANGED_AFTER_VERIFICATION = "source_changed_after_verification"
ERR_EPOCH_CHANGED = "epoch_changed"
ERR_AFTER_FINGERPRINT_MISMATCH = "after_fingerprint_mismatch"
ERR_EXISTING_AFTER_INVALID = "existing_after_invalid"
ERR_NOT_REVISIONABLE = "not_revisionable"

_PROTECTION_BEFORE = "current_run_before"
_PROTECTION_AFTER = "latest_successful_after"
_PROTECTION_HOST = "host_acceptance"
_PROTECTION_CANDIDATE = "pending_candidate"


@dataclass
class DeliverySource:
    """TaskResult.metadata.delivery_source 冻结结构。"""

    schema_version: int = DELIVERY_SOURCE_SCHEMA_VERSION
    run_id: str = ""
    state: str = STATE_UNCHANGED
    before_revision_id: str | None = None
    after_revision_id: str | None = None
    source_fingerprint: str | None = None
    changed_files: list[str] = field(default_factory=list)
    snapshot_status: str = SNAPSHOT_NOT_ATTEMPTED
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "state": self.state,
            "before_revision_id": self.before_revision_id,
            "after_revision_id": self.after_revision_id,
            "source_fingerprint": self.source_fingerprint,
            "changed_files": list(self.changed_files),
            "snapshot_status": self.snapshot_status,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Optional["DeliverySource"]:
        if not isinstance(data, dict) or not data:
            return None
        state = data.get("state")
        if state not in STATES:
            return None
        return cls(
            schema_version=int(data.get("schema_version") or DELIVERY_SOURCE_SCHEMA_VERSION),
            run_id=str(data.get("run_id") or ""),
            state=str(state),
            before_revision_id=data.get("before_revision_id") or None,
            after_revision_id=data.get("after_revision_id") or None,
            source_fingerprint=data.get("source_fingerprint") or None,
            changed_files=[str(x) for x in (data.get("changed_files") or [])],
            snapshot_status=str(data.get("snapshot_status") or SNAPSHOT_NOT_ATTEMPTED),
            error_code=data.get("error_code") or None,
        )


@dataclass
class FinalizeDeliveryInputs:
    """共享终结器输入：真实终局 + 验证结果 + 实际变更。"""

    run_id: str
    project: Any = None
    intent: str = ""
    # Handler 声称的任务成功（verification.passed / 确定性路径编译结果）
    handler_success: bool = False
    # 是否声称发生了修改（指令意图或 handler 判定）
    claimed_change: bool = False
    # 实际落盘变更文件
    changed_files: list[str] = field(default_factory=list)
    # 终局中断信号（timeout / cancel / tool_error / budget_exhausted / epoch_violated）
    interrupted: bool = False
    # 验证是否通过（与 handler_success 分开：允许验证通过但 snapshot 失败）
    verified: bool = False
    before_revision_id: str | None = None
    # 老路径已创建的 after（CREATE / 老 MODIFY 编译通过后）；None = 由终结器创建
    existing_after_revision_id: str | None = None
    # 验证完成时捕获的源指纹（用于拒绝中途外部改源）
    verified_source_fingerprint: str | None = None
    epoch_guard: Optional[Callable[[], bool]] = None
    create_revision: Optional[Callable[..., Any]] = None
    can_revision_project: Optional[Callable[[Any], bool]] = None
    compile_metadata: Optional[dict] = None
    explanation: str = ""
    # 已有 after 时是否校验指纹与当前/验证源一致
    validate_existing_after: bool = True
    # ST03 F2：继续操作关联（写入 after revision metadata，刷新后可追溯）
    continue_from: Optional[dict] = None


def _epoch_ok(inputs: FinalizeDeliveryInputs) -> bool:
    if inputs.epoch_guard is None:
        return True
    try:
        return bool(inputs.epoch_guard())
    except Exception:
        return True


def _can_revision(inputs: FinalizeDeliveryInputs) -> bool:
    project = inputs.project
    if project is None:
        return False
    if inputs.can_revision_project is not None:
        try:
            return bool(inputs.can_revision_project(project))
        except Exception:
            return False
    from pathlib import Path

    from openbrep.revisions import is_hsf_project_dir

    root = Path(getattr(project, "root", "") or "")
    try:
        return root.is_dir() and is_hsf_project_dir(root)
    except Exception:
        return False


def _has_real_change(inputs: FinalizeDeliveryInputs) -> bool:
    return bool(inputs.changed_files)


def classify_delivery_state(
    inputs: FinalizeDeliveryInputs,
) -> tuple[str, str | None]:
    """纯状态分类（不落盘）。返回 (state, error_code)。"""
    has_change = _has_real_change(inputs)

    # epoch 在 before 之后被改变：拒绝给新项目写快照/质量
    if not _epoch_ok(inputs):
        if has_change:
            return STATE_PARTIAL_CHANGE, ERR_EPOCH_CHANGED
        return STATE_FAILED_NO_CHANGE, ERR_EPOCH_CHANGED

    if not has_change:
        # 无工具解释/只检查；或明确修改但没有 diff（编译通过≠修改完成）
        if inputs.claimed_change and not inputs.interrupted:
            return STATE_UNCHANGED, None
        if inputs.interrupted:
            return STATE_FAILED_NO_CHANGE, None
        return STATE_UNCHANGED, None

    if inputs.interrupted:
        # 写一部分后 timeout/cancel/tool error：after 必须为 null
        return STATE_PARTIAL_CHANGE, None

    if not inputs.verified:
        # 有变更但验证未过：不宣称 verified_change，也不伪造 after
        return STATE_PARTIAL_CHANGE, None

    return STATE_VERIFIED_CHANGE, None


def _create_after_revision(
    inputs: FinalizeDeliveryInputs,
    parent_revision_id: str | None,
    warnings: list[str],
) -> tuple[str | None, str | None, str | None]:
    """创建 after revision。返回 (after_id, fingerprint, error_code)。"""
    if inputs.create_revision is not None:
        create_fn = inputs.create_revision
    else:
        # 函数内惰性导入：便于测试 patch openbrep.revisions.create_revision
        from openbrep.revisions import create_revision as create_fn

    project = inputs.project
    if not _can_revision(inputs):
        warnings.append("项目尚未保存为 HSF 目录，无法创建 after 版本快照")
        return None, None, ERR_NOT_REVISIONABLE
    try:
        # 先算当前源指纹，再快照；快照内容应与指纹一致
        fingerprint = compute_source_fingerprint(project.root)
        revision = create_fn(
            project.root,
            message=f"auto: after {(inputs.intent or 'modify').lower()} (verified)",
            gsm_name=getattr(project, "name", "") or "",
            metadata={
                "compile": inputs.compile_metadata or {},
                "explanation": inputs.explanation,
                "delivery": {
                    "run_id": inputs.run_id,
                    "role": "after",
                    "source_fingerprint": fingerprint,
                    **(
                        {"continue_from": dict(inputs.continue_from)}
                        if isinstance(inputs.continue_from, dict) and inputs.continue_from
                        else {}
                    ),
                },
            },
            trigger=(inputs.intent or "modify").lower(),
            intent=intent_label(inputs.intent),
            user_instruction="",
            changed_files=list(inputs.changed_files),
            parent_revision_id=parent_revision_id,
        )
        after_id = revision.revision_id
        # 快照目录指纹应与创建时工作源指纹一致
        try:
            snap_fp = compute_revision_fingerprint(revision.path)
        except Exception:
            snap_fp = fingerprint
        if not fingerprints_equal(snap_fp, fingerprint):
            warnings.append(
                f"after 快照指纹与创建时源指纹不一致：{after_id}"
            )
            return after_id, fingerprint, ERR_AFTER_FINGERPRINT_MISMATCH
        _register_protections(project, inputs, after_id)
        return after_id, fingerprint, None
    except Exception as exc:
        logger.warning("delivery finalizer: after snapshot failed: %s", exc)
        warnings.append(f"after 版本快照写盘失败：{exc}")
        return None, None, ERR_SNAPSHOT_WRITE_FAILED


def intent_label(intent: str) -> str:
    return (intent or "MODIFY").upper()


def _register_protections(project: Any, inputs: FinalizeDeliveryInputs, after_id: str | None) -> None:
    """统一引用接口：登记受保护 revision，不扫描任意文本。"""
    try:
        from openbrep.revisions import register_revision_protection
    except Exception:
        return
    root = getattr(project, "root", None)
    if not root:
        return
    if inputs.before_revision_id:
        try:
            register_revision_protection(
                root,
                inputs.before_revision_id,
                reason=_PROTECTION_BEFORE,
                run_id=inputs.run_id,
                ref={"kind": "delivery_before", "run_id": inputs.run_id},
            )
        except Exception:
            logger.debug("register before protection failed", exc_info=True)
    if after_id:
        try:
            register_revision_protection(
                root,
                after_id,
                reason=_PROTECTION_AFTER,
                run_id=inputs.run_id,
                ref={"kind": "delivery_after", "run_id": inputs.run_id},
            )
        except Exception:
            logger.debug("register after protection failed", exc_info=True)


def register_external_protection(
    project_dir: Any,
    revision_id: str,
    *,
    reason: str,
    run_id: str | None = None,
    ref: dict | None = None,
) -> dict:
    """后续任务（待审候选 / 宿主验收）通过统一接口登记保护。"""
    from openbrep.revisions import register_revision_protection

    return register_revision_protection(
        project_dir,
        revision_id,
        reason=reason or _PROTECTION_CANDIDATE,
        run_id=run_id,
        ref=ref,
    )


def finalize_delivery(
    inputs: FinalizeDeliveryInputs,
) -> tuple[DeliverySource, list[str]]:
    """共享终结器：产出明确 delivery_source 状态；失败不伪造 after。

    Returns:
        (DeliverySource, warnings)
    """
    warnings: list[str] = []
    changed_files = sorted({str(p) for p in inputs.changed_files if p})
    state, error_code = classify_delivery_state(
        FinalizeDeliveryInputs(
            run_id=inputs.run_id,
            project=inputs.project,
            intent=inputs.intent,
            handler_success=inputs.handler_success,
            claimed_change=inputs.claimed_change,
            changed_files=changed_files,
            interrupted=inputs.interrupted,
            verified=inputs.verified,
            before_revision_id=inputs.before_revision_id,
            existing_after_revision_id=inputs.existing_after_revision_id,
            verified_source_fingerprint=inputs.verified_source_fingerprint,
            epoch_guard=inputs.epoch_guard,
        )
    )

    project = inputs.project
    source_fingerprint: str | None = None
    after_id: str | None = None
    snapshot_status = SNAPSHOT_NOT_ATTEMPTED
    before_id = inputs.before_revision_id or None
    if before_id == "":
        before_id = None

    # epoch 失效：不给新项目写快照/质量
    if not _epoch_ok(inputs):
        snapshot_status = SNAPSHOT_REJECTED
        return (
            DeliverySource(
                run_id=inputs.run_id,
                state=state,
                before_revision_id=before_id,
                after_revision_id=None,
                source_fingerprint=None,
                changed_files=changed_files,
                snapshot_status=snapshot_status,
                error_code=error_code,
            ),
            warnings,
        )

    if state in (STATE_UNCHANGED, STATE_FAILED_NO_CHANGE, STATE_PARTIAL_CHANGE):
        # 失败/中断/无变化：after 必须为 null，不补造
        if state == STATE_PARTIAL_CHANGE and project is not None:
            try:
                source_fingerprint = compute_source_fingerprint(project.root)
            except Exception:
                source_fingerprint = None
        return (
            DeliverySource(
                run_id=inputs.run_id,
                state=state,
                before_revision_id=before_id,
                after_revision_id=None,
                source_fingerprint=source_fingerprint,
                changed_files=changed_files,
                snapshot_status=SNAPSHOT_SKIPPED,
                error_code=error_code,
            ),
            warnings,
        )

    # state == verified_change 候选：绑定真实 after
    current_fp: str | None = None
    if project is not None:
        try:
            current_fp = compute_source_fingerprint(project.root)
        except Exception as exc:
            warnings.append(f"计算当前源指纹失败：{exc}")
            current_fp = None

    # R10：验证后源被外部改变 → 拒绝虚假 verified_change
    if (
        inputs.verified_source_fingerprint
        and current_fp
        and not fingerprints_equal(inputs.verified_source_fingerprint, current_fp)
    ):
        warnings.append(
            "验证完成后源文件被外部修改，拒绝将旧验证绑定到新源"
            f"（error={ERR_SOURCE_CHANGED_AFTER_VERIFICATION}）"
        )
        return (
            DeliverySource(
                run_id=inputs.run_id,
                state=STATE_SNAPSHOT_FAILED,
                before_revision_id=before_id,
                after_revision_id=None,
                source_fingerprint=current_fp,
                changed_files=changed_files,
                snapshot_status=SNAPSHOT_REJECTED,
                error_code=ERR_SOURCE_CHANGED_AFTER_VERIFICATION,
            ),
            warnings,
        )

    # R04：声称修改 / 登记了 changed_files，但 before 快照与当前源指纹一致
    # （无真实 diff）→ state=unchanged，不把「编译通过」当「修改完成」。
    if before_id and current_fp and project is not None:
        try:
            from openbrep.revisions import _find_revision_dir

            before_fp = compute_revision_fingerprint(_find_revision_dir(project.root, before_id))
            if fingerprints_equal(before_fp, current_fp):
                warnings.append("检测到无真实源变更（before 指纹=当前指纹），不绑定 after")
                return (
                    DeliverySource(
                        run_id=inputs.run_id,
                        state=STATE_UNCHANGED,
                        before_revision_id=before_id,
                        after_revision_id=None,
                        source_fingerprint=current_fp,
                        changed_files=changed_files,
                        snapshot_status=SNAPSHOT_SKIPPED,
                        error_code=None,
                    ),
                    warnings,
                )
        except Exception:
            pass

    # 老路径已有 after：传递并校验，不重复生成；若快照已不在盘上则由终结器重建
    if inputs.existing_after_revision_id:
        after_id = inputs.existing_after_revision_id
        snap_fp = None
        try:
            from openbrep.revisions import _find_revision_dir

            rev_dir = _find_revision_dir(project.root, after_id)
            snap_fp = compute_revision_fingerprint(rev_dir)
            if snap_fp is None:
                snap_fp = current_fp
        except FileNotFoundError:
            # handler 声称的 after 不在盘上（测试 mock / 历史 prune）→ 不伪造引用，
            # 改为由终结器按当前源重建 after
            warnings.append(
                f"handler 声称的 after {after_id} 不在盘上，改由终结器重建"
            )
            inputs = FinalizeDeliveryInputs(
                run_id=inputs.run_id,
                project=project,
                intent=inputs.intent,
                handler_success=inputs.handler_success,
                claimed_change=inputs.claimed_change,
                changed_files=changed_files,
                interrupted=False,
                verified=True,
                before_revision_id=before_id,
                existing_after_revision_id=None,
                verified_source_fingerprint=inputs.verified_source_fingerprint,
                epoch_guard=inputs.epoch_guard,
                compile_metadata=inputs.compile_metadata,
                explanation=inputs.explanation,
            )
        except Exception as exc:
            warnings.append(f"读取已有 after 快照失败：{after_id} ({exc})")
            return (
                DeliverySource(
                    run_id=inputs.run_id,
                    state=STATE_SNAPSHOT_FAILED,
                    before_revision_id=before_id,
                    after_revision_id=None,
                    source_fingerprint=current_fp,
                    changed_files=changed_files,
                    snapshot_status=SNAPSHOT_FAILED,
                    error_code=ERR_EXISTING_AFTER_INVALID,
                ),
                warnings,
            )
        else:
            expected = inputs.verified_source_fingerprint or current_fp
            if inputs.validate_existing_after and expected and not fingerprints_equal(snap_fp, expected):
                warnings.append(
                    f"已有 after 快照指纹与验证后源不一致：{after_id}"
                    f"（error={ERR_EXISTING_AFTER_INVALID}）"
                )
                return (
                    DeliverySource(
                        run_id=inputs.run_id,
                        state=STATE_SNAPSHOT_FAILED,
                        before_revision_id=before_id,
                        after_revision_id=None,
                        source_fingerprint=current_fp,
                        changed_files=changed_files,
                        snapshot_status=SNAPSHOT_REJECTED,
                        error_code=ERR_EXISTING_AFTER_INVALID,
                    ),
                    warnings,
                )
            source_fingerprint = snap_fp or current_fp
            snapshot_status = SNAPSHOT_SAVED
            _register_protections(project, inputs, after_id)
            return (
                DeliverySource(
                    run_id=inputs.run_id,
                    state=STATE_VERIFIED_CHANGE,
                    before_revision_id=before_id,
                    after_revision_id=after_id,
                    source_fingerprint=source_fingerprint,
                    changed_files=changed_files,
                    snapshot_status=snapshot_status,
                    error_code=None,
                ),
                warnings,
            )

    # 创建 after（parent = 实际 before）
    after_id, fingerprint, create_err = _create_after_revision(
        inputs,
        parent_revision_id=before_id,
        warnings=warnings,
    )
    if create_err or not after_id:
        return (
            DeliverySource(
                run_id=inputs.run_id,
                state=STATE_SNAPSHOT_FAILED,
                before_revision_id=before_id,
                after_revision_id=None,
                source_fingerprint=fingerprint or current_fp,
                changed_files=changed_files,
                snapshot_status=SNAPSHOT_FAILED,
                error_code=create_err or ERR_SNAPSHOT_WRITE_FAILED,
            ),
            warnings,
        )

    # 指纹必须对应验证后源
    expected_fp = inputs.verified_source_fingerprint or current_fp
    if expected_fp and fingerprint and not fingerprints_equal(expected_fp, fingerprint):
        warnings.append(
            "after 指纹与验证后源不一致，拒绝宣称 verified_change"
        )
        return (
            DeliverySource(
                run_id=inputs.run_id,
                state=STATE_SNAPSHOT_FAILED,
                before_revision_id=before_id,
                after_revision_id=None,
                source_fingerprint=fingerprint,
                changed_files=changed_files,
                snapshot_status=SNAPSHOT_REJECTED,
                error_code=ERR_AFTER_FINGERPRINT_MISMATCH,
            ),
            warnings,
        )

    return (
        DeliverySource(
            run_id=inputs.run_id,
            state=STATE_VERIFIED_CHANGE,
            before_revision_id=before_id,
            after_revision_id=after_id,
            source_fingerprint=fingerprint or current_fp,
            changed_files=changed_files,
            snapshot_status=SNAPSHOT_SAVED,
            error_code=None,
        ),
        warnings,
    )


def apply_delivery_source_to_result(
    result: Any,
    delivery_source: DeliverySource,
    warnings: list[str] | None = None,
) -> Any:
    """把 delivery_source 写回 TaskResult；快照失败时不宣称完整成功。"""
    metadata = dict(getattr(result, "metadata", None) or {})
    metadata["delivery_source"] = delivery_source.to_dict()
    if warnings:
        existing = list(getattr(result, "revision_warnings", None) or [])
        for w in warnings:
            if w not in existing:
                existing.append(w)
        try:
            result.revision_warnings = existing
        except Exception:
            pass
        text = getattr(result, "plain_text", "") or ""
        note = "**交付版本提示：**\n" + "\n".join(f"- {w}" for w in warnings)
        if text:
            result.plain_text = f"{text}\n\n{note}"
        else:
            result.plain_text = note
    result.metadata = metadata

    # 快照失败：TaskResult 不宣称完整成功（保留验证子结果与可恢复工作源）。
    # 例外：项目本就未落盘为 HSF（not_revisionable）——如实标注 delivery_source，
    # 但不反转 handler 的交付判定（CREATE 内存生成 / 未 save 的测试路径）。
    if delivery_source.state == STATE_SNAPSHOT_FAILED:
        if delivery_source.error_code == ERR_NOT_REVISIONABLE:
            return result
        try:
            if getattr(result, "success", False):
                result.success = False
            if not getattr(result, "error", None):
                result.error = delivery_source.error_code or "delivery_snapshot_failed"
        except Exception:
            pass
    # partial_change / failed_no_change 也不得宣称 verified 成功
    if delivery_source.state in (STATE_PARTIAL_CHANGE, STATE_FAILED_NO_CHANGE):
        try:
            if getattr(result, "success", False) and delivery_source.changed_files:
                result.success = False
        except Exception:
            pass
    return result


def delivery_source_from_result(result: Any) -> Optional[DeliverySource]:
    metadata = getattr(result, "metadata", None) or {}
    return DeliverySource.from_dict(metadata.get("delivery_source"))
