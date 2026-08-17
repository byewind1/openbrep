"""CodexProvider 状态机测试（D1）：全部使用 fake client，不碰真实账号/网络。

覆盖：no_cli / signed_out / signed_in / error 四态、登录只打开浏览器且
authUrl 不外传、模型目录 provider-qualified、退出后 fail closed、
任何响应不泄露 token/JWT/account id/auth path。
"""

from __future__ import annotations

import contextlib
import json
import threading
import unittest
from collections import deque
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
    "token",
    "jwt",
    "access_token",
    "auth_url",
    "authUrl",
    "loginId",
    "login_id",
    "auth_path",
    "codex_home",
    "authorization",
    "chatgpt_account_id",
    "accountId",
}
_FORBIDDEN_VALUES = (
    "sk-",
    "eyj",
    "auth.openai.com",
    "localhost:1455",
    "fake-login-id",
    "auth.json",
    ".codex",
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
            {
                "id": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "displayName": "GPT-5.6 Luna",
                "hidden": False,
                "modelSpecialty": None,
            },
            {
                "id": "gpt-5.6-terra",
                "model": "gpt-5.6-terra",
                "displayName": "GPT-5.6 Terra",
                "hidden": False,
                "modelSpecialty": "balanced",
            },
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
            for bad in (
                "SUPERSECRET",
                "opaque-secret",
                "plain-secret-value",
                "access_token",
                "loginId",
                "Bearer",
            ):
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
        service = WorkbenchSettingsService(
            session, llm_adapter_factory=lambda _c: None, codex_provider=provider
        )
        response = service.codex_status()
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["state"], "error")
        self.assertEqual(response["code"], "codex_app_server")
        text = str(response)
        for bad in (
            "SUPERSECRET",
            "opaque-secret",
            "plain-secret-value",
            "access_token",
            "loginId",
            "Bearer",
        ):
            self.assertNotIn(bad, text, f"API 响应泄漏 {bad}")
        # llm_settings 的 codex 块同样干净
        llm = service.llm_settings()
        llm_text = str(llm)
        for bad in (
            "SUPERSECRET",
            "opaque-secret",
            "plain-secret-value",
            "access_token",
            "loginId",
            "Bearer",
        ):
            self.assertNotIn(bad, llm_text, f"llm_settings 泄漏 {bad}")


# ── D2：版本协商、登录白名单、device-code、取消、额度、崩溃/重启 ─────────────


class _D2FakeClient(_FakeCodexClient):
    """D1 fake + D2 扩展：server_version / transport（崩溃检测）/ 率限/取消。"""

    def __init__(
        self,
        *,
        account=None,
        models=None,
        version=(0, 147, 0),
        login_result=None,
        rate_limits=None,
        cancel_status="canceled",
        transport=None,
    ):
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
        client = _D2FakeClient(
            login_result={"type": "chatgpt", "loginId": "L", "authUrl": "https://x"}
        )
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
        provider.login_start()  # 开始浏览器登录（保存 loginId）
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
        provider.status(refresh=True)  # 填充 status + rate_limits 缓存
        provider.models(refresh=True)  # models() 内部再查一次 status（refresh）
        before = client.rate_limits_calls
        self.assertGreaterEqual(before, 1)
        # account/rateLimits/updated → 额度缓存失效（下次读取重新拉取）
        provider._on_notification({"method": "account/rateLimits/updated", "params": {}})
        provider.rate_limits()
        self.assertGreater(client.rate_limits_calls, before)
        # account/login/completed → status + models 缓存失效（命中已提交的 pending）
        with provider._lock:
            provider._pending_login_id = "fake-login-id"
            provider._login_pending = True
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
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
        return {
            "rateLimits": rl,
            "rateLimitsByLimitId": {"codex": rl},
            "rateLimitResetCredits": None,
        }

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
            key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")
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

    def test_start_timeout_closes_app_server_and_retry_clean(self):
        """第五轮 P0（真实管道）：login/start 延迟超 rpc_timeout → 失败后
        app-server 被关闭（无遗留进程）；重试创建干净的新 client 并成功。"""
        import tempfile

        marker = Path(tempfile.mkdtemp(prefix="obr-d2-delay-once-")) / "delayed"
        with self._provider(
            extra_env={
                "FAKE_CODEX_DELAY_METHOD": "account/login/start",
                "FAKE_CODEX_DELAY_RESPONSE_SECONDS": "3.0",
                "FAKE_CODEX_DELAY_ONCE_MARKER": str(marker),
            },
            rpc_timeout=0.5,
        ) as provider:
            with self.assertRaises(CodexAppServerError) as ctx:
                provider.login_start()
            self.assertEqual(ctx.exception.category, "timeout")
            # app-server 已关闭、start 会话完整退役
            self.assertIsNone(provider._client)
            self.assertIsNone(provider._login_start_inflight)
            self.assertFalse(provider._login_pending)
            self.assertEqual(provider._completed_login_ids, deque())
            # 重试（一次性延迟已消费）→ 干净的新 client + 成功登录
            result = provider.login_start()
            self.assertEqual(result["state"], "login_started")
            self.assertTrue(provider._login_pending)
            cancel_result = provider.login_cancel()
            self.assertEqual(cancel_result["state"], "signed_out")


# ── P0-1：账户状态机表驱动（signed_out/pending/signed_in × 动作）─────────────


