from types import SimpleNamespace

from openbrep.domain_skill_selection import package_hash
from openbrep.domain_skills import DomainSkillRegistry
from openbrep.workbench.modeling_plugin_service import ModelingPluginService


def test_standard_skill_markdown_import_edit_and_restore_are_versioned(tmp_path):
    source = tmp_path / "my-joinery.md"
    source.write_text(
        "---\nname: joinery-method\ndescription: Joinery modeling method\nmetadata:\n  version: '1.2.3'\n---\n\n# Joinery\n\nUse a low connection block.\n",
        encoding="utf-8",
    )
    service = ModelingPluginService(SimpleNamespace(project=None))
    service.personal_root = tmp_path / "personal"

    installed = service.install({"source_path": str(source)})
    assert installed["ok"] is True
    assert installed["skill_id"] == "joinery-method"
    current = DomainSkillRegistry(service.personal_root).load("joinery-method").skill
    assert current is not None and current.version == "1.2.3"
    assert "Use a low connection block." in current.prompt_text

    saved = service.save_methodology({
        "skill_id": "joinery-method",
        "methodology": "# Joinery\n\nKeep the low connection block aligned to the inner face.\n",
        "expected_hash": package_hash(current.package_path),
    })
    assert saved["ok"] is True
    assert saved["version"] == "1.2.4"
    assert (service.personal_root / ".versions/joinery-method/1.2.3/manifest.json").is_file()

    edited = DomainSkillRegistry(service.personal_root).load("joinery-method").skill
    assert edited is not None
    updated_manifest = dict(edited.manifest)
    updated_manifest["domain"] = "timber joinery"
    saved_data = service.save_manifest({
        "skill_id": "joinery-method",
        "manifest": updated_manifest,
        "expected_hash": package_hash(edited.package_path),
    })
    assert saved_data["ok"] is True
    assert saved_data["version"] == "1.2.5"
    assert (service.personal_root / ".versions/joinery-method/1.2.4/manifest.json").is_file()

    restored = service.restore({"skill_id": "joinery-method", "version": "1.2.3"})
    assert restored["ok"] is True
    after_restore = DomainSkillRegistry(service.personal_root).load("joinery-method").skill
    assert after_restore is not None
    assert after_restore.version == "1.2.3"
    assert "Use a low connection block." in after_restore.prompt_text


def test_tools_list_uses_runtime_tool_definitions_and_environment_status(tmp_path):
    session = SimpleNamespace(
        project=None,
        compiler_mode="mock",
        converter_path="",
        tapir_service=SimpleNamespace(status_response=lambda: {"ok": True, "tapir": {"archicad_connected": False}}),
    )
    result = ModelingPluginService(session).list_tools()
    assert result["ok"] is True
    tools = {item["id"]: item for item in result["tools"]}
    assert tools["modify_agent"]["tools"]
    assert tools["archicad.tapir"]["status"] == "not_connected"
    assert tools["compiler"]["status"] == "mock"


def test_list_exposes_methodology_diff_when_personal_version_is_newer_than_project_pin(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    service = ModelingPluginService(SimpleNamespace(project=SimpleNamespace(root=project_root)))
    service.personal_root = tmp_path / "personal"
    copied = service.personalize({"skill_id": "chinese-timber-zuodou", "source": "example"})
    assert copied["ok"] is True
    adopted = service.adopt({"skill_id": "chinese-timber-zuodou", "source": "personal"})
    assert adopted["ok"] is True
    current = DomainSkillRegistry(service.personal_root).load("chinese-timber-zuodou").skill
    assert current is not None
    changed = service.save_methodology({
        "skill_id": current.skill_id,
        "methodology": current.prompt_text + "\nPersonal rule: keep the inner faces aligned.\n",
        "expected_hash": package_hash(current.package_path),
    })
    assert changed["ok"] is True

    listing = service.list_plugins()
    personal = next(item for item in listing["plugins"] if item["source"] == "personal")
    assert personal["update_available"] is True
    assert "Personal rule: keep the inner faces aligned." in personal["update_diff"]
