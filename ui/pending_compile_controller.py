from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable

from ui.proposed_preview_controller import build_project_with_pending_diffs


def run_pending_compile_preflight(
    proj,
    pending_diffs: dict,
    *,
    gsm_name: str | None,
    script_map: list[tuple[object, str, str]],
    parse_paramlist_text_fn: Callable[[str], list],
    get_compiler_fn: Callable[[], object],
    compiler_mode: str,
    set_pending_compile_result_fn: Callable[[tuple[bool, str]], None],
    set_pending_compile_meta_fn: Callable[[dict], None],
    deepcopy_fn,
) -> tuple[bool, str]:
    if proj is None:
        return False, "❌ 当前没有项目，无法编译预检 AI 提案"
    if not pending_diffs:
        return False, "❌ 当前没有 AI 提案可编译预检"

    with tempfile.TemporaryDirectory(prefix="openbrep-pending-compile-") as tmp:
        temp_root = Path(tmp)
        proposed = build_project_with_pending_diffs(
            proj,
            pending_diffs,
            script_map=script_map,
            parse_paramlist_text_fn=parse_paramlist_text_fn,
            deepcopy_fn=deepcopy_fn,
        )
        compile_name = _safe_compile_name(gsm_name or getattr(proj, "name", "") or "pending")
        proposed.name = compile_name
        proposed.work_dir = temp_root
        proposed.root = temp_root / compile_name

        hsf_dir = proposed.save_to_disk()
        output_gsm = temp_root / "output" / f"{compile_name}.gsm"
        result = get_compiler_fn().hsf2libpart(str(hsf_dir), str(output_gsm))

        ok = bool(result.success)
        msg = _format_pending_compile_message(result=result, compiler_mode=compiler_mode)
        meta = {
            "success": ok,
            "compiler_mode": compiler_mode,
            "exit_code": getattr(result, "exit_code", 0),
            "error_count": len(getattr(result, "errors", []) or []),
            "warning_count": len(getattr(result, "warnings", []) or []),
            "changed_paths": _changed_paths(pending_diffs),
            "stderr": (getattr(result, "stderr", "") or "")[:800],
            "stdout": (getattr(result, "stdout", "") or "")[:800],
        }
        set_pending_compile_result_fn((ok, msg))
        set_pending_compile_meta_fn(meta)
        return ok, msg


def _safe_compile_name(raw: str) -> str:
    name = re.sub(r"[/:\\]+", "_", str(raw or "").strip())
    return name or "pending"


def _changed_paths(pending_diffs: dict) -> list[str]:
    return sorted([
        str(path)
        for path in pending_diffs.keys()
        if str(path).startswith("scripts/") or str(path) == "paramlist.xml"
    ])


def _format_pending_compile_message(*, result, compiler_mode: str) -> str:
    mock_tag = " [Mock]" if str(compiler_mode).startswith("Mock") else ""
    if result.success:
        msg = f"✅ **提案编译预检通过{mock_tag}**"
        if str(compiler_mode).startswith("Mock"):
            msg += "\n\n⚠️ Mock 模式只验证 HSF 结构和基础脚本配对，不生成真实可交付 GSM。"
        return msg

    stderr = getattr(result, "stderr", "") or "(无错误输出)"
    return f"❌ **提案编译预检失败**\n\n```\n{stderr[:500]}\n```"
