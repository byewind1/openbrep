"""QualityRecord schema（设计稿 v2 §3.2 首版，schema_version=1）。

形状固定、向后兼容只加字段；``validate()`` 只做硬校验（版本/主键/枚举/
隐私上限），不做任何评分。三轴（delivery / artifact_quality / execution_cost）
的内部结构由 evaluator 产出，本模块只保证顶层契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

# 终态枚举（AC-G0-2）：所有终态都进记录，不许只记成功。
OUTCOMES: tuple[str, ...] = (
    "completed",            # 交付且验证门禁通过
    "gate_fail",            # 交付了但验证/编译门禁未过
    "timeout",              # 执行超时（如 codex turn timeout）
    "budget_exhausted",     # 工具预算耗尽强制收尾
    "cancelled",            # 用户取消
    "infrastructure_error", # 基础设施失败（异常早退 / provider 不可用 / 编译器缺失）
    "not_evaluable",        # 无可评估交付物（CHAT / 确认门等待 / 零证据失败）
)

# 隐私纪律（AC-G1-6）：用户指令只存截断摘要
INSTRUCTION_MAX_CHARS = 120


@dataclass
class QualityRecord:
    """一次任务的不可变质量档案（observer-only，无综合分）。"""

    run_id: str
    intent: str
    outcome: str
    project_ref: dict = field(default_factory=dict)   # {"path_hash": sha256[:12], "name": 目录名}
    instruction_summary: str = ""                     # 用户指令截断摘要（≤120 字符）
    ts: str = ""                                      # ISO 时间戳（记录生成时刻）
    schema_version: int = SCHEMA_VERSION
    delivery: dict = field(default_factory=dict)      # compile/static/semantic 三件套
    artifact_quality: dict = field(default_factory=dict)  # requirements/parametricity/...
    execution_cost: dict = field(default_factory=dict)    # llm_calls/tool_calls/...
    provenance: dict = field(default_factory=dict)    # commit/model_route/revisions/...

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "ts": self.ts,
            "project_ref": dict(self.project_ref),
            "intent": self.intent,
            "instruction_summary": self.instruction_summary,
            "outcome": self.outcome,
            "delivery": self.delivery,
            "artifact_quality": self.artifact_quality,
            "execution_cost": self.execution_cost,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityRecord":
        record = cls(
            run_id=str(data.get("run_id") or ""),
            intent=str(data.get("intent") or ""),
            outcome=str(data.get("outcome") or ""),
            project_ref=dict(data.get("project_ref") or {}),
            instruction_summary=str(data.get("instruction_summary") or ""),
            ts=str(data.get("ts") or ""),
            schema_version=int(data.get("schema_version") or 0),
            delivery=dict(data.get("delivery") or {}),
            artifact_quality=dict(data.get("artifact_quality") or {}),
            execution_cost=dict(data.get("execution_cost") or {}),
            provenance=dict(data.get("provenance") or {}),
        )
        record.validate()
        return record

    def validate(self) -> None:
        """硬校验：违反即 ValueError（store 写盘前调用，保证档案可解析）。"""
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")
        if not self.project_ref.get("path_hash") or not self.project_ref.get("name"):
            raise ValueError("project_ref requires path_hash and name")
        if len(self.instruction_summary) > INSTRUCTION_MAX_CHARS:
            raise ValueError("instruction_summary exceeds privacy limit")
