"""Small durable receipts for retry-safe external operations on an HSF project."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def valid_operation_id(operation_id: str) -> bool:
    return bool(_SAFE_OPERATION_ID.fullmatch(str(operation_id or "")))


def operation_request_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_operation_receipt(project_root: str | Path, operation_id: str) -> dict[str, Any] | None:
    if not valid_operation_id(operation_id):
        raise ValueError("invalid operation_id")
    path = _receipt_path(project_root, operation_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"operation receipt is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("operation_id") != operation_id:
        raise ValueError("operation receipt identity is invalid")
    if not isinstance(payload.get("request_hash"), str) or not isinstance(payload.get("result"), dict):
        raise ValueError("operation receipt shape is invalid")
    return payload


def write_operation_receipt(
    project_root: str | Path,
    operation_id: str,
    request_hash: str,
    result: dict[str, Any],
) -> None:
    if not valid_operation_id(operation_id):
        raise ValueError("invalid operation_id")
    path = _receipt_path(project_root, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        {
            "schema_version": 1,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "result": result,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _receipt_path(project_root: str | Path, operation_id: str) -> Path:
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return Path(project_root) / ".openbrep" / "operations" / f"{digest}.json"
