"""Read-only cc-switch Codex provider registry adapter.

Public catalog objects contain display metadata only. Runtime configuration is
kept in a separate, non-serializable object whose secret fields are excluded
from repr and all stable error messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

_DEFAULT_DB = Path.home() / ".cc-switch" / "cc-switch.db"
_REQUIRED_COLUMNS = frozenset(
    {"id", "app_type", "name", "is_current", "settings_config"}
)
_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_MAX_TEXT = 512
_MAX_LABEL = 160
_MAX_MODELS = 256
_MAX_EFFORTS = 16


class CcSwitchError(RuntimeError):
    code = "cc_switch_unavailable"


class CcSwitchSchemaUnsupportedError(CcSwitchError):
    code = "cc_switch_schema_unsupported"


class CcSwitchProviderMissingError(CcSwitchError):
    code = "cc_switch_provider_missing"


class CcSwitchProviderUnusableError(CcSwitchError):
    code = "cc_switch_provider_unusable"


class CcSwitchCatalogUnavailableError(CcSwitchError):
    code = "cc_switch_catalog_unavailable"


class CcSwitchRuntimeError(CcSwitchError):
    code = "cc_switch_runtime_failed"


@dataclass(frozen=True)
class CcSwitchDiagnostic:
    code: str
    message: str
    provider_id: str = ""

    def to_public_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "provider_id": self.provider_id,
        }


@dataclass(frozen=True)
class CcSwitchModelInfo:
    model: str
    label: str
    efforts: tuple[str, ...] = ()
    effort_descriptions: tuple[tuple[str, str], ...] = ()
    default_effort: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        descriptions = dict(self.effort_descriptions)
        return {
            "model": self.model,
            "label": self.label,
            "supported_reasoning_efforts": [
                {"effort": effort, "description": descriptions.get(effort, "")}
                for effort in self.efforts
            ],
            "default_reasoning_effort": self.default_effort,
        }


@dataclass(frozen=True)
class CcSwitchProviderInfo:
    id: str
    name: str
    is_current: bool
    models: tuple[CcSwitchModelInfo, ...]
    catalog_source: str
    catalog_complete: bool
    runnable: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_current": self.is_current,
            "models": [model.to_public_dict() for model in self.models],
            "catalog_source": self.catalog_source,
            "catalog_complete": self.catalog_complete,
            "runnable": self.runnable,
        }


@dataclass(frozen=True)
class CcSwitchCatalog:
    detected: bool
    providers: tuple[CcSwitchProviderInfo, ...] = ()
    diagnostics: tuple[CcSwitchDiagnostic, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "cc_switch_detected": self.detected,
            "providers": [provider.to_public_dict() for provider in self.providers],
            "diagnostics": [item.to_public_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class CcSwitchRuntimeConfig:
    provider_id: str
    config_toml: str = field(repr=False)
    auth_payload: Any = field(repr=False)
    model_catalog_payload: Any = field(repr=False)
    fingerprint: str


def _stable_error(error_type: type[CcSwitchError]) -> CcSwitchError:
    messages = {
        CcSwitchSchemaUnsupportedError: "cc-switch 数据结构不受支持。",
        CcSwitchProviderMissingError: "cc-switch 供应商已不存在，请重新选择。",
        CcSwitchProviderUnusableError: "cc-switch 供应商配置不可用。",
        CcSwitchCatalogUnavailableError: "cc-switch 模型目录暂不可用。",
        CcSwitchRuntimeError: "cc-switch Codex 运行环境创建失败。",
    }
    return error_type(messages.get(error_type, "cc-switch 当前不可用。"))


def _clean_text(value: Any, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        return ""
    return text


def _safe_provider_id(value: Any) -> str:
    text = _clean_text(value, 128)
    if not _SAFE_PROVIDER_ID.fullmatch(text) or text in {".", ".."}:
        return ""
    return text


def _parse_settings(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _parse_config(raw: Any) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(raw, str) or not raw.strip():
        return "", None
    try:
        parsed = tomllib.loads(raw)
    except (tomllib.TOMLDecodeError, TypeError, ValueError):
        return raw, None
    return raw, parsed if isinstance(parsed, dict) else None


def _catalog_payload(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return raw


def _normalize_efforts(raw: Any) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    if not isinstance(raw, list):
        return (), ()
    efforts: list[str] = []
    descriptions: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            effort = _clean_text(
                item.get("effort") or item.get("reasoningEffort"), 32
            )
            description = _clean_text(item.get("description"), 120)
        else:
            effort = _clean_text(item, 32)
            description = ""
        if not effort or effort in efforts or len(efforts) >= _MAX_EFFORTS:
            continue
        efforts.append(effort)
        descriptions.append((effort, description))
    return tuple(efforts), tuple(descriptions)


def _normalize_model(raw: Any) -> CcSwitchModelInfo | None:
    if isinstance(raw, str):
        model = _clean_text(raw)
        return CcSwitchModelInfo(model=model, label=model) if model else None
    if not isinstance(raw, dict):
        return None
    model = _clean_text(raw.get("slug") or raw.get("id") or raw.get("model"))
    if not model:
        return None
    label = _clean_text(
        raw.get("display_name") or raw.get("displayName") or raw.get("name"),
        _MAX_LABEL,
    ) or model
    efforts, descriptions = _normalize_efforts(
        raw.get("supported_reasoning_levels")
        or raw.get("supportedReasoningEfforts")
        or raw.get("supported_reasoning_efforts")
        or raw.get("reasoningLevels")
    )
    default_effort = _clean_text(
        raw.get("default_reasoning_level")
        or raw.get("defaultReasoningEffort")
        or raw.get("default_reasoning_effort"),
        32,
    )
    if default_effort not in efforts:
        default_effort = ""
    return CcSwitchModelInfo(
        model=model,
        label=label,
        efforts=efforts,
        effort_descriptions=descriptions,
        default_effort=default_effort,
    )


def _models_from_catalog(payload: Any) -> tuple[CcSwitchModelInfo, ...]:
    payload = _catalog_payload(payload)
    raw_models = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return ()
    result: list[CcSwitchModelInfo] = []
    seen: set[str] = set()
    for raw in raw_models:
        model = _normalize_model(raw)
        if model is None or model.model in seen or len(result) >= _MAX_MODELS:
            continue
        seen.add(model.model)
        result.append(model)
    return tuple(result)


def _auth_usable(auth: Any) -> bool:
    if isinstance(auth, str):
        return bool(auth.strip())
    if isinstance(auth, (dict, list)):
        return bool(auth)
    return auth is not None


class CcSwitchRegistry:
    """Read cc-switch provider rows without creating or mutating its database."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        override = os.environ.get("OPENBREP_CC_SWITCH_DB") if db_path is None else None
        self.db_path = Path(db_path or override or _DEFAULT_DB).expanduser()

    def _connect(self) -> sqlite3.Connection:
        try:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            return connection
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise _stable_error(CcSwitchError) from exc

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> None:
        try:
            columns = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM pragma_table_info('providers')"
                ).fetchall()
            }
        except sqlite3.Error as exc:
            raise _stable_error(CcSwitchSchemaUnsupportedError) from exc
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise _stable_error(CcSwitchSchemaUnsupportedError)

    def _rows(self) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            self._check_schema(connection)
            return connection.execute(
                "SELECT id, name, is_current, settings_config "
                "FROM providers WHERE app_type = ? ORDER BY rowid",
                ("codex",),
            ).fetchall()
        except CcSwitchError:
            raise
        except sqlite3.Error as exc:
            raise _stable_error(CcSwitchSchemaUnsupportedError) from exc
        finally:
            connection.close()

    @staticmethod
    def _row_parts(row: sqlite3.Row) -> tuple[str, str, bool, str, dict[str, Any]] | None:
        provider_id = _safe_provider_id(row["id"])
        name = _clean_text(row["name"], _MAX_LABEL)
        raw_settings = row["settings_config"]
        settings = _parse_settings(raw_settings)
        if not provider_id or settings is None or not isinstance(raw_settings, str):
            return None
        return provider_id, name or provider_id, bool(row["is_current"]), raw_settings, settings

    def catalog(self) -> CcSwitchCatalog:
        if not self.db_path.is_file():
            return CcSwitchCatalog(
                detected=False,
                diagnostics=(
                    CcSwitchDiagnostic(
                        code="cc_switch_unavailable",
                        message="未检测到 cc-switch。",
                    ),
                ),
            )
        providers: list[CcSwitchProviderInfo] = []
        diagnostics: list[CcSwitchDiagnostic] = []
        for row in self._rows():
            parts = self._row_parts(row)
            if parts is None:
                diagnostics.append(
                    CcSwitchDiagnostic(
                        code="cc_switch_provider_unusable",
                        message="一个 cc-switch Codex 供应商配置不可用。",
                    )
                )
                continue
            provider_id, name, is_current, _raw_settings, settings = parts
            config_toml, config = _parse_config(settings.get("config"))
            catalog_payload = _catalog_payload(settings.get("modelCatalog"))
            models = _models_from_catalog(catalog_payload)
            source = "model_catalog" if models else ""
            complete = bool(models)
            if not models and config is not None:
                default_model = _clean_text(config.get("model"))
                if default_model:
                    models = (CcSwitchModelInfo(default_model, default_model),)
                    source = "config_default"
            runnable = bool(
                config_toml
                and config is not None
                and _auth_usable(settings.get("auth"))
            )
            if not runnable:
                diagnostics.append(
                    CcSwitchDiagnostic(
                        code="cc_switch_provider_unusable",
                        message="cc-switch Codex 供应商配置不可运行。",
                        provider_id=provider_id,
                    )
                )
            providers.append(
                CcSwitchProviderInfo(
                    id=provider_id,
                    name=name,
                    is_current=is_current,
                    models=models,
                    catalog_source=source,
                    catalog_complete=complete,
                    runnable=runnable,
                )
            )
        return CcSwitchCatalog(
            detected=True,
            providers=tuple(providers),
            diagnostics=tuple(diagnostics),
        )

    def runtime_config(self, provider_id: str) -> CcSwitchRuntimeConfig:
        target = _safe_provider_id(provider_id)
        if not target:
            raise _stable_error(CcSwitchProviderMissingError)
        if not self.db_path.is_file():
            raise _stable_error(CcSwitchProviderMissingError)
        for row in self._rows():
            row_id = _safe_provider_id(row["id"])
            parts = self._row_parts(row)
            if row_id != target:
                continue
            if parts is None:
                raise _stable_error(CcSwitchProviderUnusableError)
            _provider_id, _name, _current, raw_settings, settings = parts
            config_toml, config = _parse_config(settings.get("config"))
            auth = settings.get("auth")
            if not config_toml or config is None or not _auth_usable(auth):
                raise _stable_error(CcSwitchProviderUnusableError)
            return CcSwitchRuntimeConfig(
                provider_id=target,
                config_toml=config_toml,
                auth_payload=auth,
                model_catalog_payload=_catalog_payload(settings.get("modelCatalog")),
                fingerprint=hashlib.sha256(raw_settings.encode("utf-8")).hexdigest(),
            )
        catalog_ids = {provider.id for provider in self.catalog().providers}
        if target in catalog_ids:
            raise _stable_error(CcSwitchProviderUnusableError)
        raise _stable_error(CcSwitchProviderMissingError)


