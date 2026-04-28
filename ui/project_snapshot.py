from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable


def capture_last_project_snapshot(session_state, label: str, *, deepcopy_fn=deepcopy) -> None:
    proj = session_state.get("project")
    if proj is None:
        return
    session_state.last_project_snapshot = {
        "project": deepcopy_fn(proj),
        "pending_gsm_name": session_state.get("pending_gsm_name", ""),
        "script_revision": int(session_state.get("script_revision", 0)),
    }
    session_state.last_project_snapshot_label = label
    session_state.last_project_snapshot_meta = {
        "label": label,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def restore_last_project_snapshot(
    session_state,
    *,
    bump_main_editor_version_fn: Callable[[], int],
    deepcopy_fn=deepcopy,
) -> tuple[bool, str]:
    snap = session_state.get("last_project_snapshot")
    if not snap:
        return (False, "❌ 没有可恢复的上一次 AI 写入")

    session_state.project = deepcopy_fn(snap["project"])
    session_state.pending_gsm_name = snap.get("pending_gsm_name", "")
    session_state.script_revision = int(snap.get("script_revision", 0))
    session_state.pending_diffs = {}
    session_state.pending_ai_label = ""
    session_state.preview_2d_data = None
    session_state.preview_3d_data = None
    session_state.preview_warnings = []
    session_state.preview_meta = {"kind": "", "timestamp": ""}
    bump_main_editor_version_fn()
    label = session_state.get("last_project_snapshot_label") or "AI 写入"
    session_state.last_project_snapshot = None
    session_state.last_project_snapshot_meta = {}
    session_state.last_project_snapshot_label = ""
    return (True, f"✅ 已撤销上次 {label}")
