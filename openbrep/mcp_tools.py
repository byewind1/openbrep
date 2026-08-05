"""MCP 工具契约层（Phase 1 / P1-b+d）。

纯 Python，dict in / dict out。禁止 import 任何 mcp 库 —— 协议适配层后续才做。

契约约定：
- 每个工具函数第一个参数是 HSF 项目目录绝对路径 path（字符串）。
- 成功返回：{ok: True, ..., trace_id}
- 失败返回：{ok: False, error: {code, message, details?}, trace_id}
- 任何异常都不许穿透：统一包成错误 dict 返回。
- trace_id 格式：mcp-YYYYMMDD-NNNN（日内序号，模块级计数器）。
- mutation 工具本包不做（后续 P1-c），但骨架预留模块级 threading.RLock 与
  _locked() 上下文管理器；只读工具 v1 也走它（串行最稳）。

错误 code 汇总（全部工具共用同一错误形态）：
- project_not_found      path 不存在 / 不是合法 HSF 项目 / 项目加载失败 /
                         import_source 的源文件不存在
- invalid_mode           compile_hsf 的 mode 非法 / import_source 的 kind 非法 /
                         源文件后缀与 kind 不匹配
- converter_unavailable  LP_XMLConverter 不可用（import_source kind="gsm"）
- mcp_internal_error     工具内部意外异常（预览、编译、导入、服务调用等）
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject
from openbrep.revisions import get_latest_revision_id, is_hsf_project_dir
from openbrep.workbench.project_service import WorkbenchProjectService
from openbrep.workbench.project_session_service import safe_project_name, unique_project_name

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
                "trace_id": trace_id,
            }
        except Exception as exc:
            return _make_error("mcp_internal_error", f"load_project 失败: {exc}", trace_id)


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

        if mode == "mock":
            compiler: MockHSFCompiler | HSFCompiler = MockHSFCompiler()
            effective_mode = "mock"
        else:
            real = HSFCompiler()
            if mode == "real" or real.is_available:
                compiler = real
                effective_mode = "real"
            else:
                compiler = MockHSFCompiler()
                effective_mode = "mock"

        try:
            out_dir = tempfile.mkdtemp(prefix="mcp_compile_")
            output_gsm = str(Path(out_dir) / f"{root.name}.gsm")
            result = compiler.hsf2libpart(str(root), output_gsm)
            return {
                "ok": True,
                "mode": result.mode or effective_mode,
                "success": result.success,
                "errors": list(result.errors or []),
                "warnings": list(result.warnings or []),
                "exit_code": result.exit_code,
                "output_path": result.output_path,
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


# ── render_evidence ──────────────────────────────────────


def render_evidence(path: str, sweep_params: list[str] | None = None) -> dict:
    """机器可读几何证据：包围盒、网格统计、参数扫掠响应（单位：米）。

    不是给人看的 Three.js payload，而是可供下游工具/LLM 判读的结构化证据：
    复用 preview_payload 做 3D 预览，_scene_bbox 算包围盒，sweep_parameters
    的扰动机制做参数扫掠。

    sweep_params 为空 = 不扫掠。预览内部异常降级为 warning + ok:True +
    mesh_stats 为空（参考 verify_semantics 的 preview_error 降级策略），
    不让工具整体失败。
    """
    with _locked():
        trace_id = _next_trace_id()
        loaded = _load_project(path, trace_id)
        if isinstance(loaded, dict):
            return loaded
        _root, project = loaded

        from openbrep.semantic_verifier import (
            DEFAULT_BBOX_TOLERANCE,
            _scene_bbox,
        )
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
                bbox_info["size"], declared_dims, tolerance=DEFAULT_BBOX_TOLERANCE
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
    return {
        "ok": True,
        "project_path": str(session.source_path),
        "warnings": [],
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
    return {
        "ok": True,
        "project_path": str(session.source_path),
        "warnings": warnings,
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
