"""
Configuration management for openbrep.

Uses stdlib dataclasses for zero-dependency operation.
Reads from config.toml, environment variables, and CLI overrides.
"""

from __future__ import annotations

import os
import platform
import re
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
    "deepseek-v4-flash",
    "deepseek-v4-pro",
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
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwq-plus",
    "o3",
    "o3-mini",
    "o4-mini",
}


# ── Provider 注册表（LLM 链路单一事实来源）──────────────────────────────────
# 历史上"模型属于哪个 provider"被编码在五处（resolve_api_key / model_to_provider /
# llm.py _setup 的 env 映射 / llm.py _NATIVE_PROVIDERS / llm.py 控制台 URL 表），
# 关键字表互不相同且命名不一致（glm/zhipu/zai），加 provider 要同步改多处。
# 现在收敛为一张表：model_to_provider / resolve_api_key / _setup / native 判定 /
# 控制台 URL 全部从这里取。加新 provider = 加一行。


@dataclass(frozen=True)
class ProviderProfile:
    """一个官方 provider 的全部识别信息。"""

    name: str                     # 规范名（model_to_provider 的返回值）
    prefixes: tuple[str, ...]     # 模型名前缀（小写匹配）
    env_vars: tuple[str, ...]     # 凭据对应的环境变量名（litellm 生态约定）
    provider_key_names: tuple[str, ...]  # provider_keys 里的键名（按优先级）
    console_url: str = ""         # key 控制台（错误引导文案用）
    native_prefix: str = ""       # litellm 原生模型前缀（有则用官方端点，忽略 api_base 覆盖）


PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        name="zhipu",
        prefixes=("glm-",),
        env_vars=("ZAI_API_KEY", "ZHIPU_API_KEY"),
        provider_key_names=("zhipu", "zai", "zai_api_key"),
        console_url="https://open.bigmodel.cn/usercenter/apikeys",
        native_prefix="zai/",
    ),
    ProviderProfile(
        name="deepseek",
        prefixes=("deepseek-",),
        env_vars=("DEEPSEEK_API_KEY",),
        provider_key_names=("deepseek", "deepseek_api_key"),
        console_url="https://platform.deepseek.com/api_keys",
        native_prefix="deepseek/",
    ),
    ProviderProfile(
        name="anthropic",
        prefixes=("claude-",),
        env_vars=("ANTHROPIC_API_KEY",),
        provider_key_names=("anthropic", "claude", "anthropic_api_key"),
        console_url="https://console.anthropic.com/settings/keys",
        native_prefix="claude/",
    ),
    ProviderProfile(
        name="openai",
        prefixes=("gpt-", "o1", "o3", "o4"),
        env_vars=("OPENAI_API_KEY",),
        provider_key_names=("openai", "openai_api_key"),
        console_url="https://platform.openai.com/api-keys",
        native_prefix="openai/",
    ),
    ProviderProfile(
        name="google",
        prefixes=("gemini/", "gemini-"),
        env_vars=("GEMINI_API_KEY",),
        provider_key_names=("google", "gemini", "gemini_api_key"),
        console_url="https://aistudio.google.com/app/apikey",
        native_prefix="gemini/",
    ),
    ProviderProfile(
        name="aliyun",
        prefixes=("qwen-", "qwq-"),
        env_vars=("DASHSCOPE_API_KEY",),
        provider_key_names=("aliyun", "dashscope", "qwen"),
        console_url="https://bailian.console.aliyun.com/",
        native_prefix="",
    ),
    ProviderProfile(
        name="kimi",
        prefixes=("moonshot-",),
        env_vars=("MOONSHOT_API_KEY",),
        provider_key_names=("moonshot", "kimi"),
        console_url="https://platform.moonshot.cn/console/api-keys",
        native_prefix="",
    ),
    ProviderProfile(
        name="ollama",
        prefixes=("ollama/",),
        env_vars=(),
        provider_key_names=("ollama",),
        console_url="",
        native_prefix="ollama/",
    ),
)

