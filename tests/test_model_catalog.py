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


def _provider(name: str, models: list, **extra) -> dict:
    """A configured provider entry; the endpoint value is never asserted here."""

    return {"name": name, "api": "https://entry.example.test/v1", "models": models, **extra}


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
        codex_models=[_luna_entry(), {"id": "openai-codex/gpt-5.6-terra"}],
    )

    vision_preset = catalog.resolve("gemini/gemini-2.5-pro")
    vision_named_preset = catalog.resolve("glm-4.6v")
    configured = catalog.resolve("kimi-k3")
    codex_with_efforts = catalog.resolve("openai-codex/gpt-5.6-luna")
    codex_without_efforts = catalog.resolve("openai-codex/gpt-5.6-terra")

    # No model, not even a vision-named preset, is claimed to support or lack
    # anything: the legacy VISION_MODELS/REASONING_MODELS tables have no reader.
    for spec in (vision_preset, vision_named_preset, configured):
        assert spec.capabilities.as_dict() == {
            "vision": UNKNOWN,
            "reasoning": UNKNOWN,
            "tools": UNKNOWN,
        }
    # The one backed fact: the account catalogue publishes the effort list.
    assert codex_with_efforts.capabilities.reasoning == SUPPORTED
    assert codex_with_efforts.capabilities.vision == UNKNOWN
    assert codex_without_efforts.capabilities.reasoning == UNKNOWN


def test_no_source_claims_unsupported_without_a_backing_fact():
    catalog = build_model_catalog(_two_provider_config(), codex_models=[_luna_entry()])

    claimed = {
        state
        for spec in catalog.specs
        for state in spec.capabilities.as_dict().values()
    }

    assert UNSUPPORTED not in claimed
    assert claimed <= {SUPPORTED, UNKNOWN}


def test_builtin_entries_publish_only_the_preset_string():
    catalog = build_model_catalog(_config())

    # The native-prefixed preset is selectable...
    assert catalog.resolve("gemini/gemini-2.5-flash").provider == "google"
    # ...but stripping the prefix is not a published selector, and neither is a
    # bare Ollama tag, even though prefix inference would guess a provider.
    for rejected in ("gemini-2.5-flash", "qwen2.5:14b", "glm-4-fla"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(rejected)
        assert caught.value.code == "unknown_model"


def test_configured_entry_answers_to_its_upstream_model_id():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "nodef",
                    "api": "https://nodef.example.test/v1",
                    "models": [
                        {"alias": "nodef-a", "model": "up-a"},
                        {"alias": "nodef-b", "model": "up-b"},
                    ],
                }
            ]
        )
    )

    by_upstream = catalog.resolve("up-b")
    by_alias = catalog.resolve("nodef-b")

    assert by_upstream is by_alias
    assert by_upstream.identity.qualified_id == "nodef/up-b"


def test_provider_name_without_default_model_addresses_first_listed_model():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "nodef",
                    "api": "https://nodef.example.test/v1",
                    "models": [
                        {"alias": "nodef-a", "model": "up-a"},
                        {"alias": "nodef-b", "model": "up-b"},
                    ],
                }
            ]
        )
    )

    spec = catalog.resolve("nodef")

    assert spec.identity.model_id == "up-a"
    assert spec.reference == "nodef-a"


def test_default_model_naming_an_upstream_id_is_not_reported_as_ambiguity():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gw",
                    "api": "https://gw.example.test/v1",
                    "default_model": "gpt-5.4",
                    "models": [{"alias": "my-gpt", "model": "gpt-5.4"}],
                }
            ]
        )
    )

    by_alias = catalog.resolve("my-gpt")
    by_provider = catalog.resolve("gw")
    by_qualified = catalog.resolve("gw/gpt-5.4")
    by_upstream = catalog.resolve("gpt-5.4")

    assert by_alias is by_provider is by_qualified is by_upstream
    assert by_alias.identity.model_id == "gpt-5.4"
    assert [spec.reference for spec in catalog.specs].count("my-gpt") == 1


