"""
BS2G AST Parser — Blender Python → IR.

Parses a Blender Python script using the stdlib ``ast`` module and
produces an :class:`IRScript`.  Unrecognised statements degrade to
:class:`IRUnsupported` nodes; the parser never raises on unsupported
input.

Supported patterns (v1):
  - ``bpy.ops.mesh.primitive_{cube,cylinder,uv_sphere,cone}_add()``
  - ``obj.location = (x, y, z)``
  - ``obj.scale = (x, y, z)``
  - ``obj.rotation_euler = (rx, ry, rz)``
  - ``for i in range(n):``
  - ``if cond: / else:``
  - simple variable assignments
  - ``math.`` calls translated to GDL equivalents (SIN/COS/PI/…);
    unmappable math degrades to IRUnsupported instead of silent drop
  - dead-store elimination: assignments never referenced are dropped
"""

from __future__ import annotations

import ast
import copy
import re
from typing import Optional

from openbrep.importers.blender_script.ir import (
    IRAssignment,
    IRCondition,
    IRLoop,
    IRNode,
    IRPrimitive,
    IRScript,
    IRTransform,
    IRUnsupported,
)
from openbrep.importers.blender_script.param_extractor import extract_parameters
from openbrep.importers.blender_script.warnings import WarningCollector

# ── Primitive name mapping ──────────────────────────────────

_PRIMITIVE_KINDS = {
    "primitive_cube_add": "cube",
    "primitive_cylinder_add": "cylinder",
    "primitive_uv_sphere_add": "sphere",
    "primitive_cone_add": "cone",
}

# ── Transform attribute mapping ─────────────────────────────

_TRANSFORM_ATTRS = {
    "location": "translate",
    "scale": "scale",
    "rotation_euler": "rotate",
}


def parse_blender_script(
    code: str,
    target_function: Optional[str] = None,
) -> IRScript:
    """
    Parse a Blender Python script into an IRScript.

    Args:
        code: Blender Python source.
        target_function: Name of the function to convert.
            ``None`` → use the first function definition found.

    Returns:
        IRScript with parameters, body nodes, and warnings.
        Never raises on unsupported syntax — degrades to IRUnsupported.
    """
    warnings = WarningCollector()

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return IRScript(
            function_name="<syntax_error>",
            warnings=[IRUnsupported(
                operation="ast.parse",
                reason=f"Python syntax error: {exc}",
                line=exc.lineno or 0,
                source_line=exc.text or "",
            )],
        )

    func_def = _find_function(tree, target_function)
    if func_def is None:
        name = target_function or "<first>"
        return IRScript(
            function_name=name,
            warnings=[IRUnsupported(
                operation="find_function",
                reason=f"No function definition found (target={name!r})",
                line=0,
            )],
        )

    params = extract_parameters(func_def)
    body = _parse_body(func_def.body, code, warnings)

    # Collect local variable assignments for the local_vars dict,
    # then drop assignments whose value is never referenced.
    local_vars: dict[str, str] = {}
    _collect_locals(body, local_vars)
    _eliminate_dead_vars(body, local_vars)

    return IRScript(
        function_name=func_def.name,
        parameters=params,
        body=body,
        local_vars=local_vars,
        warnings=warnings.warnings,
    )


# ── Internal helpers ────────────────────────────────────────


