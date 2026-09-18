"""Project-level revision snapshots for HSF source directories."""

from __future__ import annotations

import json
import shutil
from difflib import unified_diff
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REVISION_SCHEMA_VERSION = 1
OPENBREP_DIR = ".openbrep"
REVISIONS_DIR = "revisions"
LATEST_FILE = "latest"
ARTIFACTS_DIR = "artifacts"
# ST02：受保护 revision 引用集合（统一引用接口，不扫描任意文本推断）
PROTECTIONS_FILE = "revision_protections.json"


@dataclass(frozen=True)
class Revision:
    """Metadata for a project revision snapshot."""

    revision_id: str
    project_name: str
    gsm_name: str
    created_at: str
    message: str
    files: list[str]
    path: Path
    trigger: str = "manual"
    intent: str = ""
    user_instruction: str = ""
    changed_files: list[str] | None = None
    parent_revision_id: str | None = None
    compile: dict[str, Any] | None = None
    explanation: str = ""
    compile_comparison: dict[str, Any] | None = None


def create_revision(
    project_dir: str | Path,
    message: str = "",
    gsm_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    trigger: str = "manual",
    intent: str = "",
    user_instruction: str = "",
    changed_files: list[str] | None = None,
    parent_revision_id: str | None = None,
) -> Revision:
    """Create a new snapshot under ``<project>/.openbrep/revisions``."""
    root = _resolve_project_root(project_dir)
    revision_id = _next_revision_id(root)
    if parent_revision_id is None:
        parent_revision_id = get_latest_revision_id(root)
    revision_dir = _revisions_root(root) / revision_id
    tmp_dir = revision_dir.with_name(f".{revision_id}.tmp")

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    files = _collect_source_files(root)
    if not files:
        shutil.rmtree(tmp_dir)
        raise ValueError(f"No versionable HSF source files found in {root}")

    for rel_path in files:
        src = root / rel_path
        dst = tmp_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    extra_metadata = metadata or {}
    compile_metadata = extra_metadata.get("compile") or {}
    manifest = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "schema": REVISION_SCHEMA_VERSION,
        "revision_id": revision_id,
        "project_name": root.name,
        "gsm_name": (gsm_name or root.name),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "source_format": "hsf-project",
        "files": files,
        "trigger": trigger,
        "intent": intent,
        "user_instruction": user_instruction,
        "changed_files": list(changed_files or []),
        "parent_revision_id": parent_revision_id,
        "compile": {
            "mode": compile_metadata.get("mode"),
            "success": compile_metadata.get("success"),
            "gsm_size_bytes": compile_metadata.get("gsm_size_bytes"),
            "gsm_path": compile_metadata.get("gsm_path"),
            "parameter_count": compile_metadata.get("parameter_count"),
            "exit_code": compile_metadata.get("exit_code"),
        },
        "explanation": extra_metadata.get("explanation", ""),
        "compile_comparison": extra_metadata.get("compile_comparison"),
        "metadata": extra_metadata,
        # P5d-1（设计 D7）：manifest 记录本项目当前全部 extraction 哈希
        # （简化口径：当前全部，不按 revision 快照回放；提取文件本身在
        # .openbrep/vision/ 下，属项目元数据，不进 revision 源码快照）。
        "vision_extractions": _vision_extraction_hashes(root),
    }
    # 成品归档：编译成功且产物存在时，把 .gsm 拷入 artifacts/<revision_id>/ 并让
    # manifest 的 gsm_path 指向归档路径（修复旧版指向临时目录、运行后悬空的问题）。
    # 归档失败只保持原路径，不阻断快照创建。
    raw_gsm_path = compile_metadata.get("gsm_path")
    if compile_metadata.get("success") and raw_gsm_path:
        try:
            archive_path = archive_artifact(root, raw_gsm_path, revision_id=revision_id)
            manifest["compile"]["gsm_path"] = str(archive_path)
        except Exception as _archive_exc:
            import logging as _logging
            _logging.getLogger(__name__).debug(
                "Archive artifact failed, keeping raw gsm_path: %s", _archive_exc
            )
    _write_json(tmp_dir / "manifest.json", manifest)
    _write_explanation_markdown(tmp_dir, manifest)
    tmp_dir.rename(revision_dir)
    _write_latest(root, revision_id)

    # Auto-prune: keep disk usage bounded（[revisions] keep_last_n 可配置，0 = 禁用；
    # 非阻塞：失败只记日志不上抛）。受保护引用不自动删。
    keep_last_n = _auto_prune_keep_last_n()
    prune_warnings: list[str] = []
    if keep_last_n > 0:
        try:
            # The new revision's parent is the delivery ``before`` snapshot.
            # Keep it through this prune pass; the delivery finalizer registers
            # the durable current-pair protections immediately afterwards.
            prune_result = prune_revisions(
                root,
                keep_last_n=keep_last_n,
                extra_protected_ids={parent_revision_id} if parent_revision_id else None,
            )
            prune_warnings = list(getattr(prune_result, "warnings", None) or [])
        except Exception as _prune_exc:
            import logging as _logging
            _logging.getLogger(__name__).debug("Auto-prune revisions failed: %s", _prune_exc)
    if prune_warnings:
        # manifest metadata 附带 prune 报告，便于验收/调试；不阻塞创建
        manifest.setdefault("metadata", {})
        if isinstance(manifest["metadata"], dict):
            manifest["metadata"]["prune_warnings"] = prune_warnings
        # The first manifest write happens before pruning.  Persist the report
        # added above so callers and later diagnostics see the same metadata as
        # the returned Revision object.
        _write_json(revision_dir / "manifest.json", manifest)

    return _revision_from_manifest(revision_dir, manifest)


