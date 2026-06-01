import base64

from openbrep.compiler import CompileResult
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.learning import ErrorLearningStore
from openbrep.runtime.pipeline import TaskResult
import openbrep.workbench_api as workbench_api
from openbrep.workbench_api import (
    WorkbenchSession,
    apply_parameter_values,
    build_demo_project,
    build_demo_snapshot,
    preview_2d_payload,
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


def test_preview_2d_payload_returns_plan_geometry():
    project = build_demo_project()
    project.set_script(ScriptType.SCRIPT_2D, "LINE2 0, 0, A, B\nCIRCLE2 A / 2, B / 2, 0.1\n")

    payload = preview_2d_payload(project)

    assert payload["lines"] == [{"from": [0.0, 0.0], "to": [1.2, 0.36]}]
    assert payload["circles"][0]["r"] == 0.1


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


def test_route_rpc_preview_forwards_editor_buffer_overrides():
    response = route_rpc(
        "POST",
        "/api/preview",
        {
            "parameters": {"A": 2.2},
            "scripts": {"3d.gdl": "BLOCK 2, 1, 1\n"},
        },
    )

    assert response["ok"] is True
    assert response["preview"]["verification"] == {
        "source": "editor_buffer",
        "script_overrides": ["3d.gdl"],
    }


def test_route_rpc_preview_2d_returns_preview_for_overrides():
    response = route_rpc("POST", "/api/preview/2d", {"parameters": {"A": 2.2}})

    assert response["ok"] is True
    assert "lines" in response["preview"]
    assert "warnings" in response["preview"]


def test_workbench_tapir_status_degrades_when_bridge_is_not_imported():
    session = WorkbenchSession(tapir_import_ok=False)

    response = session.route("GET", "/api/tapir/status")

    assert response["ok"] is True
    assert response["tapir"]["import_ok"] is False
    assert response["tapir"]["available"] is False
    assert response["tapir"]["archicad_connected"] is False
    assert response["tapir"]["tapir_available"] is False
    assert "未导入" in response["tapir"]["message"]


def test_workbench_tapir_sync_selection_reads_selected_archicad_elements():
    class FakeBridge:
        def is_available(self):
            return True

        def get_status(self):
            return {
                "archicad_connected": True,
                "tapir_available": True,
                "version": "/Applications/GRAPHISOFT/Archicad",
            }

        def get_selected_elements(self):
            return ["GUID-1"]

        def get_details_of_elements(self, guids):
            assert guids == ["GUID-1"]
            return [{"guid": "GUID-1", "type": "Object", "name": "Chair"}]

    session = WorkbenchSession(
        tapir_import_ok=True,
        get_tapir_bridge_fn=lambda: FakeBridge(),
        now_text_fn=lambda: "2026-06-01 10:00",
    )

    response = session.route("POST", "/api/tapir/selection/sync")

    assert response["ok"] is True
    assert response["message"] == "已同步 1 个对象"
    assert response["tapir"]["available"] is True
    assert response["tapir"]["selected_guids"] == ["GUID-1"]
    assert response["tapir"]["selected_details"] == [{"guid": "GUID-1", "type": "Object", "name": "Chair"}]
    assert response["tapir"]["last_sync_at"] == "2026-06-01 10:00"


def test_workbench_tapir_loads_and_applies_selected_parameters():
    calls = {}

    class FakeBridge:
        def is_available(self):
            return True

        def get_status(self):
            return {"archicad_connected": True, "tapir_available": True, "version": "Archicad"}

        def get_gdl_parameters_of_elements(self, guids):
            assert guids == ["GUID-1"]
            return [
                {
                    "guid": "GUID-1",
                    "gdlParameters": [
                        {"name": "A", "value": 1.0},
                        {"name": "is_visible", "value": True},
                    ],
                }
            ]

        def set_gdl_parameters_of_elements(self, rows):
            calls["rows"] = rows
            return {"executionResults": [{"success": True}]}

    session = WorkbenchSession(
        tapir_import_ok=True,
        get_tapir_bridge_fn=lambda: FakeBridge(),
        now_text_fn=lambda: "2026-06-01 10:00",
    )
    session.tapir.state.tapir_selected_guids = ["GUID-1"]

    loaded = session.route("POST", "/api/tapir/parameters/load")
    applied = session.route(
        "POST",
        "/api/tapir/parameters/apply",
        {"param_edits": {"GUID-1::A": "1.25", "GUID-1::is_visible": "false"}},
    )

    assert loaded["ok"] is True
    assert loaded["tapir"]["param_edits"] == {"GUID-1::A": "1.0", "GUID-1::is_visible": "True"}
    assert applied["ok"] is True
    assert calls["rows"] == [
        {
            "guid": "GUID-1",
            "gdlParameters": [
                {"name": "A", "value": 1.25},
                {"name": "is_visible", "value": False},
            ],
        }
    ]


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
    assert recent["projects"][0]["name"] == "RecentTwo"
    assert recent["projects"][0]["parent_dir"] == str(second.parent)
    assert all(item["exists"] for item in recent["projects"][:2])
    assert closed["ok"] is True
    assert closed["project"]["source"] == "demo"
    assert "path" not in closed["project"]


def test_workbench_session_persists_recent_projects_in_config(tmp_path):
    first = HSFProject.create_new("RecentOne", str(tmp_path / "one")).save_to_disk()
    second = HSFProject.create_new("RecentTwo", str(tmp_path / "two")).save_to_disk()
    config_path = tmp_path / "workbench.toml"

    session = WorkbenchSession(config_path=config_path)
    session.route("POST", "/api/project/load", {"path": str(first)})
    session.route("POST", "/api/project/load", {"path": str(second)})

    restored = WorkbenchSession(config_path=config_path)
    recent = restored.route("GET", "/api/project/recent")

    assert recent["ok"] is True
    assert [item["path"] for item in recent["projects"]][:2] == [str(second), str(first)]
    assert all(item["exists"] for item in recent["projects"][:2])


def test_workbench_session_exports_current_project_as_hsf(tmp_path):
    source_root = tmp_path / "source"
    export_root = tmp_path / "exported"
    project = HSFProject.create_new("SourceShelf", str(source_root))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route(
        "POST",
        "/api/project/export-hsf",
        {"parent_dir": str(export_root), "name": "Saved Shelf"},
    )

    assert response["ok"] is True
    assert response["saved_to"] == str(export_root / "Saved Shelf")
    assert response["project"]["name"] == "Saved Shelf"
    assert response["project"]["path"] == str(export_root / "Saved Shelf")
    assert (export_root / "Saved Shelf" / "libpartdata.xml").exists()
    assert HSFProject.load_from_disk(response["project"]["path"]).get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\n"


def test_workbench_session_export_hsf_rejects_non_empty_target(tmp_path):
    target = tmp_path / "exported" / "Existing"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("do not overwrite", encoding="utf-8")

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route(
        "POST",
        "/api/project/export-hsf",
        {"parent_dir": str(target.parent), "name": "Existing"},
    )

    assert response["ok"] is False
    assert "already exists" in response["error"]
    assert (target / "notes.txt").read_text(encoding="utf-8") == "do not overwrite"


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


def test_workbench_session_imports_gsm_file_with_lp_converter(tmp_path, monkeypatch):
    gsm_path = tmp_path / "ImportedShelf.gsm"
    gsm_path.write_bytes(b"fake gsm")

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            assert gsm_path_arg == str(gsm_path)
            project = HSFProject.create_new("ConverterOutput", output_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 1\n")
            project.save_to_disk()
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    monkeypatch.setattr(workbench_api, "HSFCompiler", FakeHSFCompiler)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route(
        "POST",
        "/api/settings/compiler",
        {"mode": "lp", "converter_path": "/Applications/LP_XMLConverter"},
    )

    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is True
    assert response["imported_from"] == str(gsm_path)
    assert response["project"]["source"] == "hsf"
    assert response["project"]["path"].endswith("ImportedShelf")
    imported = HSFProject.load_from_disk(response["project"]["path"])
    assert imported.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nADDZ 1\n"
    assert response["decompile"]["mode"] == "lp"


def test_workbench_session_rejects_gsm_import_in_mock_mode(tmp_path):
    gsm_path = tmp_path / "ImportedShelf.gsm"
    gsm_path.write_bytes(b"fake gsm")

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is False
    assert "LP_XMLConverter mode" in response["error"]


def test_workbench_session_creates_project_from_prompt(tmp_path):
    class FakePipeline:
        last_request = None

        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            FakePipeline.last_request = request
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 1\n")
            return TaskResult(
                success=True,
                intent="CREATE",
                scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已创建书架",
                project=project,
            )

    session = WorkbenchSession(pipeline_class=FakePipeline)
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "create a bookshelf", "output_dir": str(tmp_path)},
    )

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "create"
    assert response["project"]["source"] == "hsf"
    assert response["project"]["path"].startswith(str(tmp_path))
    assert HSFProject.load_from_disk(response["project"]["path"]).get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nADDZ 1\n"
    assert FakePipeline.last_request.intent == "CREATE"
    assert FakePipeline.last_request.output_dir == str(tmp_path.resolve())


