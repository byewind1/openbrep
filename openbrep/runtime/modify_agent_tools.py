"""MODIFY agent loop 的工具封装层（ToolRegistry）。

职责：把 compiler / StaticChecker / knowledge_graph / gdl_previewer 的既有
公开接口薄封装成 `ToolDefinition` + 执行函数，供预算制 agent loop
（`modify_agent_loop.py`）使用。

红线：本模块只包 tool-calling 接口，不重新实现任何底层逻辑；
不改动被封装模块的内部实现。
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openbrep.compiler import CompileResult
from openbrep.feedback import append_feedback
from openbrep.gdl_sanitizer import sanitize_llm_script_output
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import ToolCall, ToolDefinition
from openbrep.static_checker import StaticChecker, find_prose_leaks

logger = logging.getLogger(__name__)

# 工具结果回填给模型时的长度上限，避免一次错误输出撑爆上下文
_MAX_TOOL_RESULT_CHARS = 1200
_MAX_COMPILE_ERROR_CHARS = 800

# update_script 允许的参数文件名（不含路径前缀）
_PARAMLIST_NAME = "paramlist.xml"


@dataclass
class ToolExecutionResult:
    """一次工具执行的结果。summary 回填对话历史，data 供 loop 内部判断。"""

    name: str
    ok: bool
    summary: str
    data: dict = field(default_factory=dict)


def _truncate(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，共 {len(text)} 字符）"


def normalize_script_path(file_path: str) -> str:
    """把模型给的文件路径归一成 'scripts/3d.gdl' 或 'paramlist.xml'。"""
    raw = str(file_path or "").strip().lstrip("./")
    if _PARAMLIST_NAME in raw.lower():
        return _PARAMLIST_NAME
    name = raw.split("/")[-1]
    for stype in ScriptType:
        if stype.value == name:
            return f"scripts/{stype.value}"
    return raw


def _valid_file_paths() -> list[str]:
    return [f"scripts/{stype.value}" for stype in ScriptType] + [_PARAMLIST_NAME]


# update_script/patch_script 对 paramlist.xml 用的简化参数行格式（与
# GDLAgent._parse_param_text 的输入一致：`Length shelf_thk = 0.018 ! 描述`）。
_PARAM_LINE_RE = re.compile(
    r"^(Length|Angle|RealNum|Integer|Boolean|String|Material|"
    r"FillPattern|LineType|PenColor)\s+\w+\s*=\s*\S+",
    re.IGNORECASE,
)


def _render_param_text(parameters: list) -> str:
    """把参数表渲染成 update_script/patch_script 使用的简化参数行文本。"""
    lines: list[str] = []
    for param in parameters:
        value = param.value
        if param.type_tag == "String" and (not value or " " in value or '"' in value):
            value = f'"{value}"'
        desc = f" ! {param.description}" if param.description else ""
        lines.append(f"{param.type_tag} {param.name} = {value}{desc}")
    return "\n".join(lines)


def _param_text_ok(text: str) -> bool:
    """补丁后的参数行文本是否仍是结构合法的简化格式（防静默丢参数）。"""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("!")]
    return bool(lines) and all(_PARAM_LINE_RE.match(l.strip()) for l in lines)


# P12 字符串参数引用一致性守卫：从简化参数行文本里提取 {name: (type_tag, value)}。
# 只关心 String 参数的取值变化，无需 Length mm→m 归一化。
_PARAM_VALUE_RE = re.compile(
    r'^(Length|Angle|RealNum|Integer|Boolean|String|Material|'
    r'FillPattern|LineType|PenColor)\s+(\w+)\s*=\s*("[^"]*"|\S+)',
    re.IGNORECASE,
)


def _parse_param_values(text: str) -> dict[str, tuple[str, str]]:
    """解析简化参数行文本为 {name: (type_tag, value)}（value 去引号）。"""
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        m = _PARAM_VALUE_RE.match(stripped)
        if m:
            out[m.group(2)] = (m.group(1), m.group(3).strip('"'))
    return out


def _string_ref_hits(project: HSFProject, param_name: str, value: str) -> list[tuple[str, int]]:
    """扫描项目所有 .gdl 脚本中对 `param_name = "value"` / `VALUES "param" ... "value"`
    形状的引用，返回 [(file_path, 行号), ...]。误报宁多勿漏（阻断是 advisory 信号）。"""
    hits: list[tuple[str, int]] = []
    if not value:
        return hits
    compare_re = re.compile(
        r"\b" + re.escape(param_name) + r"\s*(?:=|<>|#)\s*\"" + re.escape(value) + r"\""
    )
    values_re = re.compile(
        r"\bVALUES\s*\"" + re.escape(param_name) + r"\"\s*((?:\"[^\"]*\"\s*)+)"
    )
    for stype in ScriptType:
        content = project.get_script(stype) or ""
        for idx, raw in enumerate(content.splitlines(), start=1):
            code = raw.split("!", 1)[0]
            if compare_re.search(code):
                hits.append((f"scripts/{stype.value}", idx))
                continue
            m = values_re.search(code)
            if m and re.search(r'\"' + re.escape(value) + r'\"', m.group(1)):
                hits.append((f"scripts/{stype.value}", idx))
    return hits


def _paramlist_string_change_violations(project: HSFProject, new_param_text: str) -> list[str]:
    """paramlist.xml 字符串参数值改动 → 脚本引用一致性检查（P12）。

    对每个值被改动的 String 参数：若旧值仍被脚本引用而新值未出现在任何
    VALUES/比较中 → 阻断（返回违规说明列表，由调用方拒绝写入）。
    """
    old_params = {p.name: (p.type_tag, p.value) for p in project.parameters}
    new_params = _parse_param_values(new_param_text)
    violations: list[str] = []
    for name, (old_type, old_value) in old_params.items():
        if old_type.lower() != "string" or not old_value:
            continue
        if name not in new_params:
            continue
        new_type, new_value = new_params[name]
        if new_type.lower() != "string" or new_value == old_value:
            continue
        old_refs = _string_ref_hits(project, name, old_value)
        if not old_refs:
            continue  # 旧值已无脚本引用：改值不破坏一致性
        new_refs = _string_ref_hits(project, name, new_value)
        if new_refs:
            continue  # 新值已在 VALUES/比较中出现：模型同步改好了脚本
        refs_text = "、".join(f"{fp}:{ln}" for fp, ln in sorted(set(old_refs)))
        violations.append(
            f"字符串参数 {name} 的值从 {old_value!r} 改为 {new_value!r}，但 {new_value!r} "
            f"未出现在任何 VALUES 或 IF 比较中，{old_value!r} 仍被 {refs_text} 引用。"
            "请同步修改脚本中的引用，或放弃改值。"
        )
    return violations


class ModifyToolRegistry:
    """MODIFY 场景工具注册表：工具定义 + 分发执行 + 工具侧状态。

    状态（changed_files / last_compile_result / tool_log）随 loop 运行累积，
    loop 结束后由调用方读取，用于组装 TaskResult 与 A/B 指标。
    """

    def __init__(
        self,
        *,
        project: HSFProject,
        compiler: Any,
        output_gsm: str,
        apply_changes: Callable[[HSFProject, dict[str, str]], None],
        on_event: Optional[Callable] = None,
    ) -> None:
        self.project = project
        self.compiler = compiler
        self.output_gsm = output_gsm
        # 复用 GDLAgent._apply_changes 的参数表/脚本落盘语义，不复制其逻辑
        self._apply_changes = apply_changes
        self.on_event = on_event or (lambda *_: None)
        self.changed_files: dict[str, str] = {}
        # diff 范围护栏：记录每个可写文件的修改前内容与最近一次写入方式
        self._baseline_content: dict[str, str] = {
            fp: self._current_file_content(fp) for fp in _valid_file_paths()
        }
        self.write_methods: dict[str, str] = {}  # file_path -> "update_script" | "patch_script"
        self.last_compile_result: Optional[CompileResult] = None
        self.tool_log: list[dict] = []

    # ── 工具定义 ──────────────────────────────────────────

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="update_script",
                description=(
                    "全量重写工程中的一个文件（替换全部内容）。file_path 取值为 "
                    + ", ".join(_valid_file_paths())
                    + "。仅当需要整文件重写时使用；局部小改动请优先用 patch_script。"
                    "改动立即生效，之后应调用 compile_script 验证。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "目标文件，如 scripts/3d.gdl 或 paramlist.xml"},
                        "content": {"type": "string", "description": "文件完整新内容（GDL 语句或参数行）"},
                    },
                    "required": ["file_path", "content"],
                },
            ),
            ToolDefinition(
                name="patch_script",
                description=(
                    "局部编辑工程中的一个文件：按精确文本匹配替换若干段（diff 级改动，"
                    "比 update_script 的全量替换更安全、可审计）。file_path 取值为 "
                    + ", ".join(_valid_file_paths())
                    + "。每段 old 必须在当前文件内容中精确匹配且仅出现一次"
                    "（匹配 0 次或多次都会拒绝）；patches 按顺序应用，任一段失败则"
                    "本次调用整体不生效（全或无）。局部改动优先用本工具，"
                    "整文件重写才用 update_script。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "目标文件，如 scripts/3d.gdl 或 paramlist.xml"},
                        "patches": {
                            "type": "array",
                            "description": "按顺序应用的替换段列表；old 必须精确匹配且唯一",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "old": {"type": "string", "description": "要替换的精确原文（必须唯一出现；建议包含足够上下文）"},
                                    "new": {"type": "string", "description": "替换后的内容"},
                                },
                                "required": ["old", "new"],
                            },
                        },
                    },
                    "required": ["file_path", "patches"],
                },
            ),
            ToolDefinition(
                name="compile_script",
                description="编译当前工程，返回编译是否成功与错误信息。每次修改后都应调用验证。",
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="run_static_check",
                description="运行静态检查（未定义变量、变换栈配平、块配对等），返回问题列表。",
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="query_knowledge",
                description=(
                    "查询 GDL 知识图谱。mode=api：按命令名查签名（如 BLOCK）；"
                    "mode=suggest：按意图推荐相关命令；mode=diagnose：诊断一段编译错误文本。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "命令名、意图描述或错误文本"},
                        "mode": {"type": "string", "enum": ["api", "suggest", "diagnose"], "description": "查询模式，默认 suggest"},
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="preview_geometry",
                description="用轻量预览器渲染当前 3D 脚本，返回 mesh 数量与包围盒摘要，用于核对几何是否符合预期。",
                parameters={"type": "object", "properties": {}},
            ),
        ]

    # ── 分发执行 ──────────────────────────────────────────

    def execute(self, call: ToolCall) -> ToolExecutionResult:
        """执行一次工具调用并记日志；任何异常都降级为 ok=False 的结果回填。"""
        handler = {
            "update_script": self._update_script,
            "patch_script": self._patch_script,
            "compile_script": self._compile_script,
            "run_static_check": self._run_static_check,
            "query_knowledge": self._query_knowledge,
            "preview_geometry": self._preview_geometry,
        }.get(call.name)
        if handler is None:
            result = ToolExecutionResult(
                name=call.name,
                ok=False,
                summary=f"未知工具：{call.name}。可用工具：update_script / patch_script / compile_script / run_static_check / query_knowledge / preview_geometry",
            )
        else:
            try:
                result = handler(call.arguments or {})
            except Exception as exc:  # 工具异常不应炸掉 loop，如实回填给模型
                logger.warning("tool %s failed: %s", call.name, exc)
                result = ToolExecutionResult(name=call.name, ok=False, summary=f"工具执行异常：{exc}")
        self.tool_log.append({
            "name": call.name,
            "arguments": dict(call.arguments or {}),
            "ok": result.ok,
            "summary": result.summary[:200],
        })
        return result

    # ── 各工具实现（薄封装） ───────────────────────────────

    def _update_script(self, args: dict) -> ToolExecutionResult:
        file_path = normalize_script_path(str(args.get("file_path") or ""))
        content = str(args.get("content") or "")
        if file_path not in _valid_file_paths():
            return ToolExecutionResult(
                name="update_script",
                ok=False,
                summary=f"非法 file_path：{args.get('file_path')!r}。允许：{', '.join(_valid_file_paths())}",
            )
        if not content.strip():
            return ToolExecutionResult(name="update_script", ok=False, summary="content 为空，未做任何改动")
        cleaned = sanitize_llm_script_output(content, file_path)
        if file_path == _PARAMLIST_NAME:
            # P12 字符串参数引用一致性守卫（paramlist.xml 不走散文守卫——XML 内容）
            violations = _paramlist_string_change_violations(self.project, cleaned)
            if violations:
                return ToolExecutionResult(
                    name="update_script",
                    ok=False,
                    summary="拒绝写入 paramlist.xml：\n" + "\n".join(violations),
                )
        else:
            # P12 GDL 散文守卫：写盘前拦截 markdown 散文泄漏（P8 ellipsis_stub 同族）
            leaks = find_prose_leaks(cleaned)
            if leaks:
                lines = "、".join(f"第 {h.line} 行（{h.kind}）" for h in leaks[:5])
                return ToolExecutionResult(
                    name="update_script",
                    ok=False,
                    summary=(
                        f"拒绝写入：{file_path} 含 {len(leaks)} 处 markdown 散文泄漏（{lines}）。"
                        "脚本文件只允许 GDL 语句；解释文字请放在对话里或以 `!` 注释书写。"
                    ),
                )
        self._apply_changes(self.project, {file_path: cleaned})
        self.changed_files[file_path] = cleaned
        self.write_methods[file_path] = "update_script"
        self.on_event("status", {"stage": "modify", "message": f"✏️ 已更新 {file_path}"})
        return ToolExecutionResult(
            name="update_script",
            ok=True,
            summary=f"已更新 {file_path}（{len(cleaned)} 字符）。请调用 compile_script 验证。",
            data={"file_path": file_path},
        )

    def _current_file_content(self, file_path: str) -> str:
        """当前（内存）文件内容：脚本取 project.scripts，paramlist 渲染为简化参数行。"""
        if file_path == _PARAMLIST_NAME:
            return _render_param_text(self.project.parameters)
        for stype in ScriptType:
            if file_path == f"scripts/{stype.value}":
                return self.project.get_script(stype) or ""
        return ""

    def _patch_script(self, args: dict) -> ToolExecutionResult:
        """局部编辑：patches 按顺序精确匹配替换；任一段失败则整体不生效（全或无）。

        每段 old 必须在当前内容中精确匹配且仅出现一次（0 次/N 次都拒绝并说明），
        成功后在结果里报告每段替换的行数与起始行号。
        """
        file_path = normalize_script_path(str(args.get("file_path") or ""))
        patches = args.get("patches")
        if file_path not in _valid_file_paths():
            return ToolExecutionResult(
                name="patch_script",
                ok=False,
                summary=f"非法 file_path：{args.get('file_path')!r}。允许：{', '.join(_valid_file_paths())}",
            )
        if not isinstance(patches, list) or not patches:
            return ToolExecutionResult(name="patch_script", ok=False, summary="patches 为空，未做任何改动")

        normalized: list[dict] = []
        for i, patch in enumerate(patches):
            if not isinstance(patch, dict) or "old" not in patch or "new" not in patch:
                return ToolExecutionResult(
                    name="patch_script", ok=False,
                    summary=f"patches[{i}] 缺少 old/new 字段，未做任何改动（全或无）",
                )
            old_text = str(patch.get("old") or "")
            if not old_text.strip():
                return ToolExecutionResult(
                    name="patch_script", ok=False,
                    summary=f"patches[{i}].old 为空文本，无法匹配，未做任何改动（全或无）",
                )
            normalized.append({"old": old_text, "new": str(patch.get("new") or "")})

        # 全或无：先在副本上依次应用全部 patches，任一失败即整体拒绝
        working = self._current_file_content(file_path)
        applied: list[dict] = []
        for i, patch in enumerate(normalized):
            count = working.count(patch["old"])
            if count == 0:
                # 反馈信号采集（best-effort，不改判定）：匹配 0 次 → patch_failure
                append_feedback(self.project.root, {
                    "kind": "patch_failure",
                    "summary": f"patch_script 匹配 0 次：{file_path}",
                    "detail": {
                        "file_path": file_path,
                        "match_count": 0,
                        "patch_index": i,
                        "old_excerpt": patch["old"],
                    },
                })
                return ToolExecutionResult(
                    name="patch_script", ok=False,
                    summary=(
                        f"patches[{i}] 在当前 {file_path} 中匹配 0 次，未做任何改动（全或无）。"
                        f"请核对 old 文本与文件当前内容：{patch['old'][:80]!r}"
                    ),
                )
            if count > 1:
                # 反馈信号采集（best-effort，不改判定）：匹配多次（非唯一）→ patch_failure
                append_feedback(self.project.root, {
                    "kind": "patch_failure",
                    "summary": f"patch_script 匹配 {count} 次（非唯一）：{file_path}",
                    "detail": {
                        "file_path": file_path,
                        "match_count": count,
                        "patch_index": i,
                        "old_excerpt": patch["old"],
                    },
                })
                return ToolExecutionResult(
                    name="patch_script", ok=False,
                    summary=(
                        f"patches[{i}] 在当前 {file_path} 中匹配 {count} 次（非唯一），"
                        f"未做任何改动（全或无）。请提供更长的上下文：{patch['old'][:80]!r}"
                    ),
                )
            pos = working.find(patch["old"])
            line_no = working[:pos].count("\n") + 1
            old_lines = patch["old"].count("\n") + 1
            new_lines = patch["new"].count("\n") + 1 if patch["new"] else 0
            working = working[:pos] + patch["new"] + working[pos + len(patch["old"]):]
            applied.append({
                "index": i, "line": line_no, "old_lines": old_lines, "new_lines": new_lines,
            })

        cleaned = sanitize_llm_script_output(working, file_path)
        if not cleaned.strip():
            return ToolExecutionResult(name="patch_script", ok=False, summary="补丁后文件为空，未做任何改动")
        if file_path == _PARAMLIST_NAME:
            if not _param_text_ok(cleaned):
                return ToolExecutionResult(
                    name="patch_script", ok=False,
                    summary="补丁后的参数行文本格式不合法（应为 `类型 名称 = 值 ! 描述` 每行一条），未做任何改动（全或无）",
                )
            # P12 字符串参数引用一致性守卫
            violations = _paramlist_string_change_violations(self.project, cleaned)
            if violations:
                return ToolExecutionResult(
                    name="patch_script",
                    ok=False,
                    summary="拒绝写入 paramlist.xml：\n" + "\n".join(violations) + "\n未做任何改动（全或无）",
                )
        else:
            # P12 GDL 散文守卫：写盘前对补丁后的完整内容做散文检查
            leaks = find_prose_leaks(cleaned)
            if leaks:
                lines = "、".join(f"第 {h.line} 行（{h.kind}）" for h in leaks[:5])
                return ToolExecutionResult(
                    name="patch_script",
                    ok=False,
                    summary=(
                        f"拒绝写入：补丁后 {file_path} 含 {len(leaks)} 处 markdown 散文泄漏（{lines}）。"
                        "脚本文件只允许 GDL 语句；解释文字请放在对话里或以 `!` 注释书写。\n未做任何改动（全或无）"
                    ),
                )

        self._apply_changes(self.project, {file_path: cleaned})
        self.changed_files[file_path] = cleaned
        self.write_methods[file_path] = "patch_script"
        self.on_event("status", {"stage": "modify", "message": f"✏️ 已局部编辑 {file_path}（{len(applied)} 段）"})
        detail = "；".join(
            f"patches[{a['index']}]：第 {a['line']} 行起，old {a['old_lines']} 行 → new {a['new_lines']} 行"
            for a in applied
        )
        return ToolExecutionResult(
            name="patch_script",
            ok=True,
            summary=f"已应用 {len(applied)} 段补丁到 {file_path}。{detail}。请调用 compile_script 验证。",
            data={"file_path": file_path, "patches_applied": len(applied)},
        )

    def change_ratios(self) -> dict[str, float]:
        """每个已变更文件的变更行占比（0~1）：1 - 公共行数 / 总行数。

        用 splitlines 消除首尾换行差异；空文件占比记 0。
        """
        ratios: dict[str, float] = {}
        for file_path, after in self.changed_files.items():
            before_lines = self._baseline_content.get(file_path, "").splitlines()
            after_lines = (after or "").splitlines()
            total = max(len(before_lines), len(after_lines))
            if total == 0:
                ratios[file_path] = 0.0
                continue
            matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
            matched = sum(block.size for block in matcher.get_matching_blocks())
            ratios[file_path] = round(1.0 - matched / total, 4)
        return ratios

    def diff_scope_warnings(self) -> tuple[list[str], dict[str, float]]:
        """diff 范围护栏（v1 advisory）：update_script 全量替换且变更行 > 50% 时警告。

        不阻断、不回滚；返回 (警告列表, 各文件变更占比)。
        """
        warnings: list[str] = []
        ratios = self.change_ratios()
        for file_path, ratio in ratios.items():
            if self.write_methods.get(file_path) == "update_script" and ratio > 0.5:
                warnings.append(
                    f"{file_path}：改动 {ratio:.0%} 行，超过文件一半，超出最小修改预期（update_script 全量替换）"
                )
        return warnings, ratios

    def _compile_script(self, _args: dict) -> ToolExecutionResult:
        hsf_dir = self.project.save_to_disk()
        self.last_compile_result = self.compiler.hsf2libpart(str(hsf_dir), self.output_gsm)
        if self.last_compile_result.success:
            self.on_event("status", {"stage": "compile", "message": "✅ 编译通过"})
            return ToolExecutionResult(
                name="compile_script",
                ok=True,
                summary="编译通过。",
                data={"success": True},
            )
        error_text = "\n".join(
            part for part in [self.last_compile_result.stderr or "", self.last_compile_result.stdout or ""] if part.strip()
        )
        if not error_text.strip():
            # P12 顺带小修：编译失败但编译器无任何输出时，回填明确标注而非空消息
            error_text = f"（编译器无错误输出，exit_code={self.last_compile_result.exit_code}）"
        self.on_event("status", {"stage": "compile", "message": "❌ 编译失败"})
        return ToolExecutionResult(
            name="compile_script",
            ok=False,
            summary=f"编译失败：\n{_truncate(error_text, _MAX_COMPILE_ERROR_CHARS)}",
            data={"success": False, "error": error_text},
        )

    def _run_static_check(self, _args: dict) -> ToolExecutionResult:
        result = StaticChecker().check(self.project)
        warn_lines = [
            f"- [warning:{w.check_type}] {w.file}: {w.detail}" for w in result.warnings[:10]
        ]
        if result.passed:
            summary = "静态检查通过，未发现问题。"
            if warn_lines:
                # P13：unknown_command 等 warning 级结果——不阻断，但浮出水面
                summary = "静态检查通过（有警告）：\n" + "\n".join(warn_lines)
            self.on_event("status", {"stage": "compile", "message": "✅ 静态检查通过"})
            return ToolExecutionResult(
                name="run_static_check",
                ok=True,
                summary=summary,
                data={"passed": True, "error_count": 0, "warning_count": len(result.warnings)},
            )
        lines = [f"- [{e.check_type}] {e.file}: {e.detail}" for e in result.errors[:10]]
        if warn_lines:
            lines.append("--- 警告（不阻断）---")
            lines.extend(warn_lines)
        self.on_event("status", {"stage": "compile", "message": f"⚠️ 静态检查发现 {len(result.errors)} 个问题"})
        return ToolExecutionResult(
            name="run_static_check",
            ok=False,
            summary=f"静态检查发现 {len(result.errors)} 个问题：\n" + "\n".join(lines),
            data={"passed": False, "error_count": len(result.errors), "warning_count": len(result.warnings)},
        )

    def _query_knowledge(self, args: dict) -> ToolExecutionResult:
        from openbrep.knowledge_graph import get_graph_manager

        query = str(args.get("query") or "").strip()
        mode = str(args.get("mode") or "suggest").strip().lower()
        if not query:
            return ToolExecutionResult(name="query_knowledge", ok=False, summary="query 为空")
        graph = get_graph_manager()
        if mode == "api":
            api = graph.query_api(query)
            summary = api.to_prompt_line() if api else f"知识图谱中未找到命令：{query}"
            return ToolExecutionResult(name="query_knowledge", ok=api is not None, summary=_truncate(summary))
        if mode == "diagnose":
            diagnosis = graph.diagnose_error(query)
            summary = diagnosis or "图谱无针对该错误的诊断，请根据错误文本自行分析。"
            return ToolExecutionResult(name="query_knowledge", ok=bool(diagnosis), summary=_truncate(summary))
        if mode == "suggest":
            apis = graph.suggest_apis(query)
            if not apis:
                return ToolExecutionResult(name="query_knowledge", ok=False, summary=f"无意图匹配的命令建议：{query}")
            summary = "相关命令：\n" + "\n".join(f"- {api.to_prompt_line()}" for api in apis)
            return ToolExecutionResult(name="query_knowledge", ok=True, summary=_truncate(summary))
        return ToolExecutionResult(
            name="query_knowledge",
            ok=False,
            summary=f"非法 mode：{mode!r}，允许 api / suggest / diagnose",
        )

    def _preview_geometry(self, _args: dict) -> ToolExecutionResult:
        from openbrep.gdl_previewer import preview_3d_script
        from openbrep.workbench.project_parameter_service import parameter_values

        script_3d = self.project.get_script(ScriptType.SCRIPT_3D) or ""
        if not script_3d.strip():
            return ToolExecutionResult(name="preview_geometry", ok=False, summary="3d.gdl 为空，没有可渲染的几何")
        setup_script = self.project.get_script(ScriptType.MASTER) or ""
        result = preview_3d_script(
            script_3d,
            parameters=parameter_values(self.project),
            setup_script=setup_script,
            unknown_command_policy="warn",
            quality="fast",
        )
        bbox = _meshes_bbox(result.meshes)
        parts = [f"mesh 数量：{len(result.meshes)}"]
        if bbox:
            mins, maxs = bbox
            parts.append(
                "包围盒：x[{:.3f},{:.3f}] y[{:.3f},{:.3f}] z[{:.3f},{:.3f}]".format(*mins, *maxs)
            )
        else:
            parts.append("包围盒：无（几何为空）")
        if result.warnings:
            parts.append("预览警告：\n" + "\n".join(f"- {w}" for w in result.warnings[:5]))
        self.on_event("status", {"stage": "preview", "message": f"📐 几何预览：{parts[0]}"})
        return ToolExecutionResult(
            name="preview_geometry",
            ok=bool(result.meshes),
            summary=_truncate("\n".join(parts)),
            data={"mesh_count": len(result.meshes)},
        )


def _meshes_bbox(meshes: list) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """从预览 mesh 列表计算场景包围盒，空场景返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for mesh in meshes or []:
        xs.extend(mesh.x)
        ys.extend(mesh.y)
        zs.extend(mesh.z)
    if not xs:
        return None
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
