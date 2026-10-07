"""Crash-recoverable persistence for adopted object specs beside HSF source.

The GDL source remains authoritative for parameter values.  This store persists
only the validated domain contract and binds it to the exact managed-source
fingerprint present at commit time.  Pending plans never belong here.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable

from openbrep.source_fingerprint import collect_managed_source_files, compute_source_fingerprint

CONTRACT_RELATIVE_PATH = ".openbrep/contracts/object_spec.json"
TRANSACTIONS_RELATIVE_PATH = ".openbrep/transactions"
CONTRACT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProjectContract:
    status: str  # missing | fresh | stale | invalid
    object_spec: dict[str, Any] | None = None
    source_fingerprint: str = ""
    current_source_fingerprint: str = ""
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitResult:
    ok: bool
    source_fingerprint: str = ""
    error: str = ""


def _root(value: Any) -> Path:
    candidate = value if isinstance(value, (str, Path)) else getattr(value, "root", value)
    path = Path(candidate).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"HSF project directory not found: {path}")
    return path


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _validate_spec(spec: dict[str, Any] | None) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if spec is None:
        return None, ()
    if hasattr(spec, "to_dict"):
        spec = spec.to_dict()
    if not isinstance(spec, dict):
        return None, ("object_spec must be a JSON object",)
    from openbrep.contracts.object_spec import parse_object_spec

    parsed = parse_object_spec(spec)
    if not parsed.ok:
        return None, tuple(f"{e.field_path}: {e.code}" for e in parsed.errors)
    return parsed.value.to_dict(), ()


def _manifest_write(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, _json_bytes(data))


def _restore_transaction(root: Path, txn_dir: Path, manifest: dict[str, Any]) -> None:
    source_files = manifest.get("source_files")
    if not isinstance(source_files, list) or any(not isinstance(name, str) for name in source_files):
        raise ValueError(f"Invalid source file manifest in transaction {txn_dir.name}")
    for name in source_files:
        rel = PurePosixPath(name)
        if (rel.is_absolute() or ".." in rel.parts or not rel.parts
                or not (len(rel.parts) == 1 and rel.suffix.lower() == ".xml"
                        or len(rel.parts) >= 2 and rel.parts[0] == "scripts")):
            raise ValueError(f"Unsafe source path in transaction {txn_dir.name}: {name!r}")
        if not (root / Path(*rel.parts)).resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Transaction source path escapes project root: {name!r}")

    # Remove the current managed set first, then restore the saved generation.
    for rel_path in collect_managed_source_files(root):
        path = root / rel_path
        if path.is_file():
            path.unlink()
    backup = txn_dir / "backup"
    for rel_path in source_files:
        source = backup / "source" / rel_path
        if not source.is_file():
            raise FileNotFoundError(f"Transaction backup is incomplete: {source}")
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    contract = root / CONTRACT_RELATIVE_PATH
    if manifest.get("contract_existed"):
        saved = backup / "contract.json"
        if not saved.is_file():
            raise FileNotFoundError(f"Transaction contract backup is incomplete: {saved}")
        _atomic_write(contract, saved.read_bytes())
    else:
        contract.unlink(missing_ok=True)


def recover_project_state(project_root: str | Path) -> bool:
    """Recover interrupted source/spec commits; return whether anything changed."""
    root = _root(project_root)
    transactions = root / TRANSACTIONS_RELATIVE_PATH
    if not transactions.is_dir():
        return False
    recovered = False
    for txn_dir in sorted(transactions.glob("source-spec-*")):
        if not txn_dir.is_dir():
            continue
        manifest_path = txn_dir / "manifest.json"
        if not manifest_path.is_file():
            # A journal is published before source writes. An incomplete initial
            # journal therefore has made no project changes and is safe to drop.
            shutil.rmtree(txn_dir)
            recovered = True
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot recover source/spec transaction {txn_dir.name}: {exc}") from exc
        if manifest.get("state") != "committed":
            _restore_transaction(root, txn_dir, manifest)
            recovered = True
        shutil.rmtree(txn_dir)
    return recovered


def commit_project_state(
    project: Any,
    object_spec: dict[str, Any] | Any | None,
    *,
    source_writer: Callable[[], Any] | None = None,
) -> CommitResult:
    """Commit saved HSF source and its adopted ObjectSpec as one recoverable unit.

    ``object_spec=None`` explicitly removes the adopted contract (used when a
    revision predates contracts). The source writer defaults to HSFProject's
    regular serializer. A killed process leaves a recoverable journal.
    """
    candidate = project if isinstance(project, (str, Path)) else getattr(project, "root", project)
    root = Path(candidate).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    validated, errors = _validate_spec(object_spec)
    if errors:
        return CommitResult(False, error="Invalid object spec: " + "; ".join(errors))

    recover_project_state(root)
    txn_id = f"source-spec-{uuid.uuid4().hex}"
    txn_dir = root / TRANSACTIONS_RELATIVE_PATH / txn_id
    backup = txn_dir / "backup"
    files = collect_managed_source_files(root)
    contract_path = root / CONTRACT_RELATIVE_PATH
    txn_dir.mkdir(parents=True, exist_ok=False)
    for rel_path in files:
        src = root / rel_path
        dst = backup / "source" / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if contract_path.is_file():
        shutil.copy2(contract_path, backup / "contract.json")
    manifest = {
        "schema_version": 1,
        "state": "prepared",
        "source_files": files,
        "contract_existed": contract_path.is_file(),
    }
    _manifest_write(txn_dir / "manifest.json", manifest)

    try:
        if source_writer is not None:
            source_writer()
        elif callable(getattr(project, "save_to_disk", None)):
            project.save_to_disk()
        else:
            raise TypeError("source_writer is required when project has no save_to_disk()")

        source_fingerprint = compute_source_fingerprint(root)
        if validated is None:
            contract_path.unlink(missing_ok=True)
        else:
            payload = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "source_fingerprint": source_fingerprint,
                "object_spec": validated,
            }
            _atomic_write(contract_path, _json_bytes(payload))
        manifest["state"] = "committed"
        manifest["source_fingerprint"] = source_fingerprint
        _manifest_write(txn_dir / "manifest.json", manifest)
        shutil.rmtree(txn_dir)
        return CommitResult(True, source_fingerprint=source_fingerprint)
    except Exception as exc:
        try:
            _restore_transaction(root, txn_dir, manifest)
            shutil.rmtree(txn_dir)
        except Exception as recovery_exc:
            return CommitResult(False, error=f"{exc}; rollback needs recovery: {recovery_exc}")
        return CommitResult(False, error=str(exc))


def load_project_contract(project_root: str | Path) -> ProjectContract:
    """Read the adopted spec, report freshness, and recover pending commits first."""
    root = _root(project_root)
    recover_project_state(root)
    path = root / CONTRACT_RELATIVE_PATH
    if not path.is_file():
        return ProjectContract("missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ProjectContract("invalid", errors=(str(exc),))
    if not isinstance(payload, dict) or payload.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        return ProjectContract("invalid", errors=("unsupported contract schema",))
    spec = payload.get("object_spec")
    validated, errors = _validate_spec(spec)
    if errors:
        return ProjectContract("invalid", errors=errors)
    current = compute_source_fingerprint(root)
    bound = str(payload.get("source_fingerprint") or "")
    status = "fresh" if bound and bound == current else "stale"
    return ProjectContract(status, validated, bound, current)
