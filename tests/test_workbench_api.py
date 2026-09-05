import base64
import json
from pathlib import Path

from openbrep import feedback_distill
from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.learning import ErrorLearningStore
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskResult
import openbrep.workbench_api as workbench_api
from openbrep.workbench.project_session_service import write_project_origin
from openbrep.workbench.workspace_service import init_workspace
from openbrep.workbench_api import (
    WorkbenchSession,
    apply_parameter_values,
    build_demo_project,
    build_demo_snapshot,
    preview_2d_payload,
    preview_payload,
    route_rpc,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_parameter_values_preserves_string_params_for_preview():
    """P9：非数值字符串参数（String 型，如 pattern_type）保留在预览参数里，
    供预览器字符串比较（IF pattern_type = "直棂"）使用；数值参数不受影响。"""
    from openbrep.workbench.project_parameter_service import parameter_values

    project = build_demo_project()
    project.add_parameter(GDLParameter(name="pattern_type", type_tag="String", value="直棂"))

    values = parameter_values(project)

    assert values["PATTERN_TYPE"] == "直棂"
    assert values["A"] == 1.2  # 数值参数照旧
    assert values["HAS_BACK_PANEL"] == 1.0  # Boolean 照旧数值化


def test_route_rpc_preview_returns_preview_for_overrides():
    route_rpc("POST", "/api/project/new", {})
    response = route_rpc(
        "POST",
        "/api/preview",
        {
            "parameters": {"A": 2.2},
            "scripts": {"3d.gdl": "BLOCK A, B, ZZYZX\n"},
        },
    )

    assert response["ok"] is True
    assert response["preview"]["meshes"]


def test_route_rpc_preview_forwards_editor_buffer_overrides():
    route_rpc("POST", "/api/project/new", {})
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


def test_workbench_session_starts_empty(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    snapshot = session.snapshot()

    assert snapshot["ok"] is True
    assert snapshot["project"] is None
    assert snapshot["parameters"] == []
    assert snapshot["preview"]["meshes"] == []


def test_workbench_session_new_project_is_untitled(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    response = session.route("POST", "/api/project/new", {})

    assert response["ok"] is True
    assert response["project"]["name"] == "Untitled GDL Object"
    assert response["project"]["source"] == "untitled"
    assert "path" not in response["project"]


def test_workbench_session_close_returns_empty(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/close", {})

    assert response["ok"] is True
    assert response["project"] is None


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
    assert closed["project"] is None


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
    session.route("POST", "/api/project/new", {})
    response = session.route(
        "POST",
        "/api/project/export-hsf",
        {"parent_dir": str(target.parent), "name": "Existing"},
    )

    assert response["ok"] is False
    assert "already exists" in response["error"]
    assert (target / "notes.txt").read_text(encoding="utf-8") == "do not overwrite"


# ── P7c：只给 name 不给 parent_dir 的保存（自动落点 + 唯一化 + session 挂载）──


def test_workbench_session_export_hsf_name_only_uses_output_dir_fallback(tmp_path):
    """只给 name：落点取设置 output_dir，保存后 session 挂载（source=hsf / path / 最近项目）。"""
    output_dir = tmp_path / "out"
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.config.output_dir = str(output_dir)
    session.output_dir = str(output_dir)
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/export-hsf", {"name": "参数化书架"})

    assert response["ok"] is True
    saved_to = output_dir / "参数化书架"
    assert response["saved_to"] == str(saved_to)
    assert response["project"]["name"] == "参数化书架"
    assert response["project"]["source"] == "hsf"
    assert response["project"]["path"] == str(saved_to)
    assert (saved_to / "libpartdata.xml").exists()
    assert session.route("GET", "/api/project/recent")["projects"][0]["path"] == str(saved_to)
    # 落盘目录可重新加载，3D 脚本保持原内容
    reloaded = HSFProject.load_from_disk(str(saved_to))
    assert reloaded.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\n"


def test_workbench_session_export_hsf_name_only_prefers_workspace_hsf(tmp_path):
    """工作区附着时：只给 name 的保存落点优先工作区 hsf/（高于设置 output_dir）。"""
    workspace_root = tmp_path / "ws"
    init_workspace(str(workspace_root))
    output_dir = tmp_path / "out"
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.config.output_dir = str(output_dir)
    session.output_dir = str(output_dir)
    opened = session.route("POST", "/api/workspace/open", {"path": str(workspace_root)})
    assert opened["ok"] is True
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/export-hsf", {"name": "坐斗"})

    assert response["ok"] is True
    saved_to = workspace_root / "hsf" / "坐斗"
    assert response["saved_to"] == str(saved_to)
    assert response["project"]["path"] == str(saved_to)
    assert (saved_to / "libpartdata.xml").exists()
    assert not (output_dir / "坐斗").exists()


def test_workbench_session_export_hsf_name_only_uniquifies_conflicts(tmp_path):
    """自动落点下同名目录已存在 → unique_project_name 加 _vN，不报"已存在"错误。"""
    output_dir = tmp_path / "out"
    existing = output_dir / "书架"
    existing.mkdir(parents=True)
    (existing / "notes.txt").write_text("keep", encoding="utf-8")
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.config.output_dir = str(output_dir)
    session.output_dir = str(output_dir)
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/export-hsf", {"name": "书架"})

    assert response["ok"] is True
    saved_to = output_dir / "书架_v2"
    assert response["saved_to"] == str(saved_to)
    assert response["project"]["name"] == "书架_v2"
    assert (existing / "notes.txt").read_text(encoding="utf-8") == "keep"
    # 再存一次 → _v3
    response2 = session.route("POST", "/api/project/export-hsf", {"name": "书架"})
    assert response2["ok"] is True
    assert response2["saved_to"] == str(output_dir / "书架_v3")


def test_workbench_session_export_hsf_name_only_falls_back_to_dot_output(tmp_path, monkeypatch):
    """无工作区、无 output_dir 配置 → ./output 兜底（chdir 到 tmp 隔离落盘位置）。"""
    monkeypatch.chdir(tmp_path)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/export-hsf", {"name": "新构件"})

    assert response["ok"] is True
    saved_to = tmp_path / "output" / "新构件"
    assert response["saved_to"] == str(saved_to)
    assert response["project"]["path"] == str(saved_to)
    assert (saved_to / "libpartdata.xml").exists()


def test_workbench_session_save_project_still_returns_needs_save_as_for_untitled(tmp_path):
    """红线回归：新建空白项目直接 save_project 仍回 needs_save_as（前端据此弹命名框）。"""
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/new", {})

    response = session.route("POST", "/api/project/save", {})

    assert response["ok"] is False
    assert response["needs_save_as"] is True


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



def _read_origin_toml(project_path):
    toml = Path(project_path) / ".openbrep" / "project.toml"
    return toml, toml.read_text(encoding="utf-8") if toml.exists() else None


def test_workbench_session_import_gdl_persists_origin_in_project_toml(tmp_path):
    """GDL 导入成功后 .openbrep/project.toml 写入 [origin]（imported_from/kind/at）。"""
    gdl_path = tmp_path / "ShelfOrigin.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True
    toml, text = _read_origin_toml(response["project"]["path"])
    assert toml.exists()
    assert "[origin]" in text
    assert f'imported_from = "{gdl_path}"' in text
    assert 'imported_kind = "gdl"' in text
    assert 'imported_at = "' in text
    assert text.count("[origin]") == 1
    # 重复导入同名源：唯一化到 ShelfOrigin_v2（P7a 起后缀统一 _vN），各自 [origin] 只有一个节、不追加
    response2 = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})
    assert response2["ok"] is True
    assert response2["project"]["path"].endswith("ShelfOrigin_v2")
    _, text2 = _read_origin_toml(response2["project"]["path"])
    assert text2.count("[origin]") == 1
    assert "imported_kind = \"gdl\"" in text2
    # 第一次导入的项目文件保持不动
    assert _read_origin_toml(response["project"]["path"])[1] == text


def test_workbench_session_import_gsm_persists_origin_in_project_toml(tmp_path, monkeypatch):
    """GSM 导入成功后 .openbrep/project.toml 写入 [origin]（imported_kind=gsm）。"""
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
            project = HSFProject.create_new("ConverterOutput", output_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
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
    toml, text = _read_origin_toml(response["project"]["path"])
    assert toml.exists()
    assert f'imported_from = "{gsm_path}"' in text
    assert 'imported_kind = "gsm"' in text
    assert 'imported_at = "' in text


def test_write_project_origin_updates_in_place_preserving_other_sections(tmp_path):
    """节级最小更新：已存在 [origin] 只覆盖三键，其他节/键逐字保留，不追加。"""
    project = HSFProject.create_new("OriginUpdate", str(tmp_path))
    hsf_dir = project.save_to_disk()
    toml = Path(hsf_dir) / ".openbrep" / "project.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        "# hand maintained\n"
        "\n"
        "[project]\n"
        'name = "OriginUpdate"\n'
        "\n"
        "[origin]\n"
        'imported_from = "OLD"\n'
        'imported_kind = "gdl"\n'
        'imported_at = "2020-01-01T00:00:00"\n'
        "\n"
        "[settings]\n"
        "foo = [1, 2]\n",
        encoding="utf-8",
    )
    before = toml.read_text(encoding="utf-8")

    write_project_origin(hsf_dir, imported_from="/tmp/new.gsm", imported_kind="gsm")

    after = toml.read_text(encoding="utf-8")
    assert after.count("[origin]") == 1
    assert "OLD" not in after
    assert "imported_kind = \"gsm\"" in after
    # 其他节与键逐字保留
    for fragment in ["# hand maintained", "[project]", 'name = "OriginUpdate"', "[settings]", "foo = [1, 2]"]:
        assert fragment in after
    assert before.split("[origin]")[0] == after.split("[origin]")[0]
    assert before.split("[settings]")[1] == after.split("[settings]")[1]


def test_write_project_origin_skips_malformed_toml_without_crashing(tmp_path):
    """坏 TOML：跳过写入（返回 None），原文件一个字节不动，不抛异常。"""
    project = HSFProject.create_new("OriginBad", str(tmp_path))
    hsf_dir = project.save_to_disk()
    toml = Path(hsf_dir) / ".openbrep" / "project.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text("this is { not [ valid toml", encoding="utf-8")

    result = write_project_origin(hsf_dir, imported_from="/tmp/x.gdl", imported_kind="gdl")

    assert result is None
    assert toml.read_text(encoding="utf-8") == "this is { not [ valid toml"


def test_workbench_session_import_gdl_uses_gdl_file_chooser_purpose(tmp_path):
    gdl_path = tmp_path / "chosen.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
    purposes = []

    session = WorkbenchSession(file_chooser=lambda purpose: purposes.append(purpose) or str(gdl_path))
    response = session.route("POST", "/api/project/import-gdl", {})

    assert response["ok"] is True
    assert purposes == ["gdl"]


def test_workbench_session_import_gdl_parses_bookshelf_parameters_and_sections(tmp_path):
    import shutil

    bookshelf = tmp_path / "Bookshelf.gdl"
    shutil.copy2(REPO_ROOT / "examples" / "Bookshelf.gdl", bookshelf)
    session = WorkbenchSession()
    response = session.route("POST", "/api/project/import-gdl", {"path": str(bookshelf)})

    assert response["ok"] is True
    assert response["warnings"] == []

    imported = HSFProject.load_from_disk(response["project"]["path"])
    assert len(imported.parameters) == 10
    by_name = {p.name: p for p in imported.parameters}
    assert by_name["nShelves"].type_tag == "Integer"
    assert by_name["nShelves"].value == "4"
    assert by_name["hasBack"].type_tag == "Boolean"
    assert by_name["frameMat"].type_tag == "Material"

    master = imported.get_script(ScriptType.MASTER)
    assert "IF A < 0.30" in master
    assert "IF A < 0.30" not in imported.get_script(ScriptType.SCRIPT_3D)


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


def _write_noncanonical_hsf(root):
    """LP_XMLConverter 风格原始输出：无 BOM、乱缩进（语义与规范等价、可无损往返）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "libpartdata.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<LibpartData Version="27" Owner="0" Signature="0">\n'
        "<Identification>\n"
        "<MainGUID>12345678-ABCD-EF01-2345-6789ABCDEF01</MainGUID>\n"
        "</Identification>\n"
        "</LibpartData>\n",
        encoding="utf-8",  # 无 BOM
    )
    (root / "paramlist.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<ParamSection>\n"
        '  <Parameters SectVersion="27" SectionFlags="0" SubIdent="0">\n'
        '    <Length Name="A">\n'
        '      <Description><![CDATA["Width"]]></Description>\n'
        "      <Fix/>\n"
        "      <Value>1.5</Value>\n"
        "    </Length>\n"
        '    <Integer Name="shelf_count">\n'
        '      <Description><![CDATA["层板数"]]></Description>\n'
        "      <Value>3</Value>\n"
        "    </Integer>\n"
        "  </Parameters>\n"
        "</ParamSection>\n",
        encoding="utf-8",
    )
    (root / "scripts" / "3d.gdl").write_text("BLOCK A, B, ZZYZX\nADDZ 1\n", encoding="utf-8")


def _make_gsm_session(tmp_path, monkeypatch, compiler_cls):
    gsm_path = tmp_path / "RawObject.gsm"
    gsm_path.write_bytes(b"fake gsm")

    monkeypatch.setattr(workbench_api, "HSFCompiler", compiler_cls)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route(
        "POST",
        "/api/settings/compiler",
        {"mode": "lp", "converter_path": "/Applications/LP_XMLConverter"},
    )
    return session, gsm_path


def test_workbench_session_import_gsm_normalizes_noncanonical_output(tmp_path, monkeypatch):
    """非规范 HSF（无 BOM/乱缩进）导入后立即规范化：BOM 到位、参数/脚本/GUID 逐项相等。"""
    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            _write_noncanonical_hsf(Path(output_dir) / "hsf_out")
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    session, gsm_path = _make_gsm_session(tmp_path, monkeypatch, FakeHSFCompiler)
    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is True
    project_dir = Path(response["project"]["path"])
    normalization = response["normalization"]
    assert normalization["ok"] is True
    assert normalization["lossless"] is True
    assert "paramlist.xml" in normalization["changed_files"]
    assert "libpartdata.xml" in normalization["changed_files"]

    # 规范落地：BOM
    assert project_dir.joinpath("paramlist.xml").read_bytes()[:3] == b"\xef\xbb\xbf"
    # 语义逐项相等
    imported = HSFProject.load_from_disk(str(project_dir))
    assert imported.guid == "12345678-ABCD-EF01-2345-6789ABCDEF01"
    assert imported.version == 27
    params = [(p.name, p.type_tag, p.value) for p in imported.parameters]
    assert params == [("A", "Length", "1.5"), ("shelf_count", "Integer", "3")]
    assert imported.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nADDZ 1\n"


def test_workbench_session_import_gsm_rolls_back_on_lossy_normalization(tmp_path, monkeypatch):
    """无损守卫：写器丢参数 → 磁盘原文件字节不变 + warning + normalization 标记有损。"""
    import openbrep.paramlist_builder as paramlist_builder

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            _write_noncanonical_hsf(Path(output_dir) / "hsf_out")
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    real_build = paramlist_builder.build_paramlist_xml
    monkeypatch.setattr(
        paramlist_builder,
        "build_paramlist_xml",
        lambda parameters: real_build(parameters[:1]),  # 丢一个参数 → 有损
    )

    session, gsm_path = _make_gsm_session(tmp_path, monkeypatch, FakeHSFCompiler)
    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is True
    normalization = response["normalization"]
    assert normalization["lossless"] is False
    assert "有损" in normalization["warning"]
    # 原文件回滚：paramlist.xml 与导入时字节一致（无 BOM 的原始输出）
    project_dir = Path(response["project"]["path"])
    assert project_dir.joinpath("paramlist.xml").read_bytes()[:3] != b"\xef\xbb\xbf"
    assert project_dir.joinpath("paramlist.xml").read_text(encoding="utf-8").count("<Integer") == 1
    # 人话 warning 进 GUI warnings 通道
    assert any("保留原始文件" in w for w in response["warnings"])


def test_workbench_session_import_gsm_normalization_exception_keeps_original(tmp_path, monkeypatch):
    """helper 抛异常 → 导入仍成功 + warning + 原文件不动。"""
    import openbrep.hsf_project as hsf_project

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            _write_noncanonical_hsf(Path(output_dir) / "hsf_out")
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    def boom(self):
        raise RuntimeError("writer exploded")

    monkeypatch.setattr(hsf_project.HSFProject, "save_to_disk", boom)

    session, gsm_path = _make_gsm_session(tmp_path, monkeypatch, FakeHSFCompiler)
    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is True
    normalization = response["normalization"]
    assert normalization["lossless"] is False
    assert "回滚" in normalization["warning"]
    project_dir = Path(response["project"]["path"])
    assert project_dir.joinpath("paramlist.xml").read_text(encoding="utf-8").count("<Integer") == 1
    assert project_dir.joinpath("scripts/3d.gdl").read_text(encoding="utf-8") == "BLOCK A, B, ZZYZX\nADDZ 1\n"
    # 人话 warning 进 GUI warnings 通道（异常路径的措辞是"已回滚原始文件"）
    assert any(normalization["warning"] in w for w in response["warnings"])


def test_workbench_session_import_gsm_uses_gsm_file_chooser_purpose(tmp_path, monkeypatch):
    gsm_path = tmp_path / "ChosenObject.gsm"
    gsm_path.write_bytes(b"fake gsm")
    purposes = []

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            project = HSFProject.create_new("ChosenObject", output_dir)
            project.save_to_disk()
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    monkeypatch.setattr(workbench_api, "HSFCompiler", FakeHSFCompiler)
    session = WorkbenchSession(file_chooser=lambda purpose: purposes.append(purpose) or str(gsm_path))
    session.route("POST", "/api/settings/compiler", {"mode": "lp", "converter_path": "/Applications/LP_XMLConverter"})

    response = session.route("POST", "/api/project/import-gsm", {})

    assert response["ok"] is True
    assert purposes == ["gsm"]


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


def test_project_create_pipeline_receives_saved_codex_auto_mode_and_provider(tmp_path):
    """D9：新项目 CREATE 主入口同步 mode/effort/provider，不只当前项目助手入口。"""
    provider = object()

    class FakePipeline:
        instance = None

        def __init__(self, trace_dir="./traces"):
            self.config = GDLAgentConfig()
            self.codex_provider = None
            FakePipeline.instance = self

        def execute(self, request):
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            return TaskResult(success=True, intent="CREATE", plain_text="ok", project=project)

    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.llm_model = "openai-codex/gpt-5.6-luna"
    session.config.llm.model = session.llm_model
    session.config.llm.reasoning_effort = "low"
    session.config.llm.codex_routing_mode = "auto"
    session.settings_service.codex_provider = provider

    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "做一个简单方块", "output_dir": str(tmp_path / "out")},
    )
    assert response["ok"] is True
    pipeline = FakePipeline.instance
    assert pipeline.config.llm.reasoning_effort == "low"
    assert pipeline.config.llm.effective_codex_routing_mode() == "auto"
    assert pipeline.codex_provider is provider


def test_workbench_session_create_uses_configured_output_dir(tmp_path):
    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(success=True, intent="CREATE", plain_text="ok", project=project)

    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.output_dir = str(tmp_path / "workspace")

    response = session.route("POST", "/api/project/create", {"prompt": "create a bookshelf"})

    assert response["ok"] is True
    assert response["project"]["path"].startswith(str((tmp_path / "workspace").resolve()))


def test_workbench_session_create_prefers_workspace_hsf(tmp_path):
    """工作区附着时：AI 新建项目落点优先工作区 hsf/（高于设置 output_dir），
    与 Save As 自动落点同口径；请求显式 output_dir 仍最高优先。"""

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(success=True, intent="CREATE", plain_text="ok", project=project)

    workspace_root = tmp_path / "ws"
    init_workspace(str(workspace_root))
    output_dir = tmp_path / "out"
    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.output_dir = str(output_dir)
    opened = session.route("POST", "/api/workspace/open", {"path": str(workspace_root)})
    assert opened["ok"] is True

    response = session.route("POST", "/api/project/create", {"prompt": "create a bookshelf"})

    assert response["ok"] is True
    assert response["project"]["path"].startswith(str((workspace_root / "hsf").resolve()))
    assert not output_dir.exists()

    explicit = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "create a shelf", "output_dir": str(output_dir)},
    )
    assert explicit["ok"] is True
    assert explicit["project"]["path"].startswith(str(output_dir.resolve()))


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

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
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

    session = WorkbenchSession(
        config_path=tmp_path / "config.toml",
        path_revealer=lambda path: revealed.append(path),
    )
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
    # output_dir 是独立设置：本 POST 不带它时应原样保留（fcb4104 缺键≠清空），
    # 取值随开发者本机 config 而变，断言从快照读期望而非硬编码 ""。
    expected_output_dir = session.route("GET", "/api/snapshot")["compiler"]["output_dir"]

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
        "output_dir": expected_output_dir,
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


def test_workbench_session_persists_compiler_settings_after_llm_settings_save(tmp_path):
    config_path = tmp_path / "config.toml"
    output_dir = tmp_path / "configured-output"
    session = WorkbenchSession(config_path=config_path)

    compiler_response = session.route(
        "POST",
        "/api/settings/compiler",
        {
            "mode": "lp",
            "converter_path": "/Applications/LP_XMLConverter",
            "output_dir": str(output_dir),
        },
    )
    llm_response = session.route(
        "POST",
        "/api/settings/llm",
        {
            "model": "deepseek-chat",
            "api_key": "deepseek-key",
            "api_base": "https://api.deepseek.com/v1",
            "max_retries": 5,
            "assistant_settings": "short answers",
        },
    )
    reloaded = WorkbenchSession(config_path=config_path)

    assert compiler_response["ok"] is True
    assert llm_response["ok"] is True
    assert reloaded.compiler_mode == "lp"
    assert reloaded.converter_path == "/Applications/LP_XMLConverter"
    assert reloaded.output_dir == str(output_dir)


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


def test_workbench_session_saves_api_key_via_settings_route(tmp_path, monkeypatch):
    for name in ["ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "config.toml"
    session = WorkbenchSession(config_path=config_path)

    switched = session.route("PATCH", "/api/settings/llm/model", {"model": "glm-4-flash"})
    assert switched["ok"] is True
    assert switched["llm"]["model_available"] is False

    saved = session.route("POST", "/api/settings/llm/api-key", {"model": "glm-4-flash", "api_key": "zk-route"})
    assert saved["ok"] is True
    assert saved["llm"]["model"] == "glm-4-flash"
    assert saved["llm"]["model_available"] is True

    reloaded = WorkbenchSession(config_path=config_path)
    assert reloaded.llm_model == "glm-4-flash"
    assert reloaded.llm_api_key == "zk-route"


def test_workbench_session_reload_runtime_settings_reads_updated_config_file(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "deepseek-chat"
api_key = "old-key"
api_base = "https://api.deepseek.com/v1"
temperature = 0.2
max_tokens = 4096
provider_keys = {}
custom_providers = []
assistant_settings = "old"

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
    config_path.write_text(
        """
[llm]
model = "mimo-v2.5-pro"
api_key = ""
api_base = ""
temperature = 0.2
max_tokens = 4096
provider_keys = {}
assistant_settings = "new"

[[llm.custom_providers]]
name = "mimo"
base_url = "https://token-plan-cn.xiaomimimo.com/v1"
api_key = "mimo-key"
protocol = "openai"
models = ["mimo-v2.5-pro"]

[agent]
max_iterations = 8
validate_xml = true
diff_check = true
auto_version = true

[compiler]
path = "/Applications/LP_XMLConverter"
timeout = 60
""",
        encoding="utf-8",
    )

    response = session.route("GET", "/api/settings/runtime")

    assert response["ok"] is True
    assert response["llm"]["model"] == "mimo-v2.5-pro"
    assert response["llm"]["api_key"] == "mimo-key"
    assert response["llm"]["api_base"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert response["llm"]["max_retries"] == 8
    assert response["llm"]["assistant_settings"] == "new"
    assert response["llm"]["model_groups"]["custom"][0]["id"] == "mimo-v2.5-pro"
    assert response["compiler"]["converter_path"] == "/Applications/LP_XMLConverter"


def test_workbench_session_exposes_official_and_custom_llm_model_groups(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "ymg-gpt-5.3-codex"
api_key = ""
api_base = ""
temperature = 0.2
max_tokens = 4096
provider_keys = {}
assistant_settings = ""

[[llm.custom_providers]]
name = "ymg"
base_url = "https://api.ymg.example/v1"
api_key = "ymg-key"
protocol = "openai"
models = [{ alias = "ymg-gpt-5.3-codex", model = "gpt-5.3-codex" }]
""",
        encoding="utf-8",
    )
    session = WorkbenchSession(config_path=config_path)

    response = session.route("GET", "/api/settings/runtime")

    assert response["ok"] is True
    custom = response["llm"]["model_groups"]["custom"]
    official = response["llm"]["model_groups"]["official"]
    assert custom == [
        {
            "id": "ymg-gpt-5.3-codex",
            "label": "ymg-gpt-5.3-codex",
            "kind": "custom",
            "provider": "ymg",
            "target_model": "gpt-5.3-codex",
            "protocol": "openai",
            "api_base": "https://api.ymg.example/v1",
            "has_api_key": True,
        }
    ]
    assert any(option["id"] == "deepseek-v4-flash" and option["kind"] == "official" for option in official)
    assert response["llm"]["models"][0] == "ymg-gpt-5.3-codex"
    assert response["llm"]["model_options"][0]["kind"] == "custom"


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
    saved = GDLAgentConfig.load(str(config_path))
    assert saved.llm.provider_keys["openai"] == "openai-key"


def test_workbench_session_preserves_official_provider_key_when_save_has_blank_key(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "gpt-4.1-mini"
api_key = ""
api_base = ""
temperature = 0.2
max_tokens = 4096
custom_providers = []
assistant_settings = ""

[llm.provider_keys]
openai = "existing-openai-key"

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
            "model": "gpt-4.1-mini",
            "api_key": "",
            "api_base": "",
            "max_retries": 5,
            "assistant_settings": "",
        },
    )
    saved = GDLAgentConfig.load(str(config_path))

    assert response["ok"] is True
    assert response["llm"]["api_key"] == "existing-openai-key"
    assert saved.llm.provider_keys["openai"] == "existing-openai-key"
    assert WorkbenchSession(config_path=config_path).llm_api_key == "existing-openai-key"


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


def test_workbench_session_choose_converter_file_returns_draft_compiler_settings(tmp_path):
    config_path = tmp_path / "config.toml"
    session = WorkbenchSession(
        config_path=config_path,
        file_chooser=lambda: "/Applications/LP_XMLConverter",
    )

    response = session.route("POST", "/api/dialog/open-file", {"purpose": "compiler"})

    assert response["ok"] is True
    assert response["path"] == "/Applications/LP_XMLConverter"
    assert response["compiler"] == {
        "mode": "mock",
        "converter_path": "/Applications/LP_XMLConverter",
        "output_dir": "",
    }
    assert WorkbenchSession(config_path=config_path).converter_path != "/Applications/LP_XMLConverter"


def test_workbench_session_choose_converter_file_uses_compiler_file_chooser_purpose(tmp_path):
    purposes = []
    session = WorkbenchSession(
        config_path=tmp_path / "config.toml",
        file_chooser=lambda purpose: purposes.append(purpose) or "/Applications/LP_XMLConverter",
    )

    response = session.route("POST", "/api/dialog/open-file", {"purpose": "compiler"})

    assert response["ok"] is True
    assert purposes == ["compiler"]


def test_workbench_session_choose_output_directory_returns_draft_compiler_settings(tmp_path):
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
    assert "Create or open a project" in response["error"]


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



def test_workbench_session_imports_assistant_history_append_merge(tmp_path):
    """P6a happy path：双项目，目标 transcript 原有条目保留 + 源条目追加在后。"""
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    source = HSFProject.create_new("SourceShelf", str(tmp_path))
    source_hsf = source.save_to_disk()
    ErrorLearningStore(source_hsf).append_chat_messages(
        [
            {"role": "user", "content": "源项目问题"},
            {"role": "assistant", "content": "源项目答复"},
        ],
        project_name="SourceShelf",
        source="ui_chat",
    )

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})
    session.route(
        "POST",
        "/api/assistant/history",
        {"messages": [{"role": "user", "content": "目标原有记录"}]},
    )

    response = session.route("POST", "/api/assistant/history/import", {"source_path": str(source_hsf)})

    assert response["ok"] is True
    assert response["imported"] == 2
    assert response["source_name"] == "SourceShelf"
    loaded = session.route("GET", "/api/assistant/history")
    assert loaded["messages"] == [
        {"role": "user", "content": "目标原有记录"},
        {"role": "user", "content": "源项目问题"},
        {"role": "assistant", "content": "源项目答复"},
    ]
    # 追加条目带 source 标记 imported:<源目录名>
    transcript = current_hsf / ".openbrep" / "memory" / "chats" / "chat_transcript.jsonl"
    lines = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [line["source"] for line in lines] == ["react_workbench", "imported:SourceShelf", "imported:SourceShelf"]
    assert [line["project_name"] for line in lines] == ["CurrentShelf", "CurrentShelf", "CurrentShelf"]


