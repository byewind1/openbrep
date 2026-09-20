"""ST09 deterministic end-to-end workflow for the spiral-stair fixture.

This deliberately exercises the real HSF mutation tools, revision store, stair
contract evaluator, and skill-proposal gate.  FakeLLM is used only for the
proposal response; it never replaces source/revision/tool persistence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbrep.compiler import CompileResult, MockHSFCompiler
from openbrep.contracts.stair import evaluate_stair_contract
from openbrep.core import GDLAgent
from openbrep.gdl_previewer import evaluate_parameter_environment, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse, ToolCall
from openbrep.revisions import create_revision, list_revisions, restore_revision
from openbrep.runtime import skill_harvest
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry
from openbrep.runtime.pipeline import TaskResult
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench.project_parameter_service import parameter_values


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "spiral_stair"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))


class FakeLLM:
    """Only controls the deterministic skill-proposal response."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def generate(self, _messages, **_kwargs):
        self.calls += 1
        return LLMResponse(content=self.content, model="st09-fake", usage={}, finish_reason="stop")


def _proposal() -> str:
    return json.dumps(
        {
            "name": "spiral_tread_loop_pattern",
            "pattern_type": "repeating_geometry",
            "content": (
                "## 适用场景 / When to Use\n"
                "楼梯踏步或栏杆需要按数量循环布置时。\n\n"
                "## 写法要点\n"
                "- 用派生数量控制循环边界；\n"
                "- 每轮 ADD/DEL 必须配对；\n"
                "- 顶部可选构件必须与同一派生数量联动。"
            ),
        },
        ensure_ascii=False,
    )


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _copy_before(tmp_path: Path) -> HSFProject:
    root = tmp_path / "spiral_stair"
    shutil.copytree(FIXTURE_ROOT / "before_top_option", root)
    return HSFProject.load_from_disk(str(root))


def _registry(project: HSFProject, tmp_path: Path) -> ModifyToolRegistry:
    agent = GDLAgent(llm=FakeLLM("unused"), compiler=MockHSFCompiler())
    return ModifyToolRegistry(
        project=project,
        compiler=MockHSFCompiler(),
        output_gsm=str(tmp_path / "out.gsm"),
        apply_changes=agent._apply_changes,
    )


def _call(registry: ModifyToolRegistry, name: str, arguments: dict) -> object:
    return registry.execute(ToolCall(id=f"st09-{name}", name=name, arguments=arguments))


def _write_contract(project: HSFProject) -> None:
    contract = {
        "schema_version": 1,
        "type": "spiral_stair",
        "parameter_bindings": {
            "height": "height", "num_steps": "num_steps", "step_riser": "step_riser",
            "show_top_tread": "show_top_tread", "handrail_height": "handrail_height",
            "pole_radius": "pole_radius", "tread_outer_radius": "tread_outer_radius",
            "tread_inner_radius": "_tread_inner_radius", "rail_center_radius": "_rail_center_radius",
        },
        "constraints": {
            "step_riser_range": {"min": 0.10, "max": 0.25, "source": "test_profile", "blocking": True},
            "handrail_height_range": {"min": 0.8, "max": 1.2, "source": "test_profile", "blocking": True},
            "rail_center_inset": {"expected": 0.025, "tolerance": 1e-9, "source": "project_choice", "blocking": True},
            "tread_pole_overlap": {"expected": 0.05, "tolerance": 1e-9, "source": "project_choice", "blocking": True},
        },
        "geometry_bindings": {},
        "provenance": {"source": "ST07 stair fixture"},
    }
    path = project.root / ".openbrep/contracts/stair.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")


def _contract_report(project: HSFProject):
    evaluated = evaluate_parameter_environment(
        project.get_script(ScriptType.MASTER) or "", parameter_values(project)
    )
    preview = preview_3d_script(
        project.get_script(ScriptType.SCRIPT_3D) or "",
        parameters=evaluated.runtime_values,
        unknown_command_policy="warn",
        quality="fast",
    )
    return evaluate_stair_contract(project, evaluated=evaluated, preview=preview)


