"""OpenBrep 配置导入导出（卡12 导出 / 卡13 导入）：自身格式，不涉及外部互操作。

导出合同（评审 §7.2）：
- 序列化走 ``GDLAgentConfig.save()`` 同一路径（"导出即保存所见"——canonical
  键、规范化语义完全一致）；
- ``include_keys=False``（默认）脱敏覆盖**完整结构**：顶层 ``api_key``、
  ``[llm.provider_keys]``、条目 ``api_key``、``credentials`` / ``api_keys`` 池
  字段。``${ENV}`` 引用原样保留（环境变量名不是秘密）；明文凭据置空——无法
  推导环境变量名时绝不凭空生成看似可用的引用；
- 脱敏导出附带说明段：导入后需补齐哪些凭据；
- ``include_keys=True`` 原样导出明文（调用方须先经用户二次确认）。
"""

from __future__ import annotations

import copy
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from openbrep.config import GDLAgentConfig
from openbrep.workbench.credential_status import is_env_ref


def export_filename(now: datetime | None = None) -> str:
    return f"openbrep-llm-{(now or datetime.now()).strftime('%Y%m%d')}.toml"


def _sanitize_secret_value(value: Any) -> str:
    """单值脱敏：${ENV} 引用原样保留；明文置空（绝不凭空生成 ${ENV} 引用）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if is_env_ref(text):
        return text
    return ""


def _sanitize_pool_list(raw: Any) -> list[Any]:
    """池字段脱敏：字符串项与 Mapping 项的 value/env 语义分别处理。"""
    items = raw if isinstance(raw, list) else [raw] if raw else []
    out: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            sanitized = dict(item)
            # env/env_var 引用不是秘密，原样保留；value/api_key/apiKey 明文置空
            for key in ("value", "api_key", "apiKey"):
                if key in sanitized:
                    sanitized[key] = _sanitize_secret_value(sanitized.get(key))
            out.append(sanitized)
        else:
            out.append(_sanitize_secret_value(item))
    return out


def sanitize_config_for_export(config: GDLAgentConfig) -> GDLAgentConfig:
    """返回脱敏后的配置副本（不改原配置）。覆盖全部凭据位置。"""
    sanitized = copy.deepcopy(config)
    sanitized.llm.api_key = _sanitize_secret_value(sanitized.llm.api_key)
    sanitized.llm.provider_keys = {
        key: _sanitize_secret_value(value) for key, value in (sanitized.llm.provider_keys or {}).items()
    }
    for entry in sanitized.llm.providers:
        if "api_key" in entry:
            entry["api_key"] = _sanitize_secret_value(entry.get("api_key"))
        if entry.get("credentials"):
            entry["credentials"] = _sanitize_pool_list(entry.get("credentials"))
        if entry.get("api_keys"):
            entry["api_keys"] = _sanitize_pool_list(entry.get("api_keys"))
    return sanitized


def _missing_credentials_report(config: GDLAgentConfig) -> list[str]:
    """脱敏导出后哪些位置需要补齐凭据（按导入后的视角计算）。"""
    from openbrep.workbench.credential_status import credential_status

    lines: list[str] = []
    if config.llm.api_key:
        lines.append(f"  - 顶层 api_key：{config.llm.api_key}（环境变量引用，需导入机器上存在该变量）")
    for key, value in (config.llm.provider_keys or {}).items():
        if value:
            lines.append(f"  - provider_keys[{key}]：{value}（环境变量引用）")
    for entry in config.llm.providers:
        name = str(entry.get("name", "") or "")
        status = credential_status(entry, config)
        if status["resolvable"]:
            if status["form"] == "pool":
                lines.append(f"  - 服务商 {name}：凭据池中存在非明文/空值项，导入后请检查池配置")
            elif is_env_ref(entry.get("api_key")):
                lines.append(f"  - 服务商 {name}：{entry['api_key']}（环境变量引用）")
        else:
            lines.append(f"  - 服务商 {name}：无可用凭据，导入后需补填")
    return lines


def _serialize(config: GDLAgentConfig) -> str:
    """走 save() 同一序列化路径（临时文件写出读回，"导出即保存所见"）。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "export.toml"
        config.save(str(tmp_path))
        return tmp_path.read_text(encoding="utf-8")