def _find_function(
    tree: ast.Module,
    target: Optional[str],
) -> Optional[ast.FunctionDef]:
    """Find the target function (or first function) in the module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if target is None or node.name == target:
                return node
    return None


def _parse_body(
    stmts: list[ast.stmt],
    source: str,
    warnings: WarningCollector,
) -> list[IRNode]:
    """Parse a list of statements into IR nodes."""
    nodes: list[IRNode] = []
    for stmt in stmts:
        # Short list-literal for-loops get unrolled (e.g. for side in [-1, 1])
        if isinstance(stmt, ast.For) and _is_short_list_literal(stmt.iter):
            nodes.extend(_unroll_for(stmt, source, warnings))
            continue
        node = _parse_stmt(stmt, source, warnings)
        if node is not None:
            nodes.append(node)
    return nodes


def _parse_stmt(
    stmt: ast.stmt,
    source: str,
    warnings: WarningCollector,
) -> Optional[IRNode]:
    """Parse a single statement into an IR node."""

    # ── Expression statement (function calls) ───────────────
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _parse_call_stmt(stmt.value, source, warnings, stmt.lineno)

    # ── Assignment ──────────────────────────────────────────
    if isinstance(stmt, ast.Assign):
        return _parse_assign(stmt, source, warnings)

    # ── For loop ────────────────────────────────────────────
    if isinstance(stmt, ast.For):
        return _parse_for(stmt, source, warnings)

    # ── If / else ───────────────────────────────────────────
    if isinstance(stmt, ast.If):
        return _parse_if(stmt, source, warnings)

    # ── import math — supported implicitly (math.* is translated) ──
    if isinstance(stmt, ast.Import) and any(a.name == "math" for a in stmt.names):
        return None

    # ── Fallback: unsupported ───────────────────────────────
    src_line = _get_source_line(source, stmt.lineno)
    return warnings.add(
        operation=type(stmt).__name__,
        reason=f"Unsupported statement type: {type(stmt).__name__}",
        line=stmt.lineno,
        source_line=src_line,
    )


def _parse_call_stmt(
    call: ast.Call,
    source: str,
    warnings: WarningCollector,
    lineno: int,
) -> IRNode:
    """Parse a call expression statement."""
    dotted = _dotted_name(call.func)

    # bpy.ops.mesh.primitive_*_add(...)
    if dotted:
        parts = dotted.split(".")
        func_name = parts[-1]

        if func_name in _PRIMITIVE_KINDS:
            try:
                return _parse_primitive(call, _PRIMITIVE_KINDS[func_name], lineno)
            except _UnmappableMath as exc:
                return warnings.add(
                    operation=f"primitive({func_name})",
                    reason=str(exc),
                    line=lineno,
                    source_line=_get_source_line(source, lineno),
                )

        # bpy.ops.object.modifier_add(...) and other bpy.ops calls
        if "bpy" in parts and "ops" in parts:
            src_line = _get_source_line(source, lineno)
            op_name = ".".join(parts[parts.index("ops") + 1:])
            return warnings.add(
                operation=f"bpy.ops.{op_name}",
                reason="Unsupported bpy.ops call — cannot map to GDL",
                line=lineno,
                source_line=src_line,
            )

    # Unrecognised call
    src_line = _get_source_line(source, lineno)
    return warnings.add(
        operation=ast.unparse(call.func),
        reason="Unrecognised function call",
        line=lineno,
        source_line=src_line,
    )


def _parse_primitive(
    call: ast.Call,
    kind: str,
    lineno: int,
) -> IRPrimitive:
    """Extract primitive kind and keyword arguments."""
    args: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg is not None:
            args[kw.arg] = _unparse_gdl(kw.value)
    # Positional args (rare but possible)
    for i, pos in enumerate(call.args):
        args[f"_pos{i}"] = _unparse_gdl(pos)
    return IRPrimitive(kind=kind, args=args, line=lineno)


def _parse_assign(
    stmt: ast.Assign,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse an assignment statement."""
    if len(stmt.targets) != 1:
        src_line = _get_source_line(source, stmt.lineno)
        return warnings.add(
            operation="multi_target_assign",
            reason="Multiple assignment targets not supported",
            line=stmt.lineno,
            source_line=src_line,
        )

    target = stmt.targets[0]

    # obj.location = (...) / obj.scale = (...) / obj.rotation_euler = (...)
    if isinstance(target, ast.Attribute) and target.attr in _TRANSFORM_ATTRS:
        kind = _TRANSFORM_ATTRS[target.attr]
        try:
            if kind == "rotate":
                # Angle units are resolved parser-side (radians → degrees);
                # the mapper emits ROT verbatim and never converts.
                components = _extract_rotation_vec3(stmt.value)
            else:
                components = _extract_vec3(stmt.value)
        except _UnmappableMath as exc:
            return warnings.add(
                operation=f"transform({target.attr})",
                reason=str(exc),
                line=stmt.lineno,
                source_line=_get_source_line(source, stmt.lineno),
            )
        return IRTransform(kind=kind, components=components, line=stmt.lineno)

    # obj.attr = value (non-transform attribute)
    if isinstance(target, ast.Attribute):
        src_line = _get_source_line(source, stmt.lineno)
        return warnings.add(
            operation=f"attr_assign({target.attr})",
            reason=f"Unsupported attribute assignment: {target.attr}",
            line=stmt.lineno,
            source_line=src_line,
        )

    # Simple variable: name = expr
    if isinstance(target, ast.Name):
        try:
            value_str = _unparse_gdl(stmt.value)
        except _UnmappableMath as exc:
            # Unmappable math.* — degrade to a warning, never silently drop
            return warnings.add(
                operation=f"assign({target.id})",
                reason=str(exc),
                line=stmt.lineno,
                source_line=_get_source_line(source, stmt.lineno),
            )
        # Filter Blender API references (bpy, bmesh) — not GDL
        if _has_blender_api_ref(value_str):
            return None
        return IRAssignment(
            name=target.id,
            value=value_str,
            line=stmt.lineno,
        )

    # Subscript or other complex target
    src_line = _get_source_line(source, stmt.lineno)
    return warnings.add(
        operation="complex_assign",
        reason=f"Unsupported assignment target: {ast.unparse(target)}",
        line=stmt.lineno,
        source_line=src_line,
    )


