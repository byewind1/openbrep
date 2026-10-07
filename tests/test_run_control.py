from unittest.mock import MagicMock

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.runtime.run_control import RunControl


def test_fake_clock_deadline_denies_late_mutation():
    now = [10.0]
    control = RunControl(tool_budget=3, task_timeout=5, clock=lambda: now[0])
    assert control.check().allowed
    now[0] = 15.0
    decision = control.begin_tool(stage="write")
    assert decision.allowed is False
    assert decision.reason == "deadline"
    assert decision.tool_calls == 0


def test_cancel_probe_and_tool_budget_are_shared_across_nested_work():
    cancelled = [False]
    control = RunControl(tool_budget=1, task_timeout=60, should_cancel=lambda: cancelled[0])
    assert control.begin_tool(stage="compile").allowed
    exhausted = control.begin_tool(stage="repair")
    assert exhausted.allowed is False
    assert exhausted.reason == "tool_budget"
    cancelled[0] = True
    assert control.check().reason == "cancelled"


def test_terminal_decision_is_idempotent_and_blocks_late_tools():
    control = RunControl(tool_budget=2, task_timeout=60)
    assert control.finish("completed") is True
    assert control.finish("cancelled") is False
    decision = control.begin_tool(stage="late_write")
    assert decision.allowed is False
    assert decision.reason == "terminal"


def test_commit_rechecks_cancel_at_the_mutation_boundary():
    cancelled = [False]
    writes = []
    control = RunControl(tool_budget=2, task_timeout=60, should_cancel=lambda: cancelled[0])
    cancelled[0] = True
    decision, value = control.commit(lambda: writes.append("late"))
    assert decision.allowed is False
    assert decision.reason == "cancelled"
    assert value is None
    assert writes == []


def test_broken_cancel_probe_fails_closed():
    def broken_probe():
        raise RuntimeError("cancel source unavailable")

    assert RunControl(tool_budget=2, task_timeout=60, should_cancel=broken_probe).check().reason == "cancelled"


def test_create_discards_model_result_returned_after_cancellation(tmp_path):
    cancelled = [False]
    project = HSFProject.create_new("late_create", work_dir=str(tmp_path))
    original = project.get_script(ScriptType.SCRIPT_3D)
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "traces"))
    pipeline._make_llm = lambda _request: MagicMock()
    pipeline._make_compiler = lambda: MagicMock()
    pipeline._load_knowledge = lambda: ""
    pipeline._load_skills = lambda _instruction: ""
    pipeline._plan_gdl_object_phase = lambda *args, **kwargs: (None, "instruction", None)
    agent = MagicMock()

    def generate_after_cancel(*args, **kwargs):
        cancelled[0] = True
        return agent, {"scripts/3d.gdl": "BLOCK 1, 1, 1\nEND\n"}, "late", ""

    pipeline._generate_with_agent = generate_after_cancel
    result = pipeline.execute(TaskRequest(
        user_input="做一个方块", intent="CREATE", project=project,
        work_dir=str(tmp_path), should_cancel=lambda: cancelled[0],
    ))

    assert result.success is False
    assert result.error == "TASK_CANCELLED"
    assert result.project is None
    assert project.get_script(ScriptType.SCRIPT_3D) == original
    agent._apply_changes.assert_not_called()
