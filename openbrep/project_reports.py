"""Project-level engineering reports stored under HSF .openbrep metadata."""

from __future__ import annotations

import json
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
    }

    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render_object_plan_markdown(payload), encoding="utf-8")
    _write_latest_pointer(reports_dir, json_path, md_path)
    return json_path


def _write_latest_pointer(reports_dir: Path, json_path: Path, md_path: Path) -> None:
    latest = {
        "object_plan_json": json_path.name,
        "object_plan_markdown": md_path.name,
    }
    (reports_dir / "latest_object_plan.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _render_object_plan_markdown(payload: dict[str, Any]) -> str:
    plan = payload.get("object_plan") or {}
    lines = [
        "# GDL Object Plan",
        "",
        f"- Project: {payload.get('project_name', '')}",
        f"- Intent: {payload.get('intent', '')}",
        f"- Created: {payload.get('created_at', '')}",
    ]
    instruction = str(payload.get("instruction") or "").strip()
    if instruction:
        lines.extend(["", "## User Goal", "", instruction])

    lines.extend(["", "## Object", "", str(plan.get("object_type") or "未命名 GDL 构件")])
    _append_list(lines, "Geometry", plan.get("geometry"))
    _append_list(lines, "Parameters", plan.get("parameters"))
    _append_list(lines, "3D Script Strategy", plan.get("script_3d_strategy"))
    _append_list(lines, "2D Script Strategy", plan.get("script_2d_strategy"))
    _append_list(lines, "Materials And Attributes", plan.get("material_strategy"))
    _append_list(lines, "Risks To Avoid", plan.get("risks"))
    return "\n".join(lines).rstrip() + "\n"


def _append_list(lines: list[str], title: str, values: Any) -> None:
    items = values if isinstance(values, list) else []
    if not items:
        return
    lines.extend(["", f"## {title}", ""])
    for item in items:
        lines.append(f"- {item}")
