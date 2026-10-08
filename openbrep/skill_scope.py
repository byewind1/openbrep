"""Resolve the two explicit scopes for user-authored GDL methods."""

from __future__ import annotations

import os
from pathlib import Path


def personal_skills_dir() -> Path:
    """Return the user's shared OpenBrep skill library, with a test override."""
    override = os.environ.get("OPENBREP_PERSONAL_SKILLS_DIR")
    return Path(override).expanduser() if override else Path.home() / ".openbrep" / "skills"


def skills_dir_for_scope(scope: str, project_root: str | Path) -> Path:
    """Resolve a project-local or user-wide artifact directory."""
    root = Path(project_root).expanduser()
    if scope == "project":
        return root / ".openbrep" / "skills"
    if scope == "personal":
        return personal_skills_dir()
    raise ValueError(f"Unsupported skill scope: {scope}")
