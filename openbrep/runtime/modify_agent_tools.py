"""MODIFY agent loop 的工具封装层（ToolRegistry）。

职责：把 compiler / StaticChecker / knowledge_graph / gdl_previewer 的既有
公开接口薄封装成 `ToolDefinition` + 执行函数，供预算制 agent loop
（`modify_agent_loop.py`）使用。

红线：本模块只包 tool-calling 接口，不重新实现任何底层逻辑；
不改动被封装模块的内部实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openbrep.compiler import CompileResult
from openbrep.gdl_sanitizer import sanitize_llm_script_output
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import ToolCall, ToolDefinition
from openbrep.static_checker import StaticChecker

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
        self.last_compile_result: Optional[CompileResult] = None
        self.tool_log: list[dict] = []

    # ── 工具定义 ──────────────────────────────────────────

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="update_script",
                description=(
                    "更新工程中的一个文件（全量替换内容）。file_path 取值为 "
                    + ", ".join(_valid_file_paths())
                    + "。改动立即生效，之后应调用 compile_script 验证。"
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
            "compile_script": self._compile_script,
            "run_static_check": self._run_static_check,
            "query_knowledge": self._query_knowledge,
            "preview_geometry": self._preview_geometry,
        }.get(call.name)
        if handler is None:
            result = ToolExecutionResult(
                name=call.name,
                ok=False,
                summary=f"未知工具：{call.name}。可用工具：update_script / compile_script / run_static_check / query_knowledge / preview_geometry",
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
        self.on_event("agent_tool_call", {"name": call.name, "ok": result.ok})
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
        self._apply_changes(self.project, {file_path: cleaned})
        self.changed_files[file_path] = cleaned
        return ToolExecutionResult(
            name="update_script",
            ok=True,
            summary=f"已更新 {file_path}（{len(cleaned)} 字符）。请调用 compile_script 验证。",
            data={"file_path": file_path},
        )

    def _compile_script(self, _args: dict) -> ToolExecutionResult:
        hsf_dir = self.project.save_to_disk()
        self.last_compile_result = self.compiler.hsf2libpart(str(hsf_dir), self.output_gsm)
        if self.last_compile_result.success:
            return ToolExecutionResult(
                name="compile_script",
                ok=True,
                summary="编译通过。",
                data={"success": True},
            )
        error_text = "\n".join(
            part for part in [self.last_compile_result.stderr or "", self.last_compile_result.stdout or ""] if part.strip()
        )
        return ToolExecutionResult(
            name="compile_script",
            ok=False,
            summary=f"编译失败：\n{_truncate(error_text, _MAX_COMPILE_ERROR_CHARS)}",
            data={"success": False, "error": error_text},
        )

    def _run_static_check(self, _args: dict) -> ToolExecutionResult:
        result = StaticChecker().check(self.project)
        if result.passed:
            return ToolExecutionResult(
                name="run_static_check",
                ok=True,
                summary="静态检查通过，未发现问题。",
                data={"passed": True, "error_count": 0},
            )
        lines = [f"- [{e.check_type}] {e.file}: {e.detail}" for e in result.errors[:10]]
        return ToolExecutionResult(
            name="run_static_check",
            ok=False,
            summary=f"静态检查发现 {len(result.errors)} 个问题：\n" + "\n".join(lines),
            data={"passed": False, "error_count": len(result.errors)},
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
