import json

import pytest

from openbrep.contracts.project_store import (
    CONTRACT_RELATIVE_PATH,
    commit_project_source_state,
    commit_project_state,
    load_project_contract,
    recover_project_state,
)
from openbrep.hsf_project import HSFProject
from openbrep.revisions import copy_project_metadata, create_revision, restore_revision
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.source_snapshot import capture_snapshot
from openbrep.workbench.project_session_service import project_to_snapshot


def _spec(spec_id="shelf-v1"):
    return {
        "schema_version": 1,
        "spec_id": spec_id,
        "object_type": "shelf",
        "params": [],
        "requirements": [],
        "relations": [],
    }


def test_committed_contract_is_bound_to_saved_source_and_survives_reopen(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))

    result = commit_project_state(project, _spec())

    assert result.ok
    saved = load_project_contract(project.root)
    assert saved.status == "fresh"
    assert saved.object_spec["spec_id"] == "shelf-v1"
    assert saved.source_fingerprint == compute_source_fingerprint(project.root)
    assert (project.root / CONTRACT_RELATIVE_PATH).is_file()


def test_normal_source_save_makes_contract_explicitly_stale(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec()).ok

    project.set_script(next(iter(project.scripts)), "BLOCK 2, 2, 2\n")
    project.save_to_disk()

    assert load_project_contract(project.root).status == "stale"


def test_coordinated_manual_source_edit_preserves_contract_as_stale(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec()).ok
    contract_before = (project.root / CONTRACT_RELATIVE_PATH).read_bytes()
    candidate = HSFProject.load_from_disk(str(project.root))
    candidate.set_script(next(iter(candidate.scripts)), "BLOCK 4, 4, 4\n")

    result = commit_project_source_state(candidate)

    assert result.ok
    assert (project.root / CONTRACT_RELATIVE_PATH).read_bytes() == contract_before
    assert load_project_contract(project.root).status == "stale"


def test_coordinated_source_edit_failure_restores_source_and_contract(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec()).ok
    contract_before = (project.root / CONTRACT_RELATIVE_PATH).read_bytes()
    source_before = (project.root / "scripts/3d.gdl").read_bytes()

    def partial_writer():
        (project.root / "scripts/3d.gdl").write_text("PARTIAL\n", encoding="utf-8")
        raise OSError("simulated source failure")

    result = commit_project_source_state(project, source_writer=partial_writer)

    assert not result.ok
    assert (project.root / "scripts/3d.gdl").read_bytes() == source_before
    assert (project.root / CONTRACT_RELATIVE_PATH).read_bytes() == contract_before
    assert load_project_contract(project.root).status == "fresh"


def test_failed_source_writer_rolls_back_source_and_contract(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec("old")).ok
    old_source = (project.root / "scripts/3d.gdl").read_bytes()

    def failing_writer():
        (project.root / "scripts/3d.gdl").write_text("BROKEN PARTIAL\n")
        raise OSError("simulated save failure")

    result = commit_project_state(project, _spec("new"), source_writer=failing_writer)

    assert not result.ok
    assert (project.root / "scripts/3d.gdl").read_bytes() == old_source
    assert load_project_contract(project.root).object_spec["spec_id"] == "old"
    assert not list((project.root / ".openbrep/transactions").glob("source-spec-*"))


def test_interrupted_commit_recovers_before_loading_contract(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec("old")).ok

    def interrupted_writer():
        (project.root / "scripts/3d.gdl").write_text("PARTIAL\n")
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        commit_project_state(project, _spec("new"), source_writer=interrupted_writer)

    reopened = HSFProject.load_from_disk(str(project.root))
    assert reopened.name == "Shelf"
    assert recover_project_state(project.root) is False
    assert load_project_contract(project.root).object_spec["spec_id"] == "old"
    assert load_project_contract(project.root).status == "fresh"


def test_corrupt_or_unknown_contract_degrades_without_failing_project_open(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    project.save_to_disk()
    path = project.root / CONTRACT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")

    assert load_project_contract(project.root).status == "invalid"
    assert HSFProject.load_from_disk(str(project.root)).name == "Shelf"


def test_contract_change_invalidates_source_snapshot(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    assert commit_project_state(project, _spec("first")).ok
    snapshot = capture_snapshot(project, 1)

    assert commit_project_state(project, _spec("second")).ok

    assert not snapshot.matches(project, 1)


def test_revision_restore_restores_matching_spec_and_legacy_revision_clears_it(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    project.save_to_disk()
    assert commit_project_state(project, _spec("first")).ok
    first = create_revision(project.root, "first with contract")

    project.set_script(next(iter(project.scripts)), "BLOCK 2, 2, 2\n")
    assert commit_project_state(project, _spec("second")).ok
    second = create_revision(project.root, "second with contract")

    restore_revision(project.root, first.revision_id)
    restored = load_project_contract(project.root)
    assert restored.status == "fresh"
    assert restored.object_spec["spec_id"] == "first"

    # A pre-contract revision has no object_spec_file field. Restoring it must
    # remove, rather than keep, a contract adopted by a newer revision.
    legacy = second.path.parent / "r9999"
    legacy.mkdir()
    for rel in second.files:
        src = second.path / rel
        dst = legacy / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    manifest = json.loads((second.path / "manifest.json").read_text(encoding="utf-8"))
    manifest["revision_id"] = "r9999"
    manifest.pop("object_spec_file", None)
    (legacy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    restore_revision(project.root, "r9999")
    assert load_project_contract(project.root).status == "missing"


def test_project_snapshot_exposes_contract_freshness_without_blocking_open(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path))
    project.save_to_disk()
    assert commit_project_state(project, _spec()).ok

    assert project_to_snapshot(project)["object_contract"]["status"] == "fresh"
    project.set_script(next(iter(project.scripts)), "BLOCK 9, 9, 9\n")
    project.save_to_disk()
    snapshot = project_to_snapshot(project)
    assert snapshot["object_contract"]["status"] == "stale"
    assert snapshot["project"]["name"] == "Shelf"


def test_save_as_metadata_copy_keeps_contract_bound_when_source_is_identical(tmp_path):
    project = HSFProject.create_new("Shelf", str(tmp_path / "source"))
    project.save_to_disk()
    assert commit_project_state(project, _spec()).ok
    target = tmp_path / "target" / "Shelf Copy"
    copied = HSFProject.load_from_disk(str(project.root))
    copied.name = "Shelf Copy"
    copied.work_dir = str(target.parent)
    copied.root = target
    copied.save_to_disk()

    assert copy_project_metadata(project.root, target)
    assert load_project_contract(target).status == "fresh"
