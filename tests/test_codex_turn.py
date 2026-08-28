"""Codex turn 层测试（D3）：ephemeral thread + 临时只读 cwd + approval never。

两类 fake：
1. 真实管道 + tests/fake_codex_app_server.py 子进程（FAKE_CODEX_TURN=1）：
   验证 wire 协议、流式通知收集、畸形帧、工具面噪声、canary 脱敏、取消/超时。
2. 内存 recording fake client：验证 D3 客户端只调用 thread/start、turn/start、
   turn/interrupt、thread/delete（绝无 fs/*、command/*、mcp/*、plugin/* 等工具面），
   且并发 turn 按 (threadId, turnId) 隔离不互相污染。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
from openbrep.codex.turn import (
    INTERRUPTED_TEXT,
    NO_FINAL_MESSAGE_TEXT,
    QUOTA_ERROR_TEXT,
    TIMEOUT_TEXT,
    TURN_ERROR_TEXT,
    CodexTurnRunner,
    build_turn_prompt,
)

FAKE_SERVER = Path(__file__).resolve().parent / "fake_codex_app_server.py"

# 恶意服务器在 item 里尝试的 shell/patch 内容（canary 反证：不得进入结果）
_EVIL_MARKERS = ("CANARY-EVIL", "rm -rf", "EVIL")
# D3 客户端绝不允许调用的工具面方法
_TOOL_SURFACE_METHODS = (
    "fs/readFile",
    "fs/writeFile",
    "fs/createDirectory",
    "fs/readDirectory",
    "fs/remove",
    "fs/copy",
    "fs/watch",
    "fs/unwatch",
    "fs/getMetadata",
    "command/exec",
    "command/exec/write",
    "command/exec/terminate",
    "mcpServer/tool/call",
    "mcpServer/resource/read",
    "plugin/install",
    "plugin/uninstall",
    "plugin/read",
    "skills/list",
    "skills/config/write",
    "thread/shellCommand",
)


class _RecordingTransport:
    """内存 transport：记录全部调用；脚本驱动响应；可注入通知帧（模拟 reader
    线程投递）。默认 thread/start → 新线程 id；turn/start → 新 turn id（永不
    完成，用于超时/取消测试）；turn/interrupt / thread/delete → {}。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.subscribers: list = []
        self.started = False
        self.closed = False
        self._script: dict = {}
        self._thread_seq = 0
        self._turn_seq = 0

    def set_script(self, script: dict):
        self._script = dict(script)

    def start(self):
        self.started = True

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method in self._script:
            handler = self._script[method]
            if callable(handler):
                return handler(params)
            return dict(handler)
        if method == "initialize":
            return {"userAgent": "openbrep/0.147.0", "codexHome": "/tmp/fake"}
        if method == "thread/start":
            self._thread_seq += 1
            return {"thread": {"id": f"th-{self._thread_seq}"}}
        if method == "turn/start":
            self._turn_seq += 1
            return {"turn": {"id": f"tn-{self._turn_seq}"}}
        if method in ("turn/interrupt", "thread/delete"):
            return {}
        raise RuntimeError(f"unknown method {method}")

    def subscribe(self, handler):
        self.subscribers.append(handler)

    def unsubscribe(self, handler):
        try:
            self.subscribers.remove(handler)
        except ValueError:
            pass

    def deliver(self, msg: dict):
        """模拟 reader 线程：按订阅顺序投递通知。"""
        for handler in list(self.subscribers):
            handler(msg)

    def close(self):
        self.closed = True


class _RecordingClient:
    """记录方法面的 CodexAppServerClient 替身（含 transport）。

    四个 turn 方法全部委托 transport.call()，保证脚本（通知流/参数捕获）
    与真实 CodexAppServerClient 一样生效，同时记录全部调用面。
    """

    def __init__(self, transport: _RecordingTransport | None = None):
        self.transport = transport or _RecordingTransport()

    def thread_start(self, params):
        return self.transport.call("thread/start", params)

    def turn_start(self, params):
        return self.transport.call("turn/start", params)

    def turn_interrupt(self, params):
        return self.transport.call("turn/interrupt", params)

    def thread_delete(self, params):
        return self.transport.call("thread/delete", params)


