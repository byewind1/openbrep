"""MODIFY 修复路径的预算制 agent loop（实验新路径，默认关闭）。

与 `_handle_script_update` 的硬编码"最多两轮"修复循环并行存在：
- 旧路径：orchestration 代码硬编码决定何时生成、何时编译、修几轮；
- 本路径：AI 通过工具调用自主决定何时改脚本、何时编译、何时查知识库、
  何时判定完成；工具调用预算（默认 6 次）耗尽即强制退出并如实报告。

设计约束（与任务红线对齐）：
- 完全独立实现，不复用 `_handle_script_update`，新旧路径可随时整体拆分删除；
- 只通过 `TaskRequest.agent_loop=True` 显式启用，默认行为不受影响；
- 底层能力全部走 `modify_agent_tools.ModifyToolRegistry` 的薄封装。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

from openbrep.compiler import CompileResult
from openbrep.core import GDLAgent
from openbrep.gdl_sanitizer import sanitize_llm_script_output
from openbrep.hsf_project import HSFProject
from openbrep.llm import assistant_tool_calls_message, tool_result_message
from openbrep.runtime.modify_agent_tools import ModifyToolRegistry

if TYPE_CHECKING:
    from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, TaskResult

logger = logging.getLogger(__name__)


def _architect_status(stage: str, **ctx) -> dict[str, object]:
    """把内部执行阶段翻译成建筑师可读的中文状态文案。

    返回字典必须包含 "stage" 与 "message"，可直接传给 on_event("status", ...)。
    """
    labels: dict[str, str] = {
        "understand": "🤔 正在理解你的修改意图…",
        "think": "🧠 AI 正在思考下一步…",
        "locate": "🎯 正在定位需要修改的位置…",
        "plan": "📝 正在制定修改方案…",
        "modify": "✏️ 正在修改代码…",
        "compile": "🔨 正在编译验证…",
        "preview": "📐 正在核对几何预览…",
        "verify": "🔍 正在检查完成条件（编译 + 几何）…",
        "retry": "🧩 验证未通过，AI 继续修复…",
        "budget": "⚠️ 工具预算耗尽，停止迭代",
        "cancel": "⏹ 任务已取消",
        "done": "✅ 修改完成",
    }
    message = labels.get(stage, stage)
    # 允许用简单模板替换少量上下文
    if ctx:
        try:
            message = message.format(**ctx)
        except (KeyError, ValueError):
            pass
    return {"stage": stage, "message": message}


# 工具内部名 → 建筑师显示名 + 阶段
_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "update_script": ("修改脚本", "modify"),
    "compile_script": ("编译验证", "compile"),
    "run_static_check": ("静态检查", "compile"),
    "query_knowledge": ("查询 GDL 知识", "think"),
    "preview_geometry": ("预览几何", "preview"),
}


def _tool_display(name: str) -> tuple[str, str]:
    return _TOOL_DISPLAY.get(name, (name, "think"))


# 工具调用预算默认值与硬上限（防止异常配置导致失控循环）
DEFAULT_AGENT_LOOP_BUDGET = 10
MAX_AGENT_LOOP_BUDGET = 20
# 完成门禁：AI 宣称完成但证据未过时的最大打回次数
# （打回本身不消耗工具预算，但修复需要预算内的工具调用）
MAX_GATE_REJECTIONS = 2


def _completion_gate(project, registry, compiler, gsm_path: str):
    """AI 宣称完成时的结构化核验（S3）：编译 + 几何语义验证。

    判决者是确定性子系统（外部编译器 + 本地 previewer），与 AI 的生成
    上下文完全独立——把"禁止谎报完成"从提示词纪律升级为结构强制。
    返回 (passed, feedback_text, semantic_result)。
    """
    from openbrep.semantic_verifier import verify_semantics

    hsf_dir = project.save_to_disk()
    compile_result = compiler.hsf2libpart(str(hsf_dir), gsm_path)
    registry.last_compile_result = compile_result
    semantic_result = verify_semantics(project)
    blocking = [i for i in semantic_result.issues if i.blocking]
    if compile_result.success and not blocking:
        return True, "", semantic_result
    parts = ["完成门禁未通过，当前状态还不能交付："]
    if not compile_result.success:
        err = (compile_result.stderr or compile_result.stdout or "")[:600].strip()
        parts.append(f"\n编译失败：\n```\n{err}\n```")
    for issue in blocking:
        parts.append(f"- [{issue.check_type}] {issue.detail}")
    parts.append("\n请用工具继续修复（在剩余预算内），修复后再次确认完成。")
    return False, "\n".join(parts), semantic_result

# 追加到系统提示的 agent loop 协议说明（预算数字在运行时替换）
_AGENT_LOOP_PROTOCOL = """

---

