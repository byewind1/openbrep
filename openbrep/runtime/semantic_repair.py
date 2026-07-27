"""Semantic repair loop (S1): close the detect → repair → re-detect cycle.

`verify_semantics` detects geometry-level failures (mesh_empty / mesh_degenerate /
bbox_mismatch) with the deterministic local previewer — fully independent of the
generation context, so there is no self-confirmation. This module feeds that
evidence back to the LLM for bounded repair rounds.

A round is accepted only when compile still passes (if a compiler is configured)
AND the blocking-issue count strictly decreases; otherwise the round is rolled
back, so delivery is never worse than no repair. Used by both the CREATE path
and the MODIFY/DEBUG/REPAIR path in `openbrep/runtime/pipeline.py`.
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
            agent._apply_changes(project, round_cleaned)

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