def test_workbench_session_import_assistant_history_requires_open_project():
    session = WorkbenchSession()
    response = session.route("POST", "/api/assistant/history/import", {"source_path": "/some/source"})

    assert response["ok"] is False
    assert response["error"] == "Load an HSF project before importing assistant history."


def test_workbench_session_import_assistant_history_rejects_missing_source_path(tmp_path):
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})

    response = session.route("POST", "/api/assistant/history/import", {})

    assert response["ok"] is False
    assert response["error"] == "source_path is required."


def test_workbench_session_import_assistant_history_names_missing_source_path(tmp_path):
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    missing = tmp_path / "does-not-exist"
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})

    response = session.route("POST", "/api/assistant/history/import", {"source_path": str(missing)})

    assert response["ok"] is False
    assert str(missing) in response["error"]


def test_workbench_session_import_assistant_history_rejects_same_project(tmp_path):
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})

    response = session.route("POST", "/api/assistant/history/import", {"source_path": str(current_hsf)})

    assert response["ok"] is False
    assert "same as the current project" in response["error"]


def test_workbench_session_import_assistant_history_empty_source_imports_zero(tmp_path):
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    source = HSFProject.create_new("EmptySource", str(tmp_path))
    source_hsf = source.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})

    response = session.route("POST", "/api/assistant/history/import", {"source_path": str(source_hsf)})

    assert response["ok"] is True
    assert response["imported"] == 0
    assert response["source_name"] == "EmptySource"
    loaded = session.route("GET", "/api/assistant/history")
    assert loaded["messages"] == []


