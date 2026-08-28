"""D9 Codex Auto routing policy derived exclusively from the D8 bake-off.

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
    """Choose the D8-backed initial route, or fail closed.

    D8 permits Luna/low for every CREATE complexity. Terra/medium is only a
    simple-task fallback when Luna is unavailable. No other model may appear.
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
    if _supports(indexed.get(LUNA_MODEL), "low"):
        reason = (
            "D8 simple/medium CREATE primary: Luna low"
            if normalized_complexity in {"simple", "medium"}
            else "D8 complex CREATE has no primary; bounded exploration starts at Luna low"
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
        "当前账户目录没有 D8 已验证且支持所需 effort 的 Auto 路由组合，任务已停止。",
        "no D8-backed model/effort combination is available",
    )


def choose_escalation(
    initial: CodexRouteDecision,
    verification: Mapping[str, Any] | None,
    catalog: Iterable[Mapping[str, Any]],
    *,
    attempts: int,
) -> CodexRouteDecision:
    """Return the sole allowed escalation: Luna low -> Luna high, once.

    Escalation needs at least one failed, named semantic/static/contract check.
    Luna/high is an explicitly untested exploration slot, never a default.
    """

    complexity = initial.complexity
    if attempts >= 1:
        return _stop(
            complexity,
            "auto_escalation_exhausted",
            "Auto 路由已达到一次升级上限，任务已停止。",
            "bounded escalation limit reached",
        )
    if initial.model != LUNA_MODEL or initial.reasoning_effort != "low":
        return _stop(
            complexity,
            "auto_escalation_not_allowed",
            "当前路由不在允许的 Luna low → Luna high 升级路径中，任务已停止。",
            "route is outside the sole D8 exploration path",
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
    if not _supports(indexed.get(LUNA_MODEL), "high"):
        return _stop(
            complexity,
            "auto_escalation_unsupported",
            "当前账户的 Luna 不支持 high effort，Auto 升级已停止。",
            "Luna high is absent from the account catalog",
        )
    return CodexRouteDecision(
        ok=True,
        model=LUNA_MODEL,
        reasoning_effort="high",
        reason=f"localized verification failure [{check_name}]; bounded Luna high exploration",
        complexity=complexity,
        escalation=True,
        untested_escalation=True,
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
    """Execute the D8 policy with one bounded, visible escalation at most."""

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
