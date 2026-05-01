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
class PreviewSourceRef:
    script_type: str
    line: int
    command: str
    label: str


@dataclass
class PreviewMesh3D:
    name: str
    x: list[float]
    y: list[float]
    z: list[float]
    i: list[int]
    j: list[int]
    k: list[int]
    source_ref: PreviewSourceRef | None = None


@dataclass
class PreviewWarning:
    line: int
    command: str
    message: str
    level: str = "warning"
    code: str = "PREVIEW_WARN"


@dataclass
class Preview2DResult:
    lines: list[tuple[Point2D, Point2D]] = field(default_factory=list)
    polygons: list[list[Point2D]] = field(default_factory=list)
    circles: list[tuple[float, float, float]] = field(default_factory=list)  # cx, cy, r
    arcs: list[tuple[float, float, float, float, float]] = field(default_factory=list)  # cx, cy, r, a0, a1
    warnings: list[str] = field(default_factory=list)
    warnings_structured: list[PreviewWarning] = field(default_factory=list)


@dataclass
class Preview3DResult:
    meshes: list[PreviewMesh3D] = field(default_factory=list)
    wires: list[list[Point3D]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    warnings_structured: list[PreviewWarning] = field(default_factory=list)


@dataclass
class PreviewResult:
    preview_2d: Preview2DResult
    preview_3d: Preview3DResult
    warnings: list[str] = field(default_factory=list)


def preview_2d_script(
    script_2d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
) -> Preview2DResult:
    """Preview a 2D GDL script using MVP command subset."""
    runtime = _PreviewRuntime(
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
    )
    runtime.execute(script_2d or "", mode="2d")
    runtime.finish()
    return runtime.result_2d


def preview_3d_script(
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
) -> Preview3DResult:
    """Preview a 3D GDL script using MVP command subset."""
    runtime = _PreviewRuntime(
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
    )
    runtime.execute(script_3d or "", mode="3d")
    runtime.finish()
    return runtime.result_3d


def preview_scripts(
    script_2d: str,
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
) -> PreviewResult:
    """Preview both 2D and 3D scripts and merge warnings."""
    p2d = preview_2d_script(
        script_2d,
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
    )
    p3d = preview_3d_script(
        script_3d,
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
    )
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

    def __init__(
        self,
        parameters: dict[str, Any] | None,
        for_limit: int,
        strict: bool = False,
        unknown_command_policy: str = "warn",
        quality: str = "fast",
    ):
        self.env = _normalize_parameters(parameters or {})
        self.for_limit = max(1, int(for_limit))
        self.loop_iterations = 0
        self.strict = bool(strict)
        self.unknown_command_policy = (unknown_command_policy or "warn").strip().lower()
        if self.unknown_command_policy not in {"warn", "ignore", "error"}:
            self.unknown_command_policy = "warn"
        self.quality = (quality or "fast").strip().lower()
        if self.quality not in {"fast", "accurate"}:
            self.quality = "fast"

        self._transform_stack: list[tuple[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]], tuple[float, float, float]]] = []
        self._A = _identity3()
        self._t = (0.0, 0.0, 0.0)

        # Mesh topology state (VERT/VECT/EDGE/PGON/BODY)
        self._verts: list[Point3D] = []
        self._vects: list[Point3D] = []
        self._edges: list[tuple[int, int]] = []  # (p1, p2) 0-based vertex indices
        self._pgons: list[list[int]] = []  # each is a list of signed edge IDs

        self.result_2d = Preview2DResult()
        self.result_3d = Preview3DResult()
        self._warnings: list[str] = []
        self._warnings_structured: list[PreviewWarning] = []

    def execute(self, script: str, mode: str) -> None:
        lines = _logical_lines(script)
        self._exec_block(lines, 0, len(lines), mode=mode)

    def finish(self) -> None:
        if self._transform_stack:
            self._warn(0, f"ADD/DEL 栈未平衡，自动收敛 DEL {len(self._transform_stack)}")
            self._transform_stack.clear()
            self._A = _identity3()
            self._t = (0.0, 0.0, 0.0)

        self.result_2d.warnings.extend(self._warnings)
        self.result_3d.warnings.extend(self._warnings)
        self.result_2d.warnings_structured.extend(self._warnings_structured)
        self.result_3d.warnings_structured.extend(self._warnings_structured)

    def _exec_block(self, lines: list[tuple[int, str]], start: int, end: int, mode: str) -> None:
        idx = start
        while idx < end:
            line_no, line = lines[idx]
            if _is_label_line(line):
                idx += 1
                continue

            # IF/ENDIF block — skip without evaluating condition
            if re.match(r"^IF\b", line, re.IGNORECASE):
                endif_idx = self._find_matching_endif(lines, idx, end)
                if endif_idx is None:
                    self._warn(line_no, "IF 缺少匹配 ENDIF，已跳过")
                    idx += 1
                    continue
                idx = endif_idx + 1
                continue

            if re.match(r"^(ENDIF|ELSE|ELSIF)\b", line, re.IGNORECASE):
                # Consumed by IF matching above — warn if stray
                if _extract_command(line) == "ENDIF":
                    self._warn(line_no, "遇到游离 ENDIF，已忽略")
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

            # Recognized but non-renderable commands — suppress "未支持命令" warning
            if re.match(
                r"^(RESOL|MATERIAL|PEN|XFORM)\b",
                line, re.IGNORECASE,
            ):
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
                    self._handle_unknown_command(line_no, cmd)
                else:
                    self._warn(line_no, "无法解析语句，已跳过", command="", code="PARSE_FAIL")

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

    def _find_matching_endif(
        self,
        lines: list[tuple[int, str]],
        if_idx: int,
        end: int,
    ) -> int | None:
        """Find ENDIF matching the IF at if_idx (handles nesting). Returns line index or None."""
        depth = 1
        for i in range(if_idx + 1, end):
            _, line = lines[i]
            cmd = _extract_command(line)
            if cmd == "IF":
                depth += 1
            elif cmd == "ENDIF":
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
        args = _split_args(arg_text)
        vals = [self._eval_expr(a, line_no) for a in args] if args else []

        if cmd in {"ADD", "ADDX", "ADDY", "ADDZ"}:
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

            v = (dx, dy, dz)
            next_t = _v_add(_m_mul_v(self._A, v), self._t)
            self._push_transform(self._A, next_t)
            return True

        if cmd in {"ROTX", "ROTY", "ROTZ", "ROT"}:
            if not vals:
                self._warn(line_no, f"{cmd} 缺少角度参数，已跳过")
                return True
            deg = float(vals[0] or 0.0)
            if cmd == "ROTX":
                M = _rot_x_deg(deg)
            elif cmd == "ROTY":
                M = _rot_y_deg(deg)
            else:
                M = _rot_z_deg(deg)
            self._push_transform(_m_mul(M, self._A), self._t)
            return True

        if cmd in {"MUL", "MULX", "MULY", "MULZ"}:
            if any(v is None for v in vals):
                self._warn(line_no, f"{cmd} 参数解析失败，已跳过")
                return True

            sx = sy = sz = 1.0
            if cmd == "MUL":
                if len(vals) == 1:
                    sx = sy = sz = float(vals[0] or 1.0)
                elif len(vals) >= 3:
                    sx = float(vals[0] or 1.0)
                    sy = float(vals[1] or 1.0)
                    sz = float(vals[2] or 1.0)
                else:
                    self._warn(line_no, "MUL 参数需 1 或 3 个，已跳过")
                    return True
            elif cmd == "MULX":
                if not vals:
                    self._warn(line_no, "MULX 缺少参数，已跳过")
                    return True
                sx = float(vals[0] or 1.0)
            elif cmd == "MULY":
                if not vals:
                    self._warn(line_no, "MULY 缺少参数，已跳过")
                    return True
                sy = float(vals[0] or 1.0)
            elif cmd == "MULZ":
                if not vals:
                    self._warn(line_no, "MULZ 缺少参数，已跳过")
                    return True
                sz = float(vals[0] or 1.0)

            M = ((sx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, sz))
            self._push_transform(_m_mul(M, self._A), self._t)
            return True

        if cmd == "DEL":
            if not args:
                del_count = 1
            else:
                val = self._eval_expr(args[0], line_no)
                if val is None:
                    self._warn(line_no, "DEL 参数解析失败，按 1 处理")
                    del_count = 1
                else:
                    del_count = max(1, int(round(float(val))))

            if del_count > len(self._transform_stack):
                self._warn(
                    line_no,
                    f"DEL {del_count} 超过栈深 {len(self._transform_stack)}，已自动清空",
                )
                del_count = len(self._transform_stack)

            for _ in range(del_count):
                prev_A, prev_t = self._transform_stack.pop()
                self._A = prev_A
                self._t = prev_t
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
            mesh, wires = _make_box_mesh(
                vals[0],
                vals[1],
                vals[2],
                self._offset(),
                transform=self._A,
                source_ref=_source_ref_3d(line_no, cmd),
            )
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
            mesh, wires = _make_frustum_mesh(
                h,
                r,
                r,
                self._offset(),
                name="CYLIND",
                seg=_quality_frustum_seg(self.quality),
                transform=self._A,
                source_ref=_source_ref_3d(line_no, cmd),
            )
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
            mesh, wires = _make_frustum_mesh(
                h,
                r1,
                r2,
                self._offset(),
                name="CONE",
                seg=_quality_frustum_seg(self.quality),
                transform=self._A,
                source_ref=_source_ref_3d(line_no, cmd),
            )
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
            mesh, wires = _make_sphere_mesh(
                r,
                self._offset(),
                lat_steps=_quality_sphere_steps(self.quality)[0],
                lon_steps=_quality_sphere_steps(self.quality)[1],
                transform=self._A,
                source_ref=_source_ref_3d(line_no, cmd),
            )
            self.result_3d.meshes.append(mesh)
            self.result_3d.wires.extend(wires)
            return True

        # ── low-level mesh: VERT / VECT / EDGE / PGON / BODY ──────────────
        if cmd == "VERT":
            vals = self._eval_args(args_raw, line_no)
            if vals is not None and len(vals) >= 3:
                ox, oy, oz = self._offset()
                self._verts.append((ox + float(vals[0]), oy + float(vals[1]), oz + float(vals[2])))
            return True

        if cmd == "VECT":
            vals = self._eval_args(args_raw, line_no)
            if vals is not None and len(vals) >= 3:
                self._vects.append((float(vals[0]), float(vals[1]), float(vals[2])))
            return True

        if cmd == "EDGE":
            vals = self._eval_args(args_raw, line_no)
            if vals is not None and len(vals) >= 2:
                p1 = int(round(float(vals[0]))) - 1  # GDL is 1-based
                p2 = int(round(float(vals[1]))) - 1
                if 0 <= p1 < len(self._verts) and 0 <= p2 < len(self._verts):
                    self._edges.append((p1, p2))
                else:
                    self._warn(line_no, f"EDGE 顶点索引越界，已忽略")
            return True

        if cmd == "PGON":
            vals = self._eval_args(args_raw, line_no)
            if vals is not None and len(vals) >= 3:
                n_edges = int(round(float(vals[0])))
                if n_edges >= 3 and n_edges <= len(vals) - 2:
                    edge_ids: list[int] = []
                    for i in range(n_edges):
                        eid = int(round(float(vals[2 + i])))
                        if abs(eid) - 1 < len(self._edges):
                            edge_ids.append(eid)
                        else:
                            edge_ids.clear()
                            break
                    if edge_ids:
                        self._pgons.append(edge_ids)
            return True

        if cmd == "BODY":
            mesh = self._build_mesh_from_topology(line_no)
            if mesh is not None:
                self.result_3d.meshes.append(mesh)
            # Clear topology for next BODY (a script can have multiple bodies)
            self._verts.clear()
            self._vects.clear()
            self._edges.clear()
            self._pgons.clear()
            return True

        if cmd in {"PRISM", "PRISM_"}:
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 4:
                self._warn(line_no, f"{cmd} 参数不足或解析失败")
                return True

            n = int(round(vals[0]))
            h = float(vals[1])
            if n <= 2:
                self._warn(line_no, f"{cmd} 顶点数必须 >= 3")
                return True

            pts = _extract_points_2d(vals[2:], n)
            if not pts:
                self._warn(line_no, f"{cmd} 顶点数据不足，已跳过")
                return True

            mesh, wires = _make_prism_mesh(
                pts,
                h,
                self._offset(),
                transform=self._A,
                name=cmd,
                source_ref=_source_ref_3d(line_no, cmd),
            )
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
        return self._t

    def _p2(self, x: float, y: float) -> Point2D:
        ox, oy, _ = self._t
        return (float(x) + ox, float(y) + oy)

    def _push_transform(
        self,
        next_A: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
        next_t: tuple[float, float, float],
    ) -> None:
        self._transform_stack.append((self._A, self._t))
        self._A = next_A
        self._t = next_t

    # ── low-level mesh topology helpers ──────────────────────────────────

    def _resolve_vertex_chain(self, edge_ids: list[int]) -> list[int] | None:
        """Resolve signed edge IDs to an ordered chain of 0-based vertex indices."""
        segments: list[tuple[int, int]] = []
        for eid in edge_ids:
            idx = abs(eid) - 1
            if idx < 0 or idx >= len(self._edges):
                return None
            p1, p2 = self._edges[idx]
            segments.append((p2, p1) if eid < 0 else (p1, p2))

        chain = list(segments[0])
        used = {0}
        while len(chain) < len(segments) + 1:
            last = chain[-1]
            found = False
            for i, (s1, s2) in enumerate(segments):
                if i in used:
                    continue
                if s1 == last:
                    chain.append(s2)
                    used.add(i)
                    found = True
                    break
                if s2 == last:
                    chain.append(s1)
                    used.add(i)
                    found = True
                    break
            if not found:
                return None  # broken chain
        # A closed polygon chain ends where it starts — strip the repeated first vertex
        if len(chain) > 1 and chain[0] == chain[-1]:
            chain.pop()
        return chain

    def _build_mesh_from_topology(self, line_no: int) -> PreviewMesh3D | None:
        """Assemble a PreviewMesh3D from accumulated VERT/EDGE/PGON data."""
        if not self._verts or not self._pgons:
            return None

        faces: list[tuple[int, int, int]] = []
        skipped = 0
        for edge_ids in self._pgons:
            chain = self._resolve_vertex_chain(edge_ids)
            if chain is None or len(chain) < 3:
                skipped += 1
                continue
            # Fan triangulation from vertex 0
            for i in range(1, len(chain) - 1):
                faces.append((chain[0], chain[i], chain[i + 1]))

        if not faces:
            self._warn(line_no, f"BODY: {skipped} 个面跳过，无有效三角面")
            return None

        return _build_mesh("MESH", self._verts, faces)

    def _warn(
        self,
        line_no: int,
        msg: str,
        *,
        command: str = "",
        level: str = "warning",
        code: str = "PREVIEW_WARN",
    ) -> None:
        cmd = (command or "").upper()
        self._warnings_structured.append(
            PreviewWarning(line=line_no, command=cmd, message=msg, level=level, code=code)
        )
        if line_no > 0:
            self._warnings.append(f"line {line_no}: {msg}")
        else:
            self._warnings.append(msg)

    def _handle_unknown_command(self, line_no: int, command: str) -> None:
        cmd = (command or "").upper()
        if self.unknown_command_policy == "ignore":
            return

        msg = f"未支持命令 {cmd}，已跳过"
        if self.unknown_command_policy == "error":
            raise ValueError(f"line {line_no}: 未支持命令 {cmd}")

        self._warn(line_no, msg, command=cmd, code="UNKNOWN_COMMAND")


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