def test_workbench_session_import_assistant_history_cleans_roles(tmp_path):
    """非法 role → assistant；空 content 跳过（与 list_assistant_history 同规则）。"""
    current = HSFProject.create_new("CurrentShelf", str(tmp_path))
    current_hsf = current.save_to_disk()
    source = HSFProject.create_new("RoleSource", str(tmp_path))
    source_hsf = source.save_to_disk()
    ErrorLearningStore(source_hsf).append_chat_messages(
        [
            {"role": "user", "content": "合法用户"},
            {"role": "system", "content": "非法 role 变成 assistant"},
            {"role": "tool", "content": "   "},
        ],
        project_name="RoleSource",
        source="ui_chat",
    )
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(current_hsf)})

    response = session.route("POST", "/api/assistant/history/import", {"source_path": str(source_hsf)})

    assert response["ok"] is True
    assert response["imported"] == 2
    loaded = session.route("GET", "/api/assistant/history")
    assert loaded["messages"] == [
        {"role": "user", "content": "合法用户"},
        {"role": "assistant", "content": "非法 role 变成 assistant"},
    ]


def test_workbench_session_distill_history_intent_happy_path(tmp_path):
    """P6b：mock LLM happy path —— prompt 含对话文本与 system 指令，返回 instruction + message_count。"""
    captured: dict = {}

    class FakeLLM:
        def generate(self, messages, **kwargs):
            captured["messages"] = messages
            return LLMResponse(content="请把书架层板数改成 5，并保留现有 3D 代码。", model="mock", usage={}, finish_reason="stop")

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def _make_llm(self, request):
            captured["request"] = request
            return FakeLLM()

    project = HSFProject.create_new("DistillShelf", str(tmp_path))
    hsf = project.save_to_disk()
    ErrorLearningStore(hsf).append_chat_messages(
        [
            {"role": "user", "content": "把书架层板数改成 5"},
            {"role": "assistant", "content": "```gdl\nSHELF_COUNT = 5\n```"},
        ],
        project_name="DistillShelf",
        source="react_workbench",
    )
    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf)})

    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is True
    assert response["instruction"] == "请把书架层板数改成 5，并保留现有 3D 代码。"
    assert response["message_count"] == 2
    # 关键断言：system prompt 一处定义的完整指令 + user prompt 含对话文本
    system = next(m for m in captured["messages"] if m["role"] == "system")
    assert "把以下 GDL 工作台对话整理成一段" in system["content"]
    assert "```gdl" in system["content"]
    user = next(m for m in captured["messages"] if m["role"] == "user")
    assert "user: 把书架层板数改成 5" in user["content"]
    assert "assistant: ```gdl" in user["content"]
    # 适配器拿到 session 的 assistant_settings
    assert captured["request"].assistant_settings == session.assistant_settings


def test_workbench_session_distill_history_requires_open_project():
    session = WorkbenchSession()
    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is False
    assert response["error"] == "Load an HSF project before distilling assistant history."


def test_workbench_session_distill_history_empty_record_errors(tmp_path):
    project = HSFProject.create_new("EmptyDistill", str(tmp_path))
    hsf = project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf)})

    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is False
    assert response["error"] == "当前项目没有聊天记录可整理。"


def test_workbench_session_distill_history_llm_failure_passthrough(tmp_path):
    """LLM 调用失败 → error 透传（不静默）。"""

    class FailingLLM:
        def generate(self, messages, **kwargs):
            raise RuntimeError("upstream quota exhausted")

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def _make_llm(self, request):
            return FailingLLM()

    project = HSFProject.create_new("FailDistill", str(tmp_path))
    hsf = project.save_to_disk()
    ErrorLearningStore(hsf).append_chat_messages(
        [{"role": "user", "content": "把层板数改成 5"}],
        project_name="FailDistill",
        source="react_workbench",
    )
    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf)})

    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is False
    assert "upstream quota exhausted" in response["error"]


