"""Project-level revision snapshots for HSF source directories."""

from __future__ import annotations

import json
import shutil
from difflib import unified_diff
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
    trigger: str = "manual"
    intent: str = ""
    user_instruction: str = ""
    changed_files: list[str] | None = None
    parent_revision_id: str | None = None
    compile: dict[str, Any] | None = None
    explanation: str = ""
    compile_comparison: dict[str, Any] | None = None


def create_revision(
    project_dir: str | Path,
    message: str = "",
    gsm_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    trigger: str = "manual",
    intent: str = "",
    user_instruction: str = "",
    changed_files: list[str] | None = None,
    parent_revision_id: str | None = None,
) -> Revision:
    """Create a new snapshot under ``<project>/.openbrep/revisions``."""
    root = _resolve_project_root(project_dir)
    revision_id = _next_revision_id(root)
    if parent_revision_id is None:
        parent_revision_id = get_latest_revision_id(root)
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

    extra_metadata = metadata or {}
    compile_metadata = extra_metadata.get("compile") or {}
    manifest = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "schema": REVISION_SCHEMA_VERSION,
        "revision_id": revision_id,
        "project_name": root.name,
        "gsm_name": (gsm_name or root.name),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "source_format": "hsf-project",
        "files": files,
        "trigger": trigger,
        "intent": intent,
        "user_instruction": user_instruction,
        "changed_files": list(changed_files or []),
        "parent_revision_id": parent_revision_id,
        "compile": {
            "mode": compile_metadata.get("mode"),
            "success": compile_metadata.get("success"),
            "gsm_size_bytes": compile_metadata.get("gsm_size_bytes"),
            "gsm_path": compile_metadata.get("gsm_path"),
            "parameter_count": compile_metadata.get("parameter_count"),
            "exit_code": compile_metadata.get("exit_code"),
        },
        "explanation": extra_metadata.get("explanation", ""),
        "compile_comparison": extra_metadata.get("compile_comparison"),
        "metadata": extra_metadata,
    }
    _write_json(tmp_dir / "manifest.json", manifest)
    _write_explanation_markdown(tmp_dir, manifest)
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
        trigger="rollback",
        parent_revision_id=get_latest_revision_id(root),
    )


def compare_revisions(project_dir: str | Path, from_revision_id: str, to_revision_id: str) -> str:
    """Return a unified text diff between two revision source snapshots."""
    root = _resolve_project_root(project_dir)
    from_dir = _find_revision_dir(root, from_revision_id)
    to_dir = _find_revision_dir(root, to_revision_id)
    from_manifest = _read_manifest(from_dir)
    to_manifest = _read_manifest(to_dir)
    files = sorted(set(from_manifest.get("files") or []) | set(to_manifest.get("files") or []))

    chunks: list[str] = []
    for rel_path in files:
        from_text = _read_revision_text(from_dir / rel_path)
        to_text = _read_revision_text(to_dir / rel_path)
        if from_text == to_text:
            continue
        chunks.extend(
            unified_diff(
                from_text.splitlines(keepends=True),
                to_text.splitlines(keepends=True),
                fromfile=f"{from_revision_id}/{rel_path}",
                tofile=f"{to_revision_id}/{rel_path}",
            )
        )
        if chunks and not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"

    compile_summary = _compare_compile_metadata(from_revision_id, from_manifest, to_revision_id, to_manifest)
    if compile_summary:
        chunks.append(compile_summary)

    explanation_summary = _compare_explanation_metadata(
        from_revision_id,
        from_manifest,
        to_revision_id,
        to_manifest,
    )
    if explanation_summary:
        chunks.append(explanation_summary)

    compile_comparison_summary = _compare_compile_comparison_metadata(
        from_revision_id,
        from_manifest,
        to_revision_id,
        to_manifest,
    )
    if compile_comparison_summary:
        chunks.append(compile_comparison_summary)

    return "".join(chunks) or f"No source differences between {from_revision_id} and {to_revision_id}.\n"


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