def list_revisions(project_dir: str | Path) -> list[Revision]:
    """Return known revisions in creation order."""
    root = _resolve_project_root(project_dir)
    revisions_dir = _revisions_root(root)
    if not revisions_dir.exists():
        return []

    revisions: list[Revision] = []
    for revision_dir in sorted(revisions_dir.iterdir()):
        if not revision_dir.is_dir() or not revision_dir.name.startswith("r"):
            continue
        manifest_path = revision_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        revisions.append(_revision_from_manifest(revision_dir, manifest))
    return revisions


def restore_revision(
    project_dir: str | Path,
    revision_id: str,
    message: str | None = None,
) -> Revision:
    """
    Restore a snapshot into the HSF project directory and record it as a new
    latest revision.
    """
    root = _resolve_project_root(project_dir)
    source_revision = _find_revision_dir(root, revision_id)
    manifest_path = source_revision / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Revision manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = list(manifest.get("files") or [])
    if not files:
        raise ValueError(f"Revision {revision_id} has no source files")

    _remove_managed_source_files(root)
    for rel_path in files:
        src = source_revision / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Revision file not found: {src}")
        dst = root / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    restore_message = message or f"Restore {revision_id}"
    return create_revision(
        root,
        message=restore_message,
        gsm_name=str(manifest.get("gsm_name") or root.name),
        metadata={"restored_from": revision_id},
        trigger="rollback",
        parent_revision_id=get_latest_revision_id(root),
    )