def test_workbench_session_distill_history_trims_to_recent_messages(tmp_path):
    """超过 30 条时只取最近 30 条（防超长），message_count 反映实际条数。"""
    captured: dict = {}

    class FakeLLM:
        def generate(self, messages, **kwargs):
            captured["messages"] = messages
            return LLMResponse(content="整理结果", model="mock", usage={}, finish_reason="stop")

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def _make_llm(self, request):
            return FakeLLM()

    project = HSFProject.create_new("LongDistill", str(tmp_path))
    hsf = project.save_to_disk()
    ErrorLearningStore(hsf).append_chat_messages(
        [{"role": "user", "content": f"第 {i} 条"} for i in range(40)],
        project_name="LongDistill",
        source="react_workbench",
    )
    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf)})

    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is True
    assert response["message_count"] == 30
    user = next(m for m in captured["messages"] if m["role"] == "user")
    assert "第 9 条" not in user["content"]  # 最旧 10 条被裁剪
    assert "第 39 条" in user["content"]


def test_workbench_session_distill_history_ignores_empty_content_entries(tmp_path):
    """空 content 条目跳过（与 list 同规则），全部为空 → 按空记录报错。"""
    project = HSFProject.create_new("BlankDistill", str(tmp_path))
    hsf = project.save_to_disk()
    ErrorLearningStore(hsf).append_chat_messages(
        [{"role": "user", "content": "   "}, {"role": "assistant", "content": ""}],
        project_name="BlankDistill",
        source="react_workbench",
    )
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf)})

    response = session.route("POST", "/api/assistant/history/distill", {})

    assert response["ok"] is False
    assert response["error"] == "当前项目没有聊天记录可整理。"


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


# ── G4：蒸馏教训确认卡路由（/api/lessons，lesson ≠ skill）──

def _write_distilled_lesson(hsf_dir, fingerprint, **fields):
    """向项目教训库追加一条蒸馏教训（与 save_lessons 同一落盘路径/形状）。"""
    lesson = {
        "fingerprint": fingerprint,
        "pattern": "参数脚本与指令不一致导致编译失败",
        "guidance": "生成前逐条核对参数名与指令描述",
        "status": "proposed",
        "count": 1,
        "first_seen": "2026-09-05T10:00:00",
        "last_seen": "2026-09-05T10:00:00",
        "evidence_refs": [
            {
                "run_id": "r_gate_1",
                "check_type": "compile",
                "before_revision": "rev_1",
                "after_revision": "rev_2",
            }
        ],
        "raw_excerpt": "编译失败（mode=mock）",
    }
    lesson.update(fields)
    target = (
        Path(hsf_dir) / ".openbrep" / "memory" / "learnings" / "distilled_lessons.jsonl"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(lesson, ensure_ascii=False, sort_keys=True) + "\n")


def _write_quality_record(hsf_dir, run_id="r_gate_1"):
    runs = Path(hsf_dir) / ".openbrep" / "quality" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "ts": "2026-09-05T10:00:00+00:00",
        "project_ref": {"path_hash": "abc123", "name": "DistillShelf"},
        "intent": "create",
        "instruction_summary": "做一个书柜",
        "outcome": "gate_fail",
        "delivery": {
            "status": "fail",
            "compile": {"status": "fail", "mode": "mock"},
            "static": {"status": "not_run"},
            "semantic": {"status": "not_run"},
        },
        "artifact_quality": {},
        "execution_cost": {"llm_calls": 3, "tool_calls": 4},
        "provenance": {"before_revision": "rev_1", "after_revision": "rev_2"},
    }
    (runs / f"{run_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


def test_workbench_session_lists_distilled_lessons_with_status_filter(tmp_path):
    project = HSFProject.create_new("LessonShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    empty = session.route("GET", "/api/lessons")
    assert empty["ok"] is True
    assert empty["lessons"] == []

    _write_distilled_lesson(hsf_dir, "quality:aaa", pattern="教训A", status="proposed")
    _write_distilled_lesson(hsf_dir, "quality:bbb", pattern="教训B", status="active")
    _write_distilled_lesson(hsf_dir, "quality:ccc", pattern="教训C", status="rejected")

    all_cards = session.route("GET", "/api/lessons")
    proposed = session.route("GET", "/api/lessons?status=proposed")
    active = session.route("GET", "/api/lessons?status=active")
    unknown_status = session.route("GET", "/api/lessons?status=nope")

    assert all_cards["ok"] is True
    assert {card["fingerprint"] for card in all_cards["lessons"]} == {
        "quality:aaa", "quality:bbb", "quality:ccc",
    }
    card = proposed["lessons"][0]
    assert [c["fingerprint"] for c in proposed["lessons"]] == ["quality:aaa"]
    assert [c["fingerprint"] for c in active["lessons"]] == ["quality:bbb"]
    assert [c["fingerprint"] for c in unknown_status["lessons"]] == []
    assert card["pattern"] == "教训A"
    assert card["status"] == "proposed"
    assert card["count"] == 1
    assert card["evidence_refs"] == [
        {
            "run_id": "r_gate_1",
            "check_type": "compile",
            "before_revision": "rev_1",
            "after_revision": "rev_2",
        }
    ]
    assert card["raw_excerpt"] == "编译失败（mode=mock）"

    no_project = WorkbenchSession(config_path=tmp_path / "config2.toml")
    assert no_project.route("GET", "/api/lessons") == {"ok": True, "lessons": []}


def test_workbench_session_promotes_and_rejects_distilled_lesson(tmp_path):
    project = HSFProject.create_new("LessonFlowShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    _write_distilled_lesson(hsf_dir, "quality:flow", pattern="书架教训流")

    promoted = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:flow", "decision": "promote"},
    )
    assert promoted["ok"] is True
    assert promoted["changed"] is True
    assert promoted["status"] == "active"
    assert "书架教训流" in feedback_distill.build_distilled_lessons_prompt(hsf_dir)

    demoted = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:flow", "decision": "demote"},
    )
    assert demoted["ok"] is True
    assert demoted["status"] == "proposed"

    rejected = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:flow", "decision": "reject"},
    )
    assert rejected["ok"] is True
    assert rejected["changed"] is True
    assert rejected["status"] == "rejected"
    assert "书架教训流" not in feedback_distill.build_distilled_lessons_prompt(hsf_dir)

    # rejected 是终态：promote 一个 rejected 属非法迁移
    revived = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:flow", "decision": "promote"},
    )
    assert revived["ok"] is False
    missing = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:nope", "decision": "promote"},
    )
    assert missing["ok"] is False
    bad_decision = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:flow", "decision": "frobnicate"},
    )
    assert bad_decision["ok"] is False


def test_workbench_session_distilled_lesson_routes_require_project(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    listed = session.route("GET", "/api/lessons")
    distilled = session.route("POST", "/api/lessons/distill", {})
    status = session.route(
        "POST", "/api/lessons/status",
        {"fingerprint": "quality:x", "decision": "promote"},
    )

    assert listed["ok"] is True
    assert listed["lessons"] == []
    assert distilled["ok"] is False
    assert "project" in distilled["error"]
    assert status["ok"] is False


def test_workbench_session_distills_quality_lessons_through_route(tmp_path, monkeypatch):
    """路由全链路：质量账本候选 → （假 LLM）提炼 → proposed 教训 → watermark 防重。"""
    project = HSFProject.create_new("DistillShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    _write_quality_record(hsf_dir)

    class FakeLLM:
        calls = 0

        def generate(self, messages, **kwargs):
            FakeLLM.calls += 1
            item = {
                "pattern": "书架指令描述不清导致编译失败",
                "guidance": "生成前核对参数脚本与指令摘要",
                "evidence_refs": [{"run_id": "r_gate_1", "check_type": "compile"}],
            }
            return type("Resp", (), {"content": json.dumps([item], ensure_ascii=False)})()

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    monkeypatch.setattr("openbrep.feedback_distill._build_distill_llm", FakeLLM)

    result = session.route("POST", "/api/lessons/distill", {})

    assert result["ok"] is True
    assert result["new_lessons"] == 1
    assert result["rejected"] == 0
    assert result["total_lessons"] == 1
    cards = session.route("GET", "/api/lessons?status=proposed")
    assert len(cards["lessons"]) == 1
    lesson = cards["lessons"][0]
    assert lesson["fingerprint"].startswith("quality:")
    assert lesson["status"] == "proposed"
    assert lesson["evidence_refs"] == [
        {
            "run_id": "r_gate_1",
            "check_type": "compile",
            "before_revision": "rev_1",
            "after_revision": "rev_2",
        }
    ]

    again = session.route("POST", "/api/lessons/distill", {})
    assert again["ok"] is True
    assert again["new_lessons"] == 0
    assert FakeLLM.calls == 1  # watermark 已推进：二次运行不建 LLM 不重复提炼


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


def test_workbench_session_generate_delivers_output_when_verification_fails(tmp_path):
    """验证未过（success=False）但有产出时照常交付并挂载，不丢用户的生成结果。"""
    project = HSFProject.create_new("ShelfWithIssue", str(tmp_path))
    hsf_dir = project.save_to_disk()

    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            pass

        def execute(self, request):
            request.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(
                success=False,
                intent="MODIFY",
                scripts={"scripts/3d.gdl": "BLOCK A, B, ZZYZX\n"},
                plain_text="已修改，但几何验证未通过",
                project=request.project,
                verification={"passed": False, "checks": []},
            )

    session = WorkbenchSession(pipeline_class=FakePipeline)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant/generate", {"message": "修改"})

    assert response["ok"] is True
    assert response["assistant"]["verification"]["passed"] is False
    assert "几何验证未通过" in response["assistant"]["reply"]


def test_workbench_session_create_delivers_output_when_verification_fails(tmp_path):
    class FakePipeline:
        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            return TaskResult(
                success=False,
                intent="CREATE",
                scripts={"scripts/3d.gdl": "BLOCK A, B, ZZYZX\n"},
                plain_text="已创建，但验证未通过",
                project=project,
                verification={"passed": False, "checks": []},
            )

    session = WorkbenchSession(pipeline_class=FakePipeline, config_path=tmp_path / "config.toml")
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "create a bookshelf", "output_dir": str(tmp_path)},
    )

    assert response["ok"] is True
    assert response["assistant"]["verification"]["passed"] is False
    assert response["project"]["source"] == "hsf"


