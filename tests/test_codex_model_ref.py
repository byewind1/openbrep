from __future__ import annotations

import pytest

from openbrep.codex.model_ref import (
    CodexModelRef,
    CodexModelRefError,
    build_cc_switch_model_ref,
    parse_codex_model_ref,
    wire_model_name,
)


def test_cc_switch_model_ref_round_trips_provider_and_model() -> None:
    value = build_cc_switch_model_ref("deepseek-prod", "team/model v4")

    assert value == "openai-codex/ccswitch/deepseek-prod/team%2Fmodel%20v4"
    assert parse_codex_model_ref(value) == CodexModelRef(
        kind="cc_switch",
        provider_id="deepseek-prod",
        model="team/model v4",
    )
    assert wire_model_name(value) == "team/model v4"


def test_cc_switch_model_ref_round_trips_unicode_model_name() -> None:
    value = build_cc_switch_model_ref("provider_01", "模型/推理 版")

    assert value == (
        "openai-codex/ccswitch/provider_01/"
        "%E6%A8%A1%E5%9E%8B%2F%E6%8E%A8%E7%90%86%20%E7%89%88"
    )
    assert parse_codex_model_ref(value).model == "模型/推理 版"


def test_legacy_model_ref_keeps_existing_wire_name() -> None:
    parsed = parse_codex_model_ref("openai-codex/gpt-5.6-sol")

    assert parsed == CodexModelRef(kind="legacy", model="gpt-5.6-sol")
    assert wire_model_name("openai-codex/gpt-5.6-sol") == "gpt-5.6-sol"


@pytest.mark.parametrize(
    "value",
    [
        "openai-codex/ccswitch/../model",
        "openai-codex/ccswitch/provider/",
        "openai-codex/ccswitch/provider/a/b",
        "openai-codex/ccswitch/provider/%00",
        "openai-codex/ccswitch/provider/%2f",
        "openai-codex/ccswitch/provider/%ZZ",
        "openai-codex/ccswitch//model",
    ],
)
def test_cc_switch_model_ref_rejects_ambiguous_or_unsafe_values(value: str) -> None:
    with pytest.raises(CodexModelRefError) as caught:
        parse_codex_model_ref(value)

    assert caught.value.code == "invalid_codex_model_ref"


@pytest.mark.parametrize(
    ("provider_id", "model"),
    [
        ("../provider", "model"),
        ("provider/name", "model"),
        ("provider", ""),
        ("provider", "bad\nmodel"),
    ],
)
def test_cc_switch_model_ref_builder_rejects_unsafe_parts(
    provider_id: str,
    model: str,
) -> None:
    with pytest.raises(CodexModelRefError) as caught:
        build_cc_switch_model_ref(provider_id, model)

    assert caught.value.code == "invalid_codex_model_ref"
