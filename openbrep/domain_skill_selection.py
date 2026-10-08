"""Version-pinned project selection for data-only domain Skills."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbrep.domain_skills import DomainSkill, DomainSkillRegistry, SkillIssue

SELECTION_RELATIVE_PATH = Path(".openbrep") / "domain_skills" / "selection.json"
SELECTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SkillSelectionRecord:
    skill_id: str
    version: str
    content_hash: str
    status: str
    source: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "status": self.status,
            "source": self.source,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class SelectionReadResult:
    records: tuple[SkillSelectionRecord, ...] = ()
    skills: tuple[DomainSkill, ...] = ()
    issues: tuple[SkillIssue, ...] = ()


def skill_requirement_rows(skills: tuple[DomainSkill, ...] | list[DomainSkill]) -> list[dict[str, Any]]:
    """Turn declared package checks into source-bound requirements for this run."""
    rows = []
    for skill in skills:
        source = f"domain-skill:{skill.skill_id}@{skill.version}#{skill.content_hash}"
        for requirement in skill.manifest.get("requirements", []):
            if not isinstance(requirement, dict):
                continue
            rows.append({
                **requirement,
                "requirement_id": f"{skill.skill_id}:{requirement.get('requirement_id', '')}",
                "source": source,
            })
    return rows


def select_project_skill(project_root: str | Path, skill: DomainSkill) -> SkillSelectionRecord:
    """Explicitly pin one validated Skill version/hash in the project."""
    root = Path(project_root).expanduser().resolve()
    actual_hash = _package_hash(skill.package_path)
    if not skill.content_hash or actual_hash != skill.content_hash:
        raise ValueError("Skill content changed after validation; reload it before selection")
    path = root / SELECTION_RELATIVE_PATH
    from openbrep.project_write_lock import project_write_lock

    with project_write_lock(root):
        payload = _read_payload(path)
        selected = {item["skill_id"]: item for item in payload["selections"]}
        source_root = skill.source_root or skill.package_path.parent
        source = _source_label(root, source_root)
        record = SkillSelectionRecord(
            skill_id=skill.skill_id,
            version=skill.version,
            content_hash=skill.content_hash,
            status=skill.status,
            source=source,
        )
        selected[skill.skill_id] = record.to_dict()
        _write_payload(path, list(selected.values()))
    return record


def set_project_skill_enabled(project_root: str | Path, skill_id: str, enabled: bool) -> None:
    path = Path(project_root).expanduser().resolve() / SELECTION_RELATIVE_PATH
    from openbrep.project_write_lock import project_write_lock

    with project_write_lock(path.parent.parent.parent):
        payload = _read_payload(path)
        found = False
        for item in payload["selections"]:
            if item.get("skill_id") == skill_id:
                item["enabled"] = bool(enabled)
                found = True
        if not found:
            raise KeyError(f"Skill is not selected: {skill_id}")
        _write_payload(path, payload["selections"])


def load_project_skill_selections(
    project_root: str | Path,
    registry: DomainSkillRegistry | None = None,
) -> SelectionReadResult:
    root = Path(project_root).expanduser().resolve()
    payload = _read_payload(root / SELECTION_RELATIVE_PATH)
    registry = registry or DomainSkillRegistry.for_project(root)
    records: list[SkillSelectionRecord] = []
    skills: list[DomainSkill] = []
    issues: list[SkillIssue] = []
    for index, item in enumerate(payload["selections"]):
        try:
            record = SkillSelectionRecord(
                skill_id=str(item["skill_id"]),
                version=str(item["version"]),
                content_hash=str(item["content_hash"]),
                status=str(item["status"]),
                source=str(item["source"]),
                enabled=bool(item.get("enabled", True)),
            )
        except (KeyError, TypeError) as exc:
            issues.append(SkillIssue("SELECTION_INVALID", f"selections[{index}]", str(exc)))
            continue
        records.append(record)
        if not record.enabled:
            continue
        loaded = registry.load(record.skill_id)
        if not loaded.ok or loaded.skill is None:
            issues.extend(loaded.issues or (SkillIssue("SKILL_UNAVAILABLE", record.skill_id, "selected Skill is unavailable"),))
            continue
        if loaded.skill.version != record.version or loaded.skill.content_hash != record.content_hash:
            issues.append(SkillIssue(
                "SKILL_VERSION_CHANGED", record.skill_id,
                "selected Skill content changed; explicitly reselect to adopt the new version",
            ))
            continue
        skills.append(loaded.skill)
    return SelectionReadResult(tuple(records), tuple(skills), tuple(issues))


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SELECTION_SCHEMA_VERSION, "selections": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise ValueError("unsupported domain Skill selection schema")
    if not isinstance(payload.get("selections"), list):
        raise ValueError("domain Skill selections must be an array")
    return payload


def _write_payload(path: Path, selections: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps({"schema_version": SELECTION_SCHEMA_VERSION, "selections": selections},
                       ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _source_label(project_root: Path, source_root: Path) -> str:
    try:
        return f"project:{source_root.resolve().relative_to(project_root).as_posix()}"
    except ValueError:
        if source_root.resolve() == (Path(__file__).parent / "data" / "domain_skills").resolve():
            return "builtin"
        return "user"


def _package_hash(package: Path) -> str:
    package = package.resolve()
    digest = hashlib.sha256()
    for target in sorted(path for path in package.rglob("*") if path.is_file()):
        relative = target.resolve().relative_to(package)
        digest.update(relative.as_posix().encode("utf-8") + b"\0" + target.read_bytes())
    return digest.hexdigest()
