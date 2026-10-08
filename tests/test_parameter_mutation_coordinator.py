from __future__ import annotations

from openbrep.contracts.import_adapter import build_import_candidate
from openbrep.contracts.project_store import commit_project_state, load_project_contract
from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.parameter_mutations import mutate_project_parameters
from openbrep.runtime.run_control import RunControl
from openbrep.source_fingerprint import compute_source_fingerprint


def _project(tmp_path):
    project = HSFProject.create_new("MutationFixture", work_dir=str(tmp_path))
    project.add_parameter(GDLParameter(name="width", type_tag="Length", value="0.2"))
    project.add_parameter(GDLParameter(name="depth", type_tag="Length", value="0.3"))
    project.save_to_disk()
    return project


def _spec():
    return {
        "schema_version": 1,
        "spec_id": "spec-mutation",
        "object_type": "test_object",
        "params": [
            {"param_id": "p.width", "gdl_name": "width", "type": "Length", "unit": "m",
             "enum_values": [], "default_value": None, "required": True},
            {"param_id": "p.depth", "gdl_name": "depth", "type": "Length", "unit": "m",
             "enum_values": [], "default_value": None, "required": False},
        ],
        "requirements": [],
        "relations": [
            {"relation_id": "depth-min", "left": "p.depth", "op": ">=", "right": {"const": 0.1}},
        ],
    }


def _adopt(project, spec=None, observation=None):
    result = commit_project_state(project, spec or _spec(), observation=observation)
    assert result.ok, result.error
    return result.source_fingerprint


def test_allowed_value_edit_preserves_contract_default_and_rebinds_freshness(tmp_path):
    project = _project(tmp_path)
    before = _adopt(project)
    callbacks = []

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=before,
        operations=[{"op": "set_value", "name": "depth", "value": 0.25}],
        before_commit=lambda: callbacks.append("snapshot"),
    )

    assert result.ok, result.error
    assert result.changed_files == ["paramlist.xml"]
    assert callbacks == ["snapshot"]
    contract = load_project_contract(project.root)
    assert contract.status == "fresh"
    depth = next(param for param in contract.object_spec["params"] if param["gdl_name"] == "depth")
    assert depth["default_value"] is None
    assert project.get_parameter("depth").value == "0.25"
    assert contract.current_source_fingerprint != before


def test_relation_violation_is_rejected_before_snapshot_or_source_write(tmp_path):
    project = _project(tmp_path)
    before = _adopt(project)
    source = (project.root / "paramlist.xml").read_bytes()
    callbacks = []

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=before,
        operations=[{"op": "set_value", "name": "depth", "value": 0.05}],
        before_commit=lambda: callbacks.append("snapshot"),
    )

    assert not result.ok
    assert result.error_code == "CONTRACT_VALUE_OUT_OF_RANGE"
    assert callbacks == []
    assert (project.root / "paramlist.xml").read_bytes() == source
    assert project.get_parameter("depth").value == "0.3"
    assert load_project_contract(project.root).status == "fresh"


def test_required_parameter_cannot_be_deleted_and_add_updates_typed_spec(tmp_path):
    project = _project(tmp_path)
    before = _adopt(project)
    rejected = mutate_project_parameters(
        project,
        expected_source_fingerprint=before,
        operations=[{"op": "delete", "name": "width"}],
    )
    assert rejected.error_code == "CONTRACT_REQUIRED_PARAMETER"

    added = mutate_project_parameters(
        project,
        expected_source_fingerprint=before,
        operations=[{"op": "add", "name": "shelf_count", "type": "Integer", "value": 4}],
    )
    assert added.ok, added.error
    contract = load_project_contract(project.root)
    shelf_count = next(param for param in contract.object_spec["params"] if param["gdl_name"] == "shelf_count")
    assert shelf_count["type"] == "Integer"
    assert shelf_count["default_value"] is None


def test_import_observation_refreshes_with_current_value_and_contract_stays_fresh(tmp_path):
    project = _project(tmp_path)
    candidate = build_import_candidate(project)
    before = _adopt(project, candidate["candidate_spec"], candidate["observation"])

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=before,
        operations=[{"op": "set_value", "name": "width", "value": 0.45}],
    )

    assert result.ok, result.error
    contract = load_project_contract(project.root)
    assert contract.status == "fresh"
    fact = next(item for item in contract.observation["items"] if item["field_path"] == "source.parameters.width")
    assert fact["value"]["current_value"] == "0.45"
    assert contract.object_spec["source_observation_id"] == contract.observation["observation_id"]


