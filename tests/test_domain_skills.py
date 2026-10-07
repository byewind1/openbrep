import json

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


def test_synthetic_domain_fixtures_load_as_typed_observations():
    registry = DomainSkillRegistry.builtin()

    for skill_id in registry.skill_ids:
        package = registry.load(skill_id).skill
        assert package is not None
        for declaration in package.manifest["fixtures"]:
            loaded = registry.load_fixture(skill_id, declaration["fixture_id"])
            assert loaded.ok, [issue.__dict__ for issue in loaded.issues]
            fixture = loaded.fixture
            assert fixture is not None
            assert fixture.version == package.version
            assert fixture.sha256 == declaration["sha256"]
            assert fixture.observation.source == "synthetic"
            declared_fields = package.manifest["observation"]["fields"]
            for item in fixture.observation.items:
                assert item.field_path in declared_fields
                assert item.unit == declared_fields[item.field_path].get("unit")
                if item.status == "unknown":
                    assert item.value is None
            if fixture.expected.get("unknown_fields_remain_unknown"):
                assert any(item.status == "unknown" for item in fixture.observation.items)


def test_skill_selection_matches_domain_and_intent_without_prompt_injection():
    registry = DomainSkillRegistry.builtin()

    selected = registry.select("请按参考图生成柜体", intent="image")
    unsupported = registry.select("生成花瓶", intent="create")
    resolution = registry.resolve("请按参考图生成柜体", intent="image")

    assert [skill.skill_id for skill in selected] == ["cabinet"]
    assert unsupported == ()
    assert selected[0].prompt_text == ""
    assert resolution.status == "unverified"


def test_fixture_loader_rejects_undeclared_fixture_id():
    result = DomainSkillRegistry.builtin().load_fixture("cabinet", "invented")

    assert not result.ok
    assert result.issues[0].code == "FIXTURE_NOT_FOUND"


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


def test_manifest_rejects_unknown_policy_values(tmp_path):
    package = tmp_path / "cabinet"
    package.mkdir()
    manifest = _manifest()
    manifest["plan_policy"].update({"conflict_policy": "trust_image", "requirement_mapping": "guess"})
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = DomainSkillRegistry(tmp_path).load("cabinet")

    assert not result.ok
    assert sum(issue.code == "INVALID_PLAN_POLICY" for issue in result.issues) == 2


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