def test_workbench_session_snapshot_carries_session_identity_and_epoch(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    snapshot = session.snapshot()
    assert snapshot["session_id"]
    epoch_start = snapshot["project_epoch"]

    session.route("POST", "/api/project/new", {})
    after_new = session.snapshot()["project_epoch"]
    session.route("POST", "/api/project/close", {})
    after_close = session.snapshot()["project_epoch"]

    assert after_new == epoch_start + 1
    assert after_close == after_new + 1


def test_workbench_session_restores_last_project_on_startup(tmp_path):
    hsf_dir = HSFProject.create_new("RestoreMe", str(tmp_path / "proj")).save_to_disk()
    config_path = tmp_path / "workbench.toml"
    session = WorkbenchSession(config_path=config_path)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    restarted = WorkbenchSession(config_path=config_path)
    result = restarted.restore_last_project()

    assert result["restored"] is True
    assert restarted.project is not None
    assert restarted.project.name == "RestoreMe"


def test_workbench_session_restore_keeps_empty_when_path_missing(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.recent_project_paths = [str(tmp_path / "gone")]

    result = session.restore_last_project()

    assert result["restored"] is False
    assert session.project is None


# ── Workspace 附着（P3-d1）──────────────────────────────────


def _make_workspace_with_project(tmp_path, project_name="Shelf", source_name="shelf.gdl"):
    """初始化工作区并导入一个 gdl 项目；返回 (workspace_root, project_dir)。"""
    from openbrep.workbench.workspace_service import import_to_workspace, init_workspace

    ws = tmp_path / "ws"
    init_workspace(str(ws))
    source = tmp_path / source_name
    source.write_text("BLOCK A, B, ZZYZX\nEND\n", encoding="utf-8")
    result = import_to_workspace(str(ws), str(source), "gdl")
    assert result["ok"] is True
    return ws, Path(result["project_path"])


def test_workbench_session_independent_mode_workspace_is_none(tmp_path):
    """独立项目模式：未附着工作区时 snapshot['workspace'] 为 None（现状行为）。"""
    session = WorkbenchSession(config_path=str(tmp_path / "cfg.toml"))
    snapshot = session.snapshot()
    assert snapshot["ok"] is True
    assert snapshot["workspace"] is None
    assert session.workspace_path is None


def test_workbench_session_implicitly_attaches_workspace_on_project_load(tmp_path):
    """隐式附着：加载工作区 hsf/ 下项目 → workspace_path = 工作区根；工作区外 → None。"""
    ws, project_dir = _make_workspace_with_project(tmp_path)

    outside = HSFProject.create_new("Outside", str(tmp_path / "plain"))
    outside.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
    outside_dir = outside.save_to_disk()

    session = WorkbenchSession(config_path=str(tmp_path / "cfg.toml"))

    loaded = session.route("POST", "/api/project/load", {"path": str(project_dir)})
    assert loaded["ok"] is True
    assert session.workspace_path == ws.resolve()

    loaded_outside = session.route("POST", "/api/project/load", {"path": str(outside_dir)})
    assert loaded_outside["ok"] is True
    assert session.workspace_path is None


def test_workbench_session_workspace_open_close_scan_search(tmp_path):
    """显式 open/close/scan/search：正常与错误形态。"""
    ws, project_dir = _make_workspace_with_project(tmp_path)
    session = WorkbenchSession(config_path=str(tmp_path / "cfg.toml"))
    session.route("POST", "/api/project/load", {"path": str(project_dir)})

    # open 未初始化路径 → 统一错误 + 提示先 init
    bad = session.route("POST", "/api/workspace/open", {"path": str(tmp_path / "nows")})
    assert bad["ok"] is False
    assert bad["code"] == "not_a_workspace"
    assert "/api/workspace/init" in bad["error"]

    # open 成功 → 返回 scan 结果
    opened = session.route("POST", "/api/workspace/open", {"path": str(ws)})
    assert opened["ok"] is True
    assert opened["workspace"] == str(ws.resolve())
    assert opened["project_count"] == 1
    assert session.workspace_path == ws.resolve()

    # scan
    scanned = session.route("GET", "/api/workspace/scan")
    assert scanned["ok"] is True
    assert scanned["workspace"] == str(ws.resolve())
    assert scanned["project_count"] == 1

    # search（query 走 URL query 参数）
    searched = session.route("GET", "/api/workspace/search?q=block")
    assert searched["ok"] is True
    assert searched["hit_count"] >= 1
    assert searched["workspace"] == str(ws.resolve())

    # close → 独立模式，项目保持打开
    closed = session.route("POST", "/api/workspace/close")
    assert closed["ok"] is True
    assert closed["workspace"] is None
    assert session.workspace_path is None
    assert session.project is not None

    # close 后 search → no_workspace；scan → {ok, workspace: null}
    no_ws = session.route("GET", "/api/workspace/search", {"q": "x"})
    assert no_ws["ok"] is False
    assert no_ws["code"] == "no_workspace"
    scanned_after = session.route("GET", "/api/workspace/scan")
    assert scanned_after["ok"] is True
    assert scanned_after["workspace"] is None


def test_workbench_session_workspace_snapshot_block_active_flag(tmp_path):
    """snapshot['workspace']：projects 带 active 标记且与当前项目一致。"""
    ws, project_dir = _make_workspace_with_project(tmp_path)
    # 第二个项目（不带 active）
    from openbrep.workbench.workspace_service import import_to_workspace

    source2 = tmp_path / "chair.gdl"
    source2.write_text("BLOCK A, B, ZZYZX\nCYLIND 1, 1\n", encoding="utf-8")
    import_to_workspace(str(ws), str(source2), "gdl")

    session = WorkbenchSession(config_path=str(tmp_path / "cfg.toml"))
    session.route("POST", "/api/project/load", {"path": str(project_dir)})
    session.route("POST", "/api/workspace/open", {"path": str(ws)})

    block = session.snapshot()["workspace"]
    assert block is not None
    assert block["path"] == str(ws.resolve())
    assert block["project_count"] == 2
    active = {p["name"]: p["active"] for p in block["projects"]}
    assert active.get("Shelf") is True or active.get("shelf") is True
    assert False in active.values()  # 至少一个非 active


def test_workbench_session_workspace_init_route(tmp_path):
    """POST /api/workspace/init：创建四区 + workspace.toml；重复调用幂等。"""
    ws = tmp_path / "ws"
    session = WorkbenchSession(config_path=str(tmp_path / "cfg.toml"))

    first = session.route("POST", "/api/workspace/init", {"path": str(ws)})
    assert first["ok"] is True
    for zone in ("materials", "sources", "hsf", "artifacts"):
        assert (ws / zone).is_dir()
    assert (ws / ".openbrep" / "workspace.toml").is_file()

    second = session.route("POST", "/api/workspace/init", {"path": str(ws)})
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_workbench_session_last_workspace_persist_and_restore(tmp_path):
    """last_workspace：open 持久化到 config；restore 静默附着；失效降级独立模式。"""
    ws, project_dir = _make_workspace_with_project(tmp_path)
    config_path = tmp_path / "cfg.toml"

    session = WorkbenchSession(config_path=str(config_path))
    session.route("POST", "/api/project/load", {"path": str(project_dir)})
    session.route("POST", "/api/workspace/open", {"path": str(ws)})
    assert session.config.last_workspace == str(ws.resolve())

    # 新会话 restore：recent_projects 有该项目 + last_workspace 有效 → 静默附着
    restored = WorkbenchSession(config_path=str(config_path))
    result = restored.restore_last_project()
    assert result["restored"] is True
    assert restored.workspace_path == ws.resolve()
    assert restored.snapshot()["workspace"] is not None

    # last_workspace 失效 → 降级独立模式（不报错）
    stale_config = tmp_path / "stale.toml"
    from openbrep.config import GDLAgentConfig

    cfg = GDLAgentConfig.load(str(config_path))
    cfg.last_workspace = str(tmp_path / "does-not-exist")
    cfg.recent_projects = [str(project_dir)]
    cfg.save(str(stale_config))
    degraded = WorkbenchSession(config_path=str(stale_config))
    degraded.restore_last_project()
    assert degraded.workspace_path is None


def test_workbench_session_trash_project_moves_to_workspace_trash(tmp_path):
    """route 正常流：附着工作区 + 非当前项目 → 移入 .openbrep/trash/。"""
    from openbrep.workbench.workspace_service import init_workspace

    ws = tmp_path / "ws"
    init_workspace(str(ws))
    project = HSFProject.create_new("TrashShelf", str(ws / "hsf"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    project_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "cfg.toml")
    session.route("POST", "/api/workspace/open", {"path": str(ws)})
    response = session.route("POST", "/api/workspace/trash-project", {"path": str(project_dir)})

    assert response["ok"] is True
    assert "trashed_to" in response
    assert Path(response["trashed_to"]).is_dir()
    assert not project_dir.exists()
    assert (ws / ".openbrep" / "trash").is_dir()


def test_workbench_session_trash_project_rejects_active_project(tmp_path):
    """安全闸 a：当前会话打开中的项目 → project_active 拒绝。"""
    from openbrep.workbench.workspace_service import init_workspace

    ws = tmp_path / "ws"
    init_workspace(str(ws))
    project = HSFProject.create_new("ActiveShelf", str(ws / "hsf"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    project_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "cfg.toml")
    session.route("POST", "/api/workspace/open", {"path": str(ws)})
    session.route("POST", "/api/project/load", {"path": str(project_dir)})

    response = session.route("POST", "/api/workspace/trash-project", {"path": str(project_dir)})

    assert response["ok"] is False
    assert response["code"] == "project_active"
    assert "先切换到其他项目" in response["error"]
    assert project_dir.exists()  # 原封不动


def test_workbench_session_trash_project_requires_attached_workspace(tmp_path):
    """未附着工作区 → no_workspace。"""
    project = HSFProject.create_new("LoneShelf", str(tmp_path))
    project_dir = project.save_to_disk()

    session = WorkbenchSession(config_path=tmp_path / "cfg.toml")
    response = session.route("POST", "/api/workspace/trash-project", {"path": str(project_dir)})

    assert response["ok"] is False
    assert response["code"] == "no_workspace"


# ── 任务 U：GUI 导入收敛到工作区 ────────────────────────────────────


def _attached_workspace_session(tmp_path):
    """初始化工作区 + 显式附着的 WorkbenchSession；返回 (session, ws_root)。"""
    from openbrep.workbench.workspace_service import init_workspace

    ws = tmp_path / "ws"
    init_workspace(str(ws))
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    opened = session.route("POST", "/api/workspace/open", {"path": str(ws)})
    assert opened["ok"] is True
    return session, ws


def test_workbench_session_attached_workspace_import_gdl_routes_to_hsf(tmp_path):
    """附着工作区时 GDL 导入：原件进 sources/、项目进 hsf/、origin 指向归档副本、
    会话加载新项目、hsf/ 无 staged 残留、源文件旁不落项目。"""
    gdl_path = tmp_path / "PlantPot.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\nEND\n", encoding="utf-8")
    session, ws = _attached_workspace_session(tmp_path)

    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True, response
    project_path = Path(response["project"]["path"])
    assert project_path.parent == (ws / "hsf").resolve()
    assert project_path.name == "PlantPot"
    # 会话加载新项目
    assert session.source_path == project_path
    # 结果字典：archived_source / project_path / imported_from
    assert response["project_path"] == str(project_path)
    assert response["imported_from"] == str(gdl_path)
    # 归档副本在 sources/ 下
    archived = Path(response["archived_source"])
    assert archived.parent == (ws / "sources").resolve()
    assert archived.read_text(encoding="utf-8") == gdl_path.read_text(encoding="utf-8")
    # origin 指向 sources/ 归档副本
    toml, text = _read_origin_toml(project_path)
    assert toml.exists()
    assert f'imported_from = "{response["archived_source"]}"' in text
    assert 'imported_kind = "gdl"' in text
    # hsf/ 无 staged 残留
    assert not (ws / "hsf" / "PlantPot.gdl").exists()
    # 源文件旁不落项目
    assert not (gdl_path.parent / "PlantPot").exists()
    # 附着保持同一工作区
    assert session.workspace_path == ws.resolve()
    assert response["workspace"]["path"] == str(ws.resolve())


def test_workbench_session_attached_workspace_import_gsm_routes_to_hsf_with_normalization(
    tmp_path, monkeypatch
):
    """附着工作区时 GSM 导入（fake compiler）：项目进 hsf/、normalization/decompile
    透传、staged 清理、origin 指向 sources/ 副本。"""
    gsm_path = tmp_path / "ImportedChair.gsm"
    gsm_path.write_bytes(b"fake gsm")

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            project = HSFProject.create_new("ConverterOutput", output_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            project.save_to_disk()
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    # 工作区路径走 import_source._import_gsm → openbrep.mcp_tools.HSFCompiler
    monkeypatch.setattr("openbrep.mcp_tools.HSFCompiler", FakeHSFCompiler)
    session, ws = _attached_workspace_session(tmp_path)

    response = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert response["ok"] is True, response
    project_path = Path(response["project"]["path"])
    assert project_path.parent == (ws / "hsf").resolve()
    assert project_path.name == "ImportedChair"
    assert session.source_path == project_path
    # normalization 透传（import_source 不产生 decompile，按设计不强求）
    assert "normalization" in response
    assert response["normalization"].get("lossless") is True
    # hsf/ 无 staged 残留
    assert not (ws / "hsf" / "ImportedChair.gsm").exists()
    # origin 指向 sources/ 副本
    toml, text = _read_origin_toml(project_path)
    assert f'imported_from = "{response["archived_source"]}"' in text
    assert 'imported_kind = "gsm"' in text


def test_workbench_session_attached_workspace_import_same_gsm_twice_two_projects_one_archive(
    tmp_path, monkeypatch
):
    """同名 GSM 导入两次 → hsf/ 两个项目目录、sources/ 只有一个归档件（字节相同复用）。"""
    gsm_path = tmp_path / "Dup.gsm"
    gsm_path.write_bytes(b"fake gsm dup")

    class FakeHSFCompiler:
        def __init__(self, converter_path=None, timeout=60):
            self.converter_path = converter_path
            self.timeout = timeout

        @property
        def is_available(self):
            return True

        def libpart2hsf(self, gsm_path_arg, output_dir):
            project = HSFProject.create_new("ConverterOutput", output_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            project.save_to_disk()
            return CompileResult(success=True, stdout="ok", exit_code=0, output_path=output_dir)

    monkeypatch.setattr("openbrep.mcp_tools.HSFCompiler", FakeHSFCompiler)
    session, ws = _attached_workspace_session(tmp_path)

    first = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})
    second = session.route("POST", "/api/project/import-gsm", {"path": str(gsm_path)})

    assert first["ok"] and second["ok"]
    p1, p2 = Path(first["project"]["path"]), Path(second["project"]["path"])
    assert p1 != p2
    assert p1.name == "Dup"
    assert p2.name == "Dup_v2"
    # sources/ 只有一个归档件（字节相同复用）
    assert len(list((ws / "sources").glob("Dup*.gsm"))) == 1
    # 两个项目 origin 指向同一个 sources 副本
    assert first["archived_source"] == second["archived_source"]
    _, t1 = _read_origin_toml(p1)
    _, t2 = _read_origin_toml(p2)
    assert f'imported_from = "{first["archived_source"]}"' in t1
    assert f'imported_from = "{second["archived_source"]}"' in t2
    # hsf/ 无 staged 残留
    assert not (ws / "hsf" / "Dup.gsm").exists()


def test_workbench_session_independent_import_gdl_stays_next_to_source(tmp_path):
    """未附着工作区：GDL 导入落点仍在源文件旁边（现有独立行为不变）。"""
    gdl_path = tmp_path / "Solo.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    assert session.workspace_path is None

    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True
    project_path = Path(response["project"]["path"])
    assert project_path.parent == gdl_path.parent
    assert project_path.name == "Solo"
    assert session.workspace_path is None
    assert "archived_source" not in response


def test_workbench_session_independent_import_chinese_filename_keeps_chinese_dir_name(tmp_path):
    """P7a 独立导入：中文文件名 .gdl 导入 → 项目目录名保持中文（不再剥成 Imported_GDL）。"""
    gdl_path = tmp_path / "书架.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True, response
    project_path = Path(response["project"]["path"])
    assert project_path.parent == gdl_path.parent
    assert project_path.name == "书架"
    assert session.workspace_path is None


def test_workbench_session_attached_workspace_import_chinese_filename_keeps_chinese_dir_name(tmp_path):
    """P7a 工作区导入：中文文件名 .gdl → 项目目录为中文、sources/ 归档件保留中文名。"""
    gdl_path = tmp_path / "新中式椅子.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\nEND\n", encoding="utf-8")
    session, ws = _attached_workspace_session(tmp_path)

    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True, response
    project_path = Path(response["project"]["path"])
    assert project_path.parent == (ws / "hsf").resolve()
    assert project_path.name == "新中式椅子"
    archived = Path(response["archived_source"])
    assert archived.parent == (ws / "sources").resolve()
    assert archived.name == "新中式椅子.gdl"
    toml, text = _read_origin_toml(project_path)
    assert toml.exists()
    assert f'imported_from = "{response["archived_source"]}"' in text


def test_workbench_session_attached_workspace_import_chinese_filename_twice_gets_vN(tmp_path):
    """P7a 工作区导入：同名中文 .gdl 导入两次 → 第一个保持原名、第二个 _v2（_vN 后缀）。"""
    gdl_path = tmp_path / "书架.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\nEND\n", encoding="utf-8")
    session, ws = _attached_workspace_session(tmp_path)

    first = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})
    second = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert first["ok"] and second["ok"]
    p1, p2 = Path(first["project"]["path"]), Path(second["project"]["path"])
    assert p1.name == "书架"
    assert p2.name == "书架_v2"
    # sources/ 只有一个归档件（字节相同复用）
    assert len(list((ws / "sources").glob("书架*.gdl"))) == 1


