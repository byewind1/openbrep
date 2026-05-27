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
