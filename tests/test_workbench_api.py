from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskResult
from openbrep.workbench_api import (
    WorkbenchSession,
    apply_parameter_values,
    build_demo_project,
    build_demo_snapshot,
    preview_payload,
    route_rpc,
)


def test_build_demo_snapshot_contains_project_parameters_and_preview():
    snapshot = build_demo_snapshot()

    assert snapshot["project"]["name"] == "Demo Bookshelf"
    names = [param["name"] for param in snapshot["parameters"]]
    assert {"A", "B", "ZZYZX", "shelf_count", "has_back_panel"}.issubset(names)
    assert snapshot["preview"]["meshes"]


def test_preview_payload_uses_parameter_overrides_without_mutating_project():
    project = build_demo_project()
    before = project.get_parameter("A").value

    payload = preview_payload(project, {"A": 2.4})

    assert project.get_parameter("A").value == before
    assert payload["meshes"]


def test_apply_parameter_values_updates_project_values():
    project = build_demo_project()

    changed = apply_parameter_values(project, {"shelf_count": 7, "has_back_panel": True})

    assert changed == {"shelf_count": 7, "has_back_panel": True}
    assert project.get_parameter("shelf_count").value == "7"
    assert project.get_parameter("has_back_panel").value == "1"


def test_route_rpc_preview_returns_preview_for_overrides():
    response = route_rpc("POST", "/api/preview", {"parameters": {"A": 2.2}})

    assert response["ok"] is True
    assert response["preview"]["meshes"]


def test_workbench_session_loads_hsf_directory_and_snapshots_project(tmp_path):
    project = HSFProject.create_new("LoadedShelf", str(tmp_path))
    project.parameters.append(GDLParameter("shelf_count", "Integer", "Shelves", "4"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    response = session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    assert response["ok"] is True
    assert response["project"]["name"] == "LoadedShelf"
    assert response["project"]["source"] == "hsf"
    assert response["project"]["path"] == str(hsf_dir)
    assert [param["name"] for param in response["parameters"]] == [
        "A",
        "B",
        "ZZYZX",
        "shelf_count",
    ]
    assert response["preview"]["meshes"]


def test_workbench_session_tracks_recent_projects_and_closes_current_project(tmp_path):
    first = HSFProject.create_new("RecentOne", str(tmp_path / "one")).save_to_disk()
    second = HSFProject.create_new("RecentTwo", str(tmp_path / "two")).save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(first)})
    session.route("POST", "/api/project/load", {"path": str(second)})
    recent = session.route("GET", "/api/project/recent")
    closed = session.route("POST", "/api/project/close", {})

    assert recent["ok"] is True
    assert [item["path"] for item in recent["projects"]][:2] == [str(second), str(first)]
    assert all(item["exists"] for item in recent["projects"][:2])
    assert closed["ok"] is True
    assert closed["project"]["source"] == "demo"
    assert "path" not in closed["project"]


def test_workbench_session_imports_single_gdl_file_as_hsf_project(tmp_path):
    gdl_path = tmp_path / "spiral stair.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\nADDZ 1\n", encoding="utf-8")

    session = WorkbenchSession()
    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True
    assert response["imported_from"] == str(gdl_path)
    assert response["project"]["source"] == "hsf"
    assert response["project"]["path"].endswith("spiral stair")
    imported = HSFProject.load_from_disk(response["project"]["path"])
    assert imported.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nADDZ 1\n"
    assert session.route("GET", "/api/project/recent")["projects"][0]["path"] == response["project"]["path"]


def test_workbench_session_rejects_non_gdl_import(tmp_path):
    text_path = tmp_path / "notes.txt"
    text_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

    session = WorkbenchSession()
    response = session.route("POST", "/api/project/import-gdl", {"path": str(text_path)})

    assert response["ok"] is False
    assert "Unsupported file type" in response["error"]


