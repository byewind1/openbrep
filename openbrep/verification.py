"""
Verification as a first-class seam.

Aggregates the checks that are currently scattered across the pipeline
(static checker, GDL linter, compile validation, object_plan.validation_checks)
into a single :class:`VerificationReport`. Turns the plan's
``validation_checks`` from prompt-only text into executed pass/fail/unknown
checklist items.

This is the first step of the verification seam described in
"OpenBrep Agent 自校正方法论落实度审计" (Phase 3 + Phase 4 + part of Phase 5).
It does NOT change existing control flow or success semantics — it only
observes and reports. The CREATE path still does not compile; that gap is
reported honestly as ``compile=not_run``, paving the way for a future Phase 2
that brings compile verification to the CREATE path.

Design rules:
- Plan checks that cannot be mapped to a deterministic checker are reported as
  UNKNOWN — never silently passed.
- ``passed`` only fails on blocking checks (static / compile / plan_check that
  actually ran). UNKNOWN and NOT_RUN lower confidence instead of failing.
- The summary text is compact to avoid becoming UI noise (per audit risk note).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openbrep.compiler import CompileResult
    from openbrep.hsf_project import HSFProject
    from openbrep.object_planner import GDLObjectPlan
    from openbrep.static_checker import StaticCheckResult

__all__ = [
    "CheckStatus",
    "VerificationCheck",
    "VerificationReport",
    "run_plan_validation_checks",
    "build_verification_report",
]


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_RUN = "not_run"


@dataclass
class VerificationCheck:
    """One executed (or not-executed) verification check."""

    name: str
    check_type: str            # static | lint | compile | plan_check
    status: CheckStatus
    detail: str = ""
    auto_repairable: bool = False
    line_errors: list = field(default_factory=list)  # [{line_number, severity, message}]


@dataclass
class VerificationReport:
    """Unified, proof-oriented report for one pipeline task."""

    intent: str
    goal: str = ""
    checks: list[VerificationCheck] = field(default_factory=list)
    errors_caught: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    remaining_risks: list[str] = field(default_factory=list)
    confidence: str = "low"     # low | medium | high
    graph_powered: bool = False  # 本次任务使用了图谱约束或诊断

    # ── derived views ───────────────────────────────────────

    @property
    def passed(self) -> bool:
        """True when no blocking check failed.

        Blocking = static / compile / plan_check that ran. UNKNOWN and
        NOT_RUN do not fail the report (they lower confidence instead).
        """
        for c in self.checks:
            if c.status == CheckStatus.FAIL and c.check_type in ("static", "compile", "plan_check"):
                return False
        return True

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in CheckStatus}
        for c in self.checks:
            counts[c.status.value] += 1
        return counts

    def compile_status(self) -> str:
        for c in self.checks:
            if c.check_type == "compile":
                return c.status.value
        return "not_run"

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "goal": self.goal,
            "passed": self.passed,
            "confidence": self.confidence,
            "graph_powered": self.graph_powered,
            "counts": self.counts(),
            "checks": [
                {
                    "name": c.name,
                    "check_type": c.check_type,
                    "status": c.status.value,
                    "detail": c.detail,
                    "auto_repairable": c.auto_repairable,
                    **({"line_errors": c.line_errors} if c.line_errors else {}),
                }
                for c in self.checks
            ],
            "errors_caught": list(self.errors_caught),
            "fixes_applied": list(self.fixes_applied),
            "remaining_risks": list(self.remaining_risks),
        }

    def to_trace_dict(self) -> dict:
        c = self.counts()
        return {
            "passed": self.passed,
            "confidence": self.confidence,
            "check_count": len(self.checks),
            "pass_count": c["pass"],
            "fail_count": c["fail"],
            "unknown_count": c["unknown"],
            "compile_status": self.compile_status(),
            "errors_caught_count": len(self.errors_caught),
            "fixes_applied_count": len(self.fixes_applied),
        }

    def to_summary_text(self) -> str:
        lines = ["### 验证报告"]

        static_chk = _find(self.checks, "static")
        if static_chk:
            lines.append(
                f"- 静态检查：{_status_icon(static_chk.status)} "
                f"{static_chk.detail or static_chk.status.value}"
            )

        lint_chk = _find(self.checks, "lint")
        if lint_chk:
            lines.append(
                f"- Linter：{_status_icon(lint_chk.status)} "
                f"{lint_chk.detail or lint_chk.status.value}"
            )

        compile_chk = _find(self.checks, "compile")
        if compile_chk:
            label = _compile_label(compile_chk.status)
            extra = f"（{compile_chk.detail}）" if compile_chk.detail else ""
            lines.append(f"- 编译：{label}{extra}")

        plan_checks = [c for c in self.checks if c.check_type == "plan_check"]
        if plan_checks:
            p = sum(1 for c in plan_checks if c.status == CheckStatus.PASS)
            f = sum(1 for c in plan_checks if c.status == CheckStatus.FAIL)
            u = sum(
                1 for c in plan_checks
                if c.status in (CheckStatus.UNKNOWN, CheckStatus.NOT_RUN)
            )
            lines.append(f"- 规划检查：{p}/{len(plan_checks)} 通过，{f} 失败，{u} 未知")
            for c in plan_checks:
                if c.status == CheckStatus.FAIL:
                    lines.append(f"  - ❌ {c.name}：{c.detail}")
                elif c.status in (CheckStatus.UNKNOWN, CheckStatus.NOT_RUN):
                    lines.append(f"  - ❓ {c.name}：{c.detail or '暂无自动化检查'}")

        lines.append(f"置信度：{_confidence_zh(self.confidence)}")
        if self.graph_powered:
            lines.append("🔷 Graph-Powered：图谱约束/诊断已介入本次任务")
        if self.remaining_risks:
            lines.append("残余风险：" + "；".join(self.remaining_risks[:3]))
        return "\n".join(lines)


# ── Plan validation checks: natural-language → executed checklist ──────────


def run_plan_validation_checks(
    plan: "GDLObjectPlan | None",
    project: "HSFProject | None",
    static_result: "StaticCheckResult | None",
) -> list[VerificationCheck]:
    """Turn ``plan.validation_checks`` (natural language) into executed items.

    Each item is matched to a deterministic checker by keyword. Items that
    cannot be mapped are reported as UNKNOWN — never silently passed.
    """
    if plan is None or not plan.validation_checks:
        return []

    from openbrep.hsf_project import ScriptType

    checks: list[VerificationCheck] = []
    static_errors = list(static_result.errors) if static_result else []

    for item in plan.validation_checks:
        key = (item or "").lower()
        name = item

        # ADD/DEL balance → stack_imbalance
        if "add" in key and ("del" in key or "平衡" in key or "栈" in key):
            fail = [e for e in static_errors if e.check_type == "stack_imbalance"]
            checks.append(_plan_check(name, fail, "变换栈 ADD/DEL 不平衡"))
            continue

        # FOR/NEXT pair → block_mismatch FOR/NEXT
        if "for" in key and ("next" in key or "配对" in key):
            fail = [
                e for e in static_errors
                if e.check_type == "block_mismatch" and "FOR/NEXT" in e.detail
            ]
            checks.append(_plan_check(name, fail, "FOR/NEXT 不配对"))
            continue

        # IF/ENDIF pair
        if "endif" in key or ("if" in key and "endif" in key):
            fail = [
                e for e in static_errors
                if e.check_type == "block_mismatch" and "IF/ENDIF" in e.detail
            ]
            checks.append(_plan_check(name, fail, "IF/ENDIF 不配对"))
            continue

        # 3D ends with END
        if "end" in key and ("结束" in key or "结尾" in key or "末尾" in key):
            ok = _check_3d_ends_with_end(project)
            if ok is None:
                checks.append(VerificationCheck(
                    name=name, check_type="plan_check",
                    status=CheckStatus.UNKNOWN, detail="3D 脚本为空，无法检查",
                ))
            else:
                checks.append(VerificationCheck(
                    name=name, check_type="plan_check",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    detail="3D 脚本以 END 结束" if ok else "3D 脚本未以 END 结束",
                ))
            continue

        # param name consistency → undefined_var
        if "参数" in key and ("一致" in key or "匹配" in key or "对应" in key):
            fail = [e for e in static_errors if e.check_type == "undefined_var"]
            checks.append(_plan_check(name, fail, "存在未声明变量"))
            continue

        # 2D script visible/present
        if "2d" in key and ("可见" in key or "脚本" in key or "输出" in key):
            if project is None:
                checks.append(VerificationCheck(
                    name=name, check_type="plan_check",
                    status=CheckStatus.UNKNOWN, detail="无项目上下文",
                ))
            else:
                s2d = (project.get_script(ScriptType.SCRIPT_2D) or "").strip()
                ok = bool(s2d)
                checks.append(VerificationCheck(
                    name=name, check_type="plan_check",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    detail="2D 脚本非空" if ok else "2D 脚本为空",
                ))
            continue

        # unmatched → unknown
        checks.append(VerificationCheck(
            name=name, check_type="plan_check",
            status=CheckStatus.UNKNOWN, detail="暂无自动化检查覆盖此项",
        ))

    return checks


# ── Report builder ─────────────────────────────────────────────────────────


def build_verification_report(
    *,
    intent: str,
    user_input: str = "",
    project: "HSFProject | None" = None,
    object_plan: "GDLObjectPlan | None" = None,
    static_result: "StaticCheckResult | None" = None,
    lint_summary: str = "",
    compile_result: "CompileResult | None" = None,
    compile_not_run_reason: str = "",
    static_repair_triggered: bool = False,
    auto_repair_info: str = "",
    graph_powered: bool = False,
) -> VerificationReport:
    """Aggregate scattered checks into one :class:`VerificationReport`.

    This is a pure observer: it reads already-computed results and does not
    run the compiler, LLM, or static checker itself.
    """
    report = VerificationReport(
        intent=intent,
        goal=(user_input or "")[:160],
        graph_powered=graph_powered,
    )
    checks: list[VerificationCheck] = []

    # 1. plan validation checks (executed from natural language)
    plan_checks = run_plan_validation_checks(object_plan, project, static_result)
    checks.extend(plan_checks)

    # 2. static check overview
    if static_result is not None:
        if static_result.passed:
            checks.append(VerificationCheck(
                name="静态检查", check_type="static",
                status=CheckStatus.PASS, detail="无问题",
            ))
        else:
            for e in static_result.errors:
                report.errors_caught.append(f"[{e.check_type}] {e.file}: {e.detail}")
            checks.append(VerificationCheck(
                name="静态检查", check_type="static", status=CheckStatus.FAIL,
                detail=f"{len(static_result.errors)} 个问题",
                auto_repairable=any(
                    e.check_type in ("undefined_var", "forward_decl")
                    for e in static_result.errors
                ),
            ))

    # 3. lint
    if lint_summary:
        fixes = _count_lint_fixes(lint_summary)
        checks.append(VerificationCheck(
            name="GDL Linter", check_type="lint",
            status=CheckStatus.PASS, detail=f"自动修复 {fixes} 处",
        ))
        if fixes:
            report.fixes_applied.append(f"linter 修复 {fixes} 处")
    else:
        checks.append(VerificationCheck(
            name="GDL Linter", check_type="lint",
            status=CheckStatus.PASS, detail="无需修复",
        ))

    # 4. compile
    if compile_result is not None:
        if compile_result.success:
            checks.append(VerificationCheck(
                name="编译验证", check_type="compile",
                status=CheckStatus.PASS, detail="GSM 输出成功",
            ))
        else:
            err = (compile_result.stderr or compile_result.stdout or "").strip()
            line_errors = _extract_line_errors_from_stderr(err)
            report.errors_caught.append(f"编译失败：{err[:300]}")
            checks.append(VerificationCheck(
                name="编译验证", check_type="compile",
                status=CheckStatus.FAIL, detail="编译失败", auto_repairable=True,
                line_errors=line_errors,
            ))
    else:
        reason = compile_not_run_reason or "本次任务未执行编译验证"
        checks.append(VerificationCheck(
            name="编译验证", check_type="compile",
            status=CheckStatus.NOT_RUN, detail=reason,
        ))
        if "SKIPPED_NO_COMPILER" not in reason:
            report.remaining_risks.append("未编译验证，GSM 输出未确认")

    # 5. fixes applied
    if static_repair_triggered:
        report.fixes_applied.append("静态检查自动修复变量问题")
    if auto_repair_info:
        first = auto_repair_info.strip().splitlines()[0][:120] if auto_repair_info.strip() else ""
        if first:
            report.fixes_applied.append(first)

    # 6. remaining risks from unknown plan checks
    unknown_plan = [c for c in plan_checks if c.status == CheckStatus.UNKNOWN]
    if unknown_plan:
        report.remaining_risks.append(
            f"{len(unknown_plan)} 项规划检查无自动化覆盖"
        )

    # 7. confidence
    report.confidence = _compute_confidence(checks, compile_result)

    report.checks = checks
    return report


# ── helpers ────────────────────────────────────────────────────────────────


def _plan_check(
    name: str, fail_errors: list, fail_detail: str
) -> VerificationCheck:
    if fail_errors:
        detail = fail_detail + "：" + "; ".join(e.detail for e in fail_errors[:2])
        return VerificationCheck(
            name=name, check_type="plan_check",
            status=CheckStatus.FAIL, detail=detail,
        )
    return VerificationCheck(
        name=name, check_type="plan_check",
        status=CheckStatus.PASS, detail="通过",
    )


def _check_3d_ends_with_end(project: "HSFProject | None") -> Optional[bool]:
    """True/False if 3D script ends with END; None if script empty."""
    if project is None:
        return None
    from openbrep.hsf_project import ScriptType

    code = (project.get_script(ScriptType.SCRIPT_3D) or "").strip()
    if not code:
        return None
    lines = [ln.split("!", 1)[0].strip() for ln in code.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    return lines[-1].upper() == "END"


def _count_lint_fixes(lint_summary: str) -> int:
    total = 0
    for m in re.finditer(r"修复\s*(\d+)\s*处", lint_summary or ""):
        total += int(m.group(1))
    return total


def _compute_confidence(checks: list[VerificationCheck], compile_result) -> str:
    has_fail = any(c.status == CheckStatus.FAIL for c in checks)
    has_unknown = any(c.status == CheckStatus.UNKNOWN for c in checks)
    compile_ok = compile_result is not None and compile_result.success
    compile_not_run = compile_result is None
    if has_fail:
        return "low"
    if compile_ok and not has_unknown:
        return "high"
    if (compile_ok or compile_not_run) and not has_fail:
        return "medium"
    return "low"


def _find(checks: list[VerificationCheck], check_type: str) -> Optional[VerificationCheck]:
    for c in checks:
        if c.check_type == check_type:
            return c
    return None


def _status_icon(status: CheckStatus) -> str:
    return {"pass": "✅", "fail": "❌", "unknown": "❓", "not_run": "⏸️"}.get(
        status.value, "❓"
    )


def _extract_line_errors_from_stderr(stderr: str) -> list[dict]:
    """从 LP_XMLConverter stderr 提取结构化行号错误列表。

    LP_XMLConverter 格式：
      (LINE) : error: MESSAGE
      (LINE) : warning: MESSAGE
    """
    results = []
    for m in re.finditer(r'\((\d+)\)\s*:\s*(error|warning):\s*(.+)', stderr or ""):
        line_num = int(m.group(1))
        if line_num > 0:  # 0 通常是"无行号"的占位
            results.append({
                "line_number": line_num,
                "severity": m.group(2),
                "message": m.group(3).strip(),
            })
    return results


def _compile_label(status: CheckStatus) -> str:
    return {
        "pass": "✅ 通过",
        "fail": "❌ 失败",
        "not_run": "⏸️ 未执行",
        "unknown": "❓ 未知",
    }.get(status.value, "❓ 未知")


def _confidence_zh(c: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(c, c)
