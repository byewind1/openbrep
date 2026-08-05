"""MCP 工具契约层骨架（Phase 1 / P1-a）。

纯 Python，dict in / dict out。禁止 import 任何 mcp 库 —— 协议适配层后续才做。

契约约定：
- 每个工具函数第一个参数是 HSF 项目目录绝对路径 path（字符串）。
- 成功返回：{ok: True, ..., trace_id}
- 失败返回：{ok: False, error: {code, message, details?}, trace_id}
- 任何异常都不许穿透：统一包成错误 dict 返回。
- path 不存在或不是合法 HSF 项目 → code="project_not_found"。
- trace_id 格式：mcp-YYYYMMDD-NNNN（日内序号，模块级计数器）。
- mutation 工具本包不做（后续 P1-c），但骨架预留模块级 threading.RLock 与
  _locked() 上下文管理器；只读工具 v1 也走它（串行最稳）。
"""

from __future__ import annotations

import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.hsf_project import HSFProject
from openbrep.revisions import get_latest_revision_id

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

    合法性判定与 revisions.is_hsf_project_dir 一致：存在 libpartdata.xml 或 scripts/。
    """
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"HSF 项目目录不存在: {root}")
    if not ((root / "libpartdata.xml").exists() or (root / "scripts").is_dir()):
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
