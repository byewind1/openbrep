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


ALL_MODELS = [
    # Zhipu GLM
    "glm-5",
    "glm-4-flash",
    "glm-4-air",
    "glm-4-plus",
    "glm-4.6",
    "glm-4.6v",
    "glm-4.7",
    # DeepSeek
    "deepseek-chat",
    "deepseek-reasoner",
    # Alibaba Qwen
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwq-plus",
    "qwen-vl-plus",
    # Moonshot Kimi
    "moonshot-v1-8k",
    "moonshot-v1-32k",
    "moonshot-v1-128k",
    # OpenAI
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-5.4",
    "gpt-5.2-codex",
    "gpt-4o",
    "gpt-4o-mini",
    "o3",
    "o3-mini",
    "o4-mini",
    # Anthropic Claude
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    # Google Gemini
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro",
    # Ollama
    "ollama/qwen2.5:14b",
    "ollama/qwen3:8b",
    "ollama/deepseek-coder-v2:16b",
]

VISION_MODELS = {
    "qwen-vl-plus",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro",
}

REASONING_MODELS = {
    "deepseek-reasoner",
    "qwq-plus",
    "o3",
    "o3-mini",
    "o4-mini",
}


def model_to_provider(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("glm-"):
        return "zhipu"
    if m.startswith("deepseek-"):
        return "deepseek"
    if m.startswith("claude-"):
        return "anthropic"
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    if m.startswith("gemini/") or m.startswith("gemini-"):
        return "google"
    if m.startswith("qwen-") or m.startswith("qwq-"):
        return "aliyun"
    if m.startswith("moonshot-"):
        return "kimi"
    if m.startswith("ollama/"):
        return "ollama"
    return "custom"


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
    custom_providers: list[dict] = field(default_factory=list)

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

        # Check custom providers by configured models list
        for provider in self.custom_providers:
            models = provider.get("models", []) or []
            if any(model_lower == str(m).lower() for m in models):
                _k = provider.get("api_key")
                if _k:
                    return str(_k)

        # Fallback to environment variables
        for name in ["ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"]:
            val = os.environ.get(name)
            if val:
                return val
        return None

    def get_provider_for_model(self, model_name: str) -> dict:
        for provider in self.custom_providers:
            if model_name in (provider.get("models", []) or []):
                return {
                    "api_key": provider.get("api_key", ""),
                    "base_url": provider.get("base_url", ""),
                    "protocol": provider.get("protocol", "openai"),
                }
        return {}



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
        example_path = None
        for name in ["config.toml.example", "config.example.toml"]:
            p = Path(name)
            if p.exists():
                example_path = p
                break

        # 自动从 example 复制，首次运行时引导用户
        if not path.exists() and example_path and example_path.exists():
            shutil.copy(example_path, path)
            print("=" * 60)
            print("📋 已自动生成 config.toml，请编辑填入你的 API Key：")
            print(f"   {path.absolute()}")
            print("=" * 60)

        if path.exists() and tomllib is not None:
            with open(path, "rb") as f:
                data = tomllib.load(f)
                llm_data = data.get("llm", {}) if isinstance(data, dict) else {}
                if isinstance(llm_data, dict):
                    custom_providers = llm_data.get("custom_providers", []) or []
                    for provider in custom_providers:
                        models = provider.get("models", []) or []
                        if str(llm_data.get("model", "") or "") in models:
                            custom_base = str(provider.get("base_url", "") or "")
                            if custom_base and not str(llm_data.get("api_base", "") or ""):
                                llm_data["api_base"] = custom_base
                            custom_key = str(provider.get("api_key", "") or "")
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
        custom_providers = []
        if isinstance(llm_data, dict):
            raw_custom = llm_data.get("custom_providers", []) or []
            if isinstance(raw_custom, list):
                custom_providers = raw_custom

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

    def get_available_models(self) -> list[str]:
        custom_models = []
        for p in self.llm.custom_providers:
            for m in p.get("models", []) or []:
                if m not in custom_models:
                    custom_models.append(m)
        return custom_models + [m for m in ALL_MODELS if m not in custom_models]

    def ensure_dirs(self):
        for d in [self.knowledge_dir, self.templates_dir, self.src_dir, self.output_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

    def save(self, config_path: str = "config.toml") -> None:
        """将当前配置写回 config.toml"""
        import toml
        data = {
            "llm": {
                "model": self.llm.model,
                "api_key": self.llm.api_key or "",
                "api_base": self.llm.api_base or "",
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
                "provider_keys": self.llm.provider_keys,
            },
            "compiler": {
                "path": self.compiler.path or "",
                "work_dir": self.output_dir,
            }
        }
        Path(config_path).write_text(toml.dumps(data), encoding="utf-8")

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