def test_default_model_naming_an_unlisted_id_stays_addressable():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gw",
                    "api": "https://gw.example.test/v1",
                    "default_model": "upstream-only",
                    "models": ["listed-model"],
                }
            ]
        )
    )

    by_provider = catalog.resolve("gw")
    by_name = catalog.resolve("gw/upstream-only")

    assert by_provider is by_name
    assert by_provider.identity.model_id == "upstream-only"
    assert catalog.resolve("listed-model").identity.model_id == "listed-model"
    # The bare unlisted id has no configuration entry and the adapter cannot
    # route it, so the catalog must not publish it as selectable.
    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("upstream-only")
    assert caught.value.code == "unknown_model"


def test_provider_name_containing_slash_publishes_no_qualified_spelling():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gw/15",
                    "api": "https://gw15.example.test/v1",
                    "models": [{"alias": "a1", "model": "m1"}],
                }
            ]
        )
    )

    assert catalog.resolve("a1").provider == "gw/15"
    assert catalog.resolve("gw/15").identity.model_id == "m1"
    # Splitting on the first "/" could never parse this back to that provider, so
    # the spelling is not claimed.
    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("gw/15/a1")
    assert caught.value.code == "unknown_model"


def test_nameless_provider_aliases_stay_addressable():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {"name": "", "api": "https://anon.example.test/v1", "models": ["anon-model"]}
            ]
        )
    )

    spec = catalog.resolve("anon-model")

    assert spec.source == "config"
    assert spec.provider == ""
    assert spec.identity.model_id == "anon-model"
    # No prefix means no qualified spelling and no provider-name selector.
    with pytest.raises(ModelResolutionError):
        catalog.resolve("/anon-model")


def test_segment_whitespace_in_qualified_references_is_normalised():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {"name": "gw", "api": "https://gw.example.test/v1", "models": ["m1"]}
            ]
        )
    )

    expected = catalog.resolve("gw/m1")

    for padded in (" gw /m1", "gw/ m1", "gw /m1"):
        assert catalog.resolve(padded) is expected


def test_bare_reserved_codex_identity_is_not_a_model():
    catalog = build_model_catalog(_config(), codex_models=[_luna_entry()])

    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("openai-codex")
    assert caught.value.code == "unknown_model"
    assert catalog.resolve("openai-codex/gpt-5.6-luna").kind == "codex"


def test_reserved_codex_identity_stays_unaddressable_when_the_entry_exists():
    """Production seeds a reserved provider entry; it must not become a model."""

    from openbrep.config import ensure_codex_provider_entry

    config = _config()
    entry = ensure_codex_provider_entry(config)
    entry["models"] = ["sneaked-in"]  # even a tampered entry publishes nothing
    catalog = build_model_catalog(config, codex_models=[_luna_entry()])

    for rejected in ("openai-codex", "sneaked-in"):
        with pytest.raises(ModelResolutionError):
            catalog.resolve(rejected)
    assert catalog.resolve("openai-codex/gpt-5.6-luna").source == "codex"


def test_codex_identity_is_matched_exactly_like_the_adapter_prefix():
    catalog = build_model_catalog(_config(), codex_models=[_luna_entry()])

    assert catalog.resolve("openai-codex/gpt-5.6-luna").kind == "codex"
    for variant in (
        "OPENAI-CODEX/gpt-5.6-luna",
        "Openai-Codex/gpt-5.6-luna",
        "openai-codex/GPT-5.6-LUNA",
        " openai-codex /gpt-5.6-luna",
    ):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(variant)
        assert caught.value.code == "unknown_model"


