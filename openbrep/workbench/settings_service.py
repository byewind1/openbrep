from __future__ import annotations

import copy
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from openbrep.config import ALL_MODELS, GDLAgentConfig, iter_custom_provider_model_entries, model_to_provider


def load_workbench_config(config_path: Path) -> GDLAgentConfig:
    if config_path.exists():
        return GDLAgentConfig.load(str(config_path))
    return GDLAgentConfig()


def resolve_workbench_config_path(config_path: str | Path | None = None) -> Path:
    if config_path:
        return Path(config_path)
    env_path = str(os.environ.get("GDL_AGENT_CONFIG") or "").strip()
    if env_path:
        return Path(env_path)
    cwd = Path.cwd()
    try:
        common_dir = subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if common_dir:
            common_path = Path(common_dir)
            if not common_path.is_absolute():
                common_path = cwd / common_path
            if common_path.name == ".git":
                main_config = common_path.parent / "config.toml"
                if main_config.exists():
                    return main_config
    except Exception:
        pass
    return cwd / "config.toml"


def save_workbench_config(config: GDLAgentConfig, config_path: Path) -> None:
    config.save(str(config_path))


def apply_llm_credentials_to_config(
    config: GDLAgentConfig,
    *,
    model: str,
    api_key: str,
    api_base: str,
) -> None:
    custom_match = config.llm._find_custom_provider_match(model)
    if custom_match is not None:
        provider = custom_match.get("provider")
        if isinstance(provider, dict):
            provider["api_key"] = api_key
            provider["base_url"] = api_base
        config.llm.api_key = api_key
        config.llm.api_base = api_base
        return

    provider_name = model_to_provider(model)
    resolved_api_key = api_key
    if provider_name and provider_name != "custom":
        if api_key:
            config.llm.provider_keys[provider_name] = api_key
        else:
            resolved_api_key = str(config.llm.provider_keys.get(provider_name, "") or "")
    config.llm.api_key = resolved_api_key
    config.llm.api_base = api_base


def llm_model_groups(config: GDLAgentConfig) -> dict[str, list[dict[str, Any]]]:
    custom = custom_model_options(config)
    custom_ids = {option["id"] for option in custom}
    official = [
        {
            "id": model,
            "label": model,
            "kind": "official",
            "provider": model_to_provider(model),
            "has_api_key": bool(config.llm.resolve_api_key(model)),
        }
        for model in ALL_MODELS
        if model not in custom_ids
    ]
    return {"custom": custom, "official": official}


def custom_model_options(config: GDLAgentConfig) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider in config.llm.custom_providers or []:
        provider_name = str(provider.get("name", "") or "").strip() or "custom"
        protocol = str(provider.get("protocol", "openai") or "openai")
        api_base = str(provider.get("base_url", "") or "")
        has_api_key = bool(str(provider.get("api_key", "") or "").strip())
        for entry in iter_custom_provider_model_entries(provider):
            alias = entry["alias"]
            if alias in seen:
                continue
            seen.add(alias)
            options.append({
                "id": alias,
                "label": alias,
                "kind": "custom",
                "provider": provider_name,
                "target_model": entry["model"],
                "protocol": protocol,
                "api_base": api_base,
                "has_api_key": has_api_key,
            })
    return options


