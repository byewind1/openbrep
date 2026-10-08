from __future__ import annotations

from types import SimpleNamespace

from openbrep.domain_skill_selection import select_project_skill
from openbrep.domain_skills import DomainSkillRegistry
from openbrep.hsf_project import HSFProject
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


class _PlannerLLM:
    def __init__(self):
        self.messages = None

    def generate(self, messages, **_kwargs):
        self.messages = messages
        return '{"object_type":"cabinet","geometry":[],"parameters":[]}'


def test_explicitly_selected_project_skill_is_versioned_in_planning_context(tmp_path):
    project = HSFProject.create_new("SkillPlanning", work_dir=str(tmp_path))
    project.save_to_disk()
    registry = DomainSkillRegistry.for_project(project.root)
    skill = registry.load("cabinet").skill
    assert skill is not None
    select_project_skill(project.root, skill)

    llm = _PlannerLLM()
    pipeline = TaskPipeline()
    events = []
    _plan, _instruction, artifact = pipeline._plan_gdl_object_phase(
        TaskRequest(user_input="按我的柜体方法生成", intent="CREATE", project=project),
        llm,
        "按我的柜体方法生成",
        SimpleNamespace(planner_context="", source_ids=[]),
        "",
        {},
        lambda name, payload: events.append((name, payload)),
    )

    planner_user_message = llm.messages[-1]["content"]
    assert "已选 Domain Skill：cabinet v0.1.0" in planner_user_message
    assert skill.content_hash in planner_user_message
    assert artifact["domain_skills"] == [{
        "skill_id": "cabinet",
        "version": "0.1.0",
        "content_hash": skill.content_hash,
        "status": "development",
    }]
