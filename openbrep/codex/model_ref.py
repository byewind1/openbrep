"""Structured, secret-free model references for Codex routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote_to_bytes

_CODEX_PREFIX = "openai-codex/"
_CC_SWITCH_PREFIX = f"{_CODEX_PREFIX}ccswitch/"
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_MAX_MODEL_LENGTH = 512


class CodexModelRefError(ValueError):
    """A Codex model reference is malformed or unsafe."""

    code = "invalid_codex_model_ref"


@dataclass(frozen=True)
class CodexModelRef:
    kind: Literal["legacy", "cc_switch"]
    model: str
    provider_id: str = ""


def _validate_provider_id(provider_id: object) -> str:
    value = str(provider_id or "")
    if not _PROVIDER_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise CodexModelRefError("Codex provider reference is invalid.")
    return value


def _validate_model(model: object) -> str:
    value = str(model or "")
    if not value or len(value) > _MAX_MODEL_LENGTH:
        raise CodexModelRefError("Codex model reference is invalid.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CodexModelRefError("Codex model reference is invalid.")
    return value


def build_cc_switch_model_ref(provider_id: str, model: str) -> str:
    """Build the canonical OpenBrep reference for one cc-switch model."""
    provider = _validate_provider_id(provider_id)
    model_id = _validate_model(model)
    return f"{_CC_SWITCH_PREFIX}{provider}/{quote(model_id, safe='')}"


def parse_codex_model_ref(value: str) -> CodexModelRef:
    """Parse a legacy or cc-switch-qualified OpenBrep Codex model reference."""
    raw = str(value or "")
    if not raw.startswith(_CODEX_PREFIX):
        raise CodexModelRefError("Codex model reference is invalid.")
    if not raw.startswith(_CC_SWITCH_PREFIX):
        return CodexModelRef(kind="legacy", model=_validate_model(raw[len(_CODEX_PREFIX) :]))

    remainder = raw[len(_CC_SWITCH_PREFIX) :]
    if remainder.count("/") != 1:
        raise CodexModelRefError("Codex model reference is invalid.")
    provider_id, encoded_model = remainder.split("/", 1)
    provider = _validate_provider_id(provider_id)
    if not encoded_model:
        raise CodexModelRefError("Codex model reference is invalid.")
    try:
        model = unquote_to_bytes(encoded_model).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise CodexModelRefError("Codex model reference is invalid.") from exc
    model = _validate_model(model)
    if quote(model, safe="") != encoded_model:
        raise CodexModelRefError("Codex model reference is not canonical.")
    return CodexModelRef(kind="cc_switch", provider_id=provider, model=model)


def wire_model_name(value: str) -> str:
    """Return only the raw model id accepted by the Codex app-server."""
    if not str(value or "").startswith(_CODEX_PREFIX):
        return _validate_model(value)
    return parse_codex_model_ref(value).model
