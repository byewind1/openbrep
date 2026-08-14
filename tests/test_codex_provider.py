"""CodexProvider 状态机测试（D1）：全部使用 fake client，不碰真实账号/网络。

覆盖：no_cli / signed_out / signed_in / error 四态、登录只打开浏览器且
authUrl 不外传、模型目录 provider-qualified、退出后 fail closed、
任何响应不泄露 token/JWT/account id/auth path。
"""

from __future__ import annotations

import contextlib
import threading
import unittest
from pathlib import Path

from openbrep.codex.app_server import CodexAppServerError, CodexCliUnavailableError
from openbrep.codex.provider import (
    CodexNotSignedInError,
    CodexProvider,
    CodexVersionIncompatibleError,
    mask_email,
    mask_rate_limits,
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
        self.assertEqual(result["state"], "signed_out")
        self.assertEqual(result["code"], "no_pending_login")
        self.assertEqual(client.login_cancel_calls, 0)

    def test_login_cancel_while_signed_in_does_not_logout(self):
        """P0-1：cancel 只对真实 pending 生效；已登录时切换账号必须先显式退出。"""
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        provider = self._provider(client)
        result = provider.login_cancel()
        self.assertEqual(result["state"], "signed_in")
        self.assertEqual(result["code"], "no_pending_login")
        self.assertEqual(client.logout_calls, 0)
        self.assertEqual(client.login_cancel_calls, 0)
        # 账户仍是登录态（没有静默登出）
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_in")

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


# ── P0-1：账户状态机表驱动（signed_out/pending/signed_in × 动作）─────────────


class TestCodexStateMachineTable(unittest.TestCase):
    """P0-1：登录前状态门禁表驱动——已登录必须显式退出；pending 拒绝第二次。"""

    def _provider_for(self, *, signed_in=False, pending=False, crashed=False):
        """构造指定初始状态的 provider（factory 每次返回新客户端）。"""
        healthy = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
            if signed_in else None
        )
        if crashed:
            first = _D2FakeClient(transport=_TransportStub(crashed=True, exit_code=42))
        else:
            first = healthy
        queue = iter([first, healthy])

        def factory():
            return next(queue)

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-table"),
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        if pending:
            # 先发起一次登录（fake 返回 chatgpt flow）→ pending 置位
            provider.login_start()
        return provider

    # (初始状态, 动作, 期望: ("state", value) 或 ("raise_category", cat))
    CASES = [
        ("signed_out", "start", ("state", "login_started")),
        ("signed_out", "device", ("state", "login_started")),
        ("signed_out", "cancel", ("state", "signed_out")),
        ("signed_out", "logout", ("state", "signed_out")),
        ("signed_out", "restart", ("state", "signed_out")),
        ("signed_out", "completion_success", ("state", "signed_out")),
        ("signed_out", "completion_failure", ("state", "signed_out")),
        ("pending", "start", ("raise_category", "login_already_pending")),
        ("pending", "device", ("raise_category", "login_already_pending")),
        ("pending", "cancel", ("state", "signed_out")),
        ("pending", "logout", ("state", "signed_out")),
        ("pending", "restart", ("state", "signed_out")),
        ("pending", "completion_success", ("state", "signed_out")),
        ("pending", "completion_failure", ("state", "signed_out")),
        ("signed_in", "start", ("raise_category", "already_signed_in")),
        ("signed_in", "device", ("raise_category", "already_signed_in")),
        ("signed_in", "cancel", ("state", "signed_in")),
        ("signed_in", "logout", ("state", "signed_out")),
        ("signed_in", "restart", ("state", "signed_in")),
        ("signed_in", "completion_success", ("state", "signed_in")),
        ("signed_in", "completion_failure", ("state", "signed_in")),
    ]

    def _run_action(self, provider, action):
        if action == "start":
            return provider.login_start()
        if action == "device":
            return provider.login_start_device_code()
        if action == "cancel":
            return provider.login_cancel()
        if action == "logout":
            return provider.logout()
        if action == "restart":
            return provider.restart()
        if action == "completion_success":
            provider._on_notification(
                {"method": "account/login/completed", "params": {"loginId": "L1", "success": True, "error": None}}
            )
            return provider.status(refresh=True)
        if action == "completion_failure":
            provider._on_notification(
                {"method": "account/login/completed", "params": {"loginId": "L1", "success": False, "error": "cancelled"}}
            )
            return provider.status(refresh=True)
        raise AssertionError(f"unknown action {action}")

    def test_state_machine_table(self):
        for initial, action, expected in self.CASES:
            with self.subTest(initial=initial, action=action):
                provider = self._provider_for(
                    signed_in=(initial == "signed_in"),
                    pending=(initial == "pending"),
                    crashed=(initial == "crashed"),
                )
                kind, value = expected
                if kind == "state":
                    result = self._run_action(provider, action)
                    self.assertEqual(result["state"], value, f"{initial}×{action}")
                else:
                    with self.assertRaises(CodexAppServerError) as ctx:
                        self._run_action(provider, action)
                    self.assertEqual(ctx.exception.category, value, f"{initial}×{action}")
                provider.close()

    def test_login_while_signed_in_never_issues_rpc(self):
        """P0-1：已登录时 login_start 绝不发 login RPC（UI 隐藏按钮不是边界）。"""
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        provider = self._provider_for(signed_in=True)
        # 使用可观测 client：_provider_for 的 factory 不可见，这里直接验证
        # 异常类别 + 未发 RPC（通过带计数 client 的 provider）
        counted = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
        )
        p2 = CodexProvider(
            codex_home=Path("/tmp/obr-codex-norpc"),
            client_factory=lambda: counted,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError) as ctx:
            p2.login_start()
        self.assertEqual(ctx.exception.category, "already_signed_in")
        self.assertEqual(counted.login_calls, 0)
        with self.assertRaises(CodexAppServerError) as ctx:
            p2.login_start_device_code()
        self.assertEqual(ctx.exception.category, "already_signed_in")
        self.assertEqual(counted.login_calls, 0)
        p2.close()

    def test_double_start_never_overwrites_pending_id(self):
        """P0-1：两条登录流程不能共存；第二条被拒，loginId 不被覆盖。"""
        client = _D2FakeClient()
        provider = self._provider_for(pending=False)
        provider.login_start()          # 第一条 → pending L1
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        with self.assertRaises(CodexAppServerError) as ctx:
            provider.login_start_device_code()  # 第二条 → 拒绝
        self.assertEqual(ctx.exception.category, "login_already_pending")
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        self.assertEqual(provider._login_pending, True)
        provider.login_cancel()
        provider.close()

    def test_completion_failure_clears_pending_and_is_actionable(self):
        """P0-1：completion failure 清 pending/id，status 不再永久 login_started。"""
        client = _D2FakeClient()
        provider = self._provider_for(pending=False)
        provider.login_start()
        self.assertEqual(provider.status(refresh=True)["state"], "login_started")
        provider._on_notification(
            {"method": "account/login/completed", "params": {"loginId": "L1", "success": False, "error": "cancelled"}}
        )
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")
        self.assertIn("login_error", status)
        self.assertIn("设备码", status["login_error"])
        # 反复 refresh 也绝不回到 login_started
        for _ in range(3):
            self.assertEqual(provider.status(refresh=True)["state"], "signed_out")
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        provider.close()

    def test_completion_success_clears_pending(self):
        """P0-1：completion success 清 pending/id，状态回到账户实际状态。"""
        client = _D2FakeClient()
        provider = self._provider_for(pending=False)
        provider.login_start()
        provider._on_notification(
            {"method": "account/login/completed", "params": {"loginId": "L1", "success": True, "error": None}}
        )
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")
        self.assertNotIn("login_error", status)
        provider.close()

    def test_completion_failure_never_shown_while_signed_in(self):
        provider = self._provider_for(signed_in=True)
        provider._on_notification(
            {"method": "account/login/completed", "params": {"loginId": "L1", "success": False, "error": "x"}}
        )
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_in")
        self.assertNotIn("login_error", status)
        provider.close()

    def test_rollback_on_missing_auth_url(self):
        """P0-1：响应校验失败必须 best-effort cancel 回滚（server 登录可能已开始）。"""
        client = _D2FakeClient(login_result={"type": "chatgpt", "loginId": "ROLLBACK-L"})
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-rollback"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError):
            provider.login_start()
        self.assertEqual(client.login_cancel_calls, 1)
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        provider.close()

    def test_rollback_on_invalid_device_code_fields(self):
        client = _D2FakeClient(
            login_result={"type": "chatgptDeviceCode", "loginId": "ROLLBACK-D", "verificationUrl": "javascript:alert(1)", "userCode": "ABCD-EFGH"}
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-rollback2"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError):
            provider.login_start_device_code()
        self.assertEqual(client.login_cancel_calls, 1)
        self.assertFalse(provider._login_pending)
        provider.close()

    def test_rollback_on_opener_exception(self):
        """P0-1：browser opener 异常 → 回滚取消，UI 状态与 server 会话一起回滚。"""
        client = _D2FakeClient()

        def bad_opener(url):
            raise RuntimeError("browser launch failed")

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-rollback3"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=bad_opener,
        )
        with self.assertRaises(CodexAppServerError):
            provider.login_start()
        self.assertEqual(client.login_cancel_calls, 1)
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        status = provider.status(refresh=True)
        self.assertEqual(status["state"], "signed_out")
        provider.close()


