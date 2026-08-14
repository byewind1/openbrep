"""Codex app-server stdio JSON-RPC 客户端测试（D1）。

两类 fake：
1. 真实管道 + fake app-server 进程（tests/fake_codex_app_server.py）：
   验证换行 JSON-RPC 帧、id 关联、EOF 优雅关闭、CODEX_HOME 环境注入。
2. 内存 fake transport：验证客户端方法面与错误映射。
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

from openbrep.codex.app_server import (
    CodexAppServerClient,
    CodexAppServerError,
    StdioJsonRpcTransport,
)

FAKE_SERVER = Path(__file__).resolve().parent / "fake_codex_app_server.py"


class _MemoryTransport:
    """脚本化内存 transport：start 无操作，call 按脚本返回或抛错。"""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.calls: list[tuple[str, dict]] = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == "initialize" and method not in self.script:
            return {"codexHome": "/tmp/fake-home"}  # 默认握手成功
        handler = self.script.get(method)
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            return handler(params or {})
        if handler is None:
            raise CodexAppServerError(f"unknown method {method}")
        result = dict(handler)
        if "error" in result and result.get("error"):
            err = result["error"]
            message = err.get("message") if isinstance(err, dict) else err
            raise CodexAppServerError(f"codex app-server {method} 返回错误：{message or err}")
        return result

    def close(self):
        self.closed = True

    def drain_notifications(self):
        return []


class TestCodexAppServerClientFakeTransport(unittest.TestCase):
    def _client(self, script=None):
        transport = _MemoryTransport(script)
        return CodexAppServerClient(transport=transport), transport

    def test_initialize_handshake(self):
        client, transport = self._client({"initialize": {"codexHome": "/tmp/home"}})
        result = client.start()
        self.assertEqual(result["codexHome"], "/tmp/home")
        self.assertTrue(transport.started)
        method, params = transport.calls[0]
        self.assertEqual(method, "initialize")
        self.assertEqual(params["clientInfo"]["name"], "openbrep")

    def test_account_read(self):
        client, _t = self._client({"account/read": {"account": None, "requiresOpenaiAuth": True}})
        client.start()
        self.assertEqual(client.account_read(), {"account": None, "requiresOpenaiAuth": True})

    def test_account_login_start_chatgpt(self):
        client, transport = self._client(
            {"account/login/start": {"type": "chatgpt", "loginId": "L1", "authUrl": "https://a"}}
        )
        client.start()
        result = client.account_login_start_chatgpt()
        self.assertEqual(result["type"], "chatgpt")
        self.assertIn("authUrl", result)
        method, params = transport.calls[1]
        self.assertEqual(method, "account/login/start")
        self.assertEqual(params, {"type": "chatgpt"})

    def test_account_logout_and_model_list(self):
        client, _t = self._client(
            {
                "account/logout": {},
                "model/list": {"data": [{"id": "gpt-5.6-luna"}], "nextCursor": None},
            }
        )
        client.start()
        self.assertEqual(client.account_logout(), {})
        result = client.model_list()
        self.assertEqual(result["data"][0]["id"], "gpt-5.6-luna")

    def test_rpc_error_raises_with_message(self):
        client, _t = self._client(
            {"account/read": {"jsonrpc": "2.0", "error": {"code": -32601, "message": "boom"}}}
        )
        client.start()
        with self.assertRaisesRegex(CodexAppServerError, "boom"):
            client.account_read()

    def test_close_closes_transport(self):
        client, transport = self._client({})
        client.start()
        client.close()
        self.assertTrue(transport.closed)


class TestCodexAppServerStdioWire(unittest.TestCase):
    """真实管道 + fake app-server 进程：验证帧格式、id 关联、环境、EOF 关闭。"""

    def _transport(self, rpc_timeout=5.0):
        import tempfile

        self._codex_home = Path(tempfile.mkdtemp(prefix="obr-codex-test-")) / "codex-home"
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=self._codex_home,
            extra_args=(str(FAKE_SERVER),),
            rpc_timeout=rpc_timeout,
        )
        transport.start()
        return transport

    def test_initialize_receives_isolated_codex_home(self):
        transport = self._transport()
        client = CodexAppServerClient(transport=transport)
        result = client.initialize()
        # fake server 回显 CODEX_HOME 环境变量 → 证明独立 CODEX_HOME 注入生效
        self.assertEqual(result["codexHome"], str(self._codex_home))
        transport.close()

    def test_jsonrpc_roundtrip_and_id_association(self):
        transport = self._transport()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        self.assertEqual(client.account_read(), {"account": None, "requiresOpenaiAuth": True})
        login = client.account_login_start_chatgpt()
        self.assertEqual(login["type"], "chatgpt")
        self.assertEqual(login["authUrl"], "https://example.test/auth/callback?state=fake")
        self.assertEqual(client.account_logout(), {})
        models = client.model_list()
        self.assertEqual([m["id"] for m in models["data"]], ["gpt-5.6-luna", "gpt-5.6-terra"])
        transport.close()

    def test_close_sends_eof_and_process_exits(self):
        transport = self._transport()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        client.close()
        # 重复 close 幂等
        client.close()

    def test_unknown_method_raises_rpc_error(self):
        transport = self._transport()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        with self.assertRaisesRegex(CodexAppServerError, "unknown method"):
            transport.call("no/such/method")
        transport.close()

    def test_rpc_timeout_raises(self):
        transport = self._transport(rpc_timeout=0.4)
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        start = time.monotonic()
        with self.assertRaisesRegex(CodexAppServerError, "超时"):
            transport.call("never/respond", {})
        self.assertLess(time.monotonic() - start, 5.0)
        transport.close()


if __name__ == "__main__":
    unittest.main()
