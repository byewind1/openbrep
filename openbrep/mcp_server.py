"""MCP stdio server（Phase 1 / P1-e）。

本文件是全仓库唯一允许 import mcp 库的模块：它只是 openbrep.mcp_tools 的
协议皮（protocol skin），不含任何业务逻辑。mcp_tools.py 一行都不许动。

职责：
- 把 mcp_tools 的工具注册为 MCP 工具（工具名与函数名一致）。
- 每个工具的 description 从 mcp_tools 对应函数的 docstring 提炼。
- 工具返回值：mcp_tools 返回的 dict 直接 JSON 序列化为 text content 返回；
  异常不抛到协议层（mcp_tools 已保证不抛，这里再加一层兜底）。

协议：stdio（stdout 是 JSON-RPC 通道，日志只写 stderr）。
入口：`obr mcp-server`，或直接 `python -m openbrep.mcp_server`。

依赖：官方 Python SDK（mcp >= 2.0.0）。注意 mcp 2.x 的 API 形态与 1.x 文档
不同：handler 注册是构造器参数（on_list_tools / on_call_tool），返回类型为
mcp_types 的 ListToolsResult / CallToolResult。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

import mcp.server.stdio as mcp_stdio
from mcp.server import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

import openbrep.mcp_tools as mcp_tools
from openbrep import __version__

logger = logging.getLogger(__name__)

_MCP_TOOL_NAMES = (
    "load_project",
    "compile_hsf",
    "semantic_verify",
    "render_evidence",
    "apply_edit",
    "rollback",
    "import_source",
    "workspace_init",
    "workspace_scan",
    "workspace_search",
    "propose_skill",
    "verify_skill",
    "reuse_skill",
    "list_skills",
    "deprecate_skill",
    "distill_feedback",
    "list_lessons",
    "promote_lesson",
)


def _schema(
    required: tuple[tuple[str, str], ...],
    optional: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """构造简单 JSON schema：required 必填，optional 选填（都进 properties）。

    每个条目是 (参数名, JSON type)，type ∈ string/number/boolean/array/object。
    """
    props = {name: {"type": t} for name, t in (*required, *optional)}
    return {"type": "object", "properties": props, "required": [n for n, _ in required]}


# ── 工具清单：name → (mcp_tools 函数, description, input schema) ──────
# description 从 mcp_tools 各函数 docstring 提炼：一句话 + 参数说明。

_TOOL_SPECS: tuple[tuple[str, Any, str, dict[str, Any]], ...] = (
    (
        "load_project",
        mcp_tools.load_project,
        "加载 HSF 项目，返回项目概貌（只读、幂等、无副作用）。"
        "参数 path: HSF 项目目录绝对路径（string）。",
        _schema(required=(("path", "string"),)),
    ),
    (
        "compile_hsf",
        mcp_tools.compile_hsf,
        "编译 HSF → .gsm（只读：产物写临时目录，不写进项目目录）。"
        "ok=True 表示工具执行成功，success 表示编译结果。"
        "参数 path: HSF 项目目录绝对路径（string）；"
        "mode: auto/mock/real，默认 auto（string，选填）。",
        _schema(required=(("path", "string"),), optional=(("mode", "string"),)),
    ),
    (
        "semantic_verify",
        mcp_tools.semantic_verify,
        "对项目 3D 脚本做几何级语义验证（不走 pipeline）。"
        "返回 passed 与 issues（含 blocking 标记）。"
        "参数 path: HSF 项目目录绝对路径（string）；"
        "sweep: 是否做参数扫掠，默认 true（boolean，选填）。",
        _schema(required=(("path", "string"),), optional=(("sweep", "boolean"),)),
    ),
    (
        "render_evidence",
        mcp_tools.render_evidence,
        "生成机器可读几何证据：包围盒、网格统计、参数扫掠响应（单位：米）。"
        "参数 path: HSF 项目目录绝对路径（string）；"
        "sweep_params: 要扫掠的参数名列表，空则跳过（array of string，选填）；"
        "tolerance: bbox_vs_declared 的 match 容差，默认 0.05（number，选填），"
        "只影响 match 判定，不影响 semantic_verifier 行为。",
        _schema(
            required=(("path", "string"),),
            optional=(("sweep_params", "array"), ("tolerance", "number")),
        ),
    ),
    (
        "apply_edit",
        mcp_tools.apply_edit,
        "应用编辑：set_parameters（改参数值）或 set_script（整脚本替换）。"
        "mode=draft 试跑零持久化（返回 diff/compile/verify）；mode=apply 落盘并可回滚。"
        "参数 path: HSF 项目目录绝对路径（string）；"
        "spec: {'type':'set_parameters','values':{...}} 或 "
        "{'type':'set_script','script_type':'1d'|'2d'|'3d'|'vl'|'ui'|'master','content':str}"
        "（object）；mode: draft/apply，默认 draft（string，选填）。",
        _schema(required=(("path", "string"), ("spec", "object")), optional=(("mode", "string"),)),
    ),
    (
        "rollback",
        mcp_tools.rollback,
        "回滚项目到指定 revision，并记录一条 trigger=rollback 的新 revision。"
        "revision_id=previous 回滚到最近变更的父版本。"
        "参数 path: HSF 项目目录绝对路径（string）；"
        "revision_id: 目标版本 id 或 previous，默认 previous（string，选填）。",
        _schema(required=(("path", "string"),), optional=(("revision_id", "string"),)),
    ),
    (
        "import_source",
        mcp_tools.import_source,
        "把外部源文件（gdl/gsm/blender_py）导入为 HSF 项目。"
        "返回 {ok, project_path, warnings, trace_id}。"
        "参数 source_path: 源文件绝对路径（string）；"
        "kind: gdl|gsm|blender_py（string）；"
        "target_dir: 目标项目父目录（string）；"
        "name: 项目名，可选（string，选填）。",
        _schema(
            required=(("source_path", "string"), ("kind", "string"), ("target_dir", "string")),
            optional=(("name", "string"),),
        ),
    ),
    (
        "workspace_init",
        mcp_tools.workspace_init,
        "初始化工作区：创建四区目录（materials/sources/hsf/artifacts）+ "
        ".openbrep/workspace.toml。已存在则校验结构幂等返回；路径含非工作区内容"
        "时不炸，报告 conflicts。参数 path: 工作区目录绝对路径（string）。",
        _schema(required=(("path", "string"),)),
    ),
    (
        "workspace_scan",
        mcp_tools.workspace_scan,
        "扫描工作区，返回索引：projects（hsf/ 下各 HSF 项目：名称/参数数/脚本清单/"
        "最新 revision/origin/成品数）、sources 文件清单、materials 计数、zones 完整性。"
        "参数 path: 工作区目录绝对路径（string）。",
        _schema(required=(("path", "string"),)),
    ),
    (
        "workspace_search",
        mcp_tools.workspace_search,
        "跨项目搜索（大小写不敏感子串）：项目名/参数名/脚本内容，返回命中"
        "（项目、位置、行号、摘要行）。纯遍历不做索引。"
        "参数 path: 工作区目录绝对路径（string）；query: 搜索词（string）。",
        _schema(required=(("path", "string"), ("query", "string"))),
    ),
    (
        "propose_skill",
        mcp_tools.propose_skill,
        "提出一个新 skill（写 {name}.md 到 skills_dir），不验证、不晋升；"
        "同名已存在不覆盖。返回 {ok, skill, status, path, trace_id}。"
        "参数 name: skill 名（string）；content: skill 正文（string）；"
        "pattern_type / source_project / source_trace_id: 溯源字段（string，选填）；"
        "slice: 跨脚本 feature slice 对象 {params, scripts}（object，选填）；"
        "skills_dir: skills 目录，默认 ./skills（string，选填）。",
        _schema(
            required=(("name", "string"), ("content", "string")),
            optional=(
                ("pattern_type", "string"),
                ("source_project", "string"),
                ("source_trace_id", "string"),
                ("slice", "object"),
                ("skills_dir", "string"),
            ),
        ),
    ),
    (
        "verify_skill",
        mcp_tools.verify_skill,
        "skill 晋升门禁：带 slice 走 full 门禁（mock 编译+语义验证），无 slice 走 "
        "structural 门禁（frontmatter 完整+触发词小节）。通过则 status 翻 verified "
        "并写 verified_evidence。返回 {ok, name, gate, passed, evidence, status, trace_id}。"
        "参数 name: skill 名（string）；"
        "skills_dir: skills 目录，默认 ./skills（string，选填）。",
        _schema(required=(("name", "string"),), optional=(("skills_dir", "string"),)),
    ),
    (
        "reuse_skill",
        mcp_tools.reuse_skill,
        "按任务描述检索可注入 skill 并返回注入文本（调用即计复用：命中把该 skill 的 "
        "reuse_count+1）。返回 {ok, query, matched, skills_text, trace_id}。"
        "参数 query: 任务描述（string）；"
        "skills_dir: skills 目录，默认 ./skills（string，选填）。",
        _schema(required=(("query", "string"),), optional=(("skills_dir", "string"),)),
    ),
    (
        "list_skills",
        mcp_tools.list_skills,
        "列出全部 skill（含 proposed/deprecated）及元数据，status 非空时按状态过滤。"
        "返回 {ok, skills, total, trace_id}。参数 status: "
        "active/verified/proposed/deprecated（string，选填）；"
        "skills_dir: skills 目录，默认 ./skills（string，选填）。",
        _schema(required=(), optional=(("status", "string"), ("skills_dir", "string"))),
    ),
    (
        "deprecate_skill",
        mcp_tools.deprecate_skill,
        "把 skill 翻为 deprecated（不再注入，文件保留不删除）；已是 deprecated 幂等成功。"
        "返回 {ok, name, status, trace_id}。参数 name: skill 名（string）；"
        "skills_dir: skills 目录，默认 ./skills（string，选填）。",
        _schema(required=(("name", "string"),), optional=(("skills_dir", "string"),)),
    ),
    (
        "distill_feedback",
        mcp_tools.distill_feedback,
        "把项目/工作区的反馈事件（.openbrep/feedback.jsonl）提炼成 proposed 态教训候选"
        "（写入 <work_dir>/.openbrep/memory/learnings/distilled_lessons.jsonl，"
        "不注入、不晋升）。返回 {ok, new_lessons, total_lessons, clusters, trace_id}。"
        "参数 path: HSF 项目目录或工作区目录绝对路径（string）；"
        "work_dir: 教训库所在工作区，默认 = path（工作区）或其父目录（项目）（string，选填）。",
        _schema(required=(("path", "string"),), optional=(("work_dir", "string"),)),
    ),
    (
        "list_lessons",
        mcp_tools.list_lessons,
        "列出教训库（distilled_lessons.jsonl）的教训视图：{fingerprint, pattern, guidance, "
        "evidence_kinds, count, status, first_seen, last_seen}，按 (status, -count, last_seen) "
        "排序。status 可选 proposed/active/rejected。返回 {ok, lessons, total, trace_id}。"
        "参数 work_dir: 教训库所在工作区（string，选填，默认 ./workdir）；"
        "status: 状态过滤（string，选填）。",
        _schema(required=(), optional=(("work_dir", "string"), ("status", "string"))),
    ),
    (
        "promote_lesson",
        mcp_tools.promote_lesson,
        "教训状态机迁移：promote（proposed→active 晋升）/ reject（proposed→rejected 拒绝）/ "
        "demote（active→proposed 撤回）。幂等：promote 已 active / reject 已 rejected → ok 但不变。"
        "非法迁移 / 未知 fingerprint / 非法 decision → 错误不静默。"
        "返回 {ok, fingerprint, decision, status, trace_id}。"
        "参数 work_dir: 教训库所在工作区（string，选填，默认 ./workdir）；"
        "fingerprint: 教训指纹（string）；decision: promote/reject/demote（string）。",
        _schema(
            required=(("fingerprint", "string"), ("decision", "string")),
            optional=(("work_dir", "string"),),
        ),
    ),
)

_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {
    name: {"fn": fn, "description": desc, "schema": schema}
    for name, fn, desc, schema in _TOOL_SPECS
}


def _list_tools_result() -> ListToolsResult:
    """由 _TOOL_SPECS 构建 ListToolsResult（工具名与 mcp_tools 函数名一致）。"""
    tools = [
        Tool(name=name, description=spec["description"], input_schema=spec["schema"])
        for name, spec in _TOOLS_BY_NAME.items()
    ]
    return ListToolsResult(tools=tools)


def _call_tool_result(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
    """分派到 mcp_tools 工具，把返回 dict JSON 序列化为 text content。

    协议层失败（未知工具 / 异常 / 非 dict 返回）走 isError=True；
    mcp_tools 的业务错误 dict（ok=False）按正常工具结果返回，由调用方判读。
    """
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "ok": False,
                            "error": {"code": "method_not_found", "message": f"未知工具: {name}"},
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
            is_error=True,
        )
    try:
        result = tool["fn"](**dict(arguments or {}))
    except Exception as exc:
        logger.exception("工具 %s 调用异常（协议层兜底）", name)
        result = {
            "ok": False,
            "error": {
                "code": "mcp_internal_error",
                "message": f"{name} 调用异常: {exc}",
                "details": {"tool": name},
            },
        }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            is_error=True,
        )
    if not isinstance(result, dict):
        result = {
            "ok": False,
            "error": {
                "code": "mcp_internal_error",
                "message": f"{name} 返回非 dict: {type(result).__name__}",
                "details": {"tool": name},
            },
        }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        is_error=False,
    )


async def _on_list_tools(ctx: Any, params: Any) -> ListToolsResult:
    del ctx, params  # 无分页：直接返回全部工具
    return _list_tools_result()


async def _on_call_tool(ctx: Any, params: CallToolRequestParams) -> CallToolResult:
    del ctx  # 业务状态全部在 mcp_tools 内部（_locked() 串行化）
    return _call_tool_result(params.name, params.arguments)


def build_server() -> Server:
    """构造 mcp 2.x Server 实例（handler 注册走构造器参数）。"""
    return Server(
        name="openbrep",
        version=__version__,
        instructions=(
            "OpenBrep MCP 工具层：加载 / 编译 / 语义验证 / 几何证据 / 编辑 / 回滚 / 导入 "
            "HSF（ArchiCAD 库对象）项目；propose/verify/reuse/list/deprecate 五个 "
            "skill 管理工具；以及 distill_feedback（反馈事件 → 教训候选）与 "
            "list_lessons / promote_lesson（教训状态机：proposed 过闸才 active 进注入）。"
            "项目类工具第一个参数都是 HSF 项目目录绝对路径 path；"
            "skill 类工具用 name/query + skills_dir。"
            "返回 {ok, ..., trace_id} 或 {ok: False, error: {code, message}, trace_id}。"
        ),
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


async def _run_server() -> None:
    """在 stdio 上驱动 server，直到读端关闭。"""
    server = build_server()
    async with mcp_stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    """MCP stdio server 前台入口；返回进程退出码。

    日志只写 stderr——stdout 是 JSON-RPC 协议通道，一个字节的脏输出都会毁掉协议。
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run_server())
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("MCP server 异常退出")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
