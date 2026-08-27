"""D10：Codex MODIFY 动态工具桥接（app-server dynamic tools → ModifyToolRegistry）。

把 ChatGPT Codex 订阅模型接入预算制 MODIFY agent loop：模型通过 app-server
的动态工具接口（``thread/start.dynamicTools`` + 服务器请求 ``item/tool/call``）
发起工具调用，OpenBrep 在本进程内用 ``ModifyToolRegistry`` 执行全部工具并把
结果写回 turn。安全不变量（D10 派单核心边界）：

- Codex 从不直接写 HSF/project cwd：app-server 只拿到一次性临时只读 cwd；
  thread/turn 参数面不含 shell / apply_patch / fs / MCP / 任何内建编辑工具。
- 工具执行唯一入口是 ``ModifyToolRegistry``（含 prose leak / String paramlist
  引用守卫 / 参数合法性校验）；任何写入都可对应 registry.tool_log 审计记录。
- 完成门禁复用 agent loop 的确定性判决（``_completion_gate``）：编译 + 语义
  验证，未通过不得 success。
- tool call/result 精确关联：call id 对应到一次执行；重复/迟到/错 thread/
  错 turn/未注册工具/越界 namespace 一律拒绝并审计，绝不污染后续轮次。
- 轮次/工具预算、lazy before revision、project epoch 守卫、取消与失败回滚
  语义与 ``modify_agent_loop.py`` 对齐。

本模块是"隐藏桥接"：feature flag ``llm.codex_modify_enabled`` 默认 false，
flag=false 时 pipeline 在调用本模块前 fail closed（全链路无入口）。
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from openbrep.codex.app_server import CodexAppServerError
from openbrep.codex.turn import (
    INTERRUPTED_TEXT,
    NO_FINAL_MESSAGE_TEXT,
    QUOTA_ERROR_TEXT,
    TIMEOUT_TEXT,
    TURN_ERROR_TEXT,
    build_turn_prompt,
    wire_model_name,
)
from openbrep.llm import ToolCall, ToolDefinition

if TYPE_CHECKING:
    from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, TaskResult

_LOGGER = logging.getLogger(__name__)

# 轮次/工具预算（与 agent loop 对齐）：默认 10，硬上限 20。
DEFAULT_MODIFY_BUDGET = 10
MAX_MODIFY_BUDGET = 20
# 完成门禁打回上限（与 agent loop 一致的有界重试，防无限扯皮）。
MAX_GATE_REJECTIONS = 2
# 单 turn 事件轮询间隔（秒）。
_POLL_INTERVAL = 0.25
# turn 默认截止（秒）：与 codex/turn.py 一致。
_DEFAULT_TURN_TIMEOUT = 90.0

# 稳定文案：绝不回显上游原文 / canary / 秘密。
MODIFY_FLAG_OFF_TEXT = "ChatGPT Codex 模型的修改（MODIFY）能力尚未开放，请求已拒绝。"
EPOCH_CHANGED_TEXT = "项目已切换，本次修改任务已中止，拒绝继续执行工具。"
BUDGET_EXHAUSTED_TOOL_TEXT = "工具预算已耗尽，无法执行更多工具调用。请如实总结当前进度。"
DUPLICATE_CALL_TEXT = "该工具调用已处理过（重复回调），结果已在上一次执行中返回，本次不重复执行。"
FORBIDDEN_TOOL_TEXT = "工具 {name} 不在开放工具列表内，调用被拒绝。"
UNHANDLED_SERVER_REQUEST_TEXT = "method not found"

# 纵深防御：即使 fake/恶意 app-server 伪造写路径名，也必须在进入 registry
# 前被拒绝；允许名单只来自 registry.definitions()。
_DANGEROUS_TOOL_PATTERNS = (
    "shell", "apply_patch", "patch", "fs", "mcp", "command", "exec",
    "bash", "python", "terminal", "browser", "computer", "web",
)



def _dynamic_tool_specs(tools: list[ToolDefinition]) -> list[dict]:
    """把 ModifyToolRegistry 的工具定义转换成 app-server dynamicTools 线协议。

    （0.147.0：thread/start 的 experimental ``dynamicTools`` 字段，camelCase：
    {type: "function", name, description, inputSchema}；inputSchema 直接复用
    OpenAI function 的 JSON Schema。）只转换描述信息，不携带任何执行逻辑。
    """
    specs: list[dict] = []
    for tool in tools:
        specs.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters or {"type": "object", "properties": {}},
            }
        )
    return specs


def _tool_allowlist(tools: list[ToolDefinition]) -> set[str]:
    return {t.name for t in tools}


def _is_dangerous_tool_name(name: str) -> bool:
    low = (name or "").strip().lower()
    return any(low == p or low.startswith(p) for p in _DANGEROUS_TOOL_PATTERNS)


@dataclass
class CodexModifyTurnOutcome:
    """单次 Codex turn 的结果（动态工具桥接专用）。

    finish_reason 语义与 codex/turn.py.CodexTurnResult 对齐：
    stop / no_final_message / interrupted / timeout / error。
    """

    content: str = ""
    finish_reason: str = "no_final_message"
    error: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    # 本次 turn 内每个服务器请求的线级处置记录（审计用）：
    # {"request_id", "call_id", "tool", "text", "success"}
    requests: list[dict] = field(default_factory=list)


def _looks_like_quota(payload: Any) -> bool:
    """错误通知是否命中额度信号（与 codex/turn.py 同一判定）。"""
    text = ""
    if isinstance(payload, dict):
        info = payload.get("codexErrorInfo")
        if isinstance(info, str):
            text += " " + info
        err = payload.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str):
                text += " " + message
            info2 = err.get("codexErrorInfo")
            if isinstance(info2, str):
                text += " " + info2
        message = payload.get("message")
        if isinstance(message, str):
            text += " " + message
    else:
        text = str(payload)
    low = text.lower()
    return any(
        s in low
        for s in (
            "usagelimitexceeded", "sessionbudgetexceeded", "ratelimitreached",
            "rate limit", "quota", "usage limit", "insufficient",
        )
    )


def _stable_error_text(payload: Any) -> str:
    if _looks_like_quota(payload):
        return QUOTA_ERROR_TEXT
    return TURN_ERROR_TEXT


# ── turn 级收集器 ─────────────────────────────────────────────

class _ModifyTurnCollector:
    """一次 Codex turn 的收集器：通知 + 服务器请求（D10）。

    reader 线程调用 handle_notification / handle_server_request（必须快：
    只入队，绝不抛异常）；驱动线程消费。不匹配（迟到 / 乱序 / 他 thread /
    他 turn）一律忽略；畸形帧由 transport 层丢弃。
    """

    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._enabled = True
        self._item_phases: dict[str, str | None] = {}

    def close(self) -> None:
        self._enabled = False

    def record_item_phase(self, item_id: Any, phase: Any) -> None:
        if not isinstance(item_id, str) or not item_id:
            return
        if isinstance(phase, str) and phase in ("commentary", "final_answer"):
            self._item_phases[item_id] = phase
        elif item_id not in self._item_phases:
            self._item_phases[item_id] = None

    def is_commentary(self, item_id: Any) -> bool:
        if not isinstance(item_id, str) or not item_id:
            return False
        return self._item_phases.get(item_id) == "commentary"

    # ── 通知（transport.subscribe 投递）────────────────────────

    def handle_notification(self, msg: dict) -> None:
        if not self._enabled:
            return
        method = msg.get("method")
        params = msg.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if self.thread_id is None or thread_id != self.thread_id:
            return  # 他 thread / 未知 thread：忽略
        turn_id = params.get("turnId")
        if self.turn_id is not None and turn_id is not None and turn_id != self.turn_id:
            return  # 他 turn：忽略
        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, dict):
                tid = turn.get("id")
                if isinstance(tid, str) and tid:
                    self.turn_id = tid
            self.q.put(("turn_started", params))
        elif method == "item/started":
            item = params.get("item")
            if isinstance(item, dict):
                self.record_item_phase(item.get("id"), item.get("phase"))
            self.q.put(("item_started", item if isinstance(item, dict) else {}))
        elif method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                self.q.put(("delta", (params.get("itemId"), delta)))
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                self.record_item_phase(item.get("id"), item.get("phase"))
                self.q.put(("item_completed", item))
        elif method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict):
                self.q.put(("turn_completed", turn))
        elif method == "error":
            self.q.put(("error", params))

    # ── 服务器请求（transport.subscribe_server_request 投递）────────

    def handle_server_request(self, req_id: int, method: str, params: dict) -> None:
        if not self._enabled:
            return
        if method == "item/tool/call":
            self.q.put(("tool_call", (req_id, params if isinstance(params, dict) else {})))
        else:
            # 未预期服务器请求：交给驱动线程以 JSON-RPC -32601 回应
            # （绝不执行任何工具、绝不静默批准）。
            self.q.put(("server_request_unhandled", (req_id, method, params)))



class CodexModifyTurnDriver:
    """在单个 app-server client 上驱动一次带动态工具的 Codex turn。

    executor(call_id, namespace, tool, arguments) -> (result_text, success)：
    由桥接层提供（预算/epoch/allowlist/registry 执行都在那里）。
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        cwd: str,
        system_text: str,
        dynamic_tools: list[dict],
        executor: Callable[[str, str | None, str, Any], tuple[str, bool]],
        timeout: float | None,
        should_cancel: Callable[[], bool] | None,
        on_delta: Callable[[str], None] | None,
        reasoning_effort: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._cwd = str(cwd)
        self._system_text = system_text
        self._dynamic_tools = dynamic_tools
        self._executor = executor
        self._timeout = timeout
        self._should_cancel = should_cancel
        self._on_delta = on_delta
        self._reasoning_effort = reasoning_effort
        self._logger = logger or _LOGGER

    # ── 参数构建（对齐 turn.py；dynamicTools 只走 thread/start）────

    def _thread_start_params(self) -> dict:
        return {
            "model": wire_model_name(self._model),
            "cwd": self._cwd,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "serviceName": "openbrep",
            "threadSource": "openbrep-modify",
            "baseInstructions": self._system_text or None,
            "dynamicTools": list(self._dynamic_tools),
        }

    def _turn_start_params(self, thread_id: str, user_text: str) -> dict:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": user_text, "text_elements": []}],
            "model": wire_model_name(self._model),
            "cwd": self._cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        effort = str(self._reasoning_effort or "").strip()
        if effort:
            params["effort"] = effort
        return params

    # ── 中断 / 清理（best-effort，绝不抛错打断主流程）──────────────

    def _interrupt(self, thread_id: str | None, turn_id: str | None) -> None:
        if not thread_id or not turn_id:
            return
        try:
            self._client.turn_interrupt({"threadId": thread_id, "turnId": turn_id})
        except Exception as exc:  # noqa: BLE001 —— 只记稳定事件名
            self._logger.warning("codex modify turn interrupt 失败（%s）", exc.__class__.__name__)

    def _cleanup_thread(self, thread_id: str | None) -> None:
        if not thread_id:
            return
        try:
            self._client.thread_delete({"threadId": thread_id})
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("codex modify thread 清理失败（%s）", exc.__class__.__name__)

    # ── 主流程 ─────────────────────────────────────────────────

    def run(self, user_text: str) -> CodexModifyTurnOutcome:
        transport = getattr(self._client, "transport", None)
        collector = _ModifyTurnCollector()
        subscribed = subscribed_req = False
        if transport is not None and hasattr(transport, "subscribe"):
            transport.subscribe(collector.handle_notification)
            subscribed = True
        if transport is not None and hasattr(transport, "subscribe_server_request"):
            transport.subscribe_server_request(collector.handle_server_request)
            subscribed_req = True
        try:
            thread_resp = self._client.thread_start(self._thread_start_params())
            thread = thread_resp.get("thread") or {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise CodexAppServerError(
                    "Codex 对话线程创建失败，请稍后重试。", category="rpc_error"
                )
            collector.thread_id = thread_id

            turn_resp = self._client.turn_start(
                self._turn_start_params(thread_id, user_text)
            )
            turn = turn_resp.get("turn") or {}
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                collector.turn_id = turn_id
            outcome = CodexModifyTurnOutcome(thread_id=thread_id, turn_id=turn_id)

            deadline = time.monotonic() + (
                float(self._timeout) if self._timeout and self._timeout > 0
                else _DEFAULT_TURN_TIMEOUT
            )
            candidates: list[tuple[str, str]] = []
            delta_buf: dict[str, list[str]] = {}
            terminal: CodexModifyTurnOutcome | None = None
            while terminal is None:
                if self._should_cancel is not None:
                    try:
                        cancelled = bool(self._should_cancel())
                    except Exception:  # noqa: BLE001
                        cancelled = False
                    if cancelled:
                        self._interrupt(thread_id, collector.turn_id)
                        terminal = CodexModifyTurnOutcome(
                            finish_reason="interrupted",
                            thread_id=thread_id,
                            turn_id=collector.turn_id,
                            error=INTERRUPTED_TEXT,
                        )
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._interrupt(thread_id, collector.turn_id)
                    terminal = CodexModifyTurnOutcome(
                        finish_reason="timeout",
                        thread_id=thread_id,
                        turn_id=collector.turn_id,
                        error=TIMEOUT_TEXT,
                    )
                    break
                try:
                    event = collector.q.get(timeout=min(remaining, _POLL_INTERVAL))
                except queue.Empty:
                    continue
                kind, payload = event
                if kind == "tool_call":
                    self._handle_tool_call(payload, thread_id, collector.turn_id, outcome)
                elif kind == "server_request_unhandled":
                    req_id, method, _params = payload
                    outcome.requests.append({
                        "request_id": req_id, "call_id": "", "tool": method,
                        "text": UNHANDLED_SERVER_REQUEST_TEXT, "success": False,
                    })
                    try:
                        transport.respond(req_id, {
                            "error": {"code": -32601, "message": UNHANDLED_SERVER_REQUEST_TEXT},
                        })
                    except Exception as exc:  # noqa: BLE001
                        self._logger.warning(
                            "codex modify server request 回应失败（%s）", exc.__class__.__name__
                        )
                elif kind == "delta":
                    item_id, delta = payload
                    if not collector.is_commentary(item_id):
                        if isinstance(item_id, str):
                            delta_buf.setdefault(item_id, []).append(delta)
                        if self._on_delta is not None:
                            try:
                                self._on_delta(delta)
                            except Exception as exc:  # noqa: BLE001
                                self._logger.warning(
                                    "codex modify delta 回调异常（%s）", exc.__class__.__name__
                                )
                elif kind == "item_started":
                    continue  # phase 已记录
                elif kind == "item_completed":
                    self._record_item(payload, candidates, delta_buf)
                elif kind == "turn_completed":
                    terminal = self._finalize_turn(payload, candidates, outcome)
                elif kind == "error":
                    terminal = CodexModifyTurnOutcome(
                        finish_reason="error",
                        thread_id=thread_id,
                        turn_id=collector.turn_id,
                        error=_stable_error_text(payload),
                    )
            # 迟到/取消后到/未消费的服务器请求：turn 已结束，逐个拒绝并回应
            # （绝不执行，也绝不让 app-server 挂着等响应）。
            self._drain_late_requests(collector, transport, outcome)
            return terminal
        finally:
            if subscribed and transport is not None:
                try:
                    transport.unsubscribe(collector.handle_notification)
                except Exception:  # noqa: BLE001
                    pass
            if subscribed_req and transport is not None:
                try:
                    transport.unsubscribe_server_request(collector.handle_server_request)
                except Exception:  # noqa: BLE001
                    pass
            collector.close()
            self._cleanup_thread(collector.thread_id or None)

    # ── 工具请求处置 ───────────────────────────────────────────

    def _handle_tool_call(
        self,
        payload: tuple[int, dict],
        thread_id: str | None,
        turn_id: str | None,
        outcome: CodexModifyTurnOutcome,
    ) -> None:
        transport = getattr(self._client, "transport", None)
        req_id, params = payload
        call_id = params.get("callId")
        namespace = params.get("namespace")
        tool = params.get("tool")
        arguments = params.get("arguments")
        # 关联纪律：thread / turn 必须与当前 turn 一致；call_id 必须是
        # 非空字符串；namespace 必须为空（本桥接不使用命名空间工具）；
        # tool 必须是非空字符串。
        if (
            str(params.get("threadId") or "") != str(thread_id or "")
            or str(params.get("turnId") or "") != str(turn_id or "")
            or not isinstance(call_id, str)
            or not call_id
            or (namespace not in (None, ""))
            or not isinstance(tool, str)
            or not tool
        ):
            outcome.requests.append({
                "request_id": req_id,
                "call_id": call_id if isinstance(call_id, str) else "",
                "tool": str(tool),
                "text": "工具调用帧校验失败，调用被拒绝。",
                "success": False,
            })
            self._respond_tool(transport, req_id, "工具调用帧校验失败，调用被拒绝。", False)
            return
        if not isinstance(arguments, dict):
            arguments = {}
        text, success = self._executor(call_id, namespace, tool, arguments)
        outcome.requests.append({
            "request_id": req_id, "call_id": call_id, "tool": tool,
            "text": text, "success": success,
        })
        self._respond_tool(transport, req_id, text, success)

    @staticmethod
    def _respond_tool(transport: Any, req_id: int, text: str, success: bool) -> None:
        if transport is None or not hasattr(transport, "respond"):
            return
        result = {
            "contentItems": [{"type": "inputText", "text": str(text)[:4000]}],
            "success": bool(success),
        }
        try:
            transport.respond(req_id, result)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("codex modify tool 回应失败（%s）", exc.__class__.__name__)

    def _drain_late_requests(
        self,
        collector: _ModifyTurnCollector,
        transport: Any,
        outcome: CodexModifyTurnOutcome,
    ) -> None:
        """turn 结束后丢弃并回应仍在队列中的服务器请求（拒绝语义）。"""
        while True:
            try:
                kind, payload = collector.q.get_nowait()
            except queue.Empty:
                return
            if kind == "tool_call":
                req_id, params = payload
                outcome.requests.append({
                    "request_id": req_id,
                    "call_id": str(params.get("callId") or ""),
                    "tool": str(params.get("tool") or ""),
                    "text": "turn 已结束，工具调用被拒绝。",
                    "success": False,
                })
                self._respond_tool(transport, req_id, "turn 已结束，工具调用被拒绝。", False)
            elif kind == "server_request_unhandled":
                req_id, method, _params = payload
                outcome.requests.append({
                    "request_id": req_id, "call_id": "", "tool": method,
                    "text": UNHANDLED_SERVER_REQUEST_TEXT, "success": False,
                })
                try:
                    transport.respond(req_id, {
                        "error": {"code": -32601, "message": UNHANDLED_SERVER_REQUEST_TEXT},
                    })
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "codex modify server request 回应失败（%s）", exc.__class__.__name__
                    )

    @staticmethod
    def _record_item(
        item: dict,
        candidates: list[tuple[str, str]],
        delta_buf: dict[str, list[str]],
    ) -> None:
        if item.get("type") != "agentMessage":
            return
        phase = item.get("phase")
        if phase == "commentary":
            return
        item_id = item.get("id")
        text = str(item.get("text") or "")
        if not text and isinstance(item_id, str):
            text = "".join(delta_buf.get(item_id, []))
        candidates.append((item_id if isinstance(item_id, str) else "", text))

    @staticmethod
    def _finalize_turn(
        turn: dict,
        candidates: list[tuple[str, str]],
        outcome: CodexModifyTurnOutcome,
    ) -> CodexModifyTurnOutcome:
        status = turn.get("status")
        if status == "interrupted":
            outcome.finish_reason = "interrupted"
            outcome.error = INTERRUPTED_TEXT
            return outcome
        if status != "completed":
            outcome.finish_reason = "error"
            outcome.error = _stable_error_text(turn.get("error"))
            return outcome
        if not candidates:
            outcome.finish_reason = "no_final_message"
            outcome.error = NO_FINAL_MESSAGE_TEXT
            return outcome
        _, text = candidates[-1]
        if not text.strip():
            outcome.finish_reason = "no_final_message"
            outcome.error = NO_FINAL_MESSAGE_TEXT
            return outcome
        outcome.content = text
        outcome.finish_reason = "stop"
        outcome.error = None
        return outcome



