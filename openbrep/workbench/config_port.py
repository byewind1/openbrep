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
