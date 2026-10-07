from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from openbrep.compiler import CompileResult
from openbrep.contracts.project_store import commit_project_state
from openbrep.hsf_project import HSFProject
from openbrep.revisions import create_revision, load_revision_protections
from openbrep.workbench import host_verification_service as host_verification_module
from openbrep.workbench.host_verification_service import HostVerificationService

FIXTURE = Path(__file__).parent / "fixtures" / "spiral_stair" / "after_top_option"


class FakeCompiler:
    def __init__(self, payload: bytes = b"CURRENT-GSM") -> None:
        self.payload = payload

    def hsf2libpart(self, hsf_dir: str, output_path: str) -> CompileResult:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(self.payload)
        return CompileResult(
            success=True,
            exit_code=0,
            stdout="compiled",
            stderr="",
            mode="lp",
            output_path=output_path,
        )


class FakeAdapter:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.on_call = None

    def verify_library_part_artifact(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_call:
            self.on_call()
        response = dict(self.response)
        response.setdefault("verificationProtocolVersion", 1)
        response.setdefault("capabilities", [
            "artifact_identity", "effective_parameters_readback", "scenario_context", "transaction_restore",
        ])
        response.setdefault("scenarioId", kwargs.get("scenario_id") or "")
        response.setdefault("specHash", kwargs.get("spec_hash"))
        response.setdefault("dependenciesHash", kwargs.get("dependencies_hash"))
        if response.get("effectiveParameters") == "__requested__":
            response["effectiveParameters"] = dict(kwargs.get("parameters") or {})
        return response


def _project(tmp_path: Path) -> HSFProject:
    root = tmp_path / "stair"
    shutil.copytree(FIXTURE, root)
    contract = root / ".openbrep" / "contracts" / "stair.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({
        "schema_version": 1,
        "type": "spiral_stair",
        "parameter_bindings": {
            "height": "height",
            "num_steps": "num_steps",
            "step_riser": "step_riser",
            "show_top_tread": "show_top_tread",
        },
        "constraints": {},
        "provenance": {"source": "test_profile"},
    }), encoding="utf-8")
    return HSFProject.load_from_disk(str(root))


def _host_response(gsm_sha: str, **overrides) -> dict:
    response = {
        "ok": True,
        "identityStatus": "verified",
        "verificationProtocolVersion": 1,
        "capabilities": [
            "artifact_identity", "effective_parameters_readback", "scenario_context", "transaction_restore",
        ],
        "loadedIdentity": {
            "name": "stair",
            "guid": "same-guid",
            "path": "/isolated/library/stair.gsm",
            "gsmSha256": gsm_sha,
        },
        "appliedParameters": ["height", "show_top_tread"],
        "skippedParameters": [],
        "effectiveParameters": "__requested__",
        "archicadVersion": "29.0",
        "addonVersion": "0.9.6",
        "libraryStateBefore": ["Archicad Library 29"],
        "libraryStateAfter": ["Archicad Library 29"],
        "libraryRestoreStatus": "restored",
        "elementCountBefore": 12,
        "elementCountAfter": 12,
        "undoStatus": "restored",
        "meshes": [{"name": "body", "vertices": [0, 0, 0], "faces": []}],
        "preview2d": {"lines": [{"from": [0, 0], "to": [1, 0]}]},
        "diagnostics": [],
    }
    response.update(overrides)
    return response


def _service(tmp_path: Path, adapter: FakeAdapter, *, compiler=None):
    project = _project(tmp_path)
    session = SimpleNamespace(
        project=project,
        project_epoch=3,
        session_id="session-test",
        converter_path="/fake/LP_XMLConverter",
        tapir=adapter,
    )
    record_ids = iter(("hv_test", "hv_test_2", "hv_test_3"))
    service = HostVerificationService(
        session,
        compiler_factory=lambda _path: compiler or FakeCompiler(),
        record_id_factory=lambda: next(record_ids),
        now_fn=lambda: "2026-09-20T00:00:00Z",
    )
    return service, session


