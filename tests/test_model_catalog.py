"""R1: read-only model catalog — identities, capability facts, strict resolution."""

from __future__ import annotations

import copy

import pytest

from openbrep.config import GDLAgentConfig, LLMConfig
from openbrep.model_catalog import (
    SUPPORTED,
    UNKNOWN,
    UNSUPPORTED,
    ModelCatalog,
    ModelResolutionError,
    build_model_catalog,
)


def _config(**llm_kwargs) -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm = LLMConfig(**llm_kwargs)
    return config


def _two_provider_config() -> GDLAgentConfig:
    return _config(
        custom_providers=[
            {
                "name": "opencode-go",
                "api": "https://opencode.example.test/v1",
                "api_key": "test-config-key",
                "default_model": "kimi-k3",
                "models": ["deepseek-v4-flash", "kimi-k3"],
            },
            {
                "name": "ymg",
                "api": "https://ymg.example.test/v1",
                "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
            },
        ]
    )


def test_builtin_preset_keeps_its_reference_provider_and_transport():
    catalog = build_model_catalog(_config())

    spec = catalog.resolve("glm-4-flash")

    assert spec.source == "builtin"
    assert spec.provider == "zhipu"
    assert spec.reference == "glm-4-flash"
    assert spec.api_mode == "chat_completions"
    assert spec.kind == "chat"


def test_native_prefixed_presets_round_trip_their_reference_unchanged():
    catalog = build_model_catalog(_config())

    gemini = catalog.resolve("gemini/gemini-2.5-flash")
    ollama = catalog.resolve("ollama/qwen3:8b")

    assert (gemini.provider, gemini.identity.model_id) == ("google", "gemini-2.5-flash")
    assert gemini.reference == "gemini/gemini-2.5-flash"
    assert gemini.identity.qualified_id == "google/gemini-2.5-flash"
    assert (ollama.provider, ollama.identity.model_id) == ("ollama", "qwen3:8b")
    assert ollama.reference == "ollama/qwen3:8b"


def test_anthropic_presets_publish_the_anthropic_wire_mode():
    catalog = build_model_catalog(_config())

    assert catalog.resolve("claude-sonnet-4-6").api_mode == "anthropic_messages"


def test_configured_entry_shadows_the_builtin_preset_it_overrides():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gateway",
                    "api": "https://gateway.example.test/v1",
                    "models": ["glm-4-flash"],
                }
            ]
        )
    )

    spec = catalog.resolve("glm-4-flash")

    assert (spec.source, spec.provider) == ("config", "gateway")
    # The shadowed preset is unreachable, so the catalog holds no duplicate row.
    assert [s.reference for s in catalog.specs].count("glm-4-flash") == 1


def test_configured_provider_answers_to_alias_target_and_provider_name():
    catalog = build_model_catalog(_two_provider_config())

    by_alias = catalog.resolve("kimi-k3")
    by_qualified = catalog.resolve("opencode-go/kimi-k3")
    by_provider = catalog.resolve("opencode-go")
    by_upstream = catalog.resolve("ymg-gpt-5.3-codex")

    assert by_alias is by_qualified is by_provider
    assert by_alias.identity.model_id == "kimi-k3"
    assert by_alias.identity.qualified_id == "opencode-go/kimi-k3"
    assert by_upstream.identity.model_id == "gpt-5.3-codex"


def _luna_entry() -> dict:
    return {
        "id": "openai-codex/gpt-5.6-luna",
        "display_name": "GPT-5.6 Luna",
        "supported_reasoning_efforts": [{"effort": "low"}, {"effort": "high"}],
    }


def test_colliding_bare_alias_across_providers_is_reported_as_ambiguous():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "provider-a",
                    "api": "https://a.example.test/v1",
                    "models": ["shared-model"],
                },
                {
                    "name": "provider-b",
                    "api": "https://b.example.test/v1",
                    "models": ["shared-model"],
                },
            ]
        )
    )

    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("shared-model")

    assert caught.value.code == "ambiguous_model_reference"
    # The qualified forms stay usable, which is the documented escape hatch.
    assert catalog.resolve("provider-b/shared-model").provider == "provider-b"


def test_same_provider_publishing_one_alias_for_two_models_is_ambiguous():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gateway",
                    "api": "https://gateway.example.test/v1",
                    "models": [
                        {"alias": "router", "model": "model-one"},
                        {"alias": "router", "model": "model-two"},
                    ],
                }
            ]
        )
    )

    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("router")

    assert caught.value.code == "ambiguous_model_reference"


