from __future__ import annotations


def clear_pending_preview_state(session_state) -> None:
    session_state.pending_preview_2d_data = None
    session_state.pending_preview_3d_data = None
    session_state.pending_current_preview_2d_data = None
    session_state.pending_current_preview_3d_data = None
    session_state.pending_preview_warnings = []
    session_state.pending_preview_meta = {"kind": "", "timestamp": "", "source": ""}
    session_state.pending_preview_diff_summary = {}
    session_state.pending_compile_result = None
    session_state.pending_compile_meta = {}