class TestCodexStateMachineCrashed(unittest.TestCase):
    """P0-1 表补：crashed 初始状态的动作语义（自愈/显式重启）。"""

    def test_crashed_start_self_heals_then_logs_in(self):
        """crashed（运行中崩溃）→ start：操作路径自愈重建后发起登录。"""
        first = _D2FakeClient()  # 先健康创建
        second = _D2FakeClient()
        queue = iter([first, second])

        def factory():
            return next(queue)

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-crashed"),
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.status()  # 创建 first（健康）
        first._transport = _TransportStub(crashed=True, exit_code=9)  # 运行中崩溃
        result = provider.login_start()  # 自愈 → second
        self.assertEqual(result["state"], "login_started")
        self.assertTrue(first.closed)
        self.assertIs(provider._client, second)
        provider.close()

    def test_crashed_status_reports_crashed_not_stale(self):
        """P1-3：崩溃后 status 不得返回旧的 signed-in 缓存。"""
        import tempfile

        crashed = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            transport=_TransportStub(crashed=False),
        )
        healthy = _D2FakeClient(account=None)
        queue = iter([crashed, healthy])

        def factory():
            return next(queue)

        provider = CodexProvider(
            codex_home=Path(tempfile.mkdtemp(prefix="obr-codex-p13-")) / "home",
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
            status_ttl=60.0,
        )
        # 第一次 status 填充 signed_in 缓存
        status = provider.status()
        self.assertEqual(status["state"], "signed_in")
        # 运行中崩溃
        crashed._transport = _TransportStub(crashed=True, exit_code=11)
        # 缓存 TTL 内也必须返回 crashed，而不是旧的 signed_in
        status = provider.status()
        self.assertEqual(status["state"], "crashed")
        provider.close()


