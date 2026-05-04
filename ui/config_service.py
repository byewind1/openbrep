from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from openbrep.config import GDLAgentConfig, iter_custom_provider_model_entries


@dataclass
class RuntimeConfigState:
    config: GDLAgentConfig | None = None
    defaults: dict = field(default_factory=dict)
    provider_keys: dict = field(default_factory=dict)
    custom_providers: list[dict] = field(default_factory=list)


def load_runtime_config(root: Path | str) -> RuntimeConfigState:
    """Load config.toml plus normalized GDLAgentConfig runtime fields."""
    root_path = Path(root)
    provider_keys: dict = {}
    custom_providers: list[dict] = []

    toml_path = root_path / "config.toml"
    if toml_path.exists():
        raw = _load_toml(toml_path)
        llm_raw = raw.get("llm", {}) if isinstance(raw, dict) else {}
        provider_keys = dict(llm_raw.get("provider_keys", {}) or {})
        custom_providers = list(llm_raw.get("custom_providers", []) or [])

    config = GDLAgentConfig.load(str(toml_path) if toml_path.exists() else None)
    provider_keys = dict(config.llm.provider_keys or provider_keys)
    custom_providers = list(config.llm.custom_providers or custom_providers)
    defaults = {
        "llm_model": config.llm.model,
        "compiler_path": config.compiler.path or "",
        "assistant_settings": config.llm.assistant_settings or "",
    }
    return RuntimeConfigState(
        config=config,
        defaults=defaults,
        provider_keys=provider_keys,
        custom_providers=custom_providers,
    )


def build_generation_config(
    root: Path | str,
    *,
    model_name: str = "",
    api_key: str = "",
    api_base: str = "",
    assistant_settings: str = "",
) -> GDLAgentConfig:
    """Build the config used by generation from the same root as the UI."""
    cfg = load_runtime_config(root).config
    model = str(model_name or "").strip()
    if model:
        cfg.llm.model = model
    if api_key:
        cfg.llm.api_key = api_key
    if api_base:
        cfg.llm.api_base = api_base
    if assistant_settings:
        cfg.llm.assistant_settings = assistant_settings
    return cfg


def available_models(config: GDLAgentConfig | None, custom_providers: list[dict], builtin_models: Iterable[str]) -> list[str]:
    if config is not None:
        return [str(model) for model in config.get_available_models()]

    models: list[str] = []
    for provider in custom_providers or []:
        for entry in iter_custom_provider_model_entries(provider):
            model = str(entry.get("alias") or entry.get("model") or "")
            if model and model not in models:
                models.append(model)

    for model in builtin_models:
        model_str = str(model)
        if model_str not in models:
            models.append(model_str)
    return models


def key_for_model(model: str, provider_keys: dict, custom_providers: list[dict]) -> str:
    m = str(model or "").lower()

    for provider in custom_providers or []:
        for entry in iter_custom_provider_model_entries(provider):
            alias = str(entry.get("alias", "") or "").lower()
            model_name = str(entry.get("model", "") or "").lower()
            if m and m in {alias, model_name}:
                return str(provider.get("api_key", "") or "")

    if "glm" in m:
        return provider_keys.get("zhipu", "")
    if "deepseek" in m and "ollama" not in m:
        return provider_keys.get("deepseek", "")
    if "claude" in m:
        return provider_keys.get("anthropic", "")
    if "gpt" in m or "o3" in m or "o1" in m:
        return provider_keys.get("openai", "")
    if "gemini" in m:
        return provider_keys.get("google", "")
    return ""


def sync_llm_top_level_fields_for_model(cfg: GDLAgentConfig, model: str) -> bool:
    if not cfg or not model:
        return False

    model_name = str(model).strip()
    if not model_name:
        return False

    changed = False
    if cfg.llm.model != model_name:
        cfg.llm.model = model_name
        changed = True

    provider = cfg.llm.get_provider_for_model(model_name)
    if provider:
        desired_api_key = str(provider.get("api_key", "") or "")
        desired_api_base = str(provider.get("base_url", "") or "")
        if (cfg.llm.api_key or "") != desired_api_key:
            cfg.llm.api_key = desired_api_key
            changed = True
        if (cfg.llm.api_base or "") != desired_api_base:
            cfg.llm.api_base = desired_api_base
            changed = True

    return changed


def refresh_session_model_keys(
    session_state,
    *,
    config: GDLAgentConfig | None,
    defaults: dict,
    provider_keys: dict,
    custom_providers: list[dict],
    builtin_models: Iterable[str],
) -> None:
    existing_model_keys = dict(session_state.get("model_api_keys", {}))
    refreshed_model_keys = dict(existing_model_keys)
    for model in available_models(config, custom_providers, builtin_models):
        refreshed_model_keys[model] = key_for_model(model, provider_keys, custom_providers) or refreshed_model_keys.get(model, "")

    session_state.model_api_keys = refreshed_model_keys
    session_state.assistant_settings = defaults.get(
        "assistant_settings",
        session_state.get("assistant_settings", ""),
    )


def _load_toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore

    with path.open("rb") as handle:
        return tomllib.load(handle)
