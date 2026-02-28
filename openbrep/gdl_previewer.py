"""Lightweight GDL preview interpreter (MVP subset).

This module executes a pragmatic subset of GDL and returns preview-friendly
geometry structures for 2D/3D rendering plus non-fatal warnings.

Design goals:
- Fast local preview for editor workflow
- Keep running on partial/unsupported scripts
- Never mutate source scripts
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from typing import Any


DEFAULT_FOR_LIMIT = 500


Point2D = tuple[float, float]
Point3D = tuple[float, float, float]


@dataclass
class PreviewMesh3D:
    name: str
    x: list[float]
    y: list[float]
    z: list[float]
    i: list[int]
    j: list[int]
    k: list[int]


@dataclass
class Preview2DResult:
    lines: list[tuple[Point2D, Point2D]] = field(default_factory=list)
    polygons: list[list[Point2D]] = field(default_factory=list)
    circles: list[tuple[float, float, float]] = field(default_factory=list)  # cx, cy, r
    arcs: list[tuple[float, float, float, float, float]] = field(default_factory=list)  # cx, cy, r, a0, a1
    warnings: list[str] = field(default_factory=list)


@dataclass
class Preview3DResult:
    meshes: list[PreviewMesh3D] = field(default_factory=list)
    wires: list[list[Point3D]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreviewResult:
    preview_2d: Preview2DResult
    preview_3d: Preview3DResult
    warnings: list[str] = field(default_factory=list)


def preview_2d_script(
    script_2d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
) -> Preview2DResult:
    """Preview a 2D GDL script using MVP command subset."""
    runtime = _PreviewRuntime(parameters=parameters, for_limit=for_limit)
    runtime.execute(script_2d or "", mode="2d")
    runtime.finish()
    return runtime.result_2d


def preview_3d_script(
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
) -> Preview3DResult:
    """Preview a 3D GDL script using MVP command subset."""
    runtime = _PreviewRuntime(parameters=parameters, for_limit=for_limit)
    runtime.execute(script_3d or "", mode="3d")
    runtime.finish()
    return runtime.result_3d


def preview_scripts(
    script_2d: str,
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
) -> PreviewResult:
    """Preview both 2D and 3D scripts and merge warnings."""
    p2d = preview_2d_script(script_2d, parameters=parameters, for_limit=for_limit)
    p3d = preview_3d_script(script_3d, parameters=parameters, for_limit=for_limit)
    return PreviewResult(
        preview_2d=p2d,
        preview_3d=p3d,
        warnings=[*p2d.warnings, *p3d.warnings],
    )


class _PreviewRuntime:
    _ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(.+)$")
    _FOR_RE = re.compile(
        r"^FOR\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+))?$",
        re.IGNORECASE,
    )

    def __init__(self, parameters: dict[str, Any] | None, for_limit: int):
        self.env = _normalize_parameters(parameters or {})
        self.for_limit = max(1, int(for_limit))
        self.loop_iterations = 0

        self._add_stack: list[Point3D] = []
        self._tx = 0.0
        self._ty = 0.0
        self._tz = 0.0

        self.result_2d = Preview2DResult()
        self.result_3d = Preview3DResult()
        self._warnings: list[str] = []

    def execute(self, script: str, mode: str) -> None:
        lines = _logical_lines(script)
        self._exec_block(lines, 0, len(lines), mode=mode)

    def finish(self) -> None:
        if self._add_stack:
            self._warn(0, f"ADD/DEL 栈未平衡，自动收敛 DEL {len(self._add_stack)}")
            self._add_stack.clear()
            self._tx = self._ty = self._tz = 0.0

        self.result_2d.warnings.extend(self._warnings)
        self.result_3d.warnings.extend(self._warnings)

    def _exec_block(self, lines: list[tuple[int, str]], start: int, end: int, mode: str) -> None:
        idx = start
        while idx < end:
            line_no, line = lines[idx]
            if _is_label_line(line):
                idx += 1
                continue

            # Assignment (except FOR header)
            if not re.match(r"^FOR\b", line, re.IGNORECASE):
                m_assign = self._ASSIGN_RE.match(line)
                if m_assign:
                    name = m_assign.group(1)
                    expr = m_assign.group(2)
                    value = self._eval_expr(expr, line_no)
                    if value is not None:
                        self.env[name.upper()] = value
                    idx += 1
                    continue

            # FOR/NEXT
            if re.match(r"^FOR\b", line, re.IGNORECASE):
                next_idx = self._find_matching_next(lines, idx, end)
                if next_idx is None:
                    self._warn(line_no, "FOR 缺少匹配 NEXT，已跳过")
                    idx += 1
                    continue

                self._execute_for(line, line_no, lines, idx + 1, next_idx, mode)
                idx = next_idx + 1
                continue

            if re.match(r"^NEXT\b", line, re.IGNORECASE):
                # Should only be consumed by _find_matching_next scope.
                self._warn(line_no, "遇到游离 NEXT，已忽略")
                idx += 1
                continue

            # Transform commands
            if self._handle_transform(line, line_no):
                idx += 1
                continue

            # No-op commands in preview
            if re.match(r"^(END|RETURN)\b", line, re.IGNORECASE):
                idx += 1
                continue

            # Geometry commands
            handled = False
            if mode == "2d":
                handled = self._handle_2d(line, line_no)
            elif mode == "3d":
                handled = self._handle_3d(line, line_no)

            if not handled:
                cmd = _extract_command(line)
                if cmd:
                    self._warn(line_no, f"未支持命令 {cmd}，已跳过")
                else:
                    self._warn(line_no, "无法解析语句，已跳过")

            idx += 1

    def _execute_for(
        self,
        for_line: str,
        line_no: int,
        lines: list[tuple[int, str]],
        body_start: int,
        body_end: int,
        mode: str,
    ) -> None:
        m = self._FOR_RE.match(for_line)
        if not m:
            self._warn(line_no, "FOR 语法无法解析，已跳过")
            return

        var_name = m.group(1).upper()
        start_v = self._eval_expr(m.group(2), line_no)
        end_v = self._eval_expr(m.group(3), line_no)
        step_v = self._eval_expr(m.group(4), line_no) if m.group(4) else 1.0

        if start_v is None or end_v is None or step_v is None:
            self._warn(line_no, "FOR 数值解析失败，已跳过")
            return

        if abs(step_v) < 1e-12:
            self._warn(line_no, "FOR STEP=0 非法，已跳过")
            return

        v = float(start_v)
        end_value = float(end_v)
        step = float(step_v)

        def _continue(cur: float) -> bool:
            if step > 0:
                return cur <= end_value + 1e-9
            return cur >= end_value - 1e-9

        while _continue(v):
            self.loop_iterations += 1
            if self.loop_iterations > self.for_limit:
                self._warn(line_no, f"FOR 迭代超过上限 {self.for_limit}，提前终止")
                return

            self.env[var_name] = v
            self._exec_block(lines, body_start, body_end, mode=mode)
            v += step

    def _find_matching_next(
        self,
        lines: list[tuple[int, str]],
        for_idx: int,
        end: int,
    ) -> int | None:
        depth = 0
        for i in range(for_idx, end):
            _, line = lines[i]
            if re.match(r"^FOR\b", line, re.IGNORECASE):
                depth += 1
            elif re.match(r"^NEXT\b", line, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _handle_transform(self, line: str, line_no: int) -> bool:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b\s*(.*)$", line)
        if not m:
            return False

        cmd = m.group(1).upper()
        arg_text = (m.group(2) or "").strip()

        if cmd in {"ADD", "ADDX", "ADDY", "ADDZ"}:
            args = _split_args(arg_text)
            vals = [self._eval_expr(a, line_no) for a in args] if args else []
            if any(v is None for v in vals):
                self._warn(line_no, f"{cmd} 参数解析失败，已跳过")
                return True

            dx = dy = dz = 0.0
            if cmd == "ADD":
                if len(vals) < 2:
                    self._warn(line_no, "ADD 需要至少 x,y 参数，已跳过")
                    return True
                dx = float(vals[0] or 0.0)
                dy = float(vals[1] or 0.0)
                dz = float(vals[2] or 0.0) if len(vals) >= 3 else 0.0
            elif cmd == "ADDX":
                if not vals:
                    self._warn(line_no, "ADDX 缺少参数，已跳过")
                    return True
                dx = float(vals[0] or 0.0)
            elif cmd == "ADDY":
                if not vals:
                    self._warn(line_no, "ADDY 缺少参数，已跳过")
                    return True
                dy = float(vals[0] or 0.0)
            elif cmd == "ADDZ":
                if not vals:
                    self._warn(line_no, "ADDZ 缺少参数，已跳过")
                    return True
                dz = float(vals[0] or 0.0)

            self._add_stack.append((dx, dy, dz))
            self._tx += dx
            self._ty += dy
            self._tz += dz
            return True

        if cmd == "DEL":
            args = _split_args(arg_text)
            if not args:
                del_count = 1
            else:
                val = self._eval_expr(args[0], line_no)
                if val is None:
                    self._warn(line_no, "DEL 参数解析失败，按 1 处理")
                    del_count = 1
                else:
                    del_count = max(1, int(round(float(val))))

            if del_count > len(self._add_stack):
                self._warn(
                    line_no,
                    f"DEL {del_count} 超过栈深 {len(self._add_stack)}，已自动清空",
                )
                del_count = len(self._add_stack)

            for _ in range(del_count):
                dx, dy, dz = self._add_stack.pop()
                self._tx -= dx
                self._ty -= dy
                self._tz -= dz
            return True

        return False

    def _handle_2d(self, line: str, line_no: int) -> bool:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b\s*(.*)$", line)
        if not m:
            return False

        cmd = m.group(1).upper()
        args_text = (m.group(2) or "").strip()
        args_raw = _split_args(args_text)

        if cmd == "LINE2":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 4:
                self._warn(line_no, "LINE2 参数不足或解析失败")
                return True
            p1 = self._p2(vals[0], vals[1])
            p2 = self._p2(vals[2], vals[3])
            self.result_2d.lines.append((p1, p2))
            return True

        if cmd == "RECT2":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 4:
                self._warn(line_no, "RECT2 参数不足或解析失败")
                return True
            x1, y1, x2, y2 = vals[:4]
            poly = [
                self._p2(x1, y1),
                self._p2(x2, y1),
                self._p2(x2, y2),
                self._p2(x1, y2),
            ]
            self.result_2d.polygons.append(poly)
            return True

        if cmd == "POLY2":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 3:
                self._warn(line_no, "POLY2 参数不足或解析失败")
                return True
            n = int(round(vals[0]))
            if n <= 0:
                self._warn(line_no, "POLY2 顶点数必须 > 0")
                return True

            rest = vals[1:]
            # Common POLY2 includes a mask after vertex count.
            data = rest[1:] if len(rest) >= (2 * n + 1) else rest
            pts = _extract_points_2d(data, n)
            if not pts:
                self._warn(line_no, "POLY2 顶点数据不足，已跳过")
                return True
            self.result_2d.polygons.append([self._p2(x, y) for x, y in pts])
            return True

        if cmd == "CIRCLE2":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 3:
                self._warn(line_no, "CIRCLE2 参数不足或解析失败")
                return True
            cx, cy = self._p2(vals[0], vals[1])
            r = abs(float(vals[2]))
            self.result_2d.circles.append((cx, cy, r))
            return True

        if cmd == "ARC2":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 5:
                self._warn(line_no, "ARC2 参数不足或解析失败")
                return True
            cx, cy = self._p2(vals[0], vals[1])
            r = abs(float(vals[2]))
            a0, a1 = float(vals[3]), float(vals[4])
            self.result_2d.arcs.append((cx, cy, r, a0, a1))
            return True

        if cmd == "PROJECT2":
            self._warn(line_no, "PROJECT2 暂为占位预览（未实现真实投影）")
            return True

        return False

    def _handle_3d(self, line: str, line_no: int) -> bool:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b\s*(.*)$", line)
        if not m:
            return False

        cmd = m.group(1).upper()
        args_text = (m.group(2) or "").strip()
        args_raw = _split_args(args_text)

        if cmd in {"BLOCK", "BRICK"}:
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 3:
                self._warn(line_no, f"{cmd} 参数不足或解析失败")
                return True
            mesh, wires = _make_box_mesh(vals[0], vals[1], vals[2], self._offset())
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        if cmd == "CYLIND":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 2:
                self._warn(line_no, "CYLIND 参数不足或解析失败")
                return True
            h = float(vals[0])
            r = abs(float(vals[1]))
            if r <= 1e-9 or abs(h) <= 1e-9:
                self._warn(line_no, "CYLIND 半径或高度为 0，已跳过")
                return True
            mesh, wires = _make_frustum_mesh(h, r, r, self._offset(), name="CYLIND")
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        if cmd == "CONE":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 3:
                self._warn(line_no, "CONE 参数不足或解析失败")
                return True
            h = float(vals[0])
            r1 = abs(float(vals[1]))
            r2 = abs(float(vals[2]))
            if abs(h) <= 1e-9 or (r1 <= 1e-9 and r2 <= 1e-9):
                self._warn(line_no, "CONE 几何退化，已跳过")
                return True
            mesh, wires = _make_frustum_mesh(h, r1, r2, self._offset(), name="CONE")
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        if cmd == "SPHERE":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 1:
                self._warn(line_no, "SPHERE 参数不足或解析失败")
                return True
            r = abs(float(vals[0]))
            if r <= 1e-9:
                self._warn(line_no, "SPHERE 半径为 0，已跳过")
                return True
            mesh, wires = _make_sphere_mesh(r, self._offset())
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        if cmd == "PRISM_":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 4:
                self._warn(line_no, "PRISM_ 参数不足或解析失败")
                return True

            n = int(round(vals[0]))
            h = float(vals[1])
            if n <= 2:
                self._warn(line_no, "PRISM_ 顶点数必须 >= 3")
                return True

            pts = _extract_points_2d(vals[2:], n)
            if not pts:
                self._warn(line_no, "PRISM_ 顶点数据不足，已跳过")
                return True

            mesh, wires = _make_prism_mesh(pts, h, self._offset())
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        return False

    def _eval_args(self, args_raw: list[str], line_no: int) -> list[float] | None:
        vals: list[float] = []
        for arg in args_raw:
            if not arg:
                continue
            v = self._eval_expr(arg, line_no)
            if v is None:
                return None
            vals.append(float(v))
        return vals

    def _eval_expr(self, expr: str | None, line_no: int) -> float | None:
        if expr is None:
            return None
        text = expr.strip()
        if not text:
            self._warn(line_no, "空表达式")
            return None
        try:
            return _safe_eval_expr(text, self.env)
        except Exception as exc:
            self._warn(line_no, f"表达式解析失败 `{text}`: {exc}")
            return None

    def _offset(self) -> Point3D:
        return (self._tx, self._ty, self._tz)

    def _p2(self, x: float, y: float) -> Point2D:
        return (float(x) + self._tx, float(y) + self._ty)

    def _warn(self, line_no: int, msg: str) -> None:
        if line_no > 0:
            self._warnings.append(f"line {line_no}: {msg}")
        else:
            self._warnings.append(msg)


def _normalize_parameters(parameters: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in parameters.items():
        name = str(k).upper()
        try:
            out[name] = float(v)
        except (TypeError, ValueError):
            # Non-numeric values are ignored for MVP numeric preview.
            continue
    return out


def _logical_lines(script: str) -> list[tuple[int, str]]:
    """Convert physical lines to logical lines (simple comma continuation)."""
    out: list[tuple[int, str]] = []
    buf = ""
    start_line = 0

    for line_no, raw in enumerate((script or "").splitlines(), start=1):
        code = raw.split("!", 1)[0].strip()
        if not code:
            continue

        if buf:
            buf += " " + code
        else:
            buf = code
            start_line = line_no

        if code.endswith(","):
            continue

        out.append((start_line, buf.strip()))
        buf = ""

    if buf:
        out.append((start_line, buf.strip()))

    return out


def _is_label_line(line: str) -> bool:
    if re.match(r'^\d+\s*:', line):
        return True
    if re.match(r'^"[^"]+"\s*:', line):
        return True
    return False


def _extract_command(line: str) -> str:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b", line)
    return m.group(1).upper() if m else ""


def _split_args(text: str) -> list[str]:
    if not text:
        return []
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            cur.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        args.append(tail)
    return args


def _extract_points_2d(values: list[float], n: int) -> list[Point2D] | None:
    if n <= 0:
        return None

    # Prefer triplets for PRISM_/POLY2 variants with edge-status:
    # n, h, s1,x1,y1, s2,x2,y2, ...
    if len(values) >= 3 * n:
        pairs = [(float(values[3 * i + 1]), float(values[3 * i + 2])) for i in range(n)]
        return pairs

    # Fallback to plain x,y pairs.
    if len(values) >= 2 * n:
        pairs = [(float(values[2 * i]), float(values[2 * i + 1])) for i in range(n)]
        return pairs

    return None


def _build_mesh(name: str, vertices: list[Point3D], faces: list[tuple[int, int, int]]) -> PreviewMesh3D:
    return PreviewMesh3D(
        name=name,
        x=[v[0] for v in vertices],
        y=[v[1] for v in vertices],
        z=[v[2] for v in vertices],
        i=[f[0] for f in faces],
        j=[f[1] for f in faces],
        k=[f[2] for f in faces],
    )


def _make_box_mesh(dx: float, dy: float, dz: float, offset: Point3D) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    x0, y0, z0 = offset
    x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz

    verts: list[Point3D] = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]

    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 5, 1), (0, 4, 5),
        (1, 6, 2), (1, 5, 6),
        (2, 7, 3), (2, 6, 7),
        (3, 4, 0), (3, 7, 4),
    ]

    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    wires = [[verts[a], verts[b]] for a, b in edges]

    return _build_mesh("BLOCK", verts, faces), wires


def _make_frustum_mesh(
    h: float,
    r1: float,
    r2: float,
    offset: Point3D,
    name: str,
    seg: int = 24,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    x0, y0, z0 = offset

    verts: list[Point3D] = []
    for t in range(seg):
        a = 2.0 * math.pi * t / seg
        verts.append((x0 + r1 * math.cos(a), y0 + r1 * math.sin(a), z0))
    for t in range(seg):
        a = 2.0 * math.pi * t / seg
        verts.append((x0 + r2 * math.cos(a), y0 + r2 * math.sin(a), z0 + h))

    base_center_idx = len(verts)
    verts.append((x0, y0, z0))
    top_center_idx = len(verts)
    verts.append((x0, y0, z0 + h))

    faces: list[tuple[int, int, int]] = []

    # Side faces
    for t in range(seg):
        n = (t + 1) % seg
        b1, b2 = t, n
        t1, t2 = seg + t, seg + n
        faces.append((b1, b2, t2))
        faces.append((b1, t2, t1))

    # Caps
    if r1 > 1e-9:
        for t in range(seg):
            n = (t + 1) % seg
            faces.append((base_center_idx, n, t))
    if r2 > 1e-9:
        for t in range(seg):
            n = (t + 1) % seg
            faces.append((top_center_idx, seg + t, seg + n))

    wires: list[list[Point3D]] = []
    base_loop = [verts[t] for t in range(seg)] + [verts[0]]
    top_loop = [verts[seg + t] for t in range(seg)] + [verts[seg]]
    wires.append(base_loop)
    wires.append(top_loop)
    for t in range(0, seg, max(1, seg // 8)):
        wires.append([verts[t], verts[seg + t]])

    return _build_mesh(name, verts, faces), wires


def _make_sphere_mesh(
    r: float,
    offset: Point3D,
    lat_steps: int = 10,
    lon_steps: int = 20,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    x0, y0, z0 = offset
    verts: list[Point3D] = []

    for la in range(lat_steps + 1):
        phi = -math.pi / 2.0 + math.pi * la / lat_steps
        cp = math.cos(phi)
        sp = math.sin(phi)
        for lo in range(lon_steps):
            th = 2.0 * math.pi * lo / lon_steps
            verts.append((
                x0 + r * cp * math.cos(th),
                y0 + r * cp * math.sin(th),
                z0 + r * sp,
            ))

    def vid(la: int, lo: int) -> int:
        return la * lon_steps + (lo % lon_steps)

    faces: list[tuple[int, int, int]] = []
    for la in range(lat_steps):
        for lo in range(lon_steps):
            a = vid(la, lo)
            b = vid(la, lo + 1)
            c = vid(la + 1, lo + 1)
            d = vid(la + 1, lo)
            faces.append((a, b, c))
            faces.append((a, c, d))

    wires: list[list[Point3D]] = []
    equator = [
        (x0 + r * math.cos(2 * math.pi * t / lon_steps),
         y0 + r * math.sin(2 * math.pi * t / lon_steps),
         z0)
        for t in range(lon_steps)
    ]
    wires.append(equator + [equator[0]])

    return _build_mesh("SPHERE", verts, faces), wires


def _make_prism_mesh(
    points: list[Point2D],
    h: float,
    offset: Point3D,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    x0, y0, z0 = offset
    n = len(points)

    base: list[Point3D] = [(x0 + x, y0 + y, z0) for x, y in points]
    top: list[Point3D] = [(x0 + x, y0 + y, z0 + h) for x, y in points]
    verts = [*base, *top]

    faces: list[tuple[int, int, int]] = []

    # Side faces
    for i in range(n):
        j = (i + 1) % n
        bi, bj = i, j
        ti, tj = n + i, n + j
        faces.append((bi, bj, tj))
        faces.append((bi, tj, ti))

    # Bottom fan
    for i in range(1, n - 1):
        faces.append((0, i + 1, i))

    # Top fan
    for i in range(1, n - 1):
        faces.append((n, n + i, n + i + 1))

    wires: list[list[Point3D]] = []
    wires.append(base + [base[0]])
    wires.append(top + [top[0]])
    for i in range(n):
        wires.append([base[i], top[i]])

    return _build_mesh("PRISM_", verts, faces), wires


_ALLOWED_FUNCS = {
    "ABS": lambda x: abs(x),
    "SQRT": lambda x: math.sqrt(x),
    "SIN": lambda x: math.sin(math.radians(x)),
    "COS": lambda x: math.cos(math.radians(x)),
    "TAN": lambda x: math.tan(math.radians(x)),
    "INT": lambda x: float(int(x)),
    "ROUND": lambda x: float(round(x)),
    "MIN": lambda *x: min(x),
    "MAX": lambda *x: max(x),
}


def _safe_eval_expr(expr: str, env: dict[str, float]) -> float:
    """Evaluate numeric expression with a very small safe AST subset."""
    text = expr.strip().replace("^", "**")
    node = ast.parse(text, mode="eval")
    return float(_eval_ast(node.body, env))


def _eval_ast(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return 1.0 if node.value else 0.0
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("常量类型不支持")

    if isinstance(node, ast.Name):
        key = node.id.upper()
        if key not in env:
            raise ValueError(f"未定义变量 {node.id}")
        return float(env[key])

    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left, env)
        right = _eval_ast(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
        if isinstance(node.op, ast.Mod):
            return left % right
        raise ValueError("二元运算符不支持")

    if isinstance(node, ast.UnaryOp):
        v = _eval_ast(node.operand, env)
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.USub):
            return -v
        raise ValueError("一元运算符不支持")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("函数调用不支持")
        fname = node.func.id.upper()
        fn = _ALLOWED_FUNCS.get(fname)
        if fn is None:
            raise ValueError(f"函数 {node.func.id} 不支持")
        args = [_eval_ast(a, env) for a in node.args]
        return float(fn(*args))

    raise ValueError("表达式语法不支持")
