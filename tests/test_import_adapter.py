import json

from openbrep.contracts.import_adapter import build_import_candidate
from openbrep.contracts.project_store import CONTRACT_RELATIVE_PATH, load_project_contract
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench_api import WorkbenchSession


def _imported_project(tmp_path):
    project = HSFProject.create_new("Imported_Cabinet", work_dir=str(tmp_path))
    project.add_parameter(GDLParameter(
        name="door_angle", type_tag="Angle", value="45", description="Door leaf angle",
    ))
    project.called_macros = [("handle_macro", "guid-handle")]
    project.subtype_guids = ["subtype-guid"]
    project.save_to_disk()
    return project


def test_import_candidate_contains_source_facts_without_domain_inference(tmp_path):
    project = _imported_project(tmp_path)

    candidate = build_import_candidate(project)
    spec = candidate["candidate_spec"]
    door_angle = next(item for item in spec["params"] if item["gdl_name"] == "door_angle")
    observation = candidate["observation"]
    angle_fact = next(item for item in observation["items"] if item["field_path"] == "source.parameters.door_angle")

    assert candidate["status"] == "proposed"
    assert door_angle["type"] == "Angle" and door_angle["unit"] == "deg"
    assert door_angle["default_value"] is None
    assert angle_fact["value"]["current_value"] == "45"
    assert any(item["field_path"] == "source.called_macros" for item in observation["items"])
    assert any("尚未由领域 Skill 证明" in warning for warning in candidate["warnings"])
    assert spec["requirements"] == [] and spec["relations"] == []
    assert load_project_contract(project.root).status == "missing"
    assert not (project.root / CONTRACT_RELATIVE_PATH).exists()


def test_explicit_adoption_is_source_bound_and_stale_candidate_is_rejected(tmp_path):
    project = _imported_project(tmp_path)
    session = WorkbenchSession(config_path=tmp_path / "config.toml", tapir_import_ok=False)
    session.project = project
    session.source = "hsf"
    session.source_path = project.root

    before_fingerprint = compute_source_fingerprint(project.root)
    candidate = session.snapshot()["import_contract_candidate"]
    response = session.route("POST", "/api/project/object-contract/adopt-import-candidate", {
        "expected_source_fingerprint": candidate["source_fingerprint"],
        "candidate_hash": candidate["candidate_hash"],
    })

    assert response["ok"] is True
    assert response["object_contract"]["status"] == "fresh"
    assert compute_source_fingerprint(project.root) == before_fingerprint
    adopted_spec = json.loads((project.root / CONTRACT_RELATIVE_PATH).read_text())
    assert adopted_spec["object_spec"]["params"]
    assert adopted_spec["observation"]["observation_id"] == adopted_spec["object_spec"]["source_observation_id"]
    assert response["object_contract"]["observation"]["observation_id"] == adopted_spec["observation"]["observation_id"]
    reopened_contract = load_project_contract(project.root)
    assert reopened_contract.observation["observation_id"] == adopted_spec["observation"]["observation_id"]

    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 0.1\n")
    project.save_to_disk()
    stale = session.route("POST", "/api/project/object-contract/adopt-import-candidate", {
        "expected_source_fingerprint": candidate["source_fingerprint"],
        "candidate_hash": candidate["candidate_hash"],
    })

    assert stale["ok"] is False and stale["code"] == "SOURCE_CHANGED"
    assert load_project_contract(project.root).status == "stale"
