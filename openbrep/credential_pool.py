"""Small, secret-safe credential pool used by model providers.

The pool keeps credentials in memory only. Selection is round-robin across
scopes, sticky within a scope, and skips credentials in a caller-controlled
cooldown. It never serializes or logs key material.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand_env_ref(value: object) -> str:
    text = str(value or "").strip()
    match = _ENV_REF_RE.match(text)
    return os.environ.get(match.group(1), "") if match else text


@dataclass(frozen=True)
class Credential:
    credential_id: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class CredentialLease:
    credential_id: str
    value: str = field(repr=False)
    scope: str = ""

    def as_metadata(self) -> dict[str, str]:
        return {"credential_id": self.credential_id, "scope": self.scope}


class CredentialPool:
    """Round-robin credential selection with session affinity and backoff."""

    def __init__(self, credentials: Sequence[Credential], *, cooldown_seconds: float = 30.0):
        self.credentials = tuple(item for item in credentials if item.value)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._cursor = 0
        self._sticky: dict[str, str] = {}
        self._failed_until: dict[str, float] = {}

    @classmethod
    def from_provider(cls, provider: Mapping[str, Any]) -> "CredentialPool":
        raw = provider.get("credentials") or provider.get("api_keys") or []
        if not isinstance(raw, list):
            raw = [raw]
        parsed: list[Credential] = []
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                value = item.get("api_key", item.get("apiKey", item.get("value", "")))
                env_name = str(item.get("env") or item.get("env_var") or "").strip()
                if env_name:
                    value = os.environ.get(env_name, "")
                credential_id = str(
                    item.get("id") or item.get("name") or f"key-{index + 1}"
                ).strip()
            else:
                value = item
                credential_id = f"key-{index + 1}"
            value = _expand_env_ref(value)
            if value:
                parsed.append(Credential(credential_id, value))
        if not parsed:
            value = _expand_env_ref(provider.get("api_key"))
            if value:
                parsed.append(Credential("primary", value))
        return cls(parsed)

    def select(self, scope: str = "default", *, now: float | None = None) -> CredentialLease | None:
        if not self.credentials:
            return None
        scope_key = str(scope or "default")
        current = now if now is not None else time.monotonic()
        sticky_id = self._sticky.get(scope_key)
        if sticky_id and self._available(sticky_id, current):
            return self._lease(sticky_id, scope_key)
        for offset in range(len(self.credentials)):
            index = (self._cursor + offset) % len(self.credentials)
            candidate = self.credentials[index]
            if self._available(candidate.credential_id, current):
                self._cursor = (index + 1) % len(self.credentials)
                self._sticky[scope_key] = candidate.credential_id
                return self._lease(candidate.credential_id, scope_key)
        return None

    def mark_failure(self, credential_id: str, *, now: float | None = None) -> None:
        if self.cooldown_seconds <= 0:
            return
        current = now if now is not None else time.monotonic()
        self._failed_until[str(credential_id)] = current + self.cooldown_seconds

    def mark_success(self, credential_id: str) -> None:
        self._failed_until.pop(str(credential_id), None)

    def _available(self, credential_id: str, now: float) -> bool:
        return self._failed_until.get(credential_id, 0.0) <= now

    def _lease(self, credential_id: str, scope: str) -> CredentialLease:
        for item in self.credentials:
            if item.credential_id == credential_id:
                return CredentialLease(item.credential_id, item.value, scope)
        raise KeyError(credential_id)