def compare_revisions(project_dir: str | Path, from_revision_id: str, to_revision_id: str) -> str:
    """Return a unified text diff between two revision source snapshots."""
    root = _resolve_project_root(project_dir)
    from_dir = _find_revision_dir(root, from_revision_id)
    to_dir = _find_revision_dir(root, to_revision_id)
    from_manifest = _read_manifest(from_dir)
    to_manifest = _read_manifest(to_dir)
    files = sorted(set(from_manifest.get("files") or []) | set(to_manifest.get("files") or []))

    chunks: list[str] = []
    for rel_path in files:
        from_text = _read_revision_text(from_dir / rel_path)
        to_text = _read_revision_text(to_dir / rel_path)
        if from_text == to_text:
            continue
        chunks.extend(
            unified_diff(
                from_text.splitlines(keepends=True),
                to_text.splitlines(keepends=True),
                fromfile=f"{from_revision_id}/{rel_path}",
                tofile=f"{to_revision_id}/{rel_path}",
            )
        )
        if chunks and not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"

    compile_summary = _compare_compile_metadata(from_revision_id, from_manifest, to_revision_id, to_manifest)
    if compile_summary:
        chunks.append(compile_summary)

    explanation_summary = _compare_explanation_metadata(
        from_revision_id,
        from_manifest,
        to_revision_id,
        to_manifest,
    )
    if explanation_summary:
        chunks.append(explanation_summary)

    compile_comparison_summary = _compare_compile_comparison_metadata(
        from_revision_id,
        from_manifest,
        to_revision_id,
        to_manifest,
    )
    if compile_comparison_summary:
        chunks.append(compile_comparison_summary)

    return "".join(chunks) or f"No source differences between {from_revision_id} and {to_revision_id}.\n"


def get_latest_revision_id(project_dir: str | Path) -> str | None:
    """Return the latest revision id, if present."""
    root = _resolve_project_root(project_dir)
    latest_path = root / OPENBREP_DIR / LATEST_FILE
    if not latest_path.exists():
        return None
    value = latest_path.read_text(encoding="utf-8").strip()
    return value or None


def copy_project_metadata(source_project_dir: str | Path, target_project_dir: str | Path) -> bool:
    """Copy project-level OpenBrep metadata, including revisions, between HSF roots."""
    source_root = Path(source_project_dir).expanduser().resolve()
    target_root = Path(target_project_dir).expanduser().resolve()
    if source_root == target_root:
        return False

    source_meta = source_root / OPENBREP_DIR
    if not source_meta.exists() or not source_meta.is_dir():
        return False

    target_meta = target_root / OPENBREP_DIR
    target_meta.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_meta, target_meta, dirs_exist_ok=True)
    return True


def is_hsf_project_dir(project_dir: str | Path) -> bool:
    """Return True when a directory looks like an HSF project root."""
    root = Path(project_dir)
    return root.is_dir() and ((root / "libpartdata.xml").exists() or (root / "scripts").is_dir())


def _resolve_project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"HSF project directory not found: {root}")
    if not is_hsf_project_dir(root):
        raise ValueError(f"Not an HSF project directory: {root}")
    return root


def _revisions_root(project_root: Path) -> Path:
    return project_root / OPENBREP_DIR / REVISIONS_DIR


def _next_revision_id(project_root: Path) -> str:
    revisions_dir = _revisions_root(project_root)
    max_number = 0
    if revisions_dir.exists():
        for path in revisions_dir.iterdir():
            if path.is_dir() and path.name.startswith("r") and path.name[1:].isdigit():
                max_number = max(max_number, int(path.name[1:]))
    return f"r{max_number + 1:04d}"


def _collect_source_files(project_root: Path) -> list[str]:
    files: list[str] = []

    for path in sorted(project_root.glob("*.xml")):
        if path.is_file():
            files.append(path.name)

    scripts_dir = project_root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*")):
            if path.is_file():
                files.append(path.relative_to(project_root).as_posix())

    return files


def _remove_managed_source_files(project_root: Path) -> None:
    for rel_path in _collect_source_files(project_root):
        path = project_root / rel_path
        if path.exists():
            path.unlink()


def _find_revision_dir(project_root: Path, revision_id: str) -> Path:
    revision_dir = _revisions_root(project_root) / revision_id
    if not revision_dir.is_dir():
        raise FileNotFoundError(f"Revision not found: {revision_id}")
    return revision_dir


