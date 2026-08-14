"""Codex BYOA 服务层：账户状态机、动态模型目录与登录/退出。

安全边界（D1）：
- 只暴露枚举化状态（no_cli / signed_out / signed_in / error / login_started）、
  脱敏邮箱与 plan_type；token / JWT / loginId / authUrl / auth 文件路径
  一律不离开本模块。
- 登录（account/login/start chatgpt）由 provider 直接打开终端用户浏览器，
  authUrl 不返回调用方。
- 未登录时任何模型能力 fail closed；绝不 fallback 到 API-key / 环境变量。
"""

from __future__ import annotations

import atexit
import logging
import shutil
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from openbrep.config import CODEX_PROVIDER_NAME
from openbrep.codex.app_server import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexCliUnavailableError,
    default_codex_home,
)

_LOGGER = logging.getLogger(__name__)

# 状态缓存有效期：status() 在此窗口内直接复用上次结果，避免每次轮询都
# 打 app-server；登录中轮询用 refresh=True 强制刷新。
_STATUS_TTL_SECONDS = 5.0
# 模型目录缓存有效期：llm_settings 每次 snapshot 都会查可用性，
# 目录缓存避免高频 model/list 调用；登录/保存等关键路径用 refresh=True。
_MODELS_TTL_SECONDS = 10.0


class CodexNotSignedInError(RuntimeError):
    """未登录 ChatGPT——动态模型目录不可读（fail closed）。"""

    code = "not_signed_in"


def mask_email(email: str) -> str:
    """邮箱脱敏：local 只留前 1-2 个字符，域名保留（用户确认连的是自己的账号）。"""
    text = str(email or "").strip()
    if not text:
        return ""
    if "@" not in text:
        return text[:1] + "***" if text else ""
    local, _, domain = text.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