def test_verified_artifact_parameters_and_host_evidence_pass(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)

    result = service.run({
        "parameters": {"height": 2.9, "show_top_tread": 1},
        "expected_project_epoch": 3,
        "run_id": "r_test",
    })

    record = result["verification"]
    assert result["ok"] is True
    assert record["status"] == "passed"
    assert record["source_fingerprint"].startswith("sha256:")
    assert record["contract_hash"].startswith("sha256:")
    assert record["gsm_sha256"] == gsm_sha
    assert record["loaded_identity"]["gsm_sha256"] == gsm_sha
    assert record["requested_parameters"]["HEIGHT"] == 2.9
    assert record["requested_parameters"]["SHOW_TOP_TREAD"] == 1
    assert "NUM_STEPS" in record["requested_parameters"]
    assert record["applied_parameters"] == ["height", "show_top_tread"]
    assert record["parameter_fingerprint"].startswith("sha256:")
    assert record["parameter_readback_status"] == "verified"
    assert record["effective_parameters"] == record["requested_parameters"]
    assert record["evidence_binding"]["evidence_kind"] == "host"
    assert record["evidence_binding"]["scene_refs"] == ["default"]
    assert record["evidence_binding"]["result_facts"]["executor_version"] == "1"
    assert record["archicad_version"] == "29.0"
    assert record["addon_version"] == "0.9.6"
    assert record["source_fingerprint_before_compile"] != ""
    assert record["source_fingerprint_after_compile"] != ""
    assert adapter.calls[0]["gsm_sha256"] == gsm_sha
    stored = session.project.root / ".openbrep" / "verification" / "host" / "hv_test.json"
    assert stored.is_file()


def test_same_name_or_guid_with_different_content_never_passes(tmp_path: Path) -> None:
    old_sha = hashlib.sha256(b"OLD-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(old_sha))
    service, _session = _service(tmp_path, adapter)

    result = service.run({"parameters": {"height": 2.9}})

    record = result["verification"]
    assert record["status"] == "identity_unverified"
    assert record["identity_status"] == "mismatch"
    assert record["gsm_sha256"] != record["loaded_identity"]["gsm_sha256"]


def test_skipped_required_parameter_fails_and_missing_versions_are_incomplete(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(
        gsm_sha,
        appliedParameters=["height"],
        skippedParameters=["show_top_tread"],
    ))
    service, _session = _service(tmp_path, adapter)
    skipped = service.run({"parameters": {"height": 2.9, "show_top_tread": 0}})
    assert skipped["verification"]["status"] == "failed"
    assert skipped["verification"]["skipped_parameters"] == ["show_top_tread"]

    adapter.response = _host_response(gsm_sha, archicadVersion="", addonVersion="")
    incomplete = service.run({"parameters": {"height": 2.9}})
    assert incomplete["verification"]["status"] == "identity_unverified"
    assert "host_version_missing" in incomplete["verification"]["diagnostics"]


def test_disconnect_and_unsupported_identity_do_not_fall_back_to_local_pass(tmp_path: Path) -> None:
    adapter = FakeAdapter({"ok": False, "error": "Archicad disconnected", "code": "disconnected"})
    service, _session = _service(tmp_path, adapter)
    disconnected = service.run({"parameters": {"height": 2.9}})
    assert disconnected["verification"]["status"] == "failed"

    adapter.response = {"ok": False, "error": "identity API unavailable", "code": "unsupported"}
    unsupported = service.run({"parameters": {"height": 2.9}})
    assert unsupported["verification"]["status"] == "unsupported"


def test_missing_or_incomplete_host_rollback_evidence_never_passes(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha, libraryRestoreStatus="unknown"))
    service, _session = _service(tmp_path, adapter)

    missing_restore = service.run({"parameters": {"height": 2.9}})

    assert missing_restore["verification"]["status"] == "failed"
    assert "host_state_not_restored" in missing_restore["verification"]["diagnostics"]

    adapter.response = _host_response(gsm_sha, elementCountAfter=13)
    changed_elements = service.run({"parameters": {"height": 2.9}})
    assert changed_elements["verification"]["status"] == "failed"
    assert "host_element_count_changed" in changed_elements["verification"]["diagnostics"]