def run_continuous_workflow(tmp_path: Path) -> dict:
    project = _copy_before(tmp_path)
    fixture_before = {state: _hashes(FIXTURE_ROOT / state) for state in ("before_top_option", "partial_top_option", "after_top_option")}
    registry = _registry(project, tmp_path)
    before_fp = compute_source_fingerprint(project.root)
    before_revision = create_revision(project.root, message="ST09 before", trigger="st09", intent="MODIFY")

    misses = []
    for _ in range(2):
        result = _call(registry, "patch_script", {"file_path": "scripts/3d.gdl", "patches": [{"old": "NOT PRESENT", "new": "END"}]})
        misses.append({"ok": result.ok, "summary": result.summary})
    assert all(not item["ok"] for item in misses)
    assert compute_source_fingerprint(project.root) == before_fp

    read = _call(registry, "read_parameters", {})
    fp = read.data["source_fingerprint"]
    edited = _call(registry, "edit_parameters", {
        "expected_source_fingerprint": fp,
        "operations": [{"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1}],
    })
    assert edited.ok
    before_1d = (FIXTURE_ROOT / "before_top_option/scripts/1d.gdl").read_text(encoding="utf-8-sig")
    partial_1d = (FIXTURE_ROOT / "partial_top_option/scripts/1d.gdl").read_text(encoding="utf-8-sig")
    patched_1d = _call(registry, "patch_script", {"file_path": "scripts/1d.gdl", "patches": [{"old": before_1d, "new": partial_1d}]})
    assert patched_1d.ok
    project.save_to_disk()
    partial_revision = create_revision(project.root, message="ST09 partial before timeout", trigger="st09", intent="MODIFY")
    partial_fp = compute_source_fingerprint(project.root)

    # Timeout is a control-flow interruption: no tool is allowed to mutate state.
    timeout_event = {"status": "timeout", "source_fingerprint": partial_fp}
    assert compute_source_fingerprint(project.root) == timeout_event["source_fingerprint"]

    partial_3d = (FIXTURE_ROOT / "partial_top_option/scripts/3d.gdl").read_text(encoding="utf-8-sig")
    after_3d = (FIXTURE_ROOT / "after_top_option/scripts/3d.gdl").read_text(encoding="utf-8-sig")
    retried = _call(registry, "patch_script", {"file_path": "scripts/3d.gdl", "patches": [{"old": partial_3d, "new": after_3d}]})
    assert retried.ok
    project.save_to_disk()
    after_revision = create_revision(project.root, message="ST09 retry complete", trigger="st09", intent="MODIFY")
    after_fp = compute_source_fingerprint(project.root)

    changed_partial = [rel for rel in MANIFEST["managed_files"] if (FIXTURE_ROOT / "before_top_option" / rel).read_bytes() != (project.root / rel).read_bytes()]
    assert set(changed_partial) == {"paramlist.xml", "scripts/1d.gdl", "scripts/3d.gdl"}

    # Restore from the immutable before snapshot and verify the after snapshot remains intact.
    restored = restore_revision(project.root, before_revision.revision_id, message="ST09 restore before")
    assert compute_source_fingerprint(project.root) == before_fp
    assert (after_revision.path / "scripts/3d.gdl").read_text(encoding="utf-8-sig") == after_3d
    assert restored.revision_id != before_revision.revision_id

    # Reload the successful after snapshot for the skill gate.
    shutil.copyfile(after_revision.path / "paramlist.xml", project.root / "paramlist.xml")
    shutil.copyfile(after_revision.path / "scripts/1d.gdl", project.root / "scripts/1d.gdl")
    shutil.copyfile(after_revision.path / "scripts/3d.gdl", project.root / "scripts/3d.gdl")
    project = HSFProject.load_from_disk(str(project.root))
    _write_contract(project)
    fake_llm = FakeLLM(_proposal())
    task = TaskResult(
        success=True, intent="MODIFY", scripts={"scripts/3d.gdl": after_3d}, project=project,
        compile_result=CompileResult(success=True, mode="mock", output_path=str(tmp_path / "out.gsm")),
        verification={"checks": [{"check_type": "compile", "status": "pass"}, {"check_type": "semantic", "status": "pass"}]},
    )
    skills_dir = tmp_path / "skills"
    session = SimpleNamespace(project_epoch=1, source_path=project.root, pending_skill_proposal=None)
    proposal = skill_harvest.store_pending_proposal(session, task, "让楼梯踏步与栏杆按数量联动", fake_llm, skills_dir)
    assert proposal and session.pending_skill_proposal and not list(skills_dir.glob("*.md"))
    approved = skill_harvest._confirm_skill_proposal_impl(session, {"approve": True}, str(skills_dir))
    assert approved["ok"] and approved["verified"] and list(skills_dir.glob("*.md"))

    return {
        "before_revision": before_revision.revision_id,
        "partial_revision": partial_revision.revision_id,
        "after_revision": after_revision.revision_id,
        "restored_revision": restored.revision_id,
        "before_fingerprint": before_fp,
        "partial_fingerprint": partial_fp,
        "after_fingerprint": after_fp,
        "patch_misses": len(misses),
        "timeout": timeout_event,
        "skill_calls": fake_llm.calls,
        "skill_verified": approved["verified"],
        "fixture_immutable": fixture_before == {state: _hashes(FIXTURE_ROOT / state) for state in fixture_before},
    }


def test_st09_continuous_workflow_preserves_revisions_and_candidate_gate(tmp_path: Path):
    result = run_continuous_workflow(tmp_path)
    assert result["patch_misses"] == 2
    assert result["timeout"]["status"] == "timeout"
    assert result["skill_calls"] == 1
    assert result["skill_verified"] is True
    assert result["fixture_immutable"] is True


@pytest.mark.parametrize(
    ("case_id", "overrides", "expected"),
    [
        ("M01", {}, "pass"),
        ("M02", {"show_top_tread": 0}, "pass"),
        ("M03", {"diameter": 1}, "pass"),
        ("M04", {"diameter": 3}, "pass"),
        ("M05", {"height": 2}, "fail"),
        ("M06", {"height": 5}, "fail"),
        ("M07", {"num_steps": 4}, "fail"),
        ("M08", {"num_steps": 30}, "fail"),
        ("M09", {"total_rotation": 90}, "pass"),
        ("M10", {"total_rotation": 720}, "pass"),
        ("M11", {"height": 5, "num_steps": 4}, "fail"),
        ("M12", {"height": 3.2, "num_steps": 18, "show_top_tread": 0}, "pass"),
    ],
)
def test_st09_parameter_matrix_contract(tmp_path: Path, case_id: str, overrides: dict, expected: str):
    project = _copy_before(tmp_path)
    # The matrix starts from the after fixture, which is the ST07 contract baseline.
    after_root = tmp_path / "after"
    shutil.copytree(FIXTURE_ROOT / "after_top_option", after_root)
    project = HSFProject.load_from_disk(str(after_root))
    _write_contract(project)
    for name, value in overrides.items():
        project.get_parameter(name).value = str(value)
    report = _contract_report(project)
    assert report.applicability == "applicable"
    if expected == "pass":
        assert report.passed, (case_id, report.to_dict())
    else:
        assert not report.passed, (case_id, report.to_dict())
