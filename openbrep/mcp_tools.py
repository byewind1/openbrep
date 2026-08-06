"""MCP 工具契约层（Phase 1 / P1-b+d+e + Phase 2 / P2-b+P2-c）。

纯 Python，dict in / dict out。禁止 import 任何 mcp 库 —— 协议适配层后续才做。

契约约定：
- 项目类工具第一个参数是 HSF 项目目录绝对路径 path（字符串）；skill 类工具
  （propose_skill / verify_skill / reuse_skill / list_skills / deprecate_skill）
  的参数是 skill 名 / query + skills_dir，不走 path。
- 成功返回：{ok: True, ..., trace_id}
- 失败返回：{ok: False, error: {code, message, details?}, trace_id}
- 任何异常都不许穿透：统一包成错误 dict 返回。
- trace_id 格式：mcp-YYYYMMDD-NNNN（日内序号，模块级计数器）。
- mutation 工具（apply_edit / rollback / propose_skill / verify_skill 的晋升
  落盘、deprecate_skill 的状态翻转）全程走 _locked()；只读工具 v1 也走它
  （串行最稳）。

错误 code 汇总（全部工具共用同一错误形态）：
- project_not_found      path 不存在 / 不是合法 HSF 项目 / 项目加载失败 /
                         import_source 的源文件不存在
- invalid_mode           compile_hsf 的 mode 非法 / import_source 的 kind 非法 /
                         源文件后缀与 kind 不匹配 / apply_edit 的 mode 非法 /
                         list_skills 的 status 非法
- invalid_spec           apply_edit 的 spec 非法（未知 type / 参数不存在 /
                         非数值参数值 / 脚本类型非法 / content 非字符串）；
                         propose_skill 的 name 不合法（路径分隔符 / 首尾空白 /
                         隐藏文件 / README 等）或 content 非字符串、或
                         slice.params 声明非法 type（不在 Length/Integer/
                         RealNum/Boolean/Material/String/Angle）或缺
                         value/type 键；verify_skill 的 slice 同样校验；
                         reuse_skill 的 query 非字符串或空；
                         deprecate_skill 的 name 不合法
- invalid_revision       rollback 的 revision_id 不存在 / 无 parent 且无
                         倒数第二条可回滚
- converter_unavailable  LP_XMLConverter 不可用（import_source kind="gsm"）
- skill_exists           propose_skill 的目标 {name}.md 已存在（不覆盖）
- skill_not_found        verify_skill / deprecate_skill 的 skill 不存在
- mcp_internal_error     工具内部意外异常（预览、编译、导入、服务调用等）
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.project_context import load_project_origin
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.revisions import (
    archive_artifact,
    create_revision,
    get_latest_revision_id,
    is_hsf_project_dir,
    list_archived_artifacts,
    list_revisions,
    restore_revision,
)
from openbrep.skills_loader import SkillsLoader, rewrite_skill_frontmatter
from openbrep.workbench.project_service import WorkbenchProjectService
from openbrep.workbench.project_session_service import safe_project_name, unique_project_name
from openbrep.workbench.workspace_service import (
    init_workspace as _ws_init,
    scan_workspace as _ws_scan,
    search_workspace as _ws_search,
)

# ── 锁与 trace_id ─────────────────────────────────────────

_LOCK = threading.RLock()  # 预留给 mutation 工具（P1-c）；只读工具 v1 也走它
_trace_date = ""
_trace_seq = 0


def _next_trace_id() -> str:
    """mcp-YYYYMMDD-NNNN：日内序号，模块级计数器（须在 _locked() 内调用）。"""
    global _trace_date, _trace_seq
    today = datetime.now().strftime("%Y%m%d")
    if today != _trace_date:
        _trace_date = today
        _trace_seq = 0
    _trace_seq += 1
    return f"mcp-{today}-{_trace_seq:04d}"


@contextmanager
def _locked() -> Iterator[None]:
    """串行化工具调用；mutation 工具（P1-c）后续复用同一把锁。"""
    with _LOCK:
        yield


def _make_error(code: str, message: str, trace_id: str, details: Any = None) -> dict:
    """统一错误形态：{ok: False, error: {code, message, details?}, trace_id}。"""
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "trace_id": trace_id}


def _require_hsf_dir(path: str) -> Path:
    """校验 path 指向合法 HSF 项目根目录，否则抛 FileNotFoundError。

    合法性判定复用 revisions.is_hsf_project_dir（存在 libpartdata.xml 或 scripts/）。
    """
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"HSF 项目目录不存在: {root}")
    if not is_hsf_project_dir(root):
        raise FileNotFoundError(f"不是合法的 HSF 项目目录（缺少 libpartdata.xml 或 scripts/）: {root}")
    return root


def _load_project(path: str, trace_id: str) -> tuple[Path, HSFProject] | dict:
    """校验并加载项目；失败返回统一错误 dict（供上层直接 return）。"""
    try:
        root = _require_hsf_dir(path)
    except Exception as exc:
        return _make_error("project_not_found", str(exc), trace_id)
    try:
        project = HSFProject.load_from_disk(str(root))
    except Exception as exc:
        return _make_error(
            "project_not_found",
            f"无法加载 HSF 项目: {exc}",
            trace_id,
            details={"path": path},
        )
    return root, project


# ── 只读工具 v1 ───────────────────────────────────────────


def load_project(path: str) -> dict:
    """加载 HSF 项目，返回项目概貌（只读、幂等、无副作用）。"""
    with _locked():
        trace_id = _next_trace_id()
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        root, project = loaded
        try:
            scripts_present = [st.name for st, content in project.scripts.items() if content.strip()]
            return {
                "ok": True,
                "name": project.name,
                "parameter_count": len(project.parameters),
                "scripts_present": scripts_present,
                "ac_version": project.version,
                "latest_revision_id": get_latest_revision_id(root),
                "origin": load_project_origin(root),
                "artifacts": list_archived_artifacts(root, limit=5),
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"load_project 失败: {exc}", trace_id)


def _resolve_compiler(mode: str) -> tuple[MockHSFCompiler | HSFCompiler, str]:
    """按 compile_hsf 语义选择编译器：mock 固定 mock；auto/real 优先真实。"""
    if mode == "mock":
        return MockHSFCompiler(), "mock"
    real = HSFCompiler()
    if mode == "real" or real.is_available:
        return real, "real"
    return MockHSFCompiler(), "mock"


def compile_hsf(path: str, mode: str = "auto") -> dict:
    """编译 HSF → .gsm（只读：产物写临时目录，不写进项目目录）。

    mode: "auto" | "mock" | "real"。auto = HSFCompiler.is_available 为真走真实，
    否则 MockHSFCompiler。ok=True 表示工具执行成功；success 表示编译结果。
    """
    with _locked():
        trace_id = _next_trace_id()
        if mode not in ("auto", "mock", "real"):
            return _make_error(
                "invalid_mode",
                f"mode 必须是 auto/mock/real，收到: {mode!r}",
                trace_id,
                details={"mode": mode},
            )
        try:
            root = _require_hsf_dir(path)
        except Exception as exc:
            return _make_error("project_not_found", str(exc), trace_id)

        compiler, effective_mode = _resolve_compiler(mode)

        try:
            out_dir = tempfile.mkdtemp(prefix="mcp_compile_")
            output_gsm = str(Path(out_dir) / f"{root.name}.gsm")
            result = compiler.hsf2libpart(str(root), output_gsm)
            # 成品归档：编译成功才归档（unversioned/），失败不归档且不阻断编译结果。
            # 临时目录产物保留，归档是副本。
            artifact_path: str | None = None
            if result.success:
                raw_output = result.output_path or output_gsm
                try:
                    artifact_path = str(archive_artifact(root, raw_output))
                except Exception:
                    artifact_path = None
            return {
                "ok": True,
                "mode": result.mode or effective_mode,
                "success": result.success,
                "errors": list(result.errors or []),
                "warnings": list(result.warnings or []),
                "exit_code": result.exit_code,
                "output_path": result.output_path,
                "artifact_path": artifact_path,
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"compile_hsf 失败: {exc}", trace_id)


def semantic_verify(path: str, sweep: bool = True) -> dict:
    """对项目 3D 脚本做几何级语义验证（不走 pipeline；verify_semantics 永不抛异常）。"""
    with _locked():
        trace_id = _next_trace_id()
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        _root, project = loaded
        from openbrep.semantic_verifier import verify_semantics

        try:
            result = verify_semantics(project, sweep=sweep)
            issues = [
                {"check_type": i.check_type, "detail": i.detail, "blocking": i.blocking}
                for i in result.issues
            ]
            return {
                "ok": True,
                "passed": result.passed,
                "issues": issues,
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"semantic_verify 失败: {exc}", trace_id)


# ── apply_edit / rollback（P1-c，mutation） ────────────────

# spec script_type 别名 → ScriptType（1d 与 master 都指向 Master/1d.gdl）
_SCRIPT_TYPE_MAP = {
    "1d": ScriptType.MASTER,
    "master": ScriptType.MASTER,
    "2d": ScriptType.SCRIPT_2D,
    "3d": ScriptType.SCRIPT_3D,
    "vl": ScriptType.PARAM,
    "ui": ScriptType.UI,
}


def apply_edit(path: str, spec: dict, mode: str = "draft") -> dict:
    """应用编辑：set_parameters（改参数值）或 set_script（整脚本替换）。

    spec 只支持两种（不做通用补丁语言）：
    - {"type": "set_parameters", "values": {param_name: number}}
    - {"type": "set_script", "script_type": "1d"|"2d"|"3d"|"vl"|"ui"|"master",
       "content": str}

    mode="draft"（试跑，零持久化）：
    1. 整个项目目录复制到 tempfile.mkdtemp 里的副本。
    2. 副本上应用 spec + save_to_disk。
    3. 对副本跑 compile（mock）与 semantic_verify。
    4. 生成原目录 vs 副本的 unified diff（只含变化源文件）。
    原项目目录字节级零改动。

    mode="apply"（落盘，可回滚）：
    1. 先 create_revision 快照（metadata 记 {trace_id, tool_spec}，随
       revisions.py 落进 manifest.json）。
    2. 应用 spec + save_to_disk。
    3. 编译（auto：真实可用走真实，否则 mock）。
    返回 revision_id 与最近 5 条 revision。
    """
    with _locked():
        trace_id = _next_trace_id()
        if mode not in ("draft", "apply"):
            return _make_error(
                "invalid_mode",
                f"mode 必须是 draft/apply，收到: {mode!r}",
                trace_id,
                details={"mode": mode},
            )
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        root, project = loaded

        spec_error = _validate_spec(spec, project)
        if spec_error:
            return _make_error("invalid_spec", spec_error, trace_id, details={"spec": spec})

        try:
            if mode == "draft":
                return _apply_edit_draft(root, spec, trace_id)
            return _apply_edit_apply(root, project, spec, trace_id)
        except Exception as exc:
            return _make_error(
                "mcp_internal_error",
                f"apply_edit 失败: {exc}",
                trace_id,
                details={"mode": mode},
            )


def rollback(path: str, revision_id: str = "previous") -> dict:
    """回滚项目到指定 revision，并记录一条 trigger="rollback" 的新 revision。

    revision_id="previous" 的解析语义：回滚到最新 revision 的父版本——
    优先用 list_revisions + manifest 的 parent_revision_id；取不到时用 list
    倒数第二条；若项目只有一条 revision（父为空，例如刚 apply_edit 一次），
    回滚到它本身，即撤销最近一次变更。
    """
    with _locked():
        trace_id = _next_trace_id()
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        root, _project = loaded

        try:
            revisions = list_revisions(root)
            if not revisions:
                return _make_error(
                    "invalid_revision",
                    "项目没有可回滚的 revision",
                    trace_id,
                    details={"revision_id": revision_id},
                )
            target_id = _resolve_rollback_target(root, revision_id, revisions)
            if target_id is None:
                return _make_error(
                    "invalid_revision",
                    f"找不到可回滚的版本: {revision_id}",
                    trace_id,
                    details={"revision_id": revision_id},
                )
            restored = restore_revision(root, target_id)
            return {
                "ok": True,
                "restored_revision": target_id,
                "new_revision_id": restored.revision_id,
                "recent_revisions": _recent_revisions(root),
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"rollback 失败: {exc}", trace_id)


# ── apply_edit 内部 ────────────────────────────────────────


def _validate_spec(spec: Any, project: HSFProject) -> str | None:
    """校验 apply_edit 的 spec；合法返回 None，否则返回错误消息。

    非法判定：非对象 / 未知 type / set_parameters 的 values 非对象或参数不存在
    或值非数值 / set_script 的 script_type 非法或 content 非字符串。
    """
    if not isinstance(spec, dict):
        return f"spec 必须是对象，收到: {type(spec).__name__}"
    stype = spec.get("type")
    if stype not in ("set_parameters", "set_script"):
        return f"未知 spec.type: {stype!r}"
    if stype == "set_parameters":
        values = spec.get("values")
        if not isinstance(values, dict) or not values:
            return "set_parameters 需要非空 values 对象"
        for name, value in values.items():
            if project.get_parameter(str(name)) is None:
                return f"参数不存在: {name}"
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"参数 {name} 的值必须是数值: {value!r}"
        return None
    script_type = spec.get("script_type")
    if script_type not in _SCRIPT_TYPE_MAP:
        return f"非法的 script_type: {script_type!r}"
    if not isinstance(spec.get("content"), str):
        return "set_script 需要字符串 content"
    return None


def _apply_spec(project: HSFProject, spec: dict) -> None:
    """把已校验的 spec 应用到内存项目（不落盘；落盘由调用方负责）。"""
    if spec["type"] == "set_parameters":
        for name, value in spec["values"].items():
            project.get_parameter(str(name)).value = _format_number(value)
        return
    script_type = _SCRIPT_TYPE_MAP[spec["script_type"]]
    project.set_script(script_type, spec["content"])


def _spec_changed_files(spec: dict) -> list[str]:
    """spec 影响的源文件相对路径（用于 revision changed_files）。"""
    if spec["type"] == "set_parameters":
        return ["paramlist.xml"]
    script_type = _SCRIPT_TYPE_MAP[spec["script_type"]]
    return [f"scripts/{script_type.value}"]


def _format_number(value: int | float) -> str:
    """数值 → GDL 参数值字符串（整数不带小数点；Boolean 按 0/1）。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _apply_edit_draft(root: Path, spec: dict, trace_id: str) -> dict:
    """draft 模式：副本试跑，原项目目录字节级零改动。"""
    tmp = Path(tempfile.mkdtemp(prefix="mcp_draft_"))
    copy_dir = tmp / root.name
    shutil.copytree(root, copy_dir)

    copy_project = HSFProject.load_from_disk(str(copy_dir))
    _apply_spec(copy_project, spec)
    copy_project.save_to_disk()

    return {
        "ok": True,
        "mode": "draft",
        "diff": _diff_dirs(root, copy_dir),
        "compile": _compile_project_result(copy_dir, "mock"),
        "verify": _verify_project(copy_dir),
        "trace_id": trace_id,
    }