def test_late_result_and_changed_inputs_are_stale(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    adapter.on_call = lambda: setattr(session, "project_epoch", 4)

    result = service.run({"parameters": {"height": 2.9}, "expected_project_epoch": 3})

    assert result["verification"]["status"] == "passed"
    assert result["current"] is False
    assert result["stale"] is True

    session.project_epoch = 3
    current = service.current({"parameters": {"height": 3.2}})
    assert current["status"] == "passed"
    assert current["stale"] is True
    assert "parameters_changed" in current["stale_reasons"]

    contract_path = session.project.root / ".openbrep" / "contracts" / "stair.json"
    contract_path.write_text(contract_path.read_text() + "\n", encoding="utf-8")
    changed_contract = service.current({"parameters": {"height": 2.9}})
    assert "contract_changed" in changed_contract["stale_reasons"]


def test_scenario_batch_runs_serially_with_separate_bindings_and_records(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)

    result = service.run({
        "scenarios": [
            {"scenario_id": "default", "scenario_kind": "default", "parameters": {"height": 2.5}},
            {"scenario_id": "upper-bound", "scenario_kind": "boundary", "parameters": {"height": 3.2}},
        ],
    })

    assert result["status"] == "passed"
    assert result["scenario_count"] == result["passed_count"] == 2
    assert [call["scenario_id"] for call in adapter.calls] == ["default", "upper-bound"]
    records = [item["verification"] for item in result["scenario_results"]]
    assert [record["scenario_id"] for record in records] == ["default", "upper-bound"]
    assert records[0]["scenario_hash"] != records[1]["scenario_hash"]
    assert all(record["evidence_binding"]["gsm_fingerprint"] == gsm_sha for record in records)
    assert all((session.project.root / ".openbrep" / "verification" / "host" / f"{record['record_id']}.json").is_file() for record in records)


def test_invalid_scenario_batch_is_rejected_before_any_host_call(tmp_path: Path) -> None:
    adapter = FakeAdapter({"ok": True})
    service, _session = _service(tmp_path, adapter)

    result = service.run({
        "scenarios": [
            {"scenario_id": "first", "parameters": {"height": 2.5}},
            {"scenario_id": "first", "parameters": {"height": 3.0}},
        ],
    })

    assert result["status"] == "not_checked"
    assert adapter.calls == []


def test_missing_or_wrong_effective_parameter_readback_cannot_pass(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha, effectiveParameters=None))
    service, _session = _service(tmp_path, adapter)

    missing = service.run({"parameters": {"height": 2.9}})
    assert missing["verification"]["status"] == "identity_unverified"
    assert "host_effective_parameters_unavailable" in missing["verification"]["diagnostics"]

    adapter.response = _host_response(gsm_sha, effectiveParameters={"HEIGHT": 2.8, "SHOW_TOP_TREAD": 1, "NUM_STEPS": 16})
    mismatch = service.run({"parameters": {"height": 2.9}})
    assert mismatch["verification"]["status"] == "failed"
    assert mismatch["verification"]["parameter_differences"]["HEIGHT"]["effective"] == 2.8


def test_legacy_addon_without_versioned_readback_protocol_is_unsupported(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha, verificationProtocolVersion=None, capabilities=[]))
    service, _session = _service(tmp_path, adapter)

    result = service.run({"parameters": {"height": 2.9}})

    assert result["verification"]["status"] == "unsupported"
    assert any(item.startswith("host_verification_protocol_unsupported:") for item in result["verification"]["diagnostics"])