def test_literal_identities_are_never_reached_through_a_padded_spelling():
    catalog = build_model_catalog(_config())

    assert catalog.resolve("gemini/gemini-2.5-flash").source == "builtin"
    for padded in (" gemini /gemini-2.5-flash", "gemini /gemini-2.5-flash"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(padded)
        assert caught.value.code == "unknown_model"


def test_reserved_identity_case_variants_never_shadow_codex_or_the_validator():
    """A case-variant alias must not claim the lowercased reserved key."""

    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "v",
                    "api": "https://v.example.test/v1",
                    "models": [{"alias": "Openai-Codex/gpt-5.6-luna", "model": "up"}],
                }
            ]
        ),
        codex_models=[_luna_entry()],
    )

    spec = catalog.resolve("openai-codex/gpt-5.6-luna")

    assert (spec.source, spec.provider) == ("codex", "openai-codex")
    assert [item.reference for item in catalog.by_source("codex")] == ["openai-codex/gpt-5.6-luna"]
    assert catalog.resolve("up").provider == "v"


def test_provider_named_as_a_reserved_identity_variant_is_not_addressable():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {"name": "Openai-Codex", "api": "https://c.example.test/v1", "models": ["sneaked"]}
            ]
        )
    )

    # The entry's own model id still routes, exactly as the read path has it...
    assert catalog.resolve("sneaked").provider == "Openai-Codex"
    # ...but every spelling of the reserved identity stays refused, in any case.
    for rejected in ("openai-codex", "Openai-Codex", "openai-codex/sneaked"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(rejected)
        assert caught.value.code == "unknown_model"


def test_padded_qualified_reference_must_name_the_claimant_provider():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                _provider("gw", [{"alias": "a1", "model": "m1"}]),
                _provider("gw/15", [{"alias": "a1", "model": "m1"}]),
                _provider("own", [{"alias": "x", "model": "v9/foo"}]),
            ]
        )
    )

    # The read path strips both halves, so padding inside a form that names the
    # owning provider is still resolvable.
    assert catalog.resolve(" gw /a1").provider == "gw"
    assert catalog.resolve("own/ v9/foo").provider == "own"
    # Padding must not invent a provider match: the head names a slash-name
    # provider, or names no provider at all.
    for rejected in (" gw /15", " v9 /foo"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(rejected)
        assert caught.value.code == "unknown_model"


def test_padded_alias_containing_a_slash_is_not_normalised_into_a_provider_match():
    """Without a provider owning the head, only the whole alias form resolves."""

    catalog = build_model_catalog(
        _config(custom_providers=[_provider("gw", [{"alias": "x", "model": "v1/foo"}])])
    )

    assert catalog.resolve("v1/foo").provider == "gw"
    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve(" v1 /foo")
    assert caught.value.code == "unknown_model"


def test_bare_selector_with_a_slash_is_not_published_when_a_provider_owns_the_head():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                _provider("gw", ["m1"]),
                _provider("gw/15", [{"alias": "a1", "model": "m1"}]),
            ]
        )
    )

    # The read path splits first: "gw/15" is provider gw with model id "15",
    # which is the documented direct-connect boundary, not this catalog's entry.
    with pytest.raises(ModelResolutionError):
        catalog.resolve("gw/15")
    assert catalog.resolve("a1").provider == "gw/15"
    # With no provider owning the head the bare spelling stays addressable.
    lone = build_model_catalog(
        _config(custom_providers=[_provider("gw/15", [{"alias": "a1", "model": "m1"}])])
    )
    assert lone.resolve("gw/15").provider == "gw/15"


def test_configured_alias_spelled_as_a_codex_reference_never_shadows_codex():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "v",
                    "api": "https://v.example.test/v1",
                    "models": [{"alias": "openai-codex/gpt-5.6-luna", "model": "up"}],
                }
            ]
        ),
        codex_models=[_luna_entry()],
    )

    spec = catalog.resolve("openai-codex/gpt-5.6-luna")

    # The adapter sends every openai-codex/... string to the subscription path.
    assert (spec.source, spec.kind, spec.provider) == ("codex", "codex", "openai-codex")
    # The configured entry stays reachable by its upstream id; only the alias
    # spelling that names the reserved identity is dead config.
    assert catalog.resolve("up").provider == "v"
    # A case variant the adapter would route to the config provider is still
    # refused: the catalog never publishes a reserved-identity spelling.
    with pytest.raises(ModelResolutionError) as caught:
        catalog.resolve("OPENAI-CODEX/gpt-5.6-luna")
    assert caught.value.code == "unknown_model"