class TestCodexStateMachineTable(unittest.TestCase):
    """P0-1：登录前状态门禁表驱动——已登录必须显式退出；pending 拒绝第二次。"""

    def _provider_for(self, *, signed_in=False, pending=False, crashed=False):
        """构造指定初始状态的 provider（factory 每次返回新客户端）。"""
        healthy = _D2FakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}
            if signed_in
            else None
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
                {
                    "method": "account/login/completed",
                    "params": {"loginId": "fake-login-id", "success": True, "error": None},
                }
            )
            return provider.status(refresh=True)
        if action == "completion_failure":
            provider._on_notification(
                {
                    "method": "account/login/completed",
                    "params": {"loginId": "fake-login-id", "success": False, "error": "cancelled"},
                }
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
        provider.login_start()  # 第一条 → pending L1
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
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": False, "error": "cancelled"},
            }
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
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
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
            {
                "method": "account/login/completed",
                "params": {"loginId": "L1", "success": False, "error": "x"},
            }
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
            login_result={
                "type": "chatgptDeviceCode",
                "loginId": "ROLLBACK-D",
                "verificationUrl": "javascript:alert(1)",
                "userCode": "ABCD-EFGH",
            }
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
            r.get("state") if isinstance(r, dict) else getattr(r, "category", None) for r in results
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
            self.assertIn(
                status_result[0]["state"], ("signed_in", "signed_out", "error", "crashed")
            )
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
        return mask_rate_limits(
            {"rateLimits": rl, "rateLimitsByLimitId": None, "rateLimitResetCredits": None}
        )

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

    def test_valid_enum_accepted(self):
        masked = self._masked(planType="pro")
        self.assertEqual(masked["plan_type"], "pro")
        # P0-5：非枚举的“自定义产品字符串”（含裸字母数字）一律置空，绝不回显
        masked = self._masked(planType="custom-plan-x")
        self.assertIsNone(masked["plan_type"])
        masked = self._masked(planType="DEVACCOUNTSECRET")
        self.assertIsNone(masked["plan_type"])
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
        for bad in (
            "SUPERSECRET",
            "a0327bbe",
            "plain-secret-value",
            "sk-abc123456789",
            "access_token",
            "loginId",
            "Bearer",
        ):
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
            session,
            llm_adapter_factory=lambda _c: None,
            codex_provider=provider,
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


# ── P0-1：generation 绑定缓存——旧请求在 restart/logout 后不得回写 ─────────


