"""Project-level revision snapshots for HSF source directories."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REVISION_SCHEMA_VERSION = 1
OPENBREP_DIR = ".openbrep"
REVISIONS_DIR = "revisions"
LATEST_FILE = "latest"


@dataclass(frozen=True)
class Revision:
    """Metadata for a project revision snapshot."""

    revision_id: str
    project_name: str
    gsm_name: str
    created_at: str
    message: str
    files: list[str]
    path: Path


def create_revision(
    project_dir: str | Path,
    message: str = "",
    gsm_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Revision:
    """Create a new snapshot under ``<project>/.openbrep/revisions``."""
    root = _resolve_project_root(project_dir)
    revision_id = _next_revision_id(root)
    revision_dir = _revisions_root(root) / revision_id
    tmp_dir = revision_dir.with_name(f".{revision_id}.tmp")

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    files = _collect_source_files(root)
    if not files:
        shutil.rmtree(tmp_dir)
        raise ValueError(f"No versionable HSF source files found in {root}")

    for rel_path in files:
        src = root / rel_path
        dst = tmp_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    manifest = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "revision_id": revision_id,
        "project_name": root.name,
        "gsm_name": (gsm_name or root.name),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "source_format": "hsf-project",
        "files": files,
        "metadata": metadata or {},
    }
    _write_json(tmp_dir / "manifest.json", manifest)
    tmp_dir.rename(revision_dir)
    _write_latest(root, revision_id)

    return _revision_from_manifest(revision_dir, manifest)


def list_revisions(project_dir: str | Path) -> list[Revision]:
    """Return known revisions in creation order."""
    root = _resolve_project_root(project_dir)
    revisions_dir = _revisions_root(root)
    if not revisions_dir.exists():
        return []

    revisions: list[Revision] = []
    for revision_dir in sorted(revisions_dir.iterdir()):
        if not revision_dir.is_dir() or not revision_dir.name.startswith("r"):
            continue
        manifest_path = revision_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        revisions.append(_revision_from_manifest(revision_dir, manifest))
    return revisions


def restore_revision(
    project_dir: str | Path,
    revision_id: str,
    message: str | None = None,
) -> Revision:
    """
    Restore a snapshot into the HSF project directory and record it as a new
    latest revision.
    """
    root = _resolve_project_root(project_dir)
    source_revision = _find_revision_dir(root, revision_id)
    manifest_path = source_revision / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Revision manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = list(manifest.get("files") or [])
    if not files:
        raise ValueError(f"Revision {revision_id} has no source files")

    _remove_managed_source_files(root)
    for rel_path in files:
        src = source_revision / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Revision file not found: {src}")
        dst = root / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    restore_message = message or f"Restore {revision_id}"
    return create_revision(
        root,
        message=restore_message,
        gsm_name=str(manifest.get("gsm_name") or root.name),
        metadata={"restored_from": revision_id},
    )


def get_latest_revision_id(project_dir: str | Path) -> str | None:
    """Return the latest revision id, if present."""
    root = _resolve_project_root(project_dir)
    latest_path = root / OPENBREP_DIR / LATEST_FILE
    if not latest_path.exists():
        return None
    value = latest_path.read_text(encoding="utf-8").strip()
    return value or None


def copy_project_metadata(source_project_dir: str | Path, target_project_dir: str | Path) -> bool:
    """Copy project-level OpenBrep metadata, including revisions, between HSF roots."""
    source_root = Path(source_project_dir).expanduser().resolve()
    target_root = Path(target_project_dir).expanduser().resolve()
    if source_root == target_root:
        return False

    source_meta = source_root / OPENBREP_DIR
    if not source_meta.exists() or not source_meta.is_dir():
        return False

    target_meta = target_root / OPENBREP_DIR
    target_meta.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_meta, target_meta, dirs_exist_ok=True)
    return True


def is_hsf_project_dir(project_dir: str | Path) -> bool:
    """Return True when a directory looks like an HSF project root."""
    root = Path(project_dir)
    return root.is_dir() and ((root / "libpartdata.xml").exists() or (root / "scripts").is_dir())


def _resolve_project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"HSF project directory not found: {root}")
    if not is_hsf_project_dir(root):
        raise ValueError(f"Not an HSF project directory: {root}")
    return root


def _revisions_root(project_root: Path) -> Path:
    return project_root / OPENBREP_DIR / REVISIONS_DIR


def _next_revision_id(project_root: Path) -> str:
    revisions_dir = _revisions_root(project_root)
    max_number = 0
    if revisions_dir.exists():
        for path in revisions_dir.iterdir():
            if path.is_dir() and path.name.startswith("r") and path.name[1:].isdigit():
                max_number = max(max_number, int(path.name[1:]))
    return f"r{max_number + 1:04d}"


def _collect_source_files(project_root: Path) -> list[str]:
    files: list[str] = []

    for path in sorted(project_root.glob("*.xml")):
        if path.is_file():
            files.append(path.name)

    scripts_dir = project_root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*")):
            if path.is_file():
                files.append(path.relative_to(project_root).as_posix())

    return files


def _remove_managed_source_files(project_root: Path) -> None:
    for rel_path in _collect_source_files(project_root):
        path = project_root / rel_path
        if path.exists():
            path.unlink()


def _find_revision_dir(project_root: Path, revision_id: str) -> Path:
    revision_dir = _revisions_root(project_root) / revision_id
    if not revision_dir.is_dir():
        raise FileNotFoundError(f"Revision not found: {revision_id}")
    return revision_dir


def _write_latest(project_root: Path, revision_id: str) -> None:
    latest_path = project_root / OPENBREP_DIR / LATEST_FILE
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(f"{revision_id}\n", encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _revision_from_manifest(revision_dir: Path, manifest: dict[str, Any]) -> Revision:
    return Revision(
        revision_id=str(manifest["revision_id"]),
        project_name=str(manifest.get("project_name") or revision_dir.parent.parent.name),
        gsm_name=str(manifest.get("gsm_name") or manifest.get("project_name") or revision_dir.parent.parent.name),
        created_at=str(manifest.get("created_at") or ""),
        message=str(manifest.get("message") or ""),
        files=list(manifest.get("files") or []),
        path=revision_dir,
    )
