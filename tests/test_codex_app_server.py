"""Codex app-server stdio JSON-RPC 客户端测试（D1）。

两类 fake：
1. 真实管道 + fake app-server 进程（tests/fake_codex_app_server.py）：
   验证换行 JSON-RPC 帧、id 关联、EOF 优雅关闭、CODEX_HOME 环境注入。
2. 内存 fake transport：验证客户端方法面与错误映射。
"""

from __future__ import annotations

import json
import sys
import threading
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


class TestCodexAppServerConcurrency(unittest.TestCase):
    """并发风险（D1 P0）：call() 内部锁串行化 JSON-RPC 帧，并发调用不交错。"""

    def test_parallel_calls_are_serialized_and_pair_correctly(self):
        import tempfile

        codex_home = Path(tempfile.mkdtemp(prefix="obr-codex-conc-")) / "home"
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=codex_home,
            extra_args=(str(FAKE_SERVER),),
            rpc_timeout=10.0,
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()

        results: list = []
        errors: list = []

        def worker(index):
            try:
                if index % 2 == 0:
                    results.append(client.account_read())
                else:
                    models = client.model_list()
                    results.append(models["data"][0]["id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        transport.close()
        self.assertEqual(errors, [])
        # 5 个 account/read（signed_out → account None）+ 5 个 model/list（首模型 id）
        self.assertEqual(results.count({"account": None, "requiresOpenaiAuth": True}), 5)
        self.assertEqual(results.count("gpt-5.6-luna"), 5)


class TestRedactSecrets(unittest.TestCase):
    """P0-2：错误文本脱敏——任何响应不泄露 authUrl/loginId/token/auth 路径。"""

    def _redact(self, text):
        from openbrep.codex.redact import redact_secrets

        return redact_secrets(text)

    def test_redacts_url_with_query(self):
        text = self._redact('login failed: {"authUrl":"https://auth.openai.com/oauth?state=SECRET"}')
        self.assertNotIn("auth.openai.com", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("authUrl", text)

    def test_redacts_jwt_and_api_key(self):
        text = self._redact(
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            " key sk-abc123456789xyz"
        )
        self.assertNotIn("eyJ", text)
        self.assertNotIn("sk-", text)

    def test_redacts_login_id_and_auth_path(self):
        text = self._redact(
            "loginId=a0327bbe-a894-4455-9e96-8c6d19ed2a53 path=/Users/me/.codex/auth.json"
        )
        self.assertNotIn("a0327bbe", text)
        self.assertNotIn(".codex", text)
        self.assertNotIn("loginId", text)

    def test_plain_error_passes_through(self):
        text = self._redact("codex app-server 请求超时：model/list")
        self.assertEqual(text, "codex app-server 请求超时：model/list")

    def test_redacts_sensitive_key_value_assign_forms(self):
        """P0-R1：key=value 形态整体替换（值不保证是 URL/JWT/sk-/UUID）。"""
        cases = [
            "access_token=SUPERSECRET",
            "loginId=opaque-secret-not-uuid",
            "chatgpt_account_id=org-secret-123",
            "api_key=some-arbitrary-value",
        ]
        for case in cases:
            text = self._redact(case)
            self.assertEqual(text, "<redacted>", case)
            for bad in ("SUPERSECRET", "opaque-secret", "org-secret-123", "some-arbitrary"):
                self.assertNotIn(bad, text)

    def test_redacts_authorization_bearer_colon_form(self):
        text = self._redact("Authorization: Bearer plain-secret-value")
        self.assertNotIn("plain-secret-value", text)
        self.assertNotIn("Bearer", text)
        # 无 Authorization 前缀的裸 Bearer 也擦
        text2 = self._redact("header Authorization Bearer second-secret-here")
        self.assertNotIn("second-secret-here", text2)

    def test_redacts_nested_quotes_and_commas(self):
        text = self._redact('{"access_token": "abc,def", "loginId":"xyz", "account_id": 123}')
        self.assertNotIn("abc", text)
        self.assertNotIn("def", text)
        self.assertNotIn("xyz", text)
        self.assertNotIn("access_token", text)

    def test_redacts_single_quoted_json_style(self):
        text = self._redact("{'authUrl': 'https://evil.example/oauth?state=Q', 'loginId': 'sec-1'}")
        self.assertNotIn("evil.example", text)
        self.assertNotIn("sec-1", text)
        self.assertNotIn("authUrl", text)