def _apply_edit_apply(root: Path, project: HSFProject, spec: dict, trace_id: str) -> dict:
    """apply 模式：快照"修改前"状态（metadata 记 {trace_id, tool_spec}）→
    应用 spec → 落盘 → 编译。与 pipeline micro_modify 同一落盘语义。"""
    revision = create_revision(
        root,
        message="mcp apply_edit",
        gsm_name=project.name,
        metadata={"trace_id": trace_id, "tool_spec": spec},
        trigger="mcp",
        intent="MCP",
        user_instruction="",
        changed_files=_spec_changed_files(spec),
    )
    _apply_spec(project, spec)
    project.save_to_disk()
    return {
        "ok": True,
        "mode": "apply",
        "diff": _diff_dirs(revision.path, root),
        "revision_id": revision.revision_id,
        "compile": _compile_project_result(root, "auto"),
        "recent_revisions": _recent_revisions(root),
        "trace_id": trace_id,
    }


def _compile_project_result(root: Path, mode: str) -> dict:
    """编译项目到临时目录并返回 compile 结果 dict（compile_hsf 同款逻辑）；
    编译异常降级为 success=False，不抛给上层。"""
    compiler, effective_mode = _resolve_compiler(mode)
    try:
        out_dir = tempfile.mkdtemp(prefix="mcp_compile_")
        output_gsm = str(Path(out_dir) / f"{root.name}.gsm")
        result = compiler.hsf2libpart(str(root), output_gsm)
        return {
            "mode": result.mode or effective_mode,
            "success": result.success,
            "errors": list(result.errors or []),
            "warnings": list(result.warnings or []),
            "exit_code": result.exit_code,
            "output_path": result.output_path,
        }
    except Exception as exc:
        return {
            "mode": effective_mode,
            "success": False,
            "errors": [f"编译异常: {exc}"],
            "warnings": [],
            "exit_code": None,
            "output_path": None,
        }


