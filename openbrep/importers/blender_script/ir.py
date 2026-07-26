"""
BS2G Intermediate Representation (IR).

Data structures produced by the AST parser and consumed by the
GDL mapper/generator.  Every node carries a ``line`` field pointing
back to the source line in the Blender script for traceability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass
class IRParameter:
    """A parameter extracted from the target function's signature."""

    name: str
    python_type: str        # "float" | "int" | "bool" | "str"
    default_value: str      # stringified default, e.g. "1.0"
    gdl_type: str           # "Length" | "Integer" | "Boolean" | "Angle" | "RealNum" | "String"
    inferred_unit: str = "" # "m" | "deg" | "" — from variable name heuristics


@dataclass
class IRPrimitive:
    """A Blender primitive creation call (cube, cylinder, …)."""

    kind: str               # "cube" | "cylinder" | "sphere" | "cone"
    args: dict              # keyword args, e.g. {"size": "1", "location": "(0, 0, 0)"}
    line: int


@dataclass
class IRTransform:
    """A transform assignment (location / scale / rotation_euler)."""

    kind: str               # "translate" | "scale" | "rotate"
    axis: str | None        # "x" | "y" | "z" | None (all axes)
    value: str              # expression string, may reference parameters
    line: int


@dataclass
class IRLoop:
    """A ``for`` loop (``for i in range(n)`` or ``for x in list``)."""

    var_name: str
    start: str              # "1" for range(n), or literal
    end: str                # expression string
    body: list[IRNode] = field(default_factory=list)
    line: int = 0


@dataclass
class IRCondition:
    """An ``if / else`` block."""

    condition: str
    then_body: list[IRNode] = field(default_factory=list)
    else_body: list[IRNode] = field(default_factory=list)
    line: int = 0


@dataclass
class IRAssignment:
    """A local variable assignment (``spacing = height / (n + 1)``)."""

    name: str
    value: str              # expression string
    line: int = 0


@dataclass
class IRUnsupported:
    """An operation that cannot be converted to GDL."""

    operation: str          # e.g. "modifier_add(SUBSURF)"
    reason: str
    line: int
    source_line: str = ""


IRNode = Union[
    IRPrimitive,
    IRTransform,
    IRLoop,
    IRCondition,
    IRAssignment,
    IRUnsupported,
]


@dataclass
class IRScript:
    """Complete intermediate representation of one Blender function."""

    function_name: str
    parameters: list[IRParameter] = field(default_factory=list)
    body: list[IRNode] = field(default_factory=list)
    local_vars: dict[str, str] = field(default_factory=dict)
    warnings: list[IRUnsupported] = field(default_factory=list)