# 前缀较长的排前面，避免 "o1" 之类短前缀误伤（先精确匹配）
_PROFILES_BY_PREFIX_LEN = sorted(PROVIDER_PROFILES, key=lambda p: -max(len(x) for x in p.prefixes))


# 官方 provider 的默认端点模板（"从模板添加"数据源；litellm native 路由不依赖它，
# 用户想用统一 [[llm.providers]] 格式配官方 provider 时从这里抄端点即可）
PROVIDER_API_TEMPLATES = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "deepseek": "https://api.deepseek.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


def provider_templates() -> list[dict]:
    """官方 provider 模板目录：预设端点 + 模型清单（数据，不参与解析逻辑）。"""
    templates = []
    for profile in PROVIDER_PROFILES:
        templates.append({
            "name": profile.name,
            "api": PROVIDER_API_TEMPLATES.get(profile.name, ""),
            "api_mode": "anthropic_messages" if profile.name == "anthropic" else "chat_completions",
            "env_vars": list(profile.env_vars),
            "console_url": profile.console_url,
            "models": [
                m for m in ALL_MODELS
                if (mp := provider_profile_for_model(m)) is not None and mp.name == profile.name
            ],
        })
    return templates


def provider_profile_for_model(model: str) -> ProviderProfile | None:
    """按模型名查官方 provider 档案；查不到（自定义/未知）返回 None。"""
    m = (model or "").lower()
    for profile in _PROFILES_BY_PREFIX_LEN:
        if any(m.startswith(prefix) for prefix in profile.prefixes):
            return profile
    return None


def model_to_provider(model: str) -> str:
    profile = provider_profile_for_model(model)
    return profile.name if profile else "custom"


@dataclass(frozen=True)
class ResolvedCredentials:
    """resolve_credentials 的结构化结果：一个模型当前可用的凭据与来源。"""

    provider: str           # provider 规范名（custom 表示自定义/未知）
    api_key: str            # 解析结果（可能为空）
    api_base: str           # 解析结果（可能为空）
    source: str             # custom_provider | provider_keys | top_level | env | none
    console_url: str = ""   # 对应 provider 的 key 控制台（可能为空）


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


# ── ${ENV_VAR} 引用：api_key 等敏感字段可以写 "${VAR}"，运行时从环境变量取值 ──
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def expand_env_ref(value) -> str:
    """整串是 ${VAR} 形式时取环境变量，其余原样返回（去掉首尾空白）。"""
    text = str(value or "").strip()
    match = _ENV_REF_RE.match(text)
    if match:
        return os.environ.get(match.group(1), "")
    return text


def _normalize_api_mode(value) -> str:
    """api_mode 归一化：chat_completions（默认）| anthropic_messages。兼容旧 protocol 写法。"""
    text = str(value or "").strip().lower()
    if text in {"anthropic", "claude", "anthropic_messages", "messages"}:
        return "anthropic_messages"
    return "chat_completions"


def normalize_provider_entry(entry: dict | None) -> dict:
    """把一条 provider 配置归一化为统一注册表格式（Hermes 式）。

    规范键：name / api / api_mode / api_key / default_model / models。
    旧键 base_url→api、protocol(openai|anthropic)→api_mode 在此收敛。
    归一化条目同时携带新旧两组键（api+base_url、api_mode+protocol），
    保证旧读者（pipeline / cli / 前端 API）与新读者都不需要分支。
    """
    raw = dict(entry or {})
    name = str(raw.get("name") or "").strip()
    api = str(raw.get("api") or raw.get("base_url") or "").strip()
    api_mode = _normalize_api_mode(raw.get("api_mode") or raw.get("protocol"))
    models = raw.get("models", []) or []
    if not isinstance(models, list):
        models = [models]
    normalized = dict(raw)
    normalized.update({
        "name": name,
        "api": api,
        "base_url": api,
        "api_mode": api_mode,
        "protocol": "anthropic" if api_mode == "anthropic_messages" else "openai",
        "api_key": str(raw.get("api_key") or "").strip(),
        "models": models,
        # 记录 api/base_url 是否在原始条目里显式出现（即使为空）：
        # 显式为空 = 不回退顶层 api_base；未出现 = 允许顶层兜底（历史语义）。
        # 已归一化过的条目保留原标记，避免二次归一化把"未出现"误判成"显式为空"。
        "_explicit_base": raw.get("_explicit_base", "api" in raw or "base_url" in raw),
    })
    default_model = str(raw.get("default_model") or "").strip()
    if default_model:
        normalized["default_model"] = default_model
    else:
        normalized.pop("default_model", None)
    return normalized


