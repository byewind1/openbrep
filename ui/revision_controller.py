from __future__ import annotations

from pathlib import Path

from openbrep.hsf_project import HSFProject
from openbrep.revisions import create_revision, restore_revision
from ui.proposed_preview_controller import clear_pending_preview_state


REVISION_SESSION_KEYS = {
    "revision_notice",
    "revision_message_input",
    "revision_restore_select",
}


def reset_revision_ui_state(session_state) -> None:
    """Clear revision panel state when the active HSF project changes."""
    for key in list(session_state.keys()):
        if key in REVISION_SESSION_KEYS or str(key).startswith("revision_project_"):
            session_state.pop(key, None)


def save_current_project_revision(
    proj: HSFProject | None,
    message: str = "",
    gsm_name: str | None = None,
) -> tuple[bool, str]:
    if proj is None:
        return False, "❌ 当前没有项目"
    try:
        proj.save_to_disk()
        revision = create_revision(proj.root, message.strip(), gsm_name=gsm_name or proj.name)
        return True, f"✅ 已保存版本 `{revision.revision_id}`（{revision.gsm_name}）"
    except Exception as exc:
        return False, f"❌ 保存版本失败：{exc}"


def restore_project_revision(
    proj: HSFProject | None,
    revision_id: str,
    *,
    session_state,
    load_project_from_disk_fn,
    reset_tapir_p0_state_fn,
    bump_main_editor_version_fn,
    message: str | None = None,
) -> tuple[bool, str]:
    if proj is None:
        return False, "❌ 当前没有项目"
    revision_id = (revision_id or "").strip()
    if not revision_id:
        return False, "❌ 请选择要恢复的版本"

    try:
        restored = restore_revision(proj.root, revision_id, message)
        reloaded = load_project_from_disk_fn(str(proj.root))
        session_state.project = reloaded
        session_state.pending_gsm_name = reloaded.name
        session_state.active_hsf_source_dir = str(Path(proj.root).expanduser().resolve())
        session_state.pending_diffs = {}
        session_state.pending_ai_label = ""
        session_state.compile_result = None
        session_state.preview_2d_data = None
        session_state.preview_3d_data = None
        session_state.preview_warnings = []
        session_state.preview_meta = {"kind": "", "timestamp": ""}
        clear_pending_preview_state(session_state)
        reset_tapir_p0_state_fn()
        bump_main_editor_version_fn()
        return True, f"✅ 已恢复 `{revision_id}`，当前最新版本为 `{restored.revision_id}`"
    except Exception as exc:
        return False, f"❌ 恢复版本失败：{exc}"
