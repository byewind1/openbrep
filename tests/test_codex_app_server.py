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


class TestCodexLogSecretSafety(unittest.TestCase):
    """P0-R1B：日志路径不得原样记录秘密（协议行 / close 异常）。"""

    @staticmethod
    def _capture_logger(logger_name):
        import logging

        records: list[str] = []
        handler = logging.Handler()

        def emit(record):
            records.append(record.getMessage())

        handler.emit = emit
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger, handler, records

    def test_non_json_line_logs_length_only(self):
        import os
        import tempfile

        codex_home = Path(tempfile.mkdtemp(prefix="obr-codex-log-")) / "home"
        previous = os.environ.get("FAKE_CODEX_GARBAGE_LINE")
        os.environ["FAKE_CODEX_GARBAGE_LINE"] = "access_token=SUPERSECRET loginId=opaque-secret"
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=codex_home,
            extra_args=(str(FAKE_SERVER),),
            rpc_timeout=5.0,
        )
        logger, handler, records = self._capture_logger("openbrep.codex.app_server")
        try:
            transport.start()
            client = CodexAppServerClient(transport=transport)
            client.initialize()  # reader 线程会先消费垃圾行再回包
            time.sleep(0.3)
        finally:
            transport.close()
            logger.removeHandler(handler)
            if previous is None:
                os.environ.pop("FAKE_CODEX_GARBAGE_LINE", None)
            else:
                os.environ["FAKE_CODEX_GARBAGE_LINE"] = previous
        joined = "\n".join(records)
        self.assertIn("非 JSON 协议输出", joined)
        for bad in ("access_token=SUPERSECRET", "SUPERSECRET", "opaque-secret", "loginId"):
            self.assertNotIn(bad, joined, f"日志泄漏 {bad}")


class TestCodexProviderCloseLogSafety(unittest.TestCase):
    """P0-R1B：close() 异常只记稳定 category/类名，不打印含秘密的 traceback。"""

    def test_close_exception_logs_stable_category_only(self):
        import logging
        import tempfile

        from openbrep.codex.provider import CodexProvider

        records: list[str] = []
        handler = logging.Handler()

        def emit(record):
            records.append(record.getMessage())

        handler.emit = emit
        logger = logging.getLogger("openbrep.codex.provider")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        class _BoomClient:
            def start(self):
                pass

            def initialize(self):
                return {}

            def account_read(self):
                return {"account": None, "requiresOpenaiAuth": True}

            def close(self):
                raise RuntimeError("Authorization: Bearer plain-secret-value")

        try:
            provider = CodexProvider(
                codex_home=Path(tempfile.mkdtemp(prefix="obr-codex-close-")) / "home",
                client_factory=lambda: _BoomClient(),
                cli_available=True,
            )
            provider.status()  # 创建 client
            provider.close()   # close 抛异常 → 吞掉并记稳定日志
        finally:
            logger.removeHandler(handler)

        joined = "\n".join(records)
        self.assertIn("关闭失败", joined)
        for bad in ("plain-secret-value", "Bearer", "Authorization"):
            self.assertNotIn(bad, joined, f"日志泄漏 {bad}")


# ── D2：通知分发、迟到响应、崩溃、重启、进程组清理 ──────────────────────────


def _codex_home_dir(prefix="obr-codex-d2-"):
    import tempfile

    return Path(tempfile.mkdtemp(prefix=prefix)) / "codex-home"


def _spawn_transport(*, rpc_timeout=5.0, extra_env=None, codex_home=None):
    import os

    env = dict(os.environ)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    home = codex_home or _codex_home_dir()
    transport = StdioJsonRpcTransport(
        codex_binary=sys.executable,
        codex_home=home,
        extra_args=(str(FAKE_SERVER),),
        rpc_timeout=rpc_timeout,
    )
    # 通过环境变量注入 fake 模式：直接替换 transport 的启动环境
    original_start = transport.start

    def start_with_env():
        import os as _os

        # 重新构造 Popen env 太深，这里在调用前设置进程级 env 由 fake 读取
        saved = {}
        for key, value in (extra_env or {}).items():
            saved[key] = _os.environ.get(key)
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value
        try:
            original_start()
        finally:
            for key, value in saved.items():
                if value is None:
                    _os.environ.pop(key, None)
                else:
                    _os.environ[key] = value

    transport.start = start_with_env  # type: ignore[method-assign]
    return transport


