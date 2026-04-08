from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class LintIssue:
    rule: str
    severity: str
    line: int
    message: str
    fixed: bool = False


@dataclass
class LintResult:
    issues: list[LintIssue] = field(default_factory=list)
    original_code: str = ""
    fixed_code: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")

    @property
    def fix_count(self) -> int:
        return sum(1 for i in self.issues if i.fixed)


class GDLLinter:
    """Conservative line-based linter for deterministic GDL fixes."""

    _ATN_ASSIGN_RE = re.compile(
        r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*ATN\s*\(\s*(?P<expr>.+?)\s*\)\s*$",
        re.IGNORECASE,
    )
    _CIRCLE2_RE = re.compile(r"^(?P<indent>\s*)CIRCLE2\b\s*(?P<args>.*)$", re.IGNORECASE)
    _HOTSPOT2_RE = re.compile(r"^(?P<indent>\s*)HOTSPOT2\b\s*(?P<args>.*)$", re.IGNORECASE)
    _MOVE_RE = re.compile(r"^(?P<indent>\s*)MOVE\b\s*(?P<args>.*)$", re.IGNORECASE)
    _PEN_RE = re.compile(r"^\s*PEN\b", re.IGNORECASE)
    _TUBE_RE = re.compile(r"^\s*TUBE\b\s*(?P<args>.*)$", re.IGNORECASE)
    _NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

    def __init__(self, script_type: str = "3D", disabled_rules: list[str] | None = None):
        self.script_type = script_type
        self.disabled_rules = set(disabled_rules or [])

    def check(self, code: str) -> LintResult:
        lines = code.splitlines()
        issues: list[LintIssue] = []

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("!"):
                continue

            if "RULE-001" not in self.disabled_rules:
                issue = self._check_rule_001(idx, line)
                if issue:
                    issues.append(issue)

            if "RULE-002" not in self.disabled_rules:
                issue = self._check_rule_002(idx, line)
                if issue:
                    issues.append(issue)

            if "RULE-003" not in self.disabled_rules:
                issue = self._check_rule_003(idx, line)
                if issue:
                    issues.append(issue)

            if "RULE-004" not in self.disabled_rules:
                issue = self._check_rule_004(idx, line)
                if issue:
                    issues.append(issue)

            if "RULE-005" not in self.disabled_rules:
                issue = self._check_rule_005(idx, line)
                if issue:
                    issues.append(issue)

            if "RULE-006" not in self.disabled_rules:
                issue = self._check_rule_006(idx, line)
                if issue:
                    issues.append(issue)

        return LintResult(issues=issues, original_code=code, fixed_code=code)

    def fix(self, code: str) -> LintResult:
        original = code
        lines = code.splitlines()
        fixed_lines: list[str] = []
        issues: list[LintIssue] = []

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("!"):
                fixed_lines.append(line)
                continue

            replaced = False

            if not replaced and "RULE-001" not in self.disabled_rules:
                fix_lines, issue = self._fix_rule_001(idx, line)
                if issue:
                    issues.append(issue)
                    if fix_lines is not None:
                        fixed_lines.extend(fix_lines)
                        replaced = True

            if not replaced and "RULE-002" not in self.disabled_rules:
                fix_line, issue = self._fix_rule_002(idx, line)
                if issue:
                    issues.append(issue)
                    if fix_line is not None:
                        fixed_lines.append(fix_line)
                        replaced = True

            if not replaced and "RULE-003" not in self.disabled_rules:
                fix_line, issue = self._fix_rule_003(idx, line)
                if issue:
                    issues.append(issue)
                    if fix_line is not None:
                        fixed_lines.append(fix_line)
                        replaced = True

            if not replaced and "RULE-004" not in self.disabled_rules:
                fix_line, issue = self._fix_rule_004(idx, line)
                if issue:
                    issues.append(issue)
                    if fix_line is not None:
                        fixed_lines.append(fix_line)
                        replaced = True

            if not replaced and "RULE-005" not in self.disabled_rules:
                issue = self._check_rule_005(idx, line)
                if issue:
                    issues.append(issue)

            if not replaced and "RULE-006" not in self.disabled_rules:
                issue = self._check_rule_006(idx, line)
                if issue:
                    issues.append(issue)

            if not replaced:
                fixed_lines.append(line)

        return LintResult(
            issues=issues,
            original_code=original,
            fixed_code="\n".join(fixed_lines),
        )

    def _check_rule_001(self, line_no: int, line: str) -> LintIssue | None:
        m = self._ATN_ASSIGN_RE.match(line)
        if not m:
            return None
        expr = m.group("expr").strip()
        parsed = self._split_division_expr(expr)
        if not parsed:
            return None
        return LintIssue(
            rule="RULE-001",
            severity="WARNING",
            line=line_no,
            message="ATN(y/x) 无法正确处理象限，建议改为 atan2 风格写法",
        )

    def _check_rule_002(self, line_no: int, line: str) -> LintIssue | None:
        if self._CIRCLE2_RE.match(line):
            return LintIssue(
                rule="RULE-002",
                severity="ERROR",
                line=line_no,
                message="CIRCLE2 命令不存在，应改为 ARC2 x, y, r, 0, 360",
            )
        return None

    def _check_rule_003(self, line_no: int, line: str) -> LintIssue | None:
        m = self._HOTSPOT2_RE.match(line)
        if not m:
            return None
        args = [p.strip() for p in m.group("args").split(",") if p.strip()]
        if len(args) < 2:
            return LintIssue(
                rule="RULE-003",
                severity="ERROR",
                line=line_no,
                message="HOTSPOT2 参数不足，至少需要 x, y",
            )
        return None

    def _check_rule_004(self, line_no: int, line: str) -> LintIssue | None:
        if self._MOVE_RE.match(line):
            return LintIssue(
                rule="RULE-004",
                severity="ERROR",
                line=line_no,
                message="MOVE 不是 GDL 命令，应改为 ADD dx, dy, dz",
            )
        return None

    def _check_rule_005(self, line_no: int, line: str) -> LintIssue | None:
        if self.script_type != "3D":
            return None
        if self._PEN_RE.match(line):
            return LintIssue(
                rule="RULE-005",
                severity="WARNING",
                line=line_no,
                message="PEN 在 3D Script 中无效，建议移到 2D Script",
            )
        return None

    def _check_rule_006(self, line_no: int, line: str) -> LintIssue | None:
        m = self._TUBE_RE.match(line)
        if not m:
            return None
        args = [p.strip() for p in m.group("args").split(",") if p.strip()]
        if not args:
            return None
        if any(not self._is_numeric_literal(arg) for arg in args):
            return LintIssue(
                rule="RULE-006",
                severity="WARNING",
                line=line_no,
                message="TUBE 含变量参数，建议改用 PUT/GET 缓冲机制避免静态参数问题",
            )
        return None

    def _fix_rule_001(self, line_no: int, line: str) -> tuple[list[str] | None, LintIssue | None]:
        m = self._ATN_ASSIGN_RE.match(line)
        if not m:
            return None, None
        target = m.group("var")
        parsed = self._split_division_expr(m.group("expr").strip())
        if not parsed:
            return None, None
        dy_expr, dx_expr = parsed
        indent = re.match(r"^\s*", line).group(0)
        fixed = [
            f"{indent}! [LINTER-FIXED RULE-001] atan2-style quadrant fix",
            f"{indent}_lint_dx = {dx_expr}",
            f"{indent}_lint_dy = {dy_expr}",
            f"{indent}IF _lint_dx = 0 THEN",
            f"{indent}    IF _lint_dy >= 0 THEN {target} = 90 ELSE {target} = -90",
            f"{indent}ELSE",
            f"{indent}    {target} = ATN(_lint_dy / _lint_dx)",
            f"{indent}    IF _lint_dx < 0 THEN {target} = {target} + 180",
            f"{indent}ENDIF",
        ]
        return fixed, LintIssue(
            rule="RULE-001",
            severity="WARNING",
            line=line_no,
            message="ATN(y/x) 已替换为 atan2 风格象限修复",
            fixed=True,
        )

    def _fix_rule_002(self, line_no: int, line: str) -> tuple[str | None, LintIssue | None]:
        m = self._CIRCLE2_RE.match(line)
        if not m:
            return None, None
        indent = m.group("indent")
        args = [p.strip() for p in m.group("args").split(",") if p.strip()]
        if len(args) >= 3:
            fixed = f"{indent}ARC2 {args[0]}, {args[1]}, {args[2]}, 0, 360    ! [LINTER-FIXED RULE-002]"
        else:
            fixed = f"{indent}! TODO: CIRCLE2 不存在，请改为 ARC2 x, y, r, 0, 360  ! [LINTER-FIXED RULE-002]"
        return fixed, LintIssue(
            rule="RULE-002",
            severity="ERROR",
            line=line_no,
            message="CIRCLE2 已替换为 ARC2 或插入 TODO 注释",
            fixed=True,
        )

    def _fix_rule_003(self, line_no: int, line: str) -> tuple[str | None, LintIssue | None]:
        issue = self._check_rule_003(line_no, line)
        if not issue:
            return None, None
        indent = re.match(r"^\s*", line).group(0)
        fixed = f"{indent}HOTSPOT2 0, 0    ! [LINTER-FIXED RULE-003] 默认坐标，请修改"
        issue.fixed = True
        issue.message = "HOTSPOT2 参数不足，已补默认坐标"
        return fixed, issue

    def _fix_rule_004(self, line_no: int, line: str) -> tuple[str | None, LintIssue | None]:
        m = self._MOVE_RE.match(line)
        if not m:
            return None, None
        indent = m.group("indent")
        args = [p.strip() for p in m.group("args").split(",") if p.strip()]
        if len(args) == 3:
            fixed = f"{indent}ADD {args[0]}, {args[1]}, {args[2]}    ! [LINTER-FIXED RULE-004]"
        else:
            fixed = f"{indent}! TODO: MOVE 不是 GDL 命令，请改为 ADD dx, dy, dz  ! [LINTER-FIXED RULE-004]"
        return fixed, LintIssue(
            rule="RULE-004",
            severity="ERROR",
            line=line_no,
            message="MOVE 已替换为 ADD 或插入 TODO 注释",
            fixed=True,
        )

    @staticmethod
    def _split_division_expr(expr: str) -> tuple[str, str] | None:
        expr = expr.strip()
        if expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1].strip()
        depth = 0
        split_idx = None
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "/" and depth == 0:
                split_idx = i
                break
        if split_idx is None:
            return None
        left = expr[:split_idx].strip()
        right = expr[split_idx + 1 :].strip()
        if not left or not right:
            return None
        return left, right

    def _is_numeric_literal(self, text: str) -> bool:
        return bool(self._NUMERIC_RE.fullmatch(text))
