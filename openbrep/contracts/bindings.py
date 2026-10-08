"""U03-B 检查能力注册与两类版本绑定（GuardBinding / EvidenceBinding）。

总则 §3 合同（本卡是唯一责任卡，schema 版本 1）：

- **GuardBinding**（执行授权）：source 指纹 / 草稿 / 项目 epoch / requirement
  范围 / Plan hash——"这次执行被授权改什么、基于哪个源状态"。
- **EvidenceBinding**（结果有效性）：在 Guard 之上叠加**结果事实**——GSM
  hash、有效参数回读 hash、依赖清单 hash、场景引用、执行器、渲染上下文、
  evidence_kind。**不是万能 hash**：各维度独立记录、独立失效，canonical
  hash 只做"同一维度内容的稳定指纹"。

canonical hash 规范（验收：字段顺序变化不无故失效）：
- dict 键递归排序（顺序不敏感）；
- 浮点统一 %%.12g（1.0 与 1 同形、-0.0 归 0.0）；
- None 与 "" 保持区分（语义不同不归一）。

freshness 合同（验收口径）：
- 仅 source 变化 → 所有证据 stale（授权基础变了）；
- 材质/图像/场景变化 → 仅 visual/host 类证据 stale（compile/static 不受影响）；
- 错 GSM → 依赖 GSM 的证据 stale（static/lint 不受影响）；
- **本地预览不伪装宿主证据**：requirement 要 host 证据时，local_preview 一律
  不满足——kind 门与 fresh/stale 无关。

读适配（只扩展读取，不新增生产存储事务）：host_verification_service 的
record → EvidenceBinding；楼梯合同（stair.json / StairContractReport）→
contract 维度证据（兼容，不迁移）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

SCHEMA_VERSION = 1

# ── canonical hash ───────────────────────────────────────────


def _canonicalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)  # NaN/Inf 以显式字符串参与（不静默归一）
        if value == 0.0:
            return 0.0  # -0.0 → 0.0
        text = f"{value:.12g}"
        return float(text) if "." in text or "e" in text or "E" in text else int(float(text))
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonicalize(v) for v in value), key=repr)
    return str(value)


def canonical_hash(data: Any) -> str:
    """稳定内容指纹：键序不敏感、浮点 12 位有效规范。"""
    payload = json.dumps(_canonicalize(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── 执行器注册（升级 U03-A 的平面注册表，保持签名兼容）────────


@dataclass(frozen=True)
class CheckExecutorSpec:
    """注册的检查执行器：check_id + 可执行能力前提 + 返回类型。"""

    check_id: str
    required_bindings: frozenset[str]   # 执行前必须成立的 Guard/Evidence 维度
    result_kind: str                    # check_result | contract_report | host_record | ...


# 内置执行器（与 U03-A BUILTIN_CHECK_EXECUTORS 同集 + 能力前提/返回类型）
_BUILTIN_EXECUTORS: dict[str, CheckExecutorSpec] = {
    spec.check_id: spec
    for spec in (
        CheckExecutorSpec("compile", frozenset({"source"}), "check_result"),
        CheckExecutorSpec("static", frozenset({"source"}), "check_result"),
        CheckExecutorSpec("lint", frozenset({"source"}), "check_result"),
        CheckExecutorSpec("semantic", frozenset({"source", "params"}), "check_result"),
        CheckExecutorSpec("plan_check", frozenset({"source"}), "check_result"),
        CheckExecutorSpec("project_contract", frozenset({"source"}), "contract_report"),
        CheckExecutorSpec("effect_contract", frozenset({"source", "params"}), "check_result"),
        CheckExecutorSpec("reserved_param_semantic_bug", frozenset({"source"}), "check_result"),
    )
}

_executor_specs: dict[str, CheckExecutorSpec] = dict(_BUILTIN_EXECUTORS)

# 内置 check_id 集（U03-A 平面合同引用）
BUILTIN_CHECK_EXECUTORS: frozenset[str] = frozenset(_BUILTIN_EXECUTORS)


def register_executor_spec(
    check_id: str, *, required_bindings: frozenset[str] | None = None, result_kind: str = "check_result"
) -> bool:
    """注册/升级执行器规格（U03-B 扩展入口）；重复同规格幂等返回 False。"""
    check_id = str(check_id or "").strip()
    if not check_id:
        return False
    spec = CheckExecutorSpec(
        check_id=check_id,
        required_bindings=frozenset(required_bindings or {"source"}),
        result_kind=result_kind,
    )
    existing = _executor_specs.get(check_id)
    if existing == spec:
        return False
    _executor_specs[check_id] = spec
    return True


def get_executor_spec(check_id: str) -> Optional[CheckExecutorSpec]:
    return _executor_specs.get(str(check_id or "").strip())


def list_executor_specs() -> tuple[CheckExecutorSpec, ...]:
    """Return a stable snapshot of trusted registered check capabilities."""
    return tuple(_executor_specs[key] for key in sorted(_executor_specs))


def reset_executor_specs_for_tests() -> None:
    _executor_specs.clear()
    _executor_specs.update(_BUILTIN_EXECUTORS)


# U03-A 兼容：平面注册表委托到规格注册表
def known_check_executor(check_id: str) -> bool:
    return str(check_id or "").strip() in _executor_specs


def register_check_executor(check_id: str) -> bool:
    """U03-A 签名兼容入口（缺省能力前提 = {source}，返回 check_result）。"""
    return register_executor_spec(check_id)


# ── GuardBinding（执行授权）──────────────────────────────────


@dataclass
class GuardBinding:
    """执行授权绑定：source/草稿/项目/requirement 范围/Plan。"""

    source_fingerprint: str             # 授权时的源指纹（source_fingerprint.compute）
    project_epoch: Optional[int] = None
    draft_id: str = ""                  # 草稿/批次标识（空 = 无草稿）
    requirement_ids: list[str] = field(default_factory=list)   # 授权的 spec 需求范围
    plan_hash: str = ""                 # 授权的 ExecutionPlan hash（空 = 未按计划授权）
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_fingerprint": self.source_fingerprint,
            "project_epoch": self.project_epoch,
            "draft_id": self.draft_id,
            "requirement_ids": list(self.requirement_ids),
            "plan_hash": self.plan_hash,
        }

    def binding_hash(self) -> str:
        return canonical_hash(self.to_dict())


# ── EvidenceBinding（结果有效性）─────────────────────────────

# 证据类别 → 有效性敏感维度（验收：材质/场景只影响该影响的证据）
EVIDENCE_KIND_DIMENSIONS: dict[str, frozenset[str]] = {
    "compile": frozenset({"source", "gsm"}),
    "static": frozenset({"source"}),
    "lint": frozenset({"source"}),
    "semantic": frozenset({"source", "params"}),
    "visual": frozenset({"source", "gsm", "params", "scene", "renderer"}),
    "host": frozenset({"source", "gsm", "params", "deps", "scene"}),
    "contract": frozenset({"source", "params"}),
}

VALID_EVIDENCE_KINDS = frozenset(EVIDENCE_KIND_DIMENSIONS)


@dataclass
class EvidenceBinding:
    """结果有效性绑定：Guard + 结果事实（各维度独立、非万能 hash）。"""

    guard: GuardBinding
    evidence_kind: str                  # VALID_EVIDENCE_KINDS 之一
    executor_id: str                    # 产出证据的执行器 check_id
    gsm_fingerprint: str = ""           # 证据对应的 GSM 产物 hash（空=未涉）
    parameter_values_hash: str = ""     # 有效参数回读 hash
    dependencies_hash: str = ""         # 依赖清单（library-parts）hash
    scene_refs: list[str] = field(default_factory=list)   # 场景/宿主证据引用
    renderer_context: dict = field(default_factory=dict)  # 引擎/视口等渲染上下文
    contract_hash: str = ""             # 领域合同文件 hash（stair.json 等）
    result_facts: dict = field(default_factory=dict)      # 执行器返回事实（脱敏负载）
    produced_at: str = ""               # 产出时间（ISO）
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "guard": self.guard.to_dict(),
            "evidence_kind": self.evidence_kind,
            "executor_id": self.executor_id,
            "gsm_fingerprint": self.gsm_fingerprint,
            "parameter_values_hash": self.parameter_values_hash,
            "dependencies_hash": self.dependencies_hash,
            "scene_refs": list(self.scene_refs),
            "renderer_context": dict(self.renderer_context),
            "contract_hash": self.contract_hash,
            "result_facts": dict(self.result_facts),
            "produced_at": self.produced_at,
        }

    def binding_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass
class FreshnessReport:
    """fresh/stale 判定：逐维度原因，不合并成单一布尔以外的黑盒。"""

    fresh: bool
    stale_reasons: list[str] = field(default_factory=list)   # source_changed/gsm_changed/...
    not_valid_for: str = ""             # kind 门不满足时的目标 kind（如 host）

    def to_dict(self) -> dict[str, Any]:
        return {
            "fresh": self.fresh,
            "stale_reasons": list(self.stale_reasons),
            "not_valid_for": self.not_valid_for,
        }


def evaluate_evidence_freshness(
    binding: EvidenceBinding,
    *,
    current_source_fingerprint: str = "",
    current_gsm_fingerprint: str = "",
    current_parameter_values_hash: str = "",
    current_dependencies_hash: str = "",
    current_contract_hash: str = "",
    current_scene_refs: Optional[list[str]] = None,
    current_renderer_context: Optional[dict] = None,
    current_project_epoch: Optional[int] = None,
    current_plan_hash: str = "",
) -> FreshnessReport:
    """已有结果 → fresh/stale 判定（只读；不落盘、不改状态）。

    - Guard 维度（授权）失效 → 一律 stale；
    - Evidence 维度只在证据类别敏感集内的才参与（材质变化不影响 compile）；
    - 当前值留空 = 未提供该维度 → 该维度跳过（不算 stale，不假装核对）。
    """
    reasons: list[str] = []
    guard = binding.guard
    if current_source_fingerprint and guard.source_fingerprint \
            and current_source_fingerprint != guard.source_fingerprint:
        reasons.append("source_changed")
    if current_project_epoch is not None and guard.project_epoch is not None \
            and current_project_epoch != guard.project_epoch:
        reasons.append("epoch_changed")
    if current_plan_hash and guard.plan_hash and current_plan_hash != guard.plan_hash:
        reasons.append("plan_changed")

    sensitive = EVIDENCE_KIND_DIMENSIONS.get(binding.evidence_kind, frozenset({"source"}))
    if "gsm" in sensitive and current_gsm_fingerprint and binding.gsm_fingerprint \
            and current_gsm_fingerprint != binding.gsm_fingerprint:
        reasons.append("gsm_changed")
    if "params" in sensitive and current_parameter_values_hash and binding.parameter_values_hash \
            and current_parameter_values_hash != binding.parameter_values_hash:
        reasons.append("params_changed")
    if "deps" in sensitive and current_dependencies_hash and binding.dependencies_hash \
            and current_dependencies_hash != binding.dependencies_hash:
        reasons.append("deps_changed")
    if "scene" in sensitive and current_scene_refs is not None and binding.scene_refs \
            and sorted(current_scene_refs) != sorted(binding.scene_refs):
        reasons.append("scene_changed")
    if "renderer" in sensitive and current_renderer_context is not None and binding.renderer_context \
            and canonical_hash(current_renderer_context) != canonical_hash(binding.renderer_context):
        reasons.append("renderer_changed")
    if binding.evidence_kind == "contract" and current_contract_hash and binding.contract_hash \
            and current_contract_hash != binding.contract_hash:
        reasons.append("contract_changed")
    return FreshnessReport(fresh=not reasons, stale_reasons=reasons)


def requirement_satisfied_by_evidence(requirement_kind: str, evidence: EvidenceBinding) -> FreshnessReport:
    """kind 门：本地预览不得伪装宿主证据（与 fresh/stale 无关）。"""
    target = requirement_kind.strip() if requirement_kind in VALID_EVIDENCE_KINDS else ""
    if not target:
        return FreshnessReport(fresh=False, stale_reasons=[], not_valid_for=requirement_kind)
    if evidence.evidence_kind != target:
        return FreshnessReport(
            fresh=False,
            stale_reasons=[f"evidence_kind_mismatch:{evidence.evidence_kind}"],
            not_valid_for=target,
        )
    return FreshnessReport(fresh=True)


# ── 读适配（不新增生产存储事务）──────────────────────────────


def evidence_binding_from_host_record(record: Mapping[str, Any], *, guard: GuardBinding) -> EvidenceBinding:
    """host_verification_service 的 record → EvidenceBinding（读适配）。

    只读取已知字段；record 形状演进时未知字段进 result_facts（不丢弃）。
    """
    record = dict(record or {})
    known = {
        "source_fingerprint_before_compile", "source_fingerprint_after_compile",
        "gsm_sha256", "status", "identity_status", "evidence_save_status",
        "library_restore_status", "record_id", "run_id", "finished_at",
    }
    result_facts = {k: v for k, v in record.items() if k not in known}
    status = str(record.get("status") or "")
    return EvidenceBinding(
        guard=guard,
        evidence_kind="host",
        executor_id="host_run",
        gsm_fingerprint=str(record.get("gsm_sha256") or ""),
        result_facts={
            "status": status,
            "identity_status": str(record.get("identity_status") or ""),
            "evidence_save_status": str(record.get("evidence_save_status") or ""),
            **result_facts,
        },
        produced_at=str(record.get("finished_at") or ""),
    )


def evidence_binding_from_stair_contract(
    contract_hash: str, *, guard: GuardBinding, report_facts: Optional[dict] = None
) -> EvidenceBinding:
    """楼梯合同证据 → EvidenceBinding（兼容适配；不迁移 stair 合同）。"""
    return EvidenceBinding(
        guard=guard,
        evidence_kind="contract",
        executor_id="project_contract",
        contract_hash=contract_hash,
        result_facts=dict(report_facts or {}),
    )