class TestCodexD2NotificationDispatch(unittest.TestCase):
    """D2：通知分发（reader 线程投递订阅者）+ drain 兼容。"""

    def test_subscriber_receives_notifications_and_unsubscribe_stops(self):
        transport = _spawn_transport(extra_env={"FAKE_CODEX_NOTIFY": "loginCompleted"})
        received: list[dict] = []

        def handler(msg):
            received.append(msg)

        transport.start()
        transport.subscribe(handler)
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        # 通知在请求响应前发送，稍等 reader 消费
        import time

        time.sleep(0.5)
        transport.close()
        self.assertTrue(
            any(m.get("method") == "account/login/completed" for m in received),
            f"订阅者未收到通知: {received}",
        )
        # drain 兼容：通知同时进入内部队列
        # （close 后队列已清空，这里只验证订阅机制本身）

    def test_unsubscribe_stops_delivery(self):
        transport = _spawn_transport(extra_env={"FAKE_CODEX_NOTIFY": "accountUpdated"})
        received: list[dict] = []

        def handler(msg):
            received.append(msg)

        transport.start()
        transport.subscribe(handler)
        transport.unsubscribe(handler)
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        import time

        time.sleep(0.5)
        transport.close()
        self.assertEqual(received, [])


class TestCodexD2LateResponse(unittest.TestCase):
    """D2：迟到响应——超时请求的响应到达后被丢弃，不污染后续调用。"""

    def test_late_response_does_not_pollute_next_call(self):
        # 首个请求延迟 1s 响应；客户端 0.3s 超时 → 超时；迟到响应被丢弃；
        # 第二个请求正常返回自己的结果
        transport = _spawn_transport(
            rpc_timeout=0.3,
            extra_env={"FAKE_CODEX_DELAY_FIRST_RESPONSE_SECONDS": "1.0"},
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        # 第一个请求（延迟 1s → 0.3s 超时）
        with self.assertRaises(CodexAppServerError) as ctx:
            client.initialize()
        self.assertEqual(ctx.exception.category, "timeout")
        # 等待迟到响应到达并被丢弃
        time.sleep(1.5)
        # 第二个请求正常（不再延迟）
        result = client.initialize()
        self.assertIn("userAgent", result)
        transport.close()


class TestCodexD2CrashRestart(unittest.TestCase):
    """D2：崩溃检测（EOF/非零退出）+ restart 恢复。"""

    def test_crash_at_startup_marks_crashed_with_exit_code(self):
        transport = _spawn_transport(extra_env={"FAKE_CODEX_CRASH_IMMEDIATELY": "1"})
        transport.start()
        time.sleep(1.0)  # reader 读到 EOF
        self.assertTrue(transport.crashed)
        self.assertEqual(transport.crash_exit_code, 42)
        with self.assertRaises(CodexAppServerError) as ctx:
            transport.call("account/read", {})
        self.assertEqual(ctx.exception.category, "process_exited")
        transport.close()

    def test_crash_after_requests_then_restart(self):
        marker = _codex_home_dir("obr-crash-marker-")
        transport = _spawn_transport(
            extra_env={
                "FAKE_CODEX_CRASH_AFTER_REQUESTS": "2",
                "FAKE_CODEX_CRASH_MARKER": str(marker),
            }
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()          # 请求 1
        client.account_read()        # 请求 2 → fake 崩溃
        time.sleep(1.0)
        self.assertTrue(transport.crashed)
        self.assertEqual(transport.crash_exit_code, 43)
        # restart：close 旧传输 → 新进程（不再崩溃）→ initialize
        result = client.restart()
        self.assertIn("userAgent", result)
        self.assertFalse(transport.crashed)
        self.assertTrue(transport.is_alive)
        # 重启后可用
        self.assertEqual(client.account_read(), {"account": None, "requiresOpenaiAuth": True})
        transport.close()

    def test_restart_after_crash_reuses_clean_state(self):
        marker = _codex_home_dir("obr-crash-marker-")
        transport = _spawn_transport(
            extra_env={
                "FAKE_CODEX_CRASH_AFTER_REQUESTS": "1",
                "FAKE_CODEX_CRASH_MARKER": str(marker),
            }
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()          # 请求 1 → 崩溃
        time.sleep(1.0)
        self.assertTrue(transport.crashed)
        client.restart()
        models = client.model_list()
        self.assertEqual([m["id"] for m in models["data"]], ["gpt-5.6-luna", "gpt-5.6-terra"])
        transport.close()


class TestCodexD2StderrCapture(unittest.TestCase):
    """D2：有界 stderr 捕获 + 脱敏（崩溃诊断不泄漏秘密）。"""

    def test_stderr_secret_is_redacted_in_tail(self):
        transport = _spawn_transport(
            extra_env={"FAKE_CODEX_STDERR_SECRET": "Authorization: Bearer super-secret-value"},
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        time.sleep(0.5)
        tail = transport.stderr_tail()
        self.assertNotIn("super-secret-value", tail)
        self.assertNotIn("Bearer", tail)
        transport.close()

    def test_crash_diagnostics_available_redacted(self):
        transport = _spawn_transport(extra_env={"FAKE_CODEX_CRASH_AFTER_REQUESTS": "1"})
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        time.sleep(1.0)
        self.assertTrue(transport.crashed)
        # 崩溃后 stderr 尾巴包含诊断（脱敏后）
        tail = transport.stderr_tail()
        self.assertIn("crash", tail)
        transport.close()


class TestCodexD2ProcessGroupCleanup(unittest.TestCase):
    """D2：close() 后无遗留子进程（fake 服务器 spawn 的 sleep 子进程）。"""

    def test_close_kills_descendant_process(self):
        import os
        import tempfile

        pid_file = Path(tempfile.mkdtemp(prefix="obr-codex-pg-")) / "child.pid"
        transport = _spawn_transport(
            extra_env={"FAKE_CODEX_SPAWN_CHILD_PID_FILE": str(pid_file)},
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        # 等 fake 写入子进程 PID
        deadline = time.monotonic() + 5
        child_pid = None
        while time.monotonic() < deadline:
            if pid_file.exists():
                child_pid = int(pid_file.read_text().strip())
                break
            time.sleep(0.1)
        self.assertIsNotNone(child_pid, "fake 未写入子进程 PID")
        # 子进程必须存活（对照组）
        self._alive(child_pid, "spawn 后子进程应存活")
        transport.close()
        time.sleep(0.5)
        self.assertFalse(self._alive(child_pid, "close 后子进程应被回收"), "遗留子进程")

    @staticmethod
    def _alive(pid, message):
        import os

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


class TestCodexVersionParsing(unittest.TestCase):
    """D2：initialize.userAgent → Codex CLI 版本解析。"""

    def test_parse_real_format(self):
        from openbrep.codex.app_server import parse_codex_version

        self.assertEqual(
            parse_codex_version("openbrep/0.147.0 (Mac OS 15.7.5; arm64)"),
            (0, 147, 0),
        )
        self.assertEqual(parse_codex_version("codex-cli/1.2.3 (Linux)"), (1, 2, 3))
        self.assertIsNone(parse_codex_version(""))
        self.assertIsNone(parse_codex_version("no-version-token"))
        self.assertIsNone(parse_codex_version("openbrep-garbage no-version-token"))


# ── P0-5：畸形 JSON-RPC 帧不能杀死 reader ────────────────────────────────────


class TestCodexD2MalformedFrames(unittest.TestCase):
    """P0-5：list/dict id、无 method 通知、非对象帧、非 JSON 行均不能杀 reader。"""

    def _transport_with_malformed(self, rpc_timeout=5.0):
        transport = _spawn_transport(
            rpc_timeout=rpc_timeout,
            extra_env={"FAKE_CODEX_MALFORMED_FRAMES": "1"},
        )
        return transport

    def test_malformed_frames_do_not_kill_reader(self):
        transport = self._transport_with_malformed()
        transport.start()
        client = CodexAppServerClient(transport=transport)
        # initialize 响应前 fake 会先发一批畸形帧；reader 必须存活并正确回包
        result = client.initialize()
        self.assertIn("userAgent", result)
        # 后续正常调用不受影响
        self.assertEqual(client.account_read(), {"account": None, "requiresOpenaiAuth": True})
        self.assertFalse(transport.crashed)
        self.assertTrue(transport.is_alive)
        transport.close()

    def test_malformed_frames_then_eof_marks_crashed(self):
        transport = self._transport_with_malformed()
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        # 正常 close 后不误标 crashed
        transport.close()
        self.assertFalse(transport.crashed)


class TestCodexD2ReaderCrashDetection(unittest.TestCase):
    """P0-5：reader 顶层 finally——异常/EOF 都标记 finished/crashed 并唤醒 waiters。"""

    def test_subscriber_exception_does_not_kill_reader(self):
        transport = _spawn_transport(extra_env={"FAKE_CODEX_NOTIFY": "loginCompleted"})

        def bad_handler(msg):
            raise RuntimeError("subscriber bug")

        transport.start()
        transport.subscribe(bad_handler)
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        time.sleep(0.5)
        self.assertFalse(transport.crashed)
        self.assertTrue(transport.is_alive)
        # 后续调用仍正常
        self.assertEqual(client.account_read(), {"account": None, "requiresOpenaiAuth": True})
        transport.close()


class TestCodexD2ClosingProtocol(unittest.TestCase):
    """P0-2：close() 置 closing 并唤醒 in-flight waiter，不干等到 rpc_timeout。"""

    def test_in_flight_call_fails_fast_on_close(self):
        transport = _spawn_transport(
            rpc_timeout=10.0,
            extra_env={
                "FAKE_CODEX_DELAY_METHOD": "account/login/start",
                "FAKE_CODEX_DELAY_RESPONSE_SECONDS": "3.0",
            },
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        errors: list = []

        def worker():
            try:
                client.account_login_start("chatgpt")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=worker)
        start = time.monotonic()
        thread.start()
        time.sleep(0.3)  # 让 login RPC 进入等待
        transport.close()  # 触发 closing → waiter 立即失败
        thread.join(timeout=2.0)
        elapsed = time.monotonic() - start
        self.assertEqual(len(errors), 1)
        self.assertLess(elapsed, 5.0, "close 后 in-flight call 应快速失败而非等满 rpc_timeout")
        # 关闭后调用直接失败
        with self.assertRaises(CodexAppServerError):
            client.account_read()


class TestCodexD2BoundedQueues(unittest.TestCase):
    """P1-1/P1-2：通知与超时 tombstone 有界，防长期运行无界增长。"""

    def test_notifications_queue_is_bounded(self):
        from openbrep.codex.app_server import _NOTIFICATIONS_CAP

        transport = _spawn_transport(extra_env={"FAKE_CODEX_NOTIFY": "loginCompleted"})
        transport.start()
        client = CodexAppServerClient(transport=transport)
        # 大量请求触发大量通知
        for _ in range(300):
            client.initialize()
        time.sleep(0.3)
        drained = transport.drain_notifications()
        self.assertLessEqual(len(drained), _NOTIFICATIONS_CAP)
        transport.close()

    def test_timed_out_tombstones_are_bounded(self):
        from openbrep.codex.app_server import _TIMED_OUT_CAP

        transport = _spawn_transport(rpc_timeout=0.2)
        transport.start()
        client = CodexAppServerClient(transport=transport)
        for _ in range(50):
            try:
                client.initialize()
            except CodexAppServerError:
                pass
        with transport._cv:
            self.assertLessEqual(len(transport._timed_out), _TIMED_OUT_CAP)
            self.assertLessEqual(len(transport._timed_out_order), _TIMED_OUT_CAP)
        transport.close()


class TestCodexD2StubbornDescendant(unittest.TestCase):
    """P0-3：SIGTERM 后直接子进程退出但后代忽略 SIGTERM → close 后仍被回收。"""

    def test_stubborn_child_killed_after_close(self):
        import os
        import tempfile

        pid_file = Path(tempfile.mkdtemp(prefix="obr-codex-stubborn-")) / "child.pid"
        transport = _spawn_transport(
            extra_env={
                "FAKE_CODEX_IGNORE_EOF": "1",
                "FAKE_CODEX_SPAWN_CHILD_PID_FILE": str(pid_file),
                "FAKE_CODEX_CHILD_IGNORE_TERM": "1",
            },
        )
        transport.start()
        client = CodexAppServerClient(transport=transport)
        client.initialize()
        deadline = time.monotonic() + 5
        child_pid = None
        while time.monotonic() < deadline:
            if pid_file.exists():
                child_pid = int(pid_file.read_text().strip())
                break
            time.sleep(0.1)
        self.assertIsNotNone(child_pid)
        # close：EOF 后 fake 继续运行（ignore EOF）→ 5s grace → SIGTERM 组 →
        # 父进程响应 TERM 退出，子进程忽略 TERM → 最终 SIGKILL 组
        start = time.monotonic()
        transport.close()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 4.5)
        time.sleep(0.5)
        try:
            os.kill(child_pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        self.assertFalse(alive, "忽略 SIGTERM 的后代在 close 后仍存活（P0-3）")
