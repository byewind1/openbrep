"""Project-level OpenBrep context for HSF source directories.

This module reads optional metadata and prompt context from an HSF project's
own ``.openbrep`` directory. It is intentionally read-only: creating or
editing project context remains an explicit project/workflow action.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbrep.hsf_project import HSFProject
from openbrep.knowledge import KnowledgeBase
from openbrep.skills_loader import SkillsLoader


OPENBREP_DIR = ".openbrep"
PROJECT_TOML = "project.toml"
KNOWLEDGE_DIR = "knowledge"
SKILLS_DIR = "skills"
REVISIONS_DIR = "revisions"


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project-level OpenBrep paths and optional metadata."""

    project_root: Path
    metadata_root: Path
    project_toml: Path
    knowledge_dir: Path
    skills_dir: Path
    revisions_dir: Path
    config: dict[str, Any] = field(default_factory=dict)


def resolve_project_context(project: HSFProject | None) -> ProjectContext | None:
    """Return the optional project context for an HSF project."""
    if project is None:
        return None

    project_root = Path(project.root).expanduser()
    metadata_root = project_root / OPENBREP_DIR
    project_toml = metadata_root / PROJECT_TOML
    return ProjectContext(
        project_root=project_root,
        metadata_root=metadata_root,
        project_toml=project_toml,
        knowledge_dir=metadata_root / KNOWLEDGE_DIR,
        skills_dir=metadata_root / SKILLS_DIR,
        revisions_dir=metadata_root / REVISIONS_DIR,
        config=load_project_toml(project_toml),
    )


def load_project_toml(path: str | Path) -> dict[str, Any]:
    """Load a project.toml file if present; malformed files degrade to empty."""
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        return {}
    try:
        data = tomllib.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_project_context_prompt(context: ProjectContext | None) -> str:
    """Render project.toml as compact prompt context."""
    if context is None or not context.config:
        return ""

    lines = [
        "## Project Context",
        "",
        "以下信息来自当前 HSF 项目的 .openbrep/project.toml，优先作为本项目约束。",
    ]
    for key, value in _flatten_toml(context.config):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def load_project_knowledge(context: ProjectContext | None, *, task_type: str = "all") -> str:
    """Load optional project-scoped knowledge from .openbrep/knowledge."""
    if context is None or not context.knowledge_dir.is_dir():
        return ""
    try:
        kb = KnowledgeBase(str(context.knowledge_dir))
        kb.load()
        return kb.get_by_task_type(task_type)
    except Exception:
        return ""


def load_project_skills(context: ProjectContext | None, instruction: str) -> str:
    """Load optional project-scoped skills from .openbrep/skills."""
    if context is None or not context.skills_dir.is_dir():
        return ""
    try:
        loader = SkillsLoader(str(context.skills_dir))
        loader.load()
        return loader.get_for_task(instruction)
    except Exception:
        return ""


def _flatten_toml(data: dict[str, Any], *, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key in sorted(data):
        value = data[key]
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(_flatten_toml(value, prefix=full_key))
        elif isinstance(value, list):
            rows.append((full_key, ", ".join(str(item) for item in value)))
        elif value is not None:
            rows.append((full_key, str(value)))
    return rows
