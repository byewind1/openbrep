"""官方 Codex app-server 的 stdio JSON-RPC 客户端（D2：可长期运行的账户连接）。

协议（实测 Codex CLI 0.147.0 `codex app-server`，默认 transport stdio://）：
- 帧格式：换行分隔 JSON（JSON Lines），不是 LSP 的 Content-Length 头。
- 请求：{"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
- 响应按 id 关联（可乱序）；无 id 的消息是通知（如 account/login/completed、
  account/rateLimits/updated、error）。
- 版本协商：initialize 返回 userAgent（首 token 为 "name/version"，version
  即 Codex CLI 版本），由上层 provider 做最小版本/能力校验。
- 关闭：关闭 stdin（EOF）即优雅退出（exit 0）；close() 兜底 terminate/kill，
  POSIX 下按进程组清理，保证应用退出无遗留 app-server 子进程。

本模块只做「子进程生命周期 + JSON-RPC 搬运 + 通知分发 + 崩溃检测」，
不包含任何业务语义；账户/模型能力、登录状态机与脱敏在 provider.py。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

# initialize 的 clientInfo 标识（app-server 用来拼 userAgent，非秘密）
_OPENBREP_CLIENT_NAME = "openbrep"
_OPENBREP_CLIENT_VERSION = "0.1.0"
# app-server 启动后默认 EOF 等待时间（秒），超时后 terminate/kill
_CLOSE_GRACE_SECONDS = 5.0
# 有界 stderr 诊断捕获上限（D2 崩溃恢复）：只保留最近 N 字节，避免 PIPE
# 被 64KB 缓冲填满阻塞子进程；内容先经 redact_secrets 脱敏再落队列。
_STDERR_CAPTURE_LIMIT = 64 * 1024
# 进程组回收宽限：SIGTERM 后无论直接子进程是否已退出，最终 SIGKILL 原进程组，
# 保证忽略 SIGTERM 的后代也被回收（P0-3）。
_KILL_GROUP_GRACE_SECONDS = 2.0
# 兼容队列（drain_notifications）上限：有订阅者时通知也已投递，保留队列
# 只作兼容；设上限防长期运行无界增长（P1-1）。
_NOTIFICATIONS_CAP = 256


class CodexCliUnavailableError(RuntimeError):
    """Codex CLI 不存在/不可执行——登录与模型能力都不可用（fail closed）。"""

    code = "codex_cli_unavailable"


class CodexAppServerError(RuntimeError):
    """app-server 进程级错误：RPC 错误、超时、进程退出等。

    ``category`` 供 API 边界映射稳定产品文案（P0-R1：不传上游原文）。
    """

    code = "codex_app_server"

    def __init__(self, message: str, *, category: str = "codex_app_server"):
        super().__init__(message)
        self.category = category


def default_codex_home() -> Path:
    """独立用户级 CODEX_HOME：~/.openbrep/codex。

    与 obr7 的 ~/.openbrep/{run,logs} 同一用户数据目录，但绝不使用 ~/.codex，
    因此不会继承开发者/日常 Codex CLI 的登录态。
    """
    return Path.home() / ".openbrep" / "codex"


def parse_codex_version(user_agent: str) -> tuple[int, int, int] | None:
    """从 initialize.userAgent 解析 Codex CLI 版本。

    实测 userAgent 首 token 为 "openbrep/0.147.0"，其中 0.147.0 是 app-server
    自身（Codex CLI）版本。解析失败返回 None（上层 fail closed）。
    """
    text = str(user_agent or "").strip()
    if not text:
        return None
    first = text.split(None, 1)[0]
    if "/" not in first:
        return None
    version = first.split("/", 1)[1].strip()
    parts = version.split(".")
    numbers: list[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
        if len(numbers) == 3:
            break
    if len(numbers) != 3:
        return None
    return (numbers[0], numbers[1], numbers[2])


class StdioJsonRpcTransport:
    """spawn `codex app-server` 并通过 stdio JSONL-RPC 通信的传输层。

    ``codex_binary``/``extra_args`` 可注入，测试用 fake app-server
    （任意语言写的 JSONL-RPC 脚本）替换真实二进制。

    D2 硬化：
    - 通知分发：subscribe(handler) 在 reader 线程逐条投递（handler 须快）。
    - 迟到响应：请求超时后到达的同 id 响应被丢弃，不污染后续调用。
    - 崩溃检测：reader EOF（非主动 close）→ crashed 状态 + 退出码。
    - 有界 stderr 捕获：PIPE 持续 drain，内容脱敏后保留最近 64KB。
    - 进程组清理：POSIX 下 close() 按进程组 terminate/kill，无遗留子进程。
    """

    def __init__(
        self,
        *,
        codex_binary: str = "codex",
        codex_home: str | Path | None = None,
        extra_args: tuple[str, ...] = ("app-server",),
        rpc_timeout: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.codex_binary = codex_binary
        self.codex_home = Path(codex_home) if codex_home is not None else default_codex_home()
        self.extra_args = extra_args
        self.rpc_timeout = rpc_timeout
        self.logger = logger or _LOGGER
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._responses: dict[int, dict] = {}
        self._notifications: deque[dict] = deque(maxlen=_NOTIFICATIONS_CAP)
        self._cv = threading.Condition()
        self._next_id = itertools.count(1)
        # 串行化 JSON-RPC 帧：app-server 是单 stdin 管道，并发写会交错帧。
        # call() 全程持锁（含等待响应），保证一帧一帧写、id 关联不混乱。
        self._call_lock = threading.Lock()
        # D2：通知订阅者（reader 线程投递）；in-flight 请求 id 集合；崩溃标记
        self._subscribers: list[Callable[[dict], None]] = []
        # 显式 pending 请求 id 集合（P0-3）：reader 只接受匹配 pending 的响应，
        # 未请求/已超时/迟到的 id 一律丢弃——绝不污染后续调用，也绝不无界增长。
        self._pending: set[int] = set()
        self._crashed = False
        self._crash_exit_code: int | None = None
        self._reader_finished = False
        # close() 开始后置 True：in-flight call 立即失败而不是干等到超时
        # （P0-2 取消/等待协议：close 唤醒所有 waiter）。
        self._closing = False
        # 有界脱敏 stderr 尾巴（崩溃诊断用，日志路径先脱敏）
        self._stderr_tail: deque[str] = deque(maxlen=64)
        self._stderr_bytes = 0
        # POSIX：启动时捕获进程组 id，close() 时即使直接子进程已退出也要
        # 回收组内后代（避免 app-server 退出后遗留孙进程）。
        self._pgid: int | None = None

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        if self._proc is not None:
            return
        self.codex_home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.codex_home)
        argv = [self.codex_binary, *self.extra_args]
        # 日志不输出 codex_home（auth 文件所在路径属敏感信息，见 D1 秘密门禁）
        self.logger.info("starting codex app-server: argv=%s", argv)
        try:
            # stderr 用 PIPE + 常驻 drain 线程：无人 drain 的 PIPE 会被 64KB
            # 缓冲填满后阻塞子进程（D1 P0-并发风险），有界捕获同时提供
            # 崩溃诊断（D2）；内容先脱敏再保留。
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            self._proc = None
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            ) from exc
        self._crashed = False
        self._crash_exit_code = None
        self._reader_finished = False
        self._closing = False
        self._responses.clear()
        self._pending.clear()
        self._notifications.clear()
        self._stderr_tail.clear()
        self._stderr_bytes = 0
        if os.name == "posix":
            try:
                self._pgid = os.getpgid(self._proc.pid)
            except OSError:
                self._pgid = None
        self._reader = threading.Thread(
            target=self._read_loop, name="codex-app-server-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._stderr_loop, name="codex-app-server-stderr", daemon=True
        )
        self._stderr_reader.start()

    @property
    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and not self._crashed and proc.poll() is None

    @property
    def crashed(self) -> bool:
        return self._crashed

    @property
    def crash_exit_code(self) -> int | None:
        return self._crash_exit_code

    def stderr_tail(self, limit: int = 4000) -> str:
        """脱敏后的 stderr 尾巴（崩溃诊断；绝不进 API payload）。"""
        with self._cv:
            return "".join(self._stderr_tail)[-limit:]

    def _append_stderr(self, chunk: str) -> None:
        # 在 reader 之外的独立线程调用；脱敏后按行入有界队列
        from openbrep.codex.redact import redact_secrets

        redacted = redact_secrets(chunk)
        with self._cv:
            self._stderr_bytes += len(chunk)
            if self._stderr_bytes > _STDERR_CAPTURE_LIMIT:
                # 超限后只保留总字节计数（避免无限增长），尾巴取最近行
                self._stderr_tail.append(redacted)
                while sum(len(x) for x in self._stderr_tail) > _STDERR_CAPTURE_LIMIT:
                    self._stderr_tail.popleft()
            else:
                self._stderr_tail.append(redacted)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for chunk in proc.stderr:
            if chunk:
                self._append_stderr(chunk)

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._handle_line(line)
        finally:
            # stdout EOF / 任何未预期异常：都标记 reader 结束；非主动 close 视为
            # 崩溃并唤醒所有 waiter（P0-5：异常不得静默杀死 reader）。
            with self._cv:
                self._reader_finished = True
                if self._proc is proc:
                    try:
                        exit_code = proc.poll()
                        if exit_code is None:
                            try:
                                proc.wait(timeout=2.0)
                            except subprocess.TimeoutExpired:
                                exit_code = None
                            else:
                                exit_code = proc.returncode
                        self._crashed = True
                        self._crash_exit_code = exit_code
                        self.logger.warning(
                            "codex app-server 进程已退出（exit=%s）——标记 crashed，"
                            "可用 restart() 恢复",
                            exit_code,
                        )
                    except Exception:  # noqa: BLE001
                        self._crashed = True
                        self._crash_exit_code = None
                self._cv.notify_all()

    def _handle_line(self, line: str) -> None:
        """处理一行协议输出（reader 线程调用；测试可注入）。P0-3：响应只认
        pending id，未请求/已超时/迟到 id 一律固定脱敏记录并丢弃。"""
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            # P0-R1B：协议行不得原样进日志（可能含 access_token/loginId/Bearer 等）；
            # 只记固定文案 + 长度，不记内容。
            self.logger.warning(
                "codex app-server: 忽略非 JSON 协议输出（length=%d）",
                len(line),
            )
            return
        if not isinstance(msg, dict):
            self.logger.warning(
                "codex app-server: 忽略非对象协议帧（length=%d）",
                len(line),
            )
            return
        rid = msg.get("id")
        if rid is None:
            # 通知：method 必须是非空字符串
            method = msg.get("method")
            if not isinstance(method, str) or not method.strip():
                # P0-5：畸形通知帧固定脱敏记录并继续，不杀 reader
                self.logger.warning(
                    "codex app-server: 忽略畸形通知帧（length=%d）",
                    len(line),
                )
                return
            with self._cv:
                self._notifications.append(msg)
                subscribers = list(self._subscribers)
                self._cv.notify_all()
            for handler in subscribers:
                try:
                    handler(msg)
                except Exception as exc:  # noqa: BLE001 —— 订阅者不得打断 reader
                    # P0-4：订阅者异常绝不 exc_info/str(exc) 进日志
                    # （异常原文可能含 Bearer/token）；只记稳定事件名+异常类名。
                    self.logger.warning(
                        "codex app-server 通知订阅者异常（%s）",
                        exc.__class__.__name__,
                    )
            return
        # P0-5：响应 id 只接受 int（拒绝 list/dict/str/bool 等）；
        # 畸形 id 固定脱敏记录并继续，绝不把未校验 id 用于 dict 索引。
        if not isinstance(rid, int) or isinstance(rid, bool):
            self.logger.warning(
                "codex app-server: 忽略畸形响应帧 id（length=%d）",
                len(line),
            )
            return
        with self._cv:
            if rid not in self._pending:
                # P0-3：未请求/已超时/迟到的响应 id——固定脱敏记录并丢弃。
                # 绝不写进 _responses，绝不污染后续同 id 调用。
                self.logger.warning(
                    "codex app-server: 丢弃非 pending 响应（id=%s）",
                    rid,
                )
            else:
                # P0-2：first-response-wins——收到第一条合法响应即原子 claim 该
                # pending id（从 pending 移除），第二条同 id 响应视为非 pending
                # 丢弃，绝不覆盖第一条。
                self._pending.discard(rid)
                self._responses[rid] = msg
                self._cv.notify_all()

    @staticmethod
    def _signal_group(pgid: int | None, sig: int) -> None:
        """POSIX：对进程组发信号；绝不误杀自己所在的进程组。"""
        if os.name != "posix" or pgid is None:
            return
        try:
            if pgid != os.getpgrp():
                os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        pgid, self._pgid = self._pgid, None
        # P0-2 取消/等待协议：置 closing 并唤醒所有 waiter（in-flight call
        # 立即以 process_exited/closed 失败，而不是干等到 rpc_timeout）。
        with self._cv:
            self._closing = True
            # P0-3：close 清 pending/responses（在途调用由各自 waiter 移除，
            # 孤儿响应不再被任何后续调用消费）。
            self._pending.clear()
            self._responses.clear()
            self._cv.notify_all()
        try:
            if proc.stdin is not None:
                proc.stdin.close()  # EOF → app-server 优雅退出
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=_CLOSE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._signal_group(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=_KILL_GROUP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            # P0-3：无论直接子进程在 SIGTERM 后是否已退出，都要对原进程组
            # 最终 SIGKILL（忽略 SIGTERM 的后代也必须被回收）。
            self._signal_group(pgid, signal.SIGKILL)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        else:
            # 直接子进程已退出（可能优雅 exit 0），但进程组内可能还有后代
            # （app-server 派生的 helper）；按进程组兜底回收，避免遗留子进程。
            self._signal_group(pgid, signal.SIGTERM)
            self._signal_group(pgid, signal.SIGKILL)
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=2.0)
        stderr_reader, self._stderr_reader = self._stderr_reader, None
        if stderr_reader is not None:
            stderr_reader.join(timeout=2.0)
        self.logger.info("codex app-server closed (exit=%s)", proc.returncode)

    # ── 通知分发 ─────────────────────────────────────────────

    def subscribe(self, handler: Callable[[dict], None]) -> None:
        with self._cv:
            if handler not in self._subscribers:
                self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[dict], None]) -> None:
        with self._cv:
            try:
                self._subscribers.remove(handler)
            except ValueError:
                pass

    # ── JSON-RPC ─────────────────────────────────────────────

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._call_lock:
            return self._call_locked(method, params)

    def _call_locked(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        proc = self._proc
        if proc is None:
            raise CodexAppServerError(
                "codex app-server 尚未启动（先调用 start()）。", category="not_started"
            )
        if self._closing:
            raise CodexAppServerError(
                "codex app-server 正在关闭，请求已取消。",
                category="closed",
            )
        if self._crashed or proc.poll() is not None:
            raise CodexAppServerError(
                f"codex app-server 进程已退出（exit={self._crash_exit_code or proc.returncode}）。"
                "请调用 restart() 恢复。",
                category="process_exited",
            )
        req_id = next(self._next_id)
        # P0-3：发出前登记 pending——reader 只认这个 id 的响应。
        with self._cv:
            self._pending.add(req_id)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._cv:
                self._pending.discard(req_id)
            raise CodexAppServerError(
                f"codex app-server 写入失败（进程可能已退出）：{exc}",
                category="write_failed",
            ) from exc

        deadline = time.monotonic() + self.rpc_timeout
        with self._cv:
            while True:
                # P0-2：先检查已接收响应再判断 deadline——响应已按时到达但
                # waiter 调度稍晚时，不得误报 timeout。
                if req_id in self._responses:
                    resp = self._responses.pop(req_id)
                    self._pending.discard(req_id)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # D2：超时——移除 pending，并丢弃任何已存入的孤儿响应
                    self._pending.discard(req_id)
                    self._responses.pop(req_id, None)
                    raise CodexAppServerError(
                        f"codex app-server 请求超时（>{self.rpc_timeout:.0f}s）：{method}",
                        category="timeout",
                    )
                if self._closing:
                    self._pending.discard(req_id)
                    raise CodexAppServerError(
                        "codex app-server 正在关闭，请求已取消。",
                        category="closed",
                    )
                if self._crashed or proc.poll() is not None:
                    self._pending.discard(req_id)
                    raise CodexAppServerError(
                        "codex app-server 进程已退出"
                        f"（exit={self._crash_exit_code or proc.returncode}）。"
                        "请调用 restart() 恢复。",
                        category="process_exited",
                    )
                self._cv.wait(remaining)
        if "error" in resp and resp.get("error"):
            err = resp["error"]
            message = ""
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            raise CodexAppServerError(
                f"codex app-server {method} 返回错误：{message or err}",
                category="rpc_error",
            )
        result = resp.get("result") or {}
        return result if isinstance(result, dict) else {"data": result}

    def drain_notifications(self) -> list[dict]:
        with self._cv:
            out = list(self._notifications)
            self._notifications.clear()
        return out


class CodexAppServerClient:
    """Codex app-server 的 JSON-RPC 客户端（D1 方法面 + D2 账户生命周期方法）。

    支持注入 transport（fake app-server），生产环境默认 StdioJsonRpcTransport。
    initialize 后记录 app-server 版本（协商由 provider 负责）。
    """

    def __init__(
        self,
        *,
        transport: Any | None = None,
        codex_binary: str = "codex",
        codex_home: str | Path | None = None,
        rpc_timeout: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._logger = logger or _LOGGER
        self._transport = transport or StdioJsonRpcTransport(
            codex_binary=codex_binary,
            codex_home=codex_home,
            rpc_timeout=rpc_timeout,
            logger=self._logger,
        )
        self._server_version: tuple[int, int, int] | None = None
        self._user_agent = ""

    @property
    def transport(self) -> Any:
        return self._transport

    @property
    def server_version(self) -> tuple[int, int, int] | None:
        """initialize 后解析出的 app-server（Codex CLI）版本。"""
        return self._server_version

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def start(self) -> dict[str, Any]:
        """启动 app-server 子进程并完成 initialize 握手。"""
        self._transport.start()
        return self.initialize()

    def restart(self) -> dict[str, Any]:
        """崩溃/退出后重建传输并重新握手（close → start → initialize）。"""
        self._transport.close()
        self._transport.start()
        return self.initialize()

    def initialize(self) -> dict[str, Any]:
        result = self._transport.call(
            "initialize",
            {
                "clientInfo": {
                    "name": _OPENBREP_CLIENT_NAME,
                    "version": _OPENBREP_CLIENT_VERSION,
                },
                "capabilities": None,
            },
        )
        self._user_agent = str(result.get("userAgent") or "")
        self._server_version = parse_codex_version(self._user_agent)
        return result

    def account_read(self) -> dict[str, Any]:
        """account/read：{account: {...}|null, requiresOpenaiAuth: bool}。"""
        return self._transport.call("account/read", {})

    def account_login_start(self, login_type: str = "chatgpt") -> dict[str, Any]:
        """account/login/start：chatgpt 浏览器 OAuth 或 chatgptDeviceCode。

        返回 {type, loginId, authUrl}（chatgpt）或
        {type, loginId, verificationUrl, userCode}（chatgptDeviceCode）。
        loginId 仅客户端内部保存用于取消，绝不外传。
        """
        return self._transport.call("account/login/start", {"type": login_type})

    def account_login_start_chatgpt(self) -> dict[str, Any]:
        """D1 兼容别名：只启动 chatgpt 浏览器 OAuth。"""
        return self.account_login_start("chatgpt")

    def account_login_cancel(self, login_id: str) -> dict[str, Any]:
        """account/login/cancel：取消进行中的登录（{status: canceled|notFound}）。"""
        return self._transport.call("account/login/cancel", {"loginId": login_id})

    def account_logout(self) -> dict[str, Any]:
        """account/logout：清除当前登录态（返回空 dict）。"""
        return self._transport.call("account/logout", {})

    def account_rate_limits_read(self) -> dict[str, Any]:
        """account/rateLimits/read：{rateLimits, rateLimitsByLimitId, ...}。

        未登录时 app-server 返回 JSON-RPC 错误（-32600）——由上层映射为
        稳定文案（fail closed），绝不透传上游原文。
        """
        return self._transport.call("account/rateLimits/read", {})

    def model_list(self) -> dict[str, Any]:
        """model/list：{data: [Model...], nextCursor}。"""
        return self._transport.call("model/list", {})

    # ── D3：turn 层（CHAT/EXPLAIN 安全调用）───────────────────────────────
    # 协议面见 openbrep/codex/turn.py（0.147.0 `codex app-server generate-ts`
    # 绑定）：thread/start（ephemeral 只读线程）→ turn/start（文本输入）→
    # 通知流（item/agentMessage/delta、item/completed、turn/completed、error）
    # → turn/interrupt（取消）→ thread/delete（清理）。

    def thread_start(self, params: dict) -> dict[str, Any]:
        """thread/start：创建（ephemeral）线程，返回 {thread, model, ...}。"""
        return self._transport.call("thread/start", params)

    def turn_start(self, params: dict) -> dict[str, Any]:
        """turn/start：在线程上启动一次 turn；事件以通知流式返回。"""
        return self._transport.call("turn/start", params)

    def turn_interrupt(self, params: dict) -> dict[str, Any]:
        """turn/interrupt：中断进行中的 turn（返回空 dict）。"""
        return self._transport.call("turn/interrupt", params)

    def thread_delete(self, params: dict) -> dict[str, Any]:
        """thread/delete：删除线程（清理 ephemeral thread，返回空 dict）。"""
        return self._transport.call("thread/delete", params)

    def close(self) -> None:
        """关闭 app-server：stdin EOF → 等待退出 → 进程组兜底 terminate/kill。"""
        self._transport.close()