def test_several_aliases_of_one_upstream_model_share_a_single_spec():
    catalog = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "dual",
                    "api": "https://dual.example.test/v1",
                    "models": [
                        {"alias": "alpha", "model": "same-up"},
                        {"alias": "beta", "model": "same-up"},
                    ],
                }
            ]
        )
    )

    by_upstream = catalog.resolve("same-up")
    by_alpha = catalog.resolve("alpha")
    by_beta = catalog.resolve("beta")
    by_qualified = catalog.resolve("dual/same-up")

    assert by_alpha is by_beta is by_upstream is by_qualified
    assert by_alpha.identity.qualified_id == "dual/same-up"
    assert catalog.resolve("dual/beta") is by_alpha


def test_preset_membership_is_exact_case_while_custom_aliases_are_not():
    catalog = build_model_catalog(_config())

    assert catalog.resolve("glm-4-flash").provider == "zhipu"
    for variant in ("GLM-4-FLASH", "GPT-5.4"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(variant)
        assert caught.value.code == "unknown_model"

    custom = build_model_catalog(
        _config(
            custom_providers=[
                {
                    "name": "gateway",
                    "api": "https://gateway.example.test/v1",
                    "models": [{"alias": "My-GPT", "model": "gpt-5.4"}],
                }
            ]
        )
    )
    assert custom.resolve("my-gpt") is custom.resolve("My-GPT")


def test_bare_provider_prefix_without_a_model_id_resolves_to_nothing():
    catalog = build_model_catalog(_config())

    for reference in ("ollama/", "ollama"):
        with pytest.raises(ModelResolutionError) as caught:
            catalog.resolve(reference)
        assert caught.value.code == "unknown_model"


def test_open_family_local_tags_resolve_without_a_catalog_entry():
    catalog = build_model_catalog(_config())

    for reference in ("ollama/llama3.1:8b", "ollama/qwen2.5:7b"):
        spec = catalog.resolve(reference)
        assert spec.source == "open"
        assert spec.provider == "ollama"
        assert spec.reference == reference
        assert spec.capabilities.as_dict() == {
            "vision": UNKNOWN,
            "reasoning": UNKNOWN,
            "tools": UNKNOWN,
        }
    # A preset tag still resolves as its built-in entry, and synthesised open
    # entries are not part of the catalog listing.
    assert catalog.resolve("ollama/qwen3:8b").source == "builtin"
    assert catalog.by_source("open") == ()
    with pytest.raises(ModelResolutionError):
        catalog.resolve("ollama/")


def test_provider_with_no_models_is_still_addressable_by_name():
    catalog = build_model_catalog(
        _config(
            custom_providers=[{"name": "empty-provider", "api": "https://empty.example.test/v1"}]
        )
    )

    spec = catalog.resolve("empty-provider")

    assert spec.provider == "empty-provider"
    assert spec.identity.model_id == "empty-provider"


def test_catalog_agrees_with_the_settings_validator_except_on_documented_ambiguity():
    """Parity guard for the R3 migration: the catalog and today's closed
    settings validator must accept the same references, except that the catalog
    reports a colliding bare alias as ambiguity where production silently picks
    the first configured entry."""

    from types import SimpleNamespace

    from openbrep.config import find_custom_provider_match
    from openbrep.workbench.settings_service import WorkbenchSettingsService

    providers = [
        {
            "name": "opencode-go",
            "api": "https://opencode.example.test/v1",
            "default_model": "kimi-k3",
            "models": ["deepseek-v4-flash", "kimi-k3"],
        },
        {
            "name": "gw",
            "api": "https://gw.example.test/v1",
            "default_model": "gpt-5.4",
            "models": [{"alias": "my-gpt", "model": "gpt-5.4"}],
        },
        {"name": "pa", "api": "https://a.example.test/v1", "models": ["shared"]},
        {"name": "pb", "api": "https://b.example.test/v1", "models": ["shared"]},
        {
            "name": "dual",
            "api": "https://dual.example.test/v1",
            "models": [
                {"alias": "alpha", "model": "same-up"},
                {"alias": "beta", "model": "same-up"},
            ],
        },
        {
            "name": "ghost",
            "api": "https://ghost.example.test/v1",
            "default_model": "ghost-model",
            "models": ["ghost-listed"],
        },
    ]
    config = _config(custom_providers=providers)
    catalog = build_model_catalog(config)

    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path="/tmp/openbrep-catalog-parity-config.toml",
        session_llm_model=None,
        session_reasoning_effort=None,
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None)

    references = [
        "glm-4-flash",
        "gemini/gemini-2.5-flash",
        "ollama/qwen3:8b",
        "ollama/llama3.1:8b",
        "ollama/qwen2.5:7b",
        "kimi-k3",
        "opencode-go",
        "opencode-go/kimi-k3",
        "my-gpt",
        "gw/gpt-5.4",
        "alpha",
        "beta",
        "same-up",
        "dual/same-up",
        "dual/beta",
        "ghost",
        "ghost/ghost-model",
        "ghost-model",
        "ghost-listed",
        "pa/shared",
        "not-a-real-model",
        "shared",  # the one documented divergence
    ]

    for reference in references:
        accepts = service._catalog_known_model(reference)
        try:
            spec = catalog.resolve(reference)
        except ModelResolutionError as exc:
            if exc.code == "ambiguous_model_reference":
                # The documented divergence: production resolves the colliding
                # bare alias by configuration order, the catalog refuses to pick.
                assert reference == "shared", f"{reference}: unexpected ambiguity"
                assert accepts
                continue
            assert exc.code == "unknown_model", f"{reference}: unexpected {exc.code}"
            assert not accepts, f"{reference} is rejected by the validator, catalog disagrees"
            continue
        assert accepts, f"{reference} resolves in the catalog but the settings validator rejects it"
        if spec.source == "config":
            production = find_custom_provider_match(providers, reference)
            assert production is not None
            assert production["provider_name"] == spec.provider
            assert production["model"] == spec.identity.model_id