def _read_manifest(revision_dir: Path) -> dict[str, Any]:
    manifest_path = revision_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Revision manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read_revision_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _compare_compile_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_compile = dict(from_manifest.get("compile") or {})
    to_compile = dict(to_manifest.get("compile") or {})
    keys = ["mode", "success", "gsm_path", "gsm_size_bytes", "exit_code"]
    lines = []
    for key in keys:
        if from_compile.get(key) != to_compile.get(key):
            lines.append(f"- {key}: {from_compile.get(key)!r} -> {to_compile.get(key)!r}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"\n## Compile metadata changed ({from_revision_id} -> {to_revision_id})\n{body}\n"


def _compare_explanation_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_explanation = str(from_manifest.get("explanation") or "")
    to_explanation = str(to_manifest.get("explanation") or "")
    if from_explanation == to_explanation:
        return ""

    diff = unified_diff(
        from_explanation.splitlines(keepends=True),
        to_explanation.splitlines(keepends=True),
        fromfile=f"{from_revision_id}/explanation.md",
        tofile=f"{to_revision_id}/explanation.md",
    )
    lines = list(diff)
    if not lines:
        return ""
    return "\n## Explanation changed ({0} -> {1})\n{2}".format(
        from_revision_id,
        to_revision_id,
        "".join(lines),
    )


def _compare_compile_comparison_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_comparison = dict(from_manifest.get("compile_comparison") or {})
    to_comparison = dict(to_manifest.get("compile_comparison") or {})
    keys = [
        "mode",
        "before.success",
        "after.success",
        "size_delta_bytes",
        "param_delta",
    ]
    lines = []
    for key in keys:
        from_value = _nested_manifest_value(from_comparison, key)
        to_value = _nested_manifest_value(to_comparison, key)
        if from_value != to_value:
            lines.append(f"- {key}: {from_value!r} -> {to_value!r}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"\n## Compile comparison changed ({from_revision_id} -> {to_revision_id})\n{body}\n"


def _nested_manifest_value(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _write_explanation_markdown(revision_dir: Path, manifest: dict[str, Any]) -> None:
    explanation = str(manifest.get("explanation") or "").strip()
    if not explanation:
        return

    changed_files = list(manifest.get("changed_files") or [])
    if not changed_files:
        changed_files = list(manifest.get("files") or [])
    compile_result = _format_compile_result(dict(manifest.get("compile") or {}))

    lines = [
        f"# Revision {manifest.get('revision_id')}",
        "",
        "## User Intent",
        "",
        str(manifest.get("user_instruction") or manifest.get("message") or "Not recorded."),
        "",
        "## Engineering Summary",
        "",
        explanation,
        "",
        "## Changed Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in changed_files)
    lines.extend(
        [
            "",
            "## Compile Result",
            "",
            compile_result,
            "",
        ]
    )
    (revision_dir / "explanation.md").write_text("\n".join(lines), encoding="utf-8")


def _format_compile_result(compile_metadata: dict[str, Any]) -> str:
    if not any(value is not None for value in compile_metadata.values()):
        return "Not recorded."

    status = compile_metadata.get("success")
    if status is True:
        label = "Passed"
    elif status is False:
        label = "Failed"
    else:
        label = "Unknown"

    mode = compile_metadata.get("mode") or "unknown"
    parts = [f"{label} ({mode})"]
    if compile_metadata.get("gsm_path"):
        parts.append(f"gsm={compile_metadata['gsm_path']}")
    if compile_metadata.get("gsm_size_bytes") is not None:
        parts.append(f"size={compile_metadata['gsm_size_bytes']} bytes")
    if compile_metadata.get("parameter_count") is not None:
        parts.append(f"parameters={compile_metadata['parameter_count']}")
    if compile_metadata.get("exit_code") is not None:
        parts.append(f"exit_code={compile_metadata['exit_code']}")
    return "; ".join(parts) + "."


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
        trigger=str(manifest.get("trigger") or "manual"),
        intent=str(manifest.get("intent") or ""),
        user_instruction=str(manifest.get("user_instruction") or ""),
        changed_files=list(manifest.get("changed_files") or []),
        parent_revision_id=manifest.get("parent_revision_id"),
        compile=dict(manifest.get("compile") or {}),
        explanation=str(manifest.get("explanation") or ""),
        compile_comparison=dict(manifest.get("compile_comparison") or {})
        if manifest.get("compile_comparison")
        else None,
    )
