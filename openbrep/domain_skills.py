"""Read-only registry for data-only, versioned GDL domain Skills.

Domain Skills declare observations, Plan policy and required checks. They do
not execute code: every check must resolve to a framework-registered executor.
This registry is intentionally separate from ``skills_loader`` so domain
contracts cannot silently become prompt text.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOP_LEVEL_FIELDS = frozenset({
    "schema_version", "skill_id", "version", "status", "domain", "intents",
    "aliases", "observation", "plan_policy", "requirements", "allowed_variations",
    "fixtures", "methodology_path",
})
_FIELD_TYPES = frozenset({"length", "angle", "number", "integer", "boolean", "string", "enum", "material", "state", "count", "array"})
_STATUSES = frozenset({"development", "proposed", "active", "verified", "deprecated"})
_SPLITS = frozenset({"contract", "train", "validation", "test", "golden"})


@dataclass(frozen=True)
class SkillIssue:
    code: str
    field_path: str
    message: str


@dataclass(frozen=True)
class DomainSkill:
    skill_id: str
    version: str
    status: str
    manifest: dict[str, Any]
    package_path: Path
    prompt_text: str = ""


@dataclass(frozen=True)
class SkillLoadResult:
    skill: DomainSkill | None = None
    issues: tuple[SkillIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.skill is not None and not self.issues


@dataclass(frozen=True)
class DomainSkillFixture:
    """A manifest-bound fixture and its parsed observation, never an executable asset."""

    skill_id: str
    version: str
    fixture_id: str
    kind: str
    license: str
    split: str
    sha256: str
    expected: dict[str, Any]
    payload: dict[str, Any]
    observation: Any


@dataclass(frozen=True)
class FixtureLoadResult:
    fixture: DomainSkillFixture | None = None
    issues: tuple[SkillIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.fixture is not None and not self.issues


@dataclass(frozen=True)
class DomainSkillSelection:
    status: str  # matched | unsupported
    skills: tuple[DomainSkill, ...] = ()


class DomainSkillRegistry:
    """Load and select signed-off data packages without importing their code."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    @classmethod
    def builtin(cls) -> "DomainSkillRegistry":
        return cls(Path(__file__).parent / "data" / "domain_skills")

    @property
    def skill_ids(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(
            path.name for path in self.root.iterdir()
            if path.is_dir() and _SKILL_ID.fullmatch(path.name)
            and (path / "manifest.json").is_file()
        ))

    def load(self, skill_id: str) -> SkillLoadResult:
        skill_id = str(skill_id or "").strip()
        if not _SKILL_ID.fullmatch(skill_id):
            return SkillLoadResult(issues=(SkillIssue("INVALID_SKILL_ID", "skill_id", "invalid skill identifier"),))
        package = (self.root / skill_id).resolve()
        if not package.is_relative_to(self.root) or not package.is_dir():
            return SkillLoadResult(issues=(SkillIssue("SKILL_NOT_FOUND", "skill_id", "domain Skill is not installed"),))
        manifest_path = package / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return SkillLoadResult(issues=(SkillIssue("MANIFEST_INVALID", "manifest", str(exc)),))
        issues = self._validate_manifest(package, manifest)
        if issues:
            return SkillLoadResult(issues=tuple(issues))
        prompt_text = ""
        methodology = manifest.get("methodology_path")
        if methodology:
            target = _contained_path(package, methodology)
            if target is None:
                return SkillLoadResult(issues=(SkillIssue("UNSAFE_PATH", "methodology_path", "path escapes Skill package"),))
            try:
                prompt_text = target.read_text(encoding="utf-8")
            except OSError as exc:
                return SkillLoadResult(issues=(SkillIssue("METHODOLOGY_UNAVAILABLE", "methodology_path", str(exc)),))
        return SkillLoadResult(DomainSkill(
            skill_id=skill_id,
            version=str(manifest["version"]),
            status=str(manifest["status"]),
            manifest=manifest,
            package_path=package,
            prompt_text=prompt_text,
        ))

    def select(self, instruction: str, *, intent: str) -> tuple[DomainSkill, ...]:
        text = str(instruction or "").casefold()
        matches = []
        for skill_id in self.skill_ids:
            result = self.load(skill_id)
            if not result.ok:
                continue
            skill = result.skill
            if skill.status == "deprecated":
                continue
            if str(intent or "").casefold() not in {
                str(value).casefold() for value in skill.manifest["intents"]
            }:
                continue
            aliases = [skill_id, *skill.manifest["aliases"]]
            if any(str(alias).casefold() in text for alias in aliases):
                matches.append(skill)
        return tuple(matches)

    def resolve(self, instruction: str, *, intent: str) -> DomainSkillSelection:
        skills = self.select(instruction, intent=intent)
        if not skills:
            return DomainSkillSelection("unsupported")
        status = "matched" if all(skill.status in {"active", "verified"} for skill in skills) else "unverified"
        return DomainSkillSelection(status, skills)

    def load_fixture(self, skill_id: str, fixture_id: str) -> FixtureLoadResult:
        """Load a package-declared fixture after revalidating provenance and shape.

        This supports synthetic contract tests and future licensed evaluation.
        It never runs package code and does not treat fixture expectations as
        evidence of domain truth.
        """
        loaded = self.load(skill_id)
        if not loaded.ok or loaded.skill is None:
            return FixtureLoadResult(issues=loaded.issues)
        declaration = next((item for item in loaded.skill.manifest["fixtures"]
                            if item.get("fixture_id") == fixture_id), None)
        if declaration is None:
            return FixtureLoadResult(issues=(SkillIssue(
                "FIXTURE_NOT_FOUND", f"fixtures.{fixture_id}",
                "fixture is not declared by this domain Skill",
            ),))
        target = _contained_path(loaded.skill.package_path, str(declaration["path"]))
        if target is None:
            return FixtureLoadResult(issues=(SkillIssue(
                "UNSAFE_PATH", f"fixtures.{fixture_id}.path", "path escapes Skill package",
            ),))
        try:
            raw = target.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return FixtureLoadResult(issues=(SkillIssue(
                "FIXTURE_INVALID", f"fixtures.{fixture_id}.path", str(exc),
            ),))
        digest = hashlib.sha256(raw).hexdigest()
        if digest != declaration.get("sha256"):
            return FixtureLoadResult(issues=(SkillIssue(
                "FIXTURE_HASH_MISMATCH", f"fixtures.{fixture_id}.sha256",
                "fixture changed after manifest validation",
            ),))
        if not isinstance(payload, dict):
            return FixtureLoadResult(issues=(SkillIssue(
                "FIXTURE_INVALID", f"fixtures.{fixture_id}", "fixture must be a JSON object",
            ),))
        issues = self._validate_fixture_payload(loaded.skill, declaration, payload)
        if issues:
            return FixtureLoadResult(issues=tuple(issues))
        observation_data = payload["observation"]
        from openbrep.contracts.object_spec import parse_observation

        parsed = parse_observation({
            "schema_version": 1,
            "observation_id": fixture_id,
            "source": observation_data["source"],
            "source_refs": [f"domain-skill:{skill_id}/{fixture_id}"],
            "items": observation_data["items"],
        })
        if not parsed.ok:
            return FixtureLoadResult(issues=tuple(
                SkillIssue(error.code, f"fixtures.{fixture_id}.{error.field_path}", error.message)
                for error in parsed.errors
            ))
        return FixtureLoadResult(DomainSkillFixture(
            skill_id=skill_id,
            version=loaded.skill.version,
            fixture_id=fixture_id,
            kind=str(declaration["kind"]),
            license=str(declaration["license"]),
            split=str(declaration["split"]),
            sha256=digest,
            expected=dict(declaration["expected"]),
            payload=payload,
            observation=parsed.value,
        ))

    @staticmethod
    def _validate_fixture_payload(
        skill: DomainSkill,
        declaration: dict[str, Any],
        payload: Any,
    ) -> list[SkillIssue]:
        issues: list[SkillIssue] = []
        base = f"fixtures.{declaration['fixture_id']}"

        def issue(code: str, path: str, message: str) -> None:
            issues.append(SkillIssue(code, path, message))

        if payload.get("fixture_schema_version") != 1:
            issue("FIXTURE_SCHEMA_VERSION", f"{base}.fixture_schema_version", "only fixture schema version 1 is supported")
        if payload.get("fixture_id") != declaration.get("fixture_id"):
            issue("FIXTURE_ID_MISMATCH", f"{base}.fixture_id", "fixture id does not match manifest")
        if payload.get("kind") != declaration.get("kind"):
            issue("FIXTURE_KIND_MISMATCH", f"{base}.kind", "fixture kind does not match manifest")
        expected = payload.get("expected")
        if not isinstance(expected, dict) or expected != declaration.get("expected"):
            issue("FIXTURE_EXPECTATION_MISMATCH", f"{base}.expected", "fixture expectations must match the manifest")
            expected = {}
        observation = payload.get("observation")
        if not isinstance(observation, dict) or not isinstance(observation.get("items"), list):
            issue("FIXTURE_OBSERVATION_INVALID", f"{base}.observation", "observation must contain an item array")
            return issues
        if not isinstance(observation.get("source"), str) or not observation["source"].strip():
            issue("FIXTURE_OBSERVATION_INVALID", f"{base}.observation.source", "source must be a non-empty string")
        declarations = skill.manifest["observation"]["fields"]
        seen: set[str] = set()
        for index, item in enumerate(observation["items"]):
            path = f"{base}.observation.items[{index}]"
            if not isinstance(item, dict):
                issue("FIXTURE_OBSERVATION_INVALID", path, "item must be an object")
                continue
            field_path = item.get("field_path")
            if not isinstance(field_path, str) or field_path not in declarations:
                issue("FIXTURE_UNKNOWN_FIELD", f"{path}.field_path", "field is not declared by the Skill")
                continue
            if field_path in seen:
                issue("FIXTURE_DUPLICATE_FIELD", f"{path}.field_path", "field appears more than once")
            seen.add(field_path)
            status = item.get("status", "observed")
            if status == "unknown" and item.get("value") is not None:
                issue("FIXTURE_UNKNOWN_HAS_VALUE", f"{path}.value", "unknown observations must not carry a value")
            if status in {"observed", "inferred"} and item.get("value") is None:
                issue("FIXTURE_VALUE_MISSING", f"{path}.value", "observed/inferred observations require a value")
            declared_unit = declarations[field_path].get("unit")
            if item.get("unit") != declared_unit:
                issue("FIXTURE_UNIT_MISMATCH", f"{path}.unit", f"expected canonical unit {declared_unit!r}")
        if declaration["kind"] == "synthetic" and expected.get("unknown_fields_remain_unknown") is True:
            if not any(item.get("status") == "unknown" and item.get("value") is None
                       for item in observation["items"] if isinstance(item, dict)):
                issue("FIXTURE_EXPECTATION_FAILED", f"{base}.expected.unknown_fields_remain_unknown", "fixture has no preserved unknown observation")
        return issues

    def _validate_manifest(self, package: Path, value: Any) -> list[SkillIssue]:
        issues: list[SkillIssue] = []

        def issue(code: str, path: str, message: str) -> None:
            issues.append(SkillIssue(code, path, message))

        if not isinstance(value, dict):
            return [SkillIssue("MANIFEST_INVALID", "manifest", "manifest must be a JSON object")]
        for key in sorted(set(value) - _TOP_LEVEL_FIELDS):
            issue("UNKNOWN_FIELD", key, "field is not part of the domain Skill manifest schema")
        for required in ("schema_version", "skill_id", "version", "status", "domain", "intents", "aliases", "observation", "plan_policy", "requirements", "allowed_variations", "fixtures"):
            if required not in value:
                issue("MISSING_FIELD", required, "required field is missing")
        if value.get("schema_version") != 1:
            issue("SCHEMA_VERSION", "schema_version", "only schema version 1 is supported")
        if value.get("skill_id") != package.name:
            issue("SKILL_ID_MISMATCH", "skill_id", "manifest id must match package directory")
        if not isinstance(value.get("version"), str) or not value.get("version"):
            issue("INVALID_VERSION", "version", "version must be a non-empty string")
        if value.get("status") not in _STATUSES:
            issue("INVALID_STATUS", "status", "status must be development/proposed/active/verified/deprecated")
        for field in ("domain",):
            if not isinstance(value.get(field), str) or not value.get(field):
                issue("INVALID_VALUE", field, "must be a non-empty string")
        for field in ("intents", "aliases", "allowed_variations"):
            if not isinstance(value.get(field), list) or any(not isinstance(item, str) or not item for item in value.get(field, [])):
                issue("INVALID_VALUE", field, "must be a list of non-empty strings")

        observation = value.get("observation")
        fields = observation.get("fields") if isinstance(observation, dict) else None
        if not isinstance(fields, dict):
            issue("INVALID_VALUE", "observation.fields", "must map field paths to typed declarations")
        else:
            for key in sorted(set(observation) - {"fields"}):
                issue("UNKNOWN_FIELD", f"observation.{key}", "field is not supported")
            for path, declaration in fields.items():
                if not isinstance(path, str) or not path or any(not part for part in path.split(".")):
                    issue("INVALID_FIELD_PATH", f"observation.fields.{path}", "field path must be dot-separated")
                    continue
                if not isinstance(declaration, dict):
                    issue("INVALID_VALUE", f"observation.fields.{path}", "declaration must be an object")
                    continue
                unknown = set(declaration) - {"type", "unit", "required", "unknown_policy", "description", "enum_values"}
                for key in sorted(unknown):
                    issue("UNKNOWN_FIELD", f"observation.fields.{path}.{key}", "field is not supported")
                if declaration.get("type") not in _FIELD_TYPES:
                    issue("INVALID_FIELD_TYPE", f"observation.fields.{path}.type", "unsupported observation type")
                if declaration.get("type") == "length" and declaration.get("unit") != "m":
                    issue("INVALID_UNIT", f"observation.fields.{path}.unit", "length observations use canonical meters")
                if declaration.get("type") == "angle" and declaration.get("unit") != "deg":
                    issue("INVALID_UNIT", f"observation.fields.{path}.unit", "angle observations use canonical degrees")
                if declaration.get("type") not in {"length", "angle"} and declaration.get("unit") is not None:
                    issue("INVALID_UNIT", f"observation.fields.{path}.unit", "unit is only valid for length or angle")
                if declaration.get("unknown_policy") not in {"preserve", "ask", "assumption"}:
                    issue("INVALID_UNKNOWN_POLICY", f"observation.fields.{path}.unknown_policy", "must preserve, ask, or assumption")
                if "required" in declaration and not isinstance(declaration["required"], bool):
                    issue("INVALID_VALUE", f"observation.fields.{path}.required", "required must be boolean")
                if "description" in declaration and not isinstance(declaration["description"], str):
                    issue("INVALID_VALUE", f"observation.fields.{path}.description", "description must be a string")
                if declaration.get("type") == "enum":
                    values = declaration.get("enum_values")
                    if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
                        issue("INVALID_ENUM", f"observation.fields.{path}.enum_values", "enum fields require non-empty string values")
                    elif len(values) != len(set(values)):
                        issue("INVALID_ENUM", f"observation.fields.{path}.enum_values", "enum values must be unique")
                elif "enum_values" in declaration:
                    issue("INVALID_ENUM", f"observation.fields.{path}.enum_values", "enum_values is only valid for enum fields")

        plan_policy = value.get("plan_policy")
        if not isinstance(plan_policy, dict):
            issue("INVALID_VALUE", "plan_policy", "must be an object")
        else:
            allowed_policy = {"unobserved_values", "conflict_policy", "requirement_mapping"}
            for key in sorted(set(plan_policy) - allowed_policy):
                issue("UNKNOWN_FIELD", f"plan_policy.{key}", "field is not supported")
            if plan_policy.get("unobserved_values") not in {"assumption_or_question", "question", "preserve_unknown"}:
                issue("INVALID_PLAN_POLICY", "plan_policy.unobserved_values", "must preserve unknowns or request clarification")
            if plan_policy.get("conflict_policy") not in {"ask_user", "preserve_unknown"}:
                issue("INVALID_PLAN_POLICY", "plan_policy.conflict_policy", "must ask_user or preserve_unknown")
            if plan_policy.get("requirement_mapping") not in {"retain_source_and_field_path"}:
                issue("INVALID_PLAN_POLICY", "plan_policy.requirement_mapping", "unsupported requirement mapping policy")

        requirements = value.get("requirements")
        if not isinstance(requirements, list):
            issue("INVALID_VALUE", "requirements", "must be a list")
        else:
            seen: set[str] = set()
            from openbrep.contracts.bindings import get_executor_spec

            for index, requirement in enumerate(requirements):
                base = f"requirements[{index}]"
                if not isinstance(requirement, dict):
                    issue("INVALID_VALUE", base, "must be an object")
                    continue
                unknown = set(requirement) - {"requirement_id", "text", "kind", "check_id", "params"}
                for key in sorted(unknown):
                    issue("UNKNOWN_FIELD", f"{base}.{key}", "field is not supported")
                req_id = requirement.get("requirement_id")
                if not isinstance(req_id, str) or not req_id:
                    issue("INVALID_VALUE", f"{base}.requirement_id", "must be a non-empty string")
                elif req_id in seen:
                    issue("DUPLICATE_ID", f"{base}.requirement_id", "requirement id must be unique")
                seen.add(req_id if isinstance(req_id, str) else "")
                if not isinstance(requirement.get("text"), str) or not requirement.get("text"):
                    issue("INVALID_VALUE", f"{base}.text", "must be a non-empty string")
                if requirement.get("kind", "check") not in {"check", "constraint", "assumption"}:
                    issue("INVALID_VALUE", f"{base}.kind", "must be check, constraint, or assumption")
                if not isinstance(requirement.get("params", {}), dict):
                    issue("INVALID_VALUE", f"{base}.params", "must be an object")
                check_id = requirement.get("check_id")
                if check_id is not None and get_executor_spec(str(check_id)) is None:
                    issue("UNKNOWN_CHECK_EXECUTOR", f"{base}.check_id", "only registered OpenBrep checks are executable")

        fixtures = value.get("fixtures")
        if not isinstance(fixtures, list):
            issue("INVALID_VALUE", "fixtures", "must be a list")
        else:
            seen_fixtures: set[str] = set()
            for index, fixture in enumerate(fixtures):
                base = f"fixtures[{index}]"
                if not isinstance(fixture, dict):
                    issue("INVALID_VALUE", base, "must be an object")
                    continue
                unknown = set(fixture) - {"fixture_id", "path", "kind", "license", "split", "sha256", "expected"}
                for key in sorted(unknown):
                    issue("UNKNOWN_FIELD", f"{base}.{key}", "field is not supported")
                fixture_id = fixture.get("fixture_id")
                if not isinstance(fixture_id, str) or not fixture_id:
                    issue("INVALID_VALUE", f"{base}.fixture_id", "must be a non-empty string")
                elif fixture_id in seen_fixtures:
                    issue("DUPLICATE_ID", f"{base}.fixture_id", "fixture id must be unique")
                seen_fixtures.add(fixture_id if isinstance(fixture_id, str) else "")
                license_name = fixture.get("license")
                if not isinstance(license_name, str) or not license_name.strip():
                    issue("FIXTURE_LICENSE_MISSING", f"{base}.license", "every fixture must declare its license")
                split = fixture.get("split")
                if split not in _SPLITS:
                    issue("INVALID_FIXTURE_SPLIT", f"{base}.split", "unsupported fixture split")
                path = fixture.get("path")
                target = _contained_path(package, path) if isinstance(path, str) else None
                if target is None or not target.is_file():
                    issue("FIXTURE_UNAVAILABLE", f"{base}.path", "fixture path is missing or escapes the package")
                    continue
                expected_hash = str(fixture.get("sha256") or "")
                actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or expected_hash != actual_hash:
                    issue("FIXTURE_HASH_MISMATCH", f"{base}.sha256", "fixture content hash does not match")
                if fixture.get("kind") not in {"synthetic", "licensed_image", "licensed_project"}:
                    issue("INVALID_FIXTURE_KIND", f"{base}.kind", "unsupported fixture kind")
                if not isinstance(fixture.get("expected"), dict):
                    issue("INVALID_VALUE", f"{base}.expected", "must document expected observations or outcomes")
        if value.get("status") == "verified":
            real_splits = {item.get("split") for item in fixtures if isinstance(item, dict) and item.get("kind") != "synthetic"}
            if not {"validation", "test"}.issubset(real_splits):
                issue("VERIFICATION_EVIDENCE_MISSING", "status", "verified requires licensed validation and test fixtures")
        methodology = value.get("methodology_path")
        if methodology is not None:
            target = _contained_path(package, methodology) if isinstance(methodology, str) else None
            if target is None or not target.is_file() or target.suffix.lower() not in {".md", ".txt"}:
                issue("UNSAFE_PATH", "methodology_path", "methodology must be a Markdown/text file inside the package")
        return issues


def _contained_path(package: Path, relative: str) -> Path | None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = (package / path).resolve()
    return resolved if resolved.is_relative_to(package.resolve()) else None