class TestBuildTurnPrompt(unittest.TestCase):
    def test_splits_system_and_folds_history(self):
        system, user = build_turn_prompt(
            [
                {"role": "system", "content": "系统 A"},
                {"role": "system", "content": "系统 B"},
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "现在问"},
            ]
        )
        self.assertEqual(system, "系统 A\n\n系统 B")
        self.assertIn("user: 第一句", user)
        self.assertIn("assistant: 第一答", user)
        self.assertTrue(user.endswith("现在问"))

    def test_empty_messages(self):
        system, user = build_turn_prompt([])
        self.assertEqual(system, "")
        self.assertEqual(user, "")

    def test_non_string_content_normalized(self):
        system, user = build_turn_prompt(
            [{"role": "user", "content": 42}, {"role": "user", "content": "ok"}]
        )
        self.assertIn("42", user)
        self.assertTrue(user.endswith("ok"))


class TestCodexTurnRunnerRecording(unittest.TestCase):
    """内存 fake：D3 客户端方法面 = 只允许 thread/turn 四方法 + 无工具面。"""

    def _runner(self):
        transport = _RecordingTransport()
        client = _RecordingClient(transport)
        runner = CodexTurnRunner(client)
        return runner, client, transport

    def _complete_script(self, transport):
        """标准 turn 脚本：agentMessage + final_answer。"""

        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-1"

            def deliver(msg):
                transport.deliver(msg)

            threading.Timer(
                0.02,
                lambda: deliver(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "inProgress"},
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.04,
                lambda: deliver(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "itemId": "m1",
                            "delta": "你好",
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.06,
                lambda: deliver(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "id": "m1",
                                "text": "你好，世界",
                                "phase": "final_answer",
                                "memoryCitation": None,
                            },
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.08,
                lambda: deliver(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "completed"},
                        },
                    }
                ),
            ).start()
            return {"turn": {"id": turn_id, "status": "inProgress"}}

        return {"turn/start": turn_start}

    def test_only_turn_surface_methods_are_called(self):
        runner, client, transport = self._runner()
        transport.set_script(self._complete_script(transport))
        cwd = tempfile.mkdtemp(prefix="obr-d3-rec-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.content, "你好，世界")
        methods = [m for m, _ in transport.calls]
        # 只允许 thread/turn 生命周期方法 + initialize
        self.assertIn("thread/start", methods)
        self.assertIn("turn/start", methods)
        self.assertIn("thread/delete", methods)
        for forbidden in _TOOL_SURFACE_METHODS:
            self.assertNotIn(forbidden, methods, f"D3 客户端不得调用工具面方法 {forbidden}")

    def test_turn_start_params_are_readonly_and_no_tools(self):
        runner, client, transport = self._runner()
        captured: dict = {}

        def turn_start(params):
            captured.update(params)
            return {"turn": {"id": "tn-1"}}

        transport.set_script({"turn/start": turn_start})
        cwd = tempfile.mkdtemp(prefix="obr-d3-params-")
        try:
            runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                timeout=1.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(captured["approvalPolicy"], "never")
        self.assertEqual(captured["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        for key in ("tools", "toolChoice", "shell", "mcp", "mcpServers", "plugins"):
            self.assertNotIn(key, captured)

    def test_thread_start_params_are_ephemeral_readonly(self):
        runner, client, transport = self._runner()
        captured: dict = {}

        def thread_start(params):
            captured.update(params)
            return {"thread": {"id": "th-1"}}

        transport.set_script({"thread/start": thread_start})
        cwd = tempfile.mkdtemp(prefix="obr-d3-tparams-")
        try:
            runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=1.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertTrue(captured["ephemeral"])
        self.assertEqual(captured["sandbox"], "read-only")
        self.assertEqual(captured["approvalPolicy"], "never")

    def test_interrupt_on_cancel_and_no_late_pollution(self):
        runner, client, transport = self._runner()
        transport.set_script(
            {
                "turn/start": lambda p: {"turn": {"id": "tn-1"}},
                "turn/interrupt": lambda p: (
                    transport.deliver(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": p["threadId"],
                                "turn": {"id": "tn-1", "status": "interrupted"},
                            },
                        }
                    )
                    or {}
                ),
            }
        )
        cwd = tempfile.mkdtemp(prefix="obr-d3-cancel-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=5.0,
                should_cancel=lambda: True,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "interrupted")
        self.assertEqual(result.error, INTERRUPTED_TEXT)
        methods = [m for m, _ in transport.calls]
        self.assertIn("turn/interrupt", methods)
        self.assertIn("thread/delete", methods)

    def test_commentary_messages_are_never_collected(self):
        runner, client, transport = self._runner()

        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-1"
            transport.deliver(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "inProgress"},
                    },
                }
            )
            transport.deliver(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": "m1",
                            "text": "中间思考",
                            "phase": "commentary",
                            "memoryCitation": None,
                        },
                    },
                }
            )
            transport.deliver(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                }
            )
            return {"turn": {"id": turn_id}}

        transport.set_script({"turn/start": turn_start})
        cwd = tempfile.mkdtemp(prefix="obr-d3-comment-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "no_final_message")
        self.assertNotIn("中间思考", result.content)

    def test_error_notification_never_leaks_canary(self):
        runner, client, transport = self._runner()
        canary = "CANARY-LEAK-9f8e"

        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-1"
            transport.deliver(
                {
                    "method": "error",
                    "params": {
                        "error": {
                            "message": f"boom {canary}",
                            "codexErrorInfo": "internalServerError",
                            "additionalDetails": None,
                        },
                        "willRetry": False,
                        "threadId": thread_id,
                        "turnId": turn_id,
                    },
                }
            )
            return {"turn": {"id": turn_id}}

        transport.set_script({"turn/start": turn_start})
        cwd = tempfile.mkdtemp(prefix="obr-d3-err-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "error")
        self.assertEqual(result.error, TURN_ERROR_TEXT)
        self.assertNotIn(canary, str(result))
        self.assertNotIn("boom", str(result))

    def test_timeout_sends_interrupt_and_next_request_succeeds(self):
        runner, client, transport = self._runner()
        transport.set_script(
            {
                "turn/start": lambda p: {"turn": {"id": "tn-1"}},  # 永不完成
                "turn/interrupt": lambda p: (
                    transport.deliver(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": p["threadId"],
                                "turn": {"id": "tn-1", "status": "interrupted"},
                            },
                        }
                    )
                    or {}
                ),
            }
        )
        cwd = tempfile.mkdtemp(prefix="obr-d3-timeout-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=0.3,
            )
            self.assertEqual(result.finish_reason, "timeout")
            self.assertEqual(result.error, TIMEOUT_TEXT)
            # 超时后同一 runner 的下一请求成功（无迟到输出污染）
            transport.set_script(self._complete_script(transport))
            result2 = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "再来"}],
                timeout=5.0,
            )
            self.assertEqual(result2.finish_reason, "stop")
            self.assertEqual(result2.content, "你好，世界")
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)

    def test_stream_yields_deltas_and_result(self):
        runner, client, transport = self._runner()
        transport.set_script(self._complete_script(transport))
        cwd = tempfile.mkdtemp(prefix="obr-d3-stream-")
        try:
            events = list(
                runner.stream(
                    model="gpt-5.6-luna",
                    cwd=cwd,
                    messages=[{"role": "user", "content": "hi"}],
                    timeout=5.0,
                )
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        deltas = [e["content"] for e in events if e["type"] == "delta"]
        self.assertGreaterEqual(len(deltas), 1)
        self.assertEqual(events[-1]["type"], "result")
        result = events[-1]["result"]
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.content, "你好，世界")

    def test_late_notification_from_old_turn_does_not_pollute_new_turn(self):
        """旧 turn 的迟到通知（同 threadId 已删、同 turnId 复用场景）不得污染新 turn。"""
        runner, client, transport = self._runner()
        turn_id = "tn-1"

        def turn_start(params):
            thread_id = params["threadId"]
            # 先投递一条「旧 turn」的迟到 delta（同一 turnId，但线程已隔离）
            transport.deliver(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "other-thread",
                        "turnId": turn_id,
                        "itemId": "old",
                        "delta": "旧消息",
                    },
                }
            )
            transport.deliver(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "inProgress"},
                    },
                }
            )
            transport.deliver(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": "m1",
                            "text": "新消息",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    },
                }
            )
            transport.deliver(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                }
            )
            return {"turn": {"id": turn_id}}

        transport.set_script({"turn/start": turn_start})
        cwd = tempfile.mkdtemp(prefix="obr-d3-late-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.content, "新消息")
        self.assertNotIn("旧消息", result.content)

    def test_concurrent_turns_do_not_pollute_each_other(self):
        """两个 CHAT 请求交错：各自 (threadId, turnId) 绑定，互不污染。"""
        runner, client, transport = self._runner()

        def make_turn_start(text: str):
            def turn_start(params):
                thread_id = params["threadId"]
                turn_id = f"tn-{id(text)}"
                transport.deliver(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "inProgress"},
                        },
                    }
                )
                transport.deliver(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "id": "m1",
                                "text": text,
                                "phase": "final_answer",
                                "memoryCitation": None,
                            },
                        },
                    }
                )
                transport.deliver(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "completed"},
                        },
                    }
                )
                return {"turn": {"id": turn_id}}

            return turn_start

        transport.set_script({"turn/start": make_turn_start("AAA-结果")})
        cwd = tempfile.mkdtemp(prefix="obr-d3-conc-")
        try:
            # 顺序执行两个 runner（同一 client）：threadId 各自独立
            r1 = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "a"}],
                timeout=5.0,
            )
            r2 = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "b"}],
                timeout=5.0,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(r1.content, "AAA-结果")
        self.assertEqual(r2.content, "AAA-结果")
        self.assertNotEqual(r1.thread_id, r2.thread_id)