def test_contract_commit_failure_rolls_back_disk_and_live_parameters(tmp_path, monkeypatch):
    import openbrep.contracts.project_store as project_store

    project = _project(tmp_path)
    fingerprint = _adopt(project)
    before_source = (project.root / "paramlist.xml").read_bytes()
    before_contract = (project.root / ".openbrep/contracts/object_spec.json").read_bytes()
    original_atomic_write = project_store._atomic_write
    failed_once = False

    def fail_contract_once(path, data):
        nonlocal failed_once
        if path == project.root / ".openbrep/contracts/object_spec.json" and not failed_once:
            failed_once = True
            raise OSError("injected contract write failure")
        return original_atomic_write(path, data)

    monkeypatch.setattr(project_store, "_atomic_write", fail_contract_once)
    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=fingerprint,
        operations=[{"op": "set_value", "name": "depth", "value": 0.4}],
    )

    assert not result.ok and result.error_code == "SOURCE_SPEC_COMMIT_FAILED"
    assert (project.root / "paramlist.xml").read_bytes() == before_source
    assert (project.root / ".openbrep/contracts/object_spec.json").read_bytes() == before_contract
    assert project.get_parameter("depth").value == "0.3"
    assert load_project_contract(project.root).status == "fresh"


def test_stale_contract_fails_closed_until_re_adopted(tmp_path):
    project = _project(tmp_path)
    fingerprint = _adopt(project)
    script = project.root / "scripts" / "3d.gdl"
    script.write_text(script.read_text() + "\n! external change\n", encoding="utf-8")

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=compute_source_fingerprint(project.root),
        operations=[{"op": "set_value", "name": "depth", "value": 0.4}],
    )

    assert not result.ok and result.error_code == "CONTRACT_STALE"
    assert project.get_parameter("depth").value == "0.3"
    assert fingerprint != compute_source_fingerprint(project.root)


def test_cancelled_run_control_rejects_at_atomic_commit_boundary(tmp_path):
    project = _project(tmp_path)
    fingerprint = _adopt(project)
    source = (project.root / "paramlist.xml").read_bytes()
    control = RunControl(tool_budget=1, task_timeout=60, should_cancel=lambda: True)
    snapshots = []

    def commit_executor(mutation):
        decision, result = control.commit(mutation, stage="commit")
        if not decision.allowed:
            raise RuntimeError(f"RUN_CONTROL_REJECTED:{decision.reason}")
        return result

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=fingerprint,
        operations=[{"op": "set_value", "name": "depth", "value": 0.4}],
        before_commit=lambda: snapshots.append("snapshot"),
        commit_executor=commit_executor,
    )

    assert not result.ok and result.error_code == "COMMIT_REJECTED"
    assert (project.root / "paramlist.xml").read_bytes() == source
    assert project.get_parameter("depth").value == "0.3"
    assert snapshots == []


def test_rename_uses_transaction_and_rewrites_script_identifiers(tmp_path):
    project = _project(tmp_path)
    from openbrep.hsf_project import ScriptType

    project.set_script(ScriptType.SCRIPT_3D, "BLOCK width, depth, ZZYZX\nADD width\n")
    project.save_to_disk()
    fingerprint = compute_source_fingerprint(project.root)

    result = mutate_project_parameters(
        project,
        expected_source_fingerprint=fingerprint,
        operations=[{"op": "rename", "name": "width", "new_name": "cabinet_width"}],
    )

    assert result.ok, result.error
    assert result.changed_files == ["paramlist.xml", "scripts/3d.gdl"]
    assert project.get_parameter("cabinet_width") is not None
    assert project.get_parameter("width") is None
    script = project.get_script(ScriptType.SCRIPT_3D)
    assert "BLOCK cabinet_width, depth, ZZYZX" in script
    assert "ADD cabinet_width" in script
    reopened = HSFProject.load_from_disk(str(project.root))
    assert reopened.get_parameter("cabinet_width") is not None
