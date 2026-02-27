"""
Configuration management for openbrep.

Uses stdlib dataclasses for zero-dependency operation.
Reads from config.toml, environment variables, and CLI overrides.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

_CONVERTER_SEARCH_PATHS = {
    "Darwin": ["/Applications/GRAPHISOFT/ArchiCAD {v}/LP_XMLConverter"],
    "Windows": [r"C:\Program Files\GRAPHISOFT\ArchiCAD {v}\LP_XMLConverter.exe"],
    "Linux": ["/opt/GRAPHISOFT/ArchiCAD{v}/LP_XMLConverter"],
}
_AC_VERSIONS = ["29", "28", "27", "26", "25"]


def _auto_detect_converter() -> Optional[str]:
    env_path = os.environ.get("CONVERTER_PATH")
    if env_path and Path(env_path).is_file():
        return env_path
    which = shutil.which("LP_XMLConverter")
    if which:
        return which
    system = platform.system()
    for tmpl in _CONVERTER_SEARCH_PATHS.get(system, []):
        for ver in _AC_VERSIONS:
            path = tmpl.format(v=ver)
            if Path(path).is_file():
                return path
    return None


@dataclass
class LLMConfig:
    model: str = "glm-4-flash"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 4096
    provider_keys: dict[str, str] = field(default_factory=dict)
    custom_providers: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        # Check provider_keys first
        model_lower = self.model.lower()
        if "glm" in model_lower:
            for key in ["zhipu", "zai", "zai_api_key"]:
                if key in self.provider_keys:
                    return self.provider_keys[key]
        elif "deepseek" in model_lower:
            for key in ["deepseek", "deepseek_api_key"]:
                if key in self.provider_keys:
                    return self.provider_keys[key]
        elif "claude" in model_lower:
            for key in ["anthropic", "claude", "anthropic_api_key"]:
                if key in self.provider_keys:
                    return self.provider_keys[key]
        elif "gemini" in model_lower:
            for key in ["google", "gemini", "gemini_api_key"]:
                if key in self.provider_keys:
                    return self.provider_keys[key]

        # Check custom providers by exact configured model name
        for provider_name, provider_cfg in self.custom_providers.items():
            if model_lower == str(provider_cfg.get("model", "")).lower():
                _k = provider_cfg.get("api_key")
                if _k:
                    return str(_k)
                if provider_name in self.provider_keys:
                    return self.provider_keys[provider_name]

        # Fallback to environment variables
        for name in ["ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"]:
            val = os.environ.get(name)
            if val:
                return val
        return None


@dataclass
class AgentConfig:
    max_iterations: int = 5
    validate_xml: bool = True
    diff_check: bool = True
    auto_version: bool = True


@dataclass
class CompilerConfig:
    path: Optional[str] = None
    timeout: int = 60


@dataclass
class GDLAgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    compiler: CompilerConfig = field(default_factory=CompilerConfig)
    knowledge_dir: str = "./knowledge"
    templates_dir: str = "./templates"
    src_dir: str = "./src"
    output_dir: str = "./output"

    @classmethod
    def load(cls, config_path: Optional[str] = None, **overrides) -> GDLAgentConfig:
        data: dict = {}
        if config_path is None:
            config_path = os.environ.get("GDL_AGENT_CONFIG", "config.toml")
        path = Path(config_path)
        if path.exists() and tomllib is not None:
            with open(path, "rb") as f:
                data = tomllib.load(f)
                llm_data = data.get("llm", {}) if isinstance(data, dict) else {}
                if isinstance(llm_data, dict):
                    provider_keys = llm_data.get("provider_keys", {})
                    for provider_name, provider_cfg in llm_data.items():
                        if provider_name in {"provider_keys", "model", "api_key", "api_base", "temperature", "max_tokens", "custom_providers"}:
                            continue
                        if not isinstance(provider_cfg, dict):
                            continue
                        custom_model = str(provider_cfg.get("model", "") or "")
                        if not custom_model:
                            continue
                        if str(llm_data.get("model", "") or "") == custom_model:
                            custom_base = str(provider_cfg.get("base_url", "") or "")
                            if custom_base and not str(llm_data.get("api_base", "") or ""):
                                llm_data["api_base"] = custom_base
                            if isinstance(provider_keys, dict):
                                custom_key = str(provider_keys.get(provider_name, "") or "")
                                if custom_key and not str(llm_data.get("api_key", "") or ""):
                                    llm_data["api_key"] = custom_key
                            if isinstance(llm_data.get("api_base"), str):
                                _norm_base = llm_data["api_base"].rstrip("/")
                                if _norm_base and not _norm_base.endswith("/v1"):
                                    llm_data["api_base"] = _norm_base + "/v1"
                            break
        for key, val in overrides.items():
            if val is not None:
                _nested_set(data, key, val)
        config = cls._from_dict(data)
        if not config.compiler.path:
            detected = _auto_detect_converter()
            if detected:
                config.compiler.path = detected
        return config

    @classmethod
    def _from_dict(cls, data: dict) -> GDLAgentConfig:
        def pick(klass, d):
            return klass(**{k: v for k, v in d.items() if k in klass.__dataclass_fields__})

        llm_data = data.get("llm", {})
        custom_providers: dict[str, dict[str, str]] = {}
        provider_keys = llm_data.get("provider_keys", {})
        if isinstance(llm_data, dict):
            for provider_name, provider_cfg in llm_data.items():
                if provider_name in {"provider_keys", "model", "api_key", "api_base", "temperature", "max_tokens", "custom_providers"}:
                    continue
                if isinstance(provider_cfg, dict):
                    custom_providers[provider_name] = {
                        "base_url": str(provider_cfg.get("base_url", "") or ""),
                        "model": str(provider_cfg.get("model", "") or ""),
                        "api_key": str(provider_keys.get(provider_name, "") or ""),
                    }

        llm_cfg = pick(LLMConfig, llm_data)
        llm_cfg.custom_providers = custom_providers

        return cls(
            llm=llm_cfg,
            agent=pick(AgentConfig, data.get("agent", {})),
            compiler=pick(CompilerConfig, data.get("compiler", {})),
            knowledge_dir=data.get("knowledge_dir", "./knowledge"),
            templates_dir=data.get("templates_dir", "./templates"),
            src_dir=data.get("src_dir", "./src"),
            output_dir=data.get("output_dir", "./output"),
        )

    def ensure_dirs(self):
        for d in [self.knowledge_dir, self.templates_dir, self.src_dir, self.output_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

    def to_toml_string(self) -> str:
        lines = [
            "# openbrep configuration", "",
            "[llm]", f'model = "{self.llm.model}"',
            f'# api_key = "your-key-here"',
        ]
        if self.llm.api_base:
            lines.append(f'api_base = "{self.llm.api_base}"')
        lines += [
            f"temperature = {self.llm.temperature}", f"max_tokens = {self.llm.max_tokens}",
            "", "[agent]", f"max_iterations = {self.agent.max_iterations}",
            f"validate_xml = {str(self.agent.validate_xml).lower()}",
            f"diff_check = {str(self.agent.diff_check).lower()}",
            f"auto_version = {str(self.agent.auto_version).lower()}",
            "", "[compiler]",
        ]
        if self.compiler.path:
            lines.append(f'path = "{self.compiler.path}"')
        else:
            lines.append('# path = "/path/to/LP_XMLConverter"')
        lines += [
            f"timeout = {self.compiler.timeout}", "",
            f'knowledge_dir = "{self.knowledge_dir}"', f'templates_dir = "{self.templates_dir}"',
            f'src_dir = "{self.src_dir}"', f'output_dir = "{self.output_dir}"',
        ]
        return "\n".join(lines) + "\n"


def _nested_set(d: dict, key: str, value):
    parts = key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value
