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
