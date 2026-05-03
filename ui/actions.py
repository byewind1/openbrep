from __future__ import annotations

from pathlib import Path
from typing import Any

from openbrep.hsf_project import HSFProject
from openbrep.revisions import copy_project_metadata
from openbrep.runtime.pipeline import TaskResult, build_generation_result_plan
from ui.project_activity import record_project_activity
from ui.proposed_preview_controller import clear_pending_preview_state


def has_any_script_content(proj: HSFProject, script_map: list[tuple[str, str, str]]) -> bool:
    return any(proj.get_script(script_type) for script_type, _, _ in script_map)


def apply_generation_plan(
    plan,
    proj: HSFProject,
    gsm_name: str | None,
    session_state,
    capture_last_project_snapshot,
    apply_scripts_to_project,
    bump_main_editor_version,
    *,
    already_applied: bool = False,
) -> tuple[str, list[str]]:
    if plan.mode == "plain_text_only":
        return "", []

    script_map = {block["path"]: block["content"] for block in plan.code_blocks}
    if plan.mode == "auto_apply":
        if not already_applied:
            capture_last_project_snapshot("AI 自动写入")
            apply_scripts_to_project(proj, script_map)
        bump_main_editor_version()
        proj.save_to_disk()
        clear_pending_preview_state(session_state)
        if gsm_name:
            session_state.pending_gsm_name = gsm_name

    code_blocks = []
    for block in plan.code_blocks:
        code_blocks.append(
            f"**{block['label']}**\n```{block['language']}\n{block['content']}\n```"
        )

    return plan.reply_prefix, code_blocks


def apply_generation_result(
    cleaned: dict,
    proj: HSFProject,
    gsm_name: str | None,
    auto_apply: bool,
    session_state,
    capture_last_project_snapshot,
    apply_scripts_to_project,
    bump_main_editor_version,
    *,
    already_applied: bool = False,
) -> tuple[str, list[str]]:
    plan = build_generation_result_plan(
        TaskResult(success=True, scripts=cleaned),
        auto_apply=auto_apply,
        gsm_name=gsm_name,
    )
    return apply_generation_plan(
        plan,
        proj,
        gsm_name,
        session_state,
        capture_last_project_snapshot,
        apply_scripts_to_project,
        bump_main_editor_version,
        already_applied=already_applied,
    )


def finalize_loaded_project(
    proj: HSFProject,
    msg: str,
    pending_gsm_name: str,
    session_state,
    reset_tapir_p0_state,
    bump_main_editor_version,
    *,
    preserve_project_root: bool = False,
) -> tuple[bool, str]:
    if preserve_project_root:
        proj.root = Path(proj.root).expanduser().resolve()
        proj.work_dir = proj.root.parent
        session_state.active_hsf_source_dir = str(proj.root)
    else:
        source_root_raw = getattr(proj, "root", None)
        source_root = Path(source_root_raw) if source_root_raw else None
        proj.work_dir = Path(session_state.work_dir)
        proj.root = proj.work_dir / proj.name
        if source_root is not None:
            copy_project_metadata(source_root, proj.root)
        session_state.active_hsf_source_dir = ""
    session_state.project = proj
    session_state.pending_diffs = {}
    session_state.preview_2d_data = None
    session_state.preview_3d_data = None
    session_state.preview_warnings = []
    session_state.preview_meta = {"kind": "", "timestamp": ""}
    session_state.hsf_save_dialog_open = False
    session_state.hsf_save_dialog_mode = ""
    clear_pending_preview_state(session_state)
    session_state.pending_gsm_name = pending_gsm_name
    session_state.script_revision = 0
    reset_tapir_p0_state()
    bump_main_editor_version()
    record_project_activity(session_state, msg)
    return (True, msg)
