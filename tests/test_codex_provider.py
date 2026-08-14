"""CodexProvider 状态机测试（D1）：全部使用 fake client，不碰真实账号/网络。

覆盖：no_cli / signed_out / signed_in / error 四态、登录只打开浏览器且
authUrl 不外传、模型目录 provider-qualified、退出后 fail closed、
任何响应不泄露 token/JWT/account id/auth path。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from openbrep.codex.app_server import CodexAppServerError, CodexCliUnavailableError
from openbrep.codex.provider import CodexNotSignedInError, CodexProvider, mask_email

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
        self.assertEqual(result, {"state": "login_started"})
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
