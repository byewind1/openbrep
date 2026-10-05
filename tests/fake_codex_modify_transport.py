"""卡03 共享的进程内 Codex modify 驱动替身（虚拟时钟 / 服务器请求注入）。"""

from __future__ import annotations

import threading


class _SteppingClock:
    """虚拟时钟：每次 monotonic() 前进固定步长（秒）。"""

    def __init__(self, step: float) -> None:
        self._step = step
        self._now = 0.0

    def __call__(self) -> float:
        self._now += self._step
        return self._now


class _ServerRequestTransport:
    """进程内 transport：脚本化通知流 + item/tool/call 服务器请求。

    支持 modify 驱动需要的 subscribe / subscribe_server_request / respond 面。
    """

    def __init__(self, notifications: list[dict], tool_calls: list[dict]) -> None:
        self.notifications = notifications
        self.tool_calls = tool_calls
        self.subscribers: list = []
        self.server_subscribers: list = []
        self.responded: list[tuple[int, dict]] = []
        self.calls: list[tuple[str, dict]] = []

    def subscribe(self, handler) -> None:
        self.subscribers.append(handler)

    def unsubscribe(self, handler) -> None:
        if handler in self.subscribers:
            self.subscribers.remove(handler)

    def subscribe_server_request(self, handler) -> None:
        self.server_subscribers.append(handler)

    def unsubscribe_server_request(self, handler) -> None:
        if handler in self.server_subscribers:
            self.server_subscribers.remove(handler)

    def respond(self, req_id: int, result: dict) -> None:
        self.responded.append((req_id, result))

    def _deliver(self, frame: dict) -> None:
        for handler in list(self.subscribers):
            handler(frame)

    def _deliver_tool_call(self, req: dict) -> None:
        for handler in list(self.server_subscribers):
            handler(req["req_id"], "item/tool/call", req["params"])

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "th-1"}}
        if method == "turn/start":
            # 先投递服务器工具请求，再投递 final/完成通知——与真实时序一致
            # （工具调用先于 final 消息；驱动按事件消费顺序处理）。
            for req in self.tool_calls:
                self._deliver_tool_call(req)
            for frame in self.notifications:
                self._deliver(frame)
            return {"turn": {"id": "tn-1"}}
        if method in ("turn/interrupt", "thread/delete"):
            return {}
        raise AssertionError(f"unexpected method: {method}")


class _DelegatingClient:
    """thread/turn 方法委托 transport 的最小客户端替身（对齐 _RecordingClient）。"""

    def __init__(self, transport: _ServerRequestTransport) -> None:
        self.transport = transport

    def thread_start(self, params):
        return self.transport.call("thread/start", params)

    def turn_start(self, params):
        return self.transport.call("turn/start", params)

    def turn_interrupt(self, params):
        return self.transport.call("turn/interrupt", params)

    def thread_delete(self, params):
        return self.transport.call("thread/delete", params)


def driver_tool_call(req_id: int, call_id: str, tool: str = "read_parameters") -> dict:
    return {"req_id": req_id, "params": {
        "callId": call_id, "tool": tool, "arguments": {},
        "threadId": "th-1", "turnId": "tn-1",
    }}


def final_turn_frames() -> list[dict]:
    thread = {"threadId": "th-1", "turnId": "tn-1"}
    return [
        {"method": "item/started", "params": {**thread, "item": {"id": "m1", "phase": "final_answer"}}},
        {"method": "item/agentMessage/delta", "params": {**thread, "itemId": "m1", "delta": "OK"}},
        {"method": "item/completed", "params": {**thread, "item": {
            "id": "m1", "type": "agentMessage", "text": "OK", "phase": "final_answer"}}},
        {"method": "turn/completed", "params": {**thread, "turn": {"id": "tn-1", "status": "completed"}}},
    ]