def test_workbench_session_choose_project_directory_loads_selected_hsf(tmp_path):
    project = HSFProject.create_new("ChosenShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(directory_chooser=lambda: str(hsf_dir))
    response = session.route("POST", "/api/dialog/open-directory", {})

    assert response["ok"] is True
    assert response["path"] == str(hsf_dir)
    assert response["project"]["name"] == "ChosenShelf"


def test_workbench_session_choose_project_directory_handles_cancel():
    session = WorkbenchSession(directory_chooser=lambda: "")

    response = session.route("POST", "/api/dialog/open-directory", {})

    assert response["ok"] is False
    assert response["cancelled"] is True


def test_workbench_session_apply_persists_loaded_hsf_parameters(tmp_path):
    project = HSFProject.create_new("PersistedShelf", str(tmp_path))
    project.parameters.append(GDLParameter("shelf_count", "Integer", "Shelves", "4"))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/apply", {"parameters": {"shelf_count": 8}})

    reloaded = HSFProject.load_from_disk(str(hsf_dir))
    assert response["ok"] is True
    assert reloaded.get_parameter("shelf_count").value == "8"


def test_workbench_session_compile_loaded_hsf_project_with_mock_compiler(tmp_path):
    project = HSFProject.create_new("CompiledShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    output_dir = tmp_path / "out"

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/compile", {"output_dir": str(output_dir)})

    assert response["ok"] is True
    assert response["compile"]["success"] is True
    assert response["compile"]["mode"] == "mock"
    assert response["compile"]["output_path"].endswith("CompiledShelf.gsm")
    assert (output_dir / "CompiledShelf.gsm").exists()


def test_workbench_session_lists_project_scripts(tmp_path):
    project = HSFProject.create_new("ScriptListShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("GET", "/api/project/scripts")

    names = [script["name"] for script in response["scripts"]]
    assert response["ok"] is True
    assert names[:8] == [
        "3d.gdl",
        "2d.gdl",
        "1d.gdl",
        "vl.gdl",
        "pr.gdl",
        "ui.gdl",
        "paramlist.xml",
        "libpartdata.xml",
    ]
    assert response["scripts"][0]["path"] == "scripts/3d.gdl"
    assert response["scripts"][0]["exists"] is True
    assert response["scripts"][2]["exists"] is False


def test_workbench_session_reads_project_script_content(tmp_path):
    project = HSFProject.create_new("ReadScriptShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 1\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("GET", "/api/project/script/3d.gdl")

    assert response["ok"] is True
    assert response["name"] == "3d.gdl"
    assert response["path"] == "scripts/3d.gdl"
    assert "ADDZ 1" in response["content"]


def test_workbench_session_saves_project_script_content(tmp_path):
    project = HSFProject.create_new("SaveScriptShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route(
        "POST",
        "/api/project/script/3d.gdl",
        {"content": "BLOCK A, B, ZZYZX\nADDZ 2\n"},
    )

    reloaded = HSFProject.load_from_disk(str(hsf_dir))
    assert response["ok"] is True
    assert response["success"] is True
    assert response["saved_at"]
    assert "ADDZ 2" in reloaded.get_script(ScriptType.SCRIPT_3D)


def test_workbench_session_mock_compile_returns_diagnostics(tmp_path):
    project = HSFProject.create_new("MockCompileDiagnostics", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "FOR i = 1 TO 2\nBLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/compile/mock", {"output_dir": str(tmp_path / "out")})

    assert response["ok"] is True
    assert response["success"] is False
    assert response["mode"] == "mock"
    assert response["duration_ms"] >= 0
    assert response["issues"]
    assert response["issues"][0]["severity"] == "error"


def test_workbench_session_exposes_and_updates_compiler_settings():
    session = WorkbenchSession()

    update = session.route(
        "POST",
        "/api/settings/compiler",
        {"mode": "lp", "converter_path": "/Applications/LP_XMLConverter"},
    )
    snapshot = session.route("GET", "/api/snapshot")

    assert update["ok"] is True
    assert update["compiler"] == {
        "mode": "lp",
        "converter_path": "/Applications/LP_XMLConverter",
    }
    assert snapshot["compiler"] == update["compiler"]


def test_workbench_session_exposes_runtime_llm_settings(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "deepseek-chat"
api_key = "deepseek-key"
api_base = "https://api.deepseek.com/v1"
temperature = 0.2
max_tokens = 4096
provider_keys = {}
custom_providers = []
assistant_settings = "prefer concise GDL diffs"

[agent]
max_iterations = 7
validate_xml = true
diff_check = true
auto_version = true

[compiler]
path = ""
timeout = 60
""",
        encoding="utf-8",
    )
    session = WorkbenchSession(config_path=config_path)

    response = session.route("GET", "/api/settings/runtime")

    assert response["ok"] is True
    assert response["llm"]["model"] == "deepseek-chat"
    assert response["llm"]["api_key"] == "deepseek-key"
    assert response["llm"]["api_base"] == "https://api.deepseek.com/v1"
    assert response["llm"]["max_retries"] == 7
    assert response["llm"]["assistant_settings"] == "prefer concise GDL diffs"
    assert "glm-4-flash" in response["llm"]["models"]


def test_workbench_session_updates_llm_settings_and_persists_config(tmp_path):
    config_path = tmp_path / "config.toml"
    session = WorkbenchSession(config_path=config_path)

    response = session.route(
        "POST",
        "/api/settings/llm",
        {
            "model": "gpt-4.1-mini",
            "api_key": "openai-key",
            "api_base": "https://api.openai.com/v1",
            "max_retries": 6,
            "assistant_settings": "先解释再改代码",
        },
    )
    reloaded = WorkbenchSession(config_path=config_path)

    assert response["ok"] is True
    assert response["llm"]["model"] == "gpt-4.1-mini"
    assert response["llm"]["max_retries"] == 6
    assert reloaded.llm_model == "gpt-4.1-mini"
    assert reloaded.llm_api_key == "openai-key"
    assert reloaded.llm_api_base == "https://api.openai.com/v1"
    assert reloaded.assistant_settings == "先解释再改代码"


def test_workbench_session_updates_custom_provider_credentials(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "mimo-v2.5-pro"
api_key = ""
api_base = ""
temperature = 0.2
max_tokens = 4096
provider_keys = {}
assistant_settings = ""

[[llm.custom_providers]]
name = "mimo"
base_url = "https://old.example.test/v1"
api_key = "old-key"
protocol = "openai"
models = ["mimo-v2.5-pro"]

[agent]
max_iterations = 5
validate_xml = true
diff_check = true
auto_version = true

[compiler]
path = ""
timeout = 60
""",
        encoding="utf-8",
    )
    session = WorkbenchSession(config_path=config_path)

    response = session.route(
        "POST",
        "/api/settings/llm",
        {
            "model": "mimo-v2.5-pro",
            "api_key": "new-key",
            "api_base": "https://new.example.test/v1",
            "max_retries": 5,
            "assistant_settings": "",
        },
    )
    reloaded = WorkbenchSession(config_path=config_path)

    assert response["ok"] is True
    assert reloaded.llm_api_key == "new-key"
    assert reloaded.llm_api_base == "https://new.example.test/v1"


def test_workbench_session_choose_converter_file_updates_compiler_settings():
    session = WorkbenchSession(file_chooser=lambda: "/Applications/LP_XMLConverter")

    response = session.route("POST", "/api/dialog/open-file", {"purpose": "compiler"})

    assert response["ok"] is True
    assert response["path"] == "/Applications/LP_XMLConverter"
    assert response["compiler"] == {
        "mode": "lp",
        "converter_path": "/Applications/LP_XMLConverter",
    }


def test_workbench_session_choose_converter_file_handles_cancel():
    session = WorkbenchSession(file_chooser=lambda: "")

    response = session.route("POST", "/api/dialog/open-file", {"purpose": "compiler"})

    assert response["ok"] is False
    assert response["cancelled"] is True


def test_workbench_session_compile_uses_session_compiler_settings(tmp_path):
    project = HSFProject.create_new("LPFallbackShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    session.route("POST", "/api/settings/compiler", {"mode": "lp", "converter_path": "/missing/converter"})
    response = session.route("POST", "/api/compile", {})

    assert response["ok"] is False
    assert response["compile"]["mode"] == "lp"
    assert "LP_XMLConverter not found" in response["error"]


def test_workbench_session_compile_requires_loaded_hsf_project():
    session = WorkbenchSession()

    response = session.route("POST", "/api/compile", {})

    assert response["ok"] is False
    assert "Load an HSF project" in response["error"]


def test_workbench_session_assistant_explains_loaded_project(tmp_path):
    project = HSFProject.create_new("ExplainedShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant", {"message": "解释这个构件"})

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "explain_project"
    assert "ExplainedShelf" in response["assistant"]["reply"]


def test_workbench_session_assistant_explains_parameter_mentions(tmp_path):
    project = HSFProject.create_new("ParameterShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant", {"message": "详细解释 A 参数"})

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "explain_parameter"
    assert "参数：A" in response["assistant"]["reply"]
    assert "3D" in response["assistant"]["reply"]


def test_workbench_session_generate_updates_project_from_pipeline_result(tmp_path):
    project = HSFProject.create_new("GeneratedShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    class FakePipeline:
        last_request = None

        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            FakePipeline.last_request = request
            request.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 1\n")
            return TaskResult(
                success=True,
                intent="MODIFY",
                scripts={"scripts/3d.gdl": request.project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已加高",
                project=request.project,
            )

    session = WorkbenchSession(pipeline_class=FakePipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant/generate", {"message": "把柜子加高"})

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "generate"
    assert response["assistant"]["changed_files"] == ["scripts/3d.gdl"]
    assert response["preview"]["meshes"]
    assert "ADDZ 1" in HSFProject.load_from_disk(str(hsf_dir)).get_script(ScriptType.SCRIPT_3D)
    assert FakePipeline.last_request.intent == "MODIFY"
    assert FakePipeline.last_request.gsm_name == "GeneratedShelf"


def test_workbench_session_generate_reports_pipeline_failure(tmp_path):
    project = HSFProject.create_new("FailedGeneration", str(tmp_path))
    hsf_dir = project.save_to_disk()

    class FailingPipeline:
        def __init__(self, trace_dir="./traces"):
            pass

        def execute(self, request):
            return TaskResult(success=False, error="missing API key")

    session = WorkbenchSession(pipeline_class=FailingPipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant/generate", {"message": "修改"})

    assert response["ok"] is False
    assert "missing API key" in response["error"]
