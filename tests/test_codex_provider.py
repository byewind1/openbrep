"""CodexProvider 状态机测试（D1）：全部使用 fake client，不碰真实账号/网络。

覆盖：no_cli / signed_out / signed_in / error 四态、登录只打开浏览器且
authUrl 不外传、模型目录 provider-qualified、退出后 fail closed、
任何响应不泄露 token/JWT/account id/auth path。
"""

from __future__ import annotations

import contextlib
import unittest
from pathlib import Path

from openbrep.codex.app_server import CodexAppServerError, CodexCliUnavailableError
from openbrep.codex.provider import (
    CodexNotSignedInError,
    CodexProvider,
    CodexVersionIncompatibleError,
    mask_email,
)

# 响应里绝不允许出现的秘密字段（值也不允许出现）
_FORBIDDEN_KEYS = {
    "token", "jwt", "access_token", "auth_url", "authUrl", "loginId", "login_id",
    "auth_path", "codex_home", "authorization", "chatgpt_account_id", "accountId",
}
_FORBIDDEN_VALUES = (
    "sk-", "eyj", "auth.openai.com", "localhost:1455", "fake-login-id",
    "auth.json", ".codex",
)


def _assert_no_secrets(testcase, payload, where="payload"):
    def walk(value, path):
        if isinstance(value, dict):
            for key, val in value.items():
                walk(val, f"{path}.{key}")
                if str(key).lower() in _FORBIDDEN_KEYS:
                    testcase.fail(f"{where} 泄露秘密字段: {path}.{key}")
        elif isinstance(value, list):
            for i, val in enumerate(value):
                walk(val, f"{path}[{i}]")
        elif isinstance(value, str):
            low = value.lower()
            for bad in _FORBIDDEN_VALUES:
                if bad.lower() in low:
                    testcase.fail(f"{where} 泄露秘密值: {path} 含 {bad!r}")

    walk(payload, "$")


class _FakeCodexClient:
    def __init__(self, account=None, models=None):
        self.started = False
        self.account = account  # None | {"type": "chatgpt", "email": ..., "planType": ...}
        self.models = models or [
            {"id": "gpt-5.6-luna", "model": "gpt-5.6-luna", "displayName": "GPT-5.6 Luna", "hidden": False, "modelSpecialty": None},
            {"id": "gpt-5.6-terra", "model": "gpt-5.6-terra", "displayName": "GPT-5.6 Terra", "hidden": False, "modelSpecialty": "balanced"},
        ]
        self.login_calls = 0
        self.logout_calls = 0
        self.account_read_calls = 0
        self.model_list_calls = 0
        self.closed = False

    def start(self):
        self.started = True

    def initialize(self):
        return {"codexHome": "/tmp/fake"}

    def account_read(self):
        self.account_read_calls += 1
        return {"account": self.account, "requiresOpenaiAuth": self.account is None}

    def account_login_start_chatgpt(self):
        self.login_calls += 1
        return {
            "type": "chatgpt",
            "loginId": "fake-login-id",
            "authUrl": "https://auth.openai.com/oauth/authorize?state=fake",
        }

    def account_logout(self):
        self.logout_calls += 1
        self.account = None
        return {}

    def model_list(self):
        self.model_list_calls += 1
        return {"data": self.models, "nextCursor": None}

    def close(self):
        self.closed = True


