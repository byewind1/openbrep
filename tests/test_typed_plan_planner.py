from types import SimpleNamespace

from openbrep.contracts.object_spec import Observation, ObservationItem, parse_execution_plan
from openbrep.object_planner import GDLObjectPlan
from openbrep.planning.typed_plan import build_planning_artifact


def _plan(**overrides):
    values = {
        "object_type": "parametric_cabinet",
        "parts": [
            {"part_id": "carcass", "description": "柜体"},
            {"part_id": "doors", "description": "双门"},
        ],
        "typed_parameters": [
            {"param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "mm", "description": "宽度", "default_value": 1200, "required": True},
            {"param_id": "p.door_count", "gdl_name": "door_count", "type": "Integer", "description": "门扇数", "default_value": 2},
        ],
        "requirement_mappings": [
            {
                "requirement_id": "req-double-door",
                "text": "保留双门分缝",
                "kind": "constraint",
                "part_refs": ["doors"],
                "parameter_refs": ["p.door_count"],
                "script_refs": ["scripts/3d.gdl", "scripts/2d.gdl"],
                "check_id": None,
                "scenario_refs": ["front-default"],
                "source_refs": ["user:double-door"],
            },
        ],
        "script_3d_strategy": ["按门扇数量生成门板"],
        "script_2d_strategy": ["显示双门分缝"],
        "parameter_script_strategy": [],
        "ui_script_strategy": [],
        "validation_checks": [],
        "degraded": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_builds_hash_bound_typed_candidate_and_canonicalizes_explicit_units():
    observation = Observation(
        "obs-image-1", "vision_extraction", ["extraction:abc"],
        [ObservationItem("parts.doors.count", "observed", 1, confidence="high")],
    )

    artifact = build_planning_artifact(_plan(), observations=[observation])

    assert artifact.status == "ready"
    assert artifact.candidate_spec["params"][0]["default_value"] == 1.2
    assert artifact.candidate_spec["params"][0]["unit"] == "m"
    assert artifact.candidate_spec["source_observation_id"] == "obs-image-1"
    plan = parse_execution_plan(artifact.execution_plan)
    assert plan.ok
    assert plan.value.requirement_mappings[0].part_refs == ["doors"]
    assert plan.value.requirement_mappings[0].parameter_refs == ["p.door_count"]
    assert artifact.execution_plan["plan_hash"]


def test_dimensionless_gdl_labels_and_material_defaults_do_not_request_input():
    plan = _plan(typed_parameters=[
        {"param_id": "p.count", "gdl_name": "count", "type": "Integer", "unit": "count", "default_value": 4},
        {"param_id": "p.material", "gdl_name": "mat_body", "type": "Material", "unit": "index", "default_value": "项目默认材质"},
    ])

    artifact = build_planning_artifact(plan)

    assert artifact.status == "degraded"
    assert artifact.candidate_spec is not None
    params = {item["gdl_name"]: item for item in artifact.candidate_spec["params"]}
    assert params["count"]["unit"] is None
    assert params["mat_body"]["unit"] is None
    assert params["mat_body"]["default_value"] is None
    assert any(issue["code"] == "NON_NUMERIC_INDEX_DEFAULT_IGNORED" for issue in artifact.issues)


def test_enum_alias_without_options_and_material_label_degrade_without_blocking_plan():
    plan = _plan(typed_parameters=[
        {"param_id": "p.direction", "gdl_name": "direction", "type": "Enum", "unit": "none", "default_value": "左开"},
        {"param_id": "p.material", "gdl_name": "mat_body", "type": "Material", "unit": "none", "default_value": "项目默认材质", "required": True},
    ])

    artifact = build_planning_artifact(plan)

    assert artifact.status == "degraded"
    assert artifact.candidate_spec is not None
    params = {item["gdl_name"]: item for item in artifact.candidate_spec["params"]}
    assert params["direction"]["type"] == "String"
    assert params["direction"]["default_value"] == "左开"
    assert params["mat_body"]["default_value"] is None
    assert params["mat_body"]["required"] is False


def test_observation_conflict_requires_input_and_does_not_erase_candidate_evidence():
    artifact = build_planning_artifact(_plan(), conflict_fields=["parts.doors.count"])

    assert artifact.status == "needs_input"
    assert artifact.execution_plan is not None
    assert artifact.issues[0]["code"] == "OBSERVATION_CONFLICT"


def test_unknown_part_or_parameter_reference_degrades_plan():
    plan = _plan(requirement_mappings=[{
        "requirement_id": "req-bad",
        "text": "未知映射",
        "kind": "constraint",
        "part_refs": ["not-declared"],
        "parameter_refs": ["p.missing"],
        "script_refs": ["scripts/3d.gdl"],
        "scenario_refs": [],
        "source_refs": [],
    }])

    artifact = build_planning_artifact(plan)

    assert artifact.status == "degraded"
    assert {issue["code"] for issue in artifact.issues} >= {"UNKNOWN_PART_REF", "UNKNOWN_PARAMETER_REF"}


def test_legacy_free_text_plan_is_degraded_instead_of_claiming_complete_typed_plan():
    artifact = build_planning_artifact(GDLObjectPlan(
        object_type="cabinet",
        parameters=["Length A = width"],
        validation_checks=["two doors"],
    ))

    assert artifact.status == "degraded"
    assert artifact.execution_plan is not None
    assert artifact.execution_plan["requirements"][0]["status"] == "unknown"
    assert any(issue["code"] == "REQUIREMENT_MAPPING_MISSING" for issue in artifact.issues)


def test_unknown_check_executor_is_not_accepted_from_planner():
    plan = _plan(requirement_mappings=[{
        "requirement_id": "req-fake-check",
        "text": "模型想象的检查",
        "kind": "check",
        "part_refs": ["doors"],
        "parameter_refs": [],
        "script_refs": ["scripts/3d.gdl"],
        "check_id": "execute_arbitrary_python",
        "scenario_refs": [],
        "source_refs": [],
    }])

    artifact = build_planning_artifact(plan)

    assert artifact.status == "degraded"
    assert artifact.execution_plan["requirements"][0]["check_id"] is None
    assert any(issue["code"] == "UNKNOWN_CHECK_EXECUTOR" for issue in artifact.issues)


def test_unbound_check_requirement_degrades_the_plan_instead_of_claiming_ready():
    plan = _plan(requirement_mappings=[{
        "requirement_id": "req-unverifiable",
        "text": "层板间距满足用户要求",
        "kind": "check",
        "part_refs": ["doors"],
        "parameter_refs": [],
        "script_refs": ["scripts/3d.gdl"],
        "check_id": None,
        "scenario_refs": [],
        "source_refs": ["user:request"],
    }])

    artifact = build_planning_artifact(plan)

    assert artifact.status == "degraded"
    assert artifact.execution_plan["requirements"][0]["status"] == "unknown"
    assert any(issue["code"] == "REQUIREMENT_EXECUTOR_MISSING" for issue in artifact.issues)


def test_explicit_user_width_overrides_image_estimate_and_keeps_provenance():
    image = Observation(
        "obs-image", "vision_extraction", ["image:front"],
        [ObservationItem("p.width", "inferred", 0.9, "m", "low", "图像估计")],
    )
    plan = _plan()
    plan.typed_parameters[0]["default_value"] = 0.9
    plan.typed_parameters[0]["value_source"] = "observation"
    plan.typed_parameters[0]["source_refs"] = ["image:front"]

    artifact = build_planning_artifact(
        plan,
        observations=[image],
        user_input="参考图估计宽度900mm，我明确指定宽度1200mm",
    )

    assert artifact.status == "ready"
    width = next(item for item in artifact.candidate_spec["params"] if item["gdl_name"] == "A")
    assert width["default_value"] == 1.2
    assert artifact.parameter_sources["A"]["source"] == "user_explicit"
    explicit = next(item for item in artifact.observations if item["observation_id"] == "user-explicit-dimensions")
    assert explicit["items"][0]["value"] == 1.2
    mapping = next(item for item in artifact.execution_plan["requirement_mappings"] if item["requirement_id"] == "req-explicit-a")
    assert mapping["parameter_refs"] == ["p.width"]
    assert mapping["source_refs"] == ["user:explicit_dimension.width"]


def test_explicit_dimension_without_unit_requests_clarification_instead_of_guessing():
    artifact = build_planning_artifact(_plan(), user_input="宽度明确指定1200")

    assert artifact.status == "needs_input"
    assert any(issue["code"] == "MISSING_EXPLICIT_UNIT" for issue in artifact.issues)


def test_user_summary_labels_degraded_plan_and_surfaces_coverage_gaps():
    plan = GDLObjectPlan(
        object_type="cabinet",
        parameters=["Length A = width"],
        validation_checks=["two doors"],
        degraded=True,
    )
    artifact = build_planning_artifact(plan)

    summary = plan.to_user_summary(artifact.to_dict())

    assert "降级候选" in summary
    assert "不能视为完整 typed Plan" in summary
    assert "要求覆盖" not in summary
    assert "Plan hash" in summary
    assert "未提供可验证的构件/参数/脚本/检查/场景映射" in summary


def test_user_summary_distinguishes_validated_candidate_from_delivery_verification():
    plan = GDLObjectPlan(**vars(_plan()))
    artifact = build_planning_artifact(plan)

    summary = plan.to_user_summary(artifact.to_dict())

    assert "结构化计划已通过合同校验" in summary
    assert "不代表生成结果已验证" in summary
    assert "p.door_count" in summary  # requirement trace retains stable internal IDs
    assert "door_count" in summary
    assert artifact.execution_plan["plan_hash"] in summary