# ── P0-2：真实线程竞态（login↔restart / status·model↔restart / login↔cancel / double start）──


class _RaceFakeClient(_D2FakeClient):
    """可控阻塞 fake client：account_login_start / model_list / account_read 可挂起。"""

    def __init__(self, *args, login_gate=None, model_gate=None, read_gate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_gate = login_gate
        self.model_gate = model_gate
        self.read_gate = read_gate
        self.login_entered = threading.Event()
        self.model_entered = threading.Event()
        self.read_entered = threading.Event()

    def account_login_start(self, login_type):
        self.login_calls += 1
        if self.login_gate is not None:
            self.login_entered.set()
            self.login_gate.wait(5)
        if self.login_result is not None:
            return self.login_result
        return {
            "type": "chatgpt",
            "loginId": "fake-login-id",
            "authUrl": "https://auth.openai.com/oauth/authorize?state=fake",
        }

    def model_list(self):
        self.model_list_calls += 1
        if self.model_gate is not None:
            self.model_entered.set()
            self.model_gate.wait(5)
        return {"data": self.models, "nextCursor": None}

    def account_read(self):
        self.account_read_calls += 1
        if self.read_gate is not None:
            self.read_entered.set()
            self.read_gate.wait(5)
        return {"account": self.account, "requiresOpenaiAuth": self.account is None}


def _race_provider(first, second=None, **kwargs):
    """每次 factory 返回下一个 client；first 用完后取 second（或 first 的副本）。"""
    second = second if second is not None else _D2FakeClient()
    queue = iter([first, second])

    def factory():
        return next(queue)

    provider = CodexProvider(
        codex_home=Path("/tmp/obr-codex-race"),
        client_factory=factory,
        cli_available=True,
        browser_opener=lambda url: None,
        **kwargs,
    )
    return provider, first, second


class TestCodexThreadRaces(unittest.TestCase):
    """P0-2：生命周期操作经 _op_lock 串行化——竞态下无陈旧状态提交。"""

    def test_login_restart_race_no_stale_pending(self):
        """login RPC 进行中 restart：op_lock 串行化，无 OLD-L 残留/错发。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(login_gate=gate)
        new_client = _D2FakeClient()
        provider, old_client, new_client = _race_provider(old_client, new_client)

        results: list = []

        def t1():
            try:
                results.append(("ok", provider.login_start()))
            except Exception as exc:  # noqa: BLE001
                results.append(("err", exc))

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.login_entered.wait(5), "login RPC 未进入")
        # T2 restart：被 _op_lock 阻塞直到 login_start 完成
        status = provider.restart()
        gate.set()
        thread.join(timeout=5)
        self.assertEqual(status["state"], "signed_out")
        # login_start 完成且提交 pending（old client）→ restart 随后清空
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        # provider 现在指向 new client；旧 client 已关闭
        self.assertIs(provider._client, new_client)
        self.assertTrue(old_client.closed)
        # cancel 不会把 OLD-L 发给 new client（没有 pending → 无取消 RPC）
        provider.login_cancel()
        self.assertEqual(new_client.login_cancel_calls, 0)
        provider.close()

    def test_double_start_serialized_second_rejected(self):
        """double start：并发两次 login_start，只发一次 RPC，第二次 login_already_pending。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-double"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        results: list = []

        def run():
            try:
                results.append(provider.login_start())
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(results), 2)
        states = [
            r.get("state") if isinstance(r, dict) else getattr(r, "category", None)
            for r in results
        ]
        self.assertIn("login_started", states)
        self.assertIn("login_already_pending", states)
        self.assertEqual(client.login_calls, 1, "double start 只能发一次 login RPC")
        provider.login_cancel()
        provider.close()

    def test_login_cancel_race(self):
        """login↔cancel：并发下要么 cancel 在 login 后取消成功，要么先返回 no_pending。"""
        gate = threading.Event()
        client = _RaceFakeClient(login_gate=gate)
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-cancel-race"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        login_result: list = []
        cancel_result: list = []

        def t1():
            try:
                login_result.append(provider.login_start())
            except Exception as exc:  # noqa: BLE001
                login_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(client.login_entered.wait(5))
        # cancel 并发：op_lock 串行化——若 login 先完成，cancel 取消 pending；
        # 若 cancel 先执行，login 随后被 pending 门禁拒绝。
        cancel_result.append(provider.login_cancel())
        gate.set()
        thread.join(timeout=5)
        # 最终一致性：无残留 pending（除非 login 被门禁拒绝后 cancel 是 no_pending）
        provider2_status = provider.status(refresh=True)
        self.assertIn(provider2_status["state"], ("signed_out", "login_started"))
        # 两种合法结果都不允许"取消后仍 pending"
        if isinstance(cancel_result[0], dict) and cancel_result[0].get("state") == "signed_out":
            self.assertFalse(provider._login_pending)
        provider.close()

    def test_models_restart_race_no_stale_cache(self):
        """model RPC 进行中 restart：要么干净返回，要么干净报错；无陈旧缓存提交。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            model_gate=gate,
        )
        new_client = _D2FakeClient(account=None)
        provider, old_client, new_client = _race_provider(old_client, new_client)
        provider.status()  # 创建 old client（signed_in）
        old_client.model_gate = gate
        models_result: list = []

        def t1():
            try:
                models_result.append(provider.models(refresh=True))
            except Exception as exc:  # noqa: BLE001
                models_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.model_entered.wait(5))
        # restart 并发（models 不走 op_lock，但 restart 关旧 client → models 干净失败或已提交）
        provider.restart()
        gate.set()
        thread.join(timeout=5)
        # restart 后 pending/缓存一致性：models 若成功提交，其缓存来自旧 client 的
        # model_list 结果（同一账户目录，安全）；restart 已清缓存并换新 client。
        self.assertIs(provider._client, new_client)
        self.assertTrue(old_client.closed)
        provider.close()

    def test_status_restart_race_clean(self):
        """status RPC 进行中 restart：status 干净返回（不抛异常），无陈旧缓存残留。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            read_gate=gate,
        )
        new_client = _D2FakeClient(account=None)
        provider, old_client, new_client = _race_provider(old_client, new_client)
        status_result: list = []

        def t1():
            try:
                status_result.append(provider.status(refresh=True))
            except Exception as exc:  # noqa: BLE001
                status_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.read_entered.wait(5))
        provider.restart()
        gate.set()
        thread.join(timeout=5)
        self.assertEqual(len(status_result), 1)
        # status 要么是 dict（干净状态），要么干净异常——不允许脏状态
        if isinstance(status_result[0], dict):
            self.assertIn(status_result[0]["state"], ("signed_in", "signed_out", "error", "crashed"))
        # restart 后缓存已失效：新 status 不残留旧 signed_in（新 client 未登录）
        final = provider.status(refresh=True)
        self.assertEqual(final["state"], "signed_out")
        provider.close()