def test_workbench_session_creates_project_from_image_prompt(tmp_path):
    class FakePipeline:
        last_request = None

        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            FakePipeline.last_request = request
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(
                success=True,
                intent=request.intent,
                scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已根据参考图创建对象",
                project=project,
            )

    session = WorkbenchSession(pipeline_class=FakePipeline)
    response = session.route(
        "POST",
        "/api/project/create",
        {
            "prompt": "根据参考图生成一个书架",
            "output_dir": str(tmp_path),
            "image_b64": "ZmFrZS1pbWFnZQ==",
            "image_mime": "image/png",
        },
    )

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "create"
    assert FakePipeline.last_request.intent == "IMAGE"
    assert FakePipeline.last_request.image_b64 == "ZmFrZS1pbWFnZQ=="
    assert FakePipeline.last_request.image_mime == "image/png"


def test_workbench_session_rejects_unsupported_image_mime_for_create(tmp_path):
    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            pass

        def execute(self, request):  # pragma: no cover - validation should stop first
            raise AssertionError("pipeline should not run")

    session = WorkbenchSession(pipeline_class=FakePipeline)
    response = session.route(
        "POST",
        "/api/project/create",
        {
            "prompt": "根据参考图生成",
            "output_dir": str(tmp_path),
            "image_b64": "ZmFrZS1pbWFnZQ==",
            "image_mime": "image/gif",
        },
    )

    assert response["ok"] is False
    assert "Unsupported image type" in response["error"]


