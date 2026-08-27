"""Codex app-server turn 层（D3）：ephemeral thread + 临时只读 cwd +
approval never 的安全 CHAT/EXPLAIN。

协议面（实测 Codex CLI 0.147.0 `codex app-server generate-ts` 绑定）：
- thread/start（ephemeral=true、sandbox="read-only"、approvalPolicy="never"）→ {thread, ...}
- turn/start（input=[{type:"text", text, text_elements:[]}]、cwd、approvalPolicy、
  sandboxPolicy={type:"readOnly", networkAccess:false}）→ {turn}
- 通知（无 id 帧，v2 信封）：turn/started、item/started、item/agentMessage/delta、
  item/completed、turn/completed、error
- turn/interrupt（threadId, turnId）→ {}
- thread/delete（threadId）→ {}

安全不变量（D3 派单）：
- 每个 turn 使用全新 ephemeral thread，结束即删（thread/delete），无持久化、无会话复用。
- 只读 sandbox + approval never + 参数面不含任何工具（shell/patch/MCP/fs 均不存在）。
- 输入只来自 OpenBrep 构建的 system/user 文本；不加载 home AGENTS/skills/plugins
  （CODEX_HOME 独立 + 临时 cwd 无 AGENTS.md + 不传 custom/developer instructions）。
- 只收集 final agent message（phase=final_answer 或 phase 未知的 legacy 消息；
  phase=commentary 的中间消息一律不收集）。
- 无 final / 截断 / interrupt / 超时 / quota / crash 各有明确 finish_reason 与
  稳定文案；上游错误原文（含 canary/秘密）绝不进入返回、日志或缓存。
- 并发：每个 turn 的收集器只认自己的 (threadId, turnId)；迟到/乱序/他 turn 的
  通知一律忽略，绝不污染后续请求。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from openbrep.codex.app_server import CodexAppServerError

_LOGGER = logging.getLogger(__name__)

# turn 级稳定文案（D3）：只收集 final agent message 的边界语义。任何上游原文
# （错误消息 / canary / token）都不得进入这些文案。
NO_FINAL_MESSAGE_TEXT = "Codex 模型未返回最终回复，请重试。"
TURN_ERROR_TEXT = "Codex 对话失败，请稍后重试。"
QUOTA_ERROR_TEXT = (
    "ChatGPT 订阅额度已耗尽或已达到用量上限。请稍后重试、等待重置，或切换到其他模型/提供商。"
)
INTERRUPTED_TEXT = "对话已取消。"
TIMEOUT_TEXT = "Codex 对话超时，请稍后重试。"
EMPTY_INPUT_TEXT = "对话内容为空，无法发送。"
IMAGE_PATH_ESCAPE_TEXT = "图片路径校验失败，请求已拒绝。请重新发起。"
THREAD_FAILED_TEXT = "Codex 对话线程创建失败，请稍后重试。"
TURN_START_FAILED_TEXT = "Codex 对话启动失败，请稍后重试。"

# 默认 turn 截止时间（秒）：turn 是长耗时操作（推理 + 生成），独立于 JSON-RPC
# 单帧 rpc_timeout；LLMAdapter 会按 config.timeout 显式传入。
_DEFAULT_TURN_TIMEOUT = 90.0
# 取消/超时检查轮询间隔（秒）：兼顾响应度与忙等开销。
_POLL_INTERVAL = 0.25
# turn/interrupt 与 thread/delete 的等待窗口（秒）：best-effort 清理，不阻塞过久。
_CLEANUP_WAIT = 3.0


def wire_model_name(model: str) -> str:
    """Return the model identifier accepted by the Codex app-server wire API.

    ``openai-codex/`` is an OpenBrep configuration/provider namespace, not
    part of the model id accepted by the ChatGPT-account app-server.
    """
    return model.removeprefix("openai-codex/")

# 额度类错误信号（CodexErrorInfo / 错误文本关键字；命中后映射稳定 quota 文案，
# 绝不把上游错误原文拼进返回）。
_QUOTA_SIGNALS = (
    "usagelimitexceeded",
    "sessionbudgetexceeded",
    "ratelimitreached",
    "rate limit",
    "quota",
    "usage limit",
    "insufficient",
)


def _looks_like_quota(payload: Any) -> bool:
    """错误通知/消息是否命中额度信号（大小写不敏感）。"""
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
    return any(signal in low for signal in _QUOTA_SIGNALS)


def _stable_error_text(payload: Any) -> str:
    """错误通知 → 稳定文案：命中额度信号给 quota 文案，否则通用稳定文案。

    绝不回显 payload 里的上游原文（可能含 canary/秘密）。
    """
    if _looks_like_quota(payload):
        return QUOTA_ERROR_TEXT
    return TURN_ERROR_TEXT


def build_turn_prompt(messages: list[dict]) -> tuple[str, str]:
    """把 OpenBrep 的 messages 拆成 (system_text, user_text)。

    system 消息合并进 thread baseInstructions；其余消息（history + 当前输入）
    折叠进 turn/start 的单个 text 输入（协议只接受一个用户输入）。
    非字符串 content 一律 str() 归一；空消息跳过。
    """
    system_parts: list[str] = []
    dialogue: list[tuple[str, str]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = msg.get("content")
        if content is None:
            continue
        if not isinstance(content, str):
            content = str(content)
        if role == "system":
            system_parts.append(content)
        else:
            dialogue.append((role, content))
    system_text = "\n\n".join(part for part in system_parts if part.strip())
    if not dialogue:
        return system_text, ""
    *history, current = dialogue
    parts: list[str] = []
    if history:
        lines = [f"{role}: {text}" for role, text in history]
        parts.append("以下是此前对话记录：\n" + "\n".join(lines))
    parts.append(str(current[1]))
    return system_text, "\n\n".join(part for part in parts if part)


@dataclass
class CodexTurnResult:
    """一次 Codex turn 的最终结果。

    finish_reason 语义（D3 契约）：
    - stop             已收到 final agent message（phase=final_answer 或未知 phase）
    - no_final_message turn 正常结束但没有任何 final agent message（含截断/空文本）
    - interrupted      用户取消（turn/interrupt 已尽力发送）
    - timeout          turn 超过截止时间（已尽力 interrupt + 清理）
    - error            turn 级 error 通知 / 额度 / 上游失败（error 为稳定文案）
    """

    content: str = ""
    model: str = ""
    finish_reason: str = "no_final_message"
    thread_id: str | None = None
    turn_id: str | None = None
    error: str | None = None
    usage: dict = field(default_factory=dict)
    # D6：本次 turn 实际请求的 reasoning effort（"" = 未覆盖，用模型默认）。
    # 与 model 一起构成 Fixed 模式的 effective 组合，供结果元数据追踪。
    reasoning_effort: str = ""


class _TurnCollector:
    """一次 turn 的通知收集器。

    transport reader 线程调用 handle_notification（必须快、绝不抛异常），
    把匹配本 turn (threadId, turnId) 的事件放进队列；驱动线程消费。
    不匹配（迟到 / 乱序 / 他 turn / 畸形）一律忽略。

    D6（UI 脱敏）：按 itemId 记录 item/started 携带的 phase（commentary /
    final_answer / 未知）。commentary（模型中间思考）的 delta 绝不进入
    delta 缓冲与 UI 流式回调——UI 任何状态都不出现 raw chain-of-thought。
    phase 未知（legacy 上游不发送 item/started）按 final 兼容处理。
    """

    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._enabled = True
        # itemId → phase（"commentary" / "final_answer" / None=未知）
        self._item_phases: dict[str, str | None] = {}

    def close(self) -> None:
        self._enabled = False

    def record_item_phase(self, item_id: Any, phase: Any) -> None:
        """记录 item 的 phase（来自 item/started 或 item/completed 的 item 对象）。"""
        if not isinstance(item_id, str) or not item_id:
            return
        if isinstance(phase, str) and phase in ("commentary", "final_answer"):
            self._item_phases[item_id] = phase
        elif item_id not in self._item_phases:
            # 未知 phase（legacy）：按 final 兼容，不记录（None 即视为 final 候选）
            self._item_phases[item_id] = None

    def is_commentary(self, item_id: Any) -> bool:
        """该 item 是否已被标记为 commentary（中间思考，UI 必须隐藏）。"""
        if not isinstance(item_id, str) or not item_id:
            return False
        return self._item_phases.get(item_id) == "commentary"

    def handle_notification(self, msg: dict) -> None:
        if not self._enabled:
            return
        method = msg.get("method")
        params = msg.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if self.thread_id is None or thread_id != self.thread_id:
            return
        turn_id = params.get("turnId")
        if self.turn_id is not None and turn_id is not None and turn_id != self.turn_id:
            return
        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, dict):
                tid = turn.get("id")
                if isinstance(tid, str) and tid:
                    self.turn_id = tid
            self.q.put(("turn_started", params))
        elif method == "item/started":
            # D6：item/started 携带 phase——先记录，再让驱动线程消费（保持
            # reader 线程只做入队；phase 查表在消费侧仍可用，因为驱动线程
            # 顺序消费 item/started 先于该 item 的 delta/completed）。
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
                # 兜底：某些上游不发送 item/started，只在 completed 携带 phase
                self.record_item_phase(item.get("id"), item.get("phase"))
                self.q.put(("item_completed", item))
        elif method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict):
                self.q.put(("turn_completed", turn))
        elif method == "error":
            self.q.put(("error", params))


class CodexTurnRunner:
    """在单个 app-server client 上执行一次安全 CHAT/EXPLAIN turn（可复用实例）。"""

    def __init__(self, client: Any, *, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._logger = logger or _LOGGER

    # ── 协议参数（只读沙箱 + approval never + 无工具面）──────────────────────

    @staticmethod
    def build_thread_start_params(*, model: str, cwd: str, system_text: str) -> dict:
        """thread/start 参数：ephemeral 线程 + 只读 sandbox + approval never。

        不传 developerInstructions / customInstructions（不加载 home
        AGENTS/skills/plugins）；threadSource 仅作分析分类。
        """
        return {
            "model": wire_model_name(model),
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "serviceName": "openbrep",
            "threadSource": "openbrep-chat",
            "baseInstructions": system_text or None,
        }

    @staticmethod
    def build_turn_start_params(
        *,
        thread_id: str,
        model: str,
        cwd: str,
        user_text: str,
        images: list | None = None,
        reasoning_effort: str = "",
    ) -> dict:
        """turn/start 参数：文本输入 + 图片输入（localImage）+ 只读 sandbox + approval never。

        D5 图片输入安全不变量：
        - 图片条目只使用 ``{"type": "localImage", "path": ...}`` 协议形状
          （实测 0.147.0 ``codex app-server`` 只接受 camelCase v2 变体：
          ``local_image``（v1 snake_case）会被拒绝——见 D5 实施报告协议探针）。
        - path 只来自 provider 物化到临时 cwd 的授权图片（不透明文件名），
          绝不转发用户提供的路径；发送前做 cwd 包含性断言（纵深防御）。
        - 参数面不含 tools / shell / patch / MCP / fs 等任何工具键。
        """
        import os
        from pathlib import Path

        cwd_path = Path(cwd).resolve()
        input_items: list[dict] = [
            {"type": "text", "text": user_text, "text_elements": []},
        ]
        for img in images or []:
            path = str(img.get("path") or "").strip()
            if not path:
                continue
            img_path = Path(path).resolve()
            try:
                within = os.path.commonpath([str(cwd_path), str(img_path)]) == str(cwd_path)
            except ValueError:
                within = False
            if not within:
                # 纵深防御：物化路径按构造必在 cwd 内；越界一律拒绝发送
                raise CodexAppServerError(
                    IMAGE_PATH_ESCAPE_TEXT, category="image_path_escape"
                )
            input_items.append({"type": "localImage", "path": str(img_path)})
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": input_items,
            "model": wire_model_name(model),
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        # D6：Fixed 模式 reasoning effort（turn/start 的 effort 覆盖键，
        # 协议对齐 codex 0.147.0 generate-ts TurnStartParams.effort）。
        # 空字符串 = 不覆盖（模型默认）——不发送该键，请求形状与基线一致。
        effort = str(reasoning_effort or "").strip()
        if effort:
            params["effort"] = effort
        return params

    # ── 内部：中断 / 清理（best-effort，绝不抛错打断主流程）──────────────────

    def _interrupt(self, thread_id: str | None, turn_id: str | None) -> None:
        if not thread_id or not turn_id:
            return
        try:
            self._client.turn_interrupt({"threadId": thread_id, "turnId": turn_id})
        except Exception as exc:  # noqa: BLE001 —— 只记稳定事件名
            self._logger.warning("codex turn interrupt 失败（%s）", exc.__class__.__name__)

    def _cleanup_thread(self, thread_id: str | None) -> None:
        if not thread_id:
            return
        try:
            self._client.thread_delete({"threadId": thread_id})
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("codex thread 清理失败（%s）", exc.__class__.__name__)

    # ── 主流程 ─────────────────────────────────────────────────────────────

    def _drive(
        self,
        *,
        model: str,
        cwd: str,
        messages: list[dict],
        timeout: float | None,
        should_cancel: Callable[[], bool] | None,
        on_delta: Callable[[str], None] | None,
        images: list | None = None,
        reasoning_effort: str = "",
    ) -> CodexTurnResult:
        system_text, user_text = build_turn_prompt(messages)
        if not user_text.strip():
            raise CodexAppServerError(EMPTY_INPUT_TEXT, category="rpc_error")

        transport = getattr(self._client, "transport", None)
        collector = _TurnCollector()
        subscribed = False
        if transport is not None and hasattr(transport, "subscribe"):
            transport.subscribe(collector.handle_notification)
            subscribed = True
        try:
            # 1. ephemeral thread
            thread_resp = self._client.thread_start(
                self.build_thread_start_params(model=model, cwd=cwd, system_text=system_text)
            )
            thread = thread_resp.get("thread") or {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise CodexAppServerError(THREAD_FAILED_TEXT, category="rpc_error")
            collector.thread_id = thread_id

            # 2. turn/start（事件以通知流式返回）
            turn_resp = self._client.turn_start(
                self.build_turn_start_params(
                    thread_id=thread_id,
                    model=model,
                    cwd=cwd,
                    user_text=user_text,
                    images=images,
                    reasoning_effort=reasoning_effort,
                )
            )
            turn = turn_resp.get("turn") or {}
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                collector.turn_id = turn_id
            result = CodexTurnResult(
                model=model,
                thread_id=thread_id,
                turn_id=turn_id,
                reasoning_effort=str(reasoning_effort or "").strip(),
            )

            # 3. 收集通知直到 turn/completed / error / 超时 / 取消
            deadline = time.monotonic() + (
                float(timeout) if timeout and timeout > 0 else _DEFAULT_TURN_TIMEOUT
            )
            candidates: list[tuple[str, str]] = []  # (itemId, text)——final 候选
            delta_buf: dict[str, list[str]] = {}
            terminal: CodexTurnResult | None = None
            while terminal is None:
                if should_cancel is not None:
                    try:
                        cancelled = bool(should_cancel())
                    except Exception:  # noqa: BLE001
                        cancelled = False
                    if cancelled:
                        self._interrupt(thread_id, collector.turn_id)
                        terminal = CodexTurnResult(
                            model=model,
                            finish_reason="interrupted",
                            thread_id=thread_id,
                            turn_id=collector.turn_id,
                            error=INTERRUPTED_TEXT,
                        )
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._interrupt(thread_id, collector.turn_id)
                    terminal = CodexTurnResult(
                        model=model,
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
                if kind == "delta":
                    item_id, delta = payload
                    # D6（UI 脱敏）：commentary（中间思考）的 delta 一律丢弃——
                    # 既不进 final 候选缓冲，也不进 UI 流式回调；final_answer /
                    # phase 未知（legacy）的 delta 才允许透出。
                    if not collector.is_commentary(item_id):
                        if isinstance(item_id, str):
                            delta_buf.setdefault(item_id, []).append(delta)
                        if on_delta is not None:
                            try:
                                on_delta(delta)
                            except Exception as exc:  # noqa: BLE001
                                self._logger.warning(
                                    "codex turn delta 回调异常（%s）", exc.__class__.__name__
                                )
                elif kind == "item_started":
                    continue  # phase 已在 reader 线程记录；无需处理
                elif kind == "item_completed":
                    self._record_item(payload, candidates, delta_buf)
                elif kind == "turn_completed":
                    terminal = self._finalize_turn(payload, candidates, result)
                elif kind == "error":
                    terminal = CodexTurnResult(
                        model=model,
                        finish_reason="error",
                        thread_id=thread_id,
                        turn_id=collector.turn_id,
                        error=_stable_error_text(payload),
                    )
            return terminal
        finally:
            if subscribed:
                try:
                    transport.unsubscribe(collector.handle_notification)
                except Exception:  # noqa: BLE001
                    pass
            collector.close()
            self._cleanup_thread(collector.thread_id)

    @staticmethod
    def _record_item(
        item: dict,
        candidates: list[tuple[str, str]],
        delta_buf: dict[str, list[str]],
    ) -> None:
        """只记录 final agent message：commentary 中间消息不收集。"""
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
        result: CodexTurnResult,
    ) -> CodexTurnResult:
        status = turn.get("status")
        if status == "interrupted":
            result.finish_reason = "interrupted"
            result.error = INTERRUPTED_TEXT
            return result
        if status != "completed":
            # failed / 未知状态：稳定文案（不读 turn.error 原文——可能含 canary）
            result.finish_reason = "error"
            result.error = _stable_error_text(turn.get("error"))
            return result
        if not candidates:
            result.finish_reason = "no_final_message"
            result.error = NO_FINAL_MESSAGE_TEXT
            return result
        # 取最后一条 final 候选（_record_item 已过滤 commentary）
        _, text = candidates[-1]
        if not text.strip():
            result.finish_reason = "no_final_message"
            result.error = NO_FINAL_MESSAGE_TEXT
            return result
        result.content = text
        result.finish_reason = "stop"
        result.error = None
        return result

    # ── 对外 API ───────────────────────────────────────────────────────────

    def run(
        self,
        *,
        model: str,
        cwd: str,
        messages: list[dict],
        timeout: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        images: list | None = None,
        reasoning_effort: str = "",
    ) -> CodexTurnResult:
        """非流式：完整执行一次 turn 并返回最终结果。

        on_event 可选：(event_type, data) 回调，事件：status / assistant_delta。
        reasoning_effort（D6）：Fixed 模式的 effort 覆盖值（"" = 模型默认）。
        """
        if on_event is not None:
            try:
                on_event("status", {"stage": "codex", "message": "Codex 对话已开始"})
            except Exception:  # noqa: BLE001
                pass

        def on_delta(text: str) -> None:
            if on_event is not None:
                try:
                    on_event("assistant_delta", {"content": text})
                except Exception:  # noqa: BLE001
                    pass

        result = self._drive(
            model=model,
            cwd=cwd,
            messages=messages,
            timeout=timeout,
            should_cancel=should_cancel,
            on_delta=on_delta,
            images=images,
            reasoning_effort=reasoning_effort,
        )
        if on_event is not None:
            try:
                on_event("status", {"stage": "codex", "message": "Codex 对话完成"})
            except Exception:  # noqa: BLE001
                pass
        return result

    def stream(
        self,
        *,
        model: str,
        cwd: str,
        messages: list[dict],
        timeout: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
        images: list | None = None,
        reasoning_effort: str = "",
    ) -> Iterator[dict]:
        """流式：yield 事件；最后一个事件 type="result"（携带 CodexTurnResult）。

        事件：{"type": "delta", "content": str} | {"type": "result", "result": CodexTurnResult}
        reasoning_effort（D6）：Fixed 模式的 effort 覆盖值（"" = 模型默认）。
        """
        bridge: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        def on_delta(text: str) -> None:
            bridge.put(("delta", text))

        def worker() -> None:
            try:
                result = self._drive(
                    model=model,
                    cwd=cwd,
                    messages=messages,
                    timeout=timeout,
                    should_cancel=should_cancel,
                    on_delta=on_delta,
                    images=images,
                    reasoning_effort=reasoning_effort,
                )
                bridge.put(("result", result))
            except BaseException as exc:  # noqa: BLE001 —— 驱动线程不裸死
                bridge.put(("error", exc))

        thread = threading.Thread(target=worker, daemon=True, name="codex-turn-stream")
        thread.start()
        while True:
            kind, payload = bridge.get()
            if kind == "delta":
                yield {"type": "delta", "content": payload}
            elif kind == "result":
                yield {"type": "result", "result": payload}
                return
            else:
                yield {"type": "error", "error": payload}
                return
