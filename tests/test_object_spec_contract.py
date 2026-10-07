"""U03-A 可执行领域对象合同测试（openbrep/contracts/object_spec.py）。

覆盖派单验收：重复 ID / 未知 executor / 量纲 / enum 错拒绝；unsupported
关系显式 unknown；Plan hash 防篡改；旧 ModelingPlan/GDLObjectPlan 只读适配；
synthetic 最小对象合同（只读规划报告与合同测试同一规格）。
"""

from __future__ import annotations

import json

import pytest

from openbrep.contracts.object_spec import (
    ExecutionPlan,
    ObjectSpec,
    Observation,
    known_check_executor,
    load_synthetic_minimal_contract,
    observation_from_modeling_plan,
    parse_execution_plan,
    parse_object_spec,
    parse_observation,
    register_check_executor,
    reset_check_executors_for_tests,
)
from openbrep.contracts.object_spec import (
    execution_plan_from_gdl_object_plan as adapt_gdl_object_plan,
)


def _spec_data(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "spec_id": "spec-t",
        "object_type": "test_object",
        "source_observation_id": "obs-t",
        "params": [
            {"param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "m", "default_value": 0.9},
            {"param_id": "p.count", "gdl_name": "count", "type": "Integer", "default_value": 4},
        ],
        "requirements": [
            {"requirement_id": "req-compile", "text": "编译通过", "kind": "check", "check_id": "compile"},
        ],
        "relations": [
            {"relation_id": "rel-1", "left": "p.width", "op": ">", "right": {"const": 0.05}},
        ],
    }
    data.update(overrides)
    return data


def _plan_data(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "plan_id": "plan-t",
        "spec_ref": "spec-t",
        "observation_ref": "obs-t",
        "steps": [
            {"step_id": "step-1", "kind": "modify_script", "target": "scripts/3d.gdl", "detail": "加层板"},
            {"step_id": "step-2", "kind": "verify", "target": "compile"},
        ],
        "requirements": [
            {"requirement_id": "req-compile", "text": "编译通过", "kind": "check", "check_id": "compile"},
        ],
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_check_executors_for_tests()
    yield
    reset_check_executors_for_tests()


# ── 解析 / 校验拒绝路径 ──────────────────────────────────────


def test_valid_spec_and_plan_round_trip():
    spec = parse_object_spec(_spec_data())
    assert spec.ok
    assert isinstance(spec.value, ObjectSpec)
    assert spec.value.params[0].unit == "m"  # Length 规范单位自动补全
    again = parse_object_spec(spec.value.to_dict())
    assert again.ok
    assert again.value.to_dict() == spec.value.to_dict()

    plan = parse_execution_plan(_plan_data())
    assert plan.ok
    assert isinstance(plan.value, ExecutionPlan)


def test_duplicate_param_id_rejected_with_field_path():
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "m"},
        {"param_id": "p.width", "gdl_name": "B", "type": "Length", "unit": "m"},
    ]))
    assert not result.ok
    assert any(
        e.code == "DUPLICATE_ID" and e.field_path == "params[1].param_id" for e in result.errors
    )


def test_duplicate_gdl_name_mapping_rejected():
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.a", "gdl_name": "A", "type": "Length"},
        {"param_id": "p.b", "gdl_name": "A", "type": "Length"},
    ]))
    assert any(e.code == "DUPLICATE_ID" and "gdl_name" in e.field_path for e in result.errors)


def test_unknown_executor_rejected():
    result = parse_object_spec(_spec_data(requirements=[
        {"requirement_id": "req-x", "text": "玄学检查", "kind": "check", "check_id": "mystic_check"},
    ]))
    assert any(
        e.code == "UNKNOWN_EXECUTOR" and e.field_path == "requirements[0].check_id"
        for e in result.errors
    )


def test_registered_executor_accepted():
    assert register_check_executor("behavior") is True
    assert register_check_executor("behavior") is False  # 幂等
    assert known_check_executor("behavior")
    result = parse_object_spec(_spec_data(requirements=[
        {"requirement_id": "req-b", "text": "行为检查", "kind": "check", "check_id": "behavior"},
    ]))
    assert result.ok


def test_dimension_mismatch_rejected():
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "mm", "default_value": 900},
    ]))
    assert any(
        e.code == "DIMENSION_MISMATCH" and e.field_path == "params[0].unit" for e in result.errors
    )
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.angle", "gdl_name": "ang", "type": "Angle", "unit": "rad", "default_value": 1.57},
    ]))
    assert any(e.code == "DIMENSION_MISMATCH" for e in result.errors)
    # 无量纲类型带单位同样拒绝
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.count", "gdl_name": "count", "type": "Integer", "unit": "mm"},
    ]))
    assert any(e.code == "DIMENSION_MISMATCH" for e in result.errors)


