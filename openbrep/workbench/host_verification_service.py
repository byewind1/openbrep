from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openbrep.compiler import HSFCompiler
from openbrep.source_fingerprint import (
    collect_managed_source_files,
    compute_revision_fingerprint,
    compute_source_fingerprint,
    fingerprints_equal,
)
from openbrep.workbench.project_parameter_service import parameter_values


HOST_RECORD_DIR = Path(".openbrep/verification/host")


class _SnapshotSourceMismatch(RuntimeError):
    pass


class HostVerificationService:
    """Bind one saved HSF source snapshot to the exact GSM evaluated by a host."""

    def __init__(
        self,
        session: Any,
        *,
        compiler_factory: Callable[[str], Any] | None = None,
        record_id_factory: Callable[[], str] | None = None,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.session = session
        self.compiler_factory = compiler_factory or (lambda converter_path: HSFCompiler(converter_path))
        self.record_id_factory = record_id_factory or (
            lambda: f"hv_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        self.now_fn = now_fn or (
            lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )

    def run(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        project = getattr(self.session, "project", None)
        if project is None:
            return {"ok": False, "error": "当前没有项目", "status": "not_checked"}
        converter_path = str(getattr(self.session, "converter_path", "") or "")
        if not converter_path:
            return {"ok": False, "error": "未配置 LP_XMLConverter", "status": "unsupported"}

        started_at = self.now_fn()
        record_id = self.record_id_factory()
        start_epoch = int(getattr(self.session, "project_epoch", 0))
        expected_epoch = body.get("expected_project_epoch")
        if expected_epoch is not None and int(expected_epoch) != start_epoch:
            return {"ok": False, "error": "项目代次已变化", "status": "not_checked", "stale": True}

        source_fingerprint = compute_source_fingerprint(project.root)
        expected_source = str(body.get("expected_source_fingerprint") or "")
        if expected_source and not fingerprints_equal(expected_source, source_fingerprint):
            return {"ok": False, "error": "源码指纹已变化", "status": "not_checked", "stale": True}

        overrides = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
        requested_parameters = parameter_values(project, overrides)
        required_names = {str(name).lower() for name in overrides}
        parameter_fingerprint = _json_fingerprint(requested_parameters)
        contract_hash = _file_hash(project.root / ".openbrep" / "contracts" / "stair.json")
        revision_id = _matching_revision(project.root, source_fingerprint)
        record_root = project.root / HOST_RECORD_DIR
        artifact_path = record_root / "artifacts" / f"{record_id}.gsm"
        record_path = record_root / f"{record_id}.json"
        diagnostics: list[str] = []
        artifact_created = False
        revision_protected = False

        record: dict[str, Any] = {
            "schema_version": 1,
            "record_id": record_id,
            "run_id": body.get("run_id") or None,
            "revision": revision_id,
            "source_fingerprint": source_fingerprint,
            "source_fingerprint_before_compile": source_fingerprint,
            "source_fingerprint_after_compile": None,
            "contract_hash": contract_hash,
            "gsm_sha256": None,
            "loaded_identity": None,
            "identity_status": "unverified",
            "requested_parameters": requested_parameters,
            "applied_parameters": [],
            "skipped_parameters": [],
            "parameter_fingerprint": parameter_fingerprint,
            "archicad_version": None,
            "addon_version": None,
            "preview_3d": None,
            "preview_2d": None,
            "host_transaction": None,
            "diagnostics": diagnostics,
            "converter": {"path": converter_path, "mode": "lp"},
            "started_at": started_at,
            "finished_at": None,
            "status": "failed",
            "evidence_save_status": "pending",
        }

        try:
            if record_path.exists() or artifact_path.exists():
                raise FileExistsError(f"host verification evidence already exists: {record_id}")
            with tempfile.TemporaryDirectory(prefix="openbrep_host_verify_") as tmpdir:
                snapshot_root = Path(tmpdir) / project.root.name
                _copy_hsf_snapshot(project.root, snapshot_root)
                snapshot_fingerprint = compute_source_fingerprint(snapshot_root)
                record["source_fingerprint_before_compile"] = snapshot_fingerprint
                if not fingerprints_equal(snapshot_fingerprint, source_fingerprint):
                    raise _SnapshotSourceMismatch
                gsm_path = Path(tmpdir) / f"{project.name}.gsm"
                compiler = self.compiler_factory(converter_path)
                compile_result = compiler.hsf2libpart(str(snapshot_root), str(gsm_path))
                record["converter"].update({
                    "mode": str(getattr(compile_result, "mode", "lp") or "lp"),
                    "exit_code": getattr(compile_result, "exit_code", None),
                })
                record["source_fingerprint_after_compile"] = compute_source_fingerprint(snapshot_root)
                if not compile_result.success or not gsm_path.is_file():
                    diagnostics.append(str(compile_result.stderr or compile_result.stdout or "编译失败"))
                else:
                    gsm_sha = hashlib.sha256(gsm_path.read_bytes()).hexdigest()
                    record["gsm_sha256"] = gsm_sha
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(gsm_path, artifact_path)
                    artifact_created = True
                    host = self._verify_host(
                        artifact_path=artifact_path,
                        gsm_sha256=gsm_sha,
                        project=project,
                        parameters=requested_parameters,
                    )
                    _apply_host_result(record, host, required_names, diagnostics)
        except _SnapshotSourceMismatch:
            diagnostics.append("snapshot_source_mismatch")
            record["status"] = "failed"
        except Exception as exc:  # noqa: BLE001 - evidence must be returned, not raised
            diagnostics.append(f"host_verification_exception:{exc}")
            record["status"] = "failed"

        record["finished_at"] = self.now_fn()
        if record["status"] == "passed" and revision_id:
            try:
                from openbrep.revisions import register_revision_protection

                register_revision_protection(
                    project.root,
                    revision_id,
                    reason="host_verification",
                    run_id=record_id,
                    ref={
                        "kind": "host_verification",
                        "record_id": record_id,
                        "run_id": record["run_id"],
                    },
                )
                revision_protected = True
            except Exception as exc:  # noqa: BLE001 - incomplete evidence cannot pass
                diagnostics.append(f"revision_protection_failed:{exc}")
                record["status"] = "failed"
        record["evidence_save_status"] = "saved"
        try:
            self._write_record(record)
        except Exception as exc:  # noqa: BLE001 - preserve completed host evidence in response
            if artifact_created:
                artifact_path.unlink(missing_ok=True)
            if revision_protected and revision_id:
                try:
                    from openbrep.revisions import unregister_revision_protection

                    unregister_revision_protection(
                        project.root,
                        revision_id,
                        reason="host_verification",
                        run_id=record_id,
                    )
                except Exception:
                    pass
            record["evidence_save_status"] = "failed"
            record["diagnostics"] = [*diagnostics, f"evidence_save_failed:{exc}"]
            return {
                "ok": False,
                "error": f"求值完成，证据保存失败：{exc}",
                "verification": record,
                "current": False,
                "stale": True,
            }

        current = (
            getattr(self.session, "project", None) is project
            and int(getattr(self.session, "project_epoch", 0)) == start_epoch
            and fingerprints_equal(compute_source_fingerprint(project.root), source_fingerprint)
        )
        return {
            "ok": True,
            "verification": record,
            "current": current,
            "stale": not current,
        }

    def current(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        project = getattr(self.session, "project", None)
        if project is None:
            return {"status": "not_checked", "stale": False, "stale_reasons": []}
        records = sorted((project.root / HOST_RECORD_DIR).glob("*.json"))
        if not records:
            return {"status": "not_checked", "stale": False, "stale_reasons": []}
        try:
            record = json.loads(records[-1].read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "status": "not_checked",
                "stale": True,
                "stale_reasons": [f"record_unreadable:{exc}"],
            }
        reasons: list[str] = []
        if not fingerprints_equal(
            str(record.get("source_fingerprint") or ""),
            compute_source_fingerprint(project.root),
        ):
            reasons.append("source_changed")
        current_contract = _file_hash(project.root / ".openbrep" / "contracts" / "stair.json")
        if record.get("contract_hash") != current_contract:
            reasons.append("contract_changed")
        overrides = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
        current_parameters = parameter_values(project, overrides)
        if record.get("parameter_fingerprint") != _json_fingerprint(current_parameters):
            reasons.append("parameters_changed")
        return {**record, "stale": bool(reasons), "stale_reasons": reasons}

    def _verify_host(self, **kwargs: Any) -> dict[str, Any]:
        adapter = getattr(self.session, "tapir", None)
        verify = getattr(adapter, "verify_library_part_artifact", None)
        if not callable(verify):
            return {"ok": False, "code": "unsupported", "error": "host identity API unavailable"}
        return verify(
            gsm_path=str(kwargs["artifact_path"]),
            gsm_sha256=kwargs["gsm_sha256"],
            lib_part_name=kwargs["project"].name,
            lib_part_guid=kwargs["project"].guid,
            parameters=kwargs["parameters"],
            want=["identity", "mesh3d", "prims2d"],
        )

    def _write_record(self, record: dict[str, Any]) -> None:
        root = self.session.project.root / HOST_RECORD_DIR
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{record['record_id']}.json"
        if target.exists():
            raise FileExistsError(f"host record already exists: {record['record_id']}")
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()


def _apply_host_result(
    record: dict[str, Any],
    host: dict[str, Any],
    required_names: set[str],
    diagnostics: list[str],
) -> None:
    if not host.get("ok"):
        code = str(host.get("code") or "")
        record["status"] = "unsupported" if code == "unsupported" else "failed"
        diagnostics.append(str(host.get("error") or "host verification failed"))
        return
    identity = host.get("loadedIdentity") if isinstance(host.get("loadedIdentity"), dict) else {}
    loaded_hash = str(identity.get("gsmSha256") or "")
    expected_hash = str(record.get("gsm_sha256") or "")
    record["loaded_identity"] = {
        "name": identity.get("name") or None,
        "guid": identity.get("guid") or None,
        "path": identity.get("path") or None,
        "gsm_sha256": loaded_hash or None,
    }
    identity_verified = host.get("identityStatus") == "verified" and loaded_hash == expected_hash
    record["identity_status"] = "verified" if identity_verified else "mismatch"
    record["applied_parameters"] = _name_list(host.get("appliedParameters"))
    record["skipped_parameters"] = _name_list(host.get("skippedParameters"))
    record["archicad_version"] = str(host.get("archicadVersion") or "") or None
    record["addon_version"] = str(host.get("addonVersion") or "") or None
    record["preview_3d"] = {"meshes": host.get("meshes") or []}
    record["preview_2d"] = host.get("preview2d") if isinstance(host.get("preview2d"), dict) else None
    library_before = host.get("libraryStateBefore")
    library_after = host.get("libraryStateAfter")
    element_before = host.get("elementCountBefore")
    element_after = host.get("elementCountAfter")
    record["host_transaction"] = {
        "library_state_before": list(library_before) if isinstance(library_before, list) else None,
        "library_state_after": list(library_after) if isinstance(library_after, list) else None,
        "library_restore_status": str(host.get("libraryRestoreStatus") or "") or None,
        "element_count_before": element_before if isinstance(element_before, int) else None,
        "element_count_after": element_after if isinstance(element_after, int) else None,
        "undo_status": str(host.get("undoStatus") or "") or None,
    }
    diagnostics.extend(str(item) for item in (host.get("diagnostics") or []))
    skipped_required = required_names & {name.lower() for name in record["skipped_parameters"]}
    libraries_restored = (
        record["host_transaction"]["library_restore_status"] == "restored"
        and record["host_transaction"]["library_state_before"] is not None
        and record["host_transaction"]["library_state_before"]
        == record["host_transaction"]["library_state_after"]
    )
    element_counts_restored = (
        record["host_transaction"]["element_count_before"] is not None
        and record["host_transaction"]["element_count_before"]
        == record["host_transaction"]["element_count_after"]
    )
    undo_restored = record["host_transaction"]["undo_status"] in {"restored", "not_needed"}
    if skipped_required:
        diagnostics.append("required_parameters_skipped:" + ",".join(sorted(skipped_required)))
        record["status"] = "failed"
    elif not libraries_restored:
        diagnostics.append("host_state_not_restored")
        record["status"] = "failed"
    elif not element_counts_restored:
        diagnostics.append("host_element_count_changed")
        record["status"] = "failed"
    elif not undo_restored:
        diagnostics.append("host_undo_not_restored")
        record["status"] = "failed"
    elif not identity_verified:
        record["status"] = "identity_unverified"
    elif not record["archicad_version"] or not record["addon_version"]:
        diagnostics.append("host_version_missing")
        record["status"] = "identity_unverified"
    elif not (record["preview_3d"]["meshes"] and _has_2d(record["preview_2d"])):
        diagnostics.append("host_geometry_missing")
        record["status"] = "failed"
    else:
        record["status"] = "passed"


def _copy_hsf_snapshot(source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for rel_path in collect_managed_source_files(source):
        src = source / rel_path
        dst = target / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _file_hash(path: Path) -> str | None:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}" if path.is_file() else None


def _json_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _name_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item.get("name") if isinstance(item, dict) else item) for item in value]


def _has_2d(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(value.get(key) for key in ("lines", "polygons", "arcs", "circles", "texts"))


def _matching_revision(project_root: Path, source_fingerprint: str) -> str | None:
    from openbrep.revisions import list_revisions

    for revision in reversed(list_revisions(project_root)):
        try:
            if fingerprints_equal(compute_revision_fingerprint(revision.path), source_fingerprint):
                return revision.revision_id
        except Exception:
            continue
    return None