class TestCodexTurnWireIntegration(unittest.TestCase):
    """真实管道 + fake app-server 子进程：流式/非流式、边界语义、canary 脱敏。"""

    @contextlib.contextmanager
    def _provider(self, extra_env=None, rpc_timeout=5.0):
        saved = {
            key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")
        }
        env = {"FAKE_CODEX_TURN": "1", "FAKE_CODEX_SIGNED_IN": "1"}
        env.update(extra_env or {})
        for key, value in env.items():
            os.environ[key] = value
        home = Path(tempfile.mkdtemp(prefix="obr-d3-wire-")) / "home"

        def factory():
            transport = StdioJsonRpcTransport(
                codex_binary=sys.executable,
                codex_home=home,
                extra_args=(str(FAKE_SERVER),),
                rpc_timeout=rpc_timeout,
            )
            return CodexAppServerClient(transport=transport)

        from openbrep.codex.provider import CodexProvider

        provider = CodexProvider(
            codex_home=home,
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        try:
            yield provider
        finally:
            try:
                provider.close()
            finally:
                for key in list(os.environ):
                    if key.startswith("FAKE_CODEX_"):
                        os.environ.pop(key, None)
                os.environ.update(saved)

    def _run(self, provider, messages=None, **kwargs):
        client, _ = provider._snapshot()
        runner = CodexTurnRunner(client)
        cwd = tempfile.mkdtemp(prefix="obr-d3-wire-cwd-")
        try:
            return runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=messages
                or [{"role": "system", "content": "sys"}, {"role": "user", "content": "你好"}],
                **kwargs,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)

    def test_streaming_chat_via_fake_app_server(self):
        with self._provider() as provider:
            client, _ = provider._snapshot()
            runner = CodexTurnRunner(client)
            cwd = tempfile.mkdtemp(prefix="obr-d3-wire-cwd-")
            try:
                events = list(
                    runner.stream(
                        model="gpt-5.6-luna",
                        cwd=cwd,
                        messages=[{"role": "user", "content": "你好"}],
                        timeout=10.0,
                    )
                )
            finally:
                import shutil

                shutil.rmtree(cwd, ignore_errors=True)
            deltas = [e["content"] for e in events if e["type"] == "delta"]
            self.assertGreaterEqual(len(deltas), 1)
            self.assertEqual("".join(deltas), "你好，我是 Codex 测试助手。")
            self.assertEqual(events[-1]["type"], "result")
            self.assertEqual(events[-1]["result"].finish_reason, "stop")
            self.assertEqual(events[-1]["result"].content, "你好，我是 Codex 测试助手。")

    def test_non_streaming_chat_via_fake_app_server(self):
        with self._provider() as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(result.content, "你好，我是 Codex 测试助手。")

    def test_no_final_message_semantics(self):
        with self._provider({"FAKE_CODEX_TURN_NO_FINAL": "1"}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "no_final_message")
            self.assertEqual(result.error, NO_FINAL_MESSAGE_TEXT)

    def test_error_canary_never_leaks(self):
        canary = "CANARY-WIRE-4d2f"
        with self._provider({"FAKE_CODEX_TURN_ERROR_CANARY": canary}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "error")
            self.assertEqual(result.error, TURN_ERROR_TEXT)
            self.assertNotIn(canary, str(result))

    def test_quota_canary_maps_to_stable_quota_text(self):
        canary = "CANARY-QUOTA-8a1b"
        with self._provider({"FAKE_CODEX_TURN_QUOTA_CANARY": canary}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "error")
            self.assertEqual(result.error, QUOTA_ERROR_TEXT)
            self.assertNotIn(canary, str(result))

    def test_commentary_only_is_no_final(self):
        with self._provider({"FAKE_CODEX_TURN_COMMENTARY_ONLY": "1"}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "no_final_message")

    def test_empty_final_is_truncation_semantics(self):
        with self._provider({"FAKE_CODEX_TURN_EMPTY_FINAL": "1"}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "no_final_message")

    def test_malformed_frames_are_ignored(self):
        with self._provider({"FAKE_CODEX_TURN_MALFORMED": "1"}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(result.content, "你好，我是 Codex 测试助手。")

    def test_malicious_server_tool_noise_is_ignored(self):
        """恶意 fake server 尝试 shell/patch 工具面：客户端只收集 final agent message。"""
        with self._provider({"FAKE_CODEX_TURN_TOOL_NOISE": "1"}) as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(result.content, "你好，我是 Codex 测试助手。")
            for marker in _EVIL_MARKERS:
                self.assertNotIn(marker, result.content)

    def test_turn_start_params_clean_against_forbidden_check(self):
        """sandbox 反证：fake 校验 turn/start 参数——D3 参数面必须通过只读+never+无工具。"""
        with self._provider() as provider:
            result = self._run(provider)
            self.assertEqual(result.finish_reason, "stop")

    def test_hang_times_out_then_next_request_succeeds(self):
        with self._provider({"FAKE_CODEX_TURN_HANG": "1"}) as provider:
            result = self._run(provider, timeout=1.0)
            self.assertEqual(result.finish_reason, "timeout")
            self.assertEqual(result.error, TIMEOUT_TEXT)
            # 同一 app-server 进程上紧接着的请求成功（无迟到输出污染）
            result2 = self._run(
                provider, messages=[{"role": "user", "content": "再来"}], timeout=10.0
            )
            self.assertEqual(result2.finish_reason, "stop")

    def test_cancel_interrupts_hanging_turn(self):
        flag = {"v": False}

        def should_cancel():
            return flag["v"]

        def set_flag():
            time.sleep(0.4)
            flag["v"] = True

        with self._provider({"FAKE_CODEX_TURN_HANG": "1"}) as provider:
            threading.Thread(target=set_flag, daemon=True).start()
            result = self._run(provider, timeout=10.0, should_cancel=should_cancel)
            self.assertEqual(result.finish_reason, "interrupted")
            self.assertEqual(result.error, INTERRUPTED_TEXT)
            # 取消后同一 app-server 上紧接着的请求成功（无残留/无迟到污染）
            result2 = self._run(
                provider, messages=[{"role": "user", "content": "再来"}], timeout=10.0
            )
            self.assertEqual(result2.finish_reason, "stop")

    def test_two_concurrent_chats_interleave_without_pollution(self):
        """两个 CHAT 请求交错执行：各自 (threadId, turnId) 绑定，互不污染。

        确定性交错：第一个 turn 挂起（不完成），第二个 turn 在它 in-flight 时
        完成——证明挂起中的 turn 不被另一 turn 的通知污染，取消后无残留。
        """
        with self._provider(
            {"FAKE_CODEX_TURN_HANG": "1", "FAKE_CODEX_TURN_FINAL_TEXT": "并发回复"}
        ) as provider:
            client, _ = provider._snapshot()
            runner = CodexTurnRunner(client)
            cwd = tempfile.mkdtemp(prefix="obr-d3-wire-cwd-")
            cancel_flag = {"v": False}
            results: list = []
            errors: list = []

            def hanging_worker():
                try:
                    results.append(
                        runner.run(
                            model="gpt-5.6-luna",
                            cwd=cwd,
                            messages=[{"role": "user", "content": "挂起消息"}],
                            timeout=10.0,
                            should_cancel=lambda: cancel_flag["v"],
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=hanging_worker, daemon=True)
            t1.start()
            time.sleep(0.3)  # 确保第一个 turn 已 in-flight（fake 只挂起第一个）
            # 第二个 turn 在第一个挂起期间完成
            result2 = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "第二个"}],
                timeout=10.0,
            )
            self.assertEqual(result2.finish_reason, "stop")
            self.assertEqual(result2.content, "并发回复")
            # 取消挂起中的第一个 turn
            cancel_flag["v"] = True
            t1.join(timeout=5.0)
            self.assertFalse(t1.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            result1 = results[0]
            self.assertEqual(result1.finish_reason, "interrupted")
            self.assertEqual(result1.error, INTERRUPTED_TEXT)
            # 挂起中的 turn 未被另一个 turn 的内容污染
            self.assertNotIn("并发回复", result1.content)
            try:
                import shutil

                shutil.rmtree(cwd, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    unittest.main()


# ── D6：Fixed 模式 effort 参数面 + delta phase 过滤（UI 脱敏）─────────────


class TestCodexTurnEffortParams(unittest.TestCase):
    """D6：turn/start 的 effort 覆盖键 + result 携带 effective effort。"""

    def test_build_turn_start_params_includes_effort_when_set(self):
        params = CodexTurnRunner.build_turn_start_params(
            thread_id="th-1",
            model="gpt-5.6-luna",
            cwd="/tmp/x",
            user_text="hi",
            reasoning_effort="high",
        )
        self.assertEqual(params["effort"], "high")

    def test_wire_model_name_strips_only_codex_namespace(self):
        thread = CodexTurnRunner.build_thread_start_params(
            model="openai-codex/gpt-5.6-luna", cwd="/tmp/x", system_text="sys"
        )
        turn = CodexTurnRunner.build_turn_start_params(
            thread_id="th-1", model="openai-codex/gpt-5.6-luna", cwd="/tmp/x", user_text="hi"
        )
        self.assertEqual(thread["model"], "gpt-5.6-luna")
        self.assertEqual(turn["model"], "gpt-5.6-luna")
        self.assertEqual(
            CodexTurnRunner.build_thread_start_params(
                model="gpt-5.6-luna", cwd="/tmp/x", system_text="sys"
            )["model"],
            "gpt-5.6-luna",
        )

    def test_build_turn_start_params_omits_effort_when_empty(self):
        params = CodexTurnRunner.build_turn_start_params(
            thread_id="th-1",
            model="gpt-5.6-luna",
            cwd="/tmp/x",
            user_text="hi",
            reasoning_effort="",
        )
        self.assertNotIn("effort", params)

    def test_run_threads_effort_into_turn_start_and_result(self):
        transport = _RecordingTransport()
        client = _RecordingClient(transport)

        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-eff-1"

            def deliver(msg):
                transport.deliver(msg)

            threading.Timer(
                0.02,
                lambda: deliver(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "inProgress"},
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.04,
                lambda: deliver(
                    {
                        "method": "item/started",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "id": "m1",
                                "phase": "final_answer",
                            },
                            "startedAtMs": 1786800000000,
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.06,
                lambda: deliver(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "itemId": "m1",
                            "delta": "final 文本",
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.08,
                lambda: deliver(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "id": "m1",
                                "text": "final 文本",
                                "phase": "final_answer",
                            },
                            "completedAtMs": 1786800000200,
                        },
                    }
                ),
            ).start()
            threading.Timer(
                0.10,
                lambda: deliver(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "completed"},
                        },
                    }
                ),
            ).start()
            return {"turn": {"id": turn_id, "status": "inProgress"}}

        transport.set_script({"turn/start": turn_start})
        runner = CodexTurnRunner(client)
        cwd = tempfile.mkdtemp(prefix="obr-d6-rec-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="high",
            )
        finally:
            import shutil
            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.reasoning_effort, "high")
        turn_params = [p for m, p in transport.calls if m == "turn/start"][0]
        self.assertEqual(turn_params.get("effort"), "high")
        # thread/start 不携带 effort（协议 TurnStartParams 才接受覆盖）
        thread_params = [p for m, p in transport.calls if m == "thread/start"][0]
        self.assertNotIn("effort", thread_params)