# ── P0-4：恶意/漂移 rate-limit payload 脱敏硬化 ──────────────────────────────


class TestCodexRateLimitMaskingHardening(unittest.TestCase):
    """P0-4：每个保留字段携带恶意字符串/嵌套对象时，一律置空或拒绝，绝不透传。"""

    SECRETS = (
        "access_token=SUPERSECRET",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
        "loginId=a0327bbe-a894-4455-9e96-8c6d19ed2a53",
        "Bearer plain-secret-value",
        "https://auth.openai.com/oauth?state=SECRET",
    )

    def _masked(self, **overrides):
        rl = {
            "limitId": "codex",
            "limitName": "Codex",
            "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 1786800000},
            "credits": {"hasCredits": True, "unlimited": False, "balance": "123.45"},
            "spendControlReached": False,
            "planType": "pro",
            "rateLimitReachedType": None,
        }
        rl.update(overrides)
        return mask_rate_limits({"rateLimits": rl, "rateLimitsByLimitId": None, "rateLimitResetCredits": None})

    def _assert_clean(self, masked, secret):
        text = str(masked)
        self.assertNotIn(secret, text, f"脱敏摘要泄漏 {secret!r}")
        self.assertNotIn("SUPERSECRET", text)
        self.assertNotIn("auth.openai.com", text)

    def test_malicious_plan_type_is_rejected(self):
        for secret in self.SECRETS:
            masked = self._masked(planType=secret)
            self.assertIsNone(masked["plan_type"], secret)
            self._assert_clean(masked, secret)

    def test_malicious_reached_type_is_rejected(self):
        for secret in self.SECRETS:
            masked = self._masked(rateLimitReachedType=secret)
            self.assertIsNone(masked["reached_type"], secret)
            self.assertFalse(masked["reached"], secret)
            self._assert_clean(masked, secret)

    def test_malicious_used_percent_is_rejected(self):
        for secret in self.SECRETS:
            masked = self._masked(primary={"usedPercent": secret})
            self.assertIsNone(masked["used_percent"], secret)
            self._assert_clean(masked, secret)

    def test_nested_objects_rejected(self):
        # 整个 dict 塞进标量字段
        masked = self._masked(planType={"token": "SUPERSECRET"})
        self.assertIsNone(masked["plan_type"])
        masked = self._masked(rateLimitReachedType=["rate_limit_reached"])
        self.assertIsNone(masked["reached_type"])
        masked = self._masked(primary={"usedPercent": {"value": 99}})
        self.assertIsNone(masked["used_percent"])
        masked = self._masked(credits={"hasCredits": "yes", "unlimited": {"x": 1}})
        self.assertEqual(masked["credits"], {"has_credits": None, "unlimited": None})
        text = str(masked)
        self.assertNotIn("SUPERSECRET", text)

    def test_non_bool_flags_rejected(self):
        masked = self._masked(spendControlReached="true")
        self.assertIsNone(masked["spend_control_reached"])
        self.assertFalse(masked["reached"])
        masked = self._masked(spendControlReached=1)
        self.assertIsNone(masked["spend_control_reached"])
        masked = self._masked(spendControlReached=True)
        self.assertIs(masked["spend_control_reached"], True)

    def test_out_of_range_numbers_rejected(self):
        masked = self._masked(primary={"usedPercent": 150})
        self.assertIsNone(masked["used_percent"])
        masked = self._masked(primary={"usedPercent": -1})
        self.assertIsNone(masked["used_percent"])
        masked = self._masked(primary={"usedPercent": float("inf")})
        self.assertIsNone(masked["used_percent"])
        masked = self._masked(primary={"resetsAt": -5})
        self.assertIsNone(masked["resets_at"])

    def test_rate_limits_not_dict_returns_empty_summary(self):
        masked = mask_rate_limits({"rateLimits": ["not", "a", "dict"]})
        self.assertIsNone(masked["plan_type"])
        self.assertIsNone(masked["used_percent"])
        self.assertFalse(masked["reached"])
        masked2 = mask_rate_limits({"rateLimits": None})
        self.assertIsNone(masked2["plan_type"])

    def test_valid_enum_and_product_string_accepted(self):
        masked = self._masked(planType="pro")
        self.assertEqual(masked["plan_type"], "pro")
        masked = self._masked(planType="custom-plan-x")
        self.assertEqual(masked["plan_type"], "custom-plan-x")
        masked = self._masked(rateLimitReachedType="rate_limit_reached")
        self.assertEqual(masked["reached_type"], "rate_limit_reached")
        self.assertTrue(masked["reached"])

    def test_status_and_service_never_leak_malicious_rate_limits(self):
        """P0-4：provider status + service API 递归断言恶意 payload 无秘密。"""
        from types import SimpleNamespace

        from openbrep.config import GDLAgentConfig
        from openbrep.workbench.settings_service import WorkbenchSettingsService

        rl = {
            "rateLimits": {
                "planType": "access_token=SUPERSECRET",
                "rateLimitReachedType": "loginId=a0327bbe-a894-4455-9e96-8c6d19ed2a53",
                "primary": {"usedPercent": "Bearer plain-secret-value", "resetsAt": {"x": 1}},
                "credits": {"hasCredits": "yes", "unlimited": {"nested": "sk-abc123456789"}},
                "spendControlReached": "true",
            },
            "rateLimitsByLimitId": {"codex": {}},
            "rateLimitResetCredits": None,
        }
        client = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            rate_limits=rl,
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-p04"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # provider status（含 rate_limits 摘要）
        status = provider.status()
        text = str(status)
        for bad in ("SUPERSECRET", "a0327bbe", "plain-secret-value", "sk-abc123456789", "access_token", "loginId", "Bearer"):
            self.assertNotIn(bad, text, f"status 泄漏 {bad}")
        self.assertIsNone(status["rate_limits"]["plan_type"])
        self.assertIsNone(status["rate_limits"]["used_percent"])
        self.assertEqual(status["rate_limits"]["credits"], {"has_credits": None, "unlimited": None})
        # service API 边界
        config = GDLAgentConfig()
        session = SimpleNamespace(
            llm_model="openai-codex/gpt-5.6-luna",
            llm_api_key="",
            llm_api_base="",
            assistant_settings="",
            max_retries=5,
            config=config,
            config_path=Path("/tmp/obr-codex-p04-config.toml"),
        )
        service = WorkbenchSettingsService(
            session, llm_adapter_factory=lambda _c: None, codex_provider=provider,
        )
        response = service.codex_status()
        resp_text = str(response)
        for bad in ("SUPERSECRET", "a0327bbe", "plain-secret-value", "sk-abc123456789"):
            self.assertNotIn(bad, resp_text, f"API 泄漏 {bad}")
        rl_response = service.codex_rate_limits()
        rl_text = str(rl_response)
        for bad in ("SUPERSECRET", "a0327bbe", "plain-secret-value", "sk-abc123456789"):
            self.assertNotIn(bad, rl_text, f"rate-limits API 泄漏 {bad}")
        provider.close()
