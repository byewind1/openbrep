"""Read-only importer for a pinned oh-my-pi model catalog snapshot.

The importer accepts a local JSON export of ``@oh-my-pi/pi-catalog``'s
``models.json`` shape. It never fetches the catalog, writes the source file, or
resolves credentials. A caller must provide a commit/version stamp so an
unreviewed moving catalog cannot silently become routing truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PiCatalogModel:
    provider: str
    model_id: str
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_images: bool = False
    supports_reasoning: bool = False
    metadata: tuple[tuple[str, object], ...] = ()

    @property
    def reference(self) -> str:
        return f"{self.provider}/{self.model_id}"


@dataclass(frozen=True)
class PiCatalogSnapshot:
    commit: str
    models: tuple[PiCatalogModel, ...]
    source: str = "oh-my-pi/pi-catalog"

    @property
    def stamp(self) -> str:
        return f"{self.source}@{self.commit}"


def import_pi_catalog(
    payload: Mapping[str, Any],
    *,
    commit: str,
) -> PiCatalogSnapshot | None:
    """Parse a pinned catalog payload, returning ``None`` on invalid input."""

    stamp = str(commit or "").strip()
    if not stamp or not isinstance(payload, Mapping):
        return None

    parsed: list[PiCatalogModel] = []
    # The bundled pi-catalog shape is provider -> model id -> metadata. A list
    # of records is accepted too, which keeps exports easy to produce in tests
    # and future tooling without changing the identity contract.
    if isinstance(payload.get("models"), list):
        for item in payload["models"]:
            if isinstance(item, Mapping):
                model = _parse_record(item, provider=item.get("provider"))
                if model is not None:
                    parsed.append(model)
    else:
        for raw_provider, raw_models in payload.items():
            provider = str(raw_provider or "").strip()
            if not provider or not isinstance(raw_models, Mapping):
                continue
            for raw_id, raw_spec in raw_models.items():
                if not isinstance(raw_spec, Mapping):
                    raw_spec = {"name": raw_spec}
                model = _parse_record(raw_spec, provider=provider, model_id=raw_id)
                if model is not None:
                    parsed.append(model)

    unique: dict[tuple[str, str], PiCatalogModel] = {}
    for model in parsed:
        unique.setdefault((model.provider.lower(), model.model_id.lower()), model)
    return PiCatalogSnapshot(commit=stamp, models=tuple(unique.values()))


def load_pi_catalog(path: str | Path, *, commit: str) -> PiCatalogSnapshot | None:
    """Load a pinned local JSON snapshot; malformed/unreadable files fail closed."""

    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return import_pi_catalog(payload, commit=commit)


def load_configured_pi_catalog(config: Mapping[str, Any] | None) -> PiCatalogSnapshot | None:
    """Load ``[llm.pi_catalog]`` without exposing its local path downstream."""

    if not isinstance(config, Mapping):
        return None
    path = str(config.get("path") or "").strip()
    commit = str(config.get("commit") or "").strip()
    if not path or not commit:
        return None
    return load_pi_catalog(path, commit=commit)


def normalize_pi_catalog_config(config: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only the explicit, non-secret path/commit configuration keys."""

    if not isinstance(config, Mapping):
        return {}
    path = str(config.get("path") or "").strip()
    commit = str(config.get("commit") or "").strip()
    if not path or not commit:
        return {}
    return {"path": path, "commit": commit}


def _parse_record(
    raw: Mapping[str, Any],
    *,
    provider: Any = None,
    model_id: Any = None,
) -> PiCatalogModel | None:
    provider_name = str(provider or raw.get("provider") or "").strip()
    identifier = str(model_id or raw.get("id") or raw.get("model") or "").strip()
    if not provider_name or not identifier:
        return None
    # Some exports put provider/model in one id; split only when provider was
    # omitted so an explicit provider remains authoritative.
    if provider is None and "/" in identifier:
        provider_name, identifier = identifier.split("/", 1)
        provider_name, identifier = provider_name.strip(), identifier.strip()
    if not provider_name or not identifier:
        return None

    input_values = raw.get("input") or raw.get("modalities") or ()
    if isinstance(input_values, str):
        input_values = (input_values,)
    inputs = {str(value).strip().lower() for value in input_values if value}
    reasoning = raw.get("reasoning")
    if reasoning is None:
        reasoning = raw.get("supportsReasoning", raw.get("thinking", False))
    metadata = tuple(
        (key, raw[key])
        for key in ("input", "reasoning", "pricing", "api", "family")
        if key in raw and isinstance(raw[key], (str, int, float, bool, list, dict))
    )
    return PiCatalogModel(
        provider=provider_name,
        model_id=identifier,
        display_name=str(raw.get("name") or raw.get("displayName") or identifier).strip(),
        context_window=_positive_int(raw.get("contextWindow", raw.get("context_window"))),
        max_output_tokens=_positive_int(raw.get("maxTokens", raw.get("max_output_tokens"))),
        supports_images="image" in inputs,
        supports_reasoning=bool(reasoning),
        metadata=metadata,
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