def _parse_for(
    stmt: ast.For,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse a for loop.  Only ``range()`` iterators are supported."""
    # Loop variable
    if isinstance(stmt.target, ast.Name):
        var_name = stmt.target.id
    else:
        var_name = ast.unparse(stmt.target)

    # Only range() calls produce GDL FOR loops
    if not _is_range_call(stmt.iter):
        src_line = _get_source_line(source, stmt.lineno)
        return warnings.add(
            operation="for_non_range",
            reason=f"Non-range iterator not supported: {ast.unparse(stmt.iter)}",
            line=stmt.lineno,
            source_line=src_line,
        )

    try:
        start, end, step = _extract_range(stmt.iter)
    except _UnmappableMath as exc:
        return warnings.add(
            operation="for_range",
            reason=str(exc),
            line=stmt.lineno,
            source_line=_get_source_line(source, stmt.lineno),
        )
    body = _parse_body(stmt.body, source, warnings)

    return IRLoop(
        var_name=var_name,
        start=start,
        end=end,
        step=step,
        body=body,
        line=stmt.lineno,
    )


# ── List-literal loop unrolling ─────────────────────────────

_MAX_UNROLL = 8


def _is_short_list_literal(node: ast.expr) -> bool:
    """True if *node* is a list/tuple literal with ≤ _MAX_UNROLL elements."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    return len(node.elts) <= _MAX_UNROLL


def _is_range_call(node: ast.expr) -> bool:
    """True if *node* is a ``range(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    return _dotted_name(node.func) == "range"


def _unroll_for(
    stmt: ast.For,
    source: str,
    warnings: WarningCollector,
) -> list[IRNode]:
    """Unroll ``for x in [a, b, c]:`` into repeated body blocks.

    Each occurrence of the loop variable in expression strings is
    replaced with the corresponding literal value.
    """
    if isinstance(stmt.target, ast.Name):
        var_name = stmt.target.id
    else:
        var_name = ast.unparse(stmt.target)

    assert isinstance(stmt.iter, (ast.List, ast.Tuple))
    literals = [ast.unparse(elt) for elt in stmt.iter.elts]

    nodes: list[IRNode] = []
    for lit in literals:
        body = _parse_body(stmt.body, source, warnings)
        nodes.extend(_substitute_var(body, var_name, lit))
    return nodes


def _substitute_var(
    nodes: list[IRNode],
    var_name: str,
    value: str,
) -> list[IRNode]:
    """Replace bare *var_name* references with *value* in expression strings."""
    pattern = re.compile(rf"\b{re.escape(var_name)}\b")

    def sub(expr: str) -> str:
        return pattern.sub(value, expr)

    result: list[IRNode] = []
    for node in nodes:
        if isinstance(node, IRPrimitive):
            new_args = {k: sub(v) for k, v in node.args.items()}
            result.append(IRPrimitive(kind=node.kind, args=new_args, line=node.line))
        elif isinstance(node, IRTransform):
            result.append(IRTransform(
                kind=node.kind,
                components=tuple(sub(c) for c in node.components),
                line=node.line,
            ))
        elif isinstance(node, IRAssignment):
            result.append(IRAssignment(name=node.name, value=sub(node.value), line=node.line))
        elif isinstance(node, IRLoop):
            result.append(IRLoop(
                var_name=node.var_name,
                start=sub(node.start),
                end=sub(node.end),
                step=sub(node.step) if node.step else None,
                body=_substitute_var(node.body, var_name, value),
                line=node.line,
            ))
        elif isinstance(node, IRCondition):
            result.append(IRCondition(
                condition=sub(node.condition),
                then_body=_substitute_var(node.then_body, var_name, value),
                else_body=_substitute_var(node.else_body, var_name, value),
                line=node.line,
            ))
        else:
            result.append(node)
    return result


def _extract_range(iter_node: ast.expr) -> tuple[str, str, str | None]:
    """Extract (start, inclusive_end, step) from a ``range()`` call.

    Python ``range`` uses exclusive end; GDL ``FOR`` uses inclusive end.
    We convert: ``range(n)`` → ``FOR i = 0 TO n - 1``.

    Returns:
        (start, end, step) where *end* is the inclusive GDL bound and
        *step* is ``None`` when the step is 1.
    """
    assert isinstance(iter_node, ast.Call)
    args = iter_node.args

    if len(args) == 1:
        # range(n) → 0 TO n-1
        return "0", _minus_one(_unparse_gdl(args[0])), None

    start = _unparse_gdl(args[0])
    end = _minus_one(_unparse_gdl(args[1]))

    step: str | None = None
    if len(args) >= 3:
        step = _unparse_gdl(args[2])

    return start, end, step


def _minus_one(expr: str) -> str:
    """Produce ``expr - 1``, collapsing pure integer literals."""
    try:
        return str(int(expr) - 1)
    except ValueError:
        return f"{expr} - 1"


def _parse_if(
    stmt: ast.If,
    source: str,
    warnings: WarningCollector,
) -> IRNode:
    """Parse an if/else block."""
    try:
        condition = _unparse_gdl(stmt.test)
    except _UnmappableMath as exc:
        return warnings.add(
            operation="if_condition",
            reason=str(exc),
            line=stmt.lineno,
            source_line=_get_source_line(source, stmt.lineno),
        )
    then_body = _parse_body(stmt.body, source, warnings)
    else_body = _parse_body(stmt.orelse, source, warnings) if stmt.orelse else []

    return IRCondition(
        condition=condition,
        then_body=then_body,
        else_body=else_body,
        line=stmt.lineno,
    )


# ── Utilities ───────────────────────────────────────────────


def _dotted_name(node: ast.expr) -> Optional[str]:
    """Reconstruct a dotted name like ``bpy.ops.mesh.primitive_cube_add``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _get_source_line(source: str, lineno: int) -> str:
    """Get a source line by 1-based line number."""
    lines = source.splitlines()
    if 0 < lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""


def _extract_vec3(node: ast.expr) -> tuple[str, str, str]:
    """Extract (x, y, z) expression strings from a tuple/list literal.

    ``(width, depth, height)`` → ``("width", "depth", "height")``
    Falls back to ``("expr", "0", "0")`` for non-tuple values.
    """
    if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 3:
        return (
            _unparse_gdl(node.elts[0]),
            _unparse_gdl(node.elts[1]),
            _unparse_gdl(node.elts[2]),
        )
    return (_unparse_gdl(node), "0", "0")


# ── Rotation components: radians → degrees ──────────────────

_ATOMIC_EXPR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?")


def _is_math_call(node: ast.expr, fn: str) -> bool:
    """True if *node* is ``math.<fn>(<single positional arg>)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "math"
        and node.func.attr == fn
        and len(node.args) == 1
        and not node.keywords
    )


def _rotation_component(node: ast.expr) -> str:
    """Convert one rotation_euler component to a GDL degrees expression.

    Blender angles are radians, GDL angles are degrees.  The conversion
    happens here (parser side) because only the parser can still see
    whether the source expression was wrapped in ``math.radians()`` or
    ``math.degrees()`` — both already denote degrees and pass through.
    Everything else (literals, variables, complex expressions) is
    conservatively treated as Blender-native radians.
    """
    if _is_math_call(node, "radians") or _is_math_call(node, "degrees"):
        return _unparse_gdl(node.args[0])
    text = _unparse_gdl(node)
    # 0 radians == 0 degrees — keep it plain
    if text in ("0", "0.0"):
        return "0"
    if _ATOMIC_EXPR_RE.fullmatch(text):
        return f"{text} * 180 / PI"
    return f"({text}) * 180 / PI"


def _extract_rotation_vec3(node: ast.expr) -> tuple[str, str, str]:
    """``rotation_euler`` tuple → (rx, ry, rz) degree expression strings."""
    if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 3:
        return (
            _rotation_component(node.elts[0]),
            _rotation_component(node.elts[1]),
            _rotation_component(node.elts[2]),
        )
    return (_rotation_component(node), "0", "0")


# ── math.* → GDL translation ────────────────────────────────


class _UnmappableMath(Exception):
    """A ``math.`` call/attribute has no GDL equivalent."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"No GDL equivalent for {name}")


# math.<fn>(x) with a direct one-argument GDL equivalent
_MATH_FUNC_MAP = {
    "sin": "SIN",
    "cos": "COS",
    "tan": "TAN",
    "sqrt": "SQR",
    "floor": "INT",
}

# math.<fn> names rewritten by _MathRewriter (bare references are
# left in place and caught by the leftover check in _unparse_gdl)
_MATH_KNOWN_FUNCS = frozenset(_MATH_FUNC_MAP) | {"radians", "degrees", "ceil"}

_LEFTOVER_MATH_RE = re.compile(r"\bmath\.")


class _MathRewriter(ast.NodeTransformer):
    """Rewrite ``math.`` calls/attributes to GDL equivalents.

    GDL angles are degrees, so ``math.radians(x)`` collapses to ``x``.
    Raises :class:`_UnmappableMath` for math members with no mapping.
    """

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "math":
            if node.attr == "pi":
                return ast.copy_location(ast.Name(id="PI", ctx=ast.Load()), node)
            if node.attr in _MATH_KNOWN_FUNCS:
                # Function reference — handled by the enclosing visit_Call.
                return node
            raise _UnmappableMath(f"math.{node.attr}")
        return node

    def visit_Call(self, node: ast.Call) -> ast.expr:
        self.generic_visit(node)
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "math"
        ):
            return node
        if len(node.args) != 1 or node.keywords:
            raise _UnmappableMath(f"math.{func.attr}")
        arg = node.args[0]
        if func.attr == "radians":
            return arg
        if func.attr == "degrees":
            inner = ast.unparse(arg)
            if not isinstance(arg, (ast.Name, ast.Constant)):
                inner = f"({inner})"
            return _expr_template(f"{inner} * 180 / PI", node)
        if func.attr == "ceil":
            a = ast.unparse(arg)
            return _expr_template(f"INT({a}) + (FRA({a}) > 0)", node)
        if func.attr in _MATH_FUNC_MAP:
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id=_MATH_FUNC_MAP[func.attr], ctx=ast.Load()),
                    args=[arg],
                    keywords=[],
                ),
                node,
            )
        raise _UnmappableMath(f"math.{func.attr}")