def test_transport_compat_reproduces_the_previous_string_checks_exactly():
    """R2: the centralised rule must be byte-equivalent to the checks it replaced."""

    from openbrep.config import ALL_MODELS, provider_profile_for_model
    from openbrep.model_catalog import transport_compat

    def legacy_drop_params(model: str) -> bool:
        lowered = model.lower()
        return "gpt-5" in lowered or "codex" in lowered

    def legacy_omit_temperature(model: str) -> bool:
        lowered = model.lower()
        return any(token in lowered for token in ("gpt-5", "codex", "o1", "o3", "o4"))

    corpus = set(ALL_MODELS)
    for reference in ALL_MODELS:
        profile = provider_profile_for_model(reference)
        if profile is not None and profile.native_prefix and "/" not in reference:
            corpus.add(f"{profile.native_prefix}{reference}")
        corpus.add(reference.upper())
    corpus |= {
        "openai/gpt-5.4",
        "openai-codex/gpt-5.6-luna",
        "openai/o3-mini",
        "openai/o4-mini",
        "o1-preview",
        "ollama/qwen3:8b",
        "glm-4-flash",
        "deepseek-v4-flash",
        "",
    }

    for wire_model in sorted(corpus):
        compat = transport_compat(wire_model)
        assert compat.drop_params == legacy_drop_params(wire_model), wire_model
        assert compat.omit_temperature == legacy_omit_temperature(wire_model), wire_model


def test_transport_compat_keeps_the_o_series_asymmetry():
    from openbrep.model_catalog import transport_compat

    # The o-series silently ignores temperature but never needed drop_params;
    # that asymmetry is the behaviour the adapter has always had.
    o_series = transport_compat("openai/o3-mini")
    assert o_series.omit_temperature is True
    assert o_series.drop_params is False

    gpt5 = transport_compat("openai/gpt-5.4")
    assert (gpt5.drop_params, gpt5.omit_temperature) == (True, True)

    plain = transport_compat("glm-4-flash")
    assert (plain.drop_params, plain.omit_temperature) == (False, False)


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