class TestCodexProvider(unittest.TestCase):
    def _provider(self, client, **kwargs):
        opened: list[str] = []

        def opener(url):
            opened.append(url)

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-provider-test"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=opener,
            **kwargs,
        )
        return provider, opened

    # ── no_cli 状态 ─────────────────────────────────────────

    def test_no_cli_status_does_not_start_client(self):
        client = _FakeCodexClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/x"),
            client_factory=lambda: client,
            cli_available=False,
        )
        status = provider.status()
        self.assertEqual(status["state"], "no_cli")
        self.assertFalse(status["connected"])
        self.assertFalse(status["codex_available"])
        self.assertFalse(client.started)

    def test_no_cli_login_and_models_fail_closed(self):
        provider = CodexProvider(
            codex_home=Path("/tmp/x"),
            cli_available=False,
        )
        with self.assertRaises(CodexCliUnavailableError):
            provider.login_start()
        with self.assertRaises(CodexCliUnavailableError):
            provider.models()

    # ── signed_out / signed_in 状态 ─────────────────────────

    def test_signed_out_status(self):
        client = _FakeCodexClient(account=None)
        provider, _opened = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "signed_out")
        self.assertFalse(status["connected"])
        self.assertIsNone(status["account"])
        self.assertTrue(client.started)

    def test_signed_in_status_masks_email_and_plan(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "johndoe@example.com", "planType": "pro"}
        )
        provider, _opened = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "signed_in")
        self.assertTrue(status["connected"])
        self.assertEqual(status["account"]["email_masked"], "jo***@example.com")
        self.assertEqual(status["account"]["plan_type"], "pro")
        _assert_no_secrets(self, status, "status")

    def test_non_chatgpt_account_is_treated_as_signed_out(self):
        # apiKey 账号不是 ChatGPT 订阅 BYOA 流（隐式 key 认证被禁止）
        client = _FakeCodexClient(account={"type": "apiKey"})
        provider, _opened = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "signed_out")
        self.assertFalse(status["connected"])

    def test_status_is_cached_within_ttl(self):
        client = _FakeCodexClient(account=None)
        provider, _opened = self._provider(client, status_ttl=60.0)
        provider.status()
        provider.status()
        self.assertEqual(client.account_read_calls, 1)

    def test_status_refresh_forces_recheck(self):
        client = _FakeCodexClient(account=None)
        provider, _opened = self._provider(client, status_ttl=60.0)
        provider.status()
        provider.status(refresh=True)
        self.assertEqual(client.account_read_calls, 2)

    # ── 登录 ────────────────────────────────────────────────

    def test_login_start_opens_browser_and_keeps_auth_url_private(self):
        client = _FakeCodexClient(account=None)
        provider, opened = self._provider(client)
        result = provider.login_start()
        self.assertEqual(result, {"state": "login_started", "method": "chatgpt"})
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith("https://auth.openai.com/"))
        self.assertEqual(client.login_calls, 1)
        # authUrl / loginId 不出现在任何返回值里
        _assert_no_secrets(self, result, "login result")

    def test_login_start_raises_when_server_returns_wrong_type(self):
        client = _FakeCodexClient(account=None)
        client.account_login_start_chatgpt = lambda: {"type": "apiKey"}
        provider, _opened = self._provider(client)
        with self.assertRaises(CodexAppServerError):
            provider.login_start()

    def test_login_start_error_never_leaks_raw_payload(self):
        """P0-2：恶意/异常登录响应（含 authUrl/loginId）不进入错误文本。"""
        client = _FakeCodexClient(account=None)
        client.account_login_start_chatgpt = lambda: {
            "type": "unexpected",
            "authUrl": "https://auth.openai.com/oauth?state=SECRET",
            "loginId": "a0327bbe-a894-4455-9e96-8c6d19ed2a53",
        }
        provider, _opened = self._provider(client)
        with self.assertRaises(CodexAppServerError) as ctx:
            provider.login_start()
        text = str(ctx.exception)
        self.assertNotIn("auth.openai.com", text)
        self.assertNotIn("a0327bbe", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("authUrl", text)
        self.assertNotIn("loginId", text)

    # ── 模型目录 ────────────────────────────────────────────

    def test_models_require_signed_in(self):
        client = _FakeCodexClient(account=None)
        provider, _opened = self._provider(client)
        with self.assertRaises(CodexNotSignedInError):
            provider.models()

    def test_models_return_provider_qualified_ids(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
        )
        provider, _opened = self._provider(client)
        models = provider.models()
        self.assertEqual(
            [m["id"] for m in models],
            ["openai-codex/gpt-5.6-luna", "openai-codex/gpt-5.6-terra"],
        )
        self.assertEqual(models[0]["label"], "GPT-5.6 Luna")
        self.assertEqual(models[1]["specialty"], "balanced")
        self.assertTrue(client.model_list_calls >= 1)
        _assert_no_secrets(self, models, "models")

    # ── 模型目录缓存 ────────────────────────────────────────

    def test_models_are_cached_within_ttl(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
        )
        provider, _opened = self._provider(client, models_ttl=60.0)
        provider.models()
        provider.models()
        self.assertEqual(client.model_list_calls, 1)

    def test_models_refresh_forces_relist(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
        )
        provider, _opened = self._provider(client, models_ttl=60.0)
        provider.models()
        provider.models(refresh=True)
        self.assertEqual(client.model_list_calls, 2)

    # ── 退出 ────────────────────────────────────────────────

    def test_logout_then_models_fail_closed(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "free"}
        )
        provider, _opened = self._provider(client)
        self.assertTrue(provider.status()["connected"])
        result = provider.logout()
        self.assertEqual(result, {"state": "signed_out"})
        self.assertEqual(client.logout_calls, 1)
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")
        with self.assertRaises(CodexNotSignedInError):
            provider.models()
        _assert_no_secrets(self, result, "logout result")

    # ── 关闭 ────────────────────────────────────────────────

    def test_close_closes_client_and_is_idempotent(self):
        client = _FakeCodexClient(account=None)
        provider, _opened = self._provider(client)
        provider.status()
        provider.close()
        provider.close()
        self.assertTrue(client.closed)