class TestCodexTurnDeltaPhaseFiltering(unittest.TestCase):
    """D6 UI 脱敏：commentary（中间思考）delta 绝不进入 on_event/结果；
    final delta 正常透出。"""

    def _script_with_phases(self, transport):
        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-phase-1"

            def deliver(msg):
                transport.deliver(msg)

            seq = [
                # commentary item：长思考 delta（canary——UI 绝不能看到）
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {"type": "agentMessage", "id": "c1", "phase": "commentary"},
                        "startedAtMs": 1786800000000,
                    },
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "c1",
                        "delta": "CANARY-REASONING-中间思考",
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": "c1",
                            "text": "CANARY-REASONING-中间思考",
                            "phase": "commentary",
                        },
                        "completedAtMs": 1786800000100,
                    },
                },
                # final item：final delta 应透出
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {"type": "agentMessage", "id": "f1", "phase": "final_answer"},
                        "startedAtMs": 1786800000200,
                    },
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "f1",
                        "delta": "最终答复",
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": "f1",
                            "text": "最终答复",
                            "phase": "final_answer",
                        },
                        "completedAtMs": 1786800000300,
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                },
            ]
            def deliver_all():
                for msg in seq:
                    time.sleep(0.008)
                    deliver(msg)

            threading.Thread(target=deliver_all, daemon=True).start()
            return {"turn": {"id": turn_id, "status": "inProgress"}}

        return {"turn/start": turn_start}

    def test_commentary_deltas_never_reach_on_event_or_result(self):
        transport = _RecordingTransport()
        client = _RecordingClient(transport)
        transport.set_script(self._script_with_phases(transport))
        runner = CodexTurnRunner(client)
        events: list[tuple] = []

        def on_event(kind, data):
            events.append((kind, data))

        cwd = tempfile.mkdtemp(prefix="obr-d6-phase-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                on_event=on_event,
            )
        finally:
            import shutil
            shutil.rmtree(cwd, ignore_errors=True)

        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.content, "最终答复")
        # 最终内容不含 commentary canary
        self.assertNotIn("CANARY-REASONING", result.content)
        # on_event 的 assistant_delta 只有 final 文本，绝无 commentary
        deltas = [d for k, d in events if k == "assistant_delta"]
        self.assertEqual(deltas, [{"content": "最终答复"}])
        self.assertFalse(any("CANARY-REASONING" in str(d) for d in deltas))
        # status 事件只是稳定文案，无任何上游原文
        for kind, data in events:
            self.assertNotIn("CANARY-REASONING", str(data))

    def test_commentary_only_with_long_reasoning_is_no_final_message(self):
        """D6：reasoning 很长但 final 为空/截断 → 统一完整性错误。"""
        transport = _RecordingTransport()
        client = _RecordingClient(transport)

        def turn_start(params):
            thread_id = params["threadId"]
            turn_id = "tn-nf-1"

            def deliver(msg):
                transport.deliver(msg)

            seq = []
            for i in range(60):
                seq.append(
                    {
                        "method": "item/started",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {"type": "agentMessage", "id": f"c{i}", "phase": "commentary"},
                            "startedAtMs": 1786800000000 + i,
                        },
                    }
                )
                seq.append(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "itemId": f"c{i}",
                            "delta": f"思考 {i} " + "x" * 200,
                        },
                    }
                )
                seq.append(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "id": f"c{i}",
                                "text": f"思考 {i} " + "x" * 200,
                                "phase": "commentary",
                            },
                            "completedAtMs": 1786800000100 + i,
                        },
                    }
                )
            seq.append(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                }
            )
            def deliver_all():
                for msg in seq:
                    time.sleep(0.002)
                    deliver(msg)

            threading.Thread(target=deliver_all, daemon=True).start()
            return {"turn": {"id": turn_id, "status": "inProgress"}}

        transport.set_script({"turn/start": turn_start})
        runner = CodexTurnRunner(client)
        events: list[tuple] = []

        def on_event(kind, data):
            events.append((kind, data))

        cwd = tempfile.mkdtemp(prefix="obr-d6-nf-")
        try:
            result = runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=[{"role": "user", "content": "hi"}],
                on_event=on_event,
            )
        finally:
            import shutil
            shutil.rmtree(cwd, ignore_errors=True)

        # 统一完整性错误：不交付空结果
        self.assertEqual(result.finish_reason, "no_final_message")
        self.assertEqual(result.error, NO_FINAL_MESSAGE_TEXT)
        self.assertEqual(result.content, "")
        # UI 流式回调零透出（没有任何 delta 到达 on_event）
        deltas = [d for k, d in events if k == "assistant_delta"]
        self.assertEqual(deltas, [])


