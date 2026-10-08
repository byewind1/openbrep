from __future__ import annotations

from pathlib import Path

from openbrep.hsf_project import HSFProject
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.skill_scope import personal_skills_dir, skills_dir_for_scope


def test_skill_scope_paths_separate_project_and_personal_storage(tmp_path, monkeypatch):
    personal = tmp_path / "user-skills"
    monkeypatch.setenv("OPENBREP_PERSONAL_SKILLS_DIR", str(personal))

    assert skills_dir_for_scope("project", tmp_path / "Chair") == tmp_path / "Chair" / ".openbrep" / "skills"
    assert skills_dir_for_scope("personal", tmp_path / "Chair") == personal_skills_dir() == personal


def test_personal_skill_is_reused_across_projects_but_excluded_from_sealed_pipeline(tmp_path, monkeypatch):
    personal = tmp_path / "user-skills"
    monkeypatch.setenv("OPENBREP_PERSONAL_SKILLS_DIR", str(personal))
    personal.mkdir()
    (personal / "personal_method.md").write_text(
        "---\nstatus: verified\npattern_type: author_method\n---\n"
        "## 触发关键词\n- personal_method\n\n按作者确认的方法执行。\n",
        encoding="utf-8",
    )
    project_a = HSFProject.create_new("First", work_dir=str(tmp_path / "a"))
    project_b = HSFProject.create_new("Second", work_dir=str(tmp_path / "b"))
    project_a.save_to_disk()
    project_b.save_to_disk()
    project_skill_dir = Path(project_a.root) / ".openbrep" / "skills"
    project_skill_dir.mkdir(parents=True)
    (project_skill_dir / "project_only_method.md").write_text(
        "## 触发关键词\n- project_only_method\n\n只对第一个项目生效。\n",
        encoding="utf-8",
    )
    request = TaskRequest(user_input="use personal_method", project=project_b)

    production = TaskPipeline(include_learned_skills=True)
    sealed = TaskPipeline(include_learned_skills=False)

    assert "## Skill: personal_method" in production._load_skills_for_request(request.user_input, request)
    assert "## Skill: personal_method" not in sealed._load_skills_for_request(request.user_input, request)
    other_project_request = TaskRequest(user_input="use project_only_method", project=project_b)
    assert "project_only_method" not in production._load_skills_for_request(
        other_project_request.user_input, other_project_request,
    )
    assert Path(project_a.root) != Path(project_b.root)