class TestCodexGenerationCacheBinding(unittest.TestCase):
    """P0-1：status/models/rate 缓存绑定账户会话 generation；restart/logout 后
    旧 client 上迟到的 RPC 结果绝不回写，随后非 refresh 读取反映新会话。"""

    def test_models_restart_race_stale_write_rejected(self):
        """旧 model RPC 在 restart 后返回 → 不得写 _models_cache；
        随后 models(refresh=False) 必须反映新 client（未登录 → 报错）。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            model_gate=gate,
        )
        new_client = _D2FakeClient(account=None)
        provider, old_client, new_client = _race_provider(old_client, new_client)
        provider.status(refresh=True)  # 创建 old client（signed_in）
        models_result: list = []

        def t1():
            try:
                models_result.append(provider.models(refresh=True))
            except Exception as exc:  # noqa: BLE001
                models_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.model_entered.wait(5))
        provider.restart()  # 换 new client（signed_out），gen 递增
        gate.set()
        thread.join(timeout=5)
        # 关键断言：旧 model RPC 结果不得回写缓存
        self.assertIsNone(provider._models_cache, "restart 后旧 client 结果回写了 models 缓存")
        # 非 refresh 读取也必须反映新会话（未登录 → fail closed）
        with self.assertRaises(CodexNotSignedInError):
            provider.models(refresh=False)
        provider.close()

    def test_status_restart_race_stale_write_rejected(self):
        """旧 status RPC 在 restart 后返回 → 不得写 _status_cache；
        随后 status(refresh=False) 必须反映新 client（signed_out）。"""
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
        # 关键断言：restart 后非 refresh status 是 signed_out，而不是旧 signed_in
        # （restart 自身会刷新并缓存新的 signed_out；若旧 signed_in 被回写，这里必红）
        final = provider.status(refresh=False)
        self.assertEqual(final["state"], "signed_out")
        self.assertEqual(final.get("connected"), False)
        cached_state = (provider._status_cache or {}).get("state")
        self.assertNotEqual(cached_state, "signed_in", "restart 后旧 client 结果回写了 status 缓存")
        provider.close()

    def test_status_logout_race_stale_write_rejected(self):
        """旧 status RPC 在 logout 后返回 → 不得回写缓存；非 refresh 读取 signed_out。"""
        gate = threading.Event()
        client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            read_gate=gate,
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-logout-race"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        status_result: list = []

        def t1():
            try:
                status_result.append(provider.status(refresh=True))
            except Exception as exc:  # noqa: BLE001
                status_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(client.read_entered.wait(5))
        provider.logout()  # 同 client 上登出（gen 递增）
        gate.set()
        thread.join(timeout=5)
        self.assertEqual(len(status_result), 1)
        # 关键断言：logout 后非 refresh status 不得是旧 signed_in
        final = provider.status(refresh=False)
        self.assertEqual(final["state"], "signed_out")
        provider.close()

    def test_models_logout_race_stale_write_rejected(self):
        """旧 model RPC 在 logout 后返回 → 不得写 models 缓存；随后读取 fail closed。"""
        gate = threading.Event()
        client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            model_gate=gate,
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-mlogout-race"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.status(refresh=True)
        models_result: list = []

        def t1():
            try:
                models_result.append(provider.models(refresh=True))
            except Exception as exc:  # noqa: BLE001
                models_result.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(client.model_entered.wait(5))
        provider.logout()
        gate.set()
        thread.join(timeout=5)
        self.assertIsNone(provider._models_cache, "logout 后旧 client 结果回写了 models 缓存")
        with self.assertRaises(CodexNotSignedInError):
            provider.models(refresh=False)
        provider.close()


# ── P0-2：loginId 校验与 completion 精确关联 ────────────────────────────────


class TestCodexLoginIdValidation(unittest.TestCase):
    """P0-2：loginId 缺失/非法 → 关闭 app-server（无法按 id 取消）fail closed。"""

    def test_missing_login_id_closes_client_and_raises(self):
        client = _D2FakeClient(
            login_result={"type": "chatgpt", "authUrl": "https://auth.openai.com/x"}
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-nolid"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError) as ctx:
            provider.login_start()
        self.assertEqual(ctx.exception.category, "login_failed")
        # 无法按 id 取消 → 关闭 app-server 强制终止
        self.assertTrue(client.closed)
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        self.assertIsNone(provider._client)
        provider.close()

    def test_invalid_login_id_closes_client_and_raises(self):
        for bad in ("has space", "包含中文", "x" * 200, "line\nbreak", ""):
            client = _D2FakeClient(
                login_result={
                    "type": "chatgpt",
                    "loginId": bad,
                    "authUrl": "https://auth.openai.com/x",
                }
            )
            provider = CodexProvider(
                codex_home=Path("/tmp/obr-codex-badid"),
                client_factory=lambda: client,
                cli_available=True,
                browser_opener=lambda url: None,
            )
            with self.assertRaises(CodexAppServerError):
                provider.login_start()
            self.assertTrue(client.closed, f"loginId={bad!r} 未关闭 client")
            self.assertIsNone(provider._client)
            provider.close()

    def test_valid_login_id_accepted(self):
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-goodid"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        result = provider.login_start()
        self.assertEqual(result["state"], "login_started")
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        provider.login_cancel()
        provider.close()

    def test_completion_with_wrong_id_does_not_clear_pending(self):
        """L1 pending 时收到 L2 的 completion → 只记 tombstone，不清 L1。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-wrongid"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()  # pending = fake-login-id
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "OTHER-LOGIN", "success": True, "error": None},
            }
        )
        self.assertTrue(provider._login_pending)
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        # cancel 仍能取消当前 L1
        provider.login_cancel()
        self.assertEqual(client.login_cancel_calls, 1)
        provider.close()

    def test_cancel_old_start_new_late_old_completion(self):
        """取消 L1 → 启动 L2 → L1 迟到 completion 到达：不得清掉 L2。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-late"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()  # L1 pending
        provider.login_cancel()  # 取消 L1（fake-login-id）
        provider.login_start()  # L2 pending（fake 仍返回 fake-login-id）
        self.assertTrue(provider._login_pending)
        # L1 的迟到 completion（同一 id 但流程已重启）→ 不应清掉 L2
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
        # 注：fake 的 loginId 复用同一字符串，completion 命中当前 pending 是合理的
        # 清理；这里验证的是"不同 id 的旧 completion 不清新 pending"（上一用例），
        # 以及 cancel 后仍能再次发起（无残留 pending 块）。
        provider.login_cancel()
        self.assertFalse(provider._login_pending)
        provider.close()

    def test_completion_before_start_response_not_committed(self):
        """completion 先于 start 响应到达：_commit_pending 见 tombstone 不提交。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-outoforder"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # 模拟 start RPC 在途（pending 尚未提交）时 reader 先投递 completion
        with provider._lock:
            provider._login_start_inflight = "start-1"
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
        # 随后 start 提交 pending → tombstone 命中 → 不置 pending
        client2 = _D2FakeClient()
        provider._client = client2
        provider._commit_pending(client2, "fake-login-id")
        self.assertFalse(provider._login_pending)
        self.assertIsNone(provider._pending_login_id)
        # 状态不永久 login_started：后续 status 反映账户实际状态
        status = provider.status(refresh=True)
        self.assertNotEqual(status["state"], "login_started")
        provider.close()

    def test_completion_without_login_id_is_ignored(self):
        """无 loginId 的 completion 无法关联 → 忽略，不动当前 pending。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-nolid-msg"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()
        provider._on_notification(
            {"method": "account/login/completed", "params": {"success": True}}
        )
        self.assertTrue(provider._login_pending)
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        provider.login_cancel()
        provider.close()


# ── P0-5：纯字母数字 canary 的 planType / rate-limit 回显 ──────────────────


class TestCodexBareAlnumPlanTypeSafety(unittest.TestCase):
    """P0-5：无标点、纯 ASCII 字母数字 canary 也必须被 enum allowlist 拒绝。"""

    BARE_SECRETS = ("SUPERSECRET", "DEVACCOUNTSECRET", "PLANTYPECANARY", "gpt5secretkey")

    def test_mask_rate_limits_bare_alnum_rejected(self):
        for secret in self.BARE_SECRETS:
            masked = mask_rate_limits({"rateLimits": {"planType": secret, "primary": {}}})
            self.assertIsNone(masked["plan_type"], secret)
            self.assertNotIn(secret, str(masked))

    def test_account_plan_type_bare_alnum_rejected(self):
        from types import SimpleNamespace

        from openbrep.config import GDLAgentConfig
        from openbrep.workbench.settings_service import WorkbenchSettingsService

        for secret in self.BARE_SECRETS:
            client = _D2FakeClient(
                account={"type": "chatgpt", "email": "jo@example.com", "planType": secret}
            )
            provider = CodexProvider(
                codex_home=Path("/tmp/obr-codex-alnum"),
                client_factory=lambda: client,
                cli_available=True,
                browser_opener=lambda url: None,
            )
            status = provider.status(refresh=True)
            self.assertIsNone(status["account"]["plan_type"], secret)
            text = str(status)
            self.assertNotIn(secret, text, f"status 泄漏 {secret}")
            # service API 边界同样干净
            config = GDLAgentConfig()
            session = SimpleNamespace(
                llm_model="openai-codex/gpt-5.6-luna",
                llm_api_key="",
                llm_api_base="",
                assistant_settings="",
                max_retries=5,
                config=config,
                config_path=Path("/tmp/obr-codex-alnum-config.toml"),
            )
            service = WorkbenchSettingsService(
                session,
                llm_adapter_factory=lambda _c: None,
                codex_provider=provider,
            )
            resp_text = str(service.codex_status())
            self.assertNotIn(secret, resp_text, f"API 泄漏 {secret}")
            provider.close()

    def test_valid_enum_still_passes(self):
        for plan in ("free", "pro", "plus", "team", "unknown"):
            masked = mask_rate_limits({"rateLimits": {"planType": plan, "primary": {}}})
            self.assertEqual(masked["plan_type"], plan)


# ── P1：URL / device code 严格校验 ─────────────────────────────────────────


class TestCodexStrictUrlAndUserCodeValidation(unittest.TestCase):
    """P1：urlsplit 校验 scheme+hostname；user code 显式 ASCII 字符集。"""

    def test_auth_url_requires_real_hostname(self):
        p = CodexProvider.__new__(CodexProvider)
        # 有效：https 真实域名 / http://localhost（本机开发）
        self.assertTrue(p._validate_auth_url("https://auth.openai.com/oauth/authorize"))
        self.assertTrue(p._validate_auth_url("https://example.test/device?x=1"))
        self.assertTrue(p._validate_auth_url("http://localhost:1234/device"))
        self.assertTrue(p._validate_auth_url("http://127.0.0.1/device"))
        # 无效：query 当 host / 单标签 host / 非 http(s) / 超长 / 空
        self.assertFalse(p._validate_auth_url("https://?next=evil"))
        self.assertFalse(p._validate_auth_url("https://evil"))
        self.assertFalse(p._validate_auth_url("javascript:alert(1)"))
        self.assertFalse(p._validate_auth_url("file:///etc/passwd"))
        self.assertFalse(p._validate_auth_url("http://intranet-host/x"))  # 明文 HTTP 仅限本机
        self.assertFalse(p._validate_auth_url(""))
        self.assertFalse(p._validate_auth_url("https://a.b/" + "x" * 2100))
        self.assertFalse(p._validate_auth_url(123))

    def test_user_code_ascii_only(self):
        p = CodexProvider.__new__(CodexProvider)
        self.assertTrue(p._validate_user_code("ABCD-EFGH"))
        self.assertTrue(p._validate_user_code("abcd1234"))
        self.assertFalse(p._validate_user_code("汉字测试码"))
        self.assertFalse(p._validate_user_code("абвг"))
        self.assertFalse(p._validate_user_code("abc"))  # 太短
        self.assertFalse(p._validate_user_code("a" * 65))  # 超长
        self.assertFalse(p._validate_user_code("AB CD"))
        self.assertFalse(p._validate_user_code(1234))

    def test_bad_url_rolls_back_with_cancel(self):
        client = _D2FakeClient(
            login_result={
                "type": "chatgpt",
                "loginId": "ROLLBACK-URL",
                "authUrl": "https://?next=evil",
            }
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-badurl"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError):
            provider.login_start()
        self.assertEqual(client.login_cancel_calls, 1)
        provider.close()


# ── P0-1：原子 (client, generation) 快照（第三轮复验） ─────────────────────


class TestCodexAtomicSnapshot(unittest.TestCase):
    """P0-1：_snapshot 在锁内解析 client 并捕获 generation——restart/logout 无法
    插入「已拿旧 client、未读 generation」窗口；旧账户结果绝不写成新会话缓存。"""

    def test_restart_during_status_rpc_new_account_wins(self):
        """评审复现：旧账户（ol***）status RPC 阻塞 → restart 到新账户（ne***）
        → 释放；非 refresh 读取必须属于新账户，旧结果不得写缓存。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "oliver@example.com", "planType": "pro"},
            read_gate=gate,
        )
        new_client = _D2FakeClient(
            account={"type": "chatgpt", "email": "newton@example.com", "planType": "pro"}
        )
        provider, old_client, new_client = _race_provider(old_client, new_client)
        results: list = []

        def t1():
            try:
                results.append(provider.status(refresh=True))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.read_entered.wait(5))
        provider.restart()
        gate.set()
        thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        # 关键：缓存必须属于新账户
        final = provider.status(refresh=False)
        self.assertEqual(final["state"], "signed_in")
        self.assertEqual(final["account"]["email_masked"], "ne***@example.com")
        cached_email = (provider._status_cache or {}).get("account", {}).get("email_masked")
        self.assertNotEqual(cached_email, "ol***@example.com", "旧账户结果回写了 status 缓存")
        provider.close()

    def test_logout_during_status_rpc_old_account_never_cached(self):
        """logout 与 status RPC 并发：旧 signed-in 结果不得写缓存；
        随后非 refresh 读取 signed_out。"""
        gate = threading.Event()
        client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "oliver@example.com", "planType": "pro"},
            read_gate=gate,
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-snap-logout"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        results: list = []

        def t1():
            try:
                results.append(provider.status(refresh=True))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(client.read_entered.wait(5))
        provider.logout()
        gate.set()
        thread.join(timeout=5)
        self.assertEqual(provider.status(refresh=False)["state"], "signed_out")
        provider.close()

    def test_models_restart_new_account_never_cached(self):
        """评审复现：旧账户 model RPC 阻塞 → restart 到新账户 → 释放；
        非 refresh models 必须属于新账户目录，旧模型不得写缓存。"""
        gate = threading.Event()
        old_client = _RaceFakeClient(
            account={"type": "chatgpt", "email": "oliver@example.com", "planType": "pro"},
            model_gate=gate,
            models=[
                {"id": "old-account-model", "model": "old-account-model", "displayName": "Old"}
            ],
        )
        new_client = _D2FakeClient(
            account={"type": "chatgpt", "email": "newton@example.com", "planType": "pro"}
        )
        provider, old_client, new_client = _race_provider(old_client, new_client)
        results: list = []

        def t1():
            try:
                results.append(provider.models(refresh=True))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        thread = threading.Thread(target=t1)
        thread.start()
        self.assertTrue(old_client.model_entered.wait(5))
        provider.restart()
        gate.set()
        thread.join(timeout=5)
        # 新账户的 model_list（fake 默认 luna/terra），绝无 old-account-model
        models = provider.models(refresh=False)
        ids = [m["id"] for m in models]
        self.assertNotIn("openai-codex/old-account-model", ids)
        provider.close()

    def test_snapshot_holds_lock_across_client_creation(self):
        """原子性证明：_snapshot 持锁解析 client（factory 阻塞）期间，
        restart 无法完成——(client, gen) 只能成对出现，绝无中间态。"""
        factory_gate = threading.Event()

        def slow_factory():
            factory_gate.wait(5)
            return _D2FakeClient(account=None)

        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-snap-lock"),
            client_factory=slow_factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        results: list = []
        restart_results: list = []

        def t1():
            try:
                results.append(provider.status(refresh=True))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        def t2():
            try:
                restart_results.append(provider.restart())
            except Exception as exc:  # noqa: BLE001
                restart_results.append(exc)

        th1 = threading.Thread(target=t1)
        th1.start()
        import time

        time.sleep(0.3)  # _snapshot 已进入 _get_client（factory 阻塞，锁被持有）
        th2 = threading.Thread(target=t2)
        th2.start()
        time.sleep(0.3)
        # factory 仍阻塞 → restart 必须被锁挡住（尚未完成）
        self.assertEqual(restart_results, [], "restart 不应在 snapshot 持锁期间完成")
        factory_gate.set()
        th1.join(timeout=5)
        th2.join(timeout=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(restart_results), 1)
        provider.close()


