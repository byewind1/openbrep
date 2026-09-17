"""只读解析本机 Codex 配置（双入口的 ``local`` 入口，2026-09-17）。

用户已经用 Codex CLI 配好了模型 / provider / 凭据（cc-switch 之类），
OpenBrep 在 ``local`` 入口只做消费者：读 ``config.toml`` 与其中的模型目录，
把可选项交给用户挑，认证与凭据管理一概不接管。

硬不变量：

- **只读**：不建目录、不写文件、不迁移、不修配置；``~/.codex`` 是用户的地盘。
- **不回显秘密**：``config.toml`` 里的 bearer token / api key / env key 只用于
  判定「这条 provider 是否自带凭据」的布尔值，值本身绝不进入返回值、日志或
  异常文本。
- **永不抛**：解析失败一律降级为稳定 code + 稳定文案，调用方拿到的是数据。

模型来源优先级（同一份 home 内）：
1. ``model_catalog_json`` 指向的目录文件（用户显式声明的模型表，例如 cc-switch）；
2. ``config.toml`` 的 ``model`` + provider ``models``（用户显式声明的默认模型）；
3. ``models_cache.json``（Codex CLI 自己缓存的账户目录，仅默认 provider 使用）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # Python ≥3.11 走标准库；旧环境与 openbrep/config.py 一致退到 tomli
    import tomllib
except ModuleNotFoundError:  # pragma: no cover —— 依赖环境决定
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

CONFIG_FILENAME = "config.toml"
AUTH_FILENAME = "auth.json"
CATALOG_CACHE_FILENAME = "models_cache.json"
CATALOG_JSON_KEY = "model_catalog_json"

STATE_NO_CLI = "no_cli"
STATE_UNCONFIGURED = "unconfigured"
STATE_SIGNED_OUT = "signed_out"
STATE_READY = "ready"
STATE_ERROR = "error"

# 目录/文案解析上限：配置是用户自己的文件，但不能让异常内容撑爆 UI 与日志。
_MAX_MODELS = 64
_MAX_TEXT = 120
_MAX_LABEL = 80
_MAX_EFFORTS = 16
_MAX_EFFORT_DESC = 120

# 默认 provider（含缺省）= ChatGPT 订阅，凭据来自 auth.json。
_OPENAI_PROVIDER_IDS = frozenset({"", "openai"})
# provider 自带凭据的字段名（值绝不外泄，只判定是否非空）
_CREDENTIAL_KEYS = ("experimental_bearer_token", "api_key", "env_key", "api_key_env")


def read_local_codex_config(codex_home: str | Path) -> dict[str, Any]:
    """只读解析一份本机 Codex home；返回结构化数据，永不抛异常。"""
    home = Path(codex_home).expanduser()
    config_path = home / CONFIG_FILENAME
    result: dict[str, Any] = {
        "ok": False,
        "code": "config_missing",
        "home": str(home),
        "config_path": str(config_path),
        "config_present": False,
        "chatgpt_auth": (home / AUTH_FILENAME).is_file(),
        "provider_id": "",
        "provider_label": "",
        "provider_credential": False,
        "auth_required": True,
        "model": "",
        "models": [],
        "models_source": "",
    }
    if tomllib is None:  # pragma: no cover —— 环境缺 TOML 解析器
        result["code"] = "config_unreadable"
        return result
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return result
    except (OSError, UnicodeDecodeError):
        result["code"] = "config_unreadable"
        return result
    try:
        config = tomllib.loads(raw_text)
    except Exception:  # noqa: BLE001 —— TOML 语法错误必须降级，绝不冒泡
        result["config_present"] = True
        result["code"] = "config_unreadable"
        return result
    if not isinstance(config, dict):
        result["config_present"] = True
        result["code"] = "config_unreadable"
        return result

    result["config_present"] = True
    provider_id, provider_entry = _resolve_provider(config)
    result["provider_id"] = provider_id
    result["provider_label"] = _provider_label(provider_id, provider_entry)
    result["provider_credential"] = _provider_has_credential(provider_entry)
    result["auth_required"] = _auth_required(provider_id, provider_entry)
    result["model"] = _clean_text(config.get("model"), _MAX_TEXT)

    models, source = _resolve_models(home, config, provider_id=provider_id)
    result["models"] = models
    result["models_source"] = source
    result["ok"] = True
    result["code"] = "" if models else "config_no_models"
    return result


def local_entry_verdict(data: dict[str, Any], *, cli_available: bool) -> dict[str, Any]:
    """三态判定 + 稳定文案：没装 CLI / 装了没登录（或没配置）/ 可用。

    返回值只含 ``state`` / ``code`` / ``error`` / ``connected`` 四个稳定字段。
    """
    if not cli_available:
        return {
            "state": STATE_NO_CLI,
            "code": "codex_cli_unavailable",
            "error": (
                "未检测到 Codex CLI。请先在终端安装 Codex CLI 并运行一次"
                "（或在设置中指定 codex 可执行文件路径）。"
            ),
            "connected": False,
        }
    if not data.get("ok"):
        if data.get("code") == "config_unreadable":
            return {
                "state": STATE_ERROR,
                "code": "codex_config_unreadable",
                "error": "无法读取本机 Codex 配置（config.toml）或其中的模型目录，请检查文件后重试。",
                "connected": False,
            }
        return {
            "state": STATE_UNCONFIGURED,
            "code": "codex_config_missing",
            "error": (
                "未检测到本机 Codex 配置。请先在终端运行 codex login 完成 Codex CLI 初始化，"
                "再回到这里刷新。"
            ),
            "connected": False,
        }
    if not data.get("models"):
        return {
            "state": STATE_UNCONFIGURED,
            "code": "codex_config_no_models",
            "error": (
                "本机 Codex 配置里没有可用模型。请在终端用 Codex CLI 选择一次模型，"
                "或在 config.toml 中设置 model。"
            ),
            "connected": False,
        }
    if data.get("auth_required") and not (
        data.get("chatgpt_auth") or data.get("provider_credential")
    ):
        return {
            "state": STATE_SIGNED_OUT,
            "code": "not_signed_in",
            "error": (
                "未检测到 Codex 登录态。请在终端运行 codex login，或为本机 Codex 配置里的"
                " provider 填好凭据后重试（OpenBrep 不接管认证）。"
            ),
            "connected": False,
        }
    return {"state": STATE_READY, "code": "", "error": "", "connected": True}


# ── 内部：provider 判定 ─────────────────────────────────────────────────────


def _resolve_provider(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider_id = _clean_text(config.get("model_provider"), _MAX_TEXT)
    providers = config.get("model_providers")
    entry: dict[str, Any] = {}
    if isinstance(providers, dict):
        candidate = providers.get(provider_id) if provider_id else None
        if candidate is None and not provider_id and len(providers) == 1:
            # 没有 model_provider 但只声明了一个 provider：取它作为事实来源
            provider_id, candidate = next(iter(providers.items()))
            provider_id = _clean_text(provider_id, _MAX_TEXT)
        if isinstance(candidate, dict):
            entry = candidate
    return provider_id, entry


def _provider_label(provider_id: str, entry: dict[str, Any]) -> str:
    name = _clean_text(entry.get("name"), _MAX_LABEL)
    if name:
        return name
    return provider_id or "openai"


def _provider_has_credential(entry: dict[str, Any]) -> bool:
    for key in _CREDENTIAL_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _auth_required(provider_id: str, entry: dict[str, Any]) -> bool:
    """这条 provider 是否需要 ChatGPT 登录态（auth.json）。

    默认 provider（openai / 缺省）凭据来自登录态；自定义 provider 自带 base_url
    且显式关闭 openai 认证时算自足——但解析不出结论时一律按「需要登录」处理。
    """
    if provider_id in _OPENAI_PROVIDER_IDS:
        return True
    if entry.get("requires_openai_auth") is False:
        return False
    return True


# ── 内部：模型目录 ─────────────────────────────────────────────────────────


def _resolve_models(
    home: Path,
    config: dict[str, Any],
    *,
    provider_id: str,
) -> tuple[list[dict[str, Any]], str]:
    catalog_ref = _clean_text(config.get(CATALOG_JSON_KEY), _MAX_TEXT)
    if catalog_ref:
        catalog_models = _models_from_json(home / catalog_ref)
        if catalog_models:
            return catalog_models, "model_catalog_json"

    config_models = _models_from_config(config)
    if config_models:
        return config_models, "config"

    if provider_id in _OPENAI_PROVIDER_IDS:
        cached = _models_from_json(home / CATALOG_CACHE_FILENAME)
        if cached:
            return cached, "models_cache"
    return [], ""


def _models_from_json(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    raw_models: Any = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []
    return _collect_models(raw_models)


def _models_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_models: list[Any] = []
    default_model = _clean_text(config.get("model"), _MAX_TEXT)
    if default_model:
        raw_models.append(default_model)
    providers = config.get("model_providers")
    if isinstance(providers, dict):
        for entry in providers.values():
            if not isinstance(entry, dict):
                continue
            declared = entry.get("models")
            if isinstance(declared, list):
                raw_models.extend(declared)
    return _collect_models(raw_models)


def _collect_models(raw_models: list[Any]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_models:
        entry = _normalize_model(raw)
        if entry is None or entry["model"] in seen:
            continue
        if len(models) >= _MAX_MODELS:
            break
        seen.add(entry["model"])
        models.append(entry)
    return models


def _normalize_model(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        slug = _clean_text(raw, _MAX_TEXT)
        if not slug:
            return None
        return {"model": slug, "label": slug, "efforts": [], "default_effort": ""}
    if not isinstance(raw, dict):
        return None
    if str(raw.get("visibility") or "").strip().lower() in {"hide", "hidden"}:
        return None
    slug = _clean_text(raw.get("slug") or raw.get("id") or raw.get("model"), _MAX_TEXT)
    if not slug:
        return None
    label = _clean_text(raw.get("display_name") or raw.get("name"), _MAX_LABEL) or slug
    efforts = _normalize_efforts(raw.get("supported_reasoning_levels"))
    default_effort = _clean_text(
        raw.get("default_reasoning_level") or raw.get("default_reasoning_effort"), _MAX_TEXT
    )
    if default_effort and default_effort not in {e["effort"] for e in efforts}:
        default_effort = ""
    return {"model": slug, "label": label, "efforts": efforts, "default_effort": default_effort}


def _normalize_efforts(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    efforts: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            effort = _clean_text(item.get("effort") or item.get("reasoningEffort"), _MAX_TEXT)
            description = _clean_text(item.get("description"), _MAX_EFFORT_DESC)
        else:
            effort = _clean_text(item, _MAX_TEXT)
            description = ""
        if not effort or effort in seen or len(efforts) >= _MAX_EFFORTS:
            continue
        seen.add(effort)
        efforts.append({"effort": effort, "description": description})
    return efforts


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]