class TestMaskEmail(unittest.TestCase):
    def test_mask_email(self):
        self.assertEqual(mask_email("johndoe@example.com"), "jo***@example.com")
        self.assertEqual(mask_email("j@example.com"), "j***@example.com")
        self.assertEqual(mask_email("no-at-sign"), "n***")
        self.assertEqual(mask_email(""), "")
        self.assertEqual(mask_email(None), "")


if __name__ == "__main__":
    unittest.main()


class TestCodexStatusErrorStateSecretSafety(unittest.TestCase):
    """P0-R1A：真实 provider 吞掉异常后的 error 状态，不得泄漏上游原文。

    组合链路：CodexProvider(fake client 抛 CodexAppServerError 含秘密)
    → provider.status() 返回值/缓存 → WorkbenchSettingsService.codex_status()。
    """

    def _boom_client(self):
        from openbrep.codex.app_server import CodexAppServerError

        class _BoomClient:
            def start(self):
                pass

            def initialize(self):
                return {}

            def account_read(self):
                raise CodexAppServerError(
                    "rpc failed: access_token=SUPERSECRET loginId=opaque-secret "
                    "Authorization: Bearer plain-secret-value",
                    category="rpc_error",
                )

            def close(self):
                pass

        return _BoomClient()

    def test_provider_status_error_state_and_cache_are_clean(self):
        import tempfile

        provider = CodexProvider(
            codex_home=Path(tempfile.mkdtemp(prefix="obr-codex-r1a-")) / "home",
            client_factory=self._boom_client,
            cli_available=True,
            status_ttl=60.0,
        )
        status = provider.status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["code"], "codex_app_server")
        self.assertEqual(status["error"], "Codex app-server 请求失败，请稍后重试。")
        # 返回值与缓存均无秘密
        cached = provider.status()
        self.assertEqual(cached, status)
        for payload in (status, cached):
            text = str(payload)
            for bad in ("SUPERSECRET", "opaque-secret", "plain-secret-value", "access_token", "loginId", "Bearer"):
                self.assertNotIn(bad, text, f"provider 返回泄漏 {bad}")

    def test_service_codex_status_never_leaks_upstream_error(self):
        import tempfile
        from types import SimpleNamespace

        from openbrep.config import GDLAgentConfig
        from openbrep.workbench.settings_service import WorkbenchSettingsService

        provider = CodexProvider(
            codex_home=Path(tempfile.mkdtemp(prefix="obr-codex-r1a-")) / "home",
            client_factory=self._boom_client,
            cli_available=True,
        )
        config = GDLAgentConfig()
        session = SimpleNamespace(
            llm_model=config.llm.model,
            llm_api_key="",
            llm_api_base="",
            assistant_settings="",
            max_retries=5,
            config=config,
            config_path=Path(tempfile.mkdtemp(prefix="obr-codex-r1a-")) / "config.toml",
        )
        service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None, codex_provider=provider)
        response = service.codex_status()
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["state"], "error")
        self.assertEqual(response["code"], "codex_app_server")
        text = str(response)
        for bad in ("SUPERSECRET", "opaque-secret", "plain-secret-value", "access_token", "loginId", "Bearer"):
            self.assertNotIn(bad, text, f"API 响应泄漏 {bad}")
        # llm_settings 的 codex 块同样干净
        llm = service.llm_settings()
        llm_text = str(llm)
        for bad in ("SUPERSECRET", "opaque-secret", "plain-secret-value", "access_token", "loginId", "Bearer"):
            self.assertNotIn(bad, llm_text, f"llm_settings 泄漏 {bad}")


