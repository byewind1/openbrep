from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from openbrep.contracts.stair import evaluate_stair_contract
from openbrep.gdl_previewer import evaluate_parameter_environment, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.project_parameter_service import parameter_values

FIXTURE = Path(__file__).parent / "fixtures" / "spiral_stair" / "after_top_option"


def _copy_project(tmp_path: Path) -> HSFProject:
    root = tmp_path / "stair"
    shutil.copytree(FIXTURE, root)
    return HSFProject.load_from_disk(str(root))


def _contract(*, geometry: bool = False) -> dict:
    contract = {
        "schema_version": 1,
        "type": "spiral_stair",
        "parameter_bindings": {
            "height": "height",
            "num_steps": "num_steps",
            "step_riser": "step_riser",
            "show_top_tread": "show_top_tread",
            "handrail_height": "handrail_height",
            "pole_radius": "pole_radius",
            "tread_outer_radius": "tread_outer_radius",
            "tread_inner_radius": "_tread_inner_radius",
            "rail_center_radius": "_rail_center_radius",
        },
        "constraints": {
            "step_riser_range": {
                "min": 0.10,
                "max": 0.25,
                "source": "test_profile",
                "blocking": True,
            },
            "handrail_height_range": {
                "min": 0.8,
                "max": 1.2,
                "source": "test_profile",
                "blocking": True,
            },
            "rail_center_inset": {
                "expected": 0.025,
                "tolerance": 1e-9,
                "source": "project_choice",
                "blocking": True,
            },
            "tread_pole_overlap": {
                "expected": 0.05,
                "tolerance": 1e-9,
                "source": "project_choice",
                "blocking": True,
            },
        },
        "provenance": {"source": "ST07 acceptance fixture"},
    }
    if geometry:
        contract["geometry_bindings"] = {
            "treads": {"command": "PRISM_", "source_line": 48},
            "balusters": {"command": "CYLIND", "source_line": 65},
        }
    return contract


def _write_contract(project: HSFProject, payload: dict) -> Path:
    path = project.root / ".openbrep" / "contracts" / "stair.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def _evaluate(project: HSFProject):
    master = project.get_script(ScriptType.MASTER) or ""
    evaluated = evaluate_parameter_environment(master, parameter_values(project))
    preview = preview_3d_script(
        project.get_script(ScriptType.SCRIPT_3D) or "",
        parameters=evaluated.runtime_values,
        unknown_command_policy="warn",
        quality="fast",
    )
    return evaluate_stair_contract(project, evaluated=evaluated, preview=preview)


def _check(report, check_id: str):
    return next(check for check in report.checks if check.check_id == check_id)


def _set_parameter(project: HSFProject, name: str, value: float) -> None:
    parameter = next(param for param in project.parameters if param.name == name)
    parameter.value = str(value)


