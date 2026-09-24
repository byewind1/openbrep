from __future__ import annotations

import pytest

from openbrep.config import LLMConfig
from openbrep.llm import LLMAdapter, LLMResponse
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


def test_from_mapping_normalizes_invalid_entries_and_roundtrips_valid_policy():
    router = RetryRouter.from_mapping(
        {
            "fallback_chains": {
                "create": [
                    {"model": "fallback", "reasoning_effort": "high", "tier": "slow"},
                    {"model": ""},
                ],
                "unknown": [{"model": "ignored"}],
            },
            "cooldown_seconds": "3.5",
            "revert_policy": "primary_after_success",
        }
    )

    assert router.cooldown_seconds == 3.5
    assert router.revert_policy == "primary_after_success"
    assert router.as_config() == {
        "fallback_chains": {
            "create": [
                {"model": "fallback", "reasoning_effort": "high", "tier": "slow"}
            ]
        },
        "cooldown_seconds": 3.5,
        "revert_policy": "primary_after_success",
    }


def test_from_mapping_invalid_cooldown_fails_closed_to_inert_policy():
    router = RetryRouter.from_mapping(
        {"cooldown_seconds": "nan", "revert_policy": "not-a-policy"}
    )

    assert router.as_config() == {}


def test_runtime_adapter_uses_role_fallback_after_primary_failure(monkeypatch):
    config = LLMConfig(
        model="primary",
        api_key="key",
        retry={"fallback_chains": {"create": [{"model": "fallback"}]}},
    )
    adapter = LLMAdapter(config)
    adapter.retry_role = "create"
    calls = []

    def fake_once(self, messages, **kwargs):
        calls.append(self.config.model)
        if self.config.model == "primary":
            raise RuntimeError("temporary provider failure")
        return LLMResponse(content="ok", model=self.config.model)

    monkeypatch.setattr(LLMAdapter, "_generate_once", fake_once)
    result = adapter.generate([{"role": "user", "content": "hi"}])
    assert result.content == "ok"
    assert calls == ["primary", "fallback"]
    assert result.metadata["retry"]["role"] == "create"
