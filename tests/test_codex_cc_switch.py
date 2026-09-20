from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from openbrep.codex.cc_switch import (
    CcSwitchProviderMissingError,
    CcSwitchProviderUnusableError,
    CcSwitchRegistry,
    CcSwitchRuntimeConfig,
    CcSwitchRuntimeError,
    CcSwitchSchemaUnsupportedError,
    materialize_runtime_home,
)

LEAK_CANARY = "SECRET_CANARY_cc_switch_47a9"


def _settings(
    *,
    model: str,
    catalog: dict | None = None,
    auth: object | None = None,
) -> str:
    payload: dict[str, object] = {
        "auth": auth if auth is not None else {"OPENAI_API_KEY": LEAK_CANARY},
        "config": (
            f'model = "{model}"\n'
            'model_provider = "custom"\n'
            '[model_providers.custom]\n'
            'base_url = "https://example.invalid/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = false\n'
        ),
    }
    if catalog is not None:
        payload["modelCatalog"] = catalog
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def cc_switch_db(tmp_path: Path) -> Path:
    path = tmp_path / "cc-switch.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE providers (
            id TEXT NOT NULL,
            app_type TEXT NOT NULL,
            name TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 0,
            settings_config TEXT NOT NULL,
            PRIMARY KEY (id, app_type)
        )
        """
    )
    catalog = {
        "models": [
            {
                "slug": "deepseek-v4-flash",
                "display_name": "DeepSeek V4 Flash",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Fast"},
                    {"effort": "high", "description": "Deep"},
                    {"effort": "max", "description": "Maximum"},
                ],
                "default_reasoning_level": "high",
            },
            {"slug": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro"},
            {"slug": "deepseek-v4-pro", "display_name": "Duplicate"},
        ]
    }
    connection.executemany(
        "INSERT INTO providers VALUES (?, ?, ?, ?, ?)",
        [
            (
                "deepseek",
                "codex",
                "DeepSeek",
                0,
                _settings(model="fallback-model", catalog=catalog),
            ),
            ("geili", "codex", "给力", 1, _settings(model="gpt-5.6-sol")),
            ("broken", "codex", "Broken", 0, "{not json"),
            ("claude-row", "claude", "Claude", 1, _settings(model="ignored")),
        ],
    )
    connection.commit()
    connection.close()
    return path


def test_catalog_reads_all_valid_codex_providers_without_secrets(cc_switch_db: Path) -> None:
    catalog = CcSwitchRegistry(cc_switch_db).catalog()

    assert [(p.id, [m.model for m in p.models]) for p in catalog.providers] == [
        ("deepseek", ["deepseek-v4-flash", "deepseek-v4-pro"]),
        ("geili", ["gpt-5.6-sol"]),
    ]
    deepseek, geili = catalog.providers
    assert deepseek.catalog_source == "model_catalog"
    assert deepseek.catalog_complete is True
    assert deepseek.models[0].efforts == ("low", "high", "max")
    assert deepseek.models[0].default_effort == "high"
    assert geili.catalog_source == "config_default"
    assert geili.catalog_complete is False
    assert geili.is_current is True
    assert deepseek.runnable is True
    assert catalog.detected is True
    assert [diagnostic.code for diagnostic in catalog.diagnostics] == [
        "cc_switch_provider_unusable"
    ]
    assert LEAK_CANARY not in repr(catalog)
    assert LEAK_CANARY not in json.dumps(catalog.to_public_dict(), ensure_ascii=False)


def test_registry_reads_database_without_changing_bytes_or_mtime(cc_switch_db: Path) -> None:
    before = (
        cc_switch_db.stat().st_mtime_ns,
        hashlib.sha256(cc_switch_db.read_bytes()).digest(),
    )

    CcSwitchRegistry(cc_switch_db).catalog()

    after = (
        cc_switch_db.stat().st_mtime_ns,
        hashlib.sha256(cc_switch_db.read_bytes()).digest(),
    )
    assert after == before


def test_runtime_config_is_private_and_bound_to_exact_provider(cc_switch_db: Path) -> None:
    runtime = CcSwitchRegistry(cc_switch_db).runtime_config("deepseek")

    assert runtime.provider_id == "deepseek"
    assert runtime.config_toml.startswith('model = "fallback-model"')
    assert runtime.auth_payload == {"OPENAI_API_KEY": LEAK_CANARY}
    assert runtime.model_catalog_payload["models"][0]["slug"] == "deepseek-v4-flash"
    assert len(runtime.fingerprint) == 64
    assert LEAK_CANARY not in repr(runtime)


def test_runtime_config_fails_closed_for_missing_or_broken_provider(
    cc_switch_db: Path,
) -> None:
    registry = CcSwitchRegistry(cc_switch_db)

    with pytest.raises(CcSwitchProviderMissingError) as missing:
        registry.runtime_config("not-present")
    with pytest.raises(CcSwitchProviderUnusableError) as broken:
        registry.runtime_config("broken")

    assert missing.value.code == "cc_switch_provider_missing"
    assert broken.value.code == "cc_switch_provider_unusable"
    assert LEAK_CANARY not in str(missing.value)
    assert LEAK_CANARY not in str(broken.value)
    assert str(cc_switch_db) not in str(missing.value)


def test_unknown_schema_returns_stable_error_without_database_path(tmp_path: Path) -> None:
    path = tmp_path / f"{LEAK_CANARY}.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE providers (id TEXT PRIMARY KEY, payload TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(CcSwitchSchemaUnsupportedError) as caught:
        CcSwitchRegistry(path).catalog()

    assert caught.value.code == "cc_switch_schema_unsupported"
    assert LEAK_CANARY not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_missing_database_is_reported_as_undetected(tmp_path: Path) -> None:
    catalog = CcSwitchRegistry(tmp_path / "missing.db").catalog()

    assert catalog.detected is False
    assert catalog.providers == ()
    assert catalog.diagnostics[0].code == "cc_switch_unavailable"


def test_environment_override_selects_registry_database(
    cc_switch_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBREP_CC_SWITCH_DB", os.fspath(cc_switch_db))

    catalog = CcSwitchRegistry().catalog()

    assert [provider.id for provider in catalog.providers] == ["deepseek", "geili"]


def test_default_test_environment_never_points_at_developer_cc_switch_home() -> None:
    configured = Path(os.environ["OPENBREP_CC_SWITCH_DB"])

    assert "obr_test_config_" in str(configured)
    assert configured.name == "missing-cc-switch.db"


def test_materialized_home_has_private_permissions_and_local_catalog(tmp_path: Path) -> None:
    runtime = CcSwitchRuntimeConfig(
        provider_id="deepseek",
        config_toml=(
            'model = "deepseek-v4-flash"\n'
            'model_catalog_json = "/never/use/source-catalog.json"\n'
            'model_provider = "custom"\n'
        ),
        auth_payload={"OPENAI_API_KEY": LEAK_CANARY},
        model_catalog_payload={"models": [{"slug": "deepseek-v4-flash"}]},
        fingerprint="f" * 64,
    )

    home = materialize_runtime_home(runtime, parent=tmp_path)

    assert home.parent == tmp_path
    assert home.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in home.iterdir()} == {
        "auth.json",
        "config.toml",
        "model_catalog.json",
    }
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in home.iterdir())
    config_text = (home / "config.toml").read_text(encoding="utf-8")
    assert config_text.count("model_catalog_json") == 1
    assert 'model_catalog_json = "model_catalog.json"' in config_text
    assert "/never/use" not in config_text
    assert json.loads((home / "auth.json").read_text(encoding="utf-8")) == {
        "OPENAI_API_KEY": LEAK_CANARY
    }


def test_materialization_failure_removes_partial_home(tmp_path: Path) -> None:
    runtime = CcSwitchRuntimeConfig(
        provider_id="deepseek",
        config_toml='model = "deepseek-v4-flash"\n',
        auth_payload={"not-json-serializable": {object()}},
        model_catalog_payload=None,
        fingerprint="f" * 64,
    )

    with pytest.raises(CcSwitchRuntimeError) as caught:
        materialize_runtime_home(runtime, parent=tmp_path)

    assert caught.value.code == "cc_switch_runtime_failed"
    assert list(tmp_path.iterdir()) == []
    assert LEAK_CANARY not in str(caught.value)