class WorkbenchSettingsService:
    def __init__(
        self,
        session: Any,
        *,
        llm_adapter_factory: Callable[[Any], Any],
    ) -> None:
        self.session = session
        self.llm_adapter_factory = llm_adapter_factory

    def compiler_settings(self) -> dict[str, str]:
        return {
            "mode": self.session.compiler_mode,
            "converter_path": self.session.converter_path,
            "output_dir": self.session.output_dir,
        }

    def update_compiler_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode") or self.session.compiler_mode).strip().lower()
        if mode not in {"mock", "lp"}:
            return {"ok": False, "error": f"Unsupported compiler mode: {mode}"}
        self.session.compiler_mode = mode
        self.session.converter_path = str(body.get("converter_path") or "").strip()
        self.session.output_dir = str(body.get("output_dir") or "").strip()
        self.session.config.compiler.mode = self.session.compiler_mode
        self.session.config.compiler.path = self.session.converter_path
        self.session.config.output_dir = self.session.output_dir or "./output"
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "compiler": self.compiler_settings()}

    def llm_settings(self) -> dict[str, Any]:
        groups = llm_model_groups(self.session.config)
        model_options = groups["custom"] + groups["official"]
        models = [option["id"] for option in model_options]
        return {
            "model": self.session.llm_model,
            "models": models,
            "model_options": model_options,
            "model_groups": groups,
            "api_key": self.session.llm_api_key,
            "api_base": self.session.llm_api_base,
            "max_retries": self.session.max_retries,
            "assistant_settings": self.session.assistant_settings,
        }

    def reload_runtime_settings(self) -> dict[str, Any]:
        self.session.config = load_workbench_config(self.session.config_path)
        self.session.compiler_mode = (
            self.session.config.compiler.mode if self.session.config.compiler.mode in {"mock", "lp"} else "mock"
        )
        self.session.converter_path = self.session.config.compiler.path or ""
        self.session.output_dir = "" if self.session.config.output_dir in {"", "./output"} else self.session.config.output_dir
        self.session.llm_model = self.session.config.llm.model
        self.session.llm_api_key = self.session.config.llm.resolve_api_key() or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base() or ""
        self.session.max_retries = self.session.config.agent.max_iterations
        self.session.assistant_settings = self.session.config.llm.assistant_settings or ""
        self.session.recent_project_paths = list(self.session.config.recent_projects or [])
        return {
            "ok": True,
            "compiler": self.compiler_settings(),
            "llm": self.llm_settings(),
        }

    def update_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.session.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required."}
        self.session.llm_model = model
        self.session.llm_api_key = str(body.get("api_key") or "").strip()
        self.session.llm_api_base = str(body.get("api_base") or "").strip()
        self.session.assistant_settings = str(body.get("assistant_settings") or "")
        try:
            self.session.max_retries = max(1, min(10, int(body.get("max_retries") or self.session.max_retries)))
        except (TypeError, ValueError):
            self.session.max_retries = 5

        self.session.config.llm.model = self.session.llm_model
        self.session.config.llm.assistant_settings = self.session.assistant_settings
        self.session.config.agent.max_iterations = self.session.max_retries
        apply_llm_credentials_to_config(
            self.session.config,
            model=self.session.llm_model,
            api_key=self.session.llm_api_key,
            api_base=self.session.llm_api_base,
        )
        self.session.llm_api_key = self.session.config.llm.resolve_api_key(self.session.llm_model) or ""
        self.session.llm_api_base = self.session.config.llm.resolve_api_base(self.session.llm_model) or ""
        save_workbench_config(self.session.config, self.session.config_path)
        return {"ok": True, "llm": self.llm_settings()}

    def test_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.session.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required.", "category": "llm_configuration"}

        test_config = copy.deepcopy(self.session.config)
        test_config.llm.model = model
        test_config.llm.assistant_settings = str(body.get("assistant_settings") or self.session.assistant_settings)
        test_config.llm.max_tokens = min(test_config.llm.max_tokens, 16)
        test_config.llm.timeout = min(test_config.llm.timeout, 20)
        apply_llm_credentials_to_config(
            test_config,
            model=model,
            api_key=str(body.get("api_key") or "").strip(),
            api_base=str(body.get("api_base") or "").strip(),
        )

        start = time.perf_counter()
        try:
            response = self.llm_adapter_factory(test_config.llm).generate(
                [{"role": "user", "content": "Reply with OK."}],
                temperature=0,
                max_tokens=8,
                timeout=20,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc) or exc.__class__.__name__,
                "category": "llm_configuration",
                "model": model,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }

        return {
            "ok": True,
            "message": "LLM connection OK",
            "model": response.model or model,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
