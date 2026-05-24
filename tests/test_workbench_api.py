from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
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


def test_workbench_session_compile_requires_loaded_hsf_project():
    session = WorkbenchSession()

    response = session.route("POST", "/api/compile", {})

    assert response["ok"] is False
    assert "Load an HSF project" in response["error"]