## Agent Loop 工作模式（本次任务生效）

你可以使用以下工具，通过 tool_calls 自主推进任务：
- update_script：全量更新一个脚本/参数文件（改完必须编译验证）
- compile_script：编译当前工程，返回成功或错误信息
- run_static_check：静态检查（未定义变量、变换栈配平等）
- query_knowledge：查 GDL 命令签名 / 按意图推荐命令 / 诊断编译错误
- preview_geometry：轻量渲染 3D 脚本，返回 mesh 数量与包围盒

工作纪律：
1. 先用 update_script 做出最小改动，再 compile_script 验证；
2. 编译失败时根据错误信息继续修复，可用 query_knowledge(mode=diagnose) 诊断；
3. 工具调用预算共 {budget} 次，请规划使用，不要重复调用同一工具空转；
4. 确认完成后，直接以纯文本答复总结改动与编译结果（不再发起 tool_calls）；
5. 若预算不足，如实说明当前进度与遗留问题，禁止谎报完成。
"""

# 计划阶段协议：让 LLM 先输出可审查的修改计划，再进入工具执行。
_PLANNING_PROTOCOL = """

---

## 修改计划阶段（本次任务生效）

在调用任何工具前，请先根据用户指令和项目上下文，输出一份修改计划。
计划必须是合法 JSON，格式如下：

{
  "intent_summary": "一句话概括用户想做什么",
  "affected_files": ["可能改动的文件，如 scripts/3d.gdl 或 paramlist.xml"],
  "parameter_changes": [
    {"name": "参数名", "from": "当前值（可选）", "to": "目标值（可选）"}
  ],
  "strategy": "简要说明修改策略，用建筑师能理解的中文"
}