def test_unknown_reference_fails_closed_instead_of_guessing():
    catalog = build_model_catalog(_config())

    with pytest.raises(ModelResolutionError) as unknown:
        catalog.resolve("not-a-real-model")
    with pytest.raises(ModelResolutionError) as missing:
        catalog.resolve("")
    # No prefix or substring fallback: a partial id must not select a model.
    with pytest.raises(ModelResolutionError):
        catalog.resolve("glm-4-fla")

    assert unknown.value.code == "unknown_model"
    assert missing.value.code == "model_reference_required"


def test_codex_entries_are_provider_qualified_only():
    catalog = build_model_catalog(_config(), codex_models=[_luna_entry()])

    spec = catalog.resolve("openai-codex/gpt-5.6-luna")

    assert spec.source == "codex"
    assert spec.kind == "codex"
    assert spec.api_mode == "codex_app_server"
    assert spec.display_name == "GPT-5.6 Luna"
    assert spec.capabilities.reasoning == SUPPORTED
    # A bare id must never reach the subscription path.
    with pytest.raises(ModelResolutionError):
        catalog.resolve("gpt-5.6-luna")


def test_codex_catalog_does_not_shadow_api_key_openai_presets():
    catalog = build_model_catalog(
        _config(),
        codex_models=[{"id": "gpt-5.4", "supported_reasoning_efforts": [{"effort": "high"}]}],
    )

    spec = catalog.resolve("gpt-5.4")

    assert spec.source == "builtin"
    assert spec.provider == "openai"
    assert spec.api_mode == "chat_completions"


def test_capabilities_stay_unknown_where_no_evidence_exists():
    catalog = build_model_catalog(
        _two_provider_config(),
        codex_models=[{"id": "openai-codex/gpt-5.6-terra"}],
    )

    vision_preset = catalog.resolve("gemini/gemini-2.5-pro")
    plain_preset = catalog.resolve("glm-4-flash")
    configured = catalog.resolve("kimi-k3")
    codex = catalog.resolve("openai-codex/gpt-5.6-terra")

    assert vision_preset.capabilities.vision == SUPPORTED
    assert plain_preset.capabilities.vision == UNSUPPORTED
    assert plain_preset.capabilities.reasoning == UNSUPPORTED
    assert configured.capabilities.vision == UNKNOWN
    assert configured.capabilities.reasoning == UNKNOWN
    assert codex.capabilities.reasoning == UNKNOWN  # no effort list published
    assert codex.capabilities.vision == UNKNOWN
    # No source claims tool support yet.
    assert {spec.capabilities.tools for spec in catalog.specs} == {UNKNOWN}


def test_catalog_is_deterministic_and_read_only():
    config = _two_provider_config()
    providers_before = copy.deepcopy(config.llm.custom_providers)

    first = build_model_catalog(config, codex_models=[_luna_entry()])
    second = build_model_catalog(config, codex_models=[_luna_entry()])

    assert isinstance(first, ModelCatalog)
    assert first.specs == second.specs
    assert [spec.reference for spec in first.specs] == [
        spec.reference for spec in second.specs
    ]
    # A projection never rewrites the configuration it reads.
    assert config.llm.custom_providers == providers_before
    with pytest.raises(TypeError):
        first.selectors["glm-4-flash"] = ()  # type: ignore[index]


def test_building_the_catalog_never_resolves_credentials():
    class ExplodingCredentials(LLMConfig):
        def resolve_credentials(self, model=None):  # noqa: D102 - test stub
            raise AssertionError("catalog must not resolve credentials")

        def resolve_api_key(self, model=None):  # noqa: D102 - test stub
            raise AssertionError("catalog must not resolve api keys")

    config = GDLAgentConfig()
    config.llm = ExplodingCredentials(
        custom_providers=[
            {"name": "gateway", "api": "https://gateway.example.test/v1", "models": ["kimi-k3"]}
        ]
    )

    catalog = build_model_catalog(config, codex_models=[_luna_entry()])

    assert catalog.resolve("kimi-k3").provider == "gateway"
    assert catalog.resolve("openai-codex/gpt-5.6-luna").kind == "codex"
    assert catalog.by_source("config") != ()
