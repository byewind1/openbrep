"""
GDL static checker — runs before compilation.

Four checks (all regex/count-based, no LLM):
  1. undefined_var   — script variables not declared in paramlist.xml
  2. forward_decl    — _underscore vars in 3d/2d not assigned in 1d.gdl
  3. stack_imbalance — ADD*/ROT*/MUL push count != DEL pop count in 3d.gdl
  4. block_mismatch  — unmatched IF/ENDIF or FOR/NEXT across any .gdl file

StaticChecker.check(project) returns StaticCheckResult immediately.
Returns passed=True when project is None (safe no-op).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from openbrep.gdl_keywords import (
    GDL_BUILTINS,
    GDL_BUILTINS_CASEFOLD,
    GLOBAL_PREFIXES,
)

__all__ = [
    "GDL_BUILTINS",
    "GLOBAL_PREFIXES",
    "StaticChecker",
    "StaticCheckResult",
    "StaticError",
]

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
    check_type: str   # "undefined_var" | "forward_decl" | "stack_imbalance" | "block_mismatch"
    file: str         # e.g. "scripts/3d.gdl"
    detail: str       # human-readable, injected into prompt hint


@dataclass
class StaticCheckResult:
    passed: bool
    errors: list[StaticError] = field(default_factory=list)


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
        errors.extend(self._check_undefined_var(project))
        errors.extend(self._check_forward_decl(project))
        errors.extend(self._check_stack_imbalance(project))
        errors.extend(self._check_block_mismatch(project))

        return StaticCheckResult(passed=len(errors) == 0, errors=errors)

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
