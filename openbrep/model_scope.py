"""Path-scoped model/provider allow and deny rules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping


def scoped_values(
    entries: Iterable[Any],
    *,
    cwd: str | os.PathLike[str] | None,
    value_keys: tuple[str, ...],
) -> tuple[str, ...]:
    """Collect global values plus entries whose path prefix contains ``cwd``."""

    current = Path(cwd or os.getcwd()).expanduser().resolve()
    result: list[str] = []
    for entry in entries or ():
        if isinstance(entry, str):
            _append(result, entry)
            continue
        if not isinstance(entry, Mapping):
            continue
        prefixes = entry.get("path") or entry.get("pathPrefix") or entry.get("paths") or entry.get("pathPrefixes")
        if isinstance(prefixes, (str, os.PathLike)):
            prefixes = [prefixes]
        if not isinstance(prefixes, (list, tuple)):
            continue
        matched = False
        for raw_prefix in prefixes:
            try:
                prefix = Path(str(raw_prefix)).expanduser().resolve()
                if current == prefix or prefix in current.parents:
                    matched = True
                    break
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
        if not matched:
            continue
        for key in value_keys:
            values = entry.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, (list, tuple)):
                for value in values:
                    _append(result, value)
    return tuple(result)


def model_enabled(
    reference: str,
    *,
    provider: str,
    enabled_models: Iterable[Any] = (),
    disabled_providers: Iterable[Any] = (),
    cwd: str | os.PathLike[str] | None = None,
) -> bool:
    """Apply the opt-in allow-list and provider deny-list for one path."""

    disabled = {item.lower() for item in scoped_values(disabled_providers, cwd=cwd, value_keys=("providers", "values", "items"))}
    if provider.lower() in disabled:
        return False
    allowed = scoped_values(enabled_models, cwd=cwd, value_keys=("models", "values", "items"))
    if not allowed:
        return True
    target = reference.lower()
    return any(_match_model(pattern.lower(), target) for pattern in allowed)


def _match_model(pattern: str, target: str) -> bool:
    if pattern in {"*", "all"}:
        return True
    if pattern.endswith("/*"):
        return target.startswith(pattern[:-1])
    return pattern == target


def _append(result: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in result:
        result.append(text)
