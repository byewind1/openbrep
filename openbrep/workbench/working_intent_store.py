"""Project-scoped persistence for Workbench goals and keep requirements."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openbrep.workbench.working_intent import initial_intent

STORE_RELATIVE_PATH = Path(".openbrep") / "workbench" / "working_intent.json"
SCHEMA_VERSION = 1


def load_working_intent(
    project_root: str | Path,
    *,
    session_id: str,
    project_epoch: int,
) -> tuple[dict[str, Any], str | None]:
    """Load durable intent, preserving rules while invalidating copied evidence."""
    root = Path(project_root).expanduser().resolve()
    path = root / STORE_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = payload.get("state")
        if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(state, dict):
            raise ValueError("unsupported working intent schema")
        if not all(isinstance(state.get(key), list) for key in ("goals", "constraints", "assumptions", "tasks", "message_refs")):
            raise ValueError("invalid working intent state")
    except FileNotFoundError:
        return initial_intent(session_id, project_epoch), None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return initial_intent(session_id, project_epoch), f"working_intent_load_failed:{type(exc).__name__}"

    state = dict(state)
    origin = str(payload.get("project_identity") or "")
    if origin != str(root):
        # A copied HSF may reuse author constraints, but revision/run evidence
        # belongs to the original project and cannot be asserted in the copy.
        for task in state["tasks"]:
            task["state"] = "invalidated"
            task["run_id"] = None
            task["delivery_ref"] = None
    state["session_id"] = session_id
    state["project_epoch"] = project_epoch
    return state, None


def save_working_intent(project_root: str | Path, state: dict[str, Any]) -> None:
    """Atomically save state under the HSF project's cross-process write lock."""
    root = Path(project_root).expanduser().resolve()
    path = root / STORE_RELATIVE_PATH
    from openbrep.project_write_lock import project_write_lock

    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_identity": str(root),
        "state": state,
    }
    with project_write_lock(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
