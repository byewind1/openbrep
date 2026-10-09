"""Project-local, content-pinned adoption of data-only domain Skills."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbrep.domain_skills import DomainSkill, DomainSkillRegistry, SkillIssue

SELECTION_RELATIVE_PATH = Path(".openbrep/domain_skills/selection.json")
PACKAGE_RELATIVE_PATH = Path(".openbrep/domain_skills/packages")


def package_hash(package: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise ValueError("Skill packages may not contain symlinks")
        digest.update(path.relative_to(package).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class SkillSelectionRecord:
    skill_id: str
    version: str
    content_hash: str
    status: str
    source: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "version": self.version,
                "content_hash": self.content_hash, "status": self.status,
                "source": self.source, "enabled": self.enabled}


@dataclass(frozen=True)
class SelectionReadResult:
    records: tuple[SkillSelectionRecord, ...] = ()
    skills: tuple[DomainSkill, ...] = ()
    issues: tuple[SkillIssue, ...] = ()


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("selections"), list):
        raise ValueError("unsupported domain Skill selection file")
    if any(not isinstance(row, dict) for row in value["selections"]):
        raise ValueError("domain Skill selections must contain objects")
    return value["selections"]


def select_project_skill(project_root: str | Path, skill: DomainSkill, *, source: str = "user") -> SkillSelectionRecord:
    root = Path(project_root).expanduser().resolve()
    digest = package_hash(skill.package_path)
    target = root / PACKAGE_RELATIVE_PATH / skill.skill_id
    selection_path = root / SELECTION_RELATIVE_PATH
    from openbrep.project_write_lock import project_write_lock
    with project_write_lock(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        staged_selection = selection_path.with_name(f".{selection_path.name}.{uuid.uuid4().hex}.tmp")
        backup_package = target.with_name(f".{target.name}.{uuid.uuid4().hex}.old")
        backup_selection = selection_path.with_name(f".{selection_path.name}.{uuid.uuid4().hex}.old")
        package_moved = selection_moved = False
        try:
            shutil.copytree(skill.package_path, temp, symlinks=False)
            if package_hash(temp) != digest:
                raise ValueError("Skill changed while it was being adopted")
            rows = _read(selection_path)
            previous = next((row for row in rows if row.get("skill_id") == skill.skill_id), None)
            record = SkillSelectionRecord(skill.skill_id, skill.version, digest, skill.status, source)
            rows = [row for row in rows if row.get("skill_id") != skill.skill_id]
            rows.append(record.to_dict())
            with staged_selection.open("w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "selections": rows}, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                old_hash = package_hash(target)
                old_version = str((previous or {}).get("version") or "unknown")
                archive_root = root / ".openbrep" / "domain_skills" / "versions" / skill.skill_id
                archive_root.mkdir(parents=True, exist_ok=True)
                archive = archive_root / f"{old_version}-{old_hash[:12]}"
                if archive.exists():
                    archive = archive_root / f"{old_version}-{old_hash[:12]}-{uuid.uuid4().hex[:8]}"
                shutil.copytree(target, archive, symlinks=False)
                os.replace(target, backup_package)
                package_moved = True
            if selection_path.exists():
                os.replace(selection_path, backup_selection)
                selection_moved = True
            os.replace(temp, target)
            os.replace(staged_selection, selection_path)
            shutil.rmtree(backup_package, ignore_errors=True)
            backup_selection.unlink(missing_ok=True)
            return record
        except Exception:
            if target.exists() and package_moved:
                shutil.rmtree(target, ignore_errors=True)
            if backup_package.exists():
                os.replace(backup_package, target)
            if selection_path.exists() and selection_moved:
                selection_path.unlink(missing_ok=True)
            if backup_selection.exists():
                os.replace(backup_selection, selection_path)
            raise
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            staged_selection.unlink(missing_ok=True)
            shutil.rmtree(backup_package, ignore_errors=True)
            backup_selection.unlink(missing_ok=True)


def set_project_skill_enabled(project_root: str | Path, skill_id: str, enabled: bool) -> None:
    root = Path(project_root).expanduser().resolve()
    path = root / SELECTION_RELATIVE_PATH
    from openbrep.project_write_lock import project_write_lock
    with project_write_lock(root):
        rows = _read(path)
        for row in rows:
            if row.get("skill_id") == skill_id:
                row["enabled"] = bool(enabled)
                _write_unlocked(path, rows)
                return
        raise KeyError(skill_id)


def load_project_skill_selections(project_root: str | Path) -> SelectionReadResult:
    root = Path(project_root).expanduser().resolve()
    from openbrep.project_write_lock import project_write_lock
    with project_write_lock(root):
        return _load_project_skill_selections_locked(root)


def _load_project_skill_selections_locked(root: Path) -> SelectionReadResult:
    rows = _read(root / SELECTION_RELATIVE_PATH)
    registry = DomainSkillRegistry(root / PACKAGE_RELATIVE_PATH)
    records: list[SkillSelectionRecord] = []
    skills: list[DomainSkill] = []
    issues: list[SkillIssue] = []
    for index, row in enumerate(rows):
        try:
            record = SkillSelectionRecord(str(row["skill_id"]), str(row["version"]),
                                          str(row["content_hash"]), str(row["status"]),
                                          str(row.get("source", "unknown")), bool(row.get("enabled", True)))
        except (KeyError, TypeError):
            issues.append(SkillIssue("SELECTION_INVALID", f"selections[{index}]", "malformed selection"))
            continue
        records.append(record)
        if not record.enabled:
            continue
        loaded = registry.load(record.skill_id)
        if not loaded.ok or loaded.skill is None:
            issues.extend(loaded.issues)
            continue
        try:
            digest = package_hash(loaded.skill.package_path)
        except (OSError, ValueError) as exc:
            issues.append(SkillIssue("SKILL_PACKAGE_UNSAFE", record.skill_id, str(exc)))
            continue
        if loaded.skill.version != record.version or digest != record.content_hash:
            issues.append(SkillIssue("SKILL_VERSION_CHANGED", record.skill_id, "adopted content changed; explicitly adopt a version to update"))
            continue
        skills.append(loaded.skill)
    return SelectionReadResult(tuple(records), tuple(skills), tuple(issues))


def _write_unlocked(path: Path, rows: list[dict[str, Any]]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "selections": rows}, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