def export_llm_config(config: GDLAgentConfig, *, include_keys: bool = False) -> dict[str, Any]:
    """导出配置 TOML 文本；默认脱敏并附带补齐说明段。"""
    if include_keys:
        content = _serialize(copy.deepcopy(config))
        return {
            "ok": True,
            "filename": export_filename(),
            "include_keys": True,
            "content": content,
            "warnings": [],
        }
    sanitized = sanitize_config_for_export(config)
    body = _serialize(sanitized)
    missing = _missing_credentials_report(sanitized)
    header_lines = [
        "# ── OpenBrep 配置导出（不含明文 API Key）──",
        "# 明文凭据已置空；${ENV_VAR} 引用原样保留。",
        "# 导入后需要补齐以下凭据：" + ("（无）" if not missing else ""),
    ]
    # 说明段每行都必须是 TOML 注释，否则破坏导入解析
    header_lines.extend(line if line.startswith("#") else f"# {line}" for line in missing)
    header = "\n".join(header_lines) + "\n\n"
    return {
        "ok": True,
        "filename": export_filename(),
        "include_keys": False,
        "content": header + body,
        "warnings": missing,
    }


# ── 卡13：OpenBrep 配置导入（dry-run 预览 + 提交复查 revision）──────────────

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11（config.py 同款先例，不新增依赖）
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

from openbrep.config import (  # noqa: E402
    CODEX_PROVIDER_NAME,
    PROVIDER_PROFILES,
    _normalize_api_mode,
)
from openbrep.workbench.llm_diagnostics import redact_secrets  # noqa: E402


def reserved_provider_names() -> set[str]:
    """不可占用的保留名：openai-codex 与官方 profile 名（小写）。"""
    names = {profile.name.lower() for profile in PROVIDER_PROFILES}
    names.add(CODEX_PROVIDER_NAME.lower())
    return names


def parse_import_content(content: str) -> dict[str, Any]:
    """解析导入的 TOML 片段：完整 [llm] 表、仅 [[llm.providers]] 数组、或顶层
    [[providers]] 片段；legacy [[llm.custom_providers]] 一并接受。

    返回 {entries, incoming_model, errors}；解析失败时 errors 给可读原因。
    """
    errors: list[str] = []
    if tomllib is None:
        return {"entries": [], "incoming_model": None, "errors": ["当前 Python 缺少 tomllib/tomli，无法解析 TOML。"]}
    try:
        data = tomllib.loads(str(content or ""))
    except Exception as exc:  # noqa: BLE001 —— 解析错误文本不含用户 secret，仍过脱敏
        errors.append(f"TOML 解析失败：{exc}")
        return {"entries": [], "incoming_model": None, "errors": errors}

    llm_block = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    raw_entries: list[Any] = []
    for key in ("providers", "custom_providers"):
        raw = llm_block.get(key)
        if isinstance(raw, list):
            raw_entries.extend(item for item in raw if isinstance(item, dict))
    if not raw_entries and isinstance(data.get("providers"), list):
        raw_entries.extend(item for item in data["providers"] if isinstance(item, dict))

    incoming_model = str(llm_block.get("model") or llm_block.get("default") or "").strip() or None
    return {"entries": raw_entries, "incoming_model": incoming_model, "errors": errors}


def _incoming_api_mode(entry: dict) -> str | None:
    try:
        return _normalize_api_mode(entry.get("api_mode") or entry.get("protocol"))
    except ValueError:
        return None


def _secret_candidates(entries: list[dict]) -> list[str]:
    secrets: list[str] = []
    for entry in entries:
        value = str(entry.get("api_key") or "").strip()
        if value and not value.startswith("${"):
            secrets.append(value)
        for pool in (entry.get("credentials") or entry.get("api_keys") or []):
            if isinstance(pool, dict):
                value = str(pool.get("value") or pool.get("api_key") or pool.get("apiKey") or "").strip()
                if value and not value.startswith("${"):
                    secrets.append(value)
            else:
                value = str(pool or "").strip()
                if value and not value.startswith("${"):
                    secrets.append(value)
    return secrets


def _mask_value(value: Any) -> str:
    from openbrep.workbench.credential_status import mask_secret

    return mask_secret(value)


def _models_signature(raw: Any) -> list[dict[str, str]] | None:
    from openbrep.workbench.provider_service import normalize_models_request

    if raw is None:
        return None
    return normalize_models_request(raw)