def test_no_contract_is_not_applicable_and_does_not_change_source(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    before = {
        path.relative_to(project.root): path.read_bytes()
        for path in project.root.rglob("*")
        if path.is_file()
    }

    report = _evaluate(project)

    after = {
        path.relative_to(project.root): path.read_bytes()
        for path in project.root.rglob("*")
        if path.is_file()
    }
    assert report.applicability == "not_applicable"
    assert report.checks == []
    assert before == after


def test_default_fixture_passes_value_relations_but_unbound_geometry_is_unknown(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    _write_contract(project, _contract())

    report = _evaluate(project)

    assert report.applicability == "applicable"
    assert _check(report, "positive_integer_steps").status == "pass"
    assert _check(report, "step_riser_relation").observed == pytest.approx(0.18125)
    assert _check(report, "step_riser_relation").status == "pass"
    assert _check(report, "rail_center_inset").status == "pass"
    assert _check(report, "tread_pole_overlap").status == "pass"
    assert _check(report, "top_tread_count").status == "unknown"
    assert _check(report, "baluster_tread_alignment").status == "unknown"
    assert _check(report, "headroom").status == "unknown"


@pytest.mark.parametrize(
    ("show_top_tread", "expected_count", "expected_top"),
    [(1, 16, 2.9), (0, 15, 2.71875)],
)
def test_top_tread_switch_controls_count_and_top_elevation(
    tmp_path: Path,
    show_top_tread: int,
    expected_count: int,
    expected_top: float,
) -> None:
    project = _copy_project(tmp_path)
    _set_parameter(project, "show_top_tread", show_top_tread)
    _write_contract(project, _contract(geometry=True))

    report = _evaluate(project)

    count = _check(report, "top_tread_count")
    elevation = _check(report, "top_tread_elevation")
    assert count.status == "pass"
    assert count.observed == expected_count
    assert elevation.status == "pass"
    assert elevation.observed == pytest.approx(expected_top)


@pytest.mark.parametrize(
    ("height", "steps", "expected_riser", "expected_rail"),
    [(5.0, 4, 1.25, 6.625), (2.9, 30, 2.9 / 30.0, (2.9 / 30.0) * 5.3)],
)
def test_project_ranges_fail_on_extreme_derived_values(
    tmp_path: Path,
    height: float,
    steps: int,
    expected_riser: float,
    expected_rail: float,
) -> None:
    project = _copy_project(tmp_path)
    _set_parameter(project, "height", height)
    _set_parameter(project, "num_steps", steps)
    _write_contract(project, _contract())

    report = _evaluate(project)

    riser = _check(report, "step_riser_range")
    rail = _check(report, "handrail_height_range")
    assert riser.status == "fail"
    assert riser.observed == pytest.approx(expected_riser)
    assert rail.status == "fail"
    assert rail.observed == pytest.approx(expected_rail)
    assert not report.passed


def test_geometry_relationships_fail_when_balusters_shift_and_inset_is_removed(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    script = project.get_script(ScriptType.SCRIPT_3D) or ""
    project.scripts[ScriptType.SCRIPT_3D] = script.replace(
        "(i + 1) * step_riser ! Fixed: place baluster bases",
        "(i + 1) * step_riser + 0.02 ! shifted test fixture; place baluster bases",
    )
    master = project.get_script(ScriptType.MASTER) or ""
    project.scripts[ScriptType.MASTER] = master.replace(
        "tread_outer_radius - 0.025",
        "tread_outer_radius - 0",
    )
    _write_contract(project, _contract(geometry=True))

    report = _evaluate(project)

    assert _check(report, "baluster_tread_alignment").status == "fail"
    assert _check(report, "rail_center_inset").status == "fail"
    assert not report.passed


def test_wrong_top_tread_geometry_fails_even_when_script_still_previews(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    script = project.get_script(ScriptType.SCRIPT_3D) or ""
    project.scripts[ScriptType.SCRIPT_3D] = script.replace(
        "FOR i = 0 TO _tread_count - 1",
        "FOR i = 0 TO _tread_count - 2",
        1,
    )
    _write_contract(project, _contract(geometry=True))

    report = _evaluate(project)

    assert _check(report, "top_tread_count").status == "fail"
    assert _check(report, "top_tread_elevation").status == "fail"
    assert not report.passed


def test_missing_binding_is_unknown_and_malformed_contract_is_blocking(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    payload = _contract()
    del payload["parameter_bindings"]["height"]
    path = _write_contract(project, payload)

    missing = _evaluate(project)
    assert _check(missing, "step_riser_relation").status == "unknown"
    assert missing.passed

    path.write_text("{not-json", encoding="utf-8")
    malformed = _evaluate(project)
    assert malformed.applicability == "invalid"
    assert _check(malformed, "contract_valid").status == "fail"
    assert not malformed.passed


def test_unsupported_master_statement_makes_dependent_checks_unknown(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    master = project.get_script(ScriptType.MASTER) or ""
    project.scripts[ScriptType.MASTER] = "FOOBAR 1\n" + master
    _write_contract(project, _contract(geometry=True))

    report = _evaluate(project)

    assert _check(report, "step_riser_relation").status == "unknown"
    assert _check(report, "top_tread_count").status == "unknown"
    assert report.passed


def test_stale_geometry_binding_is_unknown_instead_of_false_failure(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    payload = _contract(geometry=True)
    payload["geometry_bindings"]["treads"]["source_line"] = 999
    _write_contract(project, payload)

    report = _evaluate(project)

    assert _check(report, "top_tread_count").status == "unknown"
    assert _check(report, "top_tread_elevation").status == "unknown"
    assert report.passed


def test_contract_requires_provenance(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    payload = _contract()
    del payload["provenance"]
    _write_contract(project, payload)

    report = _evaluate(project)

    assert report.applicability == "invalid"
    assert _check(report, "contract_valid").status == "fail"


def test_contract_hash_changes_when_only_contract_content_changes(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    payload = _contract()
    _write_contract(project, payload)
    first = _evaluate(project)

    payload["constraints"]["step_riser_range"]["max"] = 0.26
    _write_contract(project, payload)
    second = _evaluate(project)

    assert first.contract_hash.startswith("sha256:")
    assert second.contract_hash.startswith("sha256:")
    assert first.contract_hash != second.contract_hash