class CodexProvider:
    """app-server 生命周期 + 账户/模型能力的高层封装（单进程内单例使用）。"""

    def __init__(
        self,
        *,
        codex_home: str | Path | None = None,
        codex_binary: str = "codex",
        client_factory: Callable[[], Any] | None = None,
        browser_opener: Callable[[str], Any] | None = None,
        cli_available: bool | None = None,
        status_ttl: float = _STATUS_TTL_SECONDS,
        models_ttl: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.codex_home = Path(codex_home) if codex_home is not None else default_codex_home()
        self.codex_binary = codex_binary
        self._client_factory = client_factory
        self._browser_opener = browser_opener or webbrowser.open
        # None = 自动探测（shutil.which）；测试可显式指定
        self._cli_available = cli_available
        self.status_ttl = status_ttl
        self._logger = logger or _LOGGER
        self._client: Any | None = None
        self._lock = threading.RLock()
        self._status_cache: dict[str, Any] | None = None
        self._status_ts = 0.0
        self._models_cache: list[dict[str, Any]] | None = None
        self._models_ts = 0.0
        self.models_ttl = models_ttl if models_ttl is not None else _MODELS_TTL_SECONDS
        self._closed = False
        atexit.register(self.close)

    # ── CLI 探测 ─────────────────────────────────────────────

    @property
    def cli_available(self) -> bool:
        if self._cli_available is not None:
            return self._cli_available
        return shutil.which(self.codex_binary) is not None

    # ── 内部：客户端生命周期 ─────────────────────────────────

    def _get_client(self) -> Any:
        with self._lock:
            if self._closed:
                raise CodexAppServerError("Codex app-server 已关闭。", category="closed")
            if self._client is None:
                if not self.cli_available:
                    raise CodexCliUnavailableError(
                        f"未检测到 Codex CLI（{self.codex_binary}）。"
                        "请先安装 Codex CLI 后重试。"
                    )
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    self._client = CodexAppServerClient(
                        codex_binary=self.codex_binary,
                        codex_home=self.codex_home,
                    )
                self._client.start()
            return self._client

    def _invalidate_status(self) -> None:
        with self._lock:
            self._status_cache = None
            self._status_ts = 0.0
            self._models_cache = None
            self._models_ts = 0.0

    # ── 账户状态 ─────────────────────────────────────────────

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        """枚举化登录状态：no_cli | signed_out | signed_in | error。

        永不返回 token / JWT / account id / auth 路径；signed_in 只含
        脱敏邮箱与 plan_type。
        """
        now = time.monotonic()
        with self._lock:
            if (
                self._status_cache is not None
                and not refresh
                and now - self._status_ts < self.status_ttl
            ):
                return dict(self._status_cache)

        if not self.cli_available:
            result: dict[str, Any] = {
                "state": "no_cli",
                "connected": False,
                "codex_available": False,
                "account": None,
            }
        else:
            try:
                client = self._get_client()
                result = self._read_account(client)
                result["codex_available"] = True
            except (CodexCliUnavailableError, CodexAppServerError, OSError) as exc:
                result = {
                    "state": "error",
                    "connected": False,
                    "codex_available": self.cli_available,
                    "account": None,
                    "error": str(exc),
                }
        with self._lock:
            self._status_cache = result
            self._status_ts = time.monotonic()
        return dict(result)

    def _read_account(self, client: Any) -> dict[str, Any]:
        raw = client.account_read()
        account = raw.get("account") or None
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            # apiKey/bedrock 等账号不是 ChatGPT 订阅 BYOA 流（fail closed）
            return {"state": "signed_out", "connected": False, "account": None}
        email = account.get("email")
        return {
            "state": "signed_in",
            "connected": True,
            "account": {
                "email_masked": mask_email(email) if email else None,
                "plan_type": str(account.get("planType") or "unknown"),
            },
        }

    # ── 登录 / 退出 ──────────────────────────────────────────

    def login_start(self) -> dict[str, Any]:
        """启动浏览器 OAuth（chatgpt）：浏览器由本进程打开，authUrl 不外传。

        返回 {state: "login_started"}；调用方通过 status(refresh=True) 轮询
        登录结果（account/login/completed 通知由 app-server 内部处理）。
        """
        if not self.cli_available:
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            )
        client = self._get_client()
        result = client.account_login_start_chatgpt()
        if not isinstance(result, dict) or result.get("type") != "chatgpt":
            # P0-2：绝不把原始登录响应（可能含 authUrl/loginId）拼进错误文本
            raise CodexAppServerError(
                "登录服务未返回 chatgpt 浏览器流程，无法继续登录。"
            )
        auth_url = str(result.get("authUrl") or "").strip()
        if not auth_url:
            raise CodexAppServerError("登录服务未返回浏览器地址，无法继续登录。")
        self._browser_opener(auth_url)
        self._invalidate_status()
        return {"state": "login_started"}

    def logout(self) -> dict[str, Any]:
        """退出登录；未连接时也是幂等的 signed_out。"""
        if self.cli_available:
            client = self._get_client()
            client.account_logout()
        self._invalidate_status()
        return {"state": "signed_out"}

    # ── 动态模型目录 ─────────────────────────────────────────

    def models(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """model/list 动态目录，id 统一加 provider-qualified 前缀。

        只允许已登录读取；未登录 / 无 CLI 一律报错（fail closed，不 fallback）。
        结果按 models_ttl 缓存：llm_settings 的可用性检查与登录轮询共享目录，
        不重复打 app-server。
        """
        now = time.monotonic()
        with self._lock:
            if (
                self._models_cache is not None
                and not refresh
                and now - self._models_ts < self.models_ttl
            ):
                return [dict(m) for m in self._models_cache]

        if not self.cli_available:
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            )
        client = self._get_client()
        status = self.status(refresh=refresh)
        if not status.get("connected"):
            raise CodexNotSignedInError(
                "尚未连接 ChatGPT。请先在 AI 设置中点击「连接我的 ChatGPT」完成登录。"
            )
        raw = client.model_list()
        models: list[dict[str, Any]] = []
        for entry in raw.get("data") or []:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("id") or entry.get("model") or "").strip()
            if not model_id:
                continue
            models.append(
                {
                    "id": f"{CODEX_PROVIDER_NAME}/{model_id}",
                    "label": str(entry.get("displayName") or model_id),
                    "model": model_id,
                    "display_name": str(entry.get("displayName") or ""),
                    "hidden": bool(entry.get("hidden")),
                    "specialty": entry.get("modelSpecialty"),
                }
            )
        with self._lock:
            self._models_cache = [dict(m) for m in models]
            self._models_ts = time.monotonic()
        return models

    # ── 关闭 ─────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 —— 关闭路径不掩盖其他错误
                self._logger.warning("codex app-server 关闭失败", exc_info=True)
