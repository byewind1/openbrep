from __future__ import annotations

from pathlib import Path

import pytest

from openbrep.gdl_previewer import evaluate_parameter_environment
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.parameter_observation import (
    classify_parameter_roles,
    observe_parameters,
    select_alternative_value,
)
from openbrep.workbench.project_parameter_service import parameter_values


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "spiral_stair" / "after_top_option"


def test_master_evaluation_returns_real_spiral_stair_values() -> None:
    project = HSFProject.load_from_disk(FIXTURE_ROOT)

    result = evaluate_parameter_environment(
        project.get_script(ScriptType.MASTER) or "",
        parameter_values(project),
    )

    assert result.supported is True
    assert result.values["STEP_RISER"] == pytest.approx(0.18125, abs=1e-9)
    assert result.values["POLE_RADIUS"] == pytest.approx(0.10, abs=1e-9)
    assert result.values["HANDRAIL_HEIGHT"] == pytest.approx(0.960625, abs=1e-9)
    assert result.diagnostics == []


def test_locked_unknown_parameter_is_read_only_but_unlocked_unknown_is_editable() -> None:
    project = HSFProject.load_from_disk(FIXTURE_ROOT)

    observation = observe_parameters(
        project.parameters,
        project.get_script(ScriptType.MASTER) or "",
        parameter_values(project),
        parameter_script=project.get_script(ScriptType.PARAM) or "",
    )
    by_name = {item.name: item for item in observation.parameters}

    assert by_name["step_riser"].role == "unknown"
    assert by_name["step_riser"].read_only is True
    assert "scripts/vl.gdl:LOCK" in by_name["step_riser"].sources
    assert by_name["height"].role == "unknown"
    assert by_name["height"].read_only is False


def test_master_evaluation_reports_unsupported_call_without_guessing() -> None:
    result = evaluate_parameter_environment(
        'CALL "dynamic_part" PARAMETERS width = input_width\nderived = input_width * 2',
        {"input_width": 1.5, "derived": 0.0},
    )

    assert result.supported is False
    assert result.values["derived"] == pytest.approx(3.0)
    assert [(item.code, item.line) for item in result.diagnostics] == [
        ("PARAM_EVAL_UNSUPPORTED_CALL", 1),
    ]


def test_master_evaluation_reports_failed_assignment_and_keeps_source_value() -> None:
    result = evaluate_parameter_environment(
        "derived = MISSING_FUNCTION(input_width)",
        {"input_width": 1.5, "derived": 7.0},
    )

    assert result.supported is False
    assert result.values["derived"] == 7.0
    assert any(item.code == "PARAM_EVAL_FAILED" and item.line == 1 for item in result.diagnostics)


def test_role_analysis_proves_two_hop_unconditional_derivation() -> None:
    analysis = classify_parameter_roles(
        ["diameter", "stair_radius", "pole_radius"],
        "stair_radius = diameter / 2\npole_radius = stair_radius * 0.06",
    )

    assert analysis.roles["diameter"].role == "input"
    assert analysis.roles["stair_radius"].role == "derived"
    assert analysis.roles["pole_radius"].role == "derived"
    assert analysis.roles["pole_radius"].depends_on == ("diameter",)


def test_role_analysis_keeps_conditional_and_cycle_unknown() -> None:
    analysis = classify_parameter_roles(
        ["input_value", "conditional_value", "left", "right"],
        "IF input_value > 0 THEN conditional_value = input_value\n"
        "left = right + 1\nright = left + 1",
    )

    assert analysis.roles["conditional_value"].role == "unknown"
    assert analysis.roles["left"].role == "unknown"
    assert analysis.roles["right"].role == "unknown"
    assert analysis.roles["conditional_value"].reason == "conditional_assignment"


def test_role_analysis_marks_dynamic_macro_unknown() -> None:
    analysis = classify_parameter_roles(
        ["width", "height"],
        'CALL "dynamic_part" PARAMETERS width = height',
    )

    assert analysis.roles["width"].role == "unknown"
    assert analysis.roles["width"].reason == "dynamic_macro"


def test_alternative_value_respects_boolean_integer_and_range_boundaries() -> None:
    assert select_alternative_value(1, "Boolean").value == 0
    assert select_alternative_value(0, "Boolean").value == 1
    assert select_alternative_value(1, "Integer", value_range=[1, 2]).value == 2
    assert select_alternative_value(2, "Integer", value_range=[1, 2]).value == 1

    fixed = select_alternative_value(2.0, "Length", value_range=[2.0, 2.0])
    assert fixed.value is None
    assert fixed.reason == "no_legal_alternative"


def test_alternative_value_uses_requested_delta_when_unconstrained() -> None:
    selection = select_alternative_value(10.0, "Length", delta_ratio=0.2)

    assert selection.value == pytest.approx(12.0)


def test_alternative_value_skips_material_without_geometry_failure() -> None:
    selection = select_alternative_value(1, "Material")

    assert selection.value is None
    assert selection.reason == "non_geometry_parameter"
