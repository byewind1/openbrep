"""
GDL static checker — runs before compilation.

Seven checks (all regex/count-based, no LLM):
  1. undefined_var     — script variables not declared in paramlist.xml
  2. forward_decl      — _underscore vars in 3d/2d not assigned in 1d.gdl
  3. stack_imbalance   — ADD*/ROT*/MUL push count != DEL pop count in 3d.gdl
  4. block_mismatch    — unmatched IF/ENDIF or FOR/NEXT across any .gdl file
  5. ellipsis_stub     — standalone .../… line (model-degeneration stub marker)
  6. unknown_command   — statement first word not in the known GDL command set
                         (warning level; Archicad treats it as a CALL-less macro,
                         "Missing CALL keyword (not recommended)")
  7. bare_not          — NOT used without its required parentheses NOT (x)
                         (error level; Archicad reports "Missing parameter(s)
                         after function")

StaticChecker.check(project) returns StaticCheckResult immediately.
Returns passed=True when project is None (safe no-op).

unknown_command 是 warning：写入 StaticCheckResult.warnings，不阻断交付；
bare_not 是 error：写入 StaticCheckResult.errors，阻断交付（与 ellipsis_stub
同级）。命令集合来源 openbrep/data/gdl_commands.txt（静态入库，见文件头）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from openbrep.gdl_keywords import (
    GDL_BUILTINS,
    GDL_BUILTINS_CASEFOLD,
    GLOBAL_PREFIXES,
)
from openbrep.gdl_ast import parse_gdl_script, ControlBlock, TransformFrame

__all__ = [
    "GDL_BUILTINS",
    "GLOBAL_PREFIXES",
    "GDL_COMMANDS",
    "StaticChecker",
    "StaticCheckResult",
    "StaticError",
    "ProseLeakHit",
    "find_prose_leaks",
]


# ── 已知 GDL 命令集（unknown_command 检查用）───────────────────────────────
# 静态入库文件 openbrep/data/gdl_commands.txt（生成来源与日期见文件头），
# 打包/CI 环境不依赖 knowledge/ 目录。加载失败时回退到 gdl_keywords 的
# GDL_BUILTINS（保证检查器可用，只是召回略低）。
def _load_gdl_commands() -> frozenset[str]:
    path = Path(__file__).resolve().parent / "data" / "gdl_commands.txt"
    commands = set(GDL_BUILTINS)
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            commands.add(line.upper())
    except OSError:
        pass
    return frozenset(commands)


GDL_COMMANDS: frozenset[str] = _load_gdl_commands()

if TYPE_CHECKING:
    from openbrep.hsf_project import HSFProject, ScriptType


# ── GDL built-in keywords to exclude from undefined_var check ────────────────
# Public (no leading _) so cross_script_checker etc. can import without drift.

# ArchiCAD reserved single-letter parameters available in every object
RESERVED_PARAMS: frozenset[str] = frozenset({"A", "B", "ZZYZX"})

# Regex to extract bare identifiers (word chars, not purely numeric)
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")

# Extracts the left-hand side of a simple assignment: "name =" (not "name ==")
_LOCAL_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", re.MULTILINE)

GDL_SIGNATURE_WORDS: frozenset[str] = frozenset({
    "BLOCK", "CYLIND", "SPHERE", "PRISM_", "ADD", "DEL", "ADDX", "ADDY", "ADDZ",
    "ROT", "MUL", "IF", "FOR", "GOSUB", "END", "PROJECT2", "HOTSPOT2", "RECT2",
    "LINE2", "CIRCLE2", "MATERIAL", "PEN", "TOLER",
})


@dataclass
class StaticError:
    check_type: str   # "undefined_var" | "forward_decl" | "stack_imbalance" | "block_mismatch" | "ellipsis_stub"
    file: str         # e.g. "scripts/3d.gdl"
    detail: str       # human-readable, injected into prompt hint


@dataclass
class StaticCheckResult:
    passed: bool
    errors: list[StaticError] = field(default_factory=list)
    # P13：warning 级检查结果（unknown_command 等）。不参与 passed 判定，
    # 仅供上层展示/提示——与 errors 分开，避免误阻断交付。
    warnings: list[StaticError] = field(default_factory=list)


class StaticChecker:
    """
    Compile-time static analysis for HSF/GDL projects.

    All checks are count/regex based — no LLM, no compiler invocation.
    Safe to call with project=None (returns passed=True).
    """

    def check(self, project: Optional["HSFProject"]) -> StaticCheckResult:
        if project is None:
            return StaticCheckResult(passed=True)

        errors: list[StaticError] = []
        warnings: list[StaticError] = []
        errors.extend(self._check_undefined_var(project))
        errors.extend(self._check_forward_decl(project))
        errors.extend(self._check_stack_imbalance(project))
        errors.extend(self._check_stack_imbalance_branches(project))
        errors.extend(self._check_block_mismatch(project))
        errors.extend(self._check_ellipsis_stub(project))
        # P13：裸 NOT 是 Archicad 必炸错误 → error；未知命令首词是 Archicad
        # 警告（无 CALL 的宏调用）→ warning，不阻断交付。
        errors.extend(self._check_bare_not(project))
        warnings.extend(self._check_unknown_command(project))

        return StaticCheckResult(
            passed=len(errors) == 0, errors=errors, warnings=warnings
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_script(project: "HSFProject", gdl_filename: str) -> str:
        """Return script text by filename (e.g. '3d.gdl'), or '' if absent."""
        from openbrep.hsf_project import ScriptType
        for st in ScriptType:
            if st.value == gdl_filename:
                return project.get_script(st) or ""
        return ""

    @staticmethod
    def _strip_comments(code: str) -> str:
        """Remove metadata/comment-only lines, inline comments, and quoted string literals."""
        lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("[FILE:") or stripped.startswith("!"):
                continue
            idx = line.find("!")
            clean = line[:idx] if idx >= 0 else line
            clean = re.sub(r'"[^"]*"', '""', clean)
            if clean.strip():
                lines.append(clean)
        return "\n".join(lines)

    @staticmethod
    def _declared_param_names(project: "HSFProject") -> frozenset[str]:
        """Names declared in paramlist.xml, plus ArchiCAD reserved params."""
        names = {p.name for p in project.parameters}
        names.update(RESERVED_PARAMS)
        return frozenset(names)

    @staticmethod
    def _is_valid_gdl(content: str) -> bool:
        tokens = set(re.findall(r"[A-Z_][A-Z0-9_]*", content.upper()))
        return bool(tokens & GDL_SIGNATURE_WORDS)

    # ── check 1: undefined_var ────────────────────────────────────────────────

    @staticmethod
    def _local_assigned_names(code: str) -> frozenset[str]:
        """Return names that appear on the left-hand side of an assignment in code."""
        return frozenset(m.group(1) for m in _LOCAL_ASSIGN_RE.finditer(code))

    def _check_undefined_var(self, project: "HSFProject") -> list[StaticError]:
        declared = self._declared_param_names(project)
        errors: list[StaticError] = []

        # Collect every name assigned in ANY script in the project.
        # A variable assigned somewhere (even in a sibling script) is "known"
        # and not a true undefined — avoids false positives from cross-script use.
        all_project_locals: set[str] = set()
        for _f in ("3d.gdl", "2d.gdl", "1d.gdl", "ui.gdl", "vl.gdl"):
            _code = self._strip_comments(self._get_script(project, _f))
            all_project_locals.update(self._local_assigned_names(_code))

        for gdl_file in ("3d.gdl", "2d.gdl", "1d.gdl"):
            code = self._strip_comments(self._get_script(project, gdl_file))
            if not code.strip():
                continue
            if not self._is_valid_gdl(code):
                continue

            file_path = f"scripts/{gdl_file}"
            seen_undefined: set[str] = set()

            for m in _IDENT_RE.finditer(code):
                name = m.group(1)
                if name in seen_undefined:
                    continue
                # GDL built-in (case-insensitive lookup)
                if name.upper() in GDL_BUILTINS_CASEFOLD or name in GDL_BUILTINS:
                    continue
                # Known GDL command from static command list (filters WALLHOLE, SET, ...)
                if name.upper() in GDL_COMMANDS:
                    continue
                # _ prefix: handled by forward_decl check
                if name.startswith("_"):
                    continue
                # Global/system variable prefix (gs_, ac_, GLOB_, SYMB_)
                if any(name.lower().startswith(p.lower()) for p in GLOBAL_PREFIXES):
                    continue
                # Declared in paramlist.xml or reserved (A/B/ZZYZX)
                if name in declared:
                    continue
                # Assigned anywhere in the project → known local variable
                if name in all_project_locals:
                    continue
                # Single-letter loop index (i, j, k, n, ...)
                if len(name) == 1 and name.isalpha():
                    continue
                seen_undefined.add(name)
                errors.append(StaticError(
                    check_type="undefined_var",
                    file=file_path,
                    detail=f"变量 '{name}' 未在 paramlist.xml 声明",
                ))

        return errors

    # ── check 2: forward_decl ────────────────────────────────────────────────

    def _check_forward_decl(self, project: "HSFProject") -> list[StaticError]:
        """
        _ -prefixed vars used in 3d/2d should be assigned either in 1d.gdl
        OR in the script itself (self-contained derived vars).
        Only report when neither source has the assignment.
        """
        master_code = self._strip_comments(self._get_script(project, "1d.gdl"))
        master_locals = self._local_assigned_names(master_code)
        errors: list[StaticError] = []

        for gdl_file in ("3d.gdl", "2d.gdl"):
            code = self._strip_comments(self._get_script(project, gdl_file))
            if not code.strip():
                continue

            # Names assigned within this script itself
            self_locals = self._local_assigned_names(code)
            file_path = f"scripts/{gdl_file}"
            seen: set[str] = set()

            for m in _IDENT_RE.finditer(code):
                name = m.group(1)
                if not name.startswith("_") or name in seen:
                    continue
                seen.add(name)
                # OK if assigned in 1d.gdl or within this very script
                if name in master_locals or name in self_locals:
                    continue
                errors.append(StaticError(
                    check_type="forward_decl",
                    file=file_path,
                    detail=f"变量 '{name}' 在 {gdl_file} 中使用但未在 1d.gdl 或当前脚本赋值",
                ))

        return errors

    # ── check 3: stack_imbalance ─────────────────────────────────────────────

    # Tokens that push a transformation layer (each occurrence = 1 push)
    _PUSH_RE = re.compile(
        r"\b(ADD(?:[XYZ])?|ADD2|MUL2?|ROT[XYZ]?|ROT2)\b",
        re.IGNORECASE,
    )
    # DEL N pops N layers; DEL alone pops 1; DELALL pops all (can't statically verify)
    _POP_RE = re.compile(r"\bDEL\s*(\d+)?\b", re.IGNORECASE)
    _DELALL_RE = re.compile(r"\bDELALL\b", re.IGNORECASE)

    def _check_stack_imbalance(self, project: "HSFProject") -> list[StaticError]:
        code = self._strip_comments(self._get_script(project, "3d.gdl"))
        if not code.strip():
            return []

        # DELALL pops all layers — can't statically determine depth, skip check
        if self._DELALL_RE.search(code):
            return []

        push_count = len(self._PUSH_RE.findall(code))
        pop_count = sum(
            int(m.group(1)) if m.group(1) else 1
            for m in self._POP_RE.finditer(code)
        )

        if push_count == pop_count:
            return []

        return [StaticError(
            check_type="stack_imbalance",
            file="scripts/3d.gdl",
            detail=(
                f"变换栈不平衡：push({push_count}) != pop({pop_count})。"
                " 每条 ADD/ADDX/ADDY/ADDZ/ROT/MUL 都需要对应 DEL。"
            ),
        )]

    # ── check 3b: stack_imbalance (branch-aware) ─────────────────────────────

    def _check_stack_imbalance_branches(self, project: "HSFProject") -> list[StaticError]:
        """
        Branch-aware supplement to the global stack_imbalance check.

        Uses gdl_ast.parse_gdl_script to obtain a ControlBlock tree, then
        compares THEN vs ELSE push/pop deltas for every IF block.  DELALL
        anywhere in the script causes the entire check to be skipped (same
        guard as the global check).
        """
        code = self._strip_comments(self._get_script(project, "3d.gdl"))
        if not code.strip() or self._DELALL_RE.search(code):
            return []

        script_ast = parse_gdl_script(code)
        errors: list[StaticError] = []
        self._walk_if_blocks(script_ast.controls, errors)
        return errors

    def _walk_if_blocks(
        self, controls: list[ControlBlock], errors: list[StaticError]
    ) -> None:
        """Recursively check IF blocks in *controls* for branch delta asymmetry."""
        for cb in controls:
            # Recurse into nested ControlBlocks in both branches (or FOR body)
            nested = [
                c for c in cb.children + cb.else_children
                if isinstance(c, ControlBlock)
            ]
            self._walk_if_blocks(nested, errors)

            if cb.kind != "IF":
                continue

            # Per-block DELALL guard — skip when DELALL appears in either branch
            all_raw = cb.raw_lines + cb.else_raw_lines
            if any(re.match(r"^\s*DELALL\b", l, re.IGNORECASE) for l in all_raw):
                continue

            then_push = sum(1 for c in cb.children if isinstance(c, TransformFrame))
            then_pop = self._count_del_in_raw(cb.raw_lines)
            else_push = sum(1 for c in cb.else_children if isinstance(c, TransformFrame))
            else_pop = self._count_del_in_raw(cb.else_raw_lines)

            then_delta = then_push - then_pop
            else_delta = else_push - else_pop

            if then_delta != else_delta:
                errors.append(StaticError(
                    check_type="stack_imbalance",
                    file="scripts/3d.gdl",
                    detail=(
                        f"IF 分支变换栈不对称：THEN delta={then_delta:+d}，"
                        f"ELSE delta={else_delta:+d}。"
                        " 确保每条执行路径的 ADD/DEL 数量相同。"
                    ),
                ))

    @staticmethod
    def _count_del_in_raw(raw_lines: list[str]) -> int:
        """Count DEL pops in a list of raw source lines, respecting DEL N syntax."""
        count = 0
        for line in raw_lines:
            if re.match(r"^\s*DELALL\b", line, re.IGNORECASE):
                continue
            m = re.match(r"^\s*DEL\b\s*(\d+)?", line, re.IGNORECASE)
            if m:
                count += int(m.group(1)) if m.group(1) else 1
        return count

    # ── check 4: block_mismatch ──────────────────────────────────────────────

    # Single-line IF: IF ... THEN <code> on one line (something after THEN)
    # Multi-line IF:  IF ... THEN at line end (only whitespace/comment after THEN)
    _SINGLE_LINE_IF_RE = re.compile(r"\bIF\b.*\bTHEN\b\s*\S", re.IGNORECASE)
    _ENDIF_RE = re.compile(r"\bENDIF\b", re.IGNORECASE)
    _FOR_RE = re.compile(r"\bFOR\b", re.IGNORECASE)
    _NEXT_RE = re.compile(r"\bNEXT\b", re.IGNORECASE)

    def _check_block_mismatch(self, project: "HSFProject") -> list[StaticError]:
        errors: list[StaticError] = []

        for gdl_file in ("3d.gdl", "2d.gdl", "1d.gdl", "ui.gdl", "vl.gdl"):
            code = self._strip_comments(self._get_script(project, gdl_file))
            if not code.strip():
                continue

            file_path = f"scripts/{gdl_file}"
            if_count, endif_count, for_count, next_count = self._count_blocks(code)

            if if_count != endif_count:
                errors.append(StaticError(
                    check_type="block_mismatch",
                    file=file_path,
                    detail=(
                        f"IF/ENDIF 不匹配：IF={if_count}, ENDIF={endif_count}。"
                        " 检查多行 IF ... THEN 是否都有对应 ENDIF。"
                    ),
                ))

            if for_count != next_count:
                errors.append(StaticError(
                    check_type="block_mismatch",
                    file=file_path,
                    detail=(
                        f"FOR/NEXT 不匹配：FOR={for_count}, NEXT={next_count}。"
                        " 检查嵌套循环是否都有闭合 NEXT。"
                    ),
                ))

        return errors

    # Matches a bare IF token that is NOT part of ENDIF
    _BARE_IF_RE = re.compile(r"(?<!END)\bIF\b", re.IGNORECASE)

    def _count_blocks(self, code: str) -> tuple[int, int, int, int]:
        """Count multi-line IF, ENDIF, FOR, NEXT tokens."""
        if_count = 0
        endif_count = 0
        for_count = 0
        next_count = 0

        for line in code.splitlines():
            # strip comment
            ci = line.find("!")
            clean = line[:ci] if ci >= 0 else line

            endif_count += len(self._ENDIF_RE.findall(clean))

            # Single-line IF: THEN followed by non-whitespace on same line
            # → no ENDIF needed, don't count as block opener
            if not self._SINGLE_LINE_IF_RE.search(clean):
                # Count bare IF tokens (not part of ENDIF)
                if_count += len(self._BARE_IF_RE.findall(clean))

            for_count += len(self._FOR_RE.findall(clean))
            next_count += len(self._NEXT_RE.findall(clean))

        return if_count, endif_count, for_count, next_count


    # ── check 5: ellipsis_stub ──────────────────────────────────────────────

    # 独立成行的省略号残桩（模型退化输出标记，P8 事故："3d.gdl 第 2 行是字面
    # 省略号 ..."）。只匹配"去掉行内注释后整行就是 .../…"的情况——`! ...`
    # 注释行、代码行里的 ...（如 `BLOCK ...`）都不算残桩，避免误报。
    _STANDALONE_ELLIPSIS: frozenset[str] = frozenset({"...", "…"})

    def _check_ellipsis_stub(self, project: "HSFProject") -> list[StaticError]:
        errors: list[StaticError] = []
        for gdl_file in ("3d.gdl", "2d.gdl", "1d.gdl", "ui.gdl", "vl.gdl"):
            code = self._get_script(project, gdl_file) or ""
            hit_lines = []
            for idx, line in enumerate(code.splitlines(), start=1):
                code_part = line.split("!", 1)[0].strip()
                if code_part in self._STANDALONE_ELLIPSIS:
                    hit_lines.append(idx)
            if hit_lines:
                errors.append(StaticError(
                    check_type="ellipsis_stub",
                    file=f"scripts/{gdl_file}",
                    detail=(
                        f"脚本含独立成行的省略号残桩"
                        f"（第 {'、'.join(str(n) for n in hit_lines)} 行），"
                        "疑似模型退化输出，请补全脚本内容"
                    ),
                ))
        return errors


    # ── check 6: unknown_command（P13，warning 级）───────────────────────────

    # GDL 语句行首词判定辅助：行内注释与字符串字面量遮蔽（字符串整体换成 ""，
    # 注释截断），供首词/NOT 扫描使用。GDL 字符串不跨行。
    @staticmethod
    def _mask_line(line: str) -> str:
        out: list[str] = []
        in_string = False
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == '"':
                if not in_string:
                    in_string = True
                    out.append('""')
                    i += 1
                    while i < n and line[i] != '"':
                        i += 1
                    continue  # i 停在闭合引号或行尾，下一轮处理
                in_string = False
                i += 1
                continue
            if ch == "!" and not in_string:
                break  # 行内注释截断
            out.append(ch)
            i += 1
        return "".join(out)

    # 行尾是否为 `,`（引号外）——多行语句 continuation 标记
    @staticmethod
    def _ends_with_comma(code: str) -> bool:
        return code.rstrip().endswith(",")

    def _statement_first_words(
        self, code: str
    ) -> list[tuple[int, str]]:
        """逐行取 GDL 语句首词（1-based 行号, 大写首词）。

        跳过：空行、注释行、标签行（"name":）、赋值行（含数组下标赋值
        name[i] = ...）、多行语句 continuation 行（上一语句行尾逗号）。
        """
        hits: list[tuple[int, str]] = []
        prev_cont = False
        for idx, raw in enumerate(code.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("!"):
                continue
            masked = self._mask_line(line)
            clean = masked.strip()
            if not clean:
                continue
            if prev_cont:
                prev_cont = self._ends_with_comma(clean)
                continue
            # 标签行："PatternZhileng": / "bar":
            if re.match(r'^""\s*:$', clean):
                prev_cont = False
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", clean)
            if not m:
                # 行首不是标识符（数字/括号/运算符）→ continuation 数据行
                prev_cont = self._ends_with_comma(clean)
                continue
            first, rest = m.group(1), m.group(2).strip()
            # 赋值行：name = ... / name[i] = ... / name[i + 1] = ...（排除 ==）
            if re.match(r"(\s*\[[^\]]*\])?\s*=(?!=)", rest):
                prev_cont = self._ends_with_comma(clean)
                continue
            hits.append((idx, first.upper()))
            prev_cont = self._ends_with_comma(clean)
        return hits

    def _check_unknown_command(self, project: "HSFProject") -> list[StaticError]:
        """语句首词不在已知 GDL 命令集 → warning（Archicad 当作无 CALL 的
        宏调用，"Missing CALL keyword (not recommended)"）。

        已知集合来自 openbrep/data/gdl_commands.txt（wiki frontmatter +
        gdl_keywords GDL_BUILTINS + 预览器分发表 + 命令索引 + 控制流补全，
        见文件头）。UNLOCK 这类臆造命令由此浮出水面。
        """
        warnings: list[StaticError] = []
        for gdl_file in ("3d.gdl", "2d.gdl", "1d.gdl", "ui.gdl", "vl.gdl"):
            code = self._get_script(project, gdl_file) or ""
            if not code.strip():
                continue
            by_cmd: dict[str, list[int]] = {}
            for line_no, first in self._statement_first_words(code):
                if first not in GDL_COMMANDS:
                    by_cmd.setdefault(first, []).append(line_no)
            if not by_cmd:
                continue
            file_path = f"scripts/{gdl_file}"
            for first, line_nos in sorted(by_cmd.items()):
                warnings.append(StaticError(
                    check_type="unknown_command",
                    file=file_path,
                    detail=(
                        f"第 {'、'.join(str(n) for n in line_nos)} 行出现未知命令 "
                        f"'{first}'：Archicad 会将其当作无 CALL 的宏调用"
                        "（'Missing CALL keyword (not recommended)'）。"
                        " 若是笔误请更正；若确为宏调用请改用 CALL。"
                    ),
                ))
        return warnings

    # ── check 7: bare_not（P13，error 级）────────────────────────────────────

    # NOT 是布尔**函数** NOT (x)（GDL Reference Guide Functions），不是裸
    # 运算符：语句中 NOT 后面必须紧跟 `(`，否则 Archicad 报
    # "Missing parameter(s) after function"。字符串字面量与注释内不判。
    _NOT_RE = re.compile(r"\bNOT\b", re.IGNORECASE)

    # NOT 前的上下文必须是运算符/控制流位置（表达式里的 NOT 只能以函数形态
    # NOT (x) 出现）。排除散文里的英文 "not"（如录制的 LLM 输出残留在脚本
    # 中的 "commands not whitelist"）——那既不是语句也不是 GDL。
    _BARE_NOT_PREV_WORDS: frozenset[str] = frozenset({
        "IF", "THEN", "ELSE", "ELSIF", "ELSEIF", "AND", "OR", "EXOR",
        "NOT", "MOD", "DIV",
    })
    _BARE_NOT_PREV_CHARS: frozenset[str] = frozenset("( = , : + - * / < > & | ;".split())

    def _not_in_operator_position(self, masked: str, start: int) -> bool:
        before = masked[:start].rstrip()
        if not before:
            return True  # 行首（语句以 NOT 开头本身就是裸 NOT）
        last = before[-1]
        if last.isalnum() or last == "_":
            m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", before)
            return bool(m and m.group(1).upper() in self._BARE_NOT_PREV_WORDS)
        return last in self._BARE_NOT_PREV_CHARS

    def _check_bare_not(self, project: "HSFProject") -> list[StaticError]:
        errors: list[StaticError] = []
        for gdl_file in ("3d.gdl", "2d.gdl", "1d.gdl", "ui.gdl", "vl.gdl"):
            code = self._get_script(project, gdl_file) or ""
            if not code.strip():
                continue
            hit_lines: list[int] = []
            for idx, raw in enumerate(code.splitlines(), start=1):
                masked = self._mask_line(raw)
                for m in self._NOT_RE.finditer(masked):
                    after = masked[m.end():].lstrip()
                    if after.startswith("("):
                        continue  # NOT (x) / NOT(x) 合法
                    if not self._not_in_operator_position(masked, m.start()):
                        continue  # 散文里的英文 "not"，非 GDL 运算符位置
                    hit_lines.append(idx)
                    break  # 一行最多报一次
            if not hit_lines:
                continue
            errors.append(StaticError(
                check_type="bare_not",
                file=f"scripts/{gdl_file}",
                detail=(
                    f"第 {'、'.join(str(n) for n in hit_lines)} 行出现裸 NOT："
                    "NOT 是布尔函数，必须写作 NOT (x)（或 NOT(x)），"
                    "裸 NOT 会让 Archicad 报 'Missing parameter(s) after "
                    "function'。请改为 NOT (condition) 或等效的 = 0 比较。"
                ),
            ))
        return errors


# ── prose_leak 检测函数（P12：GDL 散文守卫，供工具层写盘前拦截）──────

# markdown ATX 标题行：#/## 开头（GDL 行首没有 # 语法；注释是 !）
_MD_HEADING_RE = re.compile(r"^#{1,6}(?:\s|$)")
# markdown 加粗标记：**...**（GDL 无 ** 运算符）
_MD_BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
# markdown 行内代码标记：`...`（GDL 无反引号语法）
_MD_INLINE_CODE_RE = re.compile(r"`[^`]+`")


@dataclass
class ProseLeakHit:
    """一行代码里发现的散文（markdown 形状）泄漏。"""

    line: int      # 1-based 行号
    kind: str      # md_heading | md_table | md_bold | md_inline_code
    excerpt: str   # 去注释后的代码片段（截断展示）


def find_prose_leaks(content: str) -> list[ProseLeakHit]:
    """扫描 GDL 脚本内容中的 markdown 散文泄漏（P12 事故形状）。

    只对非注释行判定：先去掉行内 `!` 注释再匹配，合法注释里的任何文字
    都不误伤。识别形状：
      - md_heading: `#`/`##` 开头的标题行（GDL 注释是 `!`，行首 # 非 GDL）
      - md_table:   `|` 开头且含多个 `|` 的表格行（含 `| --- | --- |` 分隔行）
      - md_bold:    非注释行中的 `**粗体**`
      - md_inline_code: 非注释行中的 `反引号包裹`
    """
    hits: list[ProseLeakHit] = []
    for idx, raw in enumerate(content.splitlines(), start=1):
        code = raw.split("!", 1)[0].strip()
        if not code:
            continue
        if _MD_HEADING_RE.match(code):
            hits.append(ProseLeakHit(idx, "md_heading", code[:60]))
        elif code.startswith("|") and code.count("|") >= 2:
            hits.append(ProseLeakHit(idx, "md_table", code[:60]))
        else:
            for kind, rx in (("md_bold", _MD_BOLD_RE), ("md_inline_code", _MD_INLINE_CODE_RE)):
                if rx.search(code):
                    hits.append(ProseLeakHit(idx, kind, code[:60]))
                    break
    return hits
