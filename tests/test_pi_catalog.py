from __future__ import annotations

import json

from openbrep.pi_catalog import (
    import_pi_catalog,
    load_configured_pi_catalog,
    load_pi_catalog,
    normalize_pi_catalog_config,
)


def test_imports_provider_map_with_metadata_and_commit_stamp():
    snapshot = import_pi_catalog(
        {
            "openai": {
                "gpt-test": {
                    "name": "GPT Test",
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                    "input": ["text", "image"],
                    "reasoning": True,
                }
            }
        },
        commit="abc123",
    )

    assert snapshot is not None
    assert snapshot.stamp == "oh-my-pi/pi-catalog@abc123"
    assert snapshot.models[0].reference == "openai/gpt-test"
    assert snapshot.models[0].supports_images is True
    assert snapshot.models[0].supports_reasoning is True
    assert snapshot.models[0].context_window == 128000


def test_import_is_fail_closed_for_missing_commit_and_duplicate_identity():
    assert import_pi_catalog({"openai": {}}, commit="") is None
    snapshot = import_pi_catalog(
        {
            "models": [
                {"provider": "openai", "id": "gpt-test", "name": "first"},
                {"provider": "openai", "id": "gpt-test", "name": "second"},
            ]
        },
        commit="v1",
    )
    assert snapshot is not None
    assert len(snapshot.models) == 1
    assert snapshot.models[0].display_name == "first"


def test_local_loader_and_config_normalization_are_read_only(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"openai": {"gpt-test": {"input": "text"}}}), encoding="utf-8")

    config = {"path": str(path), "commit": "deadbeef", "ignored": "value"}
    assert normalize_pi_catalog_config(config) == {"path": str(path), "commit": "deadbeef"}
    snapshot = load_configured_pi_catalog(config)
    assert snapshot is not None
    assert snapshot.models[0].model_id == "gpt-test"
    assert load_pi_catalog(path, commit="deadbeef").stamp.endswith("@deadbeef")