# ── D2：版本协商、登录白名单、device-code、取消、额度、崩溃/重启 ─────────────


class _D2FakeClient(_FakeCodexClient):
    """D1 fake + D2 扩展：server_version / transport（崩溃检测）/ 率限/取消。"""

    def __init__(self, *, account=None, models=None, version=(0, 147, 0),
                 login_result=None, rate_limits=None, cancel_status="canceled",
                 transport=None):
        super().__init__(account=account, models=models)
        self.server_version = version
        self.login_result = login_result
        self.rate_limits_result = rate_limits
        self.cancel_status = cancel_status
        self.login_cancel_calls = 0
        self.rate_limits_calls = 0
        self._transport = transport

    @property
    def transport(self):
        return self._transport

    def account_login_start(self, login_type):
        self.login_calls += 1
        if self.login_result is not None:
            return self.login_result
        if login_type == "chatgptDeviceCode":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "fake-login-id",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }
        return {
            "type": "chatgpt",
            "loginId": "fake-login-id",
            "authUrl": "https://auth.openai.com/oauth/authorize?state=fake",
        }

    def account_login_cancel(self, login_id):
        self.login_cancel_calls += 1
        return {"status": self.cancel_status}

    def account_rate_limits_read(self):
        self.rate_limits_calls += 1
        if self.rate_limits_result is None:
            raise CodexAppServerError(
                "codex account authentication required to read rate limits",
                category="rpc_error",
            )
        return self.rate_limits_result


class _TransportStub:
    def __init__(self, crashed=False, exit_code=None):
        self.crashed = crashed
        self.crash_exit_code = exit_code