def test_workbench_session_broken_workspace_path_falls_back_to_independent(tmp_path):
    """workspace_path 指向的目录不是有效工作区（缺 workspace.toml）→ 降级独立模式。"""
    gdl_path = tmp_path / "Fallback.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.workspace_path = tmp_path / "broken_ws"  # 目录不存在，绕过 open 校验

    response = session.route("POST", "/api/project/import-gdl", {"path": str(gdl_path)})

    assert response["ok"] is True
    project_path = Path(response["project"]["path"])
    assert project_path.parent == gdl_path.parent
    assert "archived_source" not in response


def test_import_session_fake_without_workspace_path_stays_independent(tmp_path):
    """import_source 的 fake session 没有 workspace_path 属性 → 工作区分支不触发
    （防递归前提：fake session 走原独立导入路径）。"""
    from types import SimpleNamespace

    from openbrep.config import GDLAgentConfig
    from openbrep.workbench.project_session_service import WorkbenchProjectSessionService

    gdl_path = tmp_path / "FakeSession.gdl"
    gdl_path.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

    config = GDLAgentConfig()
    fake = SimpleNamespace(
        project=None,
        source="empty",
        source_path=None,
        recent_project_paths=[],
        config=config,
        config_path=tmp_path / "fake_config.toml",
        snapshot=lambda: {"ok": True},
        compiler_mode="mock",
        converter_path="",
        _choose_file_for_purpose=lambda purpose: None,
    )
    assert not hasattr(fake, "workspace_path")
    service = WorkbenchProjectSessionService(fake, real_compiler_factory=lambda _path: None)

    result = service.import_gdl_file({"path": str(gdl_path)})

    assert result["ok"] is True
    # 项目落在源文件旁边（独立路径），不经过工作区
    assert (gdl_path.parent / "FakeSession").is_dir()
    assert "archived_source" not in result


# ── P7b：AI create 生成后按 object_type rename 目录 ──────────────────

def _rename_pipeline_cls(object_type="", gsm_artifact=False):
    """生成一个可配置 object_type / .gsm 产物的 FakePipeline 类。

    object_type=None → TaskResult 不带 object_plan（模拟 plan 缺失）。
    """
    class _Pipe:
        last_request = None

        def __init__(self, trace_dir="./traces"):
            self.trace_dir = trace_dir

        def execute(self, request):
            _Pipe.last_request = request
            project = HSFProject.create_new(request.gsm_name, request.work_dir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
            if gsm_artifact:
                (Path(request.output_dir) / f"{request.gsm_name}.gsm").write_text("fake gsm", encoding="utf-8")
            kwargs = {}
            if object_type is not None:
                kwargs["object_plan"] = {"object_type": object_type}
            return TaskResult(
                success=True,
                intent="CREATE",
                scripts={"scripts/3d.gdl": project.get_script(ScriptType.SCRIPT_3D)},
                plain_text="已创建",
                project=project,
                **kwargs,
            )

    return _Pipe


def test_p7b_create_renames_project_dir_by_object_type(tmp_path):
    """object_type 可用 → 目录/项目名/session/最近列表全部切到新名，旧路径移除。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="坐斗"))
    # 预置旧临时路径，验证 rename 后从最近项目列表移除
    session.recent_project_paths = [str(tmp_path / "临时构件")]

    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )

    assert response["ok"] is True, response
    old_dir = tmp_path / "临时构件"
    new_dir = tmp_path / "坐斗"
    assert not old_dir.exists()
    assert new_dir.is_dir()
    assert response["project"]["name"] == "坐斗"
    assert response["project"]["path"] == str(new_dir.resolve())
    assert session.source_path == new_dir.resolve()
    assert str(old_dir.resolve()) not in session.recent_project_paths
    assert str(new_dir.resolve()) in session.recent_project_paths
    assert HSFProject.load_from_disk(str(new_dir)).get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\n"
    assert not [w for w in response["warnings"] if "改名" in w]


def test_p7b_create_object_type_missing_keeps_rule_name(tmp_path):
    """object_plan 缺失（默认 {}）→ 不 rename，行为同现状。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type=None))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "临时构件"
    assert (tmp_path / "临时构件").is_dir()
    assert not [w for w in response["warnings"] if "改名" in w]


def test_p7b_create_object_type_empty_keeps_rule_name(tmp_path):
    """object_type 为空串 → 不 rename。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type=""))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "临时构件"
    assert (tmp_path / "临时构件").is_dir()


def test_p7b_create_object_type_same_as_temp_name_no_rename(tmp_path):
    """object_type 与临时名相同 → 不 rename（本来就是这个名字）。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="临时构件"))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "临时构件"
    assert (tmp_path / "临时构件").is_dir()
    assert not [w for w in response["warnings"] if "改名" in w]


def test_p7b_create_object_type_sanitizes_to_fallback_no_rename(tmp_path):
    """object_type sanitize 后是兜底名（未命名构件）→ 不 rename。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="///"))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "临时构件"
    assert (tmp_path / "临时构件").is_dir()


def test_p7b_create_object_type_conflict_gets_vN(tmp_path):
    """目标名被占用 → unique _vN 后缀。"""
    (tmp_path / "坐斗").mkdir()
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="坐斗"))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "坐斗_v2"
    assert (tmp_path / "坐斗_v2").is_dir()
    assert not (tmp_path / "临时构件").exists()


def test_p7b_create_rename_failure_keeps_temp_name_with_warning(tmp_path):
    """os.rename 抛 OSError → 保留临时名照常交付 + warning，不阻断。"""
    from unittest.mock import patch

    with patch(
        "openbrep.workbench.project_session_service.os.rename",
        side_effect=OSError("permission denied"),
    ):
        session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="坐斗"))
        response = session.route(
            "POST",
            "/api/project/create",
            {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
        )

    assert response["ok"] is True, response
    assert response["project"]["name"] == "临时构件"
    assert (tmp_path / "临时构件").is_dir()
    assert session.source_path == (tmp_path / "临时构件").resolve()
    assert "warnings" in response
    assert any("改名失败" in w for w in response["warnings"])


def test_p7b_create_renames_gsm_artifact_with_dir(tmp_path):
    """编译产物 <临时名>.gsm 存在 → 随目录一起改名。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="坐斗", gsm_artifact=True))
    response = session.route(
        "POST",
        "/api/project/create",
        {"prompt": "生成一个临时构件", "output_dir": str(tmp_path)},
    )
    assert response["ok"] is True, response
    assert (tmp_path / "坐斗.gsm").is_file()
    assert not (tmp_path / "临时构件.gsm").exists()
    assert not [w for w in response["warnings"] if "改名" in w]


def test_p7b_create_explicit_project_name_wins_over_object_type(tmp_path):
    """显式 project_name 优先于 object_type（监控方补）：不 rename。"""
    session = WorkbenchSession(pipeline_class=_rename_pipeline_cls(object_type="坐斗"))
    response = session.route(
        "POST",
        "/api/project/create",
        {
            "prompt": "生成一个临时构件",
            "project_name": "我的书架",
            "output_dir": str(tmp_path),
        },
    )
    assert response["ok"] is True, response
    assert response["project"]["name"] == "我的书架"
    assert (tmp_path / "我的书架").is_dir()
    assert not (tmp_path / "坐斗").exists()
    assert session.source_path == (tmp_path / "我的书架").resolve()