def _quality_profile(quality: str) -> dict[str, Any]:
    if quality == "accurate":
        return {
            "frustum_seg": 48,
            "sphere_steps": (20, 40),
        }
    return {
        "frustum_seg": 24,
        "sphere_steps": (10, 20),
    }


def _quality_frustum_seg(quality: str) -> int:
    return int(_quality_profile(quality)["frustum_seg"])


def _quality_sphere_steps(quality: str) -> tuple[int, int]:
    return tuple(_quality_profile(quality)["sphere_steps"])


def _identity3() -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _m_mul(
    a: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    b: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    return (
        (
            a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
            a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
        ),
        (
            a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
            a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
        ),
        (
            a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
            a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
            a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
        ),
    )


def _m_mul_v(
    m: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    v: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _v_add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _rot_x_deg(deg: float) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _rot_y_deg(deg: float) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _rot_z_deg(deg: float) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _apply_affine(
    p: Point3D,
    A: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    t: Point3D,
) -> Point3D:
    x, y, z = _m_mul_v(A, p)
    return (x + t[0], y + t[1], z + t[2])


def _source_ref_3d(line_no: int, command: str) -> PreviewSourceRef:
    cmd = (command or "").upper()
    return PreviewSourceRef(
        script_type="3d",
        line=int(line_no),
        command=cmd,
        label=f"3D line {int(line_no)} {cmd}",
    )


def _build_mesh(
    name: str,
    vertices: list[Point3D],
    faces: list[tuple[int, int, int]],
    source_ref: PreviewSourceRef | None = None,
) -> PreviewMesh3D:
    return PreviewMesh3D(
        name=name,
        x=[v[0] for v in vertices],
        y=[v[1] for v in vertices],
        z=[v[2] for v in vertices],
        i=[f[0] for f in faces],
        j=[f[1] for f in faces],
        k=[f[2] for f in faces],
        source_ref=source_ref,
    )


def _make_box_mesh(
    dx: float,
    dy: float,
    dz: float,
    offset: Point3D,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    source_ref: PreviewSourceRef | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    A = transform or _identity3()

    verts_local: list[Point3D] = [
        (0.0, 0.0, 0.0),
        (dx, 0.0, 0.0),
        (dx, dy, 0.0),
        (0.0, dy, 0.0),
        (0.0, 0.0, dz),
        (dx, 0.0, dz),
        (dx, dy, dz),
        (0.0, dy, dz),
    ]
    verts = [_apply_affine(p, A, offset) for p in verts_local]

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

    return _build_mesh("BLOCK", verts, faces, source_ref=source_ref), wires


def _make_frustum_mesh(
    h: float,
    r1: float,
    r2: float,
    offset: Point3D,
    name: str,
    seg: int = 24,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    source_ref: PreviewSourceRef | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    A = transform or _identity3()

    verts_local: list[Point3D] = []
    for t in range(seg):
        a = 2.0 * math.pi * t / seg
        verts_local.append((r1 * math.cos(a), r1 * math.sin(a), 0.0))
    for t in range(seg):
        a = 2.0 * math.pi * t / seg
        verts_local.append((r2 * math.cos(a), r2 * math.sin(a), h))

    base_center_idx = len(verts_local)
    verts_local.append((0.0, 0.0, 0.0))
    top_center_idx = len(verts_local)
    verts_local.append((0.0, 0.0, h))

    verts = [_apply_affine(p, A, offset) for p in verts_local]

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

    return _build_mesh(name, verts, faces, source_ref=source_ref), wires


def _make_sphere_mesh(
    r: float,
    offset: Point3D,
    lat_steps: int = 10,
    lon_steps: int = 20,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    source_ref: PreviewSourceRef | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    A = transform or _identity3()
    verts: list[Point3D] = []

    for la in range(lat_steps + 1):
        phi = -math.pi / 2.0 + math.pi * la / lat_steps
        cp = math.cos(phi)
        sp = math.sin(phi)
        for lo in range(lon_steps):
            th = 2.0 * math.pi * lo / lon_steps
            verts.append(_apply_affine((r * cp * math.cos(th), r * cp * math.sin(th), r * sp), A, offset))

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
        _apply_affine(
            (r * math.cos(2 * math.pi * t / lon_steps), r * math.sin(2 * math.pi * t / lon_steps), 0.0),
            A,
            offset,
        )
        for t in range(lon_steps)
    ]
    wires.append(equator + [equator[0]])

    return _build_mesh("SPHERE", verts, faces, source_ref=source_ref), wires


def _make_prism_mesh(
    points: list[Point2D],
    h: float,
    offset: Point3D,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    name: str = "PRISM_",
    source_ref: PreviewSourceRef | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    A = transform or _identity3()
    n = len(points)

    base: list[Point3D] = [_apply_affine((x, y, 0.0), A, offset) for x, y in points]
    top: list[Point3D] = [_apply_affine((x, y, h), A, offset) for x, y in points]
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

    return _build_mesh(name, verts, faces, source_ref=source_ref), wires


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