class TestCodexD2Provider(unittest.TestCase):
    def _provider(self, client, **kwargs):
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-d2-provider"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
            **kwargs,
        )
        return provider

    # ── 版本协商 ────────────────────────────────────────────

    def test_version_incompatible_status(self):
        client = _D2FakeClient(version=(0, 50, 0))
        provider = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "version_incompatible")
        self.assertFalse(status["connected"])
        self.assertEqual(status["code"], "version_incompatible")
        self.assertIn("升级", status["error"])
        _assert_no_secrets(self, status, "status")

    def test_version_incompatible_blocks_login(self):
        client = _D2FakeClient(version=(0, 50, 0))
        provider = self._provider(client)
        with self.assertRaises(CodexVersionIncompatibleError):
            provider.login_start()
        with self.assertRaises(CodexVersionIncompatibleError):
            provider.models()

    def test_unparseable_version_fails_closed(self):
        client = _D2FakeClient(version=None)  # initialize 无法解析版本
        provider = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "version_incompatible")

    # ── 登录白名单 ──────────────────────────────────────────

    def test_login_start_rejects_api_key_response_type(self):
        client = _D2FakeClient(login_result={"type": "apiKey", "loginId": "L"})
        provider = self._provider(client)
        with self.assertRaises(CodexAppServerError):
            provider.login_start()
        # 白名单拒绝 apiKey / chatgptAuthTokens
        for bad_type in ("apiKey", "chatgptAuthTokens"):
            client2 = _D2FakeClient(login_result={"type": bad_type, "loginId": "L"})
            provider2 = self._provider(client2)
            with self.assertRaises(CodexAppServerError):
                provider2.login_start()

    def test_login_start_device_code_rejects_other_types(self):
        client = _D2FakeClient(login_result={"type": "chatgpt", "loginId": "L", "authUrl": "https://x"})
        provider = self._provider(client)
        with self.assertRaises(CodexAppServerError):
            provider.login_start_device_code()

    # ── 设备码登录 ──────────────────────────────────────────

    def test_device_code_login_returns_verification_info(self):
        client = _D2FakeClient()
        provider = self._provider(client)
        result = provider.login_start_device_code()
        self.assertEqual(result["state"], "login_started")
        self.assertEqual(result["method"], "chatgptDeviceCode")
        self.assertEqual(result["verification_url"], "https://example.test/device")
        self.assertEqual(result["user_code"], "ABCD-EFGH")
        # loginId 绝不出现在返回值
        self.assertNotIn("loginId", result)
        _assert_no_secrets(self, result, "device-code result")

    # ── 取消登录 ────────────────────────────────────────────

    def test_login_cancel_calls_rpc_and_returns_signed_out(self):
        client = _D2FakeClient()
        provider = self._provider(client)
        provider.login_start()          # 开始浏览器登录（保存 loginId）
        result = provider.login_cancel()
        self.assertEqual(result, {"state": "signed_out"})
        self.assertEqual(client.login_cancel_calls, 1)
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")

    def test_notifications_invalidate_caches(self):
        """D2：通知分发 → provider 缓存失效（额度/账户/登录完成）。"""
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            rate_limits=self._rl_payload(),
        )
        provider = self._provider(client)
        provider.status(refresh=True)   # 填充 status + rate_limits 缓存
        provider.models(refresh=True)   # models() 内部再查一次 status（refresh）
        before = client.rate_limits_calls
        self.assertGreaterEqual(before, 1)
        # account/rateLimits/updated → 额度缓存失效（下次读取重新拉取）
        provider._on_notification({"method": "account/rateLimits/updated", "params": {}})
        provider.rate_limits()
        self.assertGreater(client.rate_limits_calls, before)
        # account/login/completed → status + models 缓存失效
        provider._on_notification({"method": "account/login/completed", "params": {}})
        before_models = client.model_list_calls
        provider.models()  # TTL 内也应重新拉取（缓存已失效）
        self.assertGreater(client.model_list_calls, before_models)

    def test_status_reports_login_started_while_pending(self):
        client = _D2FakeClient()
        provider = self._provider(client)
        provider.login_start()
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "login_started")
        self.assertFalse(status["connected"])
        self.assertIsNone(status["account"])
        _assert_no_secrets(self, status, "login_started status")
        # 取消后回到 signed_out
        provider.login_cancel()
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")

    def test_login_cancel_without_pending_is_idempotent(self):
        client = _D2FakeClient()
        provider = self._provider(client)
        result = provider.login_cancel()
        self.assertEqual(result, {"state": "signed_out"})
        self.assertEqual(client.login_cancel_calls, 0)

    def test_login_cancel_after_device_code(self):
        client = _D2FakeClient()
        provider = self._provider(client)
        provider.login_start_device_code()
        result = provider.login_cancel()
        self.assertEqual(result, {"state": "signed_out"})
        self.assertEqual(client.login_cancel_calls, 1)

    # ── 额度 ────────────────────────────────────────────────

    @staticmethod
    def _rl_payload(reached=None):
        rl = {
            "limitId": "codex",
            "limitName": "Codex",
            "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 1786800000},
            "credits": {"hasCredits": True, "unlimited": False, "balance": "123.45"},
            "spendControlReached": False,
            "planType": "pro",
            "rateLimitReachedType": reached,
        }
        return {"rateLimits": rl, "rateLimitsByLimitId": {"codex": rl}, "rateLimitResetCredits": None}

    def test_rate_limits_masked_summary(self):
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            rate_limits=self._rl_payload(),
        )
        provider = self._provider(client)
        limits = provider.rate_limits()
        self.assertEqual(limits["reached"], False)
        self.assertEqual(limits["used_percent"], 12)
        self.assertEqual(limits["plan_type"], "pro")
        self.assertEqual(limits["credits"], {"has_credits": True, "unlimited": False})
        # 绝不暴露余额字符串 / limit / used / reset credit id
        text = str(limits)
        for bad in ("123.45", "balance", "limitName", "limitId", "resetCredit", "grantedAt"):
            self.assertNotIn(bad, text, f"额度泄漏 {bad}")
        _assert_no_secrets(self, limits, "rate limits")

    def test_rate_limits_fail_closed_when_signed_out(self):
        client = _D2FakeClient(account=None, rate_limits=self._rl_payload())
        provider = self._provider(client)
        with self.assertRaises(CodexNotSignedInError):
            provider.rate_limits()

    def test_status_quota_exhausted_when_rate_limit_reached(self):
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            rate_limits=self._rl_payload(reached="rate_limit_reached"),
        )
        provider = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "quota_exhausted")
        self.assertTrue(status["connected"])
        self.assertEqual(status["rate_limits"]["reached"], True)
        self.assertIn("额度", status["error"])
        _assert_no_secrets(self, status, "quota status")

    def test_status_quota_exhausted_on_spend_control(self):
        rl = self._rl_payload()
        rl["rateLimits"]["spendControlReached"] = True
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            rate_limits=rl,
        )
        provider = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "quota_exhausted")

    # ── 崩溃 / 重启 ─────────────────────────────────────────

    def test_status_reports_crashed(self):
        client = _D2FakeClient(transport=_TransportStub(crashed=True, exit_code=42))
        provider = self._provider(client)
        status = provider.status()
        self.assertEqual(status["state"], "crashed")
        self.assertTrue(status.get("restartable"))
        self.assertEqual(status["code"], "codex_crashed")
        _assert_no_secrets(self, status, "crashed status")

    def test_operations_self_heal_after_crash(self):
        """崩溃后下一次操作自动重建客户端（自愈）。"""
        crashed_client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        fresh_client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        created: list = []

        def factory():
            created.append(1)
            return crashed_client if len(created) == 1 else fresh_client

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-d2-selfheal"),
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # 第一次创建崩溃客户端
        status = provider.status()
        self.assertEqual(status["state"], "signed_in")
        # 手动把 transport 标记为 crashed（模拟运行中崩溃）
        crashed_client._transport = _TransportStub(crashed=True, exit_code=9)
        # 下一次操作自愈：factory 第二次返回 fresh_client
        models = provider.models(refresh=True)
        self.assertEqual(len(created), 2)
        self.assertEqual(models[0]["id"], "openai-codex/gpt-5.6-luna")

    def test_explicit_restart_returns_latest_status(self):
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        provider = self._provider(client)
        provider.status()
        status = provider.restart()
        self.assertEqual(status["state"], "signed_in")
        self.assertTrue(client.closed)  # 旧客户端被关闭

    def test_restart_after_crash_reports_crashed_until_recovered(self):
        """restart() 显式重建：先报告 crashed，重建后恢复。"""
        from openbrep.codex.app_server import CodexAppServerError

        crash_client = _D2FakeClient(transport=_TransportStub(crashed=True, exit_code=7))
        good_client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        queue = iter([crash_client, good_client])
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-d2-restart"),
            client_factory=lambda: next(queue),
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # status 报告 crashed（不重建）
        status = provider.status()
        self.assertEqual(status["state"], "crashed")
        # 显式 restart → 重建 → signed_in
        status = provider.restart()
        self.assertEqual(status["state"], "signed_in")
        self.assertTrue(crash_client.closed)