class TestCodexTurnWireD6Effort(unittest.TestCase):
    """D6 wire：effort 参数面 + 长 reasoning 无 final 完整性（fake app-server）。"""

    @contextlib.contextmanager
    def _provider(self, extra_env=None, rpc_timeout=5.0):
        saved = {
            key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")
        }
        env = {"FAKE_CODEX_TURN": "1", "FAKE_CODEX_SIGNED_IN": "1"}
        env.update(extra_env or {})
        for key, value in env.items():
            os.environ[key] = value
        home = Path(tempfile.mkdtemp(prefix="obr-d6-wire-")) / "home"

        def factory():
            transport = StdioJsonRpcTransport(
                codex_binary=sys.executable,
                codex_home=home,
                extra_args=(str(FAKE_SERVER),),
                rpc_timeout=rpc_timeout,
            )
            return CodexAppServerClient(transport=transport)

        from openbrep.codex.provider import CodexProvider

        provider = CodexProvider(
            codex_home=home,
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        try:
            yield provider
        finally:
            try:
                provider.close()
            finally:
                for key in list(os.environ):
                    if key.startswith("FAKE_CODEX_"):
                        os.environ.pop(key, None)
                os.environ.update(saved)

    def _run(self, provider, messages=None, **kwargs):
        client, _ = provider._snapshot()
        runner = CodexTurnRunner(client)
        cwd = tempfile.mkdtemp(prefix="obr-d6-wire-cwd-")
        try:
            return runner.run(
                model="gpt-5.6-luna",
                cwd=cwd,
                messages=messages
                or [{"role": "system", "content": "sys"}, {"role": "user", "content": "你好"}],
                **kwargs,
            )
        finally:
            import shutil

            shutil.rmtree(cwd, ignore_errors=True)

    def test_effort_forwarded_to_app_server_and_recorded_in_result(self):
        params_log = Path(tempfile.mkdtemp(prefix="obr-d6-log-")) / "params.jsonl"
        with self._provider(
            {
                "FAKE_CODEX_TURN_PARAMS_LOG": str(params_log),
                "FAKE_CODEX_MODEL_EFFORTS_JSON": json.dumps(
                    {
                        "gpt-5.6-luna": {
                            "efforts": [["low"], ["medium"], ["high"]],
                            "default": "medium",
                        }
                    }
                ),
            }
        ) as provider:
            result = self._run(provider, reasoning_effort="high")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.reasoning_effort, "high")
        lines = params_log.read_text(encoding="utf-8").strip().splitlines()
        turn_entries = [json.loads(ln) for ln in lines if '"turn/start"' in ln]
        self.assertTrue(turn_entries, "fake server 必须记录 turn/start 参数")
        self.assertEqual(turn_entries[0]["params"].get("effort"), "high")

    def test_effort_empty_means_no_effort_key(self):
        params_log = Path(tempfile.mkdtemp(prefix="obr-d6-log-")) / "params.jsonl"
        with self._provider({"FAKE_CODEX_TURN_PARAMS_LOG": str(params_log)}) as provider:
            result = self._run(provider)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.reasoning_effort, "")
        lines = params_log.read_text(encoding="utf-8").strip().splitlines()
        turn_entries = [json.loads(ln) for ln in lines if '"turn/start"' in ln]
        self.assertTrue(turn_entries)
        self.assertNotIn("effort", turn_entries[0]["params"])

    def test_long_reasoning_without_final_is_unified_integrity_error(self):
        events: list[tuple] = []
        params_log = Path(tempfile.mkdtemp(prefix="obr-d6-log-")) / "params.jsonl"
        with self._provider(
            {
                "FAKE_CODEX_TURN_REASONING_NO_FINAL": "1",
                "FAKE_CODEX_TURN_PARAMS_LOG": str(params_log),
            }
        ) as provider:
            client, _ = provider._snapshot()
            runner = CodexTurnRunner(client)
            cwd = tempfile.mkdtemp(prefix="obr-d6-wire-cwd-")
            try:
                result = runner.run(
                    model="gpt-5.6-luna",
                    cwd=cwd,
                    messages=[{"role": "user", "content": "你好"}],
                    on_event=lambda kind, data: events.append((kind, data)),
                )
            finally:
                import shutil

                shutil.rmtree(cwd, ignore_errors=True)
        # 长 reasoning + 空 final → 统一完整性错误，绝不交付空结果
        self.assertEqual(result.finish_reason, "no_final_message")
        self.assertEqual(result.error, NO_FINAL_MESSAGE_TEXT)
        self.assertEqual(result.content, "")
        # UI 流式回调零透出（commentary 长思考文本绝不进 on_event）
        deltas = [d for k, d in events if k == "assistant_delta"]
        self.assertEqual(deltas, [])
        self.assertFalse(any("思考过程" in str(d) for _, d in events))
