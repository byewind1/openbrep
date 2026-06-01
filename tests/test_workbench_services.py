from pathlib import Path
from types import SimpleNamespace

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from openbrep.workbench.compiler_service import WorkbenchCompilerService, parse_compile_issue
from openbrep.workbench.memory_service import WorkbenchMemoryService
from openbrep.workbench.project_service import WorkbenchProjectService
from openbrep.workbench.settings_service import WorkbenchSettingsService
from openbrep.workbench.tapir_service import WorkbenchTapirService


def test_settings_service_updates_compiler_settings_and_persists_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    session = SimpleNamespace(
        compiler_mode="mock",
        converter_path="",
        output_dir="",
        config=config,
        config_path=config_path,
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_compiler_settings({
        "mode": "lp",
        "converter_path": "/Applications/LP_XMLConverter",
        "output_dir": str(tmp_path / "out"),
    })

    reloaded = GDLAgentConfig.load(str(config_path))
    assert response["ok"] is True
    assert response["compiler"]["mode"] == "lp"
    assert reloaded.compiler.path == "/Applications/LP_XMLConverter"
    assert reloaded.output_dir == str(tmp_path / "out")


def test_compiler_service_compiles_loaded_project_with_injected_mock_compiler(tmp_path):
    project = HSFProject.create_new("ServiceShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = SimpleNamespace(
        project=project,
        source_path=hsf_dir,
        output_dir="",
        compiler_mode="mock",
        converter_path="",
        last_compile_output_path="",
    )

    class FakeCompiler:
        def hsf2libpart(self, _hsf_path, output_gsm):
            Path(output_gsm).parent.mkdir(parents=True, exist_ok=True)
            Path(output_gsm).write_bytes(b"gsm")
            return CompileResult(success=True, output_path=output_gsm, mode="mock")

    service = WorkbenchCompilerService(
        session,
        real_compiler_factory=lambda _path: FakeCompiler(),
        mock_compiler_factory=FakeCompiler,
    )

    response = service.compile_project({})

    assert response["ok"] is True
    assert response["compile"]["output_path"].endswith("ServiceShelf.gsm")
    assert response["compile"]["gsm_size_bytes"] == 3
    assert session.last_compile_output_path == response["compile"]["output_path"]


def test_compiler_service_parses_lp_compile_issue_locations():
    script, line, message = parse_compile_issue("3d.gdl line 12: missing ENDIF")

    assert script == "scripts/3d.gdl"
    assert line == 12
    assert message == "missing ENDIF"


def test_project_service_loads_hsf_directory_and_updates_session(tmp_path):
    project = HSFProject.create_new("ServiceLoadedShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = SimpleNamespace(
        project=None,
        source="demo",
        source_path=None,
        recent_project_paths=[],
        config=GDLAgentConfig(),
        config_path=tmp_path / "config.toml",
        snapshot=lambda: {"project": {"name": "ServiceLoadedShelf"}},
    )
    service = WorkbenchProjectService(session, real_compiler_factory=lambda _path: None)

    response = service.load_hsf_directory(str(hsf_dir))

    assert response["ok"] is True
    assert session.source == "hsf"
    assert session.source_path == hsf_dir.resolve()
    assert session.recent_project_paths == [str(hsf_dir.resolve())]


def test_assistant_service_extracts_classified_code_blocks():
    service = WorkbenchAssistantService(SimpleNamespace())

    response = service.extract_assistant_code_blocks({
        "content": "```gdl\n! scripts/3d.gdl\nBLOCK A, B, ZZYZX\n```",
    })

    assert response["ok"] is True
    assert response["blocks"][0]["script_name"] == "3d.gdl"
    assert "BLOCK A" in response["blocks"][0]["content"]


def test_memory_service_reports_empty_status_without_loaded_project():
    service = WorkbenchMemoryService(SimpleNamespace(source_path=None))

    response = service.memory_status()

    assert response["ok"] is True
    assert response["memory"]["memory_root"] == ""
    assert response["memory"]["lesson_count"] == 0


def test_tapir_service_normalizes_missing_parameter_edits():
    calls = []

    class FakeAdapter:
        def apply_param_edits(self, edits):
            calls.append(edits)
            return {"ok": True}

    service = WorkbenchTapirService(FakeAdapter())

    response = service.apply_param_edits({"param_edits": []})

    assert response == {"ok": True}
    assert calls == [None]