# ── P0-3：completion tombstone 绑定 generation + 严格 bool success ────────


class TestCodexCompletionTombstoneSessionBinding(unittest.TestCase):
    """P0-3：tombstone 绑定会话 generation；重启后同 id 登录不受旧 tombstone
    影响；畸形 success（非 bool）不得静默清当前 pending。"""

    def test_completion_then_restart_then_same_id_login_commits(self):
        """完成 → restart → 新 client 重用同 loginId：新登录必须真实 pending（可轮询/取消）。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-tomb-restart"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # 旧会话：登录 + 完成
        provider.login_start()
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
        self.assertFalse(provider._login_pending)
        # restart 到新 client（新会话）
        provider.restart()
        self.assertFalse(provider._login_pending)
        # 新 client 再次返回同 loginId
        result = provider.login_start()
        self.assertEqual(result["state"], "login_started")
        # 关键：新登录必须真实 pending（可取消），而不是被旧 tombstone 吞掉
        self.assertTrue(provider._login_pending, "同 id 新登录被旧会话 tombstone 吞掉")
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        cancel = provider.login_cancel()
        self.assertEqual(cancel["state"], "signed_out")
        self.assertEqual(client.login_cancel_calls, 1)
        provider.close()

    def test_malformed_success_keeps_pending(self):
        """当前 id + 非 bool success 的 completion：忽略，保持 pending（可取消）。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-tomb-badbool"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()
        for bad in ("yes", 1, 0, None, {"ok": True}, [True]):
            provider._on_notification(
                {
                    "method": "account/login/completed",
                    "params": {"loginId": "fake-login-id", "success": bad},
                }
            )
            self.assertTrue(provider._login_pending, f"success={bad!r} 不应清 pending")
            self.assertEqual(provider._pending_login_id, "fake-login-id")
        # 仍可取消
        provider.login_cancel()
        self.assertEqual(client.login_cancel_calls, 1)
        self.assertFalse(provider._login_pending)
        provider.close()

    def test_tombstone_consumed_on_match(self):
        """tombstone 命中即消费：同会话同 id 再次 _commit_pending 不再受旧 tombstone 影响。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-tomb-consume"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # start RPC 在途时 completion 先于响应到达（乱序）→ 绑定 token 的 tombstone
        with provider._lock:
            provider._login_start_inflight = "start-1"
        provider._on_notification(
            {"method": "account/login/completed", "params": {"loginId": "X", "success": True}}
        )
        # 第一次 commit：tombstone 命中 → 不提交
        provider._client = _D2FakeClient()
        provider._commit_pending(provider._client, "X")
        self.assertFalse(provider._login_pending)
        # tombstone 已消费：第二次 commit（同 id）正常提交
        provider._commit_pending(provider._client, "X")
        self.assertTrue(provider._login_pending)
        self.assertEqual(provider._pending_login_id, "X")
        provider.login_cancel()
        provider.close()


# ── 第四轮 P0：同会话同 id 正常重试不受 tombstone 影响 ─────────────────────


class _SequentialLoginClient(_D2FakeClient):
    """每次 login/start 返回序列中的下一个 loginId（模拟 app-server 重用 id）。"""

    def __init__(self, ids, *, account=None):
        super().__init__(account=account)
        self._ids = iter(ids)

    def account_login_start(self, login_type):
        self.login_calls += 1
        return {
            "type": login_type if login_type in ("chatgpt", "chatgptDeviceCode") else "chatgpt",
            "loginId": next(self._ids),
            "authUrl": "https://auth.openai.com/oauth/authorize?state=fake",
        }


class TestCodexSameSessionRetry(unittest.TestCase):
    """第四轮 P0：同一 app-server 会话内（不 restart）app-server 重用 loginId 的
    正常重试，绝不被上一次 completion 留下的 tombstone 吞掉。"""

    def test_retry_after_failure_same_id_commits_and_cancels(self):
        """pending L1 completion failure → 不 restart → 再 start 返回 L1 →
        新流程必须 pending 且 cancel 发 RPC。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-retry-fail"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()  # pending = fake-login-id
        # completion failure（命中已提交的 pending）→ 完成状态机，不留 tombstone
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": False, "error": "cancelled"},
            }
        )
        self.assertFalse(provider._login_pending)
        self.assertEqual(provider._completed_login_ids, deque())  # 无 tombstone
        # 原地重试（不 restart）：app-server 重用同 id
        result = provider.login_start()
        self.assertEqual(result["state"], "login_started")
        self.assertTrue(provider._login_pending, "重试被上一次 completion 的 tombstone 吞掉")
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        cancel = provider.login_cancel()
        self.assertEqual(cancel["state"], "signed_out")
        self.assertEqual(client.login_cancel_calls, 1, "重试后的取消必须发 RPC")
        provider.close()

    def test_retry_after_success_same_id_commits(self):
        """pending L1 completion success（账户状态尚未同步）→ 原地重试同 id，
        不得被旧 tombstone 吞掉（新流程可轮询/取消）。"""
        client = _D2FakeClient(account=None)  # 账户未同步（仍 signed_out）
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-retry-succ"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
        self.assertFalse(provider._login_pending)
        self.assertEqual(provider._completed_login_ids, deque(), "正常完成不得留 tombstone")
        result = provider.login_start()  # 原地重试同 id
        self.assertEqual(result["state"], "login_started")
        self.assertTrue(provider._login_pending, "成功重试被 tombstone 吞掉")
        self.assertEqual(provider._pending_login_id, "fake-login-id")
        provider.login_cancel()
        self.assertEqual(client.login_cancel_calls, 1)
        provider.close()

    def test_stale_completion_without_start_inflight_ignored(self):
        """无 pending、无 start 在途的 stale completion → 忽略，不留 tombstone，
        后续同 id 登录不受影响。"""
        client = _D2FakeClient()
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-stale"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        # stale completion（例如旧流程的迟到帧）
        provider._on_notification(
            {
                "method": "account/login/completed",
                "params": {"loginId": "fake-login-id", "success": True, "error": None},
            }
        )
        self.assertEqual(
            provider._completed_login_ids, deque(), "stale completion 不得埋 tombstone"
        )
        # 后续同 id 登录正常进入 pending
        result = provider.login_start()
        self.assertEqual(result["state"], "login_started")
        self.assertTrue(provider._login_pending)
        provider.login_cancel()
        provider.close()

    def test_sequential_ids_cancel_old_start_new_late_old_completion(self):
        """取消 L1 → 启动 L2（不同 id）→ L1 迟到 completion：不得清掉 L2。"""
        client = _SequentialLoginClient(ids=["L1", "L2"])
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-seq"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        provider.login_start()   # pending = L1
        provider.login_cancel()  # 取消 L1
        provider.login_start()   # pending = L2
        self.assertEqual(provider._pending_login_id, "L2")
        # L1 的迟到 completion（不同 id）→ 忽略，不动 L2
        provider._on_notification(
            {"method": "account/login/completed", "params": {"loginId": "L1", "success": True}}
        )
        self.assertTrue(provider._login_pending)
        self.assertEqual(provider._pending_login_id, "L2")
        cancel = provider.login_cancel()
        self.assertEqual(cancel["state"], "signed_out")
        self.assertEqual(client.login_cancel_calls, 2)
        provider.close()