def _read_manifest(revision_dir: Path) -> dict[str, Any]:
    manifest_path = revision_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Revision manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read_revision_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _compare_compile_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_compile = dict(from_manifest.get("compile") or {})
    to_compile = dict(to_manifest.get("compile") or {})
    keys = ["mode", "success", "gsm_path", "gsm_size_bytes", "exit_code"]
    lines = []
    for key in keys:
        if from_compile.get(key) != to_compile.get(key):
            lines.append(f"- {key}: {from_compile.get(key)!r} -> {to_compile.get(key)!r}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"\n## Compile metadata changed ({from_revision_id} -> {to_revision_id})\n{body}\n"


def _compare_explanation_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_explanation = str(from_manifest.get("explanation") or "")
    to_explanation = str(to_manifest.get("explanation") or "")
    if from_explanation == to_explanation:
        return ""

    diff = unified_diff(
        from_explanation.splitlines(keepends=True),
        to_explanation.splitlines(keepends=True),
        fromfile=f"{from_revision_id}/explanation.md",
        tofile=f"{to_revision_id}/explanation.md",
    )
    lines = list(diff)
    if not lines:
        return ""
    return "\n## Explanation changed ({0} -> {1})\n{2}".format(
        from_revision_id,
        to_revision_id,
        "".join(lines),
    )


def _compare_compile_comparison_metadata(
    from_revision_id: str,
    from_manifest: dict[str, Any],
    to_revision_id: str,
    to_manifest: dict[str, Any],
) -> str:
    from_comparison = dict(from_manifest.get("compile_comparison") or {})
    to_comparison = dict(to_manifest.get("compile_comparison") or {})
    keys = [
        "mode",
        "before.success",
        "after.success",
        "size_delta_bytes",
        "param_delta",
    ]
    lines = []
    for key in keys:
        from_value = _nested_manifest_value(from_comparison, key)
        to_value = _nested_manifest_value(to_comparison, key)
        if from_value != to_value:
            lines.append(f"- {key}: {from_value!r} -> {to_value!r}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"\n## Compile comparison changed ({from_revision_id} -> {to_revision_id})\n{body}\n"


def _nested_manifest_value(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _write_explanation_markdown(revision_dir: Path, manifest: dict[str, Any]) -> None:
    explanation = str(manifest.get("explanation") or "").strip()
    if not explanation:
        return

    changed_files = list(manifest.get("changed_files") or [])
    if not changed_files:
        changed_files = list(manifest.get("files") or [])
    compile_result = _format_compile_result(dict(manifest.get("compile") or {}))

    lines = [
        f"# Revision {manifest.get('revision_id')}",
        "",
        "## User Intent",
        "",
        str(manifest.get("user_instruction") or manifest.get("message") or "Not recorded."),
        "",
        "## Engineering Summary",
        "",
        explanation,
        "",
        "## Changed Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in changed_files)
    lines.extend(
        [
            "",
            "## Compile Result",
            "",
            compile_result,
            "",
        ]
    )
    (revision_dir / "explanation.md").write_text("\n".join(lines), encoding="utf-8")


def _format_compile_result(compile_metadata: dict[str, Any]) -> str:
    if not any(value is not None for value in compile_metadata.values()):
        return "Not recorded."

    status = compile_metadata.get("success")
    if status is True:
        label = "Passed"
    elif status is False:
        label = "Failed"
    else:
        label = "Unknown"

    mode = compile_metadata.get("mode") or "unknown"
    parts = [f"{label} ({mode})"]
    if compile_metadata.get("gsm_path"):
        parts.append(f"gsm={compile_metadata['gsm_path']}")
    if compile_metadata.get("gsm_size_bytes") is not None:
        parts.append(f"size={compile_metadata['gsm_size_bytes']} bytes")
    if compile_metadata.get("parameter_count") is not None:
        parts.append(f"parameters={compile_metadata['parameter_count']}")
    if compile_metadata.get("exit_code") is not None:
        parts.append(f"exit_code={compile_metadata['exit_code']}")
    return "; ".join(parts) + "."


def _write_latest(project_root: Path, revision_id: str) -> None:
    latest_path = project_root / OPENBREP_DIR / LATEST_FILE
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(f"{revision_id}\n", encoding="utf-8")


def archive_artifact(
    project_dir: str | Path,
    gsm_path: str | Path,
    revision_id: str | None = None,
) -> Path:
    """把编译成品 .gsm 拷入项目成品归档区。

    归档约定：``<project>/artifacts/<revision_id 或 "unversioned">/<name>.gsm``。
    - revision_id 给了就按版本归档，没给进 unversioned/；
    - 同名不覆盖：目标已存在时追加短数字后缀（``name-1.gsm``、``name-2.gsm``…）；
    - 归档是副本，源文件不动；返回归档后的实际路径。
    """
    root = _resolve_project_root(project_dir)
    gsm = Path(gsm_path).expanduser().resolve()
    if not gsm.is_file():
        raise FileNotFoundError(f"Artifact source not found: {gsm}")

    version = revision_id or "unversioned"
    target_dir = root / ARTIFACTS_DIR / version
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / gsm.name
    if target.exists():
        stem, suffix = gsm.stem, gsm.suffix
        counter = 1
        while target.exists():
            target = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1
    shutil.copy2(gsm, target)
    return target


def list_archived_artifacts(
    project_dir: str | Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """列出项目 artifacts/ 下的归档成品（只读）。

    Returns:
        按 mtime 倒序的条目列表，每条含
        {version, name, path, size_bytes, mtime_iso}。
    """
    root = _resolve_project_root(project_dir)
    artifacts_root = root / ARTIFACTS_DIR
    entries: list[dict[str, Any]] = []
    if not artifacts_root.is_dir():
        return entries
    for version_dir in artifacts_root.iterdir():
        if not version_dir.is_dir():
            continue
        for gsm in version_dir.glob("*.gsm"):
            if not gsm.is_file():
                continue
            stat = gsm.stat()
            entries.append({
                "version": version_dir.name,
                "name": gsm.name,
                "path": str(gsm),
                "size_bytes": stat.st_size,
                "mtime_iso": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })
    entries.sort(key=lambda e: e["mtime_iso"], reverse=True)
    if limit is not None:
        entries = entries[:limit]
    return entries


def _auto_prune_keep_last_n() -> int:
    """读取 [revisions] keep_last_n，决定 create_revision 尾部自动 prune 的保留数。

    取舍：不向 create_revision 的调用方（pipeline / mcp_tools / workbench 多处）传参，
    而是 create_revision 内部自行解析 config——改动只落在 revisions.py 单文件，调用链
    零改动。代价是每次 create_revision 有一次 GDLAgentConfig.load() 的文件读取；
    create_revision 属用户操作频次（非热循环），可接受。

    返回 0 表示禁用自动 prune（git 化项目把历史完全交给 git）。解析失败或值非法
    （负数/非整数）时回退默认 20，保证与旧版行为一致、绝不炸。
    """
    try:
        from openbrep.config import GDLAgentConfig

        config = GDLAgentConfig.load()
        value = config.revisions.keep_last_n
    except Exception:
        return 20
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 20
    return value


@dataclass
class PruneResult:
    """prune 结果：deleted 保持向后兼容的整数语义；warnings 报告保护溢出。"""

    deleted: int = 0
    kept_ids: list[str] = field(default_factory=list)
    protected_kept: list[str] = field(default_factory=list)
    overflow_protection: bool = False
    warnings: list[str] = field(default_factory=list)

    def __int__(self) -> int:
        return self.deleted

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, int):
            return self.deleted == other
        if isinstance(other, PruneResult):
            return self.deleted == other.deleted
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.deleted)


def _protections_path(project_root: Path) -> Path:
    return project_root / OPENBREP_DIR / REVISIONS_DIR / PROTECTIONS_FILE


def load_revision_protections(project_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    """读取受保护 revision 引用：{revision_id: [{reason, run_id, ref, registered_at}]}。"""
    root = _resolve_project_root(project_dir)
    path = _protections_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for rev_id, entries in data.items():
        if isinstance(entries, list):
            out[str(rev_id)] = [e for e in entries if isinstance(e, dict)]
        elif isinstance(entries, dict):
            out[str(rev_id)] = [entries]
    return out


def _write_revision_protections(
    project_root: Path,
    protections: dict[str, list[dict[str, Any]]],
) -> None:
    path = _protections_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(protections, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def replace_delivery_revision_protections(
    project_dir: str | Path,
    *,
    before_revision_id: str | None,
    after_revision_id: str | None,
    run_id: str,
) -> None:
    """Atomically retain only the current delivery pair.

    External protection reasons (host acceptance / pending candidate) are left
    untouched.  A partial run passes ``after_revision_id=None``: its recovery
    point replaces the previous current-run before while the last successful
    after remains protected.
    """
    root = _resolve_project_root(project_dir)
    protections = load_revision_protections(root)
    delivery_reasons = {"current_run_before"}
    if after_revision_id:
        # A completed delivery supersedes the whole previous delivery pair.
        # A partial delivery supersedes only the recovery ``before`` and keeps
        # the last known-good after protected.
        delivery_reasons.add("latest_successful_after")
    for revision_id, entries in list(protections.items()):
        kept = [
            entry for entry in entries
            if str(entry.get("reason") or "") not in delivery_reasons
        ]
        if kept:
            protections[revision_id] = kept
        else:
            protections.pop(revision_id, None)

    now = datetime.now(timezone.utc).isoformat()
    if before_revision_id:
        protections.setdefault(str(before_revision_id), []).append({
            "reason": "current_run_before",
            "run_id": run_id,
            "ref": {"kind": "delivery_before", "run_id": run_id},
            "registered_at": now,
        })
    if after_revision_id:
        protections.setdefault(str(after_revision_id), []).append({
            "reason": "latest_successful_after",
            "run_id": run_id,
            "ref": {"kind": "delivery_after", "run_id": run_id},
            "registered_at": now,
        })
    _write_revision_protections(root, protections)


def register_revision_protection(
    project_dir: str | Path,
    revision_id: str,
    *,
    reason: str,
    run_id: str | None = None,
    ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一引用接口：登记一条受保护引用（幂等：同 reason+run_id 不重复）。"""
    root = _resolve_project_root(project_dir)
    rev_id = str(revision_id or "").strip()
    if not rev_id:
        raise ValueError("revision_id is required for protection")
    protections = load_revision_protections(root)
    entries = list(protections.get(rev_id) or [])
    key = (reason or "", run_id or "")
    for existing in entries:
        if (str(existing.get("reason") or ""), str(existing.get("run_id") or "")) == key:
            return existing
    entry = {
        "reason": reason or "explicit",
        "run_id": run_id,
        "ref": dict(ref or {}),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    entries.append(entry)
    protections[rev_id] = entries
    _write_revision_protections(root, protections)
    return entry


def unregister_revision_protection(
    project_dir: str | Path,
    revision_id: str,
    *,
    reason: str | None = None,
    run_id: str | None = None,
) -> int:
    """解除保护：可按 reason/run_id 过滤；返回删除的引用条数。"""
    root = _resolve_project_root(project_dir)
    rev_id = str(revision_id or "")
    protections = load_revision_protections(root)
    entries = list(protections.get(rev_id) or [])
    kept = []
    removed = 0
    for entry in entries:
        match = True
        if reason is not None and str(entry.get("reason") or "") != reason:
            match = False
        if run_id is not None and str(entry.get("run_id") or "") != str(run_id):
            match = False
        if match:
            removed += 1
        else:
            kept.append(entry)
    if removed:
        if kept:
            protections[rev_id] = kept
        else:
            protections.pop(rev_id, None)
        _write_revision_protections(root, protections)
    return removed


def protected_revision_ids(project_dir: str | Path) -> set[str]:
    """当前仍被引用保护的 revision id 集合（引用存在即保护，即使目录已删）。"""
    protections = load_revision_protections(project_dir)
    return {rev_id for rev_id, entries in protections.items() if entries}


def prune_revisions(
    project_dir: str | Path,
    keep_last_n: int = 20,
    *,
    extra_protected_ids: set[str | None] | None = None,
) -> PruneResult:
    """Delete oldest unprotected revisions beyond keep_last_n.

    Safety guarantees:
    - The latest revision (pointed to by .openbrep/latest) is never deleted.
    - Explicitly protected revisions (current-run before / latest successful after /
      host-acceptance / pending-candidate refs) are never auto-deleted.
    - If protection causes retained count to exceed keep_last_n, report clearly
      in ``warnings`` instead of silently exceeding.
    - Deletion failures are logged but do not abort the rest of pruning.
    - Old unprotected records already deleted are gone; consumers show evidence
      unavailable and must not re-fabricate them.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    keep_last_n = max(1, keep_last_n)
    root = _resolve_project_root(project_dir)
    revisions = list_revisions(root)
    result = PruneResult()
    if not revisions:
        return result

    latest_id = get_latest_revision_id(root)
    protected = protected_revision_ids(root)
    protected.update(str(rid) for rid in (extra_protected_ids or set()) if rid)
    if latest_id:
        protected = set(protected) | {latest_id}

    # 保留窗口：最近 keep_last_n 条 + 全部受保护 + latest
    keep_window_ids = {rev.revision_id for rev in revisions[-keep_last_n:]}
    retain_ids = set(keep_window_ids) | protected
    if latest_id:
        retain_ids.add(latest_id)

    deleted = 0
    protected_kept: list[str] = []
    kept_ids: list[str] = []
    for rev in revisions:
        if rev.revision_id in retain_ids:
            kept_ids.append(rev.revision_id)
            if rev.revision_id in protected:
                protected_kept.append(rev.revision_id)
            continue
        try:
            shutil.rmtree(rev.path)
            deleted += 1
            _logger.debug("prune_revisions: deleted %s", rev.revision_id)
        except Exception as exc:
            _logger.warning("prune_revisions: failed to delete %s: %s", rev.revision_id, exc)
            kept_ids.append(rev.revision_id)

    result.deleted = deleted
    result.kept_ids = kept_ids
    result.protected_kept = protected_kept
    if len(retain_ids) > keep_last_n:
        result.overflow_protection = True
        result.warnings.append(
            f"受保护 revision 数量导致保留数超过 keep_last_n="
            f"{keep_last_n}（protected+window={len(retain_ids)}）；"
            "解除保护后将按保留策略处理"
        )
    if deleted:
        _logger.info("prune_revisions: deleted %d revision(s), kept %d", deleted, len(kept_ids))
    return result


def _vision_extraction_hashes(project_root: Path) -> list:
    """项目当前全部 extraction 哈希（manifest 用，best-effort 失败记空）。"""
    try:
        from openbrep.vision.extraction_store import list_extraction_hashes

        return list_extraction_hashes(project_root)
    except Exception:
        return []


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.write.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _revision_from_manifest(revision_dir: Path, manifest: dict[str, Any]) -> Revision:
    return Revision(
        revision_id=str(manifest["revision_id"]),
        project_name=str(manifest.get("project_name") or revision_dir.parent.parent.name),
        gsm_name=str(manifest.get("gsm_name") or manifest.get("project_name") or revision_dir.parent.parent.name),
        created_at=str(manifest.get("created_at") or ""),
        message=str(manifest.get("message") or ""),
        files=list(manifest.get("files") or []),
        path=revision_dir,
        trigger=str(manifest.get("trigger") or "manual"),
        intent=str(manifest.get("intent") or ""),
        user_instruction=str(manifest.get("user_instruction") or ""),
        changed_files=list(manifest.get("changed_files") or []),
        parent_revision_id=manifest.get("parent_revision_id"),
        compile=dict(manifest.get("compile") or {}),
        explanation=str(manifest.get("explanation") or ""),
        compile_comparison=dict(manifest.get("compile_comparison") or {})
        if manifest.get("compile_comparison")
        else None,
    )