def test_enum_value_rejected_and_enum_requires_declaration():
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.style", "gdl_name": "style", "type": "enum",
         "enum_values": ["直棂", "回纹"], "default_value": "冰裂纹"},
    ]))
    assert any(
        e.code == "ENUM_VALUE_INVALID" and e.field_path == "params[0].default_value"
        for e in result.errors
    )
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.style", "gdl_name": "style", "type": "enum"},
    ]))
    assert any(e.code == "MISSING_FIELD" and "enum_values" in e.field_path for e in result.errors)


def test_gdl_index_parameter_types_are_validated_as_integer_indices():
    data = _spec_data(relations=[], params=[
        {"param_id": "p.material", "gdl_name": "mat_body", "type": "Material", "default_value": 1},
        {"param_id": "p.pen", "gdl_name": "pen", "type": "PenColor", "default_value": 2},
    ])

    result = parse_object_spec(data)

    assert result.ok
    invalid = _spec_data(relations=[], params=[
        {"param_id": "p.material", "gdl_name": "mat_body", "type": "Material", "default_value": 1.5},
    ])
    invalid_result = parse_object_spec(invalid)
    assert not invalid_result.ok
    assert any(error.code == "INVALID_VALUE" and error.field_path == "params[0].default_value" for error in invalid_result.errors)


def test_nan_and_non_integer_rejected():
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.width", "gdl_name": "A", "type": "Length", "unit": "m",
         "default_value": float("nan")},
    ]))
    assert any(e.code == "NOT_FINITE" for e in result.errors)
    result = parse_object_spec(_spec_data(params=[
        {"param_id": "p.count", "gdl_name": "count", "type": "Integer", "default_value": 4.5},
    ]))
    assert any(e.code == "INVALID_VALUE" for e in result.errors)


def test_unsupported_relation_becomes_explicit_unknown_not_error():
    result = parse_object_spec(_spec_data(relations=[
        {"relation_id": "rel-ok", "left": "p.width", "op": ">=", "right": {"const": 0.05}},
        {"relation_id": "rel-expr", "left": "p.width", "op": "DERIVE",
         "right": {"expr": "A*2 - B"}},
    ]))
    assert result.ok  # 语法外关系不拒绝、不静默丢弃
    statuses = {r.relation_id: r.status for r in result.value.relations}
    assert statuses["rel-ok"] == "defined"
    assert statuses["rel-expr"] == "unknown"


def test_relation_referencing_unknown_param_rejected():
    result = parse_object_spec(_spec_data(relations=[
        {"relation_id": "rel-bad", "left": "p.ghost", "op": ">", "right": {"const": 1}},
    ]))
    assert any(e.code == "INVALID_VALUE" and ".left" in e.field_path for e in result.errors)


def test_plan_hash_tamper_detected():
    plan = parse_execution_plan(_plan_data())
    assert plan.ok
    data = plan.value.to_dict()
    result = parse_execution_plan(data)
    assert result.ok
    tampered = dict(data)
    tampered["steps"] = [dict(data["steps"][0], detail="被篡改")] + data["steps"][1:]
    result = parse_execution_plan(tampered)
    assert not result.ok
    assert result.errors[0].code == "HASH_MISMATCH"
    assert result.errors[0].field_path == "plan_hash"


def test_plan_requires_spec_ref():
    result = parse_execution_plan(_plan_data(spec_ref=""))
    assert any(e.code == "MISSING_FIELD" and e.field_path == "spec_ref" for e in result.errors)


def test_observation_duplicate_paths_and_status_validation():
    result = parse_observation({
        "schema_version": 1, "observation_id": "obs", "source": "manual",
        "items": [
            {"field_path": "p.width", "status": "observed", "value": 0.9},
            {"field_path": "p.width", "status": "inferred"},
            {"field_path": "p.depth", "status": "made_up"},
        ],
    })
    assert any(e.code == "DUPLICATE_ID" for e in result.errors)
    assert any(e.code == "INVALID_VALUE" and ".status" in e.field_path for e in result.errors)


# ── 旧数据只读适配 ───────────────────────────────────────────


def test_observation_from_modeling_plan_adapter():
    from openbrep.vision.modeling_plan import ModelingPlan

    plan = ModelingPlan(
        schema_name="lattice_window",
        fields={"opening_shape": "拱形", "grid_topology": {"rows": 4}},
        confidence={"opening_shape": "high", "grid_topology.rows": "low"},
        raw_description="测试漏窗",
        source_images=["deadbeef" * 8],
    )
    obs = observation_from_modeling_plan(plan)
    assert isinstance(obs, Observation)
    assert obs.source == "vision_extraction"
    statuses = {i.field_path: i.status for i in obs.items}
    assert statuses["opening.shape"] == "observed"
    assert statuses["pattern.rows"] == "observed"
    assert statuses["pattern.cols"] == "unknown"
    assert next(i for i in obs.items if i.field_path == "pattern.rows").confidence == "low"
    assert statuses["raw_description"] == "inferred"
    assert any(r.startswith("extraction:") for r in obs.source_refs)
    parsed = parse_observation(obs.to_dict())
    assert parsed.ok


