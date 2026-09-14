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
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


DEFAULT_FOR_LIMIT = 5000
DEFAULT_WALL_CLOCK_LIMIT = 10.0  # seconds; wall-clock gate for FOR loops

# 2D 文本字号的名义出图比例（1:50）：GDL 文本是纸张 mm，Archicad 按出图
# 比例映射到模型空间；预览无比例上下文，取平面符号最常用的 1:50，使
# size_mm × 0.001 × 50 = 模型单位字高在 fit 视图下比例可读。
PREVIEW_TEXT_NOMINAL_SCALE = 50.0


Point2D = tuple[float, float]
Point3D = tuple[float, float, float]


@dataclass
class PreviewSourceRef:
    script_type: str
    line: int
    command: str
    label: str
    # P1e 相关代码段：生成该 mesh 的外围块区间（FOR…NEXT / IF…ENDIF / GOSUB
    # 子程序），行号含端点；顶层命令为单行 (line, line)。可选字段向后兼容。
    segment_start: int | None = None
    segment_end: int | None = None
    # P3（CALL 执行）：该 mesh 由宏产生的宏名链（"外层/内层"）；顶层脚本直接
    # 产生的 mesh 为 None。可选字段向后兼容。
    macro: str | None = None


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


# ── CALL 宏解析协议（P1/P3，GSM-CALL 研究 2026-09-12） ─────────────────────
# gdl_previewer 不 import library_context（保持无重依赖、可被 benchmark/语义
# 验证等离线路径复用）；解析器由调用方以 callable 注入，返回 MacroLookup。


@dataclass
class MacroLookup:
    """CALL 宏解析结果（openbrep.library_context.build_macro_resolver 的返回协议）。

    status:
      "resolved"  — 找到宏；guid/scripts/parameters/called_macros 已填充
      "missing"   — 已配置图库中没有该名称的宏
      "ambiguous" — 多个同名候选且 GUID 无法唯一区分（禁止静默选第一个）
      "error"     — 解析/转换过程出错（密码保护、转换器不可用、容器不可读等）
    message 为人类可读诊断（结构化 warning 直接透传）；source 为解析来源
    （图库根/容器路径），用于 "已解析自 …" 诊断。
    """

    status: str
    name: str
    guid: str | None = None
    message: str = ""
    source: str = ""
    # 宏默认参数（原始值，字符串/数字均可，预览器自行归一化）
    parameters: dict[str, Any] = field(default_factory=dict)
    # ScriptType.value → 脚本文本（"1d.gdl"/"2d.gdl"/"3d.gdl" 等）
    scripts: dict[str, str] = field(default_factory=dict)
    # 宏自身的 calledmacros 名称 → GUID 表（嵌套 CALL 的 GUID 提示）
    called_macros: dict[str, str] = field(default_factory=dict)


# 注入预览器的宏解析器协议：(宏名, GUID 提示) → MacroLookup
MacroResolver = Callable[[str, "str | None"], MacroLookup]


# ── CALL 执行闸门（P3）────────────────────────────────────────────────────
# 执行的是递归的不受信任脚本（图库宏），必须有限深/限次/限时三道闸门。
DEFAULT_CALL_DEPTH_LIMIT = 16   # 最大 CALL 嵌套深度
DEFAULT_CALL_BUDGET = 256       # 一次预览内 CALL 总次数预算（全 runtime 共享）


@dataclass
class _CallBudget:
    """CALL 执行共享预算：顶层 runtime 自建，child runtime 复用同一对象。

    start/limit 构成共享 wall-clock deadline（limit <= 0 关闭耗时闸门）；
    total_calls 为全链路的 CALL 总次数；warned_* 保证闸门警告只发一次。
    """

    start: float
    limit: float
    max_calls: int = DEFAULT_CALL_BUDGET
    total_calls: int = 0
    warned_budget: bool = False
    warned_timeout: bool = False


@dataclass
class PreviewText:
    """2D 文本项（TEXT2/RICHTEXT2 链）：位置已过 2D 变换，size 为模型单位。

    size = 样式字号 mm × 0.001 × PREVIEW_TEXT_NOMINAL_SCALE。GDL 文本是纸张
    尺寸，Archicad 按出图比例映射到模型空间；预览无比例上下文，取名义 1:50
    （平面符号的常用比例），使文本在 fit 视图下比例可读（取舍见 _handle_2d
    文本链注释）。
    """

    x: float
    y: float
    text: str
    size: float


@dataclass
class Preview2DResult:
    lines: list[tuple[Point2D, Point2D]] = field(default_factory=list)
    polygons: list[list[Point2D]] = field(default_factory=list)
    # 与 polygons 严格等长对齐：该多边形是否填充（frame_fill 的 j2）/
    # 是否画轮廓（j1）。所有产生 polygon 的 handler 必须同步 append。
    polygon_fills: list[bool] = field(default_factory=list)
    polygon_contours: list[bool] = field(default_factory=list)
    circles: list[tuple[float, float, float]] = field(default_factory=list)  # cx, cy, r
    arcs: list[tuple[float, float, float, float, float]] = field(default_factory=list)  # cx, cy, r, a0, a1
    texts: list[PreviewText] = field(default_factory=list)
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
    setup_script: str = "",
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
    script_3d: str | None = None,
    wall_clock_limit: float = DEFAULT_WALL_CLOCK_LIMIT,
    macro_resolver: MacroResolver | None = None,
    macro_guid_map: Mapping[str, str] | None = None,
) -> Preview2DResult:
    """Preview a 2D GDL script using MVP command subset.

    script_3d（可选，P3a）：PROJECT2 顶视图投影需要 3D 模型的执行结果。
    首次遇到 PROJECT2 时用同一组 parameters/setup/for_limit/quality/
    unknown_command_policy 起内部 runtime 执行 3D 脚本并缓存 meshes；
    不传 script_3d 时 PROJECT2 行为与 P3a 前逐字节一致（占位警告）。

    macro_resolver / macro_guid_map（可选，P3）：CALL 宏解析器与调用方脚本
    的 calledmacros 名称→GUID 提示表；不传时 CALL 只发 MACRO_NO_RESOLVER
    结构化诊断（P1），不产生几何。
    """
    runtime = _PreviewRuntime(
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
        setup_script=setup_script or "",
        script_3d=script_3d,
        wall_clock_limit=wall_clock_limit,
        macro_resolver=macro_resolver,
        macro_guid_map=macro_guid_map,
    )
    if setup_script:
        runtime.execute(setup_script or "", mode="setup")
    runtime.execute(script_2d or "", mode="2d")
    runtime.finish()
    return runtime.result_2d


def preview_3d_script(
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    setup_script: str = "",
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
    wall_clock_limit: float = DEFAULT_WALL_CLOCK_LIMIT,
    macro_resolver: MacroResolver | None = None,
    macro_guid_map: Mapping[str, str] | None = None,
) -> Preview3DResult:
    """Preview a 3D GDL script using MVP command subset.

    macro_resolver / macro_guid_map（可选，P3）：见 preview_2d_script。
    """
    runtime = _PreviewRuntime(
        parameters=parameters,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
        wall_clock_limit=wall_clock_limit,
        macro_resolver=macro_resolver,
        macro_guid_map=macro_guid_map,
    )
    if setup_script:
        runtime.execute(setup_script or "", mode="setup")
    runtime.execute(script_3d or "", mode="3d")
    runtime.finish()
    return runtime.result_3d