# ── Codex BYOA（D1）：登录/状态/动态模型路由 + 秘密不泄漏 ───────────────────

class _RouteFakeCodexClient:
    """app-server 假客户端：会话级集成测试用，无网络。"""

    def __init__(self, login_type="chatgpt", rate_limits=None):
        self.started = False
        self.account = None
        self.login_calls = 0
        self.logout_calls = 0
        self.model_list_calls = 0
        self.cancel_calls = 0
        self.rate_limits_calls = 0
        self.login_type = login_type
        self.rate_limits = rate_limits
        self.closed = False

    def start(self):
        self.started = True

    def initialize(self):
        return {}

    def account_read(self):
        return {"account": self.account, "requiresOpenaiAuth": self.account is None}

    def account_login_start_chatgpt(self):
        self.login_calls += 1
        return {"type": "chatgpt", "loginId": "route-login-id", "authUrl": "https://auth.openai.com/route"}

    def account_login_start(self, login_type):
        self.login_calls += 1
        if login_type == "chatgptDeviceCode":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "route-login-id",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }
        return {"type": "chatgpt", "loginId": "route-login-id", "authUrl": "https://auth.openai.com/route"}

    def account_login_cancel(self, login_id):
        self.cancel_calls += 1
        return {"status": "canceled"}

    def account_logout(self):
        self.logout_calls += 1
        self.account = None
        return {}

    def account_rate_limits_read(self):
        self.rate_limits_calls += 1
        if self.rate_limits is None:
            raise RuntimeError("rate limits unavailable")
        return self.rate_limits

    def model_list(self):
        self.model_list_calls += 1
        return {
            "data": [
                {
                    "id": "gpt-5.6-luna", "model": "gpt-5.6-luna",
                    "displayName": "GPT-5.6 Luna", "hidden": False, "modelSpecialty": None,
                    # D6：effort 目录
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fastest"},
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                    "defaultReasoningEffort": "medium",
                },
                {
                    "id": "gpt-5.2", "model": "gpt-5.2",
                    "displayName": "GPT-5.2", "hidden": False, "modelSpecialty": None,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium", "description": "Balanced"},
                    ],
                    "defaultReasoningEffort": "medium",
                },
            ],
            "nextCursor": None,
        }

    def close(self):
        self.started = False
        self.closed = True


def _route_codex_session(tmp_path, client, opened=None):
    from openbrep.codex.provider import CodexProvider

    opened = opened if opened is not None else []

    def factory():
        return CodexProvider(
            codex_home=tmp_path / "codex-home",
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=opened.append,
        )

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.settings_service.codex_provider_factory = factory
    return session, opened


def test_codex_status_route_three_states_distinguishable(tmp_path):
    """无 CLI / 未登录 / 已登录三种状态经 API 可区分。"""
    # 已登录（chatgpt 账号）
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "johndoe@example.com", "planType": "pro"}
    session, _opened = _route_codex_session(tmp_path, client)
    signed_in = session.route("GET", "/api/settings/llm/codex/status")
    assert signed_in["ok"] is True
    assert signed_in["state"] == "signed_in"
    assert signed_in["connected"] is True
    assert signed_in["account"]["email_masked"] == "jo***@example.com"
    assert signed_in["account"]["plan_type"] == "pro"
    assert "email" not in signed_in["account"]

    # 未登录
    client2 = _RouteFakeCodexClient()
    session2, _opened2 = _route_codex_session(tmp_path, client2)
    signed_out = session2.route("GET", "/api/settings/llm/codex/status")
    assert signed_out["state"] == "signed_out"
    assert signed_out["connected"] is False

    # 无 CLI
    from openbrep.codex.provider import CodexProvider

    session3 = WorkbenchSession(config_path=tmp_path / "config.toml")
    session3.settings_service.codex_provider_factory = lambda: CodexProvider(
        codex_home=tmp_path / "codex-home3", cli_available=False,
    )
    no_cli = session3.route("GET", "/api/settings/llm/codex/status")
    assert no_cli["state"] == "no_cli"
    assert no_cli["connected"] is False
    assert no_cli["codex_available"] is False


def test_codex_login_start_route_opens_browser_and_returns_state_only(tmp_path):
    client = _RouteFakeCodexClient()
    session, opened = _route_codex_session(tmp_path, client)
    response = session.route("POST", "/api/settings/llm/codex/login/start", {})
    assert response == {"ok": True, "state": "login_started", "method": "chatgpt"}
    assert client.login_calls == 1
    assert len(opened) == 1
    assert opened[0].startswith("https://auth.openai.com/")
    # authUrl/loginId 不出现在响应
    assert "authUrl" not in response
    assert "loginId" not in response


def test_codex_logout_route(tmp_path):
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("POST", "/api/settings/llm/codex/logout", {})
    assert response == {"ok": True, "state": "signed_out"}
    assert client.logout_calls == 1
    # 退出后模型列表不可读（fail closed）
    models = session.route("GET", "/api/settings/llm/codex/models")
    assert models["ok"] is False
    assert models["code"] == "not_signed_in"


def test_codex_models_route_returns_qualified_ids(tmp_path):
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("GET", "/api/settings/llm/codex/models")
    assert response["ok"] is True
    ids = [m["id"] for m in response["models"]]
    assert ids == ["openai-codex/gpt-5.6-luna", "openai-codex/gpt-5.2"]
    # 模型目录来自 model/list，不是硬编码清单
    assert client.model_list_calls == 1
    # D6：effort 目录透出（UI 选项只来自 model/list.supportedReasoningEfforts）
    luna = next(m for m in response["models"] if m["id"].endswith("gpt-5.6-luna"))
    assert [e["effort"] for e in luna["supported_reasoning_efforts"]] == ["low", "medium", "high"]
    assert luna["default_reasoning_effort"] == "medium"


def test_update_llm_model_route_saves_codex_effort(tmp_path):
    """D6：PATCH /api/settings/llm/model 显式保存 model + effort。"""
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("PATCH", "/api/settings/llm/model", {
        "model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "high",
    })
    assert response["ok"] is True
    assert response["llm"]["model"] == "openai-codex/gpt-5.6-luna"
    assert response["llm"]["reasoning_effort"] == "high"
    # 落盘：重新加载 config 可见
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.reasoning_effort == "high"


def test_update_llm_model_route_saves_codex_auto_only_when_explicit(tmp_path):
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    assert session.snapshot()["llm"]["codex_routing_mode"] == "fixed"

    response = session.route("PATCH", "/api/settings/llm/model", {
        "model": "openai-codex/gpt-5.6-luna",
        "reasoning_effort": "low",
        "codex_routing_mode": "auto",
    })
    assert response["ok"] is True
    assert response["llm"]["codex_routing_mode"] == "auto"
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.effective_codex_routing_mode() == "auto"


def test_update_llm_model_route_rejects_unsupported_effort(tmp_path):
    """D6：effort 注入对抗——model/list 不支持的 effort 保存请求显式报错。"""
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    # gpt-5.2 只支持 medium；注入 low → 拒绝（effort_not_supported）
    response = session.route("PATCH", "/api/settings/llm/model", {
        "model": "openai-codex/gpt-5.2", "reasoning_effort": "low",
    })
    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    assert "low" in response["error"]
    # 配置未落盘
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.reasoning_effort == ""


def test_codex_routes_registered_lock_free():
    from openbrep.workbench.request_gate import LOCK_FREE_POST_ROUTES

    assert "/api/settings/llm/codex/login/start" in LOCK_FREE_POST_ROUTES
    assert "/api/settings/llm/codex/logout" in LOCK_FREE_POST_ROUTES


def test_snapshot_llm_codex_block_and_no_secrets(tmp_path):
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "johndoe@example.com", "planType": "pro"}
    session, _opened = _route_codex_session(tmp_path, client)
    # 先走一次 status 路由（现实流程：AI 设置页打开时创建 provider），
    # 之后 snapshot/llm_settings 复用同一 provider 状态，不重复拉起。
    status = session.route("GET", "/api/settings/llm/codex/status")
    assert status["state"] == "signed_in"
    snapshot = session.snapshot()
    assert snapshot["llm"]["codex"]["state"] == "signed_in"
    assert snapshot["llm"]["codex"]["connected"] is True
    # snapshot 不得包含秘密字段
    text = json.dumps(snapshot)
    for bad in ("authUrl", "loginId", "auth.json", ".codex", "johndoe@example.com", "fake-login-id"):
        assert bad not in text, f"snapshot 泄露 {bad}"
    assert "jo***@example.com" in text  # 脱敏邮箱允许出现


def test_codex_model_save_via_route_persists_provider_entry(tmp_path):
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)
    session.route("GET", "/api/settings/llm/codex/status")  # 创建 provider（已登录）
    response = session.route("PATCH", "/api/settings/llm/model", {"model": "openai-codex/gpt-5.6-luna"})
    assert response["ok"] is True
    assert response["llm"]["model"] == "openai-codex/gpt-5.6-luna"
    # 已登录 → 保存后立即可用
    assert response["llm"]["model_available"] is True
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.model == "openai-codex/gpt-5.6-luna"
    assert reloaded.llm.providers[0]["api_mode"] == "codex_app_server"
    # 保存不写任何 codex 凭据（access_token/authUrl/loginId/auth.json/sk- 等）
    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    for bad in ("auth.json", "access_token", "authUrl", "loginId", "sk-", "eyJ", "chatgpt_account_id"):
        assert bad not in saved, f"config.toml 写入秘密字段 {bad}"


def test_codex_api_key_route_rejects_codex_model(tmp_path):
    """P0-1：通用 API-key 通道对 openai-codex 订阅身份必须拒绝（后端独立）。"""
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)

    response = session.route(
        "POST",
        "/api/settings/llm/api-key",
        {"model": "openai-codex/gpt-5.6-luna", "api_key": "DEV-SECRET"},
    )
    assert response["ok"] is False
    assert response["code"] == "codex_no_api_key"
    # 拒绝后 config 不落盘（文件不存在），更不会有 DEV-SECRET
    assert not (tmp_path / "config.toml").exists()


def test_codex_model_save_route_rejects_model_not_in_catalog(tmp_path):
    """P0-4：保存 openai-codex 模型必须属于当前账户 model/list 目录。"""
    client = _RouteFakeCodexClient()
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
    session, _opened = _route_codex_session(tmp_path, client)

    response = session.route(
        "PATCH", "/api/settings/llm/model", {"model": "openai-codex/not-a-real-account-model"}
    )
    assert response["ok"] is False
    assert response["code"] == "model_not_in_catalog"
    # 未落盘
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.model != "openai-codex/not-a-real-account-model"


def test_codex_login_start_failure_route_redacts_secrets(tmp_path):
    """P0-2：登录失败路径经路由返回的错误必须脱敏（不含 authUrl/loginId 值）。"""
    client = _RouteFakeCodexClient()

    def _boom():
        raise RuntimeError(
            'login failed: {"authUrl":"https://auth.openai.com/oauth?state=SECRET",'
            '"loginId":"a0327bbe-a894-4455-9e96-8c6d19ed2a53"}'
        )

    client.account_login_start = _boom
    session, _opened = _route_codex_session(tmp_path, client)

    response = session.route("POST", "/api/settings/llm/codex/login/start", {})
    assert response["ok"] is False
    text = str(response["error"])
    assert "auth.openai.com" not in text
    assert "a0327bbe" not in text
    assert "SECRET" not in text
    assert "authUrl" not in text
    assert "loginId" not in text


