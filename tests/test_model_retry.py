from __future__ import annotations

import pytest

from openbrep.model_catalog import ModelSelection
from openbrep.model_retry import FallbackCandidate, RetryRouter


def _primary() -> ModelSelection:
    return ModelSelection(
        model="openai-codex/gpt-5.6-luna",
        reasoning_effort="low",
        policy="codex_auto",
        role="create",
        tier="balanced",
    )


def test_empty_chain_keeps_primary_and_exhausts_without_fallback():
    router = RetryRouter()
    primary = _primary()

    first = router.plan(primary, attempt_index=0)
    second = router.plan(primary, attempt_index=1)

    assert first.allowed and first.selection is primary
    assert (second.allowed, second.code) == (False, "retry_exhausted")


def test_role_chain_deduplicates_primary_and_preserves_order():
    primary = _primary()
    router = RetryRouter(
        fallback_chains={
            "create": (
                FallbackCandidate(primary.model, "low", "balanced"),
                FallbackCandidate("openai-codex/gpt-5.6-luna", "high", "slow"),
                FallbackCandidate("openai-codex/gpt-5.6-terra", "high", "slow"),
            )
        }
    )

    candidates = router.candidates(primary)

    assert [(item.model, item.reasoning_effort) for item in candidates] == [
        ("openai-codex/gpt-5.6-luna", "low"),
        ("openai-codex/gpt-5.6-luna", "high"),
        ("openai-codex/gpt-5.6-terra", "high"),
    ]


def test_fallback_plan_is_role_aware_and_does_not_mutate_primary():
    primary = _primary()
    router = RetryRouter(
        fallback_chains={
            "create": (FallbackCandidate("openai-codex/gpt-5.6-terra", "high", "slow"),)
        }
    )

    decision = router.plan(primary, attempt_index=1)

    assert decision.allowed
    assert decision.code == "fallback"
    assert decision.selection is not None
    assert decision.selection.role == "create"
    assert decision.selection.model.endswith("terra")
    assert primary.model.endswith("luna")


def test_cooldown_requires_clock_and_blocks_until_elapsed():
    router = RetryRouter(
        fallback_chains={"create": (FallbackCandidate("fallback", "", "balanced"),)},
        cooldown_seconds=5.0,
    )
    primary = _primary()

    missing_clock = router.plan(primary, attempt_index=1)
    blocked = router.plan(primary, attempt_index=1, now=103.0, last_attempt_at=100.0)
    ready = router.plan(primary, attempt_index=1, now=105.0, last_attempt_at=100.0)

    assert missing_clock.code == "cooldown_clock_required"
    assert blocked.code == "cooldown_active"
    assert ready.allowed and ready.selection is not None


def test_revert_policy_returns_primary_only_after_success():
    primary = _primary()
    router = RetryRouter(revert_policy="primary_after_success")
    fallback = ModelSelection(model="fallback", role="create", tier="slow")

    assert router.after_attempt(primary, fallback, succeeded=False) is fallback
    assert router.after_attempt(primary, fallback, succeeded=True) is primary


def test_negative_cooldown_is_rejected():
    with pytest.raises(ValueError, match="cooldown_seconds"):
        RetryRouter(cooldown_seconds=-1)