def _expr_template(template: str, ref: ast.AST) -> ast.expr:
    """Parse a small GDL expression template back into an AST node."""
    return ast.copy_location(ast.parse(template, mode="eval").body, ref)


def _unparse_gdl(node: ast.expr) -> str:
    """Unparse *node* with ``math.`` translated to GDL equivalents.

    Raises:
        _UnmappableMath: the expression uses a math member with no GDL
            equivalent, or a bare ``math.`` reference survives rewriting.
    """
    rewritten = _MathRewriter().visit(copy.deepcopy(node))
    text = ast.unparse(rewritten)
    if _LEFTOVER_MATH_RE.search(text):
        raise _UnmappableMath("math.*")
    return text


# ── Blender API filtering ───────────────────────────────────

_BLENDER_API_RE = re.compile(r'\b(bpy|bmesh)\.')


def _has_blender_api_ref(expr_str: str) -> bool:
    """Return True if *expr_str* references Blender APIs (bpy, bmesh)
    that cannot appear in GDL.

    ``math.`` is deliberately NOT filtered — it is translated to GDL
    equivalents by :func:`_unparse_gdl`; dropping it would leave later
    references to the variable undefined.
    """
    return bool(_BLENDER_API_RE.search(expr_str))


def _collect_locals(nodes: list[IRNode], out: dict[str, str]) -> None:
    """Recursively collect IRAssignment nodes into a dict."""
    for node in nodes:
        if isinstance(node, IRAssignment):
            out[node.name] = node.value
        elif isinstance(node, IRLoop):
            _collect_locals(node.body, out)
        elif isinstance(node, IRCondition):
            _collect_locals(node.then_body, out)
            _collect_locals(node.else_body, out)


