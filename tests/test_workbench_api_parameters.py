from __future__ import annotations

import shutil
from pathlib import Path

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.paramlist_builder import parse_paramlist_xml
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench_api import WorkbenchSession


def make_loaded_session(tmp_path: Path) -> WorkbenchSession:
    project = HSFProject.create_new("Chair", str(tmp_path))
    project.save_to_disk()
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    result = session.load_hsf_directory(str(project.root))
    assert result["ok"] is True
    return session


def test_add_parameter_success_persists_paramlist_xml(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters",
        {
            "name": "seat_height",
            "type_tag": "Length",
            "value": "0.45",
            "description": "Seat height",
        },
    )

    assert result["ok"] is True
    assert result["source_fingerprint"] == compute_source_fingerprint(session.project.root)
    assert result["added"]["name"] == "seat_height"
    assert any(param["name"] == "seat_height" for param in result["parameters"])
    assert session.source_path is not None
    paramlist_path = session.source_path / "paramlist.xml"
    content = paramlist_path.read_text(encoding="utf-8-sig")
    assert 'Name="seat_height"' in content
    parsed = parse_paramlist_xml(content)
    assert any(param.name == "seat_height" and param.value == "0.45" for param in parsed)


def test_add_parameter_rejects_duplicate_name(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters",
        {"name": "A", "type_tag": "Length", "value": "2.0"},
    )

    assert result["ok"] is False
    assert "already exists" in result["error"]


def test_add_parameter_rejects_invalid_name(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters",
        {"name": "bad-name", "type_tag": "Length", "value": "1.0"},
    )

    assert result["ok"] is False
    assert "Invalid parameter name" in result["error"]


def test_add_parameter_rejects_invalid_type(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters",
        {"name": "seat_height", "type_tag": "Float", "value": "0.45"},
    )

    assert result["ok"] is False
    assert "Unsupported parameter type" in result["error"]


def test_add_parameter_rejects_empty_name(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters",
        {"name": "", "type_tag": "String", "value": "Label"},
    )

    assert result["ok"] is False
    assert "Parameter name is required" in result["error"]


def test_validate_parameters_returns_paramlist_issues(tmp_path):
    session = make_loaded_session(tmp_path)

    session.project.add_parameter(GDLParameter(name="width_mm", type_tag="Length", value="1.0"))

    result = session.route("POST", "/api/project/parameters/validate", {})

    assert result["ok"] is True
    assert any("width_mm" in issue for issue in result["issues"])


def test_update_parameter_renames_and_persists_metadata(tmp_path):
    session = make_loaded_session(tmp_path)
    session.route(
        "POST",
        "/api/project/parameters",
        {"name": "seat_height", "type_tag": "Length", "value": "0.45"},
    )

    result = session.route(
        "POST",
        "/api/project/parameters/update",
        {
            "name": "seat_height",
            "new_name": "chair_seat_height",
            "type_tag": "RealNum",
            "value": "0.5",
            "description": "Chair seat height",
        },
    )

    assert result["ok"] is True
    assert result["updated"]["name"] == "chair_seat_height"
    assert result["updated"]["type_tag"] == "RealNum"
    assert result["updated"]["value"] == "0.5"
    assert session.source_path is not None
    content = (session.source_path / "paramlist.xml").read_text(encoding="utf-8-sig")
    assert 'Name="chair_seat_height"' in content
    assert 'Name="seat_height"' not in content


def test_update_parameter_rejects_duplicate_target_name(tmp_path):
    session = make_loaded_session(tmp_path)
    session.route(
        "POST",
        "/api/project/parameters",
        {"name": "seat_height", "type_tag": "Length", "value": "0.45"},
    )

    result = session.route(
        "POST",
        "/api/project/parameters/update",
        {"name": "seat_height", "new_name": "A"},
    )

    assert result["ok"] is False
    assert "already exists" in result["error"]


def test_update_fixed_parameter_rejects_rename(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route(
        "POST",
        "/api/project/parameters/update",
        {"name": "A", "new_name": "width"},
    )

    assert result["ok"] is False
    assert "cannot be renamed" in result["error"]


def test_delete_parameter_removes_non_fixed_parameter(tmp_path):
    session = make_loaded_session(tmp_path)
    session.route(
        "POST",
        "/api/project/parameters",
        {"name": "seat_height", "type_tag": "Length", "value": "0.45"},
    )

    result = session.route(
        "POST",
        "/api/project/parameters/delete",
        {"name": "seat_height"},
    )

    assert result["ok"] is True
    assert result["deleted"] == "seat_height"
    assert all(param["name"] != "seat_height" for param in result["parameters"])


def test_delete_parameter_rejects_fixed_parameter(tmp_path):
    session = make_loaded_session(tmp_path)

    result = session.route("POST", "/api/project/parameters/delete", {"name": "A"})

    assert result["ok"] is False
    assert "cannot be deleted" in result["error"]


def test_effective_parameters_get_and_draft_post_are_read_only(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "spiral_stair" / "after_top_option"
    project_root = tmp_path / "Spiral"
    shutil.copytree(fixture, project_root)
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    assert session.load_hsf_directory(str(project_root))["ok"] is True
    before = {
        path.relative_to(project_root): path.read_bytes()
        for path in project_root.rglob("*")
        if path.is_file()
    }

    saved = session.route("GET", "/api/project/parameters/effective", {})
    draft = session.route(
        "POST",
        "/api/project/parameters/effective",
        {"parameters": {"height": 3.2}},
    )

    saved_by_name = {item["name"]: item for item in saved["parameters"]}
    draft_by_name = {item["name"]: item for item in draft["parameters"]}
    assert saved["ok"] is True
    assert saved["source_fingerprint"] == compute_source_fingerprint(project_root)
    assert saved["project_epoch"] == session.project_epoch
    assert saved_by_name["step_riser"]["effective_value"] == 0.18125
    assert saved_by_name["pole_radius"]["effective_value"] == 0.1
    assert saved_by_name["handrail_height"]["effective_value"] == 0.960625
    assert saved_by_name["step_riser"]["read_only"] is True
    assert "scripts/vl.gdl:LOCK" in saved_by_name["step_riser"]["sources"]
    assert saved_by_name["height"]["role"] == "unknown"
    assert saved_by_name["height"]["read_only"] is False
    assert draft_by_name["step_riser"]["effective_value"] == 0.2
    assert draft_by_name["step_riser"]["source_value"] == "0.17"
    assert saved_by_name["step_riser"]["source_value"] == "0.17"
    assert saved["source_fingerprint"] == draft["source_fingerprint"]
    assert before == {
        path.relative_to(project_root): path.read_bytes()
        for path in project_root.rglob("*")
        if path.is_file()
    }


def test_effective_parameters_require_an_open_project(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")

    get_result = session.route("GET", "/api/project/parameters/effective", {})
    post_result = session.route(
        "POST", "/api/project/parameters/effective", {"parameters": {"A": 2}}
    )

    assert get_result == {"ok": False, "error": "Create or open a project first."}
    assert post_result == get_result