# ── 第五轮 P0：start RPC 失败（服务端可能已开始）→ 完整退役 + 关闭 client ──


class _FailingStartClient(_D2FakeClient):
    """login/start 先标记 server flow 已开始，再抛指定异常。"""

    def __init__(self, error, *, account=None):
        super().__init__(account=account)
        self._error = error
        self.server_flow_started = False

    def account_login_start(self, login_type):
        self.login_calls += 1
        self.server_flow_started = True
        raise self._error


class TestCodexStartRpcFailureCleanup(unittest.TestCase):
    """第五轮 P0：start RPC 在取得 loginId 前失败 → 退役 token/tombstone/pending、
    关闭 app-server，UI 与服务端流程不会失去对应关系。"""

    def test_rpc_failure_aborts_session_and_closes_client(self):
        """RPC 抛 timeout/EOF/rpc error：client closed、provider 无 client、
        token=None、pending=false、tombstone 空，原 category 保留。"""
        for exc in (
            CodexAppServerError("request timeout", category="timeout"),
            CodexAppServerError("process exited", category="process_exited"),
            CodexAppServerError("rpc failed", category="rpc_error"),
        ):
            client = _FailingStartClient(exc)
            provider = CodexProvider(
                codex_home=Path("/tmp/obr-codex-start-fail"),
                client_factory=lambda: client,
                cli_available=True,
                browser_opener=lambda url: None,
            )
            with self.assertRaises(CodexAppServerError) as ctx:
                provider.login_start()
            self.assertEqual(
                ctx.exception.category, exc.category, f"{exc.category} 的 category 未保留"
            )
            self.assertTrue(client.server_flow_started)
            self.assertTrue(client.closed, f"{exc.category}: app-server 未关闭")
            self.assertIsNone(provider._client, f"{exc.category}: provider 仍持有 client")
            self.assertIsNone(provider._login_start_inflight, f"{exc.category}: token 未退役")
            self.assertFalse(provider._login_pending)
            self.assertIsNone(provider._pending_login_id)
            self.assertEqual(provider._completed_login_ids, deque())
            provider.close()

    def test_rpc_failure_message_with_secret_never_logged(self):
        """异常原文（含 Bearer 秘密）不得进日志；只记稳定类别/类名。"""
        import logging

        records: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logger = logging.getLogger("openbrep.codex.provider")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        client = _FailingStartClient(
            CodexAppServerError("Authorization: Bearer START-RPC-SECRET", category="timeout")
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-start-secret"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        try:
            with self.assertRaises(CodexAppServerError) as ctx:
                provider.login_start()
            self.assertEqual(ctx.exception.category, "timeout")
        finally:
            provider.close()
            logger.removeHandler(handler)
        joined = "\n".join(records)
        self.assertNotIn("START-RPC-SECRET", joined, "start RPC 失败原文进日志")
        self.assertNotIn("Bearer", joined)

    def test_wrong_type_response_cleans_token_and_closes_client(self):
        """响应类型非法（如 apiKey）：_abort_start_session 完整清理。"""
        client = _D2FakeClient(login_result={"type": "apiKey", "loginId": "L"})
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-start-type"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError):
            provider.login_start()
        self.assertTrue(client.closed)
        self.assertIsNone(provider._client)
        self.assertIsNone(provider._login_start_inflight)
        self.assertFalse(provider._login_pending)
        self.assertEqual(provider._completed_login_ids, deque())
        provider.close()

    def test_missing_login_id_cleans_token_and_closes_client(self):
        """loginId 缺失：_abort_start_session 完整清理（比只断言 closed 更严）。"""
        client = _D2FakeClient(
            login_result={"type": "chatgpt", "authUrl": "https://auth.openai.com/x"}
        )
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-codex-start-nolid"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )
        with self.assertRaises(CodexAppServerError) as ctx:
            provider.login_start()
        self.assertEqual(ctx.exception.category, "login_failed")
        self.assertTrue(client.closed)
        self.assertIsNone(provider._client)
        self.assertIsNone(provider._login_start_inflight)
        self.assertFalse(provider._login_pending)
        self.assertEqual(provider._completed_login_ids, deque())
        provider.close()


# ── D3：provider.chat CHAT/EXPLAIN 安全调用 ────────────────────────────────


class TestCodexProviderChatWire(unittest.TestCase):
    """provider.chat 端到端（真实管道 + fake app-server）：fail closed 门禁、
    临时 cwd 清理、CHAT/EXPLAIN 安全语义。"""

    @contextlib.contextmanager
    def _provider(self, extra_env=None, rpc_timeout=5.0):
        import os
        import sys
        import tempfile

        from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport

        saved = {
            key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")
        }
        env = {"FAKE_CODEX_TURN": "1"}
        env.update(extra_env or {})
        for key, value in env.items():
            os.environ[key] = value
        home = Path(tempfile.mkdtemp(prefix="obr-d3-chat-")) / "home"

        def factory():
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

    @staticmethod
    def _turn_dirs_leftover() -> list[str]:
        import tempfile

        root = Path(tempfile.gettempdir())
        return [p.name for p in root.glob("openbrep-codex-turn-*")]

    def test_chat_signed_out_fails_closed(self):
        with self._provider() as provider:  # 未登录
            with self.assertRaises(CodexNotSignedInError):
                provider.chat(
                    [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                    model="gpt-5.6-luna",
                )

    def test_chat_quota_exhausted_fails_closed(self):
        with self._provider(
            {"FAKE_CODEX_SIGNED_IN": "1", "FAKE_CODEX_RATE_LIMITS": "reached"}
        ) as provider:
            with self.assertRaises(CodexAppServerError) as ctx:
                provider.chat(
                    [{"role": "user", "content": "hi"}],
                    model="gpt-5.6-luna",
                )
            self.assertEqual(ctx.exception.category, "quota_exhausted")

    def test_chat_no_cli_fails_closed(self):
        provider = CodexProvider(
            codex_home=Path("/tmp/obr-d3-no-cli"),
            cli_available=False,
        )
        with self.assertRaises(CodexCliUnavailableError):
            provider.chat([{"role": "user", "content": "hi"}], model="gpt-5.6-luna")

    def test_chat_completes_and_cleans_temp_cwd(self):
        before = set(self._turn_dirs_leftover())
        with self._provider({"FAKE_CODEX_SIGNED_IN": "1"}) as provider:
            result = provider.chat(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "你好"}],
                model="gpt-5.6-luna",
                timeout=10.0,
            )
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(result.content, "你好，我是 Codex 测试助手。")
        after = set(self._turn_dirs_leftover())
        self.assertEqual(before, after, "临时 turn cwd 必须用完即删")

    def test_chat_does_not_touch_workspace_dirs(self):
        """CHAT 的临时 cwd 与项目/工作区隔离：外部目录树逐字节不变。"""
        import tempfile

        workspace = Path(tempfile.mkdtemp(prefix="obr-d3-ws-"))
        (workspace / "keep.txt").write_text("payload", encoding="utf-8")
        (workspace / "sub").mkdir()
        (workspace / "sub" / "keep.gdl").write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

        def snapshot(root: Path) -> dict:
            out = {}
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    out[str(p.relative_to(root))] = p.read_bytes()
            return out

        before = snapshot(workspace)
        with self._provider({"FAKE_CODEX_SIGNED_IN": "1"}) as provider:
            result = provider.chat(
                [{"role": "user", "content": "你好"}],
                model="gpt-5.6-luna",
                timeout=10.0,
            )
            self.assertEqual(result.finish_reason, "stop")
        after = snapshot(workspace)
        self.assertEqual(before, after, "CHAT 不得在工作区创建/修改任何文件")

    def test_chat_error_canary_zero_echo(self):
        canary = "CANARY-CHAT-5c3d"
        with self._provider(
            {"FAKE_CODEX_SIGNED_IN": "1", "FAKE_CODEX_TURN_ERROR_CANARY": canary}
        ) as provider:
            result = provider.chat(
                [{"role": "user", "content": "hi"}],
                model="gpt-5.6-luna",
                timeout=10.0,
            )
            self.assertEqual(result.finish_reason, "error")
            self.assertNotIn(canary, str(result))


