"""D9 Codex Auto routing policy, evidence lineage D8 (2026-08) -> D13 (2026-09-03).

D8 established the initial table (Luna/low everywhere, Terra/medium as the
simple-only fallback) and the bounded single-escalation contract.  D13
(bake-off v2) added measured evidence for strong tiers: Luna/low scored 0/6
on complex CREATE while Luna/high scored 2/6 (C10 2/2), so complex tasks now
start at Luna/high; Terra/high (2/6, lower quota cost than Sol/high) is the
complex escalation target.  Sol/high stays out of the table: same tier as
Terra/high but more expensive.  The escalation chain itself (fail, switch
model, rerun) still has zero measured samples -- reasons must not claim it
is verified.

The policy is deliberately pure: UI and pipeline code pass it the current
account catalog/status and receive an explainable decision.  No provider,
filesystem, or configuration side effects live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from openbrep.codex.redact import redact_secrets

LUNA_MODEL = "openai-codex/gpt-5.6-luna"
TERRA_MODEL = "openai-codex/gpt-5.6-terra"

_LOCALIZED_CHECK_TYPES = frozenset(
    {
        "semantic",
        "static",
        "plan_check",
        "contract",
        "reserved_param_semantic_bug",
    }
)


class AutoRouteResult(Protocol):
    """Small structural contract kept independent from runtime.pipeline."""

    success: bool
    verification: Mapping[str, Any] | None
    metadata: dict[str, Any]
    plain_text: str


def classify_create_complexity(instruction: str) -> str:
    """Reuse Preflight complexity while removing CREATE's non-discriminating +2.

    ``PreflightAnalyzer`` adds two points whenever source XML is absent.  Every
    D9 request is CREATE, so retaining that constant would make ``simple``
    unreachable and silently disable D8's Terra fallback.  A minimal valid
    placeholder keeps the established keyword/section heuristic intact while
    measuring only differences between CREATE instructions.
    """

    from openbrep.preflight import PreflightAnalyzer

    return PreflightAnalyzer().analyze(instruction, xml_content="<Object/>").complexity


@dataclass(frozen=True)
class CodexRouteDecision:
    """One auditable routing decision."""

    ok: bool
    model: str = ""
    reasoning_effort: str = ""
    reason: str = ""
    code: str = ""
    error: str = ""
    complexity: str = ""
    escalation: bool = False
    untested_escalation: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "mode": "auto",
            "complexity": self.complexity,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "reason": self.reason,
            "escalation": self.escalation,
            "untested_escalation": self.untested_escalation,
            **({"code": self.code} if self.code else {}),
        }


def choose_initial_route(
    complexity: str,
    catalog: Iterable[Mapping[str, Any]],
    status: Mapping[str, Any],
) -> CodexRouteDecision:
    """Choose the D8/D13-backed initial route, or fail closed.

    D13 (evidence lineage D8 -> D13): complex CREATE starts at Luna/high
    (D8's Luna/low scored 0/6 on complex; D13 Luna/high 2/6 with C10 2/2).
    Simple/medium keep the D8 Luna/low primary.  Terra/medium is only a
    simple-task fallback when Luna is unavailable.  Sol stays out of the
    table: D13 measured Sol/high at the same 2/6 tier as Terra/high but at
    higher quota cost.  No other model may appear.
    """

    normalized_complexity = _normalize_complexity(complexity)
    if status.get("state") == "quota_exhausted":
        return _stop(
            normalized_complexity,
            "quota_exhausted",
            "ChatGPT 订阅额度已耗尽或达到用量上限，Auto 路由已停止。",
            "quota_exhausted",
        )
    if not status.get("connected"):
        return _stop(
            normalized_complexity,
            "not_signed_in",
            "尚未连接 ChatGPT，Auto 路由已停止。",
            "not_signed_in",
        )

    indexed = _catalog_index(catalog)
    if normalized_complexity == "complex" and _supports(indexed.get(LUNA_MODEL), "high"):
        return CodexRouteDecision(
            ok=True,
            model=LUNA_MODEL,
            reasoning_effort="high",
            reason=(
                "D13 complex CREATE primary: Luna high "
                "(D8 Luna low 0/6 on complex; D13 Luna high 2/6)"
            ),
            complexity=normalized_complexity,
        )

    if _supports(indexed.get(LUNA_MODEL), "low"):
        reason = (
            "D8 simple/medium CREATE primary: Luna low"
            if normalized_complexity in {"simple", "medium"}
            else "Luna high absent from catalog; complex CREATE degrades to D8 Luna low (explicit)"
        )
        return CodexRouteDecision(
            ok=True,
            model=LUNA_MODEL,
            reasoning_effort="low",
            reason=reason,
            complexity=normalized_complexity,
        )

    if normalized_complexity == "simple" and _supports(indexed.get(TERRA_MODEL), "medium"):
        return CodexRouteDecision(
            ok=True,
            model=TERRA_MODEL,
            reasoning_effort="medium",
            reason="D8 simple CREATE fallback: Luna low unavailable; using Terra medium",
            complexity=normalized_complexity,
        )

    return _stop(
        normalized_complexity,
        "auto_route_unavailable",
        "当前账户目录没有 D8/D13 已验证且支持所需 effort 的 Auto 路由组合，任务已停止。",
        "no D8/D13-backed model/effort combination is available",
    )


def choose_escalation(
    initial: CodexRouteDecision,
    verification: Mapping[str, Any] | None,
    catalog: Iterable[Mapping[str, Any]],
    *,
    attempts: int,
) -> CodexRouteDecision:
    """Return the sole allowed escalation, once.

    Escalation paths (evidence lineage D8 -> D13):
    - Luna/low -> Luna/high (any complexity).  D13 measured Luna/high on
      complex CREATE (2/6, C10 2/2) and MODIFY (6/6), so this is no longer
      an untested slot.
    - Luna/high -> Terra/high (complex only).  D13 measured Terra/high at
      2/6; Sol/high scored the same tier but costs more quota, so Sol is
      the documented same-tier candidate that lost on price and stays out.

    Both targets are D13-measured models, but the escalation chain itself
    (fail, switch model, rerun) still has zero measured samples -- the
    reason text says so and must not claim the chain is verified.

    Escalation needs at least one failed, named semantic/static/contract
    check and the target model/effort present in the account catalog;
    anything else fails closed.
    """

    complexity = initial.complexity
    if attempts >= 1:
        return _stop(
            complexity,
            "auto_escalation_exhausted",
            "Auto 路由已达到一次升级上限，任务已停止。",
            "bounded escalation limit reached",
        )

    # Sole allowed escalation sources.  Sol/high was a same-tier complex
    # candidate in D13 but is excluded for higher quota cost.
    if initial.model == LUNA_MODEL and initial.reasoning_effort == "low":
        target_model, target_effort = LUNA_MODEL, "high"
    elif (
        initial.model == LUNA_MODEL
        and initial.reasoning_effort == "high"
        and complexity == "complex"
    ):
        target_model, target_effort = TERRA_MODEL, "high"
    else:
        return _stop(
            complexity,
            "auto_escalation_not_allowed",
            "当前路由不在允许的升级路径中（Luna low → Luna high，"
            "或 complex 的 Luna high → Terra high），任务已停止。",
            "route is outside the allowed D8/D13 escalation paths",
        )

    check_name = _localized_failure_name(verification)
    if not check_name:
        return _stop(
            complexity,
            "auto_failure_not_localized",
            "验证失败但没有可定位的检查项，Auto 不会盲目升级。",
            "verification failure has no named semantic/static/contract check",
        )
    indexed = _catalog_index(catalog)
    if not _supports(indexed.get(target_model), target_effort):
        return _stop(
            complexity,
            "auto_escalation_unsupported",
            f"当前账户目录不支持升级目标 {target_model} @ {target_effort}，Auto 升级已停止。",
            f"escalation target {target_model} @ {target_effort} is absent from the catalog",
        )
    return CodexRouteDecision(
        ok=True,
        model=target_model,
        reasoning_effort=target_effort,
        reason=(
            f"localized verification failure [{check_name}]; D13-measured "
            f"{target_model} @ {target_effort} escalation "
            "(target measured; escalation chain itself still zero-sample)"
        ),
        complexity=complexity,
        escalation=True,
        untested_escalation=False,
    )


def run_auto_route(
    *,
    complexity: str,
    catalog: Iterable[Mapping[str, Any]],
    status: Mapping[str, Any],
    run: Callable[[CodexRouteDecision], AutoRouteResult],
    make_stop_result: Callable[[CodexRouteDecision], AutoRouteResult],
    on_event: Callable[[str, dict[str, Any]], None],
    should_cancel: Callable[[], bool] | None = None,
) -> AutoRouteResult:
    """Execute the D8/D13 policy with one bounded, visible escalation at most."""

    catalog_snapshot = [dict(item) for item in catalog if isinstance(item, Mapping)]
    initial = choose_initial_route(complexity, catalog_snapshot, status)
    decisions = [initial.to_metadata()]
    if not initial.ok:
        result = make_stop_result(initial)
        _attach_metadata(result, decisions, stopped=initial)
        _emit_stop(on_event, initial)
        return result

    _emit_decision(on_event, initial)
    first = run(initial)
    _attach_metadata(first, decisions)
    if first.success:
        return first

    if should_cancel is not None and should_cancel():
        first.metadata["codex_auto_route"]["cancelled_before_escalation"] = True
        on_event(
            "status",
            {"stage": "cancel", "message": "Auto 升级已取消；未启动新的 Codex turn。"},
        )
        return first

    escalation = choose_escalation(initial, first.verification, catalog_snapshot, attempts=0)
    decisions.append(escalation.to_metadata())
    if not escalation.ok:
        _attach_metadata(first, decisions, stopped=escalation)
        first.plain_text = _append_stop(first.plain_text, escalation.error)
        _emit_stop(on_event, escalation)
        return first

    _emit_decision(on_event, escalation)
    second = run(escalation)
    _attach_metadata(second, decisions)
    if second.success:
        return second
    if should_cancel is not None and should_cancel():
        second.metadata["codex_auto_route"]["cancelled_during_escalation"] = True
        on_event(
            "status",
            {"stage": "cancel", "message": "Auto 升级已取消；Codex turn 已清理。"},
        )
        return second

    exhausted = choose_escalation(
        escalation,
        second.verification,
        catalog_snapshot,
        attempts=1,
    )
    decisions.append(exhausted.to_metadata())
    _attach_metadata(second, decisions, stopped=exhausted)
    second.plain_text = _append_stop(second.plain_text, exhausted.error)
    _emit_stop(on_event, exhausted)
    return second


def _localized_failure_name(verification: Mapping[str, Any] | None) -> str:
    if not isinstance(verification, Mapping):
        return ""
    for check in verification.get("checks") or []:
        if not isinstance(check, Mapping):
            continue
        if check.get("status") != "fail" or check.get("check_type") not in _LOCALIZED_CHECK_TYPES:
            continue
        name = str(check.get("name") or "").strip()
        if name:
            # Check names can originate in model-authored plan text.  Keep the
            # requested concrete check name, but never turn route metadata/UI
            # into a secret-reflection path.
            return redact_secrets(name)[:160]
    return ""


def _catalog_index(catalog: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in catalog:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            result[model_id] = item
    return result


def _supports(item: Mapping[str, Any] | None, effort: str) -> bool:
    if item is None:
        return False
    supported = {
        str(entry.get("effort") or "").strip()
        for entry in item.get("supported_reasoning_efforts") or []
        if isinstance(entry, Mapping)
    }
    return effort in supported


def _normalize_complexity(value: str) -> str:
    return value if value in {"simple", "medium", "complex"} else "complex"


def _stop(
    complexity: str,
    code: str,
    error: str,
    reason: str,
) -> CodexRouteDecision:
    return CodexRouteDecision(
        ok=False,
        reason=reason,
        code=code,
        error=error,
        complexity=complexity,
    )


def _emit_decision(
    on_event: Callable[[str, dict[str, Any]], None],
    decision: CodexRouteDecision,
) -> None:
    payload = decision.to_metadata()
    on_event("codex_route", payload)
    on_event(
        "status",
        {
            "stage": "retry" if decision.escalation else "plan",
            "message": (
                f"Auto 路由：{decision.model} @ {decision.reasoning_effort}"
                f"（{decision.reason}）"
            ),
        },
    )


def _emit_stop(
    on_event: Callable[[str, dict[str, Any]], None],
    decision: CodexRouteDecision,
) -> None:
    on_event("status", {"stage": "budget", "message": decision.error})


def _attach_metadata(
    result: AutoRouteResult,
    decisions: list[dict[str, Any]],
    *,
    stopped: CodexRouteDecision | None = None,
) -> None:
    metadata = dict(result.metadata or {})
    route: dict[str, Any] = {
        "mode": "auto",
        "decisions": [dict(item) for item in decisions],
    }
    if stopped is not None:
        route["stopped"] = {"code": stopped.code, "reason": stopped.reason}
    metadata["codex_auto_route"] = route
    result.metadata = metadata


def _append_stop(text: str, error: str) -> str:
    notice = f"Auto 路由停止：{error}"
    return f"{text}\n\n{notice}" if text else notice
