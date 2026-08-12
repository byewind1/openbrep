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
    from openbrep.semantic_verifier import SemanticVerificationResult
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
            if c.status == CheckStatus.FAIL and c.check_type in (
                "static", "compile", "plan_check", "semantic", "reserved_param_semantic_bug",
            ):
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

        static_chks = [c for c in self.checks if c.check_type == "static"]
        if static_chks:
            failed_static = [c for c in static_chks if c.status == CheckStatus.FAIL]
            if failed_static:
                # P8：交付完整性检查也是 static 类型；聚合展示所有 static FAIL，
                # 避免占位交付时摘要仍显示"静态检查 ✅ 无问题"（空转全绿事故回归）。
                parts = [f"{c.name}：{c.detail or '失败'}" for c in failed_static]
                lines.append(f"- 静态检查：❌ " + "；".join(parts))
            else:
                first = static_chks[0]
                lines.append(
                    f"- 静态检查：{_status_icon(first.status)} "
                    f"{first.detail or first.status.value}"
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

        semantic_chk = _find(self.checks, "semantic")
        if semantic_chk:
            lines.append(
                f"- 语义验证：{_status_icon(semantic_chk.status)} "
                f"{semantic_chk.detail or semantic_chk.status.value}"
            )

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

_RESERVED_ROLE_CN = {"A": "宽度", "B": "深度", "ZZYZX": "高度"}


def _format_reserved_bug_detail(conflict) -> str:
    """保留名误用的说人话版本：谁被错用、正确槽位是什么、影响、建议。"""
    reserved = conflict.reserved_name
    expected = conflict.expected_name
    reserved_role = _RESERVED_ROLE_CN.get(reserved, reserved)
    expected_role = _RESERVED_ROLE_CN.get(expected, expected)
    evidence = conflict.role_in_script or "脚本角色位"
    if expected == "ZZYZX":
        # 高度语义被塞进了宽度/深度槽位
        return (
            f"高度语义被赋给了保留参数 {reserved}（{reserved} 在 ArchiCAD 中是{reserved_role}）；"
            f"期望参数：ZZYZX（高度），实际使用：{reserved}（出现于{evidence}）。"
            f"影响：此对象在 ArchiCAD 中拖拽{reserved_role}控制点会改变高度，"
            f"尺寸标注会显示错误的维度。"
            f"建议：将高度相关的几何改为使用 ZZYZX。"
        )
    # ZZYZX 被当成宽度/深度用
    return (
        f"{expected_role}语义被赋给了保留参数 ZZYZX（ZZYZX 在 ArchiCAD 中是高度）；"
        f"期望参数：{expected}（{expected_role}），实际使用：ZZYZX（出现于{evidence}）。"
        f"影响：此对象在 ArchiCAD 中拖拽高度控制点会改变{expected_role}，"
        f"尺寸标注会显示错误的维度。"
        f"建议：将{expected_role}相关的几何改为使用 {expected}。"
    )


def build_verification_report(
    *,
    intent: str,
    user_input: str = "",
    project: "HSFProject | None" = None,
    object_plan: "GDLObjectPlan | None" = None,
    static_result: "StaticCheckResult | None" = None,
    semantic_result: "SemanticVerificationResult | None" = None,
    lint_summary: str = "",
    compile_result: "CompileResult | None" = None,
    compile_not_run_reason: str = "",
    static_repair_triggered: bool = False,
    auto_repair_info: str = "",
    graph_powered: bool = False,
    reserved_conflicts: list | None = None,
    enable_delivery_integrity: Optional[bool] = None,
) -> VerificationReport:
    """Aggregate scattered checks into one :class:`VerificationReport`.

    This is a pure observer: it reads already-computed results and does not
    run the compiler, LLM, or static checker itself.

    ``enable_delivery_integrity`` (P8): when True AND intent is CREATE/IMAGE,
    run CREATE 专属交付完整性检查——3D 脚本为空或仍为 create_new 占位脚本
    （placeholder_delivery）、paramlist 缺 A/B/ZZYZX（reserved_params_missing），
    均以 static FAIL 阻断。默认 None = 不启用（MODIFY 老项目打开即改是合法场景）。
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

    # 2b. P8 交付完整性（CREATE/IMAGE 专属）：占位脚本 / 参数表缺保留参数 → static FAIL。
    # 事故回归：CREATE 零产出时 pipeline 交付 create_new 占位项目（BLOCK A,B,ZZYZX），
    # 旧报告对占位脚本空转全绿——这里用确定性字节对比把它打成阻断 FAIL。
    # MODIFY 不启用（老项目打开就改是合法场景）。
    if enable_delivery_integrity and intent in ("CREATE", "IMAGE") and project is not None:
        from openbrep.hsf_project import HSFProject, ScriptType

        actual_3d = project.get_script(ScriptType.SCRIPT_3D) or ""
        if not actual_3d.strip():
            checks.append(VerificationCheck(
                name="占位脚本交付", check_type="static",
                status=CheckStatus.FAIL, detail="3D 脚本为空：交付内容缺失，生成未生效",
            ))
            report.errors_caught.append("[placeholder_delivery] 3D 脚本为空，生成未生效")
        else:
            placeholder_3d = HSFProject.create_new(
                "__delivery_integrity_probe__"
            ).get_script(ScriptType.SCRIPT_3D)
            if actual_3d == placeholder_3d:
                checks.append(VerificationCheck(
                    name="占位脚本交付", check_type="static",
                    status=CheckStatus.FAIL,
                    detail="交付内容仍是 create_new 占位脚本（BLOCK A, B, ZZYZX），生成未生效",
                ))
                report.errors_caught.append(
                    "[placeholder_delivery] 3D 脚本仍为 create_new 占位脚本"
                )
        missing = [
            n for n in ("A", "B", "ZZYZX")
            if not any(getattr(p, "name", None) == n for p in (project.parameters or []))
        ]
        if missing:
            checks.append(VerificationCheck(
                name="保留参数缺失", check_type="static",
                status=CheckStatus.FAIL,
                detail=f"参数表缺少 ArchiCAD 保留参数：{'、'.join(missing)}",
            ))
            report.errors_caught.append(
                f"[reserved_params_missing] 参数表缺少 {'、'.join(missing)}"
            )

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

    # 4b. semantic verification (geometry-level, via gdl_previewer — works even
    # without a configured LP_XMLConverter)
    if semantic_result is not None:
        blocking_issues = [i for i in semantic_result.issues if i.blocking]
        info_issues = [i for i in semantic_result.issues if not i.blocking]
        for issue in info_issues:
            report.remaining_risks.append(issue.detail)
        if semantic_result.passed:
            checks.append(VerificationCheck(
                name="语义验证", check_type="semantic",
                status=CheckStatus.PASS, detail="无问题",
            ))
        else:
            for issue in blocking_issues:
                report.errors_caught.append(f"[{issue.check_type}] {issue.detail}")
            checks.append(VerificationCheck(
                name="语义验证", check_type="semantic", status=CheckStatus.FAIL,
                detail=f"{len(blocking_issues)} 个问题",
            ))

    # 4c. reserved-param semantic bug（保留名被误用到错误维度角色：
    # 高度塞进 A/B 宽度/深度槽位，或 ZZYZX 被当宽度/深度用。
    # 这类对象在 ArchiCAD 里是真的坏——拖一个维度的控制点会改到另一个维度。
    # blocking FAIL，产物照常交付（见 S2 门禁语义）。
    for _conflict in (reserved_conflicts or []):
        if getattr(_conflict, "severity", "") != "semantic_bug":
            continue
        detail = _format_reserved_bug_detail(_conflict)
        report.errors_caught.append(
            f"[reserved_param_semantic_bug] {_conflict.expected_name}↔{_conflict.reserved_name}"
        )
        checks.append(VerificationCheck(
            name="保留参数语义",
            check_type="reserved_param_semantic_bug",
            status=CheckStatus.FAIL,
            detail=detail,
        ))

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