要求：
- 只输出 JSON，不要输出 Markdown 代码块标记；
- 若无法确定参数当前值，可省略 from/to；
- 输出计划后，系统会把计划展示给用户；用户未打断，你再按 plan 调用工具执行。
"""


def run_modify_agent_loop(pipeline: "TaskPipeline", request: "TaskRequest") -> "TaskResult":
    """预算制 agent loop 的 MODIFY/DEBUG/REPAIR 处理入口（实验新路径）。

    返回 TaskResult 的字段语义尽量与旧路径对齐（scripts/compile_result/
    plain_text/verification），方便 benchmark A/B 直接对比。
    """
    # 延迟导入 pipeline 内部工具函数，避免模块级循环依赖
    from openbrep.runtime.pipeline import TaskResult, _normalize_modify_request
    from openbrep.verification import build_verification_report
    from openbrep.static_checker import StaticChecker

    llm = pipeline._make_llm(request)
    compiler = pipeline._make_compiler()
    clean_instruction, syntax_report = _normalize_modify_request(request)
    intent = request.intent or "MODIFY"

    project = request.project
    if project is None:
        project = HSFProject.create_new(request.gsm_name or "untitled", work_dir=request.work_dir)
    request.project = project

    assembled = pipeline._assemble_context(
        request, project, instruction=clean_instruction, include_modify_rules=True,
    )
    on_event = request.on_event or (lambda *_: None)

    # 复用 GDLAgent 的上下文构建、[FILE:] 解析与变更应用逻辑；
    # 生成动作不走 generate_only，而由本 loop 用 generate_with_tools 驱动。
    agent = GDLAgent(
        llm=llm,
        compiler=compiler,
        on_event=on_event,
        assistant_settings=request.assistant_settings,
        should_cancel=request.should_cancel,
    )
    affected = project.get_affected_scripts(clean_instruction)
    context = agent._build_context(project, affected, include_all=True)
    messages = agent._build_messages(
        clean_instruction,
        context,
        assembled.generation_context,
        assembled.skills_text,
        error=None,
        history=request.history,
        chat_mode=True,
        syntax_report=syntax_report,
    )

    budget = request.agent_loop_budget or DEFAULT_AGENT_LOOP_BUDGET
    budget = max(1, min(budget, MAX_AGENT_LOOP_BUDGET))
    messages[0]["content"] = (messages[0].get("content") or "") + _AGENT_LOOP_PROTOCOL.format(budget=budget)

    from pathlib import Path
    out_dir = Path(request.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gsm_path = str(out_dir / f"{request.gsm_name or project.name}.gsm")

    registry = ModifyToolRegistry(
        project=project,
        compiler=compiler,
        output_gsm=gsm_path,
        apply_changes=agent._apply_changes,
        on_event=on_event,
    )
    tools = registry.definitions()

    llm_calls = 0
    tool_calls_used = 0
    budget_exhausted = False
    cancelled = False
    final_text = ""
    gate_rejections = 0
    gate_unresolved = False
    semantic_result = None

    on_event("status", _architect_status("understand"))

    # ── 计划阶段（可选）：LLM 先输出可审查的修改计划，用户 ESC 可打断 ──
    plan_data: dict[str, object] | None = None
    if request.agent_loop_plan:
        try:
            if request.should_cancel and request.should_cancel():
                cancelled = True
                on_event("status", _architect_status("cancel"))
                return _build_cancelled_result(request, project, registry, gsm_path, compiler, intent)
            on_event("status", _architect_status("plan"))
            planning_messages = _inject_planning_prompt(messages)
            plan_response = llm.generate(planning_messages)
            llm_calls += 1
            plan_data = _parse_plan_response(plan_response.content or "")
            if plan_data:
                on_event("plan", plan_data)
            if request.should_cancel and request.should_cancel():
                cancelled = True
                on_event("status", _architect_status("cancel"))
                return _build_cancelled_result(request, project, registry, gsm_path, compiler, intent)
            # 把计划作为 assistant 回复注入历史，约束后续工具调用
            plan_text = plan_response.content or ""
            if plan_text:
                messages.append({"role": "assistant", "content": plan_text})
                messages.append({
                    "role": "user",
                    "content": "计划已收到。如果我没有打断，请严格按上述计划调用工具执行修改。",
                })
        except Exception as exc:
            logger.warning("Planning stage failed: %s", exc)
            # 计划阶段失败不阻塞执行，降级到无计划继续

    # ── 主循环：有 tool_calls 就执行并回填，没有（纯文本答复）即声
    # 称完成——声称完成要过完成门禁（S3）：编译 + 几何语义的结构核验，
    # 未过则把确定性证据打回对话让 AI 继续（有界打回，防止无限扯皮）。
    while True:
        if request.should_cancel and request.should_cancel():
            cancelled = True
            on_event("status", _architect_status("cancel"))
            break
        on_event("status", _architect_status("think"))
        response = llm.generate_with_tools(messages, tools=tools)
        llm_calls += 1
        if response.content:
            on_event("assistant_delta", {"content": response.content})
        if not response.has_tool_calls:
            on_event("status", _architect_status("verify"))
            gate_ok, gate_feedback, semantic_result = _completion_gate(
                project, registry, compiler, gsm_path,
            )
            can_fix = tool_calls_used < budget
            if gate_ok or gate_rejections >= MAX_GATE_REJECTIONS or not can_fix:
                gate_unresolved = not gate_ok
                final_text = response.content or ""
                # 兜底：最终答复里若夹带 [FILE:] 块（旧 prompt 习惯），同样解析应用
                fallback_changes = agent._parse_response(final_text)
                if fallback_changes:
                    cleaned = {k: sanitize_llm_script_output(v, k) for k, v in fallback_changes.items()}
                    agent._apply_changes(project, cleaned)
                    registry.changed_files.update(cleaned)
                break
            gate_rejections += 1
            logger.info(
                "agent loop completion gate rejected (%d/%d)",
                gate_rejections, MAX_GATE_REJECTIONS,
            )
            on_event("status", _architect_status("retry", n=gate_rejections))
            messages.append({"role": "assistant", "content": response.content or ""})
            messages.append({"role": "user", "content": gate_feedback})
            continue

        messages.append(assistant_tool_calls_message(response))
        for call in response.tool_calls:
            if tool_calls_used >= budget:
                budget_exhausted = True
                break
            display_name, tool_stage = _tool_display(call.name)
            on_event("status", _architect_status(tool_stage, tool=display_name))
            result = registry.execute(call)
            tool_calls_used += 1
            messages.append(tool_result_message(call.id, result.summary, name=call.name))
            on_event("tool_call", {
                "name": call.name,
                "display_name": display_name,
                "stage": tool_stage,
                "summary": result.summary,
                "ok": result.ok,
            })
        if budget_exhausted:
            logger.info(
                "agent loop budget exhausted: %d/%d tool calls, %d llm calls",
                tool_calls_used, budget, llm_calls,
            )
            on_event("status", _architect_status("budget"))
            break

    compile_result = registry.last_compile_result
    if compile_result is None:
        # AI 全程未编译：为如实报告补跑一次最终编译（不算工具调用、不做修复）
        hsf_dir = project.save_to_disk()
        compile_result = compiler.hsf2libpart(str(hsf_dir), gsm_path)

    if semantic_result is None:
        # 预算耗尽/取消导致门禁未运行：为报告补一次语义验证（never raises）
        from openbrep.semantic_verifier import verify_semantics
        semantic_result = verify_semantics(project)

    # ── 组装输出：AI 总结 + loop 状态块（预算/工具记录/编译结果） ──
    output_parts: list[str] = []
    if final_text:
        output_parts.append(final_text)
    status_lines = [
        f"**Agent loop（实验路径）**：工具调用 {tool_calls_used}/{budget} 次，LLM 调用 {llm_calls} 次",
    ]
    if gate_rejections:
        status_lines.append(f"🧩 完成门禁打回 {gate_rejections} 次（AI 宣称完成但证据未过）")
    if gate_unresolved:
        status_lines.append("⚠️ 完成门禁未通过（编译/语义证据仍有问题），如实交付当前状态。")
    if budget_exhausted:
        status_lines.append("⚠️ 预算耗尽，AI 未能在预算内主动判定完成，以上为其当前进度。")
    if cancelled:
        status_lines.append("⚠️ 任务被取消，以上为中断时的进度。")
    tool_digest = _tool_digest(registry.tool_log)
    if tool_digest:
        status_lines.append(f"工具记录：{tool_digest}")
    if compile_result.success:
        status_lines.append("✅ 编译通过")
    else:
        short_err = (compile_result.stderr or "")[:300].strip()
        status_lines.append(f"❌ 编译失败：\n```\n{short_err}\n```")
    output_parts.append("\n".join(status_lines))

    static_result = StaticChecker().check(project)
    from openbrep.naming_alignment import detect_reserved_param_misuse
    verification_report = build_verification_report(
        intent=intent,
        user_input=request.user_input,
        project=project,
        object_plan=None,
        static_result=static_result,
        semantic_result=semantic_result,
        lint_summary="",
        compile_result=compile_result,
        auto_repair_info="",
        graph_powered=False,
        reserved_conflicts=detect_reserved_param_misuse(project),
    )
    output_parts.append(verification_report.to_summary_text())

    return TaskResult(
        success=verification_report.passed,
        intent=intent,
        scripts=registry.changed_files,
        plain_text="\n\n".join(part for part in output_parts if part),
        project=project,
        compile_result=compile_result,
        verification=verification_report.to_dict(),
    )


def _tool_digest(tool_log: list[dict]) -> str:
    """把工具调用日志压成一行摘要，如 update_script×2, compile_script×3。"""
    counts: dict[str, int] = {}
    for entry in tool_log:
        counts[entry["name"]] = counts.get(entry["name"], 0) + 1
    return ", ".join(f"{name}×{count}" for name, count in counts.items())


def _inject_planning_prompt(messages: list[dict]) -> list[dict]:
    """返回一份复制品：system prompt 追加 planning protocol。"""
    copy = [dict(m) for m in messages]
    if copy and copy[0].get("role") == "system":
        copy[0]["content"] = (copy[0].get("content") or "") + _PLANNING_PROTOCOL
    else:
        copy.insert(0, {"role": "system", "content": _PLANNING_PROTOCOL})
    return copy


def _parse_plan_response(content: str) -> dict[str, object] | None:
    """从 LLM 计划回复里提取 JSON plan。"""
    if not content:
        return None
    text = content.strip()
    # 允许模型把 JSON 包在 Markdown 代码块里
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 尝试从文本中提取第一个 { ... } 块
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return {
        "intent_summary": str(parsed.get("intent_summary") or "").strip(),
        "affected_files": list(parsed.get("affected_files") or []),
        "parameter_changes": list(parsed.get("parameter_changes") or []),
        "strategy": str(parsed.get("strategy") or "").strip(),
    }


def _build_cancelled_result(
    request: "TaskRequest",
    project: HSFProject,
    registry: ModifyToolRegistry,
    gsm_path: str,
    compiler,
    intent: str,
) -> "TaskResult":
    """用户在计划阶段取消时，返回一个干净的 TaskResult（未开始修改）。"""
    from openbrep.runtime.pipeline import TaskResult
    from openbrep.verification import build_verification_report
    from openbrep.static_checker import StaticChecker
    from openbrep.naming_alignment import detect_reserved_param_misuse

    hsf_dir = project.save_to_disk()
    compile_result = compiler.hsf2libpart(str(hsf_dir), gsm_path)
    registry.last_compile_result = compile_result
    static_result = StaticChecker().check(project)
    from openbrep.semantic_verifier import verify_semantics
    semantic_result = verify_semantics(project)
    verification_report = build_verification_report(
        intent=intent,
        user_input=request.user_input,
        project=project,
        object_plan=None,
        static_result=static_result,
        semantic_result=semantic_result,
        lint_summary="",
        compile_result=compile_result,
        auto_repair_info="",
        graph_powered=False,
        reserved_conflicts=detect_reserved_param_misuse(project),
    )
    return TaskResult(
        success=False,
        intent=intent,
        scripts={},
        plain_text="⏹ 任务已取消：AI 在制定计划阶段被打断，尚未修改任何文件。",
        project=project,
        compile_result=compile_result,
        verification=verification_report.to_dict(),
    )
