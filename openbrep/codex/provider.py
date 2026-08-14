"""Codex BYOA 服务层：账户生命周期、动态模型目录、登录/退出与额度/崩溃恢复。

安全边界（D1 + D2）：
- 只暴露枚举化状态（no_cli / version_incompatible / signed_out / signed_in /
  login_started / quota_exhausted / crashed / error）、脱敏邮箱、plan_type
  与脱敏额度摘要；token / JWT / loginId / authUrl / auth 文件路径一律不离开本模块。
- 登录类型白名单：只允许 `chatgpt`（浏览器 OAuth）与 `chatgptDeviceCode`
  （设备码，用户显式选择）；`apiKey` / `chatgptAuthTokens` 等一律拒绝
  （fail closed，绝不把实验凭据形态放进订阅 provider）。
- 登录（account/login/start chatgpt）由 provider 直接打开终端用户浏览器，
  authUrl 不返回调用方；device-code 的 verificationUrl + userCode 是用户
  完成授权所必需的产品信息，显式返回给 UI 展示。
- 未登录时任何模型能力 fail closed；绝不 fallback 到 API-key / 环境变量。
- app-server 崩溃：status() 显式报告 crashed；登录/模型/额度等操作在下次
  调用时自愈重建传输；restart() 提供显式重启。
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
from openbrep.codex.errors import error_response

_LOGGER = logging.getLogger(__name__)

# 状态缓存有效期：status() 在此窗口内直接复用上次结果，避免每次轮询都
# 打 app-server；登录中轮询用 refresh=True 强制刷新。
_STATUS_TTL_SECONDS = 5.0
# 模型目录缓存有效期：llm_settings 每次 snapshot 都会查可用性，
# 目录缓存避免高频 model/list 调用；登录/保存等关键路径用 refresh=True。
_MODELS_TTL_SECONDS = 10.0
# 额度缓存有效期：quota 状态不必每次轮询都打 account/rateLimits/read。
_RATE_LIMITS_TTL_SECONDS = 30.0

# 登录类型白名单（D2）：只允许 ChatGPT 订阅的两种官方登录形态。
# `apiKey`（按量计费身份）与实验 `chatgptAuthTokens`（客户端自管 token）
# 一律拒绝进入 subscription provider。
ALLOWED_LOGIN_TYPES = frozenset({"chatgpt", "chatgptDeviceCode"})

# 最小 Codex CLI 版本：app-server 协议面（account/login/cancel、
# account/rateLimits/read、通知）以 0.147.0 实测验证；低于此版本
# version_incompatible，fail closed。version 解析自 initialize.userAgent。
MIN_CODEX_VERSION = (0, 147, 0)


class CodexNotSignedInError(RuntimeError):
    """未登录 ChatGPT——动态模型目录不可读（fail closed）。"""

    code = "not_signed_in"


class CodexVersionIncompatibleError(RuntimeError):
    """Codex CLI 版本低于要求/无法识别——协议面未验证，fail closed。"""

    code = "version_incompatible"


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


def mask_rate_limits(raw: dict[str, Any]) -> dict[str, Any]:
    """account/rateLimits/read 的脱敏摘要——只暴露产品需要的字段。

    保留：是否触顶、触顶类型、plan、usedPercent、窗口/重置时间、has_credits/
    unlimited。绝不暴露：余额字符串、limit/used 字符串、reset credit id、
    individualLimit、账号原始字段。
    """
    rl = raw.get("rateLimits") or {}
    primary = rl.get("primary") or {}
    credits = rl.get("credits") or {}
    reached_type = rl.get("rateLimitReachedType")
    spend_reached = rl.get("spendControlReached")
    return {
        "reached": bool(reached_type) or bool(spend_reached),
        "reached_type": reached_type or None,
        "spend_control_reached": spend_reached,
        "plan_type": rl.get("planType"),
        "used_percent": primary.get("usedPercent"),
        "window_duration_mins": primary.get("windowDurationMins"),
        "resets_at": primary.get("resetsAt"),
        "credits": {
            "has_credits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
        },
    }


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
        rate_limits_ttl: float | None = None,
        min_codex_version: tuple[int, int, int] = MIN_CODEX_VERSION,
        logger: logging.Logger | None = None,
    ) -> None:
        self.codex_home = Path(codex_home) if codex_home is not None else default_codex_home()
        self.codex_binary = codex_binary
        self._client_factory = client_factory
        self._browser_opener = browser_opener or webbrowser.open
        # None = 自动探测（shutil.which）；测试可显式指定
        self._cli_available = cli_available
        self.status_ttl = status_ttl
        self.min_codex_version = min_codex_version
        self._logger = logger or _LOGGER
        self._client: Any | None = None
        self._lock = threading.RLock()
        self._status_cache: dict[str, Any] | None = None
        self._status_ts = 0.0
        self._models_cache: list[dict[str, Any]] | None = None
        self._models_ts = 0.0
        self.models_ttl = models_ttl if models_ttl is not None else _MODELS_TTL_SECONDS
        self.rate_limits_ttl = rate_limits_ttl if rate_limits_ttl is not None else _RATE_LIMITS_TTL_SECONDS
        self._rate_limits_cache: dict[str, Any] | None = None
        self._rate_limits_ts = 0.0
        self._pending_login_id: str | None = None
        # 登录进行中：login_start/device-code 后置 True，登录完成/取消/退出/重启清 False
        self._login_pending = False
        self._closed = False
        atexit.register(self.close)

    # ── CLI 探测 ─────────────────────────────────────────────

    @property
    def cli_available(self) -> bool:
        if self._cli_available is not None:
            return self._cli_available
        return shutil.which(self.codex_binary) is not None

    # ── 内部：客户端生命周期 ─────────────────────────────────

    def _check_version(self, client: Any) -> None:
        """版本/schema 协商：真实客户端 initialize 后校验最小版本。

        fake client（无 server_version 属性）跳过校验（测试注入）。
        """
        if not hasattr(client, "server_version"):
            return
        version = client.server_version
        if version is None:
            raise CodexVersionIncompatibleError(
                "无法识别 Codex CLI 版本（initialize.userAgent 缺失版本号），"
                "无法保证协议兼容，请升级 Codex CLI 后重试。"
            )
        if version < self.min_codex_version:
            raise CodexVersionIncompatibleError(
                f"Codex CLI 版本过低（{'.'.join(map(str, version))}，"
                f"需要 ≥{'.'.join(map(str, self.min_codex_version))}）。"
                "请升级 Codex CLI 后重试。"
            )

    def _get_client(self, *, heal: bool = True) -> Any:
        with self._lock:
            if self._closed:
                raise CodexAppServerError("Codex app-server 已关闭。", category="closed")
            client = self._client
            if client is not None:
                transport = getattr(client, "transport", None)
                if transport is not None and getattr(transport, "crashed", False):
                    if not heal:
                        # status() 不重建：显式报告 crashed（UI 可点「重启」）
                        raise CodexAppServerError(
                            "codex app-server 进程已退出，请调用 restart() 恢复。",
                            category="process_exited",
                        )
                    # D2 自愈：崩溃后下次操作重建传输（操作路径直接恢复；
                    # status() 单独报告 crashed，由 UI 决定是否显式 restart）。
                    try:
                        client.close()
                    except Exception as exc:  # noqa: BLE001
                        self._logger.warning(
                            "codex app-server 崩溃后关闭旧进程失败（%s）",
                            exc.__class__.__name__,
                        )
                    self._client = None
                    self._pending_login_id = None
                    self._login_pending = False
                    client = None
            if client is None:
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
                try:
                    self._check_version(self._client)
                except Exception:
                    # 版本不兼容：不缓存该客户端，后续调用继续 fail closed
                    failed = self._client
                    self._client = None
                    try:
                        failed.close()
                    except Exception:  # noqa: BLE001
                        pass
                    raise
                transport = getattr(self._client, "transport", None)
                if transport is not None and hasattr(transport, "subscribe"):
                    transport.subscribe(self._on_notification)
                client = self._client
            # 刚创建/刚取到的客户端已崩溃（如启动即退出）：统一按 crashed 处理，
            # 避免 heal 死循环（创建后立即崩溃 → 显式报错，由外层决定）。
            transport = getattr(client, "transport", None)
            if transport is not None and getattr(transport, "crashed", False):
                if not heal:
                    raise CodexAppServerError(
                        "codex app-server 进程已退出，请调用 restart() 恢复。",
                        category="process_exited",
                    )
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    if self._client is client:
                        self._client = None
                raise CodexAppServerError(
                    "codex app-server 进程已退出，请调用 restart() 恢复。",
                    category="process_exited",
                )
            return client

    def _on_notification(self, msg: dict) -> None:
        """reader 线程投递的通知 → 缓存失效（额度/账户/登录完成）。"""
        method = str(msg.get("method") or "")
        if method == "account/rateLimits/updated":
            with self._lock:
                self._rate_limits_cache = None
                self._rate_limits_ts = 0.0
        elif method in ("account/updated", "account/login/completed"):
            with self._lock:
                self._status_cache = None
                self._status_ts = 0.0
                self._models_cache = None
                self._models_ts = 0.0

    def _invalidate_status(self) -> None:
        with self._lock:
            self._status_cache = None
            self._status_ts = 0.0
            self._models_cache = None
            self._models_ts = 0.0
            self._rate_limits_cache = None
            self._rate_limits_ts = 0.0

    # ── 账户状态 ─────────────────────────────────────────────

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        """枚举化登录状态。

        状态全集：no_cli | version_incompatible | signed_out | signed_in |
        login_started（登录进行中，未完成前不返回账户）| quota_exhausted |
        crashed | error。永不返回 token / JWT / account id / auth 路径；
        signed_in 只含脱敏邮箱、plan_type 与脱敏额度摘要。
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
                # status() 不重建崩溃的 app-server：显式报告 crashed，让 UI
                # 提供「重启」动作；登录/模型/额度等操作路径自愈。
                client = self._get_client(heal=False)
                result = self._read_account(client)
                result["codex_available"] = True
                if not result.get("connected") and self._login_pending:
                    # 登录进行中：未完成前不返回账户，状态为 login_started
                    result = {
                        "state": "login_started",
                        "connected": False,
                        "codex_available": True,
                        "account": None,
                    }
                if result.get("connected"):
                    limits = self._read_rate_limits_cached(client, refresh=refresh)
                    if limits is not None:
                        result["rate_limits"] = limits
                        if limits.get("reached"):
                            result = dict(result)
                            result["state"] = "quota_exhausted"
                            result["error"] = (
                                "ChatGPT 订阅额度已耗尽或已达到用量上限。"
                                "请稍后重试、等待重置，或切换到其他模型/提供商。"
                            )
            except CodexAppServerError as exc:
                if getattr(exc, "category", None) == "process_exited":
                    # 崩溃 → 显式 crashed 状态（可 restart）
                    result = {
                        "state": "crashed",
                        "connected": False,
                        "codex_available": True,
                        "account": None,
                        "restartable": True,
                        "code": "codex_crashed",
                        "error": "Codex app-server 进程异常退出。请点击「重启」恢复连接。",
                    }
                else:
                    # 其他 CodexAppServerError 走下方稳定文案映射（不重抛，
                    # 保证 status() 永远返回枚举化状态而非异常）
                    result = None
                    pending_exc = exc
            except CodexVersionIncompatibleError:
                result = {
                    "state": "version_incompatible",
                    "connected": False,
                    "codex_available": True,
                    "account": None,
                    "code": "version_incompatible",
                    "error": "Codex CLI 版本与 OpenBrep 不兼容，请升级 Codex CLI 后重试。",
                }
            except (CodexCliUnavailableError, OSError) as exc:
                pending_exc = exc
                result = None
            if result is None:
                # P0-R1A：error 状态只携带稳定 code + 稳定产品文案，
                # 绝不在返回值/缓存里保存原始 str(exc)（可能含秘密）。
                stable = error_response(pending_exc)
                result = {
                    "state": "error",
                    "connected": False,
                    "codex_available": self.cli_available,
                    "account": None,
                    "code": stable["code"],
                    "error": stable["error"],
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

    # ── 额度 ─────────────────────────────────────────────────

    def _read_rate_limits_cached(self, client: Any, *, refresh: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            if (
                self._rate_limits_cache is not None
                and not refresh
                and now - self._rate_limits_ts < self.rate_limits_ttl
            ):
                return dict(self._rate_limits_cache)
        try:
            raw = client.account_rate_limits_read()
        except Exception:  # noqa: BLE001 —— 额度是辅助信息，读不到不拖垮状态
            return None
        masked = mask_rate_limits(raw)
        with self._lock:
            self._rate_limits_cache = masked
            self._rate_limits_ts = time.monotonic()
        return dict(masked)

    def rate_limits(self, *, refresh: bool = False) -> dict[str, Any]:
        """account/rateLimits/read 的脱敏摘要；未登录 fail closed。"""
        if not self.cli_available:
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            )
        client = self._get_client()
        status = self.status(refresh=refresh)
        if not status.get("connected"):
            raise CodexNotSignedInError(
                "尚未连接 ChatGPT，无法读取订阅额度。请先登录。"
            )
        limits = self._read_rate_limits_cached(client, refresh=True)
        if limits is None:
            raise CodexAppServerError(
                "无法读取订阅额度（app-server 未返回额度数据）。",
                category="rpc_error",
            )
        return limits

    # ── 登录 / 退出 / 取消 / 重启 ────────────────────────────

    def _login_start_type(self, login_type: str) -> dict[str, Any]:
        if not self.cli_available:
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            )
        if login_type not in ALLOWED_LOGIN_TYPES:
            # D2：白名单拒绝——apiKey / chatgptAuthTokens 等一律不允许
            raise CodexAppServerError(
                f"登录类型 {login_type} 不被允许：ChatGPT Codex 只支持 "
                "chatgpt（浏览器）或 chatgptDeviceCode（设备码）。",
                category="login_failed",
            )
        client = self._get_client()
        if login_type == "chatgpt" and not hasattr(client, "account_login_start"):
            # D1 兼容：旧 fake/客户端只暴露 account_login_start_chatgpt()
            result = client.account_login_start_chatgpt()
        else:
            result = client.account_login_start(login_type)
        if result.get("type") not in ALLOWED_LOGIN_TYPES or result.get("type") != login_type:
            # P0-2：绝不把原始登录响应（可能含 authUrl/loginId）拼进错误文本
            raise CodexAppServerError(
                "登录服务未返回所请求的 ChatGPT 浏览器/设备码流程，无法继续登录。"
            )
        self._pending_login_id = str(result.get("loginId") or "")
        return result

    def login_start(self) -> dict[str, Any]:
        """启动浏览器 OAuth（chatgpt）：浏览器由本进程打开，authUrl 不外传。

        返回 {state: "login_started", method: "chatgpt"}；调用方通过
        status(refresh=True) 轮询登录结果（account/login/completed 通知
        由 app-server 内部处理并触发本 provider 缓存失效）。
        """
        result = self._login_start_type("chatgpt")
        auth_url = str(result.get("authUrl") or "").strip()
        if not auth_url:
            raise CodexAppServerError("登录服务未返回浏览器地址，无法继续登录。")
        self._browser_opener(auth_url)
        with self._lock:
            self._login_pending = True
        self._invalidate_status()
        return {"state": "login_started", "method": "chatgpt"}

    def login_start_device_code(self) -> dict[str, Any]:
        """设备码登录（用户显式选择）：返回 verification_url + user_code 供 UI 展示。

        这是完成授权所必需的产品信息（用户需在浏览器输入该码），
        不是 token/JWT/请求头；loginId 只保存在 provider 内部用于取消。
        """
        result = self._login_start_type("chatgptDeviceCode")
        verification_url = str(result.get("verificationUrl") or "").strip()
        user_code = str(result.get("userCode") or "").strip()
        if not verification_url or not user_code:
            raise CodexAppServerError("登录服务未返回设备码信息，无法继续登录。")
        with self._lock:
            self._login_pending = True
        self._invalidate_status()
        return {
            "state": "login_started",
            "method": "chatgptDeviceCode",
            "verification_url": verification_url,
            "user_code": user_code,
        }

    def login_cancel(self) -> dict[str, Any]:
        """取消进行中的登录。取消 RPC 失败时关闭 app-server 强制终止登录会话。"""
        with self._lock:
            login_id, self._pending_login_id = self._pending_login_id, None
            self._login_pending = False
            client = self._client
        if client is not None and login_id:
            try:
                client.account_login_cancel(login_id)
            except Exception as exc:  # noqa: BLE001 —— 取消失败仍要退出登录态
                self._logger.warning(
                    "codex 登录取消 RPC 失败（%s），关闭 app-server 终止登录会话",
                    exc.__class__.__name__,
                )
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    self._client = None
        self._invalidate_status()
        return {"state": "signed_out"}

    def logout(self) -> dict[str, Any]:
        """退出登录；未连接时也是幂等的 signed_out。"""
        if self.cli_available:
            client = self._get_client()
            client.account_logout()
        with self._lock:
            self._pending_login_id = None
            self._login_pending = False
        self._invalidate_status()
        return {"state": "signed_out"}

    def restart(self) -> dict[str, Any]:
        """显式重启 app-server（崩溃恢复）：关闭旧进程 → 重建 → 返回最新状态。"""
        with self._lock:
            client, self._client = self._client, None
            self._pending_login_id = None
            self._login_pending = False
            self._invalidate_status()
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "codex app-server 重启时关闭旧进程失败（%s）",
                    exc.__class__.__name__,
                )
        return self.status(refresh=True)

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
            except Exception as exc:  # noqa: BLE001 —— 关闭路径不掩盖其他错误
                # P0-R1B：不打印 traceback（异常原文可能含秘密）；
                # 只记稳定 category/异常类名。
                category = getattr(exc, "category", None)
                self._logger.warning(
                    "codex app-server 关闭失败（category=%s）",
                    category or exc.__class__.__name__,
                )
