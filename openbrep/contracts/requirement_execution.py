"""Requirement-scoped dispatch over checks already produced for one source snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from openbrep.contracts.bindings import canonical_hash, get_executor_spec
from openbrep.contracts.object_spec import Requirement
from openbrep.verification import CheckResult

Executor = Callable[[dict[str, Any], dict[str, Any]], CheckResult]


def check_evidence_key(check_id: str, params: dict[str, Any] | None = None) -> str:
    """Evidence key includes executor arguments so unlike scopes cannot alias."""
    return f"{check_id}#{canonical_hash(params or {})}"


@dataclass
class RequirementEvaluation:
    status: str
    results: list[CheckResult] = field(default_factory=list)
    required_total: int = 0
    required_passed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "required_total": self.required_total,
            "required_passed": self.required_passed,
            "results": [result.to_dict() for result in self.results],
        }


def _result_from_context(check_id: str, context: dict[str, Any], params: dict[str, Any]) -> CheckResult:
    """Exact-ID adapter: params may select a named result but never fall back to another check."""
    checks = context.get("checks", {})
    supplied = checks.get(check_evidence_key(check_id, params))
    if supplied is None and not params:
        supplied = checks.get(check_id)
    if isinstance(supplied, CheckResult):
        return supplied
    if isinstance(supplied, dict):
        return CheckResult(
            check_id=check_id,
            status=str(supplied.get("status", "unverified")),
            reason=str(supplied.get("reason", "")),
            coverage=str(supplied.get("coverage", "")),
            evidence_refs=list(supplied.get("evidence_refs", [])),
            stale=bool(supplied.get("stale", False)),
        )
    return CheckResult(
        check_id=check_id,
        status="not_run",
        reason="本轮没有该检查的执行结果",
        coverage=f"params={params!r}",
    )


_EXECUTORS: dict[str, Executor] = {
    key: (lambda context, params, check_id=key: _result_from_context(check_id, context, params))
    for key in ("compile", "static", "lint", "semantic", "plan_check", "project_contract", "effect_contract", "reserved_param_semantic_bug")
}


def register_requirement_executor(check_id: str, executor: Executor) -> None:
    """Register trusted Python code; domain data cannot install executables."""
    if get_executor_spec(check_id) is None:
        raise ValueError(f"检查器未声明能力规格: {check_id}")
    if not callable(executor):
        raise TypeError("executor 必须可调用")
    _EXECUTORS[check_id] = executor


def execute_requirements(
    requirements: list[Requirement],
    context: dict[str, Any],
) -> RequirementEvaluation:
    """Evaluate each requirement independently against existing evidence.

    Evidence is never inferred from another check ID. Missing executors, missing
    results and stale source bindings remain non-passing statuses.
    """
    results: list[CheckResult] = []
    required_total = 0
    required_passed = 0
    for requirement in requirements:
        if requirement.strength == "required":
            required_total += 1
        check_id = requirement.check_id
        spec = get_executor_spec(check_id) if check_id else None
        executor = _EXECUTORS.get(check_id or "")
        if spec is None or executor is None:
            result = CheckResult(
                check_id=check_id or "unbound",
                status="unverified",
                reason="没有可调用的受信任检查执行器",
                coverage="requirement 未验证",
                stale=bool(context.get("source_stale", False)),
            )
        else:
            try:
                result = executor(context, dict(requirement.params))
            except Exception as exc:
                result = CheckResult(check_id=check_id, status="degraded", reason=f"检查执行异常：{exc}")
        result.requirement_id = requirement.requirement_id
        result.executor_id = check_id or ""
        result.source_fingerprint = str(context.get("source_fingerprint", ""))
        result.requirement_source = requirement.source
        if context.get("source_stale"):
            result.stale = True
            if result.status == "pass":
                result.status = "unverified"
                result.reason = "检查结果对应的源码已变化"
        if requirement.strength == "required" and result.status == "pass" and not result.stale:
            required_passed += 1
        results.append(result)

    if required_total == 0:
        status = "not_applicable" if not requirements else "advisory_only"
    elif required_passed == required_total:
        status = "passed"
    elif any(r.stale for r in results if r.requirement_id):
        status = "stale"
    elif any(r.status in {"fail", "degraded"} for r in results if r.requirement_id):
        status = "failed"
    else:
        status = "incomplete"
    return RequirementEvaluation(status, results, required_total, required_passed)