FAKE_SERVER = Path(__file__).resolve().parent / "fake_codex_app_server.py"


class TestCodexD2ProviderWireIntegration(unittest.TestCase):
    """D2：provider ↔ 真实管道 ↔ fake app-server 进程的端到端生命周期。"""

    @contextlib.contextmanager
    def _provider(self, extra_env=None, codex_home=None, rpc_timeout=5.0):
        """fake 模式 env 按测试隔离：进入时清空 FAKE_CODEX_*，退出时恢复。"""
        import os
        import sys
        import tempfile

        from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport

        saved = {
            key: os.environ.pop(key)
            for key in list(os.environ)
            if key.startswith("FAKE_CODEX_")
        }
        for key, value in (extra_env or {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        home = codex_home or Path(tempfile.mkdtemp(prefix="obr-d2-wire-")) / "home"

        def factory():
            # 不在这里 start()：由 CodexProvider._get_client() 统一 start（避免
            # 二次 initialize 消耗 fake 的 crash 请求预算）。
            transport = StdioJsonRpcTransport(
                codex_binary=sys.executable,
                codex_home=home,
                extra_args=(str(FAKE_SERVER),),
                rpc_timeout=rpc_timeout,
            )
            return CodexAppServerClient(transport=transport)

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

    def test_login_cancel_with_real_pipe(self):
        with self._provider() as provider:
            result = provider.login_start()
            self.assertEqual(result, {"state": "login_started", "method": "chatgpt"})
            cancel = provider.login_cancel()
            self.assertEqual(cancel, {"state": "signed_out"})
            status = provider.status(refresh=True)
            self.assertEqual(status["state"], "signed_out")

    def test_device_code_login_with_real_pipe(self):
        with self._provider(extra_env={"FAKE_CODEX_LOGIN_TYPE": "chatgptDeviceCode"}) as provider:
            result = provider.login_start_device_code()
            self.assertEqual(result["method"], "chatgptDeviceCode")
            self.assertEqual(result["verification_url"], "https://example.test/device")
            self.assertEqual(result["user_code"], "ABCD-EFGH")
            self.assertNotIn("loginId", result)
            provider.login_cancel()

    def test_crash_then_status_crashed_then_restart(self):
        import time

        marker = Path(__import__("tempfile").mkdtemp(prefix="obr-d2-wire-marker-")) / "crashed"
        with self._provider(
            extra_env={
                "FAKE_CODEX_SIGNED_IN": "1",
                "FAKE_CODEX_CRASH_AFTER_REQUESTS": "2",
                "FAKE_CODEX_CRASH_MARKER": str(marker),
            }
        ) as provider:
            # 第一次 status → 创建客户端 → initialize(1) + account/read(2) → 崩溃
            status = provider.status()
            self.assertEqual(status["state"], "signed_in")
            time.sleep(1.0)
            # status 报告 crashed（不重建）
            status = provider.status(refresh=True)
            self.assertEqual(status["state"], "crashed")
            self.assertTrue(status.get("restartable"))
            # 显式 restart → 新进程（marker 阻止再次崩溃）→ 恢复 signed_in
            status = provider.restart()
            self.assertEqual(status["state"], "signed_in")
            # 操作自愈
            models = provider.models()
            self.assertEqual(
                [m["id"] for m in models],
                ["openai-codex/gpt-5.6-luna", "openai-codex/gpt-5.6-terra"],
            )

    def test_quota_exhausted_end_to_end(self):
        with self._provider(
            extra_env={"FAKE_CODEX_SIGNED_IN": "1", "FAKE_CODEX_RATE_LIMITS": "reached"}
        ) as provider:
            status = provider.status()
            self.assertEqual(status["state"], "quota_exhausted")
            self.assertEqual(status["rate_limits"]["reached"], True)
            self.assertIn("额度", status["error"])

    def test_quota_exhausted_with_real_pipe(self):
        with self._provider(extra_env={"FAKE_CODEX_RATE_LIMITS": "reached"}) as provider:
            # 未登录：rate_limits fail closed（额度不会错误地暴露给未登录用户）
            with self.assertRaises(CodexNotSignedInError):
                provider.rate_limits()