def test_workbench_session_saves_and_lists_project_revisions(tmp_path):
    project = HSFProject.create_new("RevisionShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    saved = session.route("POST", "/api/project/revision/save", {"message": "stable shelf"})
    listed = session.route("GET", "/api/project/revisions")

    assert saved["ok"] is True
    assert saved["revision"]["revision_id"] == "r0001"
    assert listed["ok"] is True
    assert listed["latest_revision_id"] == "r0001"
    assert listed["revisions"][0]["message"] == "stable shelf"
    assert listed["revisions"][0]["is_latest"] is True


def test_workbench_session_exposes_project_git_controls(tmp_path):
    project = HSFProject.create_new("GitApiShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    initialized = session.route("POST", "/api/project/git/init")
    status = session.route("GET", "/api/project/git")
    committed = session.route("POST", "/api/project/git/commit", {"message": "Initial HSF source"})

    assert initialized["ok"] is True
    assert status["git"]["enabled"] is True
    assert status["git"]["initialized"] is True
    assert committed["ok"] is True
    assert committed["git"]["last_commit"]


def test_workbench_session_restores_project_revision_and_refreshes_snapshot(tmp_path):
    project = HSFProject.create_new("RevisionShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    session.route("POST", "/api/project/revision/save", {"message": "box"})
    session.route("POST", "/api/project/script/3d.gdl", {"content": "CYLIND 1, 1\n"})

    restored = session.route("POST", "/api/project/revision/restore", {"revision_id": "r0001"})

    assert restored["ok"] is True
    assert restored["restored_revision_id"] == "r0001"
    assert restored["latest_revision_id"] == "r0002"
    assert session.route("GET", "/api/project/script/3d.gdl")["content"] == "BLOCK A, B, ZZYZX\n"
    assert restored["project"]["source"] == "hsf"


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
    assert response["compile"]["gsm_size_bytes"] is not None
    assert response["compile"]["parameter_count"] == 3
    assert (output_dir / "CompiledShelf.gsm").exists()


def test_workbench_session_reveals_last_compiled_artifact(tmp_path):
    project = HSFProject.create_new("RevealShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    revealed: list[Path] = []
    output_dir = tmp_path / "out"

    session = WorkbenchSession(path_revealer=lambda path: revealed.append(path))
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    compile_response = session.route("POST", "/api/compile", {"output_dir": str(output_dir)})
    response = session.route("POST", "/api/artifact/reveal", {})

    assert compile_response["ok"] is True
    assert response["ok"] is True
    assert response["path"] == str(output_dir / "RevealShelf.gsm")
    assert revealed == [output_dir / "RevealShelf.gsm"]


def test_workbench_session_reveal_artifact_rejects_missing_path(tmp_path):
    revealed: list[Path] = []
    session = WorkbenchSession(path_revealer=lambda path: revealed.append(path))

    response = session.route(
        "POST",
        "/api/artifact/reveal",
        {"path": str(tmp_path / "missing.gsm")},
    )

    assert response["ok"] is False
    assert "Artifact not found" in response["error"]
    assert revealed == []


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
    assert response["output_path"].endswith("MockCompileDiagnostics.gsm")
    assert response["parameter_count"] == 3
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
        "output_dir": "",
    }
    assert snapshot["compiler"] == update["compiler"]


def test_workbench_session_updates_compile_output_directory(tmp_path):
    output_dir = tmp_path / "configured-out"
    project = HSFProject.create_new("ConfiguredOutputShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    update = session.route(
        "POST",
        "/api/settings/compiler",
        {"mode": "mock", "converter_path": "", "output_dir": str(output_dir)},
    )
    response = session.route("POST", "/api/compile", {})

    assert update["ok"] is True
    assert update["compiler"]["output_dir"] == str(output_dir)
    assert response["ok"] is True
    assert response["compile"]["output_path"] == str(output_dir / "ConfiguredOutputShelf.gsm")
    assert (output_dir / "ConfiguredOutputShelf.gsm").exists()


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


def test_workbench_session_uses_gdl_agent_config_env_by_default(tmp_path, monkeypatch):
    config_path = tmp_path / "personal-config.toml"
    config_path.write_text(
        """
[llm]
model = "mimo-v2.5-pro"
api_key = "mimo-key"
api_base = "https://token-plan-cn.xiaomimimo.com/v1"
temperature = 0.2
max_tokens = 4096
provider_keys = {}
custom_providers = []
assistant_settings = "personal preference"
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GDL_AGENT_CONFIG", str(config_path))
    session = WorkbenchSession()
    response = session.route("GET", "/api/settings/runtime")

    assert session.config_path == config_path
    assert response["llm"]["model"] == "mimo-v2.5-pro"
    assert response["llm"]["api_key"] == "mimo-key"
    assert response["llm"]["api_base"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert response["llm"]["assistant_settings"] == "personal preference"


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


def test_workbench_session_tests_llm_connection_success(tmp_path, monkeypatch):
    captured_models: list[str] = []

    class FakeLLMAdapter:
        def __init__(self, config):
            captured_models.append(config.model)

        def generate(self, messages, **kwargs):
            return type("Response", (), {"model": "deepseek-chat"})()

    monkeypatch.setattr(workbench_api, "LLMAdapter", FakeLLMAdapter)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route(
        "POST",
        "/api/settings/llm/test",
        {"model": "deepseek-chat", "api_key": "key", "api_base": ""},
    )

    assert response["ok"] is True
    assert response["message"] == "LLM connection OK"
    assert response["model"] == "deepseek-chat"
    assert response["duration_ms"] >= 0
    assert captured_models == ["deepseek-chat"]


def test_workbench_session_tests_llm_connection_reports_configuration_error(tmp_path, monkeypatch):
    class FakeLLMAdapter:
        def __init__(self, config):
            pass

        def generate(self, messages, **kwargs):
            raise RuntimeError("LLM 认证失败：API Key invalid")

    monkeypatch.setattr(workbench_api, "LLMAdapter", FakeLLMAdapter)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route("POST", "/api/settings/llm/test", {"model": "deepseek-chat"})

    assert response["ok"] is False
    assert response["category"] == "llm_configuration"
    assert "API Key invalid" in response["error"]


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
        "output_dir": "",
    }


def test_workbench_session_choose_output_directory_updates_compiler_settings(tmp_path):
    output_dir = tmp_path / "selected-output"
    session = WorkbenchSession(
        config_path=tmp_path / "config.toml",
        directory_chooser=lambda: str(output_dir),
    )

    response = session.route("POST", "/api/dialog/output-directory", {})

    assert response["ok"] is True
    assert response["path"] == str(output_dir.resolve())
    assert response["compiler"]["output_dir"] == str(output_dir.resolve())


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


def test_workbench_session_lp_compile_without_path_uses_real_compiler_auto_detect(tmp_path, monkeypatch):
    project = HSFProject.create_new("LPAutoDetectShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    constructed_paths: list[str | None] = []

    class FakeHSFCompiler:
        def __init__(self, converter_path=None):
            constructed_paths.append(converter_path)

        def hsf2libpart(self, hsf_path, output_gsm):
            return CompileResult(success=True, stdout="compiled", output_path=output_gsm, mode="real")

    monkeypatch.setattr(workbench_api, "HSFCompiler", FakeHSFCompiler)
    session = WorkbenchSession()
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    session.route("POST", "/api/settings/compiler", {"mode": "lp", "converter_path": ""})
    response = session.route("POST", "/api/compile", {})

    assert response["ok"] is True
    assert response["compile"]["mode"] == "real"
    assert constructed_paths == [None]


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


def test_workbench_session_persists_project_assistant_history(tmp_path):
    project = HSFProject.create_new("HistoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    saved = session.route(
        "POST",
        "/api/assistant/history",
        {
            "messages": [
                {"role": "user", "content": "做一个书架"},
                {"role": "assistant", "content": "已创建书架"},
            ]
        },
    )
    loaded = session.route("GET", "/api/assistant/history")

    assert saved["ok"] is True
    assert saved["count"] == 2
    assert loaded["ok"] is True
    assert loaded["messages"] == [
        {"role": "user", "content": "做一个书架"},
        {"role": "assistant", "content": "已创建书架"},
    ]
    transcript = hsf_dir / ".openbrep" / "memory" / "chats" / "chat_transcript.jsonl"
    assert transcript.exists()


def test_workbench_session_clears_project_assistant_history(tmp_path):
    project = HSFProject.create_new("HistoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    session.route("POST", "/api/assistant/history", {"messages": [{"role": "user", "content": "旧记录"}]})
    cleared = session.route("DELETE", "/api/assistant/history")
    loaded = session.route("GET", "/api/assistant/history")

    assert cleared["ok"] is True
    assert cleared["count"] == 0
    assert loaded["messages"] == []


def test_workbench_session_extracts_code_blocks_from_assistant_history_text():
    session = WorkbenchSession()
    response = session.route(
        "POST",
        "/api/assistant/code-blocks",
        {
            "content": """这里是修改后的 3D 脚本：

```gdl
BLOCK A, B, ZZYZX
ADDZ 1
END
```
"""
        },
    )

    assert response["ok"] is True
    assert response["blocks"] == [
        {
            "path": "scripts/3d.gdl",
            "script_name": "3d.gdl",
            "content": "BLOCK A, B, ZZYZX\nADDZ 1\nEND",
        }
    ]


def test_workbench_session_reports_and_clears_project_memory_status(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    session.route("POST", "/api/assistant/history", {"messages": [{"role": "user", "content": "旧记录"}]})

    status = session.route("GET", "/api/memory/status")
    cleared = session.route("DELETE", "/api/memory")
    after = session.route("GET", "/api/memory/status")

    assert status["ok"] is True
    assert status["memory"]["chat_count"] == 1
    assert status["memory"]["memory_root"] == str(hsf_dir / ".openbrep" / "memory")
    assert status["memory"]["total_bytes"] > 0
    assert cleared["ok"] is True
    assert cleared["before"]["chat_count"] == 1
    assert after["memory"]["chat_count"] == 0
    assert after["memory"]["total_bytes"] == 0


def test_workbench_session_lists_project_memory_lessons(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    ErrorLearningStore(hsf_dir).record_error(
        "Unknown command FOO at line 3",
        source="test",
        project_name="MemoryShelf",
        instruction="bad command",
    )

    response = session.route("GET", "/api/memory/lessons")

    assert response["ok"] is True
    assert len(response["lessons"]) == 1
    lesson = response["lessons"][0]
    assert lesson["category"]
    assert "FOO" in lesson["summary"]
    assert lesson["guidance"]
    assert lesson["count"] == 1
    assert lesson["project_name"] == "MemoryShelf"
    assert lesson["source"] == "test"


def test_workbench_session_summarizes_project_memory_to_skill(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    ErrorLearningStore(hsf_dir).record_error(
        "Unknown command FOO at line 3",
        source="test",
        project_name="MemoryShelf",
        instruction="bad command",
    )

    response = session.route("POST", "/api/memory/summarize", {})

    assert response["ok"] is True
    assert response["summary"]["ok"] is True
    assert response["summary"]["lesson_count"] >= 1
    assert response["summary"]["path"].endswith("learned_skill.md")
    assert "规则整理" in response["summary"]["message"]
    assert "FOO" in response["skill"]


def test_workbench_session_deletes_project_memory_lesson(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    lesson = ErrorLearningStore(hsf_dir).record_error(
        "Unknown command FOO at line 3",
        source="test",
        project_name="MemoryShelf",
        instruction="bad command",
    )

    response = session.route("DELETE", f"/api/memory/lessons/{lesson.fingerprint}")
    lessons = session.route("GET", "/api/memory/lessons")
    status = session.route("GET", "/api/memory/status")

    assert response["ok"] is True
    assert response["deleted"] == lesson.fingerprint
    assert response["remaining_count"] == 0
    assert lessons["lessons"] == []
    assert status["memory"]["lesson_count"] == 0


def test_workbench_session_ignores_project_memory_lesson_without_deleting_it(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    lesson = ErrorLearningStore(hsf_dir).record_error(
        "Unknown command FOO at line 3",
        source="test",
        project_name="MemoryShelf",
        instruction="bad command",
    )

    response = session.route("POST", f"/api/memory/lessons/{lesson.fingerprint}/ignore")
    visible_lessons = session.route("GET", "/api/memory/lessons")
    status = session.route("GET", "/api/memory/status")
    stored_lessons = ErrorLearningStore(hsf_dir).list_error_lessons(include_ignored=True)

    assert response["ok"] is True
    assert response["ignored"] == lesson.fingerprint
    assert response["remaining_count"] == 0
    assert visible_lessons["lessons"] == []
    assert status["memory"]["lesson_count"] == 0
    assert len(stored_lessons) == 1
    assert stored_lessons[0].ignored is True


def test_workbench_session_updates_project_memory_lesson(tmp_path):
    project = HSFProject.create_new("MemoryShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    lesson = ErrorLearningStore(hsf_dir).record_error(
        "Unknown command FOO at line 3",
        source="test",
        project_name="MemoryShelf",
        instruction="bad command",
    )

    response = session.route(
        "PATCH",
        f"/api/memory/lessons/{lesson.fingerprint}",
        {
            "category": "syntax",
            "summary": "FOO is not a valid GDL command.",
            "guidance": "Replace FOO with a supported primitive or helper.",
            "example": "Use BLOCK A, B, ZZYZX instead.",
        },
    )
    lessons = session.route("GET", "/api/memory/lessons")

    assert response["ok"] is True
    assert response["lesson"]["fingerprint"] == lesson.fingerprint
    assert response["lesson"]["category"] == "syntax"
    assert response["lesson"]["summary"] == "FOO is not a valid GDL command."
    assert response["lesson"]["guidance"] == "Replace FOO with a supported primitive or helper."
    assert response["lesson"]["example"] == "Use BLOCK A, B, ZZYZX instead."
    assert response["lesson"]["count"] == 1
    assert lessons["lessons"][0]["summary"] == "FOO is not a valid GDL command."


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


def test_workbench_session_generate_passes_reference_image_to_pipeline(tmp_path):
    project = HSFProject.create_new("VisionShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    class FakePipeline:
        last_request = None

        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            FakePipeline.last_request = request
            request.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(
                success=True,
                intent="MODIFY",
                scripts={"scripts/3d.gdl": request.project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已按参考图调整",
                project=request.project,
            )

    session = WorkbenchSession(pipeline_class=FakePipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route(
        "POST",
        "/api/assistant/generate",
        {
            "message": "按这张图调整比例",
            "image_b64": "ZmFrZS1pbWFnZQ==",
            "image_mime": "image/jpeg",
        },
    )

    assert response["ok"] is True
    assert FakePipeline.last_request.intent == "MODIFY"
    assert FakePipeline.last_request.image_b64 == "ZmFrZS1pbWFnZQ=="
    assert FakePipeline.last_request.image_mime == "image/jpeg"


def test_workbench_session_normalizes_vision_provider_errors(tmp_path):
    project = HSFProject.create_new("VisionShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()

    class FailingVisionPipeline:
        def __init__(self, trace_dir="./traces"):
            pass

        def execute(self, request):
            return TaskResult(success=False, error="BadRequest: unsupported image_url content block")

    session = WorkbenchSession(pipeline_class=FailingVisionPipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route(
        "POST",
        "/api/assistant/generate",
        {
            "message": "按图调整",
            "image_b64": "ZmFrZS1pbWFnZQ==",
            "image_mime": "image/png",
        },
    )

    assert response["ok"] is False
    assert "当前模型或网关不支持图片分析" in response["error"]
    assert "unsupported image_url" in response["error"]


def test_workbench_session_rejects_oversized_generate_image(tmp_path):
    project = HSFProject.create_new("VisionShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    too_large = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            pass

        def execute(self, request):  # pragma: no cover - validation should stop first
            raise AssertionError("pipeline should not run")

    session = WorkbenchSession(pipeline_class=FakePipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route(
        "POST",
        "/api/assistant/generate",
        {
            "message": "按图调整",
            "image_b64": too_large,
            "image_mime": "image/png",
        },
    )

    assert response["ok"] is False
    assert "5 MB" in response["error"]


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