def test_observation_adapter_degraded_marks_unknown():
    from openbrep.vision.modeling_plan import ModelingPlan

    plan = ModelingPlan(
        schema_name="lattice_window",
        fields={"opening_shape": "拱形"},
        degraded=True,
    )
    obs = observation_from_modeling_plan(plan)
    assert all(i.status == "unknown" for i in obs.items)


def test_execution_plan_from_gdl_object_plan_adapter():
    from openbrep.object_planner import GDLObjectPlan

    gdl_plan = GDLObjectPlan(
        object_type="书架",
        parameters=["A", "B", "shelf_count"],
        script_3d_strategy=["层板用 FOR 循环", "侧板用 BLOCK"],
        script_2d_strategy=["PROJECT2 投影"],
        validation_checks=["检查 2D 脚本是否可见", "检查 exotic 玄学项"],
    )
    adapted = adapt_gdl_object_plan(gdl_plan, plan_id="plan-adapted", spec_ref="spec-t")
    assert adapted.ok
    plan = adapted.value
    assert isinstance(plan, ExecutionPlan)
    kinds = [s.kind for s in plan.steps]
    assert kinds.count("modify_script") == 3
    assert kinds.count("set_param") == 3
    # validation_checks 无法映射执行器 → 显式 unknown，不冒充可查
    assert all(r.check_id is None and r.status == "unknown" for r in plan.requirements)
    assert len(plan.requirements) == 2
    assert plan.plan_hash


def test_unexecutable_textual_check_roundtrips_as_unknown():
    parsed = parse_object_spec({
        "schema_version": 1,
        "spec_id": "spec-text-only-check",
        "object_type": "cabinet",
        "params": [],
        "requirements": [{
            "requirement_id": "req-door-count",
            "text": "双门分缝可见",
            "kind": "check",
            "check_id": None,
        }],
        "relations": [],
    })

    assert parsed.ok
    requirement = parsed.value.requirements[0]
    assert requirement.status == "unknown"
    assert parse_object_spec(parsed.value.to_dict()).value.requirements[0].status == "unknown"


# ── synthetic 最小对象合同（同一规格两处消费）────────────────


def test_synthetic_minimal_contract_loads_and_validates():
    result = load_synthetic_minimal_contract()
    assert result.ok, [e.to_dict() for e in result.errors]
    spec = result.value["object_spec"]
    plan = result.value["execution_plan"]
    obs = result.value["observation"]
    assert isinstance(spec, ObjectSpec) and spec.spec_id == "spec-minimal-shelf"
    assert isinstance(plan, ExecutionPlan) and plan.spec_ref == spec.spec_id
    assert isinstance(obs, Observation)
    assert plan.plan_hash


def test_synthetic_contract_matches_pipeline_report(tmp_path):
    """同一规格：合同测试加载的 spec 与只读规划报告 metadata 中的完全一致。"""
    from openbrep.compiler import MockHSFCompiler
    from openbrep.config import GDLAgentConfig
    from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
    from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

    project = HSFProject.create_new("Shelf", work_dir=str(tmp_path))
    project.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="2"))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    project.save_to_disk()

    mock_llm = MagicMockPlanLLM()
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    request = TaskRequest(
        user_input="给书架加一层层板",
        intent="MODIFY",
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
        execution_policy={"mode": "plan"},
    )
    result = pipeline.execute(request)
    assert result.success
    contract = result.metadata.get("object_contract")
    assert contract and contract["parse_ok"] is True

    fixture = load_synthetic_minimal_contract()
    assert fixture.ok
    assert contract["object_spec"] == fixture.value["object_spec"].to_dict()
    assert contract["execution_plan"] == fixture.value["execution_plan"].to_dict()
    # plan hash 在报告里可独立复核
    from openbrep.contracts.object_spec import execution_plan_hash_dict

    assert (
        execution_plan_hash_dict(
            {k: v for k, v in contract["execution_plan"].items() if k != "plan_hash"}
        )
        == contract["execution_plan"]["plan_hash"]
    )


class MagicMockPlanLLM:
    """只读规划的 LLM 替身：返回计划协议 JSON。"""

    def __init__(self):
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        from openbrep.llm import LLMResponse

        plan = {
            "intent_summary": "给书架加一层层板",
            "user_visible_changes": ["3D 几何多一层板"],
            "affected_files": ["scripts/3d.gdl"],
            "risk": "低",
        }
        return LLMResponse(content=json.dumps(plan, ensure_ascii=False), model="mock", usage={}, finish_reason="stop")


def test_stair_contract_module_untouched():
    """兼容：stair 合同不受对象合同影响（不迁移楼梯合同，派单明确）。"""
    from openbrep.contracts.stair import StairContractCheck, StairContractReport

    check = StairContractCheck(check_id="x", status="unknown")
    report = StairContractReport(applicability="not_applicable", checks=[check])
    assert report.passed
