from openbrep.hsf_project import GDLParameter
from openbrep.planning.paramlist_contract import apply_planned_parameters


def _artifact(params, sources=None):
    return {
        "candidate_spec": {
            "schema_version": 1,
            "spec_id": "spec-test",
            "object_type": "cabinet",
            "source_observation_id": "user-request",
            "params": params,
            "requirements": [],
            "relations": [],
        },
        "parameter_sources": sources or {},
    }


def test_planned_parameter_is_added_with_typed_default_and_hsf_values_are_preserved():
    result = apply_planned_parameters(
        [GDLParameter("A", "Length", "Width", "0.9", is_fixed=True)],
        _artifact([{
            "param_id": "p.shelf_count", "gdl_name": "shelf_count", "type": "Integer",
            "unit": None, "description": "层板数量", "default_value": 5,
            "enum_values": [], "required": True,
        }]),
    )
    assert result.ok
    assert result.added == ("shelf_count",)
    assert [(p.name, p.value, p.type_tag) for p in result.parameters] == [
        ("A", "0.9", "Length"), ("shelf_count", "5", "Integer"),
    ]


def test_explicit_dimension_overrides_generated_value_after_unit_normalization():
    result = apply_planned_parameters(
        [GDLParameter("A", "Length", "Width", "0.8", is_fixed=True)],
        _artifact([{
            "param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "m",
            "description": "宽度", "default_value": 0.8, "enum_values": [], "required": True,
        }], {"A": {"source": "user_explicit", "value": 1.2, "unit": "m"}}),
    )
    assert result.ok
    assert result.parameters[0].value == "1.2"
    assert result.parameters[0].is_fixed is True


def test_required_parameter_without_a_value_fails_closed():
    result = apply_planned_parameters([], _artifact([{
        "param_id": "p.width", "gdl_name": "width", "type": "Length", "unit": "m",
        "description": "宽度", "default_value": None, "enum_values": [], "required": True,
    }]))
    assert not result.ok
    assert result.errors[0]["code"] == "MISSING_REQUIRED_VALUE"


def test_plan_enum_is_written_as_gdl_string_and_invalid_default_is_rejected():
    artifact = _artifact([{
        "param_id": "p.pattern", "gdl_name": "pattern", "type": "enum", "unit": None,
        "description": "纹样", "default_value": "diagonal", "enum_values": ["plain", "diagonal"],
        "required": True,
    }])
    result = apply_planned_parameters([], artifact)
    assert result.ok
    assert (result.parameters[0].type_tag, result.parameters[0].value) == ("String", "diagonal")
    artifact["candidate_spec"]["params"][0]["default_value"] = "unknown"
    invalid = apply_planned_parameters([], artifact)
    assert not invalid.ok
    assert invalid.errors[0]["code"] == "ENUM_VALUE_INVALID"