def provider_entry_to_toml(entry: dict) -> dict:
    """保存时只写规范键（新格式），实现"保存即迁移"。"""
    normalized = normalize_provider_entry(entry)
    out: dict = {
        "name": normalized["name"],
        "api": normalized["api"],
        "api_mode": normalized["api_mode"],
        "api_key": normalized["api_key"],
    }
    if normalized.get("default_model"):
        out["default_model"] = normalized["default_model"]
    out["models"] = normalized.get("models", [])
    native_prefix = str(normalized.get("native_prefix") or "").strip()
    if native_prefix:
        out["native_prefix"] = native_prefix
    return out


def _normalize_custom_model_entry(entry) -> Optional[dict[str, str]]:
    if isinstance(entry, dict):
        alias = str(entry.get("alias", "") or entry.get("name", "") or entry.get("model", "") or "").strip()
        model = str(entry.get("model", "") or entry.get("alias", "") or "").strip()
        if not alias and not model:
            return None
        return {
            "alias": alias or model,
            "model": model or alias,
        }

    value = str(entry or "").strip()
    if not value:
        return None
    return {
        "alias": value,
        "model": value,
    }


def iter_custom_provider_model_entries(provider: dict | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    models = (provider or {}).get("models", []) or []
    for entry in models:
        normalized = _normalize_custom_model_entry(entry)
        if normalized:
            entries.append(normalized)
    return entries


def _build_provider_match(provider: dict, entry: dict, raw: dict | None = None) -> dict:
    return {
        # provider 回传原始条目（可变引用），UI/CLI 写入 api_key/api 才能落盘；
        # _normalized 供读取侧取归一化视图（如 _explicit_base 标记）
        "provider": raw if raw is not None else provider,
        "_normalized": provider,
        "provider_name": provider["name"],
        "alias": entry["alias"],
        "model": entry["model"],
        "protocol": provider.get("protocol", "openai"),
        "api_mode": provider.get("api_mode", "chat_completions"),
        "api_key": str(provider.get("api_key", "") or ""),
        "base_url": str(provider.get("api", "") or provider.get("base_url", "") or ""),
        "api": str(provider.get("api", "") or provider.get("base_url", "") or ""),
    }


def _match_within_provider(pair: tuple[dict, dict], rest: str, *, explicit_ref: bool) -> Optional[dict]:
    """provider 已确定时，在 provider 内部解析模型：rest 为空走 default_model/models[0]。"""
    provider, raw = pair
    entries = iter_custom_provider_model_entries(provider)
    rest_lower = rest.strip().lower()
    if rest_lower:
        for entry in entries:
            if rest_lower in {entry["alias"].lower(), entry["model"].lower()}:
                return _build_provider_match(provider, entry, raw)
        if explicit_ref:
            # 显式 provider/model 引用：models 未列出的模型 id 也允许直连
            return _build_provider_match(provider, {"alias": rest.strip(), "model": rest.strip()}, raw)
        return None
    default_model = str(provider.get("default_model") or "").strip()
    if default_model:
        for entry in entries:
            if default_model.lower() in {entry["alias"].lower(), entry["model"].lower()}:
                return _build_provider_match(provider, entry, raw)
        return _build_provider_match(provider, {"alias": default_model, "model": default_model}, raw)
    first = entries[0] if entries else {"alias": provider["name"], "model": provider["name"]}
    return _build_provider_match(provider, first, raw)


def find_custom_provider_match(
    custom_providers: list[dict] | None,
    target_model: str | None,
    *,
    include_provider_name: bool = True,
) -> Optional[dict]:
    target = str(target_model or "").strip()
    if not target:
        return None
    target_lower = target.lower()
    pairs = [(normalize_provider_entry(p), p) for p in custom_providers or [] if isinstance(p, dict)]

    # provider/model 显式引用（如 "opencode-go/deepseek-v4-flash"）：先按 provider 名拆分
    if "/" in target:
        head, _, rest = target.partition("/")
        head_lower = head.strip().lower()
        if head_lower and rest.strip():
            for pair in pairs:
                if pair[0]["name"].lower() == head_lower:
                    return _match_within_provider(pair, rest.strip(), explicit_ref=True)

    for pair in pairs:
        provider = pair[0]
        provider_name = provider["name"]
        if include_provider_name and provider_name and provider_name.lower() == target_lower:
            return _match_within_provider(pair, "", explicit_ref=False)

        for entry in iter_custom_provider_model_entries(provider):
            if target_lower in {entry["alias"].lower(), entry["model"].lower()}:
                return _build_provider_match(provider, entry, pair[1])
    return None


@dataclass
class LLMConfig:
    model: str = "glm-4-flash"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 16384
    timeout: int = 90
    provider_keys: dict[str, str] = field(default_factory=dict)
    custom_providers: list[dict] = field(default_factory=list)
    assistant_settings: str = ""
    extra_body: dict = field(default_factory=dict)

    @property
    def providers(self) -> list[dict]:
        """统一 provider 注册表（Hermes 式）。"""
        return self.custom_providers

    @providers.setter
    def providers(self, value: list[dict]) -> None:
        self.custom_providers = value

    def _find_custom_provider_match(self, model: str | None = None, *, include_provider_name: bool = True) -> Optional[dict]:
        return find_custom_provider_match(
            self.custom_providers,
            model or self.model,
            include_provider_name=include_provider_name,
        )

    def _is_custom_provider_model(self, model: str | None = None) -> bool:
        return self._find_custom_provider_match(model) is not None

    def resolve_api_key(self, model: str | None = None) -> Optional[str]:
        target_model = model or self.model
        custom_match = self._find_custom_provider_match(target_model)
        if custom_match is not None:
            custom_key = expand_env_ref(custom_match.get("api_key", ""))
            return custom_key or None

        profile = provider_profile_for_model(target_model)
        if profile is not None:
            for key in profile.provider_key_names:
                if self.provider_keys.get(key):
                    return expand_env_ref(self.provider_keys[key]) or None

        if self.api_key:
            return expand_env_ref(self.api_key) or None

        # Fallback to environment variables（保持历史优先级顺序，新增的放后面）
        for name in [
            "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY",
        ]:
            val = os.environ.get(name)
            if val:
                return val
        return None

    def resolve_api_base(self, model: str | None = None) -> Optional[str]:
        target_model = model or self.model
        custom_match = self._find_custom_provider_match(target_model)
        if custom_match is not None:
            provider = custom_match.get("_normalized") or custom_match.get("provider") or {}
            has_explicit_base = bool(provider.get("_explicit_base")) if isinstance(provider, dict) else False
            custom_base = str(custom_match.get("api", "") or custom_match.get("base_url", "") or "").strip()
            if custom_base:
                return custom_base
            # Explicitly configured but empty api/base_url means "do not fallback"
            if has_explicit_base:
                return None
            # If api/base_url key is absent in custom provider, allow top-level fallback

        if self.api_base:
            return self.api_base
        return None

    def resolve_credentials(self, model: str | None = None) -> "ResolvedCredentials":
        """一个模型当前可用凭据的统一解析（单一事实来源）。

        读取侧全部走这里：settings 展示、model_available、连接测试、pipeline 生成。
        解析顺序：custom_providers 条目 → provider_keys（注册表）→ 顶层 → 环境变量。
        """
        target_model = model or self.model
        custom_match = self._find_custom_provider_match(target_model)
        if custom_match is not None:
            key = expand_env_ref(custom_match.get("api_key", ""))
            base = str(custom_match.get("api", "") or custom_match.get("base_url", "") or "").strip() or (self.api_base or "")
            return ResolvedCredentials(
                provider=str(custom_match.get("provider_name", "custom") or "custom"),
                api_key=key,
                api_base=base,
                source="custom_provider",
                console_url="",
            )

        profile = provider_profile_for_model(target_model)
        if profile is not None:
            for key_name in profile.provider_key_names:
                if self.provider_keys.get(key_name):
                    return ResolvedCredentials(
                        provider=profile.name,
                        api_key=expand_env_ref(self.provider_keys[key_name]),
                        api_base=self.api_base or "",
                        source="provider_keys",
                        console_url=profile.console_url,
                    )
            if self.api_key:
                return ResolvedCredentials(
                    provider=profile.name,
                    api_key=expand_env_ref(self.api_key),
                    api_base=self.api_base or "",
                    source="top_level",
                    console_url=profile.console_url,
                )
            for env_name in profile.env_vars:
                val = os.environ.get(env_name)
                if val:
                    return ResolvedCredentials(
                        provider=profile.name,
                        api_key=val,
                        api_base=self.api_base or "",
                        source="env",
                        console_url=profile.console_url,
                    )
            return ResolvedCredentials(
                provider=profile.name,
                api_key="",
                api_base=self.api_base or "",
                source="none",
                console_url=profile.console_url,
            )

        # 未知/自定义模型：只能依赖顶层与环境变量
        if self.api_key:
            return ResolvedCredentials(
                provider="custom", api_key=self.api_key,
                api_base=self.api_base or "", source="top_level",
            )
        env_key = self.resolve_api_key(target_model)
        return ResolvedCredentials(
            provider="custom",
            api_key=env_key or "",
            api_base=self.api_base or "",
            source="env" if env_key else "none",
        )

    def get_provider_for_model(self, model_name: str) -> dict:
        custom_match = self._find_custom_provider_match(model_name)
        if custom_match:
            return {
                "api_key": custom_match.get("api_key", ""),
                "base_url": custom_match.get("base_url", ""),
                "api": custom_match.get("api", ""),
                "protocol": custom_match.get("protocol", "openai"),
                "api_mode": custom_match.get("api_mode", "chat_completions"),
                "provider_name": custom_match.get("provider_name", ""),
                "alias": custom_match.get("alias", model_name),
                "model": custom_match.get("model", model_name),
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
    mode: str = "mock"
    path: Optional[str] = None
    timeout: int = 60


@dataclass
class GDLAgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    compiler: CompilerConfig = field(default_factory=CompilerConfig)
    knowledge_dir: str = "./knowledge"
    user_knowledge_dir: str = "./user_knowledge"
    templates_dir: str = "./templates"
    src_dir: str = "./src"
    output_dir: str = "./output"
    recent_projects: list[str] = field(default_factory=list)

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
                if isinstance(llm_data, dict) and isinstance(llm_data.get("api_base"), str):
                    _norm_base = llm_data["api_base"].rstrip("/")
                    if _norm_base and not _norm_base.endswith("/v1"):
                        llm_data["api_base"] = _norm_base + "/v1"
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
            # [llm] default 是 model 的 Hermes 式别名（default 优先仅在 model 缺失时）
            if "model" not in llm_data and llm_data.get("default"):
                llm_data = dict(llm_data)
                llm_data["model"] = llm_data["default"]
            # 统一注册表：[[llm.providers]] 为规范格式，[[llm.custom_providers]] 为旧格式，合并归一化
            for key in ("providers", "custom_providers"):
                raw = llm_data.get(key, []) or []
                if isinstance(raw, list):
                    custom_providers.extend(raw)

        llm_cfg = pick(LLMConfig, llm_data)
        llm_cfg.custom_providers = [normalize_provider_entry(p) for p in custom_providers]

        raw_recent_projects = data.get("recent_projects", [])
        if not isinstance(raw_recent_projects, list):
            raw_recent_projects = []
        compiler_data = data.get("compiler", {})
        if not isinstance(compiler_data, dict):
            compiler_data = {}
        compiler_cfg = pick(CompilerConfig, compiler_data)
        if compiler_cfg.mode not in {"mock", "lp"}:
            compiler_cfg.mode = "mock"
        if "mode" not in compiler_data and compiler_cfg.path:
            compiler_cfg.mode = "lp"

        return cls(
            llm=llm_cfg,
            agent=pick(AgentConfig, data.get("agent", {})),
            compiler=compiler_cfg,
            knowledge_dir=data.get("knowledge_dir", "./knowledge"),
            user_knowledge_dir=data.get("user_knowledge_dir", "./user_knowledge"),
            templates_dir=data.get("templates_dir", "./templates"),
            src_dir=data.get("src_dir", "./src"),
            output_dir=data.get("output_dir", "./output"),
            recent_projects=[
                str(path)
                for path in raw_recent_projects
                if isinstance(path, str) and path.strip()
            ],
        )

    def get_available_models(self) -> list[str]:
        custom_models = []
        for p in self.llm.custom_providers:
            for entry in iter_custom_provider_model_entries(p):
                alias = entry["alias"]
                if alias not in custom_models:
                    custom_models.append(alias)
        return custom_models + [m for m in ALL_MODELS if m not in custom_models]

    def ensure_dirs(self):
        for d in [self.knowledge_dir, self.user_knowledge_dir, self.templates_dir, self.src_dir, self.output_dir]:
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
                # 统一注册表：保存即迁移，只写规范键（api/api_mode），不再写 custom_providers
                "providers": [provider_entry_to_toml(p) for p in self.llm.providers],
                "assistant_settings": self.llm.assistant_settings or "",
            },
            "agent": {
                "max_iterations": self.agent.max_iterations,
                "validate_xml": self.agent.validate_xml,
                "diff_check": self.agent.diff_check,
                "auto_version": self.agent.auto_version,
            },
            "compiler": {
                "mode": self.compiler.mode,
                "path": self.compiler.path or "",
                "timeout": self.compiler.timeout,
            },
            "knowledge_dir": self.knowledge_dir,
            "user_knowledge_dir": self.user_knowledge_dir,
            "templates_dir": self.templates_dir,
            "src_dir": self.src_dir,
            "output_dir": self.output_dir,
            "recent_projects": self.recent_projects,
        }
        Path(config_path).write_text(toml.dumps(data), encoding="utf-8")

    def to_toml_string(self) -> str:
        lines = [
            "# openbrep configuration", "",
            "[llm]", f'model = "{self.llm.model}"',
            f'# api_key = "your-key-here"',
        ]
        if self.llm.assistant_settings:
            lines.append('assistant_settings = """' + self.llm.assistant_settings + '"""')
        else:
            lines.append('# assistant_settings = """告诉我你的使用场景、经验水平，或你希望我怎么协助你"""')
        if self.llm.api_base:
            lines.append(f'api_base = "{self.llm.api_base}"')
        lines += [
            f"temperature = {self.llm.temperature}", f"max_tokens = {self.llm.max_tokens}",
            "", "[agent]", f"max_iterations = {self.agent.max_iterations}",
            f"validate_xml = {str(self.agent.validate_xml).lower()}",
            f"diff_check = {str(self.agent.diff_check).lower()}",
            f"auto_version = {str(self.agent.auto_version).lower()}",
            "", "[compiler]",
            f'mode = "{self.compiler.mode}"',
        ]
        if self.compiler.path:
            lines.append(f'path = "{self.compiler.path}"')
        else:
            lines.append('# path = "/path/to/LP_XMLConverter"')
        lines += [
            f"timeout = {self.compiler.timeout}", "",
            f'knowledge_dir = "{self.knowledge_dir}"',
            f'user_knowledge_dir = "{self.user_knowledge_dir}"',
            f'templates_dir = "{self.templates_dir}"',
            f'src_dir = "{self.src_dir}"', f'output_dir = "{self.output_dir}"',
        ]
        if self.recent_projects:
            lines += ["", "recent_projects = ["]
            for path in self.recent_projects:
                lines.append(f'  "{path}",')
            lines.append("]")
        return "\n".join(lines) + "\n"


def _nested_set(d: dict, key: str, value):
    parts = key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value
