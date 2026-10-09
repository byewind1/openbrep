import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from openbrep.domain_skill_selection import (
    load_project_skill_selections,
    select_project_skill,
    set_project_skill_enabled,
)
from openbrep.domain_skills import DomainSkillRegistry
from openbrep.hsf_project import HSFProject
from openbrep.runtime.pipeline import TaskPipeline
from openbrep.config import GDLAgentConfig


def test_project_adoption_pins_package_content_and_can_be_disabled(tmp_path):
    builtin = DomainSkillRegistry.builtin().load("cabinet").skill
    assert builtin is not None

    record = select_project_skill(tmp_path, builtin, source="builtin")
    loaded = load_project_skill_selections(tmp_path)
    assert loaded.records == (record,)
    assert [skill.skill_id for skill in loaded.skills] == ["cabinet"]
    assert (tmp_path / ".openbrep/domain_skills/packages/cabinet/manifest.json").is_file()

    set_project_skill_enabled(tmp_path, "cabinet", False)
    assert load_project_skill_selections(tmp_path).skills == ()
    set_project_skill_enabled(tmp_path, "cabinet", True)
    assert [skill.skill_id for skill in load_project_skill_selections(tmp_path).skills] == ["cabinet"]


def test_project_adoption_archives_previous_package_and_detects_external_change(tmp_path):
    package = tmp_path / "source" / "cabinet"
    package.parent.mkdir()
    shutil.copytree(DomainSkillRegistry.builtin().root / "cabinet", package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["methodology_path"] = "method.md"
    (package / "method.md").write_text("cabinet method v1", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    original = DomainSkillRegistry(package.parent).load("cabinet").skill
    assert original is not None
    project = tmp_path / "project"
    project.mkdir()
    select_project_skill(project, original, source="personal")

    manifest["version"] = "0.2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    updated = DomainSkillRegistry(package.parent).load("cabinet").skill
    assert updated is not None
    select_project_skill(project, updated, source="personal")

    archives = list((project / ".openbrep/domain_skills/versions/cabinet").iterdir())
    assert len(archives) == 1
    adopted = project / ".openbrep/domain_skills/packages/cabinet/manifest.json"
    changed = json.loads(adopted.read_text(encoding="utf-8"))
    changed["version"] = "0.3.0"
    adopted.write_text(json.dumps(changed), encoding="utf-8")
    result = load_project_skill_selections(project)
    assert result.skills == ()
    assert result.issues[0].code == "SKILL_VERSION_CHANGED"


def test_pipeline_injects_only_explicitly_adopted_matching_method(tmp_path):
    source_root = tmp_path / "example-skills"
    source_root.mkdir()
    package = source_root / "chinese-timber-zuodou"
    shutil.copytree(
        Path(__file__).resolve().parents[1]
        / "examples" / "chinese-architecture" / "skills" / "chinese-timber-zuodou",
        package,
    )
    skill = DomainSkillRegistry(source_root).load("chinese-timber-zuodou").skill
    assert skill is not None
    project = HSFProject.create_new("Zuodou", work_dir=str(tmp_path / "workspace"))
    before = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "trace"), include_learned_skills=False)
    plain = before._load_skills_for_request("修改坐斗", SimpleNamespace(project=project, intent="MODIFY"))
    assert "Adopted domain method" not in plain

    select_project_skill(project.root, skill, source="example")
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "trace2"), include_learned_skills=False)
    prompt = pipeline._load_skills_for_request("修改坐斗", SimpleNamespace(project=project, intent="MODIFY"))
    assert "Adopted domain method: chinese-timber-zuodou 1.0.0" in prompt
    assert "内侧对齐" in prompt
    assert pipeline._domain_skill_usage["chinese-timber-zuodou"]["consumed"] == "methodology"
