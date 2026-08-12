"""Semantic repair loop (S1): close the detect → repair → re-detect cycle.

`verify_semantics` detects geometry-level failures (mesh_empty / mesh_degenerate /
bbox_mismatch) with the deterministic local previewer — fully independent of the
generation context, so there is no self-confirmation. This module feeds that
evidence back to the LLM for bounded repair rounds.

A round is accepted only when compile still passes (if a compiler is configured)
AND the blocking-issue count strictly decreases; otherwise the round is rolled
back, so delivery is never worse than no repair. Used by both the CREATE path
and the MODIFY/DEBUG/REPAIR path in `openbrep/runtime/pipeline.py`.

P8 防退化守卫（确定性，与模型强弱无关）：接受判定前先拦截退化修复——
修复脚本含独立成行的省略号残桩（.../…）、修复后脚本内容锐减、或修复后
参数表丢失 A/B/ZZYZX / 参数总数下降，一律拒绝并回退。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional

from openbrep.compiler import CompileResult
from openbrep.gdl_sanitizer import sanitize_llm_script_output

logger = logging.getLogger(__name__)

MAX_SEMANTIC_REPAIR_ROUNDS = 2

# ── P8 防退化守卫阈值（测试钉死，勿随手改）────────────────────────────────
# 独立成行的省略号残桩（模型退化输出标记，如 "3d.gdl 第 2 行是字面省略号 ..."）
ELLIPSIS_STUB_LINES: frozenset[str] = frozenset({"...", "…"})
# 内容锐减守卫：修复前 ≥ MIN_PRE_SHRINK_LINES 行的脚本掉到 ≤ SHRUNK_SCRIPT_MAX_LINES 行，
# 或全脚本总行数掉过 TOTAL_LINE_DROP_RATIO（50%），视为退化。
MIN_PRE_SHRINK_LINES = 10
SHRUNK_SCRIPT_MAX_LINES = 3
TOTAL_LINE_DROP_RATIO = 0.5
# 参数丢失守卫：修复后缺失任一 ArchiCAD 保留参数即拒绝
RESERVED_PARAM_NAMES: tuple[str, ...] = ("A", "B", "ZZYZX")


def _find_ellipsis_stub(cleaned: dict[str, str]) -> Optional[tuple[str, int]]:
    """返回 (文件路径, 行号) 若任一脚本含独立成行的 .../…；行内注释里的 ... 不误报。

    只匹配"去掉行内注释后整行就是省略号"的情况——`! ...` 注释行、代码行里的
    `...`（如 `BLOCK ...`）都不是残桩。
    """
    for fpath, code in (cleaned or {}).items():
        for idx, line in enumerate((code or "").splitlines(), start=1):
            code_part = line.split("!", 1)[0].strip()
            if code_part in ELLIPSIS_STUB_LINES:
                return fpath, idx
    return None


def _count_code_lines(code: str) -> int:
    """非空非注释行数：空行与 `!` 开头（或 [FILE: 元数据）的行不算代码。"""
    n = 0
    for line in (code or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("!"):
            n += 1
    return n


def _is_degenerate(prev_scripts: dict, new_scripts: dict) -> Optional[str]:
    """内容锐减守卫：返回拒绝原因（None = 未退化）。

    规则：任一修复前 ≥ MIN_PRE_SHRINK_LINES 行的脚本掉到 ≤ SHRUNK_SCRIPT_MAX_LINES 行，
    或全脚本总行数掉过 TOTAL_LINE_DROP_RATIO。
    """
    for key in set(prev_scripts) | set(new_scripts):
        prev_n = _count_code_lines(prev_scripts.get(key) or "")
        new_n = _count_code_lines(new_scripts.get(key) or "")
        if prev_n >= MIN_PRE_SHRINK_LINES and new_n <= SHRUNK_SCRIPT_MAX_LINES:
            label = getattr(key, "value", str(key))
            return f"因修复后脚本内容锐减（{label}：{prev_n} 行 → {new_n} 行）被拒绝"
    prev_total = sum(_count_code_lines(c) for c in (prev_scripts or {}).values())
    new_total = sum(_count_code_lines(c) for c in (new_scripts or {}).values())
    if prev_total > 0 and new_total < prev_total * TOTAL_LINE_DROP_RATIO:
        return f"因修复后脚本总行数下降过半（{prev_total} → {new_total} 行）被拒绝"
    return None


def _param_loss_reason(prev_params: list, new_params: list) -> Optional[str]:
    """参数丢失守卫：返回拒绝原因（None = 参数表未退化）。"""
    prev_params = prev_params or []
    new_params = new_params or []
    new_names = {getattr(p, "name", "") for p in new_params}
    missing = [n for n in RESERVED_PARAM_NAMES if n not in new_names]
    if missing:
        return f"因修复后参数表丢失 ArchiCAD 保留参数（{', '.join(missing)}）被拒绝"
    if len(new_params) < len(prev_params):
        return f"因修复后参数总数下降（{len(prev_params)} → {len(new_params)}）被拒绝"
    return None


@dataclass
class SemanticRepairOutcome:
    """Updated pipeline state after the loop (accepted rounds applied)."""

    cleaned: dict[str, str]
    compile_result: Optional[CompileResult]
    semantic_result: Any  # SemanticVerificationResult
    lint_summary: str
    auto_repair_info: str
    accepted_rounds: int
    rounds_attempted: int = 0


def _join_info(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


def run_semantic_repair_loop(
    *,
    agent,
    project,
    cleaned: dict[str, str],
    compile_result: Optional[CompileResult],
    semantic_result,
    instruction: str,
    knowledge: str,
    skills_text: str,
    history,
    compiler,
    compiler_configured: bool,
    gsm_path: Optional[str],
    lint_summary: str,
    auto_repair_info: str,
    on_event: Callable,
    lint_fn: Callable,
    max_rounds: int = MAX_SEMANTIC_REPAIR_ROUNDS,
) -> SemanticRepairOutcome:
    """Run bounded semantic repair rounds against ``project`` in place.

    ``instruction`` is the same enriched instruction used for generation, so the
    repair call shares the generation context — that is safe because the *judge*
    (verify_semantics / previewer) is deterministic and context-independent.
    """
    # 延迟导入：与 pipeline 保持一致，便于测试 patch 模块属性
    from openbrep.semantic_verifier import verify_semantics

    new_cleaned = dict(cleaned)
    accepted = 0
    round_no = 0
    compile_gate_ok = (not compiler_configured) or (
        compile_result is not None and compile_result.success
    )

    while (
        compile_gate_ok
        and new_cleaned
        and any(issue.blocking for issue in semantic_result.issues)
        and round_no < max_rounds
    ):
        round_no += 1
        prev_blocking = [i for i in semantic_result.issues if i.blocking]
        prev_scripts = dict(project.scripts)
        prev_params = deepcopy(project.parameters)
        prev_cleaned = dict(new_cleaned)
        prev_compile_result = compile_result

        issue_lines = "\n".join(
            f"- [{i.check_type}] {i.detail}" for i in prev_blocking
        )
        hint_issues = [
            i for i in semantic_result.issues
            if not i.blocking and i.check_type == "sweep_unresponsive"
        ]
        hint_block = ""
        if hint_issues:
            hint_block = "\n附带提示（非阻断，能改则改）：\n" + "\n".join(
                f"- [{i.check_type}] {i.detail}" for i in hint_issues
            )
        on_event("status", {
            "message": f"🧩 几何语义验证未过（第 {round_no} 轮），正在自动修复…"
        })
        logger.info(
            "Semantic repair round %d; blocking=%d", round_no, len(prev_blocking),
        )
        repair_instruction = (
            f"{instruction}\n\n"
            f"脚本已能编译，但几何语义验证发现以下问题（第 {round_no} 轮），"
            f"请基于当前脚本进行最小改动修复：\n{issue_lines}{hint_block}"
        )

        def _rollback() -> None:
            project.scripts.clear()
            project.scripts.update(prev_scripts)
            project.parameters = prev_params

        try:
            changes, _plain = agent.generate_only(
                instruction=repair_instruction,
                project=project,
                knowledge=knowledge,
                skills=skills_text,
                include_all_scripts=True,
                history=history,
            )
            round_cleaned = (
                {k: sanitize_llm_script_output(v, k) for k, v in changes.items()}
                if changes else {}
            )
            round_cleaned, round_lint = lint_fn(round_cleaned, on_event=on_event)
            if round_lint:
                lint_summary = _join_info(lint_summary, round_lint)
            if not round_cleaned:
                auto_repair_info = _join_info(
                    auto_repair_info,
                    f"🧩 第 {round_no} 轮几何语义修复未产出可应用的修改，停止重试",
                )
                break

            # ── P8 退化守卫 1：省略号残桩（独立成行的 .../…）——不进接受判定，
            # 直接回退。行内注释里的 ... 不误报。─────────────────────────────
            ellipsis_hit = _find_ellipsis_stub(round_cleaned)
            if ellipsis_hit is not None:
                _rollback()
                new_cleaned = prev_cleaned
                compile_result = prev_compile_result
                auto_repair_info = _join_info(
                    auto_repair_info,
                    f"🧩 第 {round_no} 轮几何语义修复因脚本含省略号残桩"
                    f"（{ellipsis_hit[0]} 第 {ellipsis_hit[1]} 行）被拒绝，已回退",
                )
                break

            agent._apply_changes(project, round_cleaned)

            # ── P8 退化守卫 2/3：内容锐减 / 参数丢失（对比修复前后，命中即回退）──
            degrade_reason = _is_degenerate(prev_scripts, project.scripts)
            if degrade_reason is None:
                degrade_reason = _param_loss_reason(prev_params, project.parameters)
            if degrade_reason is not None:
                _rollback()
                new_cleaned = prev_cleaned
                if compiler_configured and gsm_path:
                    compile_result = compiler.hsf2libpart(
                        str(project.save_to_disk()), gsm_path
                    )
                else:
                    project.save_to_disk()
                    compile_result = prev_compile_result
                auto_repair_info = _join_info(
                    auto_repair_info,
                    f"🧩 第 {round_no} 轮几何语义修复{degrade_reason}，已回退",
                )
                break

            # 编译门：配置编译器时，修复后必须仍通过编译
            round_compile_ok = True
            if compiler_configured and gsm_path:
                compile_result = compiler.hsf2libpart(
                    str(project.save_to_disk()), gsm_path
                )
                on_event("compile_result", {
                    "success": compile_result.success,
                    "error": compile_result.stderr if not compile_result.success else "",
                })
                round_compile_ok = compile_result.success

            new_semantic = verify_semantics(project)
            new_blocking = [i for i in new_semantic.issues if i.blocking]
            if round_compile_ok and len(new_blocking) < len(prev_blocking):
                # 接受本轮修复
                new_cleaned.update(round_cleaned)
                semantic_result = new_semantic
                accepted += 1
                auto_repair_info = _join_info(
                    auto_repair_info,
                    f"🧩 第 {round_no} 轮几何语义修复生效："
                    f"阻断问题 {len(prev_blocking)} → {len(new_blocking)}",
                )
            else:
                # 未改善或改坏编译：回退并恢复编译产物，保证交付不劣化
                _rollback()
                new_cleaned = prev_cleaned
                if compiler_configured and gsm_path:
                    compile_result = compiler.hsf2libpart(
                        str(project.save_to_disk()), gsm_path
                    )
                else:
                    project.save_to_disk()
                    compile_result = prev_compile_result
                reject_reason = (
                    "修复后编译失败" if not round_compile_ok else "阻断问题数未下降"
                )
                auto_repair_info = _join_info(
                    auto_repair_info,
                    f"🧩 第 {round_no} 轮几何语义修复{reject_reason}，已回退",
                )
                break
        except Exception as exc:
            _rollback()
            new_cleaned = prev_cleaned
            compile_result = prev_compile_result
            logger.warning("Semantic repair round %d exception: %s", round_no, exc)
            auto_repair_info = _join_info(
                auto_repair_info,
                f"🧩 第 {round_no} 轮几何语义修复异常：{exc}",
            )
            break

    return SemanticRepairOutcome(
        cleaned=new_cleaned,
        compile_result=compile_result,
        semantic_result=semantic_result,
        lint_summary=lint_summary,
        auto_repair_info=auto_repair_info,
        accepted_rounds=accepted,
        rounds_attempted=round_no,
    )