def plan_import(config: Any, content: str) -> dict[str, Any]:
    """dry-run 预览：{to_add, to_update, conflicts, skipped, errors, notes}。

    合同（评审 §7.1 / 卡13）：
    - key 保留规则：incoming 缺省/空 → 保留现有；显式非空 → 替换（导入的
      两态语义——导入文件不带 key 不应抹掉既有 key）；
    - 默认模型：以现有为准——incoming model/default_model 只在 notes 中标注，
      绝不静默覆盖（保存服务商不顺手改默认模型）；
    - openai-codex 条目一律 skipped；保留名条目 skipped；
    - diff 中的凭据只显示掩码，不泄露 secret。
    """
    parsed = parse_import_content(content)
    errors = list(parsed["errors"])
    secrets = _secret_candidates(parsed["entries"])
    errors = [redact_secrets(error, secrets) for error in errors]

    to_add: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    notes: list[str] = []

    existing_by_name = {
        str(entry.get("name", "") or "").strip().lower(): entry
        for entry in config.llm.providers
        if isinstance(entry, dict)
    }

    for incoming in parsed["entries"]:
        name = str(incoming.get("name") or "").strip()
        if name.lower() == CODEX_PROVIDER_NAME.lower():
            skipped.append({"name": name or "openai-codex", "reason": "codex_entry_protected：保留订阅身份，不参与导入。"})
            continue
        if not name or _NAME_FORBIDDEN_RE_IMPORT.search(name):
            skipped.append({"name": name or "(未命名)", "reason": "invalid_name：名称为空或含空白/斜杠。"})
            continue
        if name.lower() in reserved_provider_names():
            skipped.append({"name": name, "reason": "reserved_name：内置服务商保留名，不能占用。"})
            continue
        existing = existing_by_name.get(name.lower())
        if existing is None:
            to_add.append({
                "name": name,
                "api": str(incoming.get("api") or incoming.get("base_url") or "").strip(),
                "api_mode": _incoming_api_mode(incoming) or "chat_completions",
                "default_model": str(incoming.get("default_model") or "").strip(),
                "model_count": len(_models_signature(incoming.get("models")) or []),
                "has_credential": bool(str(incoming.get("api_key") or "").strip() or incoming.get("credentials") or incoming.get("api_keys")),
            })
            continue

        changes: list[dict[str, Any]] = []
        # api（显式覆盖语义：incoming 提供即写入，含显式空）
        if "api" in incoming or "base_url" in incoming:
            new_api = str(incoming.get("api") or incoming.get("base_url") or "").strip()
            old_api = str(existing.get("api") or existing.get("base_url") or "").strip()
            if new_api != old_api:
                changes.append({"field": "api", "old": old_api, "new": new_api})
        # api_mode
        new_mode = _incoming_api_mode(incoming)
        if new_mode is not None:
            old_mode = str(existing.get("api_mode") or "chat_completions")
            if new_mode != old_mode:
                changes.append({"field": "api_mode", "old": old_mode, "new": new_mode})
        # api_key：缺省/空 → 保留；显式非空 → 替换（diff 显示掩码）
        incoming_key = str(incoming.get("api_key") or "").strip()
        if incoming_key:
            old_key = str(existing.get("api_key") or "").strip()
            if incoming_key != old_key:
                changes.append({"field": "api_key", "old": _mask_value(old_key), "new": _mask_value(incoming_key)})
        # models：incoming 提供即整体替换
        new_models = _models_signature(incoming.get("models"))
        if new_models is not None:
            from openbrep.workbench.provider_service import normalize_models_request

            old_models = normalize_models_request(existing.get("models") or [])
            if new_models != old_models:
                changes.append({"field": "models", "old": [m["alias"] for m in old_models], "new": [m["alias"] for m in new_models]})
        # 高级字段：incoming 提供即替换
        for field in ("temperature", "extra_body", "credentials", "api_keys"):
            if field in incoming:
                old_value = existing.get(field)
                if incoming[field] != old_value:
                    shown_old = _mask_value(old_value) if field in ("credentials", "api_keys") else old_value
                    shown_new = _mask_value(incoming[field]) if field in ("credentials", "api_keys") else incoming[field]
                    changes.append({"field": field, "old": shown_old, "new": shown_new})
        # 默认模型冲突：以现有为准，预览标注
        incoming_default = str(incoming.get("default_model") or "").strip()
        existing_default = str(existing.get("default_model") or "").strip()
        if incoming_default and incoming_default != existing_default:
            conflicts.append({
                "name": name,
                "field": "default_model",
                "incoming": incoming_default,
                "existing": existing_default,
                "rule": "keep_existing",
                "reason": "默认模型以现有配置为准，incoming 值不会应用。",
            })
        if changes:
            to_update.append({"name": name, "changes": changes})

    if parsed["incoming_model"]:
        notes.append(
            f"导入文件中的默认模型 {parsed['incoming_model']} 不会应用：[llm].model 以现有配置为准（切换默认模型是独立动作）。"
        )
    return {
        "to_add": to_add,
        "to_update": to_update,
        "conflicts": conflicts,
        "skipped": skipped,
        "errors": errors,
        "notes": notes,
    }