# ── Dead-store elimination ──────────────────────────────────

_IDENT_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _eliminate_dead_vars(body: list[IRNode], local_vars: dict[str, str]) -> None:
    """Drop assignments whose value is never referenced (dead stores).

    A variable is live when referenced by a non-assignment expression
    (primitive arg, transform component, loop bound, condition) or by
    the value of another live assignment (fixpoint).  Dead IRAssignment
    nodes are pruned from *body* (in place) and from *local_vars*.
    """
    if not local_vars:
        return
    assigned = set(local_vars)

    def refs(expr: str) -> set[str]:
        return set(_IDENT_TOKEN_RE.findall(expr)) & assigned

    live: set[str] = set()

    def scan(nodes: list[IRNode]) -> None:
        for node in nodes:
            if isinstance(node, IRPrimitive):
                for v in node.args.values():
                    live.update(refs(v))
            elif isinstance(node, IRTransform):
                for c in node.components:
                    live.update(refs(c))
            elif isinstance(node, IRLoop):
                live.update(refs(node.start))
                live.update(refs(node.end))
                if node.step:
                    live.update(refs(node.step))
                scan(node.body)
            elif isinstance(node, IRCondition):
                live.update(refs(node.condition))
                scan(node.then_body)
                scan(node.else_body)

    scan(body)

    # Propagate liveness through assignment values until fixpoint
    changed = True
    while changed:
        changed = False
        for name, value in local_vars.items():
            if name not in live:
                continue
            for ref in refs(value):
                if ref not in live:
                    live.add(ref)
                    changed = True

    dead = assigned - live
    if not dead:
        return

    for name in dead:
        del local_vars[name]

    def prune(nodes: list[IRNode]) -> None:
        nodes[:] = [
            node for node in nodes
            if not (isinstance(node, IRAssignment) and node.name in dead)
        ]
        for node in nodes:
            if isinstance(node, IRLoop):
                prune(node.body)
            elif isinstance(node, IRCondition):
                prune(node.then_body)
                prune(node.else_body)

    prune(body)