# ── D6：Fixed 模式 effort（model/list 目录 + 运行时门禁）──────────────────


class TestCodexModelEffortCatalog(unittest.TestCase):
    """D6：effort 选项只来自 model/list.supportedReasoningEfforts（不硬编码），
    逐项校验/去重/限长；默认值必须落在支持集合内。"""

    def _provider(self, client):
        return CodexProvider(
            codex_home=Path("/tmp/obr-d6-catalog"),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )

    def test_models_expose_sanitized_supported_efforts(self):
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            models=[
                {
                    "id": "gpt-5.6-luna",
                    "model": "gpt-5.6-luna",
                    "displayName": "GPT-5.6 Luna",
                    "hidden": False,
                    "modelSpecialty": None,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fastest"},
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep thinking"},
                    ],
                    "defaultReasoningEffort": "medium",
                },
                {
                    "id": "gpt-5.6-terra",
                    "model": "gpt-5.6-terra",
                    "displayName": "GPT-5.6 Terra",
                    "hidden": False,
                    "modelSpecialty": None,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                    "defaultReasoningEffort": "high",
                },
            ],
        )
        provider = self._provider(client)
        models = provider.models()
        luna = next(m for m in models if m["id"] == "openai-codex/gpt-5.6-luna")
        self.assertEqual(
            luna["supported_reasoning_efforts"],
            [
                {"effort": "low", "description": "Fastest"},
                {"effort": "medium", "description": "Balanced"},
                {"effort": "high", "description": "Deep thinking"},
            ],
        )
        self.assertEqual(luna["default_reasoning_effort"], "medium")
        terra = next(m for m in models if m["id"] == "openai-codex/gpt-5.6-terra")
        self.assertEqual(
            [e["effort"] for e in terra["supported_reasoning_efforts"]], ["medium", "high"]
        )
        self.assertEqual(terra["default_reasoning_effort"], "high")
        _assert_no_secrets(self, models, "models")

    def test_effort_catalog_drops_invalid_entries(self):
        """恶意/漂移上游：非法字符、超长、重复、默认不在集合内——全部清洗。"""
        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            models=[
                {
                    "id": "gpt-5.6-luna",
                    "model": "gpt-5.6-luna",
                    "displayName": "GPT-5.6 Luna",
                    "hidden": False,
                    "modelSpecialty": None,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "ok"},
                        {"reasoningEffort": "low", "description": "duplicate"},
                        {"reasoningEffort": "evil effort; rm -rf /", "description": "inject"},
                        {"reasoningEffort": "x" * 64, "description": "too long"},
                        {"reasoningEffort": "high", "description": "z" * 500},
                        "not-a-dict",
                        {"description": "no effort key"},
                    ],
                    "defaultReasoningEffort": "does-not-exist",
                }
            ],
        )
        provider = self._provider(client)
        models = provider.models()
        luna = next(m for m in models if m["id"] == "openai-codex/gpt-5.6-luna")
        self.assertEqual(
            luna["supported_reasoning_efforts"],
            [
                {"effort": "low", "description": "ok"},
                {"effort": "high", "description": "z" * 120},
            ],
        )
        # 默认不在支持集合 → 置空（绝不把非法默认值透传）
        self.assertEqual(luna["default_reasoning_effort"], "")

    def test_validate_reasoning_effort_rejects_unsupported(self):
        from openbrep.codex.provider import CodexUnsupportedEffortError

        client = _FakeCodexClient(
            account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
            models=[
                {
                    "id": "gpt-5.6-terra",
                    "model": "gpt-5.6-terra",
                    "displayName": "GPT-5.6 Terra",
                    "hidden": False,
                    "modelSpecialty": None,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                    "defaultReasoningEffort": "high",
                }
            ],
        )
        provider = self._provider(client)
        # 支持 → 通过
        provider.validate_reasoning_effort("openai-codex/gpt-5.6-terra", "medium")
        provider.validate_reasoning_effort("openai-codex/gpt-5.6-terra", "high")
        provider.validate_reasoning_effort("openai-codex/gpt-5.6-terra", "")  # 空 = 不覆盖
        # 不支持（含 luna 独有 low）→ fail closed
        with self.assertRaises(CodexUnsupportedEffortError):
            provider.validate_reasoning_effort("openai-codex/gpt-5.6-terra", "low")
        # 非法格式 → fail closed
        with self.assertRaises(CodexUnsupportedEffortError):
            provider.validate_reasoning_effort("openai-codex/gpt-5.6-terra", "high; rm -rf")