def preview_scripts(
    script_2d: str,
    script_3d: str,
    parameters: dict[str, Any] | None = None,
    setup_script: str = "",
    for_limit: int = DEFAULT_FOR_LIMIT,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
    wall_clock_limit: float = DEFAULT_WALL_CLOCK_LIMIT,
    macro_resolver: MacroResolver | None = None,
    macro_guid_map: Mapping[str, str] | None = None,
) -> PreviewResult:
    """Preview both 2D and 3D scripts and merge warnings."""
    p2d = preview_2d_script(
        script_2d,
        parameters=parameters,
        setup_script=setup_script,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
        script_3d=script_3d,
        wall_clock_limit=wall_clock_limit,
        macro_resolver=macro_resolver,
        macro_guid_map=macro_guid_map,
    )
    p3d = preview_3d_script(
        script_3d,
        parameters=parameters,
        setup_script=setup_script,
        for_limit=for_limit,
        strict=strict,
        unknown_command_policy=unknown_command_policy,
        quality=quality,
        wall_clock_limit=wall_clock_limit,
        macro_resolver=macro_resolver,
        macro_guid_map=macro_guid_map,
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
    _GET_USE_RE = re.compile(r"^(GET|USE)\s*\((.+)\)$", re.IGNORECASE)

    def __init__(
        self,
        parameters: dict[str, Any] | None,
        for_limit: int,
        strict: bool = False,
        unknown_command_policy: str = "warn",
        quality: str = "fast",
        setup_script: str = "",
        script_3d: str | None = None,
        wall_clock_limit: float = DEFAULT_WALL_CLOCK_LIMIT,
        macro_resolver: MacroResolver | None = None,
        macro_guid_map: Mapping[str, str] | None = None,
        _call_budget: "_CallBudget | None" = None,
        _macro_chain: tuple[str, ...] = (),
    ):
        self.env = _normalize_parameters(parameters or {})
        # P3a：PROJECT2 顶视图投影——内部 3D runtime 需要的 setup/3D 脚本
        # 与缓存。_script_3d 为 None 时 PROJECT2 保持占位警告（行为不变）。
        self._setup_script = setup_script or ""
        self._script_3d = script_3d
        self._project2_meshes: list[PreviewMesh3D] | None = None
        self._project2_method_warned = False
        self.for_limit = max(1, int(for_limit))
        # 双闸门：迭代上限（for_limit）+ wall-clock 上限（秒）。wall_clock_limit
        # <= 0 表示关闭耗时闸门（仅迭代闸门生效）。
        self.wall_clock_limit = max(0.0, float(wall_clock_limit))
        self.loop_iterations = 0
        self._start_time = time.monotonic()
        self.strict = bool(strict)
        self.unknown_command_policy = (unknown_command_policy or "warn").strip().lower()
        if self.unknown_command_policy not in {"warn", "ignore", "error"}:
            self.unknown_command_policy = "warn"
        self.quality = (quality or "fast").strip().lower()
        if self.quality not in {"fast", "accurate"}:
            self.quality = "fast"

        # P3（CALL 执行）：宏解析器 + 当前脚本的 calledmacros 名称→GUID 提示表。
        # _macro_chain 为当前 CALL 链上的宏名（不含顶层脚本），用于递归环检测
        # 与 source_ref.macro / 警告前缀的链式拼接；_call_budget 一次预览内全
        # runtime 共享（顶层自建，child 复用）。
        self._macro_resolver = macro_resolver
        self._macro_guid_map: dict[str, str] = dict(macro_guid_map or {})
        self._macro_chain: tuple[str, ...] = tuple(_macro_chain)
        self._call_budget = _call_budget or _CallBudget(
            start=time.monotonic(),
            limit=self.wall_clock_limit,
        )
        # CALL 诊断去重（同一 runtime 内同一 宏名+code 只警一次，避免循环刷屏）
        self._call_warned: set[tuple[str, str]] = set()

        # P4（2D 覆盖扩展）：文本链状态（样式表/段落/textblock 映射/当前样式）
        # 与 POLY2 系状态码告警去重。CALL child runtime 各自持有独立状态
        # （宏自带 define style），结果在 _handle_call 合并。
        self._styles: dict[str, dict[str, Any]] = {}
        self._paragraphs: dict[str, dict[str, Any]] = {}
        self._textblocks: dict[str, str] = {}
        self._current_style: str | None = None
        self._poly2_status_warned: set[int] = set()
        self._tangent_fallback_warned = False

        self._transform_stack: list[tuple[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]], tuple[float, float, float]]] = []
        self._A = _identity3()
        self._t = (0.0, 0.0, 0.0)

        # GROUP 布尔组（P14，预览近似）：命名组内容存储 + ADDGROUP 等运算结果
        # 的匿名组变量表 + GROUP/ENDGROUP 定义栈。组内的 mesh/wire 先进组存储，
        # PLACEGROUP 才把组内容（按当前变换）放进结果。不做真实 CSG：
        # ADDGROUP=并集，SUBGROUP(a,b)≈a，ISECTGROUP(a,b)≈b（详见
        # _handle_group_bool_assign）。
        self._groups: dict[str, dict[str, list]] = {}
        self._group_vars: dict[str, str] = {}
        self._group_stack: list[str] = []
        self._group_anon_seq = 0

        # Mesh topology state (VERT/VECT/EDGE/PGON/BODY)
        self._verts: list[Point3D] = []
        self._vects: list[Point3D] = []
        self._edges: list[tuple[int, int]] = []  # (p1, p2) 0-based vertex indices
        self._pgons: list[list[int]] = []  # each is a list of signed edge IDs

        # RULED chain welding state — consecutive RULED segments whose
        # base ring coincides with the previous segment's top ring are
        # merged into ONE mesh so vertex normals average smoothly across
        # the joint (no visible banding on lofts).
        self._ruled_chain_mesh: PreviewMesh3D | None = None
        self._ruled_chain_top: list[Point3D] | None = None
        self._ruled_chain_top_idx: list[int] | None = None

        # PUT/GET value stack and GOSUB call stack.
        self._put_stack: list[float] = []
        self._gosub_stack: list[int] = []
        self._label_map: dict[str, int] = {}

        # P1e 相关代码段追踪：_block_stack 存进入中的 IF/FOR 块区间
        # （(start_line, end_line)，含端点）；_subroutine_stack 与 _gosub_stack
        # 平行，存 GOSUB 目标的子程序区间（未知时为 None）。_source_ref_3d
        # 取"数值上包含命令行的最内层区间"作为 segment。
        self._block_stack: list[tuple[int, int]] = []
        self._subroutine_stack: list[tuple[int, int] | None] = []
        self._subroutine_extents: dict[int, tuple[int, int]] = {}

        # Function dispatch table for expression evaluation.
        self._funcs = dict(_ALLOWED_FUNCS)

        self.result_2d = Preview2DResult()
        self.result_3d = Preview3DResult()
        self._warnings: list[str] = []
        self._warnings_structured: list[PreviewWarning] = []

    def execute(self, script: str, mode: str) -> None:
        lines = _logical_lines(script)
        self._label_map = _build_label_map(lines)
        self._subroutine_extents = _build_subroutine_extents(lines)
        self._block_stack.clear()
        self._subroutine_stack.clear()
        self._exec_block(lines, 0, len(lines), mode=mode)

    def finish(self) -> None:
        if self._transform_stack:
            self._warn(0, f"ADD/DEL 栈未平衡，自动收敛 DEL {len(self._transform_stack)}")
            self._transform_stack.clear()
            self._A = _identity3()
            self._t = (0.0, 0.0, 0.0)

        if self._group_stack:
            self._warn(0, f"GROUP/ENDGROUP 未平衡，自动收敛 ENDGROUP {len(self._group_stack)}")
            self._group_stack.clear()

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

            inline_if = _extract_inline_if(line)
            if inline_if is not None:
                condition, statement = inline_if
                should_run = self._eval_condition(condition, line_no)
                # P14：单行 IF…THEN…ELSE…（GDL 允许同行 ELSE）：顶层 ELSE
                # 拆分，THEN 语句由条件门控，ELSE 语句仅在条件为假时执行。
                else_statement = None
                else_split = _split_inline_else(statement)
                if else_split is not None:
                    statement, else_statement = else_split
                # P13：Archicad 语义对齐——单行 IF 同行只允许一条条件语句
                # （GDL Reference Guide AC23 p323）；`:` 后的语句无条件执行，
                # 由 _exec_inline_statement 的 condition_true 门控首句。
                self._exec_inline_statement(
                    statement, line_no, lines, idx, mode, condition_true=should_run
                )
                if else_statement is not None and should_run is False:
                    self._exec_inline_statement(
                        else_statement, line_no, lines, idx, mode, condition_true=True
                    )
                idx += 1
                continue

            # IF/ENDIF block
            if re.match(r"^IF\b", line, re.IGNORECASE):
                else_idx, endif_idx = self._find_matching_if_bounds(lines, idx, end)
                if endif_idx is None:
                    self._warn(line_no, "IF 缺少匹配 ENDIF，已跳过")
                    idx += 1
                    continue
                condition = _extract_if_condition(line)
                if condition is None:
                    self._warn(line_no, "IF 条件无法解析，已跳过")
                    idx = endif_idx + 1
                    continue
                should_run = self._eval_condition(condition, line_no)
                if should_run is None:
                    idx = endif_idx + 1
                    continue
                # P1e：IF…ENDIF 整块（含 ELSE 段）作为段区间
                if_block = (lines[idx][0], lines[endif_idx][0])
                self._block_stack.append(if_block)
                try:
                    if should_run:
                        body_end = else_idx if else_idx is not None else endif_idx
                        self._exec_block(lines, idx + 1, body_end, mode=mode)
                    elif else_idx is not None:
                        self._exec_block(lines, else_idx + 1, endif_idx, mode=mode)
                finally:
                    self._block_stack.pop()
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
                # DIM 数组声明（GDL 数组 1-based；重复声明即重置）
                m_dim = re.match(r"^DIM\s+(.+)$", line, re.IGNORECASE)
                if m_dim:
                    self._handle_dim(m_dim.group(1), line_no)
                    idx += 1
                    continue
                # GROUP 布尔运算赋值：v = ADDGROUP/SUBGROUP/ISECTGROUP(...)
                m_group_assign = re.match(
                    r"^([A-Za-z_]\w*)\s*=\s*(ADDGROUP|SUBGROUP|ISECTGROUP)\s*\((.*)\)\s*$",
                    line,
                    re.IGNORECASE,
                )
                if m_group_assign:
                    self._handle_group_bool_assign(
                        m_group_assign.group(1),
                        m_group_assign.group(2).upper(),
                        m_group_assign.group(3),
                        line_no,
                    )
                    idx += 1
                    continue
                # 数组元素赋值：name[i] = expr / name[i][j] = expr
                m_array_assign = re.match(
                    r"^([A-Za-z_]\w*)\s*((?:\[[^\]]*\])+)\s*=\s*(.+)$", line
                )
                if m_array_assign:
                    self._handle_array_assign(
                        m_array_assign.group(1),
                        m_array_assign.group(2),
                        m_array_assign.group(3),
                        line_no,
                    )
                    idx += 1
                    continue
                m_assign = self._ASSIGN_RE.match(line)
                if m_assign:
                    name = m_assign.group(1)
                    expr = m_assign.group(2)
                    value = self._eval_any(expr, line_no)
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

                # P1e：FOR…NEXT 整块（含头尾）作为段区间
                for_block = (lines[idx][0], lines[next_idx][0])
                self._block_stack.append(for_block)
                try:
                    self._execute_for(line, line_no, lines, idx + 1, next_idx, mode)
                finally:
                    self._block_stack.pop()
                idx = next_idx + 1
                continue

            if re.match(r"^NEXT\b", line, re.IGNORECASE):
                # Should only be consumed by _find_matching_next scope.
                self._warn(line_no, "遇到游离 NEXT，已忽略")
                idx += 1
                continue

            # GOSUB/RETURN subroutine calls
            if re.match(r"^GOSUB\b", line, re.IGNORECASE):
                target = self._resolve_gosub_target(line[5:], line_no)
                if target is None:
                    self._warn(line_no, "GOSUB 目标标签未找到，已跳过")
                    idx += 1
                elif target < start or target >= end:
                    # P10：目标落在当前块区间 [start, end) 之外（典型：FOR 体内
                    # GOSUB 跳到脚本尾部的子程序）。复用 P9 单行 IF 的成熟做法
                    # —— 压 None 哨兵 + 子程序 extent，在完整脚本上下文中从标签
                    # 执行到 RETURN（RETURN 消费 None 哨兵 break），返回后继续
                    # 本块下一条语句。子程序真实执行、GOSUB 栈不泄漏。
                    self._gosub_stack.append(None)
                    self._subroutine_stack.append(self._subroutine_extents.get(lines[target][0]))
                    self._exec_block(lines, target, len(lines), mode=mode)
                    idx += 1
                else:
                    # 目标在块区间之内：压返回点原地跳转（既有行为保持不变）。
                    self._gosub_stack.append(idx + 1)
                    # P1e：GOSUB 子程序区间（标签到 RETURN），未知时 None
                    self._subroutine_stack.append(self._subroutine_extents.get(lines[target][0]))
                    idx = target
                continue

            if re.match(r"^RETURN\b", line, re.IGNORECASE):
                if not self._gosub_stack:
                    self._warn(line_no, "RETURN 没有对应 GOSUB，已忽略")
                    idx += 1
                else:
                    ret = self._gosub_stack.pop()
                    self._subroutine_stack.pop()
                    if ret is None:
                        # P9：单行 IF 语句里的 GOSUB 返回后即结束该语句作用域
                        # （_exec_inline_statement 用 None 哨兵表示）。
                        break
                    idx = ret
                continue

            # Transform commands
            if self._handle_transform(line, line_no):
                idx += 1
                continue

            # No-op / flow-control commands in preview
            if re.match(r"^END\b", line, re.IGNORECASE):
                # GDL END terminates script execution.
                break

            if re.match(r"^PUT\b", line, re.IGNORECASE):
                args_text = (re.match(r"^PUT\b\s*(.*)$", line, re.IGNORECASE).group(1) or "").strip()
                vals = self._eval_args(_split_args(args_text), line_no)
                if vals is not None:
                    self._put_stack.extend(vals)
                idx += 1
                continue

            # GROUP/ENDGROUP/PLACEGROUP（P14）：3D 组布尔构造。仅 3d 模式
            # 执行；2d/setup 模式静默跳过（组是纯 3D 构造）。
            if re.match(r"^(GROUP|ENDGROUP|PLACEGROUP)\b", line, re.IGNORECASE):
                if mode == "3d":
                    self._handle_group_command(line, line_no)
                idx += 1
                continue

            # ADDGROUP/SUBGROUP/ISECTGROUP 语句形式（无赋值目标，结果回写第
            # 一个算子组）；赋值形式在上面的赋值分发里拦截。
            m_group_stmt = re.match(
                r"^(ADDGROUP|SUBGROUP|ISECTGROUP)\s*(?:\((.*)\)|\s+(.*))$",
                line, re.IGNORECASE,
            )
            if m_group_stmt:
                if mode == "3d":
                    args_text = m_group_stmt.group(2)
                    if args_text is None:
                        args_text = m_group_stmt.group(3) or ""
                    self._handle_group_bool_statement(
                        m_group_stmt.group(1).upper(), args_text, line_no
                    )
                idx += 1
                continue

            # P4（2D 文本链）：SET STYLE "name" 跟踪当前样式（供 TEXT2 用）。
            # 必须在下方静默 no-op 正则（含 SET）之前拦截；其余 SET * 保持原
            # no-op 行为不变。setup/3d 模式不需要——落入原 no-op。
            if mode == "2d" and re.match(r"^SET\s+STYLE\b", line, re.IGNORECASE):
                m_set_style = re.match(r'^SET\s+STYLE\s+"([^"]*)"', line, re.IGNORECASE)
                if m_set_style:
                    self._current_style = m_set_style.group(1)
                idx += 1
                continue

            # P4（2D 文本链）：PARAGRAPH 是块结构——向前消费逻辑行直到
            # ENDPARAGRAPH。2d 模式解析内容（set style + 纯字符串行）；3d 模式
            # 整块静默跳过（文本链是纯 2D 构造）；setup 模式不拦截，逐行落入
            # 下方 setup 跳过即可。
            if mode in ("2d", "3d") and re.match(r"^PARAGRAPH\b", line, re.IGNORECASE):
                body: list[tuple[int, str]] = []
                j = idx + 1
                while j < end and not re.match(r"^ENDPARAGRAPH\b", lines[j][1], re.IGNORECASE):
                    body.append(lines[j])
                    j += 1
                if j >= end:
                    self._warn(line_no, "PARAGRAPH 缺少匹配 ENDPARAGRAPH，已跳过")
                    idx = end
                    continue
                if mode == "2d":
                    self._handle_paragraph(line, body, line_no)
                idx = j + 1
                continue

            # Recognized but non-renderable commands — suppress "未支持命令" warning.
            # SET（属性设置语句，如 SET MATERIAL x）与 VALUES（参数脚本构造，如
            # VALUES "A" RANGE [..]）对预览无几何副作用：SET 无副作用处理，
            # VALUES 属参数脚本范畴，在 2D/3D 中静默忽略，均不告警。
            # FILL / LINE_PROPERTY（P4）：属性设置语句，同样无几何副作用。
            # WALLHOLE / WALLNICHE（P14）：墙体开洞构造，只影响 Archicad 里的
            # 宿主墙，无自身几何；SECT_FILL 是属性设置。HOTSPOT/HOTLINE/HOTARC
            # 系（含 2D 变体）是捕捉点/辅助线，不渲染。
            if re.match(
                r"^(RESOL|TOLER|MATERIAL|PEN|XFORM|SET|VALUES|FILL|LINE_PROPERTY"
                r"|WALLHOLE|WALLNICHE|SECT_FILL"
                r"|HOTSPOT2?|HOTLINE2?|HOTARC2?)\b",
                line, re.IGNORECASE,
            ):
                idx += 1
                continue

            # P3（CALL）：在 setup 跳过逻辑之前拦截。setup 模式跳过 CALL
            # （与 BLOCK 等几何命令一致——master script 里的 CALL 不执行，
            # MVP 近似：Archicad 中 master script 的 CALL 实际会执行）。
            if re.match(r"^CALL\b", line, re.IGNORECASE):
                if mode != "setup":
                    self._handle_call(line, line_no, mode)
                idx += 1
                continue

            if mode == "setup":
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

    def _exec_inline_statement(
        self,
        statement: str,
        line_no: int,
        lines: list[tuple[int, str]],
        idx: int,
        mode: str,
        condition_true: bool | None = True,
    ) -> None:
        """执行单行 IF（IF cond THEN stmt）的语句部分。

        GDL 的 `:` 是语句分隔符：语句部分先按 `:` 拆成多条独立语句（引号
        "..." 内的 `:` 不拆）逐条执行；单语句无 `:` 时行为逐字节不变。

        P13（Archicad 语义对齐）：单行 IF 同行只允许**一条**条件语句——
        GDL Reference Guide（AC23 官方 PDF p323）原文 "A command after THEN
        in the same row means a definite ENDIF."。因此 `IF cond THEN s1 : s2`
        等价于 `IF cond THEN s1`（条件执行）再接 `s2`（无条件执行）：
        `condition_true` 只门控首句；`:` 后的语句无论条件真假都执行。

        GOSUB 语句特殊处理：子程序在脚本后方（标签位置），单元素子块无法
        跳到那里。这里直接在完整脚本上下文中从标签执行到 RETURN（RETURN 用
        None 哨兵返回，结束语句作用域，不会继续执行脚本剩余部分）。其余
        语句保持原有单行子块执行方式。
        """
        parts = _split_colon_statements(statement)
        if len(parts) > 1:
            # P13：`:` 多语句——首句由条件门控，其余无条件执行（拆成单元素
            # 子块复用 _exec_block 既有语句分发：赋值/变换/GOSUB 等；跨作用域
            # GOSUB 由 _exec_block 的 P10 None 哨兵分支处理）。
            if condition_true:
                self._exec_block([(line_no, parts[0])], 0, 1, mode=mode)
            self._exec_block(
                [(line_no, part) for part in parts[1:]], 0, len(parts) - 1, mode=mode
            )
            return
        if not condition_true:
            return
        m = re.match(r"^GOSUB\b\s*(.+)$", statement, re.IGNORECASE)
        if m:
            target = self._resolve_gosub_target(m.group(1), line_no)
            if target is None:
                self._warn(line_no, "GOSUB 目标标签未找到，已跳过")
                return
            self._gosub_stack.append(None)
            self._subroutine_stack.append(self._subroutine_extents.get(lines[target][0]))
            self._exec_block(lines, target, len(lines), mode=mode)
            return
        self._exec_block([(line_no, statement)], 0, 1, mode=mode)

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
            if self.wall_clock_limit > 0 and (
                time.monotonic() - self._start_time
            ) > self.wall_clock_limit:
                self._warn(
                    line_no,
                    f"FOR 执行超过耗时上限 {self.wall_clock_limit:g} 秒，提前终止",
                )
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
                # P9：单行 IF（IF cond THEN stmt，自包含）不计深度——它有自己的
                # THEN 语句、不消耗 ENDIF。只有块级 IF（IF cond THEN + ENDIF）
                # 才增加嵌套深度。
                if _extract_inline_if(line) is None:
                    depth += 1
            elif cmd == "ENDIF":
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _find_matching_if_bounds(
        self,
        lines: list[tuple[int, str]],
        if_idx: int,
        end: int,
    ) -> tuple[int | None, int | None]:
        """Find ELSE/ENDIF matching IF at if_idx. Returns (else_idx, endif_idx)."""
        depth = 1
        else_idx: int | None = None
        for i in range(if_idx + 1, end):
            _, line = lines[i]
            cmd = _extract_command(line)
            if cmd == "IF":
                # P9：单行 IF 不计深度（同 _find_matching_endif）。
                if _extract_inline_if(line) is None:
                    depth += 1
            elif cmd == "ENDIF":
                depth -= 1
                if depth == 0:
                    return else_idx, i
            elif cmd == "ELSE" and depth == 1 and else_idx is None:
                else_idx = i
        return else_idx, None

    def _eval_condition(self, condition: str, line_no: int) -> bool | None:
        try:
            return _safe_eval_condition(condition, self.env, funcs=self._funcs)
        except Exception as exc:
            self._warn(line_no, f"IF 条件解析失败 `{condition}`: {exc}")
            return None

    # ── CALL 宏执行（P3 MVP）────────────────────────────────────────────
    # 设计取舍（MVP 近似，注释留痕）：
    # - PARAMETERS ALL 不区分参数与局部变量，caller 整个 env 直接合并覆盖。
    # - master script 以 mode="setup" 执行，其中的 CALL 不生效（嵌套 master
    #   委托极少见；真需要时扩展 setup 模式下的受限 CALL）。
    # - child 只回写几何与警告，不回写 env / 变换 / PUT 栈——天然隔离。
    # - 严格模式（strict / unknown_command_policy="error"）不为 CALL 新增抛错
    #   路径：CALL 是有明确诊断码的已知命令，诊断保持 warning（复用 _warn 而
    #   非 _handle_unknown_command——后者只对真正未知的命令生效）。
    # - unknown_command_policy="ignore" 同样静默 CALL 诊断（用户显式选择不
    #   看告警），但闸门依然生效（只跳过执行）。

    def _call_warn(self, line_no: int, msg: str, *, code: str, dedupe_key: str = "") -> None:
        """CALL 诊断出口：policy=ignore 时静默；dedupe_key 非空时同键只警一次。"""
        if self.unknown_command_policy == "ignore":
            return
        if dedupe_key:
            key = (code, dedupe_key)
            if key in self._call_warned:
                return
            self._call_warned.add(key)
        self._warn(line_no, msg, command="CALL", code=code)

    def _handle_call(self, line: str, line_no: int, mode: str) -> None:
        """解析并执行/诊断一条 CALL 语句（mode 为 "2d" 或 "3d"）。"""
        spec = _parse_call_statement(line)
        if spec is None:
            self._call_warn(
                line_no,
                f"CALL 语句无法解析（缺少引号包裹的宏名），已跳过: {line[:80]}",
                code="MACRO_PARSE_FAIL",
            )
            return
        name, param_kind, param_entries, had_malformed = spec
        if had_malformed:
            self._call_warn(
                line_no,
                f"CALL '{name}' 的 PARAMETERS 列表含无法解析的条目，已忽略该条目",
                code="MACRO_PARSE_FAIL",
            )

        budget = self._call_budget
        # 共享 wall-clock 闸门：累计耗时超限后跳过后续所有 CALL
        if budget.limit > 0 and (time.monotonic() - budget.start) > budget.limit:
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏执行累计耗时超过上限 {budget.limit:g} 秒，跳过后续 CALL",
                code="MACRO_TIMEOUT",
                dedupe_key="__timeout__",
            )
            return
        # 总次数预算（全链路共享）
        if budget.total_calls >= budget.max_calls:
            self._call_warn(
                line_no,
                f"CALL 总次数超过预算 {budget.max_calls}，跳过后续 CALL（含 '{name}'）",
                code="MACRO_BUDGET",
                dedupe_key="__budget__",
            )
            return

        if self._macro_resolver is None:
            # P1：不执行也要可诊断——区分"脚本本身无几何"与"几何在宏中"。
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏依赖未解析（未配置图库上下文），几何可能在宏中",
                code="MACRO_NO_RESOLVER",
                dedupe_key=name,
            )
            return

        # 递归环：同一解析链中宏名重复出现
        if name in self._macro_chain:
            chain_text = "/".join((*self._macro_chain, name))
            self._call_warn(
                line_no,
                f"CALL '{name}'：检测到宏递归环（{chain_text}），已跳过",
                code="MACRO_RECURSION",
                dedupe_key=name,
            )
            return
        # 嵌套深度闸门
        if len(self._macro_chain) >= DEFAULT_CALL_DEPTH_LIMIT:
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏嵌套深度超过上限 {DEFAULT_CALL_DEPTH_LIMIT}，已跳过",
                code="MACRO_DEPTH_LIMIT",
                dedupe_key=name,
            )
            return

        # GUID 提示：当前脚本的 calledmacros 表（精确匹配失败再大小写不敏感）
        guid_hint = self._macro_guid_map.get(name)
        if guid_hint is None:
            for k, v in self._macro_guid_map.items():
                if k.upper() == name.upper():
                    guid_hint = v
                    break

        try:
            lookup = self._macro_resolver(name, guid_hint)
        except Exception as exc:  # 解析器自身异常按 error 诊断，不崩溃
            lookup = MacroLookup(status="error", name=name, message=f"宏解析器异常: {exc}")

        if lookup.status == "missing":
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏未找到。{lookup.message}".rstrip(),
                code="MACRO_MISSING",
                dedupe_key=name,
            )
            return
        if lookup.status == "ambiguous":
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏名存在多个候选，无法唯一确定。{lookup.message}".rstrip(),
                code="MACRO_AMBIGUOUS",
                dedupe_key=name,
            )
            return
        if lookup.status != "resolved":
            self._call_warn(
                line_no,
                f"CALL '{name}'：宏解析出错。{lookup.message}".rstrip(),
                code="MACRO_ERROR",
                dedupe_key=name,
            )
            return

        # resolved 但带非空 message（典型：按名称命中但 GUID 不符）→ 透传告警
        if lookup.message:
            self._call_warn(
                line_no,
                f"CALL '{name}'：{lookup.message}",
                code="MACRO_GUID_MISMATCH",
                dedupe_key=name,
            )

        budget.total_calls += 1

        # 参数环境：宏默认值 → PARAMETERS 覆盖（表达式在 caller env 中求值）
        child_env = _normalize_parameters(lookup.parameters)
        if param_kind == "all":
            # MVP 近似：不区分参数与局部变量，caller 整个 env 覆盖宏默认
            for k, v in self.env.items():
                child_env[str(k).upper()] = v
        else:
            for pname, expr in param_entries:
                value = self._eval_call_param(expr, line_no)
                if value is not None:
                    child_env[pname.upper()] = value

        # child 的 FOR 耗时闸门沿用"剩余时间"（共享 deadline 由 budget 保证）
        if budget.limit > 0:
            remaining = max(0.0, budget.limit - (time.monotonic() - budget.start))
        else:
            remaining = 0.0

        child = _PreviewRuntime(
            parameters=child_env,
            for_limit=self.for_limit,
            strict=self.strict,
            unknown_command_policy=self.unknown_command_policy,
            quality=self.quality,
            wall_clock_limit=remaining,
            macro_resolver=self._macro_resolver,
            macro_guid_map=lookup.called_macros,
            _call_budget=budget,
            _macro_chain=(*self._macro_chain, name),
        )
        # 变换继承：拷贝 caller 当前 _A/_t（不共享可变状态），child 的变换栈
        # 为空；child 内 ADD/DEL 不回写 caller（天然隔离）。
        child._A = tuple(tuple(row) for row in self._A)
        child._t = tuple(self._t)

        # 先执行宏的 master script（setup 模式；其中的 CALL 不生效，MVP 近似）
        master = lookup.scripts.get("1d.gdl")
        if master:
            child.execute(master, mode="setup")
        script_key = "2d.gdl" if mode == "2d" else "3d.gdl"
        mode_text = "2D" if mode == "2d" else "3D"
        macro_script = lookup.scripts.get(script_key)
        if not macro_script:
            self._call_warn(
                line_no,
                f"宏 '{name}' 没有 {mode_text} 脚本，已跳过",
                code="MACRO_NO_SCRIPT",
                dedupe_key=f"{name}::{mode}",
            )
            return
        child.execute(macro_script, mode=mode)
        child.finish()

        chain_label = "/".join((*self._macro_chain, name))

        # 结果合并：3d → meshes/wires；2d → 各裸 tuple 列表
        if mode == "3d":
            for mesh in child.result_3d.meshes:
                if mesh.source_ref is not None and mesh.source_ref.macro is None:
                    # 仅设置宏内直接产生的 mesh；嵌套 CALL 的 mesh 已带完整链
                    mesh.source_ref.macro = chain_label
                self._emit_mesh3d(mesh)
            self._emit_wires3d(child.result_3d.wires)
        else:
            self.result_2d.lines.extend(child.result_2d.lines)
            self.result_2d.polygons.extend(child.result_2d.polygons)
            self.result_2d.polygon_fills.extend(child.result_2d.polygon_fills)
            self.result_2d.polygon_contours.extend(child.result_2d.polygon_contours)
            self.result_2d.circles.extend(child.result_2d.circles)
            self.result_2d.arcs.extend(child.result_2d.arcs)
            self.result_2d.texts.extend(child.result_2d.texts)

        # child 警告透传：文本加 [宏 链] 前缀；structured 保留 line/command/code
        for w in child._warnings:
            self._warnings.append(f"[宏 {chain_label}] {w}")
        for ws in child._warnings_structured:
            self._warnings_structured.append(
                PreviewWarning(
                    line=ws.line,
                    command=ws.command,
                    message=f"[宏 {chain_label}] {ws.message}",
                    level=ws.level,
                    code=ws.code,
                )
            )

    def _eval_call_param(self, expr: str, line_no: int) -> Any:
        """PARAMETERS 覆盖值求值：在 caller env 中求值；去引号字符串字面量与
        caller env 中的字符串值按字符串传递（参考 _normalize_parameters 的
        字符串保留逻辑）；数值走 _eval_expr。求值失败返回 None（已告警）。"""
        text = (expr or "").strip()
        if not text:
            self._warn(line_no, "CALL PARAMETERS 含空表达式，已跳过该参数", command="CALL")
            return None
        # 字符串字面量（单/双/反引号）
        if len(text) >= 2 and text[0] in {'"', "'", "`"} and text[-1] == text[0]:
            return text[1:-1]
        # caller env 中的字符串变量
        if re.match(r"^[A-Za-z_]\w*$", text):
            value = self.env.get(text.upper())
            if isinstance(value, str):
                return value
        return self._eval_expr(text, line_no)

    def _handle_transform(self, line: str, line_no: int) -> bool:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b\s*(.*)$", line)
        if not m:
            return False

        cmd = m.group(1).upper()
        arg_text = (m.group(2) or "").strip()
        args = _split_args(arg_text)

        if cmd in {"ADD", "ADDX", "ADDY", "ADDZ", "ADD2"}:
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
            elif cmd == "ADD2":
                # 2D 平移：等价于 ADD dx, dy（z 不影响 2D 平面）。
                if not vals:
                    self._warn(line_no, "ADD2 缺少参数，已跳过")
                    return True
                dx = float(vals[0] or 0.0)
                dy = float(vals[1] or 0.0) if len(vals) >= 2 else 0.0
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

        if cmd in {"ROTX", "ROTY", "ROTZ", "ROT", "ROT2"}:
            vals = [self._eval_expr(a, line_no) for a in args] if args else []
            if not vals:
                self._warn(line_no, f"{cmd} 缺少角度参数，已跳过")
                return True
            deg = float(vals[0] or 0.0)
            if cmd == "ROTX":
                M = _rot_x_deg(deg)
            elif cmd == "ROTY":
                M = _rot_y_deg(deg)
            else:
                # ROT / ROTZ / ROT2 → 绕 Z 轴旋转（2D 平面旋转）
                M = _rot_z_deg(deg)
            self._push_transform(_m_mul(M, self._A), self._t)
            return True

        if cmd in {"MUL", "MULX", "MULY", "MULZ", "MUL2"}:
            vals = [self._eval_expr(a, line_no) for a in args] if args else []
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
            elif cmd == "MUL2":
                # 2D 缩放：MUL2 s（等比）或 MUL2 sx, sy
                if len(vals) == 1:
                    sx = sy = float(vals[0] or 1.0)
                elif len(vals) >= 2:
                    sx = float(vals[0] or 1.0)
                    sy = float(vals[1] or 1.0)
                else:
                    self._warn(line_no, "MUL2 参数需 1 或 2 个，已跳过")
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

        if cmd in {"DEL", "DEL2"}:
            if not args:
                del_count = 1
            else:
                val = self._eval_expr(args[0], line_no)
                if val is None:
                    self._warn(line_no, f"{cmd} 参数解析失败，按 1 处理")
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
            # 对齐契约：RECT2 无填充语义——fill=False, contour=True
            self.result_2d.polygon_fills.append(False)
            self.result_2d.polygon_contours.append(True)
            return True

        if cmd in {"HOTSPOT2", "HOTLINE2", "HOTARC2"}:
            # 交互拖拽热点/热区（见 knowledge/GDL_2d_commands.md）：非渲染几何，
            # 预览中识别为无副作用——不产生几何、不告警。
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
            # 对齐契约：旧 POLY2 无填充语义——fill=False, contour=True
            self.result_2d.polygon_fills.append(False)
            self.result_2d.polygon_contours.append(True)
            return True

        # P4：POLY2_ / POLY2_A / POLY2_B / POLY2_B{5}——带附加状态码的多边形
        # （圆弧/洞/属性记录），头部值数：2 / 3 / 4 / 13。
        if cmd in {"POLY2_", "POLY2_A", "POLY2_B"}:
            return self._handle_poly2_ex(cmd, args_text, line_no)

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
            # 未持有 3D 脚本（semantic_verifier / 验收等旧调用方）：行为与
            # P3a 前逐字节一致——原样占位警告，不做任何新解析/新警告。
            if self._script_3d is None:
                self._warn(line_no, "PROJECT2 暂为占位预览（未实现真实投影）")
                return True
            # PROJECT2{n} 扩展形态（ARCHICAD 限定面/体投影）：MVP 不支持，
            # 明确警告、不静默（_split_args 会把 {n} 当参数吃掉，先拦截原文）。
            if re.match(r"^PROJECT2\{", line, re.IGNORECASE):
                self._warn(
                    line_no,
                    "PROJECT2{n} 扩展形态暂不支持（MVP 仅支持基本形态）",
                )
                return True
            self._handle_project2(args_raw, line_no)
            return True

        # ── P4：2D 文本链（DEFINE STYLE / TEXTBLOCK / RICHTEXT2 / TEXT2）────
        if cmd == "DEFINE":
            return self._handle_define(args_text, line_no)

        if cmd == "TEXTBLOCK":
            # TEXTBLOCK "name" ...最后一个字符串参数是段落名
            names = re.findall(r'"([^"]*)"', args_text)
            if len(names) < 2:
                self._warn(line_no, "TEXTBLOCK 缺少段落引用，已忽略", command=cmd)
                return True
            self._textblocks[names[0]] = names[-1]
            return True

        if cmd == "RICHTEXT2":
            m_rt = re.match(r'^(.*?),\s*"([^"]*)"\s*$', args_text)
            if not m_rt:
                self._warn(line_no, "RICHTEXT2 参数无法解析，已跳过", command=cmd)
                return True
            vals = self._eval_args(_split_args(m_rt.group(1)), line_no)
            if vals is None or len(vals) < 2:
                self._warn(line_no, "RICHTEXT2 坐标解析失败，已跳过", command=cmd)
                return True
            self._emit_richtext(vals[0], vals[1], m_rt.group(2), line_no)
            return True

        if cmd == "TEXT2":
            # 文本表达式可能含逗号/引号 → 用引号+括号感知的 splitter
            parts = _split_call_args(args_text)
            if len(parts) < 3:
                self._warn(line_no, "TEXT2 参数不足或解析失败", command=cmd)
                return True
            vals = self._eval_args(parts[:2], line_no)
            if vals is None:
                self._warn(line_no, "TEXT2 坐标解析失败，已跳过", command=cmd)
                return True
            text = self._eval_text_expr(", ".join(parts[2:]), line_no)
            if text is None:
                return True
            size = 0.0
            if self._current_style is not None:
                style = self._styles.get(self._current_style)
                if style is None:
                    self._warn(
                        line_no,
                        f"TEXT2 引用了未定义的样式 '{self._current_style}'",
                        command=cmd,
                    )
                else:
                    # mm → 模型单位：纸张尺寸 × 名义出图比例 1:50（不随 MUL2
                    # 缩放，取舍见 PREVIEW_TEXT_NOMINAL_SCALE 注释）
                    size = float(style["size_mm"]) * 0.001 * PREVIEW_TEXT_NOMINAL_SCALE
            px, py = self._p2(vals[0], vals[1])
            self.result_2d.texts.append(PreviewText(x=px, y=py, text=text, size=size))
            return True

        return False

    def _handle_project2(self, args_raw: list[str], line_no: int) -> None:
        """PROJECT2 顶视图投影（MVP，P3a）。

        GDL 形态：``PROJECT2 projection_code, angle, method``
        - projection_code：只支持 3（顶视图）。其他值 → 明确警告"暂不支持"，
          不静默、不投影。
        - angle：投影后绕原点旋转。选 Archicad 语义——"视角旋转 = 投影点
          反向旋转 angle 度"，即旋转 −angle（标准数学逆时针为正；angle=90
          时投影点旋转 −90°）。投影点先旋转、再过 _p2 应用当前 2D 变换
          （ADD2/ROT2 等，见测试锁定）。
        - method（hidden-line 等）：MVP 无 hidden-line，忽略 + 一次性警告。
        - PROJECT2{2}/{3} 扩展形态在 _handle_2d 中先拦截 → 明确警告。

        投影算法（MVP 线框）：每 mesh 收集去重边（面边按顶点索引对
        (min,max) 去重），投影 = 丢 z 取 (x, y)，过 _p2 后写入
        result_2d.lines。不做轮廓并集、不做 hidden-line。3D mesh 顶点已是
        世界坐标（3D 变换在建 mesh 时应用完毕），投影直接取最终顶点。
        """
        vals = self._eval_args(args_raw, line_no)
        if vals is None or not vals:
            self._warn(line_no, "PROJECT2 参数不足或解析失败")
            return

        code = int(round(float(vals[0])))
        angle = float(vals[1]) if len(vals) >= 2 else 0.0
        if len(vals) >= 3 and not self._project2_method_warned:
            self._warn(line_no, "PROJECT2 method 参数暂忽略（无 hidden-line）")
            self._project2_method_warned = True

        if code != 3:
            self._warn(
                line_no,
                f"PROJECT2 投影方式 {code} 暂不支持（当前仅支持 3=顶视图）",
            )
            return

        # 惰性投影：首次 PROJECT2 时用同一组 parameters/setup/for_limit/
        # quality/unknown_command_policy 起内部 runtime 执行 3D 脚本，缓存
        # meshes 供本 runtime 内后续 PROJECT2 复用。内部 3D 执行的 warnings
        # 不并入 2D 结果——3D 预览路径已展示，避免重复告警。
        if self._project2_meshes is None:
            inner = _PreviewRuntime(
                parameters=self.env,
                for_limit=self.for_limit,
                strict=self.strict,
                unknown_command_policy=self.unknown_command_policy,
                quality=self.quality,
                wall_clock_limit=self.wall_clock_limit,
                macro_resolver=self._macro_resolver,
                macro_guid_map=self._macro_guid_map,
                _call_budget=self._call_budget,
                _macro_chain=self._macro_chain,
            )
            if self._setup_script:
                inner.execute(self._setup_script, mode="setup")
            inner.execute(self._script_3d, mode="3d")
            inner.finish()
            self._project2_meshes = inner.result_3d.meshes

        rad = math.radians(-angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        for mesh in self._project2_meshes:
            verts = list(zip(mesh.x, mesh.y, mesh.z))
            edges: set[tuple[int, int]] = set()
            for a, b, c in zip(mesh.i, mesh.j, mesh.k):
                for p, q in ((a, b), (b, c), (c, a)):
                    if p != q:
                        edges.add((min(p, q), max(p, q)))
            for p, q in edges:
                x1, y1, _ = verts[p]
                x2, y2, _ = verts[q]
                # 投影（丢 z）后绕原点反向旋转 angle 度
                px1 = x1 * cos_a - y1 * sin_a
                py1 = x1 * sin_a + y1 * cos_a
                px2 = x2 * cos_a - y2 * sin_a
                py2 = x2 * sin_a + y2 * cos_a
                self.result_2d.lines.append(
                    (self._p2(px1, py1), self._p2(px2, py2))
                )

    # ── P4：POLY2_ 系（附加状态码多边形）与 2D 文本链 ────────────────────

    def _handle_poly2_ex(self, cmd: str, args_text: str, line_no: int) -> bool:
        """POLY2_ / POLY2_A / POLY2_B / POLY2_B{5} 统一入口。

        头部值数：POLY2_=2（n, frame_fill）、POLY2_A=3（+fill_pen）、
        POLY2_B=4（+fill_background_pen）、POLY2_B{5}=13（+fillcategory、
        distortion_flags、origo、变形矩阵、gradientInnerRadius）。
        之后 n 条记录，每条 3 值 (x, y, status)；n 计入圆弧参数记录。
        frame_fill = j1 + 2*j2 + 4*j3：j1 画轮廓、j2 画填充、j3 闭合。
        """
        variant = None
        m_var = re.match(r"^\{(\d+)\}\s*(.*)$", args_text, re.DOTALL)
        text = args_text
        if m_var:
            variant = int(m_var.group(1))
            text = m_var.group(2)
        header = {"POLY2_": 2, "POLY2_A": 3, "POLY2_B": 4}[cmd]
        if cmd == "POLY2_B" and variant == 5:
            header = 13
        elif variant is not None and not (cmd == "POLY2_B" and variant == 5):
            self._warn(
                line_no,
                f"{cmd}{{{variant}}} 变体暂不支持（按 {cmd} 基本形态解析头部）",
                command=cmd,
            )

        vals = self._eval_args(_split_args(text), line_no)
        if vals is None or len(vals) < header:
            self._warn(line_no, f"{cmd} 参数不足或解析失败", command=cmd)
            return True
        n = int(round(vals[0]))
        frame_fill = int(round(vals[1]))
        if n <= 0:
            self._warn(line_no, f"{cmd} 记录数必须 > 0", command=cmd)
            return True
        if n > 10000:
            # 防畸形输入：记录数上限
            self._warn(line_no, f"{cmd} 记录数 {n} 超过上限 10000，已跳过", command=cmd)
            return True
        need = header + 3 * n
        if len(vals) < need:
            self._warn(line_no, f"{cmd} 记录数据不足（需 {need} 个值），已跳过", command=cmd)
            return True
        records = [
            (float(vals[header + 3 * i]), float(vals[header + 3 * i + 1]), int(round(vals[header + 3 * i + 2])))
            for i in range(n)
        ]
        contours = self._build_poly2_contours(records, line_no, cmd)

        j1 = bool(frame_fill & 1)  # 画轮廓
        j2 = bool(frame_fill & 2)  # 画填充
        j3 = bool(frame_fill & 4)  # 闭合开放多边形
        if not j1 and not j2:
            self._warn(line_no, f"{cmd} frame_fill=0（不画轮廓也不填充），未产生几何", command=cmd)
            return True
        for contour in contours:
            pts = contour
            if j3 and len(pts) >= 2 and (abs(pts[0][0] - pts[-1][0]) > 1e-9 or abs(pts[0][1] - pts[-1][1]) > 1e-9):
                pts = pts + [pts[0]]
            if len(pts) < 2:
                continue
            self.result_2d.polygons.append([self._p2(x, y) for x, y in pts])
            self.result_2d.polygon_fills.append(j2)
            self.result_2d.polygon_contours.append(j1)
        return True

    def _build_poly2_contours(
        self, records: list[tuple[float, float, int]], line_no: int, cmd: str
    ) -> list[list[Point2D]]:
        """把 (x, y, status) 记录流展开为轮廓点列（局部坐标，未过 _p2）。

        状态码分类按十进制数值区间（官方文档写作 s & 900 / s & 3000 等，
        但它们是数值不是位标志——900 & 4000 ≠ 0，位与无法互斥区分）：
        [600,700) 填充属性 / [700,800) 段属性（跳过）/ [800,900) 切线向量 /
        [900,1000) 圆心 / [1000,2000) 切线弧 / [2000,3000) 半径+圆心角弧 /
        [3000,4000) 弧终点 / ≥4000 起角+圆心角（4000/4001 同等对待）。
        s & 1 的边可见性 MVP 忽略（画全部轮廓）；s == -1 轮廓结束（洞分隔）
        ——洞按独立多边形 emit，填充不做布尔减除。
        """
        contours: list[list[Point2D]] = []
        current: list[Point2D] = []
        pending_center: Point2D | None = None
        pending_tangent: Point2D | None = None

        def flush() -> None:
            nonlocal current
            if current:
                contours.append(current)
                current = []

        def append_arc(pts: list[Point2D], exact_end: Point2D | None = None) -> None:
            if not pts:
                return
            if current and _dist2(pts[0], current[-1]) <= 1e-18:
                pts = pts[1:]
            if exact_end is not None and pts:
                pts[-1] = exact_end
            current.extend(pts)

        def warn_status_once(code: int, msg: str) -> None:
            if code not in self._poly2_status_warned:
                self._poly2_status_warned.add(code)
                self._warn(line_no, msg, command=cmd)

        for x, y, s in records:
            if s == -1:
                # 轮廓结束（洞分隔）
                flush()
                pending_center = None
                pending_tangent = None
                continue

            if pending_center is not None:
                # 圆心记录之后：本记录决定弧
                cx, cy = pending_center
                pending_center = None
                if not current:
                    self._warn(line_no, f"{cmd} 圆心记录前没有轮廓点，已跳过该弧", command=cmd)
                    continue
                px, py = current[-1]
                r = math.hypot(px - cx, py - cy)
                if s >= 4000:
                    # 起角 + 圆心角（度）
                    append_arc(_arc_tessellate(cx, cy, r, x, y))
                elif 3000 <= s < 4000:
                    # 弧终点：方向由三点叉积定，退化（共线）按逆时针半圆劣弧
                    a0 = math.degrees(math.atan2(py - cy, px - cx))
                    a1 = math.degrees(math.atan2(y - cy, x - cx))
                    cross = (px - cx) * (y - cy) - (py - cy) * (x - cx)
                    if cross > 1e-12:
                        sweep = (a1 - a0) % 360.0
                    elif cross < -1e-12:
                        sweep = -((a0 - a1) % 360.0)
                    else:
                        sweep = 180.0
                    append_arc(_arc_tessellate(cx, cy, r, a0, sweep), exact_end=(x, y))
                else:
                    warn_status_once(s, f"{cmd} 圆心后的弧记录状态码 {s} 无法识别，按角点处理")
                    current.append((x, y))
                continue

            if 600 <= s < 800:
                # 填充/段属性记录：不计入轮廓、不告警
                continue
            if 800 <= s < 900:
                # 切线方向向量（供紧随的切线弧使用）
                pending_tangent = (x, y)
                continue
            if 900 <= s < 1000:
                # 圆心（不在轮廓上），下一记录决定弧
                pending_center = (x, y)
                continue
            if 1000 <= s < 2000:
                # 切线弧到端点 (x,y)；切线由前置 800 记录给出
                if pending_tangent is None or not current:
                    if not self._tangent_fallback_warned:
                        self._tangent_fallback_warned = True
                        self._warn(line_no, f"{cmd} 切线弧无可用切线，退化为直线", command=cmd)
                    current.append((x, y))
                    pending_tangent = None
                    continue
                arc = _tangent_arc_points(current[-1], pending_tangent, (x, y))
                pending_tangent = None
                if arc is None:
                    if not self._tangent_fallback_warned:
                        self._tangent_fallback_warned = True
                        self._warn(line_no, f"{cmd} 切线弧几何退化，退化为直线", command=cmd)
                    current.append((x, y))
                    continue
                append_arc(arc, exact_end=(x, y))
                continue
            if 2000 <= s < 3000:
                # 半径 + 圆心角弧：从当前点出发，x=半径，y=圆心角（正=逆时针）
                if not current:
                    self._warn(line_no, f"{cmd} 半径弧记录前没有轮廓点，已跳过", command=cmd)
                    continue
                r = abs(x)
                sweep = y
                px, py = current[-1]
                # MVP 近似：圆心取"上一段方向"的左/右法向（sweep>0 → 左侧）；
                # 无前一段时用 +x 方向
                if len(current) >= 2:
                    dx = px - current[-2][0]
                    dy = py - current[-2][1]
                else:
                    dx, dy = 1.0, 0.0
                length = math.hypot(dx, dy)
                if length <= 1e-12 or r <= 1e-12:
                    continue
                nx, ny = -dy / length, dx / length  # 左法向
                side = 1.0 if sweep >= 0 else -1.0
                cx, cy = px + nx * r * side, py + ny * r * side
                a0 = math.degrees(math.atan2(py - cy, px - cx))
                append_arc(_arc_tessellate(cx, cy, r, a0, sweep))
                continue
            if s >= 3000:
                # 孤立的弧终点/角度记录（前面没有圆心）——不合法但别崩
                warn_status_once(s, f"{cmd} 弧记录状态码 {s} 缺少前置圆心记录，已跳过")
                continue
            if s >= 64:
                # 未知状态码：按角点处理，每个码值只警一次
                warn_status_once(s, f"{cmd} 未知状态码 {s}，按角点处理")
            # s & 1（边可见性）MVP 忽略：画全部轮廓
            current.append((x, y))
            pending_tangent = None

        flush()
        return contours

    # ── P4：2D 文本链 ────────────────────────────────────────────────────

    def _handle_paragraph(
        self, header: str, body: list[tuple[int, str]], line_no: int
    ) -> None:
        """PARAGRAPH 块：名 + 内容行（set style + 纯字符串字面量拼接为文本）。"""
        m = re.match(r'^PARAGRAPH\s+"([^"]*)"', header, re.IGNORECASE)
        if not m:
            self._warn(line_no, "PARAGRAPH 无法解析名称，已忽略", command="PARAGRAPH")
            return
        name = m.group(1)
        style: str | None = None
        parts: list[str] = []
        for _, bline in body:
            text = bline.strip()
            m_style = re.match(r'^SET\s+STYLE\s+"([^"]*)"\s*$', text, re.IGNORECASE)
            if m_style:
                style = m_style.group(1)
                continue
            m_text = re.match(r'^"([^"]*)"\s*$', text)
            if m_text:
                parts.append(m_text.group(1))
            # 其他内容（对齐/缩进属性行）MVP 忽略
        self._paragraphs[name] = {"text": "".join(parts), "style": style}

    def _handle_define(self, args_text: str, line_no: int) -> bool:
        """DEFINE STYLE / DEFINE STYLE{2} "name" "font", size_mm, flags → 样式表。

        非 STYLE 的 DEFINE 返回 False（走未知命令路径，行为与现状一致）。
        """
        m = re.match(
            r'^STYLE(?:\{\d+\})?\s+"([^"]*)"\s+"([^"]*)"\s*,\s*(.*)$',
            args_text,
            re.IGNORECASE,
        )
        if not m:
            return False
        name, font, rest = m.group(1), m.group(2), m.group(3)
        parts = _split_args(rest)
        size = self._eval_expr(parts[0], line_no) if parts and parts[0] else None
        if size is None:
            self._warn(line_no, f"DEFINE STYLE '{name}' 字号解析失败，已忽略", command="DEFINE")
            return True
        self._styles[name] = {"font": font, "size_mm": float(size)}
        return True

    def _emit_richtext(self, x: float, y: float, textblock_name: str, line_no: int) -> None:
        """RICHTEXT2：textblock → 段落 → 样式 逐层解析，缺引用告警不崩。"""
        par_name = self._textblocks.get(textblock_name)
        if par_name is None:
            self._warn(line_no, f"RICHTEXT2 引用了未定义的 TEXTBLOCK '{textblock_name}'", command="RICHTEXT2")
            return
        par = self._paragraphs.get(par_name)
        if par is None:
            self._warn(line_no, f"TEXTBLOCK '{textblock_name}' 引用了未定义的段落 '{par_name}'", command="RICHTEXT2")
            return
        size = 0.0
        style_name = par.get("style")
        if style_name:
            style = self._styles.get(style_name)
            if style is None:
                self._warn(line_no, f"段落 '{par_name}' 引用了未定义的样式 '{style_name}'", command="RICHTEXT2")
            else:
                # mm → 模型单位：纸张尺寸 × 名义出图比例 1:50（不随 MUL2
                # 缩放，取舍见 PREVIEW_TEXT_NOMINAL_SCALE 注释）
                size = float(style["size_mm"]) * 0.001 * PREVIEW_TEXT_NOMINAL_SCALE
        px, py = self._p2(x, y)
        self.result_2d.texts.append(PreviewText(x=px, y=py, text=str(par["text"]), size=size))

    def _eval_text_expr(self, expr: str, line_no: int) -> str | None:
        """TEXT2 的文本表达式：字符串字面量 / env 中的字符串或数值变量 /
        数值表达式（转 str）。失败返回 None（已告警）。"""
        text = (expr or "").strip()
        if not text:
            self._warn(line_no, "TEXT2 缺少文本表达式", command="TEXT2")
            return None
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            return text[1:-1]
        if re.match(r"^[A-Za-z_]\w*$", text) and text.upper() in self.env:
            value = self.env[text.upper()]
            if isinstance(value, str):
                return value
            return f"{float(value):g}"
        value = self._eval_expr(text, line_no)
        if value is None:
            return None
        return f"{float(value):g}"

    def _handle_3d(self, line: str, line_no: int) -> bool:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b\s*(.*)$", line)
        if not m:
            return False

        cmd = m.group(1).upper()
        args_text = (m.group(2) or "").strip()
        args_raw = _split_args(args_text)
        if cmd != "RULED":
            self._reset_ruled_chain()

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
                source_ref=self._source_ref_3d(line_no, cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
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
                source_ref=self._source_ref_3d(line_no, cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
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
                source_ref=self._source_ref_3d(line_no, cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
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
                source_ref=self._source_ref_3d(line_no, cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
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
                # PGON n, vect, status, edge1..edgen — edges start at index 3
                if n_edges >= 3 and n_edges <= len(vals) - 3:
                    edge_ids: list[int] = []
                    for i in range(n_edges):
                        eid = int(round(float(vals[3 + i])))
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
                self._emit_mesh3d(mesh)
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

            # P14：PRISM_ 三元组状态码 -1 = 轮廓结束（洞/多轮廓）。含 -1 时
            # 走轮廓拆分路径（洞桥接耳切盖帽 + 洞壁侧墙）；无 -1 保持旧路径
            # 逐字节不变。
            contours = _split_prism_contours(vals[2:], n)
            if contours is not None:
                outer, holes = contours[0], contours[1:]
                if holes:
                    mesh, wires = _make_prism_mesh_with_holes(
                        outer,
                        holes,
                        h,
                        self._offset(),
                        transform=self._A,
                        name=cmd,
                        source_ref=self._source_ref_3d(line_no, cmd),
                        warn=lambda msg: self._warn(line_no, msg, command=cmd),
                    )
                else:
                    mesh, wires = _make_prism_mesh(
                        outer,
                        h,
                        self._offset(),
                        transform=self._A,
                        name=cmd,
                        source_ref=self._source_ref_3d(line_no, cmd),
                        warn=lambda msg: self._warn(line_no, msg, command=cmd),
                    )
                self._emit_mesh3d(mesh)
                self._emit_wires3d(wires)
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
                source_ref=self._source_ref_3d(line_no, cmd),
                warn=lambda msg: self._warn(line_no, msg, command=cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
            return True

        if cmd == "RULED":
            # Optional version tag: RULED{2} — treat the same for preview
            rest = re.sub(r"^\{\d+\}\s*", "", args_text)
            vals = self._eval_args(_split_args(rest), line_no)
            if vals is None or len(vals) < 2:
                self._warn(line_no, "RULED 参数解析失败")
                return True
            n = int(round(vals[0]))
            mask = int(round(vals[1]))
            need = 2 + 3 * n + 3 * n
            if n < 2 or len(vals) < need:
                self._warn(line_no, "RULED 节点数据不足，已跳过")
                return True
            base = [
                (float(vals[2 + 3 * i]), float(vals[2 + 3 * i + 1]), 0.0)
                for i in range(n)
            ]
            top_off = 2 + 3 * n
            top = [
                (
                    float(vals[top_off + 3 * i]),
                    float(vals[top_off + 3 * i + 1]),
                    float(vals[top_off + 3 * i + 2]),
                )
                for i in range(n)
            ]
            # Open polylines are closed by the j3 side surface; closed
            # polylines (first == last node) must not be closed twice.
            first_b, last_b = base[0], base[-1]
            first_t, last_t = top[0], top[-1]
            tol = 1e-9
            already_closed = (
                abs(first_b[0] - last_b[0]) <= tol
                and abs(first_b[1] - last_b[1]) <= tol
                and abs(first_t[0] - last_t[0]) <= tol
                and abs(first_t[1] - last_t[1]) <= tol
                and abs(first_t[2] - last_t[2]) <= tol
            )
            self._emit_ruled(
                base,
                top,
                mask=mask,
                closed=not already_closed,
                line_no=line_no,
                cmd=cmd,
            )
            return True

        if cmd in {"TUBE", "TUBE_"}:
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 3:
                self._warn(line_no, f"{cmd} 参数不足或解析失败")
                return True
            n = int(round(vals[0]))
            m = int(round(vals[1]))
            mask = int(round(vals[2]))
            need = 3 + n * 3 + m * 2
            if n < 2 or m < 2 or len(vals) < need:
                self._warn(line_no, f"{cmd} 路径/截面数据不足，已跳过")
                return True
            path = [
                (float(vals[3 + 3 * i]), float(vals[3 + 3 * i + 1]), float(vals[3 + 3 * i + 2]))
                for i in range(n)
            ]
            sec_off = 3 + n * 3
            section = [
                (float(vals[sec_off + 2 * i]), float(vals[sec_off + 2 * i + 1]))
                for i in range(m)
            ]
            mesh, wires = _make_tube_mesh(
                path,
                section,
                self._offset(),
                mask=mask,
                transform=self._A,
                source_ref=self._source_ref_3d(line_no, cmd),
                warn=lambda msg: self._warn(line_no, msg, command=cmd),
            )
            self._emit_mesh3d(mesh)
            self._emit_wires3d(wires)
            return True

        if cmd == "LIN_":
            vals = self._eval_args(args_raw, line_no)
            if vals is None or len(vals) < 6:
                self._warn(line_no, "LIN_ 参数不足或解析失败")
                return True
            p1 = _apply_affine((float(vals[0]), float(vals[1]), float(vals[2])), self._A, self._t)
            p2 = _apply_affine((float(vals[3]), float(vals[4]), float(vals[5])), self._A, self._t)
            self._emit_wires3d([[p1, p2]])
            return True

        return False

    # ── RULED chain welding ──────────────────────────────────

    def _reset_ruled_chain(self) -> None:
        self._ruled_chain_mesh = None
        self._ruled_chain_top = None
        self._ruled_chain_top_idx = None

    def _emit_ruled(
        self,
        base: list[Point3D],
        top: list[Point3D],
        *,
        mask: int,
        closed: bool,
        line_no: int,
        cmd: str,
    ) -> None:
        """Emit RULED, welding onto the open chain mesh when the new base
        ring coincides with the previous segment's top ring."""
        n = len(base)
        base_world = [_apply_affine(p, self._A, self._t) for p in base]
        top_world = [_apply_affine(p, self._A, self._t) for p in top]

        if self._try_weld_ruled(base_world, top_world, mask=mask, closed=closed):
            return

        mesh, wires = _make_ruled_mesh(
            base,
            top,
            self._offset(),
            closed=closed,
            cap_base=bool(mask & 1),
            cap_top=bool(mask & 2),
            transform=self._A,
            source_ref=self._source_ref_3d(line_no, cmd),
            warn=lambda msg: self._warn(line_no, msg, command=cmd),
        )
        self._emit_mesh3d(mesh)
        self._emit_wires3d(wires)
        # Top ring world coords are the last n vertices before any cap centroid
        self._ruled_chain_mesh = mesh
        self._ruled_chain_top = top_world
        self._ruled_chain_top_idx = list(range(n, 2 * n))

    def _try_weld_ruled(
        self,
        base_world: list[Point3D],
        top_world: list[Point3D],
        *,
        mask: int,
        closed: bool,
    ) -> bool:
        """Append a segment to the open chain when rings coincide."""
        mesh = self._ruled_chain_mesh
        prev_top = self._ruled_chain_top
        prev_idx = self._ruled_chain_top_idx
        if mesh is None or prev_top is None or prev_idx is None:
            return False
        if len(prev_top) != len(base_world):
            return False
        for a, b in zip(base_world, prev_top):
            if not (
                math.isclose(a[0], b[0], rel_tol=1e-9, abs_tol=1e-12)
                and math.isclose(a[1], b[1], rel_tol=1e-9, abs_tol=1e-12)
                and math.isclose(a[2], b[2], rel_tol=1e-9, abs_tol=1e-12)
            ):
                return False

        n = len(base_world)
        idx_base = prev_idx
        idx_top: list[int] = []
        for p in top_world:
            idx_top.append(len(mesh.x))
            mesh.x.append(p[0])
            mesh.y.append(p[1])
            mesh.z.append(p[2])

        span = n if closed else n - 1
        for i in range(span):
            j = (i + 1) % n
            b1, b2 = idx_base[i], idx_base[j]
            t1, t2 = idx_top[i], idx_top[j]
            mesh.i.extend((b1, b1))
            mesh.j.extend((b2, t2))
            mesh.k.extend((t2, t1))

        if mask & 2:  # top cap on the new top ring (凸→旧扇形 / 凹→耳切，P3c)
            top2d = _project_polygon_to_plane(top_world)
            use_earclip, tris, degraded = (False, [], False)
            if top2d is not None:
                use_earclip, tris, degraded = _plan_cap_triangulation(top2d)
            if use_earclip:
                for a, b, c in tris:
                    mesh.i.append(idx_top[a])
                    mesh.j.append(idx_top[b])
                    mesh.k.append(idx_top[c])
            else:
                cx = sum(p[0] for p in top_world) / n
                cy = sum(p[1] for p in top_world) / n
                cz = sum(p[2] for p in top_world) / n
                c = len(mesh.x)
                mesh.x.append(cx)
                mesh.y.append(cy)
                mesh.z.append(cz)
                for i in range(span):
                    j = (i + 1) % n
                    mesh.i.append(c)
                    mesh.j.append(idx_top[i])
                    mesh.k.append(idx_top[j])
                if degraded:
                    self._warn(0, "RULED 顶面盖帽轮廓退化（重复顶点/共线/自交），回退扇形三角化", command="RULED")

        top_loop = [(mesh.x[i], mesh.y[i], mesh.z[i]) for i in idx_top]
        if closed:
            top_loop = top_loop + [top_loop[0]]
        self._emit_wires3d([top_loop])

        self._ruled_chain_top = top_world
        self._ruled_chain_top_idx = idx_top
        return True

    def _eval_args(self, args_raw: list[str], line_no: int) -> list[float] | None:
        vals: list[float] = []
        for arg in args_raw:
            if not arg:
                continue
            m = self._GET_USE_RE.match(arg)
            if m:
                func_name = m.group(1).upper()
                count = self._eval_expr(m.group(2).strip(), line_no)
                if count is None:
                    return None
                count_i = max(0, int(round(float(count))))
                if func_name == "GET":
                    items = self._put_get(count_i, line_no)
                else:
                    items = self._put_use(count_i, line_no)
                if items is None:
                    return None
                vals.extend(items)
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
            return _safe_eval_expr(text, self.env, funcs=self._funcs)
        except Exception as exc:
            self._warn(line_no, f"表达式解析失败 `{text}`: {exc}")
            return None

    def _eval_any(self, expr: str | None, line_no: int) -> float | str | None:
        """赋值右值求值：数值或字符串（字符串字面量/字符串变量/字符串数组
        元素原样返回 str）。失败告警并返回 None。"""
        if expr is None:
            return None
        text = expr.strip()
        if not text:
            self._warn(line_no, "空表达式")
            return None
        try:
            return _safe_eval_any(text, self.env, funcs=self._funcs)
        except Exception as exc:
            self._warn(line_no, f"表达式解析失败 `{text}`: {exc}")
            return None

    # ── DIM / 数组元素赋值（P14）─────────────────────────────────────────
    # GDL 数组 1-based；dim 声明即重置；写入超出声明长度自动扩展（动态数组
    # 语义，如 dim arr[] 后逐元素赋值）；读取越界在 _eval_ast 里返回 0。

    _DIM_DECL_RE = re.compile(r"^([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)$")

    def _handle_dim(self, decls_text: str, line_no: int) -> None:
        for decl in decls_text.split(","):
            decl = decl.strip()
            if not decl:
                continue
            m = self._DIM_DECL_RE.match(decl)
            if not m:
                self._warn(line_no, f"DIM 声明无法解析: {decl[:60]}")
                continue
            name = m.group(1).upper()
            dims: list[int] = []
            ok = True
            for raw in re.findall(r"\[([^\]]*)\]", m.group(2)):
                raw = raw.strip()
                if not raw:
                    dims.append(0)  # dim arr[]：动态（空）数组
                    continue
                value = self._eval_expr(raw, line_no)
                if value is None:
                    ok = False
                    break
                dims.append(max(0, int(round(float(value)))))
            if not ok:
                continue
            self.env[name] = self._make_array(dims)

    @staticmethod
    def _make_array(dims: list[int]) -> list:
        if not dims:
            return []
        if len(dims) == 1:
            return [0.0] * dims[0]
        return [_PreviewRuntime._make_array(dims[1:]) for _ in range(dims[0])]

    def _handle_array_assign(self, name: str, idx_text: str, rhs: str, line_no: int) -> None:
        key = name.upper()
        indices: list[int] = []
        for raw in re.findall(r"\[([^\]]*)\]", idx_text):
            value = self._eval_expr(raw, line_no)
            if value is None:
                return
            idx = int(round(float(value)))
            if idx < 1:
                self._warn(line_no, f"数组下标越界: {key}[{idx}]，已跳过")
                return
            indices.append(idx)
        if not indices:
            return
        value = self._eval_any(rhs, line_no)
        if value is None:
            return
        arr = self.env.get(key)
        if not isinstance(arr, list):
            arr = []
            self.env[key] = arr
        target = arr
        for idx in indices[:-1]:
            while len(target) < idx:
                target.append(0.0)
            child = target[idx - 1]
            if not isinstance(child, list):
                child = []
                target[idx - 1] = child
            target = child
        last = indices[-1]
        while len(target) < last:
            target.append(0.0)
        target[last - 1] = value

    # ── GROUP 布尔组（P14，预览近似）─────────────────────────────────────

    def _emit_mesh3d(self, mesh: PreviewMesh3D) -> None:
        """mesh 出口：GROUP 定义期间进当前组，否则进结果。"""
        if self._group_stack:
            self._groups[self._group_stack[-1]]["meshes"].append(mesh)
        else:
            self.result_3d.meshes.append(mesh)

    def _emit_wires3d(self, wires: list[list[Point3D]]) -> None:
        if self._group_stack:
            self._groups[self._group_stack[-1]]["wires"].extend(wires)
        else:
            self.result_3d.wires.extend(wires)

    @staticmethod
    def _group_name_key(arg: str) -> str | None:
        text = (arg or "").strip()
        m = re.match(r'^"([^"]*)"$', text)
        if m:
            return m.group(1).upper()
        if re.match(r"^[A-Za-z_]\w*$", text):
            return text.upper()
        return None

    def _resolve_group_ref(self, token: str) -> str | None:
        """组引用解析：引号名 → 组名；裸标识符 → 组变量（ADDGROUP 结果）
        优先，其次按组名。"""
        text = (token or "").strip()
        m = re.match(r'^"([^"]*)"$', text)
        if m:
            return m.group(1).upper()
        if re.match(r"^[A-Za-z_]\w*$", text):
            return self._group_vars.get(text.upper(), text.upper())
        return None

    def _handle_group_command(self, line: str, line_no: int) -> None:
        m = re.match(r"^([A-Za-z_]\w*)\b\s*(.*)$", line)
        if not m:
            return
        cmd = m.group(1).upper()
        arg = (m.group(2) or "").strip()
        if cmd == "GROUP":
            key = self._group_name_key(arg)
            if key is None:
                self._warn(line_no, f"GROUP 名称无法解析，已跳过: {arg[:60]}")
                return
            self._groups[key] = {"meshes": [], "wires": []}
            self._group_stack.append(key)
            return
        if cmd == "ENDGROUP":
            if self._group_stack:
                self._group_stack.pop()
            else:
                self._warn(line_no, "ENDGROUP 没有对应 GROUP，已忽略")
            return
        # PLACEGROUP
        key = self._resolve_group_ref(arg)
        if key is None or key not in self._groups:
            self._warn(line_no, f"PLACEGROUP 引用了未定义的组 `{arg[:60]}`，已跳过")
            return
        self._place_group(key)

    def _group_bool_content(self, func: str, keys: list[str | None]) -> dict[str, list]:
        """组布尔运算内容（预览近似，不做真实 CSG）：

        - ADDGROUP(a, b) = 两算子并集
        - SUBGROUP(a, b) ≈ a + b 并集（减体一并渲染：与 P14 前"组内几何直出"
          的旧行为一致；螺栓孔等减体可见，参数响应检查也能观察到孔阵列随
          hole_count 变化）
        - ISECTGROUP(a, b) ≈ b（门窗标准用法是 ISECTGROUP(洞口容器, 细节)，
          取细节最接近真实交集；取并集会把容器渲染成实心板盖住一切）
        """
        chosen: list[str] = []
        if func in {"ADDGROUP", "SUBGROUP"}:
            chosen = [k for k in keys if k is not None and k in self._groups]
        else:  # ISECTGROUP
            for k in reversed(keys):
                if k is not None and k in self._groups:
                    chosen = [k]
                    break
        content: dict[str, list] = {"meshes": [], "wires": []}
        for k in chosen:
            content["meshes"].extend(self._groups[k]["meshes"])
            content["wires"].extend(self._groups[k]["wires"])
        return content

    def _handle_group_bool_assign(self, name: str, func: str, args_text: str, line_no: int) -> None:
        """g = ADDGROUP/SUBGROUP/ISECTGROUP(...) 赋值形式：结果存入匿名组并
        绑定组变量（语义见 _group_bool_content）。"""
        tokens = [t.strip() for t in _split_args(args_text) if t.strip()]
        keys = [self._resolve_group_ref(t) for t in tokens]
        self._group_anon_seq += 1
        anon = f"__GRP_{self._group_anon_seq}"
        self._groups[anon] = self._group_bool_content(func, keys)
        self._group_vars[name.upper()] = anon

    def _handle_group_bool_statement(self, func: str, args_text: str, line_no: int) -> None:
        """ADDGROUP/SUBGROUP/ISECTGROUP 语句形式（无赋值目标）：GDL 语义为
        结果回写第一个算子组，如 SUBGROUP "beam", "holes" 把孔从梁里减掉。
        """
        tokens = [t.strip() for t in _split_args(args_text) if t.strip()]
        keys = [self._resolve_group_ref(t) for t in tokens]
        if not keys or keys[0] is None or keys[0] not in self._groups:
            self._warn(line_no, f"{func} 的第一个算子不是已定义的组，已跳过")
            return
        first = keys[0]
        self._groups[first] = self._group_bool_content(func, keys)

    def _place_group(self, key: str) -> None:
        """把组内容按当前变换放入结果（拷贝，不共享引用）。"""
        group = self._groups.get(key)
        if group is None:
            return
        for mesh in group["meshes"]:
            placed = PreviewMesh3D(
                name=mesh.name,
                x=[], y=[], z=[],
                i=list(mesh.i), j=list(mesh.j), k=list(mesh.k),
                source_ref=mesh.source_ref,
            )
            for x, y, z in zip(mesh.x, mesh.y, mesh.z):
                px, py, pz = _apply_affine((x, y, z), self._A, self._t)
                placed.x.append(px)
                placed.y.append(py)
                placed.z.append(pz)
            self.result_3d.meshes.append(placed)
        for wire in group["wires"]:
            self.result_3d.wires.append([_apply_affine(p, self._A, self._t) for p in wire])

    def _put_get(self, n: int, line_no: int) -> list[float] | None:
        if n > len(self._put_stack):
            self._warn(line_no, f"GET({n}) 超出 PUT 栈深度 {len(self._put_stack)}，已跳过")
            return None
        if n <= 0:
            return []
        start = len(self._put_stack) - n
        values = self._put_stack[start:]
        del self._put_stack[start:]
        return values

    def _put_use(self, n: int, line_no: int) -> list[float] | None:
        if n > len(self._put_stack):
            self._warn(line_no, f"USE({n}) 超出 PUT 栈深度 {len(self._put_stack)}，已跳过")
            return None
        if n <= 0:
            return []
        return self._put_stack[-n:]

    def _resolve_gosub_target(self, arg_text: str, line_no: int) -> int | None:
        text = (arg_text or "").strip()
        if not text:
            self._warn(line_no, "GOSUB 缺少目标标签")
            return None
        # Numeric label: GOSUB 1000
        if re.match(r"^\d+$", text):
            return self._label_map.get(text)
        # String label: GOSUB "label"
        m = re.match(r'^"([^"]*)"$', text)
        if m:
            return self._label_map.get(m.group(1))
        # Expression resolving to label name
        try:
            value = _safe_eval_expr(text, self.env, funcs=self._funcs, missing_names_zero=True)
            key = str(int(round(float(value))))
            return self._label_map.get(key)
        except Exception:
            return self._label_map.get(text.upper())

    def _source_ref_3d(self, line_no: int, command: str) -> PreviewSourceRef:
        cmd = (command or "").upper()
        segment_start, segment_end = self._current_segment(int(line_no))
        return PreviewSourceRef(
            script_type="3d",
            line=int(line_no),
            command=cmd,
            label=f"3D line {int(line_no)} {cmd}",
            segment_start=segment_start,
            segment_end=segment_end,
        )

    def _current_segment(self, line_no: int) -> tuple[int, int]:
        """命令行的相关代码段：数值上包含该行的最内层区间。

        候选 = 进行中的 IF/FOR 块栈 + 当前 GOSUB 子程序区间。GOSUB 调用点
        可能位于某个 IF/FOR 块内，但 mesh 命令在子程序体内执行、其行号不在
        那个块的区间里，所以必须按"包含命令行"过滤而不是简单取栈顶。
        无候选（顶层命令或区间未知）→ 单行。
        """
        candidates: list[tuple[int, int]] = list(self._block_stack)
        if self._subroutine_stack and self._subroutine_stack[-1] is not None:
            candidates.append(self._subroutine_stack[-1])
        containing = [
            (start, end)
            for (start, end) in candidates
            if start is not None and end is not None and start <= line_no <= end
        ]
        if not containing:
            return (line_no, line_no)
        return max(containing, key=lambda item: item[0])

    def _offset(self) -> Point3D:
        return self._t

    def _p2(self, x: float, y: float) -> Point2D:
        # 2D 仿射变换：_A 的左上 2×2（ROT2/MUL2 的旋转/缩放）+ _t 平移
        # （ADD2）。PROJECT2 投影点同样过 _p2，因此 PROJECT2 前的
        # ADD2/ROT2/MUL2 自动作用于投影结果。
        a11, a12, _ = self._A[0]
        a21, a22, _ = self._A[1]
        ox, oy, _ = self._t
        return (
            float(x) * a11 + float(y) * a12 + ox,
            float(x) * a21 + float(y) * a22 + oy,
        )

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


def _normalize_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in parameters.items():
        name = str(k).upper()
        if isinstance(v, str):
            # P9：保留字符串参数值（如 pattern_type = "直棂"），供字符串比较
            # （_safe_eval_condition 字符串分支）使用；数值路径不受影响。
            out[name] = v
            continue
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


def _extract_label_name(line: str) -> str | None:
    m = re.match(r'^(\d+)\s*:', line)
    if m:
        return m.group(1)
    m = re.match(r'^"([^"]+)"\s*:', line)
    if m:
        return m.group(1)
    return None


def _build_label_map(lines: list[tuple[int, str]]) -> dict[str, int]:
    return {
        name: idx
        for idx, (_, line) in enumerate(lines)
        if (name := _extract_label_name(line)) is not None
    }


def _build_subroutine_extents(lines: list[tuple[int, str]]) -> dict[int, tuple[int, int]]:
    """标签行号 → 子程序区间 (label_line, return_line)。

    标签与 RETURN 按"先进先配"配对：遇到标签压栈，遇到 RETURN 弹出最近未配
    的标签并记录区间。顺序子程序（label…RETURN, label…RETURN）天然正确；
    罕见嵌套标签（前一子程序体内再声明标签）也按栈序配对。
    """
    extents: dict[int, tuple[int, int]] = {}
    pending: list[int] = []
    for line_no, line in lines:
        if _is_label_line(line):
            pending.append(line_no)
        elif pending and re.match(r"^RETURN\b", line, re.IGNORECASE):
            label_line = pending.pop()
            extents[label_line] = (label_line, line_no)
    return extents


def _extract_command(line: str) -> str:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b", line)
    return m.group(1).upper() if m else ""


def _extract_if_condition(line: str) -> str | None:
    m = re.match(r"^IF\s+(.+?)(?:\s+THEN\b.*)?$", line, re.IGNORECASE)
    if not m:
        return None
    condition = (m.group(1) or "").strip()
    return condition or None


def _extract_inline_if(line: str) -> tuple[str, str] | None:
    """Extract one-line GDL IF statements: IF condition THEN statement."""
    m = re.match(r"^IF\s+(.+?)\s+THEN\s+(.+)$", line, re.IGNORECASE)
    if not m:
        return None
    condition = (m.group(1) or "").strip()
    statement = (m.group(2) or "").strip()
    if not condition or not statement:
        return None
    return condition, statement


def _split_inline_else(text: str) -> tuple[str, str] | None:
    """在括号/引号之外找单行 IF 语句部分里的顶层 ELSE 关键字，拆成
    (THEN 语句, ELSE 语句)；无顶层 ELSE 或任一侧为空返回 None。"""
    depth = 0
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and text[i:i + 4].upper() == "ELSE":
            before_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
            after = i + 4
            after_ok = after >= n or not (text[after].isalnum() or text[after] == "_")
            if before_ok and after_ok:
                then_part = text[:i].strip()
                else_part = text[after:].strip()
                if then_part and else_part:
                    return then_part, else_part
                return None
        i += 1
    return None


def _split_colon_statements(text: str) -> list[str]:
    """按 GDL 语句分隔符 `:` 拆分语句列表；`"..."` 字符串字面量内的 `:` 不拆。

    返回去空白后的语句片段列表；无 `:` 时返回 [text]（原样单条，逐字节不变）。
    """
    if not text:
        return []
    parts: list[str] = []
    cur: list[str] = []
    in_string = False
    for ch in text:
        if ch == '"':
            in_string = not in_string
            cur.append(ch)
            continue
        if ch == ":" and not in_string:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


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


def _split_call_args(text: str) -> list[str]:
    """CALL PARAMETERS 列表的拆分：引号（' " `）与括号都感知。

    _split_args 只跟踪括号深度，引号内逗号（如 name="a,b"）会被错拆——
    CALL 解析用这个本地 splitter，_split_args 的全局语义不动（其他命令
    依赖现状）。
    """
    if not text:
        return []
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in text:
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            cur.append(ch)
            continue
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


def _parse_call_statement(
    line: str,
) -> tuple[str, str, list[tuple[str, str]], bool] | None:
    """解析 CALL 语句 → (宏名, 参数模式, [(参数名, 表达式)], 含畸形条目)。

    宏名 = CALL 后第一个引号（单/双/反引号）包裹串，可含中文/空格/任意字符。
    参数模式："none"（无 PARAMETERS 或列表为空，全用宏默认）/ "all"
    （PARAMETERS ALL，大小写不敏感）/ "list"（name = expr 列表，按引号+括号
    感知的 _split_call_args 拆分）。无引号名 → None（调用方发
    MACRO_PARSE_FAIL）。列表中无 `=` 的条目被跳过并计入"含畸形条目"。
    """
    m = re.match(r"""^CALL\s+(['"`])(.*?)\1(.*)$""", line, re.IGNORECASE)
    if not m:
        return None
    name = m.group(2)
    rest = (m.group(3) or "").strip()
    if not rest:
        return name, "none", [], False
    m_params = re.match(r"^PARAMETERS\b(.*)$", rest, re.IGNORECASE)
    if not m_params:
        return None  # 宏名后跟了非 PARAMETERS 的内容，按解析失败处理
    params_text = (m_params.group(1) or "").strip()
    if not params_text:
        return name, "none", [], False
    if params_text.upper() == "ALL":
        return name, "all", [], False
    entries: list[tuple[str, str]] = []
    malformed = False
    for part in _split_call_args(params_text):
        pname, sep, expr = part.partition("=")
        pname = pname.strip()
        if not sep or not pname:
            malformed = True
            continue
        entries.append((pname, expr.strip()))
    return name, "list", entries, malformed


def _extract_points_2d(values: list[float], n: int) -> list[Point2D] | None:
    if n <= 0:
        return None

    # Prefer triplets for PRISM_/POLY2 variants with edge-status:
    # n, h, x1,y1,s1, x2,y2,s2, ...
    if len(values) >= 3 * n:
        pairs = [(float(values[3 * i]), float(values[3 * i + 1])) for i in range(n)]
        return pairs

    # Fallback to plain x,y pairs.
    if len(values) >= 2 * n:
        pairs = [(float(values[2 * i]), float(values[2 * i + 1])) for i in range(n)]
        return pairs

    return None


def _dist2(a: Point2D, b: Point2D) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _arc_tessellate(
    cx: float, cy: float, r: float, a0_deg: float, sweep_deg: float
) -> list[Point2D]:
    """圆弧离散为点列（含两端）：约每 10° 一段，每弧最少 4 点、最多 36 点。

    退化（半径≈0 或扫角≈0）返回空列表，由调用方决定回退。
    """
    if r <= 1e-12 or abs(sweep_deg) <= 1e-9:
        return []
    steps = max(3, min(35, int(math.ceil(abs(sweep_deg) / 10.0))))
    pts: list[Point2D] = []
    for i in range(steps + 1):
        a = math.radians(a0_deg + sweep_deg * i / steps)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _tangent_arc_points(
    start: Point2D, tangent: Point2D, end: Point2D
) -> list[Point2D] | None:
    """过 start、切线方向 tangent、到 end 的圆弧（GDL 切线弧，状态码 1000）。

    圆心在 start 处切线的法线上：C = start + n·rs（n 为切线左法向单位向量），
    由 |C - end| = |rs| 解出带符号半径 rs；rs > 0 圆心在行进方向左侧 → 逆时针。
    退化（零切线/端点在切线上）返回 None，调用方退化为直线。
    """
    tx, ty = tangent
    tlen = math.hypot(tx, ty)
    if tlen <= 1e-12:
        return None
    nx, ny = -ty / tlen, tx / tlen  # 左法向
    dx, dy = end[0] - start[0], end[1] - start[1]
    denom = 2.0 * (nx * dx + ny * dy)
    if abs(denom) <= 1e-12:
        return None
    rs = (dx * dx + dy * dy) / denom
    r = abs(rs)
    if r <= 1e-12:
        return None
    cx, cy = start[0] + nx * rs, start[1] + ny * rs
    a0 = math.degrees(math.atan2(start[1] - cy, start[0] - cx))
    a1 = math.degrees(math.atan2(end[1] - cy, end[0] - cx))
    sweep = (a1 - a0) % 360.0 if rs > 0 else -((a0 - a1) % 360.0)
    return _arc_tessellate(cx, cy, r, a0, sweep)


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


# ── 多边形盖帽三角化：凸→旧扇形 / 凹→耳切（P3c）────────────
# 自包含、零第三方依赖（AGENTS.md 规则 8）。凸轮廓保持旧扇形逐字节不变；
# 凹轮廓改用耳切三角化；退化轮廓（重复顶点/共线/自交）警告 + 回退旧扇形。

_POLY_EPS = 1e-9


def _orient2(a: Point2D, b: Point2D, c: Point2D) -> int:
    """2D orientation of (a, b, c): 1 = CCW, -1 = CW, 0 = collinear."""
    val = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(val) < _POLY_EPS:
        return 0
    return 1 if val > 0 else -1


def _on_segment2(a: Point2D, b: Point2D, p: Point2D) -> bool:
    return (
        min(a[0], b[0]) - _POLY_EPS <= p[0] <= max(a[0], b[0]) + _POLY_EPS
        and min(a[1], b[1]) - _POLY_EPS <= p[1] <= max(a[1], b[1]) + _POLY_EPS
    )


def _segments_intersect2(
    p1: Point2D, p2: Point2D, p3: Point2D, p4: Point2D
) -> bool:
    """True when segments (p1,p2) and (p3,p4) cross or touch (incl. collinear
    overlap)."""
    d1 = _orient2(p3, p4, p1)
    d2 = _orient2(p3, p4, p2)
    d3 = _orient2(p1, p2, p3)
    d4 = _orient2(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    if d1 == 0 and _on_segment2(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment2(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment2(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment2(p1, p2, p4):
        return True
    return False


def _polygon_self_intersects2(points: list[Point2D]) -> bool:
    """O(n²) self-intersection / self-touch detection for a closed contour."""
    n = len(points)
    for i in range(n):
        a1, a2 = points[i], points[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue  # adjacent edges share an endpoint by construction
            if _segments_intersect2(a1, a2, points[j], points[(j + 1) % n]):
                return True
    return False


def _polygon_signed_area2(points: list[Point2D]) -> float:
    """Signed shoelace area (positive = CCW in standard math coordinates)."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _classify_polygon2(points: list[Point2D]) -> str:
    """Classify a 2D contour.

    Returns 'convex' (strictly convex: every adjacent-edge cross product has
    the same sign and none is zero), 'concave' (simple polygon with at least
    one reflex vertex), or 'degenerate' (duplicate vertex, collinear vertex,
    self-intersecting contour, or zero area).
    """
    n = len(points)
    if n < 3:
        return "degenerate"

    # 重复顶点（相邻，含首尾闭合重复）→ 退化
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        if abs(a[0] - b[0]) <= _POLY_EPS and abs(a[1] - b[1]) <= _POLY_EPS:
            return "degenerate"

    # 共线顶点（任一相邻边叉积为零）→ 退化
    crosses: list[float] = []
    for i in range(n):
        a, b, c = points[i], points[(i + 1) % n], points[(i + 2) % n]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) <= _POLY_EPS:
            return "degenerate"
        crosses.append(cross)

    if abs(_polygon_signed_area2(points)) <= _POLY_EPS:
        return "degenerate"

    if _polygon_self_intersects2(points):
        return "degenerate"

    signs = {1 if c > 0 else -1 for c in crosses}
    if len(signs) == 1:
        return "convex"
    return "concave"


def _point_in_triangle2(
    p0: Point2D, p1: Point2D, p2: Point2D, p: Point2D
) -> bool:
    """True when p lies inside the triangle or on its boundary (conservative:
    boundary counts as blocking for the ear test)."""
    d1 = _orient2(p0, p1, p)
    d2 = _orient2(p1, p2, p)
    d3 = _orient2(p2, p0, p)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _triangulate_polygon(
    points: list[Point2D],
) -> list[tuple[int, int, int]] | None:
    """Ear-clipping triangulation of a simple polygon (O(n²), fine for GDL
    contour sizes). Returns index triples into ``points`` in CCW coordinate
    order, or None when the polygon cannot be triangulated."""
    n = len(points)
    if n < 3:
        return None
    if abs(_polygon_signed_area2(points)) <= _POLY_EPS:
        return None

    remaining = list(range(n))
    if _polygon_signed_area2(points) < 0:
        remaining.reverse()  # walk CCW in coordinate space

    triangles: list[tuple[int, int, int]] = []
    guard = 0
    max_guard = 4 * n * n + 128
    while len(remaining) > 3:
        guard += 1
        if guard > max_guard:
            return None
        m = len(remaining)
        clipped = False
        for pos in range(m):
            i0 = remaining[(pos - 1) % m]
            i1 = remaining[pos]
            i2 = remaining[(pos + 1) % m]
            p0, p1, p2 = points[i0], points[i1], points[i2]
            if _orient2(p0, p1, p2) <= 0:
                continue  # reflex or collinear → not an ear
            blocked = False
            for idx in remaining:
                if idx in (i0, i1, i2):
                    continue
                q = points[idx]
                # P14：洞桥接轮廓含坐标重合的缝顶点（不同索引）——与三角形
                # 顶点重合的点不算阻挡，否则桥附近的耳永远剪不掉。对无重合
                # 点的旧输入这是 no-op，行为逐字节不变。
                if (
                    (abs(q[0] - p0[0]) <= _POLY_EPS and abs(q[1] - p0[1]) <= _POLY_EPS)
                    or (abs(q[0] - p1[0]) <= _POLY_EPS and abs(q[1] - p1[1]) <= _POLY_EPS)
                    or (abs(q[0] - p2[0]) <= _POLY_EPS and abs(q[1] - p2[1]) <= _POLY_EPS)
                ):
                    continue
                if _point_in_triangle2(p0, p1, p2, q):
                    blocked = True
                    break
            if blocked:
                continue
            triangles.append((i0, i1, i2))
            remaining.pop(pos)
            clipped = True
            break
        if not clipped:
            return None  # numerical pathology → caller falls back to fan
    triangles.append((remaining[0], remaining[1], remaining[2]))
    return triangles


def _plan_cap_triangulation(
    points2d: list[Point2D],
) -> tuple[bool, list[tuple[int, int, int]], bool]:
    """Decide how to triangulate a cap contour.

    Returns (use_earclip, triples, degraded):
    - use_earclip=True: triples are CCW index triples into points2d.
    - use_earclip=False: caller must keep the old fan; degraded=True means a
      warning should be emitted (duplicate/collinear/self-intersecting contour
      or the ear clipping itself failed).
    """
    kind = _classify_polygon2(points2d)
    if kind == "convex":
        return False, [], False
    if kind == "concave":
        tris = _triangulate_polygon(points2d)
        if tris is not None:
            return True, tris, False
    return False, [], True


def _project_polygon_to_plane(
    points3d: list[Point3D],
) -> list[Point2D] | None:
    """Project a (near-planar) 3D polygon onto its Newell best-fit plane.
    Returns 2D coordinates, or None when the polygon has no definable plane
    (zero area / collinear / too few points)."""
    n = len(points3d)
    if n < 3:
        return None
    nx = ny = nz = 0.0
    for i in range(n):
        p1 = points3d[i]
        p2 = points3d[(i + 1) % n]
        nx += (p1[1] - p2[1]) * (p1[2] + p2[2])
        ny += (p1[2] - p2[2]) * (p1[0] + p2[0])
        nz += (p1[0] - p2[0]) * (p1[1] + p2[1])
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= _POLY_EPS:
        return None
    nx, ny, nz = nx / length, ny / length, nz / length
    if abs(nx) < abs(ny) and abs(nx) < abs(nz):
        u = (0.0, -nz, ny)
    elif abs(ny) < abs(nz):
        u = (nz, 0.0, -nx)
    else:
        u = (-ny, nx, 0.0)
    ul = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
    if ul <= _POLY_EPS:
        return None
    u = (u[0] / ul, u[1] / ul, u[2] / ul)
    v = (ny * u[2] - nz * u[1], nz * u[0] - nx * u[2], nx * u[1] - ny * u[0])
    return [
        (p[0] * u[0] + p[1] * u[1] + p[2] * u[2], p[0] * v[0] + p[1] * v[1] + p[2] * v[2])
        for p in points3d
    ]


def _make_ruled_mesh(
    base: list[Point3D],
    top: list[Point3D],
    offset: Point3D,
    *,
    closed: bool = True,
    cap_base: bool = False,
    cap_top: bool = False,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    source_ref: PreviewSourceRef | None = None,
    warn: Callable[[str], None] | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    """Ruled surface between two polylines of equal length (GDL RULED).

    ``base`` nodes are given in the local x-y plane (z=0), ``top`` nodes
    are a space curve.  Quads connect corresponding nodes; caps (when the
    RULED mask requests them) are centroid fans on convex contours and
    ear-clipped on concave contours (P3c).
    """
    A = transform or _identity3()
    n = len(base)

    verts = [_apply_affine(p, A, offset) for p in base + top]

    faces: list[tuple[int, int, int]] = []
    span = n if closed else n - 1
    for i in range(span):
        j = (i + 1) % n
        b1, b2 = i, j
        t1, t2 = n + i, n + j
        faces.append((b1, b2, t2))
        faces.append((b1, t2, t1))

    # 盖帽三角化计划：凸→旧扇形；凹→耳切（base 在 z=0 平面直接取 (x,y)；
    # top 是空间曲线，投影到 Newell 拟合平面再判）。
    cap_base_earclip, cap_base_tris, cap_base_degraded = (False, [], False)
    if cap_base:
        cap_base_earclip, cap_base_tris, cap_base_degraded = _plan_cap_triangulation(
            [(p[0], p[1]) for p in base]
        )
    cap_top_earclip, cap_top_tris, cap_top_degraded = (False, [], False)
    if cap_top:
        top2d = _project_polygon_to_plane(top)
        if top2d is not None:
            cap_top_earclip, cap_top_tris, cap_top_degraded = _plan_cap_triangulation(
                top2d
            )
        else:
            cap_top_degraded = True

    if cap_base:
        if cap_base_earclip:
            for a, b, c in cap_base_tris:
                faces.append((a, c, b))  # 底盖：法向朝下（与旧扇形一致）
        else:
            cx = sum(p[0] for p in base) / n
            cy = sum(p[1] for p in base) / n
            cz = sum(p[2] for p in base) / n
            verts.append(_apply_affine((cx, cy, cz), A, offset))
            c = len(verts) - 1
            for i in range(span):
                j = (i + 1) % n
                faces.append((c, j, i))
            if cap_base_degraded and warn is not None:
                warn("RULED 底面盖帽轮廓退化（重复顶点/共线/自交），回退扇形三角化")

    if cap_top:
        if cap_top_earclip:
            for a, b, c in cap_top_tris:
                faces.append((n + a, n + b, n + c))  # 顶盖：法向朝上
        else:
            cx = sum(p[0] for p in top) / n
            cy = sum(p[1] for p in top) / n
            cz = sum(p[2] for p in top) / n
            verts.append(_apply_affine((cx, cy, cz), A, offset))
            c = len(verts) - 1
            for i in range(span):
                j = (i + 1) % n
                faces.append((c, n + i, n + j))
            if cap_top_degraded and warn is not None:
                warn("RULED 顶面盖帽轮廓退化（重复顶点/共线/自交），回退扇形三角化")

    wires: list[list[Point3D]] = []
    base_loop = [verts[i] for i in range(n)]
    top_loop = [verts[n + i] for i in range(n)]
    if closed:
        base_loop = base_loop + [base_loop[0]]
        top_loop = top_loop + [top_loop[0]]
    wires.append(base_loop)
    wires.append(top_loop)

    return _build_mesh("RULED", verts, faces, source_ref=source_ref), wires


def _make_prism_mesh(
    points: list[Point2D],
    h: float,
    offset: Point3D,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    name: str = "PRISM_",
    source_ref: PreviewSourceRef | None = None,
    warn: Callable[[str], None] | None = None,
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

    # 盖帽：凸轮廓保持旧扇形逐字节不变；凹轮廓耳切（P3c）。
    use_earclip, tris, degraded = _plan_cap_triangulation(points)
    if use_earclip:
        for a, b, c in tris:
            faces.append((a, c, b))  # 底盖：法向朝下
            faces.append((n + a, n + b, n + c))  # 顶盖：法向朝上
    else:
        # Bottom fan
        for i in range(1, n - 1):
            faces.append((0, i + 1, i))

        # Top fan
        for i in range(1, n - 1):
            faces.append((n, n + i, n + i + 1))
        if degraded and warn is not None:
            warn(f"{name} 轮廓退化（重复顶点/共线/自交），盖帽回退扇形三角化")

    wires: list[list[Point3D]] = []
    wires.append(base + [base[0]])
    wires.append(top + [top[0]])
    for i in range(n):
        wires.append([base[i], top[i]])

    return _build_mesh(name, verts, faces, source_ref=source_ref), wires


def _dedupe_ring(points: list[Point2D]) -> list[Point2D]:
    """去掉相邻（含首尾环绕）坐标重合点。lowall=0 这类参数化脚本退化时
    轮廓自带重复点，零长边在 GDL 中是 no-op。无重复点时行为不变。"""
    out: list[Point2D] = []
    for p in points:
        if out and abs(p[0] - out[-1][0]) <= _POLY_EPS and abs(p[1] - out[-1][1]) <= _POLY_EPS:
            continue
        out.append(p)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= _POLY_EPS and abs(out[0][1] - out[-1][1]) <= _POLY_EPS:
        out.pop()
    return out


def _split_prism_contours(values: list[float], n: int) -> list[list[Point2D]] | None:
    """PRISM_ 三元组节点拆轮廓：状态码 -1 = 当前轮廓结束（其坐标通常与
    该轮廓首点重合）。非三元组布局或不含 -1 返回 None（调用方走旧无洞
    路径，行为逐字节不变）。返回 [外轮廓, 洞1, ...]，轮廓不含重复闭合点。
    """
    if len(values) < 3 * n:
        return None
    statuses = [int(round(float(values[3 * i + 2]))) for i in range(n)]
    if -1 not in statuses:
        return None
    contours: list[list[Point2D]] = []
    current: list[Point2D] = []
    for i in range(n):
        x = float(values[3 * i])
        y = float(values[3 * i + 1])
        if statuses[i] == -1:
            if current and (
                abs(x - current[0][0]) > _POLY_EPS or abs(y - current[0][1]) > _POLY_EPS
            ):
                current.append((x, y))
            current = _dedupe_ring(current)
            if len(current) >= 3:
                contours.append(current)
            current = []
        else:
            current.append((x, y))
    current = _dedupe_ring(current)
    if len(current) >= 3:
        contours.append(current)
    return contours or None


def _segment_clear(
    h: Point2D,
    hvid: int,
    v: Point2D,
    vvid: int,
    merged: list[tuple[Point2D, int]],
    hole: list[tuple[Point2D, int]],
) -> bool:
    """桥接线段 H-V 是否与任何环边相交（跳过与 H/V 关联的边）。"""
    for ring in (merged, hole):
        m = len(ring)
        for e in range(m):
            a, aid = ring[e]
            b, bid = ring[(e + 1) % m]
            if hvid in (aid, bid) or vvid in (aid, bid):
                continue
            if _segments_intersect2(h, v, a, b):
                return False
    return True


def _splice_holes(
    outer: list[Point2D], holes: list[list[Point2D]]
) -> list[tuple[Point2D, int]] | None:
    """把洞轮廓桥接进外轮廓，得到可被简单多边形耳切的单一带缝轮廓。

    返回 [(点, 底环顶点 id)]；顶点 id 与 _make_prism_mesh_with_holes 的底环
    编号一致（外轮廓 0..n0-1，洞依次接续）。每个洞取最右点 H，在已合并
    轮廓上找与 H 线段不相交的最近顶点 V 做桥；H 与 V 在结果中各出现两次
    （缝）。找不到安全桥返回 None（调用方回退）。
    """
    merged: list[tuple[Point2D, int]] = [(p, i) for i, p in enumerate(outer)]
    next_id = len(outer)
    for hole in holes:
        if len(hole) < 3:
            continue
        hole_items = [(p, next_id + i) for i, p in enumerate(hole)]
        next_id += len(hole)
        # 最右点（x 最大，并列取 y 最小）
        hi = max(
            range(len(hole_items)),
            key=lambda i: (hole_items[i][0][0], -hole_items[i][0][1]),
        )
        h_pt, h_vid = hole_items[hi]
        # 洞的绕行方向须与外轮廓相反
        hole_seq = [hole_items[(hi + k) % len(hole_items)] for k in range(len(hole_items))]
        if _polygon_signed_area2(outer) * _polygon_signed_area2([p for p, _ in hole_seq]) > 0:
            hole_seq = [hole_items[hi]] + [
                hole_items[(hi - k) % len(hole_items)] for k in range(1, len(hole_items))
            ]
        candidates = sorted(
            range(len(merged)),
            key=lambda i: (merged[i][0][0] - h_pt[0]) ** 2 + (merged[i][0][1] - h_pt[1]) ** 2,
        )
        bridge = None
        for ci in candidates:
            v_pt, v_vid = merged[ci]
            if _segment_clear(h_pt, h_vid, v_pt, v_vid, merged, hole_items):
                bridge = ci
                break
        if bridge is None:
            return None
        v_pt, v_vid = merged[bridge]
        merged = (
            merged[: bridge + 1]
            + hole_seq
            + [(h_pt, h_vid), (v_pt, v_vid)]
            + merged[bridge + 1 :]
        )
    return merged


def _make_prism_mesh_with_holes(
    outer: list[Point2D],
    holes: list[list[Point2D]],
    h: float,
    offset: Point3D,
    *,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    name: str = "PRISM_",
    source_ref: PreviewSourceRef | None = None,
    warn: Callable[[str], None] | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    """带洞棱柱（P14）：每个轮廓环（外轮廓 + 各洞）独立侧壁；盖帽把洞
    桥接进外轮廓后耳切。桥接/耳切失败回退外轮廓盖帽（洞被盖住）并告警。
    """
    A = transform or _identity3()
    rings = [outer] + holes
    ring_starts: list[int] = []
    s = 0
    for ring in rings:
        ring_starts.append(s)
        s += len(ring)
    n_base = s

    verts: list[Point3D] = []
    for ring in rings:
        verts.extend(_apply_affine((x, y, 0.0), A, offset) for x, y in ring)
    for ring in rings:
        verts.extend(_apply_affine((x, y, h), A, offset) for x, y in ring)

    faces: list[tuple[int, int, int]] = []
    # 侧壁：外轮廓与洞壁各自成环
    for rs, ring in zip(ring_starts, rings):
        size = len(ring)
        for i in range(size):
            j = (i + 1) % size
            bi, bj = rs + i, rs + j
            ti, tj = n_base + rs + i, n_base + rs + j
            faces.append((bi, bj, tj))
            faces.append((bi, tj, ti))

    # 盖帽：洞桥接 + 耳切
    cap_done = False
    merged = _splice_holes(outer, holes)
    if merged is not None:
        pts = [p for p, _ in merged]
        tris = _triangulate_polygon(pts)
        if tris:
            for a, b, c in tris:
                va, vb, vc = merged[a][1], merged[b][1], merged[c][1]
                if va == vb or vb == vc or va == vc:
                    continue  # 桥缝退化三角形
                faces.append((va, vc, vb))  # 底盖：法向朝下
                faces.append((n_base + va, n_base + vb, n_base + vc))  # 顶盖
            cap_done = True
    if not cap_done:
        if warn is not None:
            warn(f"{name} 含洞轮廓盖帽三角化失败，回退外轮廓盖帽（洞被盖住）")
        n0 = len(outer)
        use_earclip, tris0, _ = _plan_cap_triangulation(outer)
        if use_earclip:
            for a, b, c in tris0:
                faces.append((a, c, b))
                faces.append((n_base + a, n_base + b, n_base + c))
        else:
            for i in range(1, n0 - 1):
                faces.append((0, i + 1, i))
                faces.append((n_base, n_base + i, n_base + i + 1))

    wires: list[list[Point3D]] = []
    for rs, ring in zip(ring_starts, rings):
        size = len(ring)
        base_loop = [verts[rs + i] for i in range(size)]
        top_loop = [verts[n_base + rs + i] for i in range(size)]
        wires.append(base_loop + [base_loop[0]])
        wires.append(top_loop + [top_loop[0]])
        for i in range(size):
            wires.append([verts[rs + i], verts[n_base + rs + i]])

    return _build_mesh(name, verts, faces, source_ref=source_ref), wires


def _make_tube_mesh(
    path: list[Point3D],
    section: list[Point2D],
    offset: Point3D,
    *,
    mask: int = 127,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    source_ref: PreviewSourceRef | None = None,
    warn: Callable[[str], None] | None = None,
) -> tuple[PreviewMesh3D, list[list[Point3D]]]:
    """Sweep a 2D section along a 3D path (GDL TUBE_).

    ``path`` contains n 3D nodes. ``section`` contains m (u, v) points
    describing the cross-section in the section's local plane.  Consecutive
    section rings are connected by quads; caps are added when the mask bits
    request them (j1 = start cap, j2 = end cap).
    """
    A = transform or _identity3()
    n_path = len(path)
    n_sec = len(section)

    def _normalize(v: Point3D) -> Point3D:
        length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if length < 1e-12:
            return (0.0, 0.0, 1.0)
        return (v[0] / length, v[1] / length, v[2] / length)

    def _cross(a: Point3D, b: Point3D) -> Point3D:
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def _frame(tangent: Point3D) -> tuple[Point3D, Point3D]:
        tx, ty, tz = tangent
        if abs(tz) < 0.99:
            up = (0.0, 0.0, 1.0)
        else:
            up = (0.0, 1.0, 0.0)
        ux, uy, uz = up
        # x_axis = normalize(up - dot(up, tangent) * tangent)
        dot = ux * tx + uy * ty + uz * tz
        x_axis = _normalize((ux - dot * tx, uy - dot * ty, uz - dot * tz))
        y_axis = _cross(tangent, x_axis)
        return x_axis, y_axis

    # Compute tangents at each path point.
    tangents: list[Point3D] = []
    for i in range(n_path):
        if i == 0:
            t = _normalize((path[1][0] - path[0][0], path[1][1] - path[0][1], path[1][2] - path[0][2]))
        elif i == n_path - 1:
            t = _normalize((path[-1][0] - path[-2][0], path[-1][1] - path[-2][1], path[-1][2] - path[-2][2]))
        else:
            t = _normalize((path[i + 1][0] - path[i - 1][0], path[i + 1][1] - path[i - 1][1], path[i + 1][2] - path[i - 1][2]))
        tangents.append(t)

    verts_local: list[Point3D] = []
    for p, tangent in zip(path, tangents):
        x_axis, y_axis = _frame(tangent)
        for u, v in section:
            px = p[0] + u * x_axis[0] + v * y_axis[0]
            py = p[1] + u * x_axis[1] + v * y_axis[1]
            pz = p[2] + u * x_axis[2] + v * y_axis[2]
            verts_local.append((px, py, pz))

    verts = [_apply_affine(p, A, offset) for p in verts_local]

    faces: list[tuple[int, int, int]] = []
    for i in range(n_path - 1):
        for j in range(n_sec):
            j_next = (j + 1) % n_sec
            a = i * n_sec + j
            b = i * n_sec + j_next
            c = (i + 1) * n_sec + j_next
            d = (i + 1) * n_sec + j
            faces.append((a, b, c))
            faces.append((a, c, d))

    # 盖帽三角化计划：凸→旧质心扇形；凹→耳切（section 是局部 2D 轮廓，直接
    # 在 (u,v) 空间判定，索引映射回截面环顶点）。
    use_earclip, tris, degraded = _plan_cap_triangulation(section)

    # Start cap (mask bit 1)
    if mask & 1 and n_sec >= 3:
        if use_earclip:
            for a, b, c in tris:
                faces.append((a, c, b))  # 起始盖：与旧扇形同向（法向朝内）
        else:
            cx = sum(verts_local[j][0] for j in range(n_sec)) / n_sec
            cy = sum(verts_local[j][1] for j in range(n_sec)) / n_sec
            cz = sum(verts_local[j][2] for j in range(n_sec)) / n_sec
            verts.append(_apply_affine((cx, cy, cz), A, offset))
            c = len(verts) - 1
            for j in range(n_sec):
                j_next = (j + 1) % n_sec
                faces.append((c, j_next, j))

    # End cap (mask bit 2)
    base = (n_path - 1) * n_sec
    if mask & 2 and n_sec >= 3:
        if use_earclip:
            for a, b, c in tris:
                faces.append((base + a, base + b, base + c))
        else:
            cx = sum(verts_local[base + j][0] for j in range(n_sec)) / n_sec
            cy = sum(verts_local[base + j][1] for j in range(n_sec)) / n_sec
            cz = sum(verts_local[base + j][2] for j in range(n_sec)) / n_sec
            verts.append(_apply_affine((cx, cy, cz), A, offset))
            c = len(verts) - 1
            for j in range(n_sec):
                j_next = (j + 1) % n_sec
                faces.append((c, base + j, base + j_next))

    if degraded and warn is not None and (mask & 1 or mask & 2) and n_sec >= 3:
        warn("TUBE 截面轮廓退化（重复顶点/共线/自交），盖帽回退扇形三角化")

    wires: list[list[Point3D]] = []
    wires.append([verts[j] for j in range(n_sec)] + [verts[0]])
    wires.append([verts[(n_path - 1) * n_sec + j] for j in range(n_sec)] + [verts[(n_path - 1) * n_sec]])
    wires.append([verts[i * n_sec] for i in range(n_path)])

    return _build_mesh("TUBE_", verts, faces, source_ref=source_ref), wires


_ALLOWED_FUNCS = {
    "ABS": lambda x: abs(x),
    "SQRT": lambda x: math.sqrt(x),
    # P9：GDL 平方根函数名是 SQR（Archicad 合法脚本用 SQR，如菱花对角线计算）。
    "SQR": lambda x: math.sqrt(x),
    "SIN": lambda x: math.sin(math.radians(x)),
    "COS": lambda x: math.cos(math.radians(x)),
    "TAN": lambda x: math.tan(math.radians(x)),
    "INT": lambda x: float(int(x)),
    "ROUND": lambda x: float(round(x)),
    "MIN": lambda *x: min(x),
    "MAX": lambda *x: max(x),
    # P14：GDL NOT 的函数形态 not(x)（_translate_gdl_expr 译为大写调用）
    "NOT": lambda x: 0.0 if abs(float(x)) > 1e-12 else 1.0,
}


def _translate_gdl_expr(text: str) -> str:
    """把 GDL 表达式表面语法翻译成 Python ast 可解析的形式（P14）。

    引号/反引号字符串内容原样保留（两端统一转成 `"`），只在引号外替换：
    - 反引号原样字符串 `` `...` `` → "..."
    - AND/OR/NOT/EXOR 关键字（任意大小写）→ and/or/not / !=
    - `<>` → `!=`；`!=` 保留；单个 `=` → `==`（GDL 表达式里 = 即相等比较）
    - 单个 `|` → or；单个 `&` → and
    - `^` → `**`
    """
    out: list[str] = []
    i = 0
    n = len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                out.append('"')
                quote = None
            else:
                out.append(ch)
            i += 1
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            out.append('"')
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            upper = word.upper()
            if upper == "AND":
                out.append(" and ")
            elif upper == "OR":
                out.append(" or ")
            elif upper == "NOT":
                # not(x) 函数形态（GDL 常用）→ NOT( 函数调用（Python 的 not
                # 关键字优先级太低，`a * not(x)` 是语法错误）；NOT x 关键词
                # 形态仍译为 not。
                k = j
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == "(":
                    out.append("NOT")
                else:
                    out.append(" not ")
            elif upper == "EXOR":
                out.append(" != ")
            else:
                out.append(word)
            i = j
            continue
        if ch == "<" and i + 1 < n and text[i + 1] == ">":
            out.append("!=")
            i += 2
            continue
        if ch == "!" and i + 1 < n and text[i + 1] == "=":
            out.append("!=")
            i += 2
            continue
        if ch in "<>" and i + 1 < n and text[i + 1] == "=":
            out.append(ch + "=")
            i += 2
            continue
        if ch == "=":
            out.append("==")
            i += 2 if i + 1 < n and text[i + 1] == "=" else 1
            continue
        if ch == "|":
            out.append(" or ")
            i += 1
            continue
        if ch == "&":
            out.append(" and ")
            i += 1
            continue
        if ch == "^":
            out.append("**")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _safe_eval_any(
    expr: str,
    env: dict[str, Any],
    *,
    funcs: dict[str, Any] | None = None,
    missing_names_zero: bool = False,
) -> float | str:
    """数值/字符串表达式求值：结果为 str 时原样返回，否则返回 float。"""
    node = ast.parse(_translate_gdl_expr(expr.strip()).strip(), mode="eval")
    value = _eval_ast(node.body, env, funcs=funcs, missing_names_zero=missing_names_zero)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        raise ValueError("表达式结果是数组")
    return float(value)


def _safe_eval_expr(
    expr: str,
    env: dict[str, float],
    *,
    funcs: dict[str, Any] | None = None,
    missing_names_zero: bool = False,
) -> float:
    """Evaluate numeric expression with a very small safe AST subset."""
    value = _safe_eval_any(expr, env, funcs=funcs, missing_names_zero=missing_names_zero)
    if isinstance(value, str):
        raise ValueError("表达式结果是字符串")
    return float(value)


def _string_value(src: str, env: dict[str, Any], *, funcs: dict[str, Any] | None = None) -> str | None:
    """Interpret a condition operand as a string: quoted/backtick literal,
    a string-valued env name, or a string-valued array element (name[i] /
    name[i][j]). Returns None when it cannot be a string."""
    s = (src or "").strip()
    if len(s) >= 2 and s[0] == "`" and s[-1] == "`":
        # GDL 反引号原样字符串（LP_XMLConverter 常用，如 `关`）
        return s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        return s[1:-1]
    if re.match(r"^[A-Za-z_]\w*$", s):
        value = env.get(s.upper())
        if isinstance(value, str):
            return value
        return None
    if re.match(r"^[A-Za-z_]\w*(?:\[[^\]]+\])+$", s):
        try:
            value = _safe_eval_any(s, env, funcs=funcs)
        except Exception:
            return None
        return value if isinstance(value, str) else None
    return None


def _strip_outer_parens(text: str) -> str:
    """去掉包裹整个表达式的冗余外层括号（可多层）。引号/反引号内的括号
    不计深度；括号未闭合或未包裹整个表达式时返回原文。"""
    s = text.strip()
    while len(s) >= 2 and s[0] == "(":
        depth = 0
        quote: str | None = None
        closes_at_end = False
        balanced = False
        for i, ch in enumerate(s):
            if quote is not None:
                if ch == quote:
                    quote = None
                continue
            if ch in {'"', "'", "`"}:
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = i == len(s) - 1
                    balanced = True
                    break
                if depth < 0:
                    break
        if not balanced or not closes_at_end:
            break
        s = s[1:-1].strip()
    return s


def _split_logical_top(text: str, kind: str) -> list[str] | None:
    """在括号/引号之外按逻辑运算符切分条件文本。

    kind="or" 匹配 OR 关键字与单个 `|`；kind="and" 匹配 AND 与单个 `&`。
    无顶层运算符返回 None；切出空片段（如 `a |`）视为非法，同样返回 None
    （交回上层按整体求值，保留旧的报错路径）。
    """
    word = "OR" if kind == "or" else "AND"
    char = "|" if kind == "or" else "&"
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            i += 1
            continue
        if ch in "([":
            depth += 1
            i += 1
            continue
        if ch in ")]":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if ch == char:
                parts.append(text[start:i])
                i += 1
                start = i
                continue
            if text[i:i + len(word)].upper() == word:
                before_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
                after = i + len(word)
                after_ok = after >= n or not (text[after].isalnum() or text[after] == "_")
                if before_ok and after_ok:
                    parts.append(text[start:i])
                    i = after
                    start = after
                    continue
        i += 1
    if not parts:
        return None
    parts.append(text[start:])
    stripped = [part.strip() for part in parts]
    if any(not part for part in stripped):
        return None
    return stripped


def _safe_eval_condition(
    condition: str,
    env: dict[str, Any],
    *,
    funcs: dict[str, Any] | None = None,
) -> bool:
    text = _strip_outer_parens((condition or "").strip())
    if not text:
        raise ValueError("空条件")

    # GDL 条件常用逻辑组合。AND/OR 拆分必须括号感知（P14）：旧实现用
    # 正则 \bOR\b 直接切，`(a=4 or b=24) and c` 会在括号内的 or 处切碎，
    # 导致整个 IF 块（含 ELSE）被跳过。OR 优先级低于 AND（先拆 OR）。
    # 单个 `|` / `&` 视同 OR / AND（GDL 允许）。
    parts = _split_logical_top(text, "or")
    if parts is not None:
        values = [_safe_eval_condition(part, env, funcs=funcs) for part in parts]
        return any(values)
    parts = _split_logical_top(text, "and")
    if parts is not None:
        values = [_safe_eval_condition(part, env, funcs=funcs) for part in parts]
        return all(values)

    # P9：前级 NOT（逻辑优先级最高）。AND/OR 已在上面拆分，因此这里 NOT
    # 只作用于自己的操作数：`NOT a AND b` → `(NOT a) AND (b)`。
    m_not = re.match(r"^NOT\b\s*(.+)$", text, re.IGNORECASE)
    if m_not:
        return not _safe_eval_condition(m_not.group(1).strip(), env, funcs=funcs)

    m = re.match(r"^(.+?)\s*(<=|>=|<>|#|=|<|>)\s*(.+)$", text)
    if not m:
        return abs(_safe_eval_expr(text, env, funcs=funcs, missing_names_zero=True)) > 1e-12

    left_src, op, right_src = m.group(1).strip(), m.group(2), m.group(3).strip()

    # 数值路径：两侧都能数值求值时走原逻辑（行为不变）。任一侧数值求值失败
    # 才尝试字符串解释——字符串比较只支持 = / <> / #，其余运算符抛不支持。
    try:
        left = _safe_eval_expr(left_src, env, funcs=funcs, missing_names_zero=True)
    except Exception:
        left = None
    try:
        right = _safe_eval_expr(right_src, env, funcs=funcs, missing_names_zero=True)
    except Exception:
        right = None
    if left is not None and right is not None:
        if op == "=":
            return abs(left - right) <= 1e-9
        if op in {"<>", "#"}:
            return abs(left - right) > 1e-9
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right + 1e-9
        if op == ">=":
            return left >= right - 1e-9
        raise ValueError(f"条件运算符不支持: {op}")

    left_s = _string_value(left_src, env, funcs=funcs)
    right_s = _string_value(right_src, env, funcs=funcs)
    if left_s is not None and right_s is not None:
        if op == "=":
            return left_s == right_s
        if op in {"<>", "#"}:
            return left_s != right_s
        raise ValueError(f"条件运算符不支持: {op}")
    raise ValueError(f"条件无法求值: {text}")


def _eval_ast(
    node: ast.AST,
    env: dict[str, float],
    *,
    funcs: dict[str, Any] | None = None,
    missing_names_zero: bool = False,
) -> Any:
    funcs = funcs if funcs is not None else _ALLOWED_FUNCS

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return 1.0 if node.value else 0.0
        if isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node.value, str):
            # 字符串字面量（含反引号原样字符串，_translate_gdl_expr 已统一引号）
            return node.value
        raise ValueError("常量类型不支持")

    if isinstance(node, ast.Name):
        key = node.id.upper()
        if key not in env:
            if missing_names_zero:
                return 0.0
            raise ValueError(f"未定义变量 {node.id}")
        value = env[key]
        if isinstance(value, list):
            return value  # 数组原样返回，由下标/赋值消费
        if isinstance(value, str):
            # 数值形态字符串（如宏默认参数 "0.25"）按数值消费；真正的
            # 字符串参数（"直棂"）保留 str，供字符串比较/赋值使用。
            try:
                return float(value)
            except ValueError:
                return value
        return float(value)

    if isinstance(node, ast.Subscript):
        base = _eval_ast(node.value, env, funcs=funcs, missing_names_zero=missing_names_zero)
        if not isinstance(base, list):
            raise ValueError("下标作用于非数组变量")
        if isinstance(node.slice, ast.Slice):
            raise ValueError("数组切片不支持")
        index = _eval_ast(node.slice, env, funcs=funcs, missing_names_zero=missing_names_zero)
        if not isinstance(index, (int, float)) or isinstance(index, (str, bool)):
            raise ValueError("数组下标不是数值")
        idx = int(round(float(index)))
        if idx < 1:
            raise ValueError(f"数组下标越界: {idx}")
        if idx > len(base):
            return 0.0  # GDL 未初始化/越界元素读作 0（预览近似）
        return base[idx - 1]  # GDL 数组 1-based

    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left, env, funcs=funcs, missing_names_zero=missing_names_zero)
        right = _eval_ast(node.right, env, funcs=funcs, missing_names_zero=missing_names_zero)
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

    if isinstance(node, ast.Compare):
        # 由 _translate_gdl_expr 的 = → == 翻译进入；支持数值与字符串比较
        if len(node.ops) != 1:
            raise ValueError("链式比较不支持")
        left = _eval_ast(node.left, env, funcs=funcs, missing_names_zero=missing_names_zero)
        right = _eval_ast(node.comparators[0], env, funcs=funcs, missing_names_zero=missing_names_zero)
        op = node.ops[0]
        if isinstance(left, str) or isinstance(right, str):
            if isinstance(left, str) and isinstance(right, str):
                if isinstance(op, ast.Eq):
                    return 1.0 if left == right else 0.0
                if isinstance(op, ast.NotEq):
                    return 1.0 if left != right else 0.0
            raise ValueError("比较运算的操作数类型不支持")
        if isinstance(op, ast.Eq):
            return 1.0 if abs(left - right) <= 1e-9 else 0.0
        if isinstance(op, ast.NotEq):
            return 1.0 if abs(left - right) > 1e-9 else 0.0
        if isinstance(op, ast.Lt):
            return 1.0 if left < right else 0.0
        if isinstance(op, ast.LtE):
            return 1.0 if left <= right + 1e-9 else 0.0
        if isinstance(op, ast.Gt):
            return 1.0 if left > right else 0.0
        if isinstance(op, ast.GtE):
            return 1.0 if left >= right - 1e-9 else 0.0
        raise ValueError("比较运算符不支持")

    if isinstance(node, ast.BoolOp):
        values = _eval_ast_bool_operands(node, env, funcs, missing_names_zero)
        if isinstance(node.op, ast.And):
            return 1.0 if all(values) else 0.0
        if isinstance(node.op, ast.Or):
            return 1.0 if any(values) else 0.0
        raise ValueError("逻辑运算符不支持")

    if isinstance(node, ast.UnaryOp):
        v = _eval_ast(node.operand, env, funcs=funcs, missing_names_zero=missing_names_zero)
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.Not):
            # GDL NOT 是函数式 not(x)，翻译后落在 Python 的 not 上
            return 0.0 if _truthy(v) else 1.0
        raise ValueError("一元运算符不支持")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("函数调用不支持")
        fname = node.func.id.upper()
        fn = funcs.get(fname)
        if fn is None:
            raise ValueError(f"函数 {node.func.id} 不支持")
        args = [_eval_ast(a, env, funcs=funcs, missing_names_zero=missing_names_zero) for a in node.args]
        return float(fn(*args))

    raise ValueError("表达式语法不支持")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return abs(float(value)) > 1e-12
    raise ValueError("逻辑运算的操作数类型不支持")


def _eval_ast_bool_operands(
    node: ast.BoolOp,
    env: dict[str, Any],
    funcs: dict[str, Any],
    missing_names_zero: bool,
) -> list[bool]:
    return [
        _truthy(_eval_ast(v, env, funcs=funcs, missing_names_zero=missing_names_zero))
        for v in node.values
    ]