def _verify_project(root: Path, sweep: bool = True) -> dict:
    """对项目目录做语义验证，返回 {passed, issues}；异常降级为 failed（advisory）。"""
    from openbrep.semantic_verifier import verify_semantics

    try:
        project = HSFProject.load_from_disk(str(root))
        result = verify_semantics(project, sweep=sweep)
        issues = [
            {"check_type": i.check_type, "detail": i.detail, "blocking": i.blocking}
            for i in result.issues
        ]
        return {"passed": result.passed, "issues": issues}
    except Exception as exc:
        return {
            "passed": False,
            "issues": [
                {"check_type": "verify_error", "detail": f"语义验证异常: {exc}", "blocking": True}
            ],
        }


def _recent_revisions(root: Path, limit: int = 5) -> list[dict]:
    revs = list_revisions(root)[-limit:]
    return [{"id": r.revision_id, "message": r.message, "trigger": r.trigger} for r in revs]


def _resolve_rollback_target(root: Path, revision_id: str, revisions: list) -> str | None:
    """把 rollback 的 revision_id（含 "previous"）解析为实际要恢复的 revision id。"""
    if revision_id != "previous":
        return revision_id if any(r.revision_id == revision_id for r in revisions) else None

    latest_id = get_latest_revision_id(root)
    latest = next((r for r in revisions if r.revision_id == latest_id), None) or revisions[-1]
    if latest.parent_revision_id:
        parent = next(
            (r for r in revisions if r.revision_id == latest.parent_revision_id), None
        )
        if parent is not None:
            return parent.revision_id
    if len(revisions) >= 2:
        return revisions[-2].revision_id
    # 单条 revision（如 apply_edit 快照"修改前"状态）：回滚到它本身 = 撤销最近一次变更
    return latest.revision_id


def _source_relative_files(root: Path) -> list[str]:
    """项目源文件相对路径（*root*.xml + scripts/**），与 revisions 同口径。"""
    files: list[str] = []
    for path in sorted(root.glob("*.xml")):
        if path.is_file():
            files.append(path.name)
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*")):
            if path.is_file():
                files.append(path.relative_to(root).as_posix())
    return files


