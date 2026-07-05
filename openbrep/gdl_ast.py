"""
GDL lightweight AST — dataclasses + tolerant parser.

Four node types:
  TransformFrame  — ADD/ROT/MUL/ADDX/ADDY/ADDZ/ADD2/MUL2/ROT2/ROTX/ROTY/ROTZ
  GeometryCall    — BLOCK/PRISM_/CYLIND etc. geometry primitives
  ControlBlock    — IF/FOR control flow with nested children, THEN/ELSE split
  GDLScript       — top-level container returned by parse_gdl_script()

parse_gdl_script(code) is tolerant: never raises, always returns a GDLScript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

# ── regex catalogue ────────────────────────────────────────────────────────────

_TRANSFORM_RE = re.compile(
    r"^\s*(ADD(?:[XYZ2])?|ADD2|MUL2?|ROT(?:[XYZ2])?|ROTX|ROTY|ROTZ)\b",
    re.IGNORECASE,
)

_GEOMETRY_RE = re.compile(
    r"^\s*(BLOCK|RECT|CYLIND|CONE|SPHERE|ELLIPSE|PRISM_?|BPRISM_?|CPRISM_?"
    r"|FPRISM_?|CROOF_?|ARMC|TUBE|RULED|MESH|TEXT|TEXT2|PICTURE|POLY_?|"
    r"POLY2_?|MASS|LIGHT|WALLHOLE|WALLNICHE|CUTPLANE|CUTPOLY|GROUP|ENDGROUP"
    r"|GLOB|HOTSPOT|HOTSPOT2|LINETYPE|SETLAYER|PEN|MATERIAL|SURFACE)\b",
    re.IGNORECASE,
)

_IF_RE = re.compile(r"^\s*IF\b", re.IGNORECASE)
_SINGLE_LINE_IF_RE = re.compile(r"^\s*IF\b.*\bTHEN\b\s*\S", re.IGNORECASE)
_ELSE_RE = re.compile(r"^\s*ELSE\b", re.IGNORECASE)
_ENDIF_RE = re.compile(r"^\s*ENDIF\b", re.IGNORECASE)
_FOR_RE = re.compile(r"^\s*FOR\b", re.IGNORECASE)
_NEXT_RE = re.compile(r"^\s*NEXT\b", re.IGNORECASE)
_DELALL_RE = re.compile(r"^\s*DELALL\b", re.IGNORECASE)

_ARGS_RE = re.compile(r"[^,\s]+")


def _parse_args(rest: str) -> list[str]:
    return _ARGS_RE.findall(rest)


# ── AST node types ─────────────────────────────────────────────────────────────

@dataclass
class TransformFrame:
    """One push onto the GDL transformation stack (ADD, ROT, MUL variants)."""
    name: str          # e.g. "ADD", "ROTX"
    args: list[str]    # raw argument tokens
    lineno: int        # 1-based line number in the original source


@dataclass
class GeometryCall:
    """One geometry primitive call (BLOCK, CYLIND, PRISM_, …)."""
    name: str
    args: list[str]
    lineno: int


@dataclass
class ControlBlock:
    """IF … ENDIF or FOR … NEXT block with separate THEN and ELSE tracking."""
    kind: str                               # "IF" or "FOR"
    children: list[ASTNode] = field(default_factory=list)
    # ELSE branch — populated only for IF blocks that have ELSE
    else_children: list[ASTNode] = field(default_factory=list)
    # Unrecognised lines in THEN branch (or FOR body); used for DEL counting
    raw_lines: list[str] = field(default_factory=list)
    # Unrecognised lines in ELSE branch
    else_raw_lines: list[str] = field(default_factory=list)
    has_else: bool = False
    # Net push delta from direct TransformFrames in THEN branch (quick reference)
    push_delta: int = 0
    pop_delta: int = 0
    lineno: int = 0
    end_lineno: int = 0


ASTNode = Union[TransformFrame, GeometryCall, ControlBlock]


@dataclass
class GDLScript:
    """Top-level parse result for a single .gdl source text."""
    frames: list[TransformFrame] = field(default_factory=list)
    geometry: list[GeometryCall] = field(default_factory=list)
    controls: list[ControlBlock] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)


# ── tolerant parser ────────────────────────────────────────────────────────────

def _split_name_args(line: str) -> tuple[str, list[str]]:
    """Return (command_name_upper, [arg_token, …]) from a stripped line."""
    parts = line.split(None, 1)
    name = parts[0].upper()
    args = _parse_args(parts[1]) if len(parts) > 1 else []
    return name, args


def parse_gdl_script(code: str) -> GDLScript:
    """Parse GDL source into a GDLScript AST.

    Tolerant: never raises on bad input; unrecognised lines go to raw_lines.
    THEN and ELSE branches are tracked separately in ControlBlock.
    """
    script = GDLScript()
    lines = code.splitlines()

    # Stack of (ControlBlock, in_else: bool) for nested IF/FOR
    block_stack: list[tuple[ControlBlock, bool]] = []

    def append_node(node: ASTNode) -> None:
        if block_stack:
            cb, in_else = block_stack[-1]
            if in_else:
                cb.else_children.append(node)
            else:
                cb.children.append(node)
        else:
            if isinstance(node, TransformFrame):
                script.frames.append(node)
            elif isinstance(node, GeometryCall):
                script.geometry.append(node)
            elif isinstance(node, ControlBlock):
                script.controls.append(node)

    def append_raw(raw: str) -> None:
        if block_stack:
            cb, in_else = block_stack[-1]
            if in_else:
                cb.else_raw_lines.append(raw)
            else:
                cb.raw_lines.append(raw)
        else:
            script.raw_lines.append(raw)

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        # Skip blank lines and comments
        if not stripped or stripped.startswith("!"):
            continue

        # Single-line IF (IF … THEN <stmt>) — do not open a block
        if _SINGLE_LINE_IF_RE.match(stripped):
            append_raw(raw)
            continue

        # DELALL — treat as raw (resets stack, branch analysis skips it)
        if _DELALL_RE.match(stripped):
            append_raw(raw)
            continue

        # IF (multiline)
        if _IF_RE.match(stripped):
            cb = ControlBlock(kind="IF", lineno=lineno)
            append_node(cb)
            block_stack.append((cb, False))
            continue

        # ELSE
        if _ELSE_RE.match(stripped):
            if block_stack and block_stack[-1][0].kind == "IF":
                cb = block_stack[-1][0]
                cb.has_else = True
                block_stack[-1] = (cb, True)
            continue

        # ENDIF
        if _ENDIF_RE.match(stripped):
            if block_stack and block_stack[-1][0].kind == "IF":
                closed, _ = block_stack.pop()
                closed.end_lineno = lineno
            continue

        # FOR
        if _FOR_RE.match(stripped):
            cb = ControlBlock(kind="FOR", lineno=lineno)
            append_node(cb)
            block_stack.append((cb, False))
            continue

        # NEXT
        if _NEXT_RE.match(stripped):
            if block_stack and block_stack[-1][0].kind == "FOR":
                closed, _ = block_stack.pop()
                closed.end_lineno = lineno
            continue

        # Transform frame
        if _TRANSFORM_RE.match(stripped):
            name, args = _split_name_args(stripped)
            node = TransformFrame(name=name, args=args, lineno=lineno)
            append_node(node)
            # Track push delta only in THEN branch
            if block_stack:
                cb, in_else = block_stack[-1]
                if not in_else:
                    cb.push_delta += 1
            continue

        # Geometry call
        if _GEOMETRY_RE.match(stripped):
            name, args = _split_name_args(stripped)
            node = GeometryCall(name=name, args=args, lineno=lineno)
            append_node(node)
            continue

        # Unrecognised — route to the current branch's raw_lines
        append_raw(raw)

    # Unclosed blocks: still valid nodes, just mark end_lineno = last line
    for cb, _ in block_stack:
        if cb.end_lineno == 0:
            cb.end_lineno = len(lines)

    return script