# ── 桥接协议注入（system 提示追加；与 modify_agent_loop 同构）────────

_MODIFY_BRIDGE_PROTOCOL = """

---

## Agent Loop 工作模式（本次任务生效，Codex 动态工具桥接）

你可以通过工具调用接口使用以下工具，自主推进任务：
- patch_script：局部编辑（精确匹配替换若干段文本，diff 级最小改动；优先使用）
- update_script：全量重写一个脚本/参数文件（仅当需要整文件重写时才用）
- compile_script：编译当前工程，返回成功或错误信息
- run_static_check：静态检查（未定义变量、变换栈配平等）
- query_knowledge：查 GDL 命令签名 / 按意图推荐命令 / 诊断编译错误
- preview_geometry：轻量渲染 3D 脚本，返回 mesh 数量与包围盒

工作纪律：
1. 局部改动优先用 patch_script 做最小 diff，整文件重写才用 update_script；
   每次修改后调用 compile_script 验证；
2. 编译失败时根据错误信息继续修复，可用 query_knowledge(mode=diagnose) 诊断；
3. 工具调用预算共 {budget} 次，请规划使用，不要重复调用同一工具空转；
4. 确认完成后，直接以纯文本答复总结改动与编译结果（不再发起工具调用）；
5. 若预算不足，如实说明当前进度与遗留问题，禁止谎报完成；
6. 本通道不接收 [FILE:] 交付块：改动必须通过工具调用落盘
   （patch_script / update_script），回复里的 [FILE:] 内容不会被应用；
7. 完成声明会经过独立的编译 + 语义验证门禁核验，未通过会被打回并附上
   确定性证据，请在剩余预算内继续用工具修复。
"""