import re as _re  # noqa: E402

_NAME_FORBIDDEN_RE_IMPORT = _re.compile(r"[\s/]")


def apply_import(config: GDLAgentConfig, content: str) -> dict[str, Any]:
    """在（副本）配置上应用导入计划：新增条目 + 按合同更新既有条目。"""
    parsed = parse_import_content(content)
    secrets = _secret_candidates(parsed["entries"])
    existing_by_name = {
        str(entry.get("name", "") or "").strip().lower(): entry
        for entry in config.llm.providers
        if isinstance(entry, dict)
    }
    applied = 0
    for incoming in parsed["entries"]:
        name = str(incoming.get("name") or "").strip()
        if not name or name.lower() == CODEX_PROVIDER_NAME.lower() or _NAME_FORBIDDEN_RE_IMPORT.search(name):
            continue
        if name.lower() in reserved_provider_names():
            continue
        existing = existing_by_name.get(name.lower())
        if existing is None:
            from openbrep.workbench.provider_service import normalize_models_request

            entry: dict[str, Any] = {"name": name}
            if "api" in incoming or "base_url" in incoming:
                entry["api"] = str(incoming.get("api") or incoming.get("base_url") or "").strip()
                entry["base_url"] = entry["api"]
                entry["_explicit_base"] = True
            new_mode = _incoming_api_mode(incoming)
            if new_mode is not None:
                entry["api_mode"] = new_mode
            if str(incoming.get("api_key") or "").strip():
                entry["api_key"] = str(incoming.get("api_key")).strip()
            if str(incoming.get("default_model") or "").strip():
                entry["default_model"] = str(incoming.get("default_model")).strip()
            models = normalize_models_request(incoming.get("models") or [])
            entry["models"] = models
            for field in ("temperature", "extra_body", "credentials", "api_keys"):
                if field in incoming:
                    entry[field] = incoming[field]
            config.llm.providers.append(entry)
            existing_by_name[name.lower()] = entry
            applied += 1
            continue
        changed = False
        if "api" in incoming or "base_url" in incoming:
            new_api = str(incoming.get("api") or incoming.get("base_url") or "").strip()
            old_api = str(existing.get("api") or existing.get("base_url") or "").strip()
            if new_api != old_api:
                existing["api"] = new_api
                existing["base_url"] = new_api
                existing["_explicit_base"] = True
                changed = True
        new_mode = _incoming_api_mode(incoming)
        if new_mode is not None and new_mode != str(existing.get("api_mode") or "chat_completions"):
            existing["api_mode"] = new_mode
            changed = True
        incoming_key = str(incoming.get("api_key") or "").strip()
        if incoming_key and incoming_key != str(existing.get("api_key") or "").strip():
            existing["api_key"] = incoming_key
            changed = True
        new_models = _models_signature(incoming.get("models"))
        if new_models is not None:
            from openbrep.workbench.provider_service import normalize_models_request

            if new_models != normalize_models_request(existing.get("models") or []):
                existing["models"] = new_models
                changed = True
        for field in ("temperature", "extra_body", "credentials", "api_keys"):
            if field in incoming and incoming[field] != existing.get(field):
                existing[field] = incoming[field]
                changed = True
        if changed:
            applied += 1
    return {"applied": applied, "errors": [redact_secrets(error, secrets) for error in parsed["errors"]]}
