"""Project-level OpenBrep context for HSF source directories.

This module reads optional metadata and prompt context from an HSF project's
own ``.openbrep`` directory. Context files are explicit project artifacts;
runtime learnings may append compact memory records under ``.openbrep/memory``.
"""

from __future__ import annotations

import datetime as _dt
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
KNOWLEDGE_MANIFEST = "manifest.toml"
KNOWLEDGE_PROJECT_TOML = "project.toml"
MEMORY_DIR = "memory"
DECISIONS_FILE = "decisions.md"
SKILLS_DIR = "skills"
REVISIONS_DIR = "revisions"


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project-level OpenBrep paths and optional metadata."""

    project_root: Path
    metadata_root: Path
    project_toml: Path
    knowledge_project_toml: Path
    knowledge_dir: Path
    memory_dir: Path
    decisions_file: Path
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
    knowledge_dir = metadata_root / KNOWLEDGE_DIR
    knowledge_project_toml = knowledge_dir / KNOWLEDGE_PROJECT_TOML
    config = _merge_dicts(
        load_project_toml(project_toml),
        load_project_toml(knowledge_project_toml),
    )
    return ProjectContext(
        project_root=project_root,
        metadata_root=metadata_root,
        project_toml=project_toml,
        knowledge_project_toml=knowledge_project_toml,
        knowledge_dir=knowledge_dir,
        memory_dir=metadata_root / MEMORY_DIR,
        decisions_file=metadata_root / MEMORY_DIR / DECISIONS_FILE,
        skills_dir=metadata_root / SKILLS_DIR,
        revisions_dir=metadata_root / REVISIONS_DIR,
        config=config,
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
    manifest_text = _load_project_knowledge_manifest(context.knowledge_dir, task_type=task_type)
    if manifest_text:
        return manifest_text
    try:
        kb = KnowledgeBase(str(context.knowledge_dir))
        kb.load()
        return kb.get_by_task_type(task_type)
    except Exception:
        return ""


def _load_project_knowledge_manifest(knowledge_dir: Path, *, task_type: str = "all") -> str:
    """Load project knowledge docs declared in .openbrep/knowledge/manifest.toml."""
    manifest = knowledge_dir / KNOWLEDGE_MANIFEST
    if not manifest.is_file():
        return ""

    data = load_project_toml(manifest)
    docs = data.get("docs") if isinstance(data, dict) else None
    if not isinstance(docs, list):
        return ""

    task = (task_type or "all").lower()
    selected: list[tuple[int, str, str]] = []
    for item in docs:
        if not isinstance(item, dict):
            continue
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            continue
        task_types = item.get("task_types")
        if isinstance(task_types, list):
            allowed = {str(value).lower() for value in task_types}
            if task != "all" and "all" not in allowed and task not in allowed:
                continue

        fp = (knowledge_dir / path_value).resolve()
        try:
            fp.relative_to(knowledge_dir.resolve())
        except ValueError:
            continue
        if not fp.is_file() or fp.name == KNOWLEDGE_MANIFEST:
            continue

        try:
            body = fp.read_text(encoding="utf-8")
        except Exception:
            continue

        doc_id = str(item.get("id") or fp.stem)
        try:
            priority = int(item.get("priority", 0))
        except Exception:
            priority = 0
        selected.append((priority, doc_id, body))

    if not selected:
        return ""

    selected.sort(key=lambda row: row[0], reverse=True)
    parts = [f"## Project Knowledge: {doc_id}\n\n{body.strip()}" for _, doc_id, body in selected]
    return "\n\n---\n\n".join(parts)


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


def append_project_decision(
    context: ProjectContext | None,
    *,
    summary: str,
    intent: str,
    instruction: str,
    changed_files: list[str],
    revision_id: str | None = None,
) -> Path | None:
    """Append a compact successful-change record to project memory."""
    if context is None:
        return None
    body = (summary or "").strip()
    if not body:
        return None

    timestamp = _dt.datetime.now().isoformat(timespec="seconds")
    title_revision = f" / {revision_id}" if revision_id else ""
    lines = [
        f"## {timestamp} · {intent or 'UPDATE'}{title_revision}",
        "",
    ]
    if instruction.strip():
        lines.append(f"- Instruction: {instruction.strip()}")
    if changed_files:
        lines.append(f"- Changed files: {', '.join(changed_files)}")
    lines.extend(["", body, ""])

    context.memory_dir.mkdir(parents=True, exist_ok=True)
    with context.decisions_file.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")
    return context.decisions_file


def load_project_memory(context: ProjectContext | None, *, limit_chars: int = 6000) -> str:
    """Load compact project memory that should influence later sessions."""
    if context is None or not context.decisions_file.is_file():
        return ""
    try:
        text = context.decisions_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > limit_chars:
        text = text[-limit_chars:]
    return "## Project Memory: decisions\n\n" + text


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge TOML dicts, with right-hand project knowledge overriding left."""
    if not left:
        return dict(right)
    if not right:
        return dict(left)
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


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