def _read_source_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _diff_dirs(original: Path, modified: Path) -> str:
    """original vs modified 的 unified diff（只含变化源文件）。"""
    files = sorted(
        set(_source_relative_files(original)) | set(_source_relative_files(modified))
    )
    chunks: list[str] = []
    for rel in files:
        original_text = _read_source_text(original / rel)
        modified_text = _read_source_text(modified / rel)
        if original_text == modified_text:
            continue
        chunks.extend(
            difflib.unified_diff(
                original_text.splitlines(keepends=True),
                modified_text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
    return "".join(chunks)


# ── render_evidence ──────────────────────────────────────


def render_evidence(
    path: str,
    sweep_params: list[str] | None = None,
    tolerance: float = 0.05,
) -> dict:
    """机器可读几何证据：包围盒、网格统计、参数扫掠响应（单位：米）。

    不是给人看的 Three.js payload，而是可供下游工具/LLM 判读的结构化证据：
    复用 preview_payload 做 3D 预览，_scene_bbox 算包围盒，sweep_parameters
    的扰动机制做参数扫掠。

    sweep_params 为空 = 不扫掠。预览内部异常降级为 warning + ok:True +
    mesh_stats 为空（参考 verify_semantics 的 preview_error 降级策略），
    不让工具整体失败。

    tolerance 只影响 bbox_vs_declared 的 match 判定（默认 0.05，即 5%）；
    不影响 semantic_verifier 的任何行为。deltas 照旧全量报告。
    """
    with _locked():
        trace_id = _next_trace_id()
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        _root, project = loaded

        from openbrep.semantic_verifier import _scene_bbox
        from openbrep.static_checker import RESERVED_PARAMS
        from openbrep.workbench.preview_service import preview_payload
        from openbrep.workbench.project_parameter_service import parameter_values

        params = parameter_values(project)
        declared_dims = {name: params[name] for name in RESERVED_PARAMS if name in params}

        try:
            payload = preview_payload(project, overrides={}, script_overrides={})
        except Exception as exc:
            return {
                "ok": True,
                "bbox": None,
                "mesh_stats": {
                    "mesh_count": 0,
                    "vertex_count": 0,
                    "face_count": 0,
                    "warnings": [f"3D 预览执行异常，证据降级为空：{exc}"],
                },
                "sweep": [],
                "declared_dims": declared_dims,
                "bbox_vs_declared": None,
                "trace_id": trace_id,
            }

        payload_meshes = payload.get("meshes", [])
        warnings = list(payload.get("warnings") or [])

        mesh_count = len(payload_meshes)
        vertex_count = sum(len(m.get("vertices") or []) for m in payload_meshes)
        face_count = sum(len(m.get("faces") or []) for m in payload_meshes)

        bbox = _scene_bbox(_payload_mesh_xyz_views(payload_meshes))
        bbox_info = _bbox_info(bbox)

        sweep = _render_evidence_sweep(project, params, sweep_params or [], warnings)

        bbox_vs_declared = None
        if bbox_info is not None and declared_dims:
            bbox_vs_declared = _bbox_vs_declared(
                bbox_info["size"], declared_dims, tolerance=tolerance
            )

        return {
            "ok": True,
            "bbox": bbox_info,
            "mesh_stats": {
                "mesh_count": mesh_count,
                "vertex_count": vertex_count,
                "face_count": face_count,
                "warnings": warnings,
            },
            "sweep": sweep,
            "declared_dims": declared_dims,
            "bbox_vs_declared": bbox_vs_declared,
            "trace_id": trace_id,
        }


def _payload_mesh_xyz_views(payload_meshes: list[dict]) -> list[Any]:
    """把 preview_payload 的 mesh（vertices: [[x,y,z], ...]）转成 _scene_bbox
    需要的 x/y/z 列表形态（复用 semantic_verifier._scene_bbox 的包围盒逻辑）。"""
    views: list[Any] = []
    for mesh in payload_meshes:
        vertices = mesh.get("vertices") or []
        x: list[float] = []
        y: list[float] = []
        z: list[float] = []
        for v in vertices:
            x.append(float(v[0]))
            y.append(float(v[1]))
            z.append(float(v[2]))
        views.append(SimpleNamespace(x=x, y=y, z=z))
    return views


def _bbox_info(bbox: tuple[float, float, float, float, float, float] | None) -> dict | None:
    """(min_x, max_x, min_y, max_y, min_z, max_z) → {min, max, size}；None 直通。"""
    if bbox is None:
        return None
    min_x, max_x, min_y, max_y, min_z, max_z = bbox
    return {
        "min": [min_x, min_y, min_z],
        "max": [max_x, max_y, max_z],
        "size": [max_x - min_x, max_y - min_y, max_z - min_z],
    }


def _bbox_vs_declared(
    bbox_size: list[float],
    declared_dims: dict[str, float],
    tolerance: float,
) -> dict:
    """把 bbox.size 与声明尺寸（A/B/ZZYZX）按从大到小配对比较。

    与 semantic_verifier.check_bounding_box_against_dimensions 同一套容差规则，
    容忍旋转（不按轴向硬配对）。返回 {match, deltas}，deltas 为每维
    actual - expected。
    """
    actual_sorted = sorted(bbox_size, reverse=True)
    dims_sorted = sorted(declared_dims.items(), key=lambda kv: kv[1], reverse=True)
    match = True
    deltas: dict[str, float] = {}
    for i, (name, expected) in enumerate(dims_sorted):
        actual = actual_sorted[i] if i < len(actual_sorted) else 0.0
        low, high = expected * (1 - tolerance), expected * (1 + tolerance)
        if not (low <= actual <= high):
            match = False
        deltas[name] = actual - expected
    return {"match": match, "deltas": deltas}


def _render_evidence_sweep(
    project: HSFProject,
    params: dict[str, float],
    sweep_params: list[str],
    warnings: list[str],
) -> list[dict]:
    """对请求的每个参数做一次扰动预览，产出 sweep 证据条目。

    复用 semantic_verifier.sweep_parameters 的扰动与预览机制（_perturb_value +
    preview_3d_script + _scene_bbox）。passed = 扰动后几何仍存在
    （bbox_response 非 None）。
    """
    if not sweep_params:
        return []

    from openbrep.gdl_previewer import preview_3d_script
    from openbrep.hsf_project import ScriptType
    from openbrep.semantic_verifier import DEFAULT_SWEEP_DELTA_RATIO, _perturb_value, _scene_bbox

    script_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
    setup_script = project.get_script(ScriptType.MASTER) or ""

    entries: list[dict] = []
    for raw_name in sweep_params:
        name = str(raw_name).upper()
        if name not in params:
            warnings.append(f"扫掠参数不存在或非数值，已跳过: {raw_name}")
            continue
        base_value = params[name]
        perturbed_value = _perturb_value(base_value, DEFAULT_SWEEP_DELTA_RATIO)
        perturbed_params = dict(params)
        perturbed_params[name] = perturbed_value
        try:
            result = preview_3d_script(
                script_3d,
                parameters=perturbed_params,
                setup_script=setup_script,
                unknown_command_policy="warn",
                quality="fast",
            )
            sweep_bbox = _scene_bbox(result.meshes)
        except Exception as exc:
            warnings.append(f"参数 {name} 扫掠预览异常: {exc}")
            sweep_bbox = None
        bbox_response = _bbox_info(sweep_bbox)
        entries.append({
            "param": name,
            "base_value": base_value,
            "delta": perturbed_value - base_value,
            "bbox_response": bbox_response,
            "passed": bbox_response is not None,
        })
    return entries


# ── import_source ────────────────────────────────────────


def import_source(source_path: str, kind: str, target_dir: str, name: str | None = None) -> dict:
    """把外部源文件（gdl / gsm / blender_py）导入为 HSF 项目。

    复用 workbench 服务的导入路径（WorkbenchProjectService.import_gdl_file /
    import_gsm_file，以及 blender_script 转换入口 convert_blender_script）。
    返回 {ok, project_path, warnings, trace_id}。

    kind="gsm" 依赖 LP_XMLConverter，不可用时返回 code="converter_unavailable"
    （不许静默失败）。blender_py 的 unsupported 警告透传进 warnings
    （不许静默丢几何，AGENTS.md 规则 6）。
    """
    with _locked():
        trace_id = _next_trace_id()
        if kind not in ("gdl", "gsm", "blender_py"):
            return _make_error(
                "invalid_mode",
                f"kind 必须是 gdl/gsm/blender_py，收到: {kind!r}",
                trace_id,
                details={"kind": kind},
            )

        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return _make_error(
                "project_not_found",
                f"源文件不存在: {source_path}",
                trace_id,
                details={"path": source_path},
            )

        expected_suffix = {"gdl": ".gdl", "gsm": ".gsm", "blender_py": ".py"}[kind]
        if source.suffix.lower() != expected_suffix:
            return _make_error(
                "invalid_mode",
                f"kind={kind} 期望 {expected_suffix} 文件，收到: {source.suffix or '(无后缀)'}",
                trace_id,
                details={"path": str(source), "kind": kind},
            )

        target = Path(target_dir).expanduser().resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return _make_error(
                "mcp_internal_error",
                f"无法创建目标目录: {exc}",
                trace_id,
                details={"target_dir": target_dir},
            )

        if kind == "gdl":
            return _import_gdl(source, target, name, trace_id)
        if kind == "gsm":
            return _import_gsm(source, target, name, trace_id)
        return _import_blender_py(source, target, name, trace_id)


def _stage_source(source: Path, target_dir: Path, name: str | None) -> Path:
    """把源文件（按需）复制进 target_dir，使服务把项目创建在 target_dir 下。

    name 给定时用它作为项目名（通过复制后的文件名 stem 传递）。
    """
    stem = safe_project_name(name) if name else source.stem
    staged = target_dir / f"{stem}{source.suffix}"
    if staged.resolve() == source.resolve():
        return source
    shutil.copy2(source, staged)
    return staged


def _import_session_fake() -> SimpleNamespace:
    """最小 session 伪造：复用 workbench 服务的导入方法（与测试同款手法），
    不触碰真实用户配置（config 写进临时目录）。"""
    config = GDLAgentConfig()
    config.recent_projects = []
    config_dir = tempfile.mkdtemp(prefix="mcp_import_")
    return SimpleNamespace(
        project=None,
        source="empty",
        source_path=None,
        recent_project_paths=[],
        config=config,
        config_path=Path(config_dir) / "config.toml",
        snapshot=lambda: {"ok": True},
        compiler_mode="mock",
        converter_path="",
        _choose_file_for_purpose=lambda purpose: None,
    )


def _import_gdl(source: Path, target_dir: Path, name: str | None, trace_id: str) -> dict:
    staged = _stage_source(source, target_dir, name)
    session = _import_session_fake()
    service = WorkbenchProjectService(session, real_compiler_factory=lambda _path: None)
    try:
        result = service.import_gdl_file({"path": str(staged)})
    except Exception as exc:
        return _make_error(
            "mcp_internal_error", f"GDL 导入失败: {exc}", trace_id, details={"path": str(source)}
        )
    if not result.get("ok"):
        return _make_error(
            "mcp_internal_error",
            f"GDL 导入失败: {result.get('error')}",
            trace_id,
            details={"path": str(source)},
        )
    warnings = list(result.get("warnings") or [])
    return {
        "ok": True,
        "project_path": str(session.source_path),
        "warnings": warnings,
        "trace_id": trace_id,
    }


def _import_gsm(source: Path, target_dir: Path, name: str | None, trace_id: str) -> dict:
    compiler = HSFCompiler()
    if not compiler.is_available:
        return _make_error(
            "converter_unavailable",
            f"LP_XMLConverter 不可用，无法导入 .gsm: {compiler.converter_path or '(未配置)'}",
            trace_id,
            details={"path": str(source)},
        )
    staged = _stage_source(source, target_dir, name)
    session = _import_session_fake()
    session.compiler_mode = "lp"
    service = WorkbenchProjectService(session, real_compiler_factory=lambda _path: compiler)
    try:
        result = service.import_gsm_file({"path": str(staged)})
    except Exception as exc:
        return _make_error(
            "mcp_internal_error", f"GSM 导入失败: {exc}", trace_id, details={"path": str(source)}
        )
    if not result.get("ok"):
        return _make_error(
            "mcp_internal_error",
            f"GSM 导入失败: {result.get('error')}",
            trace_id,
            details={"path": str(source)},
        )
    decompile = result.get("decompile") or {}
    stderr = str(decompile.get("stderr") or "").strip()
    warnings = [line.strip() for line in stderr.splitlines() if line.strip()][:20] if stderr else []
    # 规范化结果透传：有损/异常时把保留原始文件的人话 warning 带给 MCP 调用方
    normalization = result.get("normalization") or {}
    if not normalization.get("lossless"):
        warnings.append(
            str(normalization.get("warning") or "GSM 导入规范化失败，已保留原始文件")
        )
    return {
        "ok": True,
        "project_path": str(session.source_path),
        "warnings": warnings,
        "normalization": normalization,
        "trace_id": trace_id,
    }


def _import_blender_py(source: Path, target_dir: Path, name: str | None, trace_id: str) -> dict:
    from openbrep.importers.blender_script.converter import (
        convert_blender_script,
        probe_object_name,
    )

    try:
        content = source.read_text(encoding="utf-8-sig")
        if name:
            object_name = safe_project_name(name)
        else:
            base_name = probe_object_name(content, None, script_path=str(source))
            if base_name.startswith("<"):
                base_name = source.stem
            object_name = safe_project_name(base_name)
        object_name = unique_project_name(object_name, target_dir)
        project, ir = convert_blender_script(
            content,
            output_dir=str(target_dir),
            function_name=None,
            object_name=object_name,
            script_path=str(source),
        )
    except Exception as exc:
        return _make_error(
            "mcp_internal_error",
            f"Blender 脚本导入失败: {exc}",
            trace_id,
            details={"path": str(source)},
        )
    warnings = [f"line {w.line}: {w.operation} — {w.reason}" for w in ir.warnings]
    return {
        "ok": True,
        "project_path": str(project.root),
        "warnings": warnings,
        "trace_id": trace_id,
    }


# ── 工作区工具（Workspace，只读/低变更） ────────────────────


def workspace_init(path: str) -> dict:
    """初始化工作区：创建四区目录（materials/sources/hsf/artifacts）+
    .openbrep/workspace.toml。已存在则校验结构幂等返回；路径含非工作区内容
    时不炸，报告 conflicts。参数 path: 工作区目录绝对路径（string）。"""
    with _locked():
        trace_id = _next_trace_id()
        result = _ws_init(path)
        result["trace_id"] = trace_id
        return result


def workspace_scan(path: str) -> dict:
    """扫描工作区，返回索引：projects（hsf/ 下各 HSF 项目：名称/参数数/脚本清单/
    最新 revision/origin/成品数）、sources 文件清单、materials 计数、zones 完整性。
    参数 path: 工作区目录绝对路径（string）。"""
    with _locked():
        trace_id = _next_trace_id()
        result = _ws_scan(path)
        result["trace_id"] = trace_id
        return result


def workspace_search(path: str, query: str) -> dict:
    """跨项目搜索（大小写不敏感子串）：项目名/参数名/脚本内容，返回命中
    （项目、位置、行号、摘要行）。纯遍历不做索引。
    参数 path: 工作区目录绝对路径（string）；query: 搜索词（string）。"""
    with _locked():
        trace_id = _next_trace_id()
        result = _ws_search(path, query)
        result["trace_id"] = trace_id
        return result


# ── propose_skill / verify_skill（P2-b，skill 晋升门禁） ─────


# slice.scripts 键名 → ScriptType（与 apply_edit 的 _SCRIPT_TYPE_MAP 同口径）
_SKILL_SCRIPT_TYPE_MAP = {
    "1d": ScriptType.MASTER,
    "master": ScriptType.MASTER,
    "2d": ScriptType.SCRIPT_2D,
    "3d": ScriptType.SCRIPT_3D,
    "vl": ScriptType.PARAM,
    "ui": ScriptType.UI,
}

# structural 门禁要求的触发词小节标记（与 SkillsLoader 激活词提取同口径）
_TRIGGER_SECTION_MARKERS = ("触发关键词", "activation keywords", "适用场景", "when to use")


def _is_valid_skill_name(name: Any) -> bool:
    """skill 名合法性：非空字符串、无首尾空白、非 ./.. /隐藏文件 /README、
    不含路径分隔符或控制字符。"""
    if not isinstance(name, str) or not name:
        return False
    if name != name.strip():
        return False
    if name in (".", ".."):
        return False
    if name.upper() == "README":
        return False
    if name[0] == ".":
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    if any(ch in name for ch in ('/', "\\", "\x00", "<", ">", ":", '"', "|", "?", "*")):
        return False
    return True


def _fm_scalar_field(value: Any) -> str:
    """frontmatter 标量字段的值文本：换行拍平成空格，去首尾空白。"""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def propose_skill(
    name: str,
    content: str,
    pattern_type: str = "",
    source_project: str = "",
    source_trace_id: str = "",
    slice=None,
    skills_dir: str = "./skills",
) -> dict:
    """提出一个新 skill（写 {name}.md 到 skills_dir），不验证、不晋升。

    frontmatter 含：status: proposed、skill_version: 1、pattern_type、
    source_project、source_trace_id、reuse_count: 0、last_used: null（复用字段
    提前带上，让复用计数门禁对它生效）；slice 非空时把 slice dict 以单行 JSON
    存进 slice 字段（跨脚本 feature slice，极简 YAML 子集不支持任意深度嵌套）。

    slice.params 支持两种形态并存（写盘时按原样序列化，类型语义在 verify 时
    落地）：
    - 简写 {name: value}：按 Python 值类型推断（bool→Boolean / int→Integer /
      float→RealNum / 其余→String）；
    - 完整 {name: {value: v, type: "Length"|"Integer"|"RealNum"|"Boolean"|
      "Material"|"String"|"Angle"}}：value 为参数值，type 为声明的 GDL 类型。
    slice = {params: {...}, scripts: {"3d": "...", "2d": "...", ...}}，
    脚本键名映射：1d/master→MASTER、2d→SCRIPT_2D、3d→SCRIPT_3D、vl→PARAM、
    ui→UI。

    失败：同名文件已存在 → code="skill_exists"（不覆盖）；name 不合法 →
    code="invalid_spec"；slice.params 声明非法 type 或缺 value/type 键 →
    code="invalid_spec"。
    """
    with _locked():
        trace_id = _next_trace_id()
        if not _is_valid_skill_name(name):
            return _make_error(
                "invalid_spec",
                f"非法 skill 名（拒绝路径分隔符/首尾空白/隐藏文件等）: {name!r}",
                trace_id,
                details={"name": name},
            )
        if not isinstance(content, str):
            return _make_error(
                "invalid_spec",
                f"content 必须是字符串，收到: {type(content).__name__}",
                trace_id,
                details={"name": name},
            )
        if slice and isinstance(slice, dict) and slice.get("params"):
            slice_err = _validate_slice_params(slice["params"])
            if slice_err:
                return _make_error(
                    "invalid_spec",
                    f"slice.params 非法: {slice_err}",
                    trace_id,
                    details={"name": name, "params": slice.get("params")},
                )
        skills_path = Path(skills_dir).expanduser().resolve()
        try:
            skills_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return _make_error(
                "mcp_internal_error",
                f"无法创建 skills 目录: {exc}",
                trace_id,
                details={"skills_dir": skills_dir},
            )
        target = skills_path / f"{name}.md"
        if target.exists():
            return _make_error(
                "skill_exists",
                f"skill 已存在，不覆盖: {name}",
                trace_id,
                details={"name": name, "path": str(target)},
            )

        lines = [
            "status: proposed",
            "skill_version: 1",
            f"pattern_type: {_fm_scalar_field(pattern_type)}",
            f"source_project: {_fm_scalar_field(source_project)}",
            f"source_trace_id: {_fm_scalar_field(source_trace_id)}",
            "reuse_count: 0",
            "last_used: null",
        ]
        if slice:
            lines.append(f"slice: {json.dumps(slice, ensure_ascii=False, sort_keys=True)}")
        text = f"---\n{chr(10).join(lines)}\n---\n\n{content}"
        try:
            target.write_text(text, encoding="utf-8")
        except Exception as exc:
            return _make_error(
                "mcp_internal_error",
                f"写入 skill 文件失败: {exc}",
                trace_id,
                details={"path": str(target)},
            )
        return {
            "ok": True,
            "skill": name,
            "status": "proposed",
            "path": str(target),
            "trace_id": trace_id,
        }


def _slice_from_meta(meta: dict) -> dict | None:
    """从 skill_meta 取回 slice dict；无 slice / 解析失败返回 None。

    slice 在 frontmatter 里是单行 JSON 字符串（propose 时写入），读到的是原串；
    也容忍直接 dict 形态。空 slice（无 params 且无 scripts）视为无 slice。
    """
    raw = meta.get("slice")
    if raw is None:
        return None
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        return None
    if isinstance(data, dict) and (data.get("params") or data.get("scripts")):
        return data
    return None


# slice.params 完整形态可声明的 GDL 参数类型（hsf_project.VALID_PARAM_TYPES 的子集，
# 类型字符串→GDL 类型直接复用 hsf_project 的类型体系，不另建映射表）。
_SLICE_PARAM_TYPES = ("Length", "Integer", "RealNum", "Boolean", "Material", "String", "Angle")


def _is_full_param_spec(spec: Any) -> bool:
    """完整形态判定：{value: v, type: t}（简写形态是裸标量）。"""
    return isinstance(spec, dict) and "value" in spec and "type" in spec


def _validate_slice_params(params: Any) -> str | None:
    """校验 slice.params：简写 {name: value} 或完整 {name: {value, type}} 并存。

    返回错误消息；合法返回 None。完整形态缺 value/type 键、或 type 不在
    _SLICE_PARAM_TYPES 内 → 报错（调用方统一转 invalid_spec）。
    """
    if not params:
        return None
    if not isinstance(params, dict):
        return f"slice.params 必须是 dict，收到: {type(params).__name__}"
    for name, spec in params.items():
        if isinstance(spec, dict):
            if "value" not in spec or "type" not in spec:
                return (
                    f"参数 {name!r} 的完整形态必须同时含 'value' 与 'type' 键，"
                    f"收到: {spec!r}"
                )
            ptype = spec["type"]
            if ptype not in _SLICE_PARAM_TYPES:
                return (
                    f"参数 {name!r} 声明了非法类型 {ptype!r}，"
                    f"可选: {'/'.join(_SLICE_PARAM_TYPES)}"
                )
    return None


def _slice_param_type(spec: Any) -> str:
    """单条 slice 参数的 GDL 类型：完整形态用声明的 type，简写按值推断。"""
    if _is_full_param_spec(spec):
        return spec["type"]
    return _infer_param_type(spec)


def _slice_param_value(spec: Any) -> Any:
    """单条 slice 参数的实际值：完整形态取 value 字段，简写原样返回。"""
    if _is_full_param_spec(spec):
        return spec["value"]
    return spec


def _infer_param_type(value: Any) -> str:
    """slice.params 简写形态的值 → GDLParameter 类型（按 Python 值类型推断）。"""
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Integer"
    if isinstance(value, float):
        return "RealNum"
    return "String"


def _param_value_text(value: Any) -> str:
    """slice.params 的值 → GDLParameter.value 字符串。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _has_trigger_section(content: str) -> bool:
    """正文含触发词小节（## 触发关键词 / ## When to Use 等，与 SkillsLoader 激活词
    提取同口径）。"""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            lower = stripped.lower()
            if any(marker in lower for marker in _TRIGGER_SECTION_MARKERS):
                return True
    return False


def _write_verified_evidence(target: Path, block: dict) -> None:
    """晋升落盘：status 翻 verified + 写 verified_evidence 块（复用 skills_loader
    的公开行级写接口；失败静默——证据缺失不影响 status 已落盘）。"""
    rewrite_skill_frontmatter(
        target,
        updates={"status": "verified"},
        nested_blocks={"verified_evidence": block},
    )


def _verify_full_gate(
    name: str, target: Path, meta: dict, slice_data: dict, trace_id: str
) -> dict:
    """full 门禁：把 slice 落成最小 HSF 项目（tempfile.mkdtemp 里），跑
    compile_hsf(mock) + semantic_verify，两个都过才算过。

    slice.params 简写形态按值推断类型，完整形态（{value, type}）用声明的 GDL
    类型建 GDLParameter（类型字符串→GDL 类型复用 hsf_project 的类型体系）；
    声明非法 type → code="invalid_spec"。"""
    today = date.today().isoformat()
    params = slice_data.get("params") or {}
    scripts = slice_data.get("scripts") or {}
    slice_err = _validate_slice_params(params)
    if slice_err:
        return _make_error(
            "invalid_spec",
            f"skill slice 参数声明非法: {slice_err}",
            trace_id,
            details={"name": name, "params": params},
        )
    try:
        with tempfile.TemporaryDirectory(prefix="skill_verify_") as tmp:
            project = HSFProject.create_new(name, tmp)
            for pname, pspec in params.items():
                p = project.get_parameter(str(pname))
                if p is None:
                    p = GDLParameter(str(pname), _slice_param_type(pspec))
                    project.add_parameter(p)
                p.value = _param_value_text(_slice_param_value(pspec))
            unknown_scripts: list[str] = []
            for key, script_content in scripts.items():
                script_type = _SKILL_SCRIPT_TYPE_MAP.get(str(key).lower())
                if script_type is None:
                    unknown_scripts.append(str(key))
                    continue
                project.set_script(script_type, script_content)
            project.save_to_disk()

            compile_result = compile_hsf(str(project.root), mode="mock")
            semantic_result = semantic_verify(str(project.root))

        compile_mode = compile_result.get("mode")
        compile_success = compile_result.get("success") is True
        compile_errors = list(compile_result.get("errors") or [])
        if unknown_scripts:
            compile_success = False
            compile_errors.extend(f"未知脚本类型: {key}" for key in unknown_scripts)
        semantic_passed = semantic_result.get("passed") is True
    except Exception as exc:
        return {
            "ok": True,
            "name": name,
            "gate": "full",
            "passed": False,
            "evidence": {
                "gate": "full",
                "compile": {"mode": "mock", "success": False, "errors": [f"验证构建失败: {exc}"]},
                "semantic": {"passed": False},
                "at": today,
            },
            "status": "proposed",
            "trace_id": trace_id,
        }

    evidence: dict[str, Any] = {
        "gate": "full",
        "compile": {"mode": compile_mode, "success": compile_success},
        "semantic": {"passed": semantic_passed},
        "at": today,
    }
    if compile_errors:
        evidence["compile"]["errors"] = compile_errors

    passed = bool(compile_success and semantic_passed)
    if passed:
        _write_verified_evidence(
            target,
            {
                "gate": "full",
                "compile_mode": compile_mode,
                "compile_success": compile_success,
                "semantic_passed": semantic_passed,
                "at": today,
            },
        )
    return {
        "ok": True,
        "name": name,
        "gate": "full",
        "passed": passed,
        "evidence": evidence,
        "status": "verified" if passed else "proposed",
        "trace_id": trace_id,
    }


def _verify_structural_gate(
    name: str, target: Path, meta: dict, content: str, trace_id: str
) -> dict:
    """structural 门禁：frontmatter 完整（status/pattern_type 非空）+ 正文含
    触发词小节。带 slice 的 skill 不走这里。"""
    today = date.today().isoformat()
    frontmatter_ok = bool(meta.get("status")) and bool(meta.get("pattern_type"))
    trigger_section = _has_trigger_section(content)
    passed = bool(frontmatter_ok and trigger_section)

    evidence = {
        "gate": "structural",
        "structural": {
            "frontmatter_complete": frontmatter_ok,
            "pattern_type": meta.get("pattern_type"),
            "trigger_section": trigger_section,
        },
        "at": today,
    }
    if passed:
        _write_verified_evidence(
            target,
            {
                "gate": "structural",
                "pattern_type": meta.get("pattern_type"),
                "trigger_section": True,
                "at": today,
            },
        )
    return {
        "ok": True,
        "name": name,
        "gate": "structural",
        "passed": passed,
        "evidence": evidence,
        "status": "verified" if passed else "proposed",
        "trace_id": trace_id,
    }


def verify_skill(name: str, skills_dir: str = "./skills") -> dict:
    """skill 晋升门禁（P2-b 心脏）。

    双闸门，按 skill 是否带 slice 分流：
    - 带 slice（gate="full"）：tempfile.mkdtemp 里 HSFProject.create_new 造最小
      项目 → slice.params 写参数（简写 {name: value} 按值类型推断：
      bool→Boolean / int→Integer / float→RealNum / str→String；完整形态
      {name: {value, type}} 用声明的 GDL 类型建 GDLParameter，类型字符串→GDL
      类型复用 hsf_project 类型体系；声明非法 type → invalid_spec）→
      slice.scripts 按脚本类型 set_script（键名映射：1d/master→MASTER、
      2d→SCRIPT_2D、3d→SCRIPT_3D、vl→PARAM、ui→UI）→ save_to_disk →
      compile_hsf(mock) + semantic_verify，两个都过才过。
    - 不带 slice 的纯策略 skill（gate="structural"）：frontmatter 完整
      （status/pattern_type 非空）+ 正文含触发词小节（## 触发关键词 / When to
      Use）。

    通过：写 verified_evidence 块 + status 翻 verified（verified 即可注入，即
    自动 active）；失败：status 保持 proposed 不动。读 skill 内容直接读文件
    （get_by_name 对 proposed 返回 None），元数据用 SkillsLoader.skill_meta。
    失败：skill 不存在 → code="skill_not_found"；slice 声明非法 type →
    code="invalid_spec"。
    """
    with _locked():
        trace_id = _next_trace_id()
        skills_path = Path(skills_dir).expanduser().resolve()
        loader = SkillsLoader(str(skills_path))
        loader.load()
        if name not in loader.skill_names:
            return _make_error(
                "skill_not_found",
                f"skill 不存在: {name}",
                trace_id,
                details={"name": name, "skills_dir": str(skills_path)},
            )
        meta = loader.skill_meta(name)
        target = skills_path / f"{name}.md"
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return _make_error(
                "mcp_internal_error",
                f"读取 skill 文件失败: {exc}",
                trace_id,
                details={"path": str(target)},
            )

        slice_data = _slice_from_meta(meta)
        if slice_data:
            return _verify_full_gate(name, target, meta, slice_data, trace_id)
        return _verify_structural_gate(name, target, meta, content, trace_id)


# ── reuse_skill / list_skills / deprecate_skill（P2-c，skill 管理） ─────

_VALID_SKILL_STATUSES = ("active", "verified", "proposed", "deprecated")


def _skill_blocks_from_injected(loader: SkillsLoader, skills_text: str) -> list[tuple[str, str]]:
    """从 get_for_task 的注入文本解析命中 skill 的 (name, 正文) 序列。

    注入格式为 `## Skill: {name}\n\n{body}`，按 `## Skill: ` 头解析名字
    （顺序即注入顺序）；正文取 loader 的 body（与注入内容同源）。
    """
    header_re = re.compile(r"^## Skill: ([^\n]+)$", re.MULTILINE)
    blocks: list[tuple[str, str]] = []
    for m in header_re.finditer(skills_text):
        name = m.group(1).strip()
        blocks.append((name, loader._skills.get(name, "")))
    return blocks


def reuse_skill(query: str, skills_dir: str = "./skills") -> dict:
    """按任务描述检索可注入 skill 并返回注入文本（只读包装 SkillsLoader）。

    get_for_task(query) 命中 active/verified 的 skill 并把拼接好的注入文本
    原样返回给调用方使用——因此本工具"调用即计复用"：每次命中都会把该 skill
    的 reuse_count +1、last_used 记为今天（P2-a 复用计数语义，属预期行为）。
    docstring 注明：本工具返回的 skills_text 就是给调用方注入用的。

    matched 为命中详情：{name, status, pattern_type, reuse_count, excerpt}
    （excerpt 取正文前 200 字符）。proposed / deprecated 不会出现在 matched。

    失败：query 非字符串或空 → code="invalid_spec"。
    """
    with _locked():
        trace_id = _next_trace_id()
        if not isinstance(query, str) or not query:
            return _make_error(
                "invalid_spec",
                f"query 必须是非空字符串，收到: {query!r}",
                trace_id,
                details={"query": query},
            )
        try:
            skills_path = Path(skills_dir).expanduser().resolve()
            loader = SkillsLoader(str(skills_path))
            loader.load()
            skills_text = loader.get_for_task(query)
            matched = []
            for name, body in _skill_blocks_from_injected(loader, skills_text):
                meta = loader.skill_meta(name)
                matched.append({
                    "name": name,
                    "status": meta.get("status"),
                    "pattern_type": meta.get("pattern_type"),
                    "reuse_count": meta.get("reuse_count"),
                    "excerpt": body[:200],
                })
            return {
                "ok": True,
                "query": query,
                "matched": matched,
                "skills_text": skills_text,
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"reuse_skill 失败: {exc}", trace_id)


def list_skills(status: str | None = None, skills_dir: str = "./skills") -> dict:
    """列出全部 skill 及其元数据（含 proposed/deprecated，管理面视图）。

    每条：{name, status, pattern_type, skill_version, reuse_count, last_used,
    source_project}。status 非空时按状态过滤（active/verified/proposed/
    deprecated）；status 非法值 → code="invalid_mode"。
    """
    with _locked():
        trace_id = _next_trace_id()
        if status and status not in _VALID_SKILL_STATUSES:
            return _make_error(
                "invalid_mode",
                f"非法 status: {status!r}（可选: {'/'.join(_VALID_SKILL_STATUSES)}）",
                trace_id,
                details={"status": status},
            )
        try:
            skills_path = Path(skills_dir).expanduser().resolve()
            loader = SkillsLoader(str(skills_path))
            loader.load()
            skills = []
            for name in loader.skill_names:
                meta = loader.skill_meta(name)
                if status and meta.get("status") != status:
                    continue
                skills.append({
                    "name": name,
                    "status": meta.get("status"),
                    "pattern_type": meta.get("pattern_type"),
                    "skill_version": meta.get("skill_version"),
                    "reuse_count": meta.get("reuse_count"),
                    "last_used": meta.get("last_used"),
                    "source_project": meta.get("source_project"),
                })
            return {
                "ok": True,
                "skills": skills,
                "total": len(skills),
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"list_skills 失败: {exc}", trace_id)


def deprecate_skill(name: str, skills_dir: str = "./skills") -> dict:
    """把 skill 翻为 deprecated（不再注入，文件保留不删除，保留溯源）。

    复用 skills_loader.rewrite_skill_frontmatter 行级写接口。翻完后
    SkillsLoader 不再注入该 skill（_INJECTABLE_STATUSES 不含 deprecated）。
    已是 deprecated 的重复调用幂等返回成功。

    失败：name 不合法 → code="invalid_spec"；skill 不存在 → code="skill_not_found"；
    写回失败 → code="mcp_internal_error"。
    """
    with _locked():
        trace_id = _next_trace_id()
        if not _is_valid_skill_name(name):
            return _make_error(
                "invalid_spec",
                f"非法 skill 名（拒绝路径分隔符/首尾空白/隐藏文件等）: {name!r}",
                trace_id,
                details={"name": name},
            )
        skills_path = Path(skills_dir).expanduser().resolve()
        target = skills_path / f"{name}.md"
        if not target.is_file():
            return _make_error(
                "skill_not_found",
                f"skill 不存在: {name}",
                trace_id,
                details={"name": name, "skills_dir": str(skills_path)},
            )
        try:
            loader = SkillsLoader(str(skills_path))
            loader.load()
            if loader.skill_meta(name).get("status") == "deprecated":
                return {"ok": True, "name": name, "status": "deprecated", "trace_id": trace_id}
            rewritten = rewrite_skill_frontmatter(target, updates={"status": "deprecated"})
            if not rewritten:
                return _make_error(
                    "mcp_internal_error",
                    f"翻 deprecated 写回失败（无 frontmatter 或写盘失败）: {name}",
                    trace_id,
                    details={"path": str(target)},
                )
            return {"ok": True, "name": name, "status": "deprecated", "trace_id": trace_id}
        except Exception as exc:
            return _make_error("mcp_internal_error", f"deprecate_skill 失败: {exc}", trace_id)
