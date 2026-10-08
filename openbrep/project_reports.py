"""Project-level engineering reports stored under HSF .openbrep metadata."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openbrep.hsf_project import HSFProject
from openbrep.project_context import OPENBREP_DIR

REPORTS_DIR = "reports"
OBJECT_PLAN_PREFIX = "object_plan"


def write_object_plan_report(
    project: HSFProject,
    object_plan: dict[str, Any],
    *,
    instruction: str = "",
    intent: str = "",
    planning_artifact: dict[str, Any] | None = None,
    plan_selection: str = "auto_selected",
) -> Path | None:
    """Persist a generated object plan as project-level JSON and Markdown."""
    if project is None or not object_plan:
        return None

    reports_dir = Path(project.root).expanduser() / OPENBREP_DIR / REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"{OBJECT_PLAN_PREFIX}_{timestamp}"
    payload = {
        "schema_version": 1,
        "kind": "gdl_object_plan",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project.name,
        "intent": intent,
        "instruction": instruction,
        "object_plan": object_plan,
        "planning_artifact": planning_artifact,
        "plan_selection": plan_selection if plan_selection in {"auto_selected", "user_approved"} else "auto_selected",
    }

    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    _atomic_text_write(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_text_write(md_path, _render_object_plan_markdown(payload))
    _write_latest_pointer(reports_dir, json_path, md_path)
    return json_path


def _write_latest_pointer(reports_dir: Path, json_path: Path, md_path: Path) -> None:
    latest = {
        "object_plan_json": json_path.name,
        "object_plan_markdown": md_path.name,
    }
    _atomic_text_write(reports_dir / "latest_object_plan.json", json.dumps(latest, ensure_ascii=False, indent=2) + "\n")


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _render_object_plan_markdown(payload: dict[str, Any]) -> str:
    plan = payload.get("object_plan") or {}
    lines = [
        "# GDL Object Plan",
        "",
        f"- Project: {payload.get('project_name', '')}",
        f"- Intent: {payload.get('intent', '')}",
        f"- Plan selection: {payload.get('plan_selection', 'auto_selected')}",
        f"- Created: {payload.get('created_at', '')}",
    ]
    instruction = str(payload.get("instruction") or "").strip()
    if instruction:
        lines.extend(["", "## User Goal", "", instruction])

    lines.extend(["", "## Object", "", str(plan.get("object_type") or "未命名 GDL 构件")])
    _append_list(lines, "Assumptions", plan.get("assumptions"))
    _append_list(lines, "Geometry", plan.get("geometry"))
    _append_list(lines, "Geometry Parts", plan.get("geometry_parts"))
    _append_list(lines, "Parameters", plan.get("parameters"))
    _append_list(lines, "Parameter Groups", plan.get("parameter_groups"))
    _append_list(lines, "Derived Parameters", plan.get("derived_parameters"))
    _append_list(lines, "Command Candidates", plan.get("command_candidates"))
    _append_list(lines, "3D Script Strategy", plan.get("script_3d_strategy"))
    _append_list(lines, "2D Script Strategy", plan.get("script_2d_strategy"))
    _append_list(lines, "Parameter Script Strategy", plan.get("parameter_script_strategy"))
    _append_list(lines, "UI Script Strategy", plan.get("ui_script_strategy"))
    _append_list(lines, "Materials And Attributes", plan.get("material_strategy"))
    _append_list(lines, "Hotspots And Editability", plan.get("hotspots_and_editability"))
    _append_list(lines, "Validation Checks", plan.get("validation_checks"))
    _append_list(lines, "Knowledge Sources", plan.get("knowledge_sources"))
    _append_list(lines, "Risks To Avoid", plan.get("risks"))
    artifact = payload.get("planning_artifact") or {}
    if artifact:
        lines.extend([
            "", "## Typed Plan", "",
            f"- Status: {artifact.get('status', 'unknown')}",
        ])
        candidate_spec = artifact.get("candidate_spec") or {}
        lines.append(f"- Candidate spec: {candidate_spec.get('spec_id', 'unavailable')}")
        execution = artifact.get("execution_plan") or {}
        lines.append(f"- Execution plan: {execution.get('plan_id', 'unavailable')}")
        lines.append(f"- Plan hash: {execution.get('plan_hash', 'unavailable')}")
        _append_list(lines, "Assumptions And Gaps", [
            f"{item.get('field_path', '')}: {item.get('message', '')}"
            for item in artifact.get("issues", []) if isinstance(item, dict)
        ])
        _append_list(lines, "Explicit User Values", [
            f"{gdl_name} = {source.get('value')} {source.get('unit')}; "
            f"source={source.get('source')}; refs={source.get('source_refs', [])}"
            for gdl_name, source in (artifact.get("parameter_sources") or {}).items()
            if isinstance(source, dict)
        ])
        _append_list(lines, "Requirement Mappings", [
            f"{mapping.get('requirement_id', '')}: parts={mapping.get('part_refs', [])}; "
            f"parameters={mapping.get('parameter_refs', [])}; scripts={mapping.get('script_refs', [])}; "
            f"scenarios={mapping.get('scenario_refs', [])}"
            for mapping in execution.get("requirement_mappings", []) if isinstance(mapping, dict)
        ])
    return "\n".join(lines).rstrip() + "\n"


def _append_list(lines: list[str], title: str, values: Any) -> None:
    items = values if isinstance(values, list) else []
    if not items:
        return
    lines.extend(["", f"## {title}", ""])
    for item in items:
        lines.append(f"- {item}")