class TestCodexProviderChatEffort(unittest.TestCase):
    """D6：provider.chat 运行时刻 effort 门禁——不支持即拒绝，不启动 turn。"""

    @contextlib.contextmanager
    def _provider(self, extra_env=None, rpc_timeout=5.0):
        import os
        import sys
        import tempfile

        from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport

        saved = {
            key: os.environ.pop(key) for key in list(os.environ) if key.startswith("FAKE_CODEX_")
        }
        env = {"FAKE_CODEX_TURN": "1", "FAKE_CODEX_SIGNED_IN": "1"}
        env.update(extra_env or {})
        for key, value in env.items():
            os.environ[key] = value
        home = Path(tempfile.mkdtemp(prefix="obr-d6-pchat-")) / "home"

        def factory():
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

    def test_chat_supported_effort_forwarded_and_recorded(self):
        params_log = Path("/tmp") / f"obr-d6-pchat-{threading.get_ident()}.jsonl"
        params_log.unlink(missing_ok=True)
        with self._provider(
            {"FAKE_CODEX_TURN_PARAMS_LOG": str(params_log)}
        ) as provider:
            result = provider.chat(
                [{"role": "user", "content": "你好"}],
                model="openai-codex/gpt-5.6-luna",
                timeout=10.0,
                reasoning_effort="high",
            )
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(result.reasoning_effort, "high")
        lines = [ln for ln in params_log.read_text(encoding="utf-8").strip().splitlines() if ln]
        turn_entries = [json.loads(ln) for ln in lines if '"turn/start"' in ln]
        self.assertTrue(turn_entries)
        self.assertEqual(turn_entries[0]["params"].get("effort"), "high")

    def test_chat_unsupported_effort_fails_closed_without_turn(self):
        from openbrep.codex.provider import CodexUnsupportedEffortError

        params_log = Path("/tmp") / f"obr-d6-pchat-reject-{threading.get_ident()}.jsonl"
        params_log.unlink(missing_ok=True)
        with self._provider(
            # terra 只支持 medium/high；low 是 luna 独有（残留场景）
            {"FAKE_CODEX_TURN_PARAMS_LOG": str(params_log)}
        ) as provider:
            with self.assertRaises(CodexUnsupportedEffortError):
                provider.chat(
                    [{"role": "user", "content": "你好"}],
                    model="openai-codex/gpt-5.6-terra",
                    timeout=10.0,
                    reasoning_effort="low",
                )
        # 失败路径零 turn 请求（models() 目录查询不产生 turn/start）。
        # fake server 只在收到 thread/start / turn/start 时才写 log——
        # log 不存在或没有任何 turn 条目都证明零 turn 请求。
        if params_log.exists():
            lines = [ln for ln in params_log.read_text(encoding="utf-8").strip().splitlines() if ln]
            self.assertFalse(
                [ln for ln in lines if '"turn/start"' in ln],
                "不支持的 effort 绝不能发出 turn/start",
            )
            self.assertFalse(
                [ln for ln in lines if '"thread/start"' in ln],
                "不支持的 effort 绝不能发出 thread/start",
            )
