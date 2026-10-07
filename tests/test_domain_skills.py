import json
from pathlib import Path

from openbrep.domain_skills import DomainSkillRegistry


def _manifest():
    return {
        "schema_version": 1,
        "skill_id": "cabinet",
        "version": "0.1.0",
        "status": "development",
        "domain": "cabinet",
        "intents": ["create", "image", "modify"],
        "aliases": ["柜体", "cabinet"],
        "observation": {
            "fields": {
                "overall.width": {"type": "length", "unknown_policy": "preserve"},
                "parts.doors.count": {"type": "integer", "unknown_policy": "preserve"},
            }
        },
        "plan_policy": {"unobserved_values": "assumption_or_question"},
        "requirements": [],
        "allowed_variations": [],
        "fixtures": [],
    }


def test_builtin_skill_packages_are_development_only_and_use_synthetic_fixtures():
    registry = DomainSkillRegistry.builtin()

    assert registry.skill_ids == ("cabinet", "lattice_window")
    for skill_id in registry.skill_ids:
        result = registry.load(skill_id)
        assert result.ok
        assert result.skill.status == "development"
        assert result.skill.manifest["fixtures"]
        assert all(item["license"] == "CC0-1.0" for item in result.skill.manifest["fixtures"])
        assert all(item["kind"] == "synthetic" for item in result.skill.manifest["fixtures"])


def test_skill_selection_matches_domain_and_intent_without_prompt_injection():
    registry = DomainSkillRegistry.builtin()

    selected = registry.select("请按参考图生成柜体", intent="image")
    unsupported = registry.select("生成花瓶", intent="create")
    resolution = registry.resolve("请按参考图生成柜体", intent="image")

    assert [skill.skill_id for skill in selected] == ["cabinet"]
    assert unsupported == ()
    assert selected[0].prompt_text == ""
    assert resolution.status == "unverified"


def test_manifest_rejects_unknown_fields_and_unregistered_checks(tmp_path):
    package = tmp_path / "test_skill"
    package.mkdir()
    manifest = _manifest()
    manifest["python_entrypoint"] = "run.py"
    manifest["requirements"] = [{"id": "r1", "check_id": "skill.run_python"}]
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "run.py").write_text("raise RuntimeError()", encoding="utf-8")

    result = DomainSkillRegistry(tmp_path).load("test_skill")

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {"UNKNOWN_FIELD", "UNKNOWN_CHECK_EXECUTOR"}


def test_fixture_hash_license_and_package_paths_are_verified(tmp_path):
    package = tmp_path / "cabinet"
    package.mkdir()
    (package / "fixture.json").write_text('{"kind":"synthetic"}\n', encoding="utf-8")
    manifest = _manifest()
    manifest["fixtures"] = [{
        "fixture_id": "synthetic-1",
        "path": "fixture.json",
        "kind": "synthetic",
        "license": "",
        "split": "contract",
        "sha256": "0" * 64,
        "expected": {"observation_status": "partial"},
    }]
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = DomainSkillRegistry(tmp_path).load("cabinet")

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {"FIXTURE_LICENSE_MISSING", "FIXTURE_HASH_MISMATCH"}


def test_development_fixtures_cannot_be_promoted_to_verified_or_use_wrong_units(tmp_path):
    package = tmp_path / "cabinet"
    (package / "fixtures").mkdir(parents=True)
    fixture = package / "fixtures" / "synthetic.json"
    fixture.write_text("{}\n", encoding="utf-8")
    import hashlib

    manifest = _manifest()
    manifest["status"] = "verified"
    manifest["observation"]["fields"]["overall.width"]["unit"] = "mm"
    manifest["fixtures"] = [{
        "fixture_id": "synthetic",
        "path": "fixtures/synthetic.json",
        "kind": "synthetic",
        "license": "CC0-1.0",
        "split": "test",
        "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "expected": {},
    }]
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = DomainSkillRegistry(tmp_path).load("cabinet")

    assert not result.ok
    assert {issue.code for issue in result.issues} >= {"INVALID_UNIT", "VERIFICATION_EVIDENCE_MISSING"}
