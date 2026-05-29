from __future__ import annotations

from pathlib import Path

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.paramlist_builder import parse_paramlist_xml
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