def test_codex_config_migrates_reserved_provider_entry(tmp_path):
    """P0-3：配置里同名的恶意 openai-codex 条目在加载时被强制规范。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
model = "openai-codex/gpt-5.6-luna"

[[llm.providers]]
name = "openai-codex"
api_mode = "chat_completions"
api = "https://evil.invalid"
api_key = "DEV-SECRET"
models = ["gpt-5.6-luna"]
""",
        encoding="utf-8",
    )
    session = WorkbenchSession(config_path=config_path)
    entry = session.config.llm.providers[0]
    assert entry["api_mode"] == "codex_app_server"
    assert entry["api_key"] == ""
    assert entry["api"] == ""
    assert entry["models"] == []
    # 保存后写回的是规范形态
    session.route("GET", "/api/settings/runtime")
    session.config.save(str(config_path))
    saved = config_path.read_text(encoding="utf-8")
    assert "DEV-SECRET" not in saved
    assert 'api_mode = "codex_app_server"' in saved


# ── D2：device-code / 取消 / 额度 / 重启 路由 + 锁分类 ───────────────────────


def test_codex_device_code_route_explicit(tmp_path):
    client = _RouteFakeCodexClient(login_type="chatgptDeviceCode")
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("POST", "/api/settings/llm/codex/login/device-code", {})
    assert response["ok"] is True
    assert response["state"] == "login_started"
    assert response["method"] == "chatgptDeviceCode"
    assert response["verification_url"] == "https://example.test/device"
    assert response["user_code"] == "ABCD-EFGH"
    # loginId 绝不出现在响应
    assert "loginId" not in response


def test_codex_login_cancel_route(tmp_path):
    client = _RouteFakeCodexClient()
    session, _opened = _route_codex_session(tmp_path, client)
    # 先开始登录，再取消
    session.route("POST", "/api/settings/llm/codex/login/start", {})
    response = session.route("POST", "/api/settings/llm/codex/login/cancel", {})
    assert response == {"ok": True, "state": "signed_out"}
    assert client.cancel_calls == 1


def test_codex_rate_limits_route_masked(tmp_path):
    rl = {
        "rateLimits": {
            "limitId": "codex",
            "limitName": "Codex",
            "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 1786800000},
            "credits": {"hasCredits": True, "unlimited": False, "balance": "123.45"},
            "spendControlReached": False,
            "planType": "pro",
            "rateLimitReachedType": None,
        },
        "rateLimitsByLimitId": {"codex": {}},
        "rateLimitResetCredits": None,
    }
    client = _RouteFakeCodexClient(rate_limits=rl)
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("GET", "/api/settings/llm/codex/rate-limits")
    assert response["ok"] is True
    assert response["rate_limits"]["used_percent"] == 12
    assert response["rate_limits"]["reached"] is False
    text = str(response)
    for bad in ("123.45", "balance", "limitName", "limitId"):
        assert bad not in text, f"额度泄漏 {bad}"


def test_codex_restart_route(tmp_path):
    client = _RouteFakeCodexClient()
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("POST", "/api/settings/llm/codex/restart", {})
    assert response["ok"] is True
    assert response["state"] == "signed_out"


def test_codex_crashed_status_route(tmp_path):
    """app-server 崩溃 → API 返回 crashed 状态 + restartable。"""

    class _CrashTransport:
        crashed = True
        crash_exit_code = 42

    class _CrashClient(_RouteFakeCodexClient):
        @property
        def transport(self):
            return _CrashTransport()

    session, _opened = _route_codex_session(tmp_path, _CrashClient())
    response = session.route("GET", "/api/settings/llm/codex/status")
    assert response["ok"] is True
    assert response["state"] == "crashed"
    assert response["restartable"] is True
    # 可操作信息、无内部细节
    assert "重启" in response["error"]


def test_codex_quota_exhausted_status_route(tmp_path):
    """额度耗尽 → API 状态 quota_exhausted + 脱敏额度摘要。"""
    rl = {
        "rateLimits": {
            "primary": {"usedPercent": 100, "windowDurationMins": 360, "resetsAt": 1786800000},
            "credits": {"hasCredits": False, "unlimited": False},
            "spendControlReached": False,
            "planType": "pro",
            "rateLimitReachedType": "rate_limit_reached",
        },
        "rateLimitsByLimitId": None,
        "rateLimitResetCredits": None,
    }
    client = _RouteFakeCodexClient(rate_limits=rl)
    client.account = {"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
    session, _opened = _route_codex_session(tmp_path, client)
    response = session.route("GET", "/api/settings/llm/codex/status")
    assert response["ok"] is True
    assert response["state"] == "quota_exhausted"
    assert response["connected"] is True
    assert response["rate_limits"]["reached"] is True
    text = str(response)
    for bad in ("balance", "resetCredit", "grantedAt"):
        assert bad not in text, f"泄漏 {bad}"


def test_codex_d2_routes_registered_lock_free(tmp_path):
    from openbrep.workbench.request_gate import LOCK_FREE_POST_ROUTES

    for route in (
        "/api/settings/llm/codex/login/start",
        "/api/settings/llm/codex/login/device-code",
        "/api/settings/llm/codex/login/cancel",
        "/api/settings/llm/codex/logout",
        "/api/settings/llm/codex/restart",
    ):
        assert route in LOCK_FREE_POST_ROUTES, route
    # 未知 codex 路由返回明确错误（不静默）
    session, _opened = _route_codex_session(tmp_path, _RouteFakeCodexClient())
    response = session.route("POST", "/api/settings/llm/codex/nope", {})
    assert response["ok"] is False


# ── D3：Codex 模型 CHAT/EXPLAIN 经 workbench API ──────────────────────────


class _RouteCodexChatProvider:
    """支持 chat() 的 CodexProvider 替身（settings service 注入用）。"""

    def __init__(self, content="Codex API 回复。"):
        self.content = content
        self.chat_calls = 0
        self.signed_in = True

    def chat(self, messages, model, **kwargs):
        self.chat_calls += 1
        on_event = kwargs.get("on_event")
        if on_event is not None:
            on_event("status", {"stage": "codex", "message": "Codex 对话已开始"})
            on_event("assistant_delta", {"content": self.content})
            on_event("status", {"stage": "codex", "message": "Codex 对话完成"})
        from openbrep.codex.turn import CodexTurnResult

        return CodexTurnResult(content=self.content, model=model, finish_reason="stop")


def _codex_chat_session(tmp_path, provider=None):
    """带 codex 模型配置 + fake codex provider 的 WorkbenchSession。"""
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.providers = [
        {
            "name": "openai-codex",
            "api_mode": "codex_app_server",
            "api_key": "",
            "models": [],
        }
    ]
    cfg_path = tmp_path / "config.toml"
    config.save(str(cfg_path))
    session = WorkbenchSession(config_path=cfg_path)
    provider = provider or _RouteCodexChatProvider()
    session.settings_service.codex_provider = provider
    return session, provider


def test_assistant_codex_chat_no_project_no_crash(tmp_path):
    """无项目 + Codex 模型：/api/assistant 返回 Codex 回复，不创建任何目录。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    before = sorted(p.name for p in workspace.iterdir())
    session, provider = _codex_chat_session(tmp_path)

    response = session.route("POST", "/api/assistant", {"message": "你好"})

    assert response["ok"] is True
    assert response["assistant"]["kind"] == "chat"
    assert response["assistant"]["reply"] == "Codex API 回复。"
    assert provider.chat_calls == 1
    after = sorted(p.name for p in workspace.iterdir())
    assert before == after
    assert not list(workspace.rglob("*"))


def test_assistant_codex_explain_with_project_no_revision(tmp_path):
    """有项目 + Codex 模型：EXPLAIN 走 Codex，revision 数不变。"""
    project = HSFProject.create_new("ApiExplain", str(tmp_path / "proj"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()
    revisions_dir = Path(hsf_dir) / ".openbrep" / "revisions"
    before = len(list(revisions_dir.iterdir())) if revisions_dir.exists() else 0

    session, provider = _codex_chat_session(tmp_path)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})
    response = session.route("POST", "/api/assistant", {"message": "解释一下这个构件"})

    assert response["ok"] is True
    assert provider.chat_calls == 1
    after = len(list(revisions_dir.iterdir())) if revisions_dir.exists() else 0
    assert before == after, "EXPLAIN 不得创建 revision"
    # 项目未被修改（UTF-8 BOM 是 HSF 保存的既有行为，去掉后再比较）
    saved = Path(hsf_dir, "scripts", "3d.gdl").read_text(encoding="utf-8").lstrip("\ufeff").strip()
    assert saved == "BLOCK A, B, ZZYZX"


def test_assistant_non_codex_no_project_graceful_error(tmp_path):
    """非 Codex 模型 + 无项目：本地解释器给可操作提示（不再 500）。"""
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route("POST", "/api/assistant", {"message": "你好"})
    assert response["ok"] is False
    assert "项目" in response["error"]


def test_assistant_generate_stream_chat_codex_emits_events(tmp_path):
    """/api/assistant/generate stream=1 + intent=CHAT + Codex：SSE 事件流。"""
    project = HSFProject.create_new("StreamExplain", str(tmp_path / "proj"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    hsf_dir = project.save_to_disk()

    session, provider = _codex_chat_session(tmp_path)
    session.route("POST", "/api/project/load", {"path": str(hsf_dir)})

    events = list(
        session.route(
            "POST",
            "/api/assistant/generate",
            {
                "message": "解释一下这个构件",
                "intent": "CHAT",
                "stream": True,
            },
        )
    )
    types = [e["type"] for e in events]
    assert "status" in types or "assistant_delta" in types
    done = [e for e in events if e["type"] == "done"]
    assert done, "流式 CHAT 必须以 done 结束"
    assert done[0]["data"]["ok"] is True
    assert done[0]["data"]["assistant"]["reply"] == "Codex API 回复。"
    assert provider.chat_calls >= 1


# ── D11：Codex MODIFY API 路由与移除 flag 回归 ─────────────

def _codex_modify_config_session(tmp_path) -> WorkbenchSession:
    """构造选中 openai-codex 模型的 workbench 会话。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join([
            "[llm]",
            'model = "openai-codex/gpt-5.6-luna"',
            "",
            "[[llm.providers]]",
            'name = "openai-codex"',
            'api_mode = "codex_app_server"',
            'api_key = ""',
            "models = []",
        ]),
        encoding="utf-8",
    )
    return WorkbenchSession(config_path=config_path)


def test_codex_modify_api_reaches_pipeline_without_flag(tmp_path):
    """Codex MODIFY 不再被 API flag 门禁拦截。"""
    calls = []

    class _StubPipeline:
        def __init__(self, trace_dir=None, **kwargs):
            self.config = GDLAgentConfig()

        def execute(self, request):
            calls.append(request.intent)
            return TaskResult(
                success=True, intent=request.intent or "MODIFY",
                plain_text="stub-ok", project=None,
            )

    session = _codex_modify_config_session(tmp_path)
    session.pipeline_class = _StubPipeline
    proj = HSFProject.create_new("Shelf", str(tmp_path / "Shelf")).save_to_disk()
    session.route("POST", "/api/project/load", {"path": str(proj)})
    response = session.route("POST", "/api/assistant/generate", {
        "message": "给书架加一层层板",
        "intent": "MODIFY",
    })
    assert response["ok"] is True
    assert calls == ["MODIFY"]


def test_settings_route_does_not_expose_removed_codex_modify_flag(tmp_path):
    snapshot = _codex_modify_config_session(tmp_path).route("GET", "/api/snapshot", {})
    assert snapshot["ok"] is True
    assert "codex_modify_enabled" not in snapshot["llm"]