def _private_json_bytes(payload: Any) -> bytes:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise _stable_error(CcSwitchRuntimeError) from exc
    try:
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _stable_error(CcSwitchRuntimeError) from exc


def _runtime_config_text(config_toml: str, *, has_catalog: bool) -> str:
    """Replace only the top-level catalog path with the isolated local file."""
    output: list[str] = []
    in_top_level = True
    assignment = re.compile(r"^\s*model_catalog_json\s*=")
    for line in config_toml.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("["):
            in_top_level = False
        if in_top_level and assignment.match(line):
            continue
        output.append(line)
    if has_catalog:
        output.insert(0, 'model_catalog_json = "model_catalog.json"')
    return "\n".join(output).rstrip() + "\n"


def _write_private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def materialize_runtime_home(
    runtime: CcSwitchRuntimeConfig,
    *,
    parent: Path | None = None,
) -> Path:
    """Create one permission-restricted Codex home for a cc-switch provider."""
    home: Path | None = None
    try:
        home = Path(
            tempfile.mkdtemp(
                prefix="openbrep-codex-ccswitch-",
                dir=os.fspath(parent) if parent is not None else None,
            )
        )
        os.chmod(home, 0o700)
        catalog = runtime.model_catalog_payload
        config = _runtime_config_text(
            runtime.config_toml,
            has_catalog=catalog is not None,
        )
        _write_private_file(home / "config.toml", config.encode("utf-8"))
        _write_private_file(home / "auth.json", _private_json_bytes(runtime.auth_payload))
        if catalog is not None:
            _write_private_file(
                home / "model_catalog.json",
                _private_json_bytes(catalog),
            )
        return home
    except Exception as exc:
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
        if isinstance(exc, CcSwitchRuntimeError):
            raise
        raise _stable_error(CcSwitchRuntimeError) from exc


def remove_runtime_home(path: Path | None) -> None:
    """Best-effort cleanup for an OpenBrep-owned cc-switch runtime home."""
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)
