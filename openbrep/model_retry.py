"""Role-aware model retry policy primitives.

This module is intentionally side-effect free. It plans a fallback selection
from immutable inputs; it does not call providers, persist cooldown state, or
change the active configuration. Codex D8/D13 routing remains in
``openbrep.codex.routing`` until a later integration slice supplies an explicit
policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from openbrep.model_catalog import ModelSelection, ModelTier, TaskRole

RevertPolicy = Literal["per_request", "primary_after_success"]


@dataclass(frozen=True)
class FallbackCandidate:
    """One configured fallback target."""

    model: str
    reasoning_effort: str = ""
    tier: ModelTier | None = None

    def key(self) -> tuple[str, str, ModelTier | None]:
        return (self.model, self.reasoning_effort, self.tier)


@dataclass(frozen=True)
class RetryDecision:
    """Result of planning one attempt."""

    allowed: bool
    attempt_index: int
    selection: ModelSelection | None = None
    code: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RetryRouter:
    """Plan role-specific fallbacks without owning runtime state.

    ``fallback_chains`` stores only fallback entries; the primary selection is
    supplied per request. Attempt zero always returns that primary selection.
    Cooldown is evaluated against caller-supplied timestamps, so callers can use
    monotonic time, wall time, or a deterministic test clock.
    """

    fallback_chains: Mapping[TaskRole, tuple[FallbackCandidate, ...]] = field(
        default_factory=dict
    )
    cooldown_seconds: float = 0.0
    revert_policy: RevertPolicy = "per_request"

    def __post_init__(self) -> None:
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "RetryRouter":
        """Build a safe router from the optional ``[llm.retry]`` table.

        Unknown roles, malformed candidates, invalid cooldowns, and unsupported
        revert policies are ignored or normalized to the inert defaults. This
        keeps configuration loading fail closed and avoids making retry policy a
        second source of model identity rules.
        """

        data = raw if isinstance(raw, Mapping) else {}
        chains: dict[TaskRole, tuple[FallbackCandidate, ...]] = {}
        raw_chains = data.get("fallback_chains")
        valid_roles = {"main", "create", "modify", "vision", "repair", "compact", "judge"}
        if isinstance(raw_chains, Mapping):
            for raw_role, entries in raw_chains.items():
                role = str(raw_role or "").strip()
                if role not in valid_roles or not isinstance(entries, (list, tuple)):
                    continue
                parsed: list[FallbackCandidate] = []
                for item in entries:
                    if not isinstance(item, Mapping):
                        continue
                    model = str(item.get("model") or "").strip()
                    if not model:
                        continue
                    tier = str(item.get("tier") or "").strip() or None
                    if tier not in {None, "smol", "balanced", "slow"}:
                        tier = None
                    parsed.append(
                        FallbackCandidate(
                            model=model,
                            reasoning_effort=str(item.get("reasoning_effort") or "").strip(),
                            tier=tier,
                        )
                    )
                if parsed:
                    chains[role] = tuple(parsed)  # type: ignore[assignment]

        try:
            cooldown = float(data.get("cooldown_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            cooldown = 0.0
        if not math.isfinite(cooldown) or cooldown < 0:
            cooldown = 0.0
        revert = str(data.get("revert_policy") or "per_request").strip()
        if revert not in {"per_request", "primary_after_success"}:
            revert = "per_request"
        return cls(fallback_chains=chains, cooldown_seconds=cooldown, revert_policy=revert)

    def as_config(self) -> dict[str, Any]:
        """Serialize only normalized, non-default retry policy values."""

        chains = {
            role: [
                {
                    "model": candidate.model,
                    **(
                        {"reasoning_effort": candidate.reasoning_effort}
                        if candidate.reasoning_effort
                        else {}
                    ),
                    **({"tier": candidate.tier} if candidate.tier else {}),
                }
                for candidate in candidates
            ]
            for role, candidates in self.fallback_chains.items()
            if candidates
        }
        result: dict[str, Any] = {}
        if chains:
            result["fallback_chains"] = chains
        if self.cooldown_seconds:
            result["cooldown_seconds"] = self.cooldown_seconds
        if self.revert_policy != "per_request":
            result["revert_policy"] = self.revert_policy
        return result

    def candidates(
        self,
        primary: ModelSelection,
        *,
        role: TaskRole | None = None,
    ) -> tuple[FallbackCandidate, ...]:
        """Return primary plus unique fallbacks in configured order."""

        effective_role = role or primary.role
        primary_candidate = FallbackCandidate(
            model=primary.model,
            reasoning_effort=primary.reasoning_effort,
            tier=primary.tier,
        )
        seen = {primary_candidate.key()}
        result = [primary_candidate]
        for candidate in self.fallback_chains.get(effective_role, ()):
            if candidate.key() in seen:
                continue
            seen.add(candidate.key())
            result.append(candidate)
        return tuple(result)

    def plan(
        self,
        primary: ModelSelection,
        *,
        attempt_index: int,
        now: float | None = None,
        last_attempt_at: float | None = None,
        role: TaskRole | None = None,
    ) -> RetryDecision:
        """Plan an attempt, enforcing chain bounds and cooldown.

        ``attempt_index=0`` is always the primary. For fallback attempts,
        ``last_attempt_at`` is the caller's timestamp for the previous attempt;
        no clock is read here. This makes the decision deterministic and keeps
        cooldown state outside the policy object.
        """

        if attempt_index < 0:
            return RetryDecision(
                False,
                attempt_index,
                code="invalid_attempt",
                reason="attempt index must be non-negative",
            )
        if attempt_index == 0:
            return RetryDecision(
                True,
                0,
                selection=primary,
                code="primary",
                reason="primary selection",
            )

        effective_role = role or primary.role
        chain = self.candidates(primary, role=effective_role)
        if attempt_index >= len(chain):
            return RetryDecision(
                False,
                attempt_index,
                code="retry_exhausted",
                reason=(
                    f"no fallback candidate for role={effective_role} "
                    f"at attempt {attempt_index}"
                ),
            )

        if self.cooldown_seconds and (now is None or last_attempt_at is None):
            return RetryDecision(
                False,
                attempt_index,
                code="cooldown_clock_required",
                reason="cooldown requires both now and last_attempt_at",
            )
        if self.cooldown_seconds and now - last_attempt_at < self.cooldown_seconds:
            return RetryDecision(
                False,
                attempt_index,
                code="cooldown_active",
                reason=f"fallback cooldown active for role={effective_role}",
            )

        candidate = chain[attempt_index]
        selection = ModelSelection(
            model=candidate.model,
            reasoning_effort=candidate.reasoning_effort,
            policy="retry_fallback",
            route_reason=f"role={effective_role} fallback attempt {attempt_index}",
            role=effective_role,
            tier=candidate.tier,
        )
        return RetryDecision(
            True,
            attempt_index,
            selection=selection,
            code="fallback",
            reason=selection.route_reason,
        )

    def after_attempt(
        self,
        primary: ModelSelection,
        current: ModelSelection,
        *,
        succeeded: bool,
    ) -> ModelSelection:
        """Apply the configured post-success revert rule in memory."""

        if succeeded and self.revert_policy == "primary_after_success":
            return primary
        return current