def test_dependency_manifest_change_stales_previous_host_record(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    result = service.run({"parameters": {"height": 2.9}})
    assert result["verification"]["status"] == "passed"

    manifest = session.project.root / ".openbrep" / "dependencies" / "library-parts.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"parts": []}\n', encoding="utf-8")
    current = service.current({"parameters": {"height": 2.9}})
    assert "dependencies_changed" in current["stale_reasons"]


def test_adopted_object_spec_is_bound_and_spec_only_edit_stales_record(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    spec = {
        "schema_version": 1,
        "spec_id": "spec-stair-v1",
        "object_type": "spiral_stair",
        "params": [],
        "requirements": [],
        "relations": [],
    }
    assert commit_project_state(session.project, spec).ok

    verified = service.run({"parameters": {"height": 2.9}})
    record = verified["verification"]
    assert record["status"] == "passed"
    assert record["spec_id"] == "spec-stair-v1"
    assert record["spec_hash"].startswith("sha256:")

    contract_path = session.project.root / ".openbrep" / "contracts" / "object_spec.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["object_spec"]["object_type"] = "changed_only_in_spec"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    current = service.current({"parameters": {"height": 2.9}})
    assert "contract_changed" in current["stale_reasons"]


def test_record_write_failure_preserves_previous_record(tmp_path: Path, monkeypatch) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    first = service.run({"parameters": {"height": 2.9}})
    record_path = session.project.root / ".openbrep" / "verification" / "host" / "hv_test.json"
    artifact_path = session.project.root / ".openbrep" / "verification" / "host" / "artifacts" / "hv_test.gsm"
    before = record_path.read_bytes()
    artifact_before = artifact_path.read_bytes()
    service.compiler_factory = lambda _path: FakeCompiler(b"DIFFERENT-GSM")

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_write_record", fail_write)
    failed = service.run({"parameters": {"height": 3.2}})

    assert first["verification"]["evidence_save_status"] == "saved"
    assert failed["ok"] is False
    assert failed["verification"]["evidence_save_status"] == "failed"
    assert "求值完成" in failed["error"]
    assert record_path.read_bytes() == before
    assert artifact_path.read_bytes() == artifact_before


def test_snapshot_contains_every_st02_managed_source_file(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    (session.project.root / "custom-section.xml").write_text("<Custom />", encoding="utf-8")

    class InspectingCompiler(FakeCompiler):
        def hsf2libpart(self, hsf_dir: str, output_path: str) -> CompileResult:
            assert (Path(hsf_dir) / "custom-section.xml").read_text(encoding="utf-8") == "<Custom />"
            return super().hsf2libpart(hsf_dir, output_path)

    service.compiler_factory = lambda _path: InspectingCompiler()

    result = service.run({"parameters": {"height": 2.9}})

    assert result["verification"]["status"] == "passed"
    assert (
        result["verification"]["source_fingerprint_before_compile"]
        == result["verification"]["source_fingerprint_after_compile"]
    )


def test_snapshot_changed_while_copying_is_rejected_before_compile_or_host(
    tmp_path: Path,
    monkeypatch,
) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, _session = _service(tmp_path, adapter)
    original_copy = host_verification_module._copy_hsf_snapshot

    def copy_then_mutate(source: Path, target: Path) -> None:
        original_copy(source, target)
        (target / "scripts" / "3d.gdl").write_text("BLOCK 99, 99, 99\n", encoding="utf-8")

    monkeypatch.setattr(host_verification_module, "_copy_hsf_snapshot", copy_then_mutate)

    result = service.run({"parameters": {"height": 2.9}})

    record = result["verification"]
    assert record["status"] == "failed"
    assert "snapshot_source_mismatch" in record["diagnostics"]
    assert record["gsm_sha256"] is None
    assert adapter.calls == []


def test_matching_revision_is_registered_as_host_verification_protection(tmp_path: Path) -> None:
    gsm_sha = hashlib.sha256(b"CURRENT-GSM").hexdigest()
    adapter = FakeAdapter(_host_response(gsm_sha))
    service, session = _service(tmp_path, adapter)
    revision = create_revision(session.project.root, message="host acceptance source")

    result = service.run({"parameters": {"height": 2.9}, "run_id": "r_host"})

    record = result["verification"]
    assert record["status"] == "passed"
    assert record["revision"] == revision.revision_id
    protections = load_revision_protections(session.project.root)
    assert len(protections[revision.revision_id]) == 1
    protection = protections[revision.revision_id][0]
    assert protection["reason"] == "host_verification"
    assert protection["run_id"] == "hv_test"
    assert protection["ref"] == {
        "kind": "host_verification",
        "record_id": "hv_test",
        "run_id": "r_host",
    }