class CodexModifyBridge:
    """app-server dynamic tools → ModifyToolRegistry 的完整桥接执行器。

    一次性使用：构造后调用 ``run()``。状态（audit / budget / seen call ids /
    before revision / epoch guard）全部集中在实例上。
    """

    def __init__(
        self,
        *,
        pipeline: "TaskPipeline",
        request: "TaskRequest",
        provider: Any,
        model: str,
        reasoning_effort: str,
        logger: logging.Logger | None = None,
    ) -> None:
        from openbrep.core import GDLAgent
        from openbrep.hsf_project import HSFProject
        from openbrep.runtime.modify_agent_tools import ModifyToolRegistry
        from openbrep.runtime.pipeline import _normalize_modify_request

        self.pipeline = pipeline
        self.request = request
        self.provider = provider
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.logger = logger or _LOGGER
        self.intent = request.intent or "MODIFY"
        self.on_event = request.on_event or (lambda *_: None)

        compiler = pipeline._make_compiler()
        self.compiler = compiler
        llm = pipeline._make_llm(request)
        clean_instruction, syntax_report = _normalize_modify_request(request)

        project = request.project
        if project is None:
            project = HSFProject.create_new(
                request.gsm_name or "untitled", work_dir=request.work_dir
            )
        request.project = project
        self.project = project

        assembled = pipeline._assemble_context(
            request, project, instruction=clean_instruction, include_modify_rules=True,
        )
        agent = GDLAgent(
            llm=llm,
            compiler=compiler,
            on_event=self.on_event,
            assistant_settings=request.assistant_settings,
            should_cancel=request.should_cancel,
        )
        self.agent = agent
        self.clean_instruction = clean_instruction
        self.syntax_report = syntax_report

        affected = project.get_affected_scripts(clean_instruction)
        context = agent._build_context(project, affected, include_all=True)
        self.messages = agent._build_messages(
            clean_instruction,
            context,
            assembled.generation_context,
            assembled.skills_text,
            error=None,
            history=request.history,
            chat_mode=True,
            syntax_report=syntax_report,
        )

        budget = request.agent_loop_budget or DEFAULT_MODIFY_BUDGET
        self.budget = max(1, min(budget, MAX_MODIFY_BUDGET))
        self.messages[0]["content"] = (
            (self.messages[0].get("content") or "")
            + _MODIFY_BRIDGE_PROTOCOL.format(budget=self.budget)
        )

        # 修改前验收快照（必须在任何修改前取）
        from openbrep.runtime.modify_acceptance import preview_geometry_summary
        self.before_params = [(p.name, p.value) for p in project.parameters]
        self.before_preview = preview_geometry_summary(project)

        # 惰性 before revision
        from openbrep.revisions import get_latest_revision_id
        from openbrep.runtime.pipeline import _can_revision_project, _create_auto_revision
        self._can_revision_project = _can_revision_project
        self._create_auto_revision = _create_auto_revision
        self._get_latest_revision_id = get_latest_revision_id
        self.before_revision_id: str | None = None

        out_dir = Path(request.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.gsm_path = str(out_dir / f"{request.gsm_name or project.name}.gsm")

        self.registry = ModifyToolRegistry(
            project=project,
            compiler=compiler,
            output_gsm=self.gsm_path,
            apply_changes=agent._apply_changes,
            on_event=self.on_event,
        )
        self.tools = self.registry.definitions()
        self.tool_specs = _dynamic_tool_specs(self.tools)
        self.allowlist = _tool_allowlist(self.tools)

        # 桥接状态
        self.tool_calls_used = 0
        self.turns = 0
        self.budget_exhausted = False
        self.cancelled = False
        self.gate_rejections = 0
        self.gate_unresolved = False
        self.epoch_violated = False
        self.seen_call_ids: set[str] = set()
        self.audit: list[dict] = []
        self.turn_outcomes: list[CodexModifyTurnOutcome] = []
        self.epoch_guard = getattr(request, "epoch_guard", None)

    # ── 写侧预处理 ─────────────────────────────────────────────

    def _ensure_before_revision(self) -> None:
        if self.before_revision_id is not None:
            return
        if not self._can_revision_project(self.project):
            self.logger.warning("codex modify: project not revisionable, mutating without snapshot")
            self.on_event("status", {"message": "⚠️ 项目未保存为 HSF 目录，本次修改无版本快照"})
            self.before_revision_id = ""
            return
        self.before_revision_id, warning = self._create_auto_revision(
            self.project,
            message=f"auto: before {self.intent.lower()} (codex modify bridge)",
            trigger=self.intent.lower(),
            intent=self.intent,
            user_instruction=self.clean_instruction,
            changed_files=[],
            parent_revision_id=self._get_latest_revision_id(self.project.root),
        )
        if warning:
            self.logger.warning("codex modify before-revision: %s", warning)

    def _epoch_ok(self) -> bool:
        if self.epoch_guard is None:
            return True
        try:
            return bool(self.epoch_guard())
        except Exception:  # noqa: BLE001 —— 守卫异常按通过处理（服务层兜底）
            return True

    # ── 工具执行器（driver 回调；唯一执行/拒绝入口）──────────────

    def execute_tool_call(
        self, call_id: str, namespace: str | None, tool: str, arguments: Any
    ) -> tuple[str, bool]:
        """执行或拒绝一次动态工具调用；每次调用都产生一条审计记录。"""
        entry: dict[str, Any] = {
            "call_id": call_id, "tool": tool, "namespace": namespace,
            "executed": False, "ok": False, "rejected_reason": None,
        }
        if not self._epoch_ok():
            self.epoch_violated = True
            entry["rejected_reason"] = "epoch_changed"
            self.audit.append(entry)
            return EPOCH_CHANGED_TEXT, False
        if tool in self.allowlist:
            # 注册表 allowlist（来自 ModifyToolRegistry.definitions()）是可信源：
            # allowlist 内的名字（含 patch_script）永远可达，危险名规则不得
            # 误杀合法注册工具（P1 修复，验收 e394981）。
            pass
        elif _is_dangerous_tool_name(tool):
            # 纵深防御：非 allowlist 的伪造写路径名（shell/apply_patch/MCP/
            # fs/...）优先拒绝并区分审计原因
            entry["rejected_reason"] = "dangerous_tool_name"
            self.audit.append(entry)
            return FORBIDDEN_TOOL_TEXT.format(name=tool), False
        else:
            entry["rejected_reason"] = "tool_not_allowed"
            self.audit.append(entry)
            return FORBIDDEN_TOOL_TEXT.format(name=tool), False
        if call_id in self.seen_call_ids:
            # 重复回调：first-response-wins——不重复执行，二次直接拒绝
            entry["rejected_reason"] = "duplicate_call"
            self.audit.append(entry)
            return DUPLICATE_CALL_TEXT, False
        if self.tool_calls_used >= self.budget:
            self.budget_exhausted = True
            entry["rejected_reason"] = "budget_exhausted"
            self.audit.append(entry)
            return BUDGET_EXHAUSTED_TOOL_TEXT, False
        self.seen_call_ids.add(call_id)
        if tool in ("update_script", "patch_script"):
            # 写工具首次执行前惰性快照一次（零快照主洞修复，见 agent loop）
            self._ensure_before_revision()
        call = ToolCall(
            id=call_id,
            name=tool,
            arguments=arguments if isinstance(arguments, dict) else {},
            raw_arguments=json.dumps(arguments) if isinstance(arguments, dict) else "{}",
        )
        result = self.registry.execute(call)
        self.tool_calls_used += 1
        entry["executed"] = True
        entry["ok"] = result.ok
        self.audit.append(entry)
        self.on_event("tool_call", {
            "name": tool,
            "display_name": tool,
            "stage": "think",
            "summary": result.summary,
            "ok": result.ok,
        })
        return result.summary, result.ok


    # ── 主循环 ─────────────────────────────────────────────────

    def run(self) -> "TaskResult":
        """执行完整的多轮桥接直到完成门禁通过 / 预算耗尽 / 取消 / 失败。"""
        from openbrep.runtime.modify_agent_loop import _completion_gate  # 同一判决器

        system_text, user_text = build_turn_prompt(self.messages)

        turn_cwd = Path(tempfile.mkdtemp(prefix="openbrep-codex-modify-"))
        client = None
        try:
            client, _gen = self.provider._snapshot()
            if client is None:
                return TaskResult(
                    success=False, intent=self.intent,
                    plain_text=TURN_ERROR_TEXT, error="codex client unavailable",
                )
            if not user_text.strip():
                from openbrep.codex.turn import EMPTY_INPUT_TEXT
                return TaskResult(success=False, intent=self.intent, plain_text=EMPTY_INPUT_TEXT)
            if self.epoch_guard is not None and not self._epoch_ok():
                return TaskResult(
                    success=False, intent=self.intent, plain_text=EPOCH_CHANGED_TEXT,
                    error="epoch_changed",
                )

            self.on_event("status", {"stage": "understand", "message": "🤔 正在理解你的修改意图…"})
            kwargs = {
                "client": client,
                "model": self.model,
                "cwd": str(turn_cwd),
                "system_text": system_text,
                "dynamic_tools": self.tool_specs,
                "executor": self.execute_tool_call,
                "should_cancel": self.request.should_cancel,
                "on_delta": None,
                "reasoning_effort": self.reasoning_effort,
                "logger": self.logger,
            }
            timeout = None
            cfg = getattr(self.pipeline.config, "llm", None)
            if cfg is not None and getattr(cfg, "timeout", None):
                timeout = float(cfg.timeout)
            kwargs["timeout"] = timeout

            driver = CodexModifyTurnDriver(**kwargs)
            self.last_driver = driver

            current_input = user_text
            final_text = ""
            compile_result = None
            semantic_result = None
            gate_unresolved = False
            file_block_warning = ""
            file_block_warnings: list[str] = []
            last_error_text = ""
            # 逐 turn（turn 数受门禁打回上限约束）。注意：每个 turn 都新建
            # ephemeral thread（CodexModifyTurnDriver.run 每次 thread/start），
            # 模型在门禁打回续轮时只看到 system 提示 + 门禁反馈文本，上一轮
            # 对话/工具结果上下文不保留——这是刻意的最小面 fail-safe 语义
            # （验收 e394981 P2 更正，与实施报告同步）。
            while True:
                if self.request.should_cancel is not None:
                    try:
                        if self.request.should_cancel():
                            self.cancelled = True
                            break
                    except Exception:  # noqa: BLE001
                        pass
                if self.epoch_guard is not None and not self._epoch_ok():
                    self.epoch_violated = True
                    break
                self.turns += 1
                self.on_event("status", {"stage": "think", "message": "🧠 AI 正在思考下一步…"})
                try:
                    outcome = driver.run(current_input)
                except CodexAppServerError as exc:
                    # app-server 崩溃/关闭/写入失败：fail closed，如实报告
                    self.logger.warning(
                        "codex modify turn 驱动失败（category=%s）",
                        exc.category or exc.__class__.__name__,
                    )
                    outcome = CodexModifyTurnOutcome(
                        finish_reason="error", error=TURN_ERROR_TEXT,
                    )
                except Exception as exc:  # noqa: BLE001 —— 稳定文案兜底
                    self.logger.warning(
                        "codex modify turn 驱动异常（%s）", exc.__class__.__name__
                    )
                    outcome = CodexModifyTurnOutcome(
                        finish_reason="error", error=TURN_ERROR_TEXT,
                    )
                self.turn_outcomes.append(outcome)
                compile_result = self.registry.last_compile_result
                if self.epoch_violated or (self.epoch_guard is not None and not self._epoch_ok()):
                    self.epoch_violated = True
                    break

                if outcome.finish_reason == "stop":
                    final_text = outcome.content
                    # [FILE:] 兜底被拒：本通道不把 final 文本当作交付通道。
                    file_blocks = _has_file_blocks(final_text)
                    file_block_warning = (
                        "⚠️ 检测到回复中的 [FILE:] 内容：本通道（Codex 桥接）不应用 "
                        "[FILE:] 交付块，改动必须通过工具调用落盘。"
                    ) if file_blocks else ""
                    if file_blocks:
                        file_block_warnings.append(file_block_warning)
                    # A：gate 时序——门禁评估改动后的项目（工具已全程落盘）
                    self.on_event("status", {
                        "stage": "verify", "message": "🔍 正在检查完成条件（编译 + 几何）…",
                    })
                    gate_ok, gate_feedback, semantic_result = _completion_gate(
                        self.project, self.registry, self.compiler, self.gsm_path,
                    )
                    compile_result = self.registry.last_compile_result
                    # 零工具 + 文本夹带 [FILE:] 变更 = 未验证交付：打回指引回工具链
                    unverified_delivery = file_blocks and self.tool_calls_used == 0
                    if unverified_delivery:
                        gate_ok = False
                        gate_feedback = (
                            "你的改动未通过工具链验证（本轮未调用任何工具，且回复含 "
                            "[FILE:] 内容）。请调用 compile_script 验证，若需调整请用 "
                            "patch_script。"
                        )
                    if gate_ok:
                        break
                    can_fix = self.tool_calls_used < self.budget
                    if self.gate_rejections >= MAX_GATE_REJECTIONS or not can_fix:
                        gate_unresolved = True
                        break
                    self.gate_rejections += 1
                    self.logger.info(
                        "codex modify completion gate rejected (%d/%d)",
                        self.gate_rejections, MAX_GATE_REJECTIONS,
                    )
                    self.on_event("status", {
                        "stage": "retry", "message": "🧩 验证未通过，AI 继续修复…",
                    })
                    current_input = gate_feedback
                    continue
                # 非 stop：无法继续推进（错误/超时/无 final/中断）
                final_text = outcome.content
                last_error_text = outcome.error or last_error_text
                if outcome.finish_reason == "interrupted":
                    self.cancelled = True
                break

            if self.cancelled:
                self.on_event("status", {"stage": "cancel", "message": "⏹ 任务已取消"})
            elif self.epoch_violated:
                self.on_event("status", {"stage": "cancel", "message": EPOCH_CHANGED_TEXT})
            elif self.budget_exhausted:
                self.on_event("status", {"stage": "budget", "message": "⚠️ 工具预算耗尽，停止迭代"})

            # 如实报告的最终编译（AI 全程未编译时补跑一次；不算工具调用）
            if compile_result is None:
                hsf_dir = self.project.save_to_disk()
                compile_result = self.compiler.hsf2libpart(str(hsf_dir), self.gsm_path)
                self.registry.last_compile_result = compile_result

            if semantic_result is None:
                from openbrep.semantic_verifier import verify_semantics
                semantic_result = verify_semantics(self.project)

            return self._assemble_result(
                final_text=final_text,
                compile_result=compile_result,
                semantic_result=semantic_result,
                gate_unresolved=gate_unresolved,
                file_block_warning=file_block_warning,
                file_block_warnings=file_block_warnings,
                last_error_text=last_error_text,
            )
        finally:
            # 临时 cwd 用完即删；thread 清理由 driver finally 保证
            shutil.rmtree(turn_cwd, ignore_errors=True)

    def _assemble_result(
        self,
        *,
        final_text: str,
        compile_result: Any,
        semantic_result: Any,
        gate_unresolved: bool,
        file_block_warning: str,
        file_block_warnings: list[str] | None = None,
        last_error_text: str = "",
    ) -> "TaskResult":
        """组装 TaskResult：与 modify_agent_loop 输出结构对齐 + codex 审计元数据。"""
        from openbrep.naming_alignment import detect_reserved_param_misuse
        from openbrep.runtime.modify_acceptance import (
            build_modify_acceptance,
            preview_geometry_summary,
        )
        from openbrep.runtime.pipeline import TaskResult
        from openbrep.static_checker import StaticChecker
        from openbrep.verification import build_verification_report

        # 反馈信号采集（只采集，best-effort；不改变任何判定/交付语义）
        if not self.cancelled and not self.epoch_violated:
            from openbrep.feedback import append_feedback
            blocking_issues = [i for i in semantic_result.issues if i.blocking]
            if gate_unresolved and compile_result is not None and not compile_result.success:
                append_feedback(self.project.root, {
                    "kind": "compile_failure",
                    "summary": (compile_result.stderr or compile_result.stdout or "compile failed"),
                    "detail": {
                        "stage": "completion_gate",
                        "error": (compile_result.stderr or "")[:600],
                    },
                })
            if blocking_issues:
                append_feedback(self.project.root, {
                    "kind": "semantic_blocking",
                    "summary": "；".join(i.detail for i in blocking_issues),
                    "detail": {
                        "stage": "completion_gate",
                        "checks": [i.check_type for i in blocking_issues],
                    },
                })

        output_parts: list[str] = []
        if final_text:
            output_parts.append(final_text)
        for warning in file_block_warnings or []:
            if warning and warning not in output_parts:
                output_parts.append(warning)
        if file_block_warning and file_block_warning not in output_parts:
            output_parts.append(file_block_warning)
        status_lines = [
            "**Agent loop（Codex 动态工具桥接）**：工具调用 "
            f"{self.tool_calls_used}/{self.budget} 次，turn {self.turns} 次",
        ]
        if self.gate_rejections:
            status_lines.append(
                f"🧩 完成门禁打回 {self.gate_rejections} 次（宣称完成但证据未过）"
            )
        if gate_unresolved:
            status_lines.append("⚠️ 完成门禁未通过（编译/语义证据仍有问题），如实交付当前状态。")
        if self.budget_exhausted:
            status_lines.append("⚠️ 预算耗尽，AI 未能在预算内主动判定完成，以上为其当前进度。")
        if self.cancelled:
            status_lines.append("⚠️ 任务被取消，以上为中断时的进度。")
        if self.epoch_violated:
            status_lines.append("⚠️ 项目已切换，任务已中止。")
        if last_error_text and not self.cancelled:
            status_lines.append(f"⚠️ {last_error_text}")
        tool_digest = _tool_digest(self.registry.tool_log)
        if tool_digest:
            status_lines.append(f"工具记录：{tool_digest}")
        if compile_result is not None and compile_result.success:
            status_lines.append("✅ 编译通过")
        elif compile_result is not None:
            short_err = (compile_result.stderr or "")[:300].strip()
            status_lines.append(f"❌ 编译失败：\n```\n{short_err}\n```")
        output_parts.append("\n".join(status_lines))

        # 验收报告（V5）：参数 diff + 前后几何对比 + 验证结论（确定性，不调 LLM）
        after_params = [(p.name, p.value) for p in self.project.parameters]
        before_map = dict(self.before_params)
        after_map = dict(after_params)
        parameter_changes = [
            {"name": name, "from": before_map.get(name), "to": after_map.get(name)}
            for name in sorted(set(before_map) | set(after_map))
            if before_map.get(name) != after_map.get(name)
        ]
        acceptance = build_modify_acceptance(
            before=self.before_preview,
            after=preview_geometry_summary(self.project),
            parameter_changes=parameter_changes,
            changed_files=list(self.registry.changed_files.keys()),
            compile_result=compile_result,
            semantic_issues=[i.detail for i in semantic_result.issues if i.blocking],
        )

        diff_warnings, diff_ratios = self.registry.diff_scope_warnings()
        if diff_warnings:
            output_parts.append(
                "⚠️ diff 范围护栏（advisory，不阻断）：\n"
                + "\n".join(f"- {w}" for w in diff_warnings)
            )

        static_result = StaticChecker().check(self.project)
        verification_report = build_verification_report(
            intent=self.intent,
            user_input=self.request.user_input,
            project=self.project,
            object_plan=None,
            static_result=static_result,
            semantic_result=semantic_result,
            lint_summary="",
            compile_result=compile_result,
            auto_repair_info="",
            graph_powered=False,
            reserved_conflicts=detect_reserved_param_misuse(self.project),
        )
        output_parts.append(verification_report.to_summary_text())

        # 交付门禁：取消 / epoch 违规 / 崩溃 / 超时 / 无 final 一律不算成功
        # （任何非 stop 终局都意味着任务未完成——不得以"项目恰好可编译"假成功）。
        aborted_delivery = (
            self.cancelled
            or self.epoch_violated
            or any(
                o.finish_reason in ("interrupted", "timeout", "error", "no_final_message")
                for o in self.turn_outcomes
            )
        )
        return TaskResult(
            success=verification_report.passed and not aborted_delivery,
            intent=self.intent,
            scripts=dict(self.registry.changed_files),
            plain_text="\n\n".join(part for part in output_parts if part),
            project=self.project,
            compile_result=compile_result,
            verification=verification_report.to_dict(),
            metadata={
                "agent_loop": {
                    "diff_guardrail": {
                        "warnings": diff_warnings,
                        "ratios": diff_ratios,
                        "write_methods": dict(self.registry.write_methods),
                    }
                },
                "acceptance": acceptance,
                "codex_modify": {
                    "enabled": bool(self.pipeline.config.llm.codex_modify_enabled),
                    "model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "turns": self.turns,
                    "tool_calls": self.tool_calls_used,
                    "budget": self.budget,
                    "budget_exhausted": self.budget_exhausted,
                    "gate_rejections": self.gate_rejections,
                    "cancelled": self.cancelled,
                    "epoch_violated": self.epoch_violated,
                    # 审计：每次工具调用/拒绝（含原因）+ 每次线级服务器请求
                    "tool_audit": _merge_audit_wire(self.audit, self.turn_outcomes),
                    "wire_requests": [
                        r for out in self.turn_outcomes for r in out.requests
                    ],
                },
            },
        )


def _merge_audit_wire(audit: list[dict], outcomes: list[CodexModifyTurnOutcome]) -> list[dict]:
    """把执行审计与线级请求记录按 call_id 关联（审计即证据链）。"""
    merged = [dict(e) for e in audit]
    by_call: dict[str, list[dict]] = {}
    for out in outcomes:
        for req in out.requests:
            cid = req.get("call_id") or ""
            by_call.setdefault(cid, []).append(req)
    for entry in merged:
        cid = entry.get("call_id") or ""
        wire = by_call.get(cid) or []
        if wire:
            entry["request_ids"] = [r["request_id"] for r in wire]
    return merged


def _tool_digest(tool_log: list[dict]) -> str:
    """把工具调用日志压成一行摘要，如 update_script×2, compile_script×3。"""
    counts: dict[str, int] = {}
    for entry in tool_log:
        counts[entry["name"]] = counts.get(entry["name"], 0) + 1
    return ", ".join(f"{name}×{count}" for name, count in counts.items())


def _has_file_blocks(text: str) -> bool:
    return "[FILE:" in (text or "")


def _modify_ready_error(provider: Any, model: str, reasoning_effort: str) -> str | None:
    """Codex MODIFY 前置门禁：与 provider.chat 相同的 fail-closed 检查。

    返回稳定错误文案；None = 可继续。任何失败都不发起 thread/turn 请求。
    """
    from openbrep.codex.provider import CodexNotSignedInError, CodexUnsupportedEffortError

    try:
        if getattr(provider, "cli_available", False) is not True:
            return "未检测到 Codex CLI。请先安装 Codex CLI 后重试。"
        status = provider.status(refresh=True)
        if not status.get("connected"):
            return "尚未连接 ChatGPT。请先在 AI 设置中点击「连接我的 ChatGPT」完成登录。"
        if status.get("state") == "quota_exhausted":
            return QUOTA_ERROR_TEXT
        provider.validate_reasoning_effort(model, reasoning_effort)
    except CodexNotSignedInError:
        return "尚未连接 ChatGPT。请先在 AI 设置中点击「连接我的 ChatGPT」完成登录。"
    except CodexUnsupportedEffortError:
        return (
            "当前模型不支持所选 reasoning effort，请求已拒绝。"
            "请到 AI 设置中选择该模型支持的 effort。"
        )
    except Exception as exc:  # noqa: BLE001 —— 兜底稳定文案
        category = getattr(exc, "category", None)
        _LOGGER.warning(
            "codex modify ready 检查失败（category=%s）",
            category or exc.__class__.__name__,
        )
        return TURN_ERROR_TEXT
    return None


def run_codex_modify_agent_loop(pipeline: "TaskPipeline", request: "TaskRequest") -> "TaskResult":
    """Codex 模型 MODIFY/DEBUG/REPAIR 的动态工具桥接入口（D10）。

    仅当 ``config.llm.codex_modify_enabled`` 为 True 时由 pipeline 调用；
    flag=false 时 pipeline 在进入本函数前 fail closed。
    """
    from openbrep.runtime.pipeline import TaskResult

    model = str(pipeline.config.llm.model or "")
    provider = getattr(pipeline, "codex_provider", None)
    if provider is None:
        llm = pipeline._make_llm(request)
        provider = llm._codex_provider()
    if provider is None:
        text = (
            "ChatGPT Codex（openai-codex）模型不可用：未检测到可用的 Codex 连接。"
            "请先在 AI 设置中完成 ChatGPT 登录后重试。"
        )
        return TaskResult(
            success=False, intent=request.intent or "MODIFY",
            plain_text=text, error="codex provider unavailable",
        )
    reasoning_effort = str(pipeline.config.llm.codex_reasoning_effort() or "").strip()
    ready_error = _modify_ready_error(provider, model, reasoning_effort)
    if ready_error is not None:
        return TaskResult(
            success=False, intent=request.intent or "MODIFY",
            plain_text=ready_error, error=ready_error,
        )
    bridge = CodexModifyBridge(
        pipeline=pipeline,
        request=request,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return bridge.run()
