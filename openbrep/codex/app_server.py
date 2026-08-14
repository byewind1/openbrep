"""官方 Codex app-server 的 stdio JSON-RPC 最小客户端。

协议（实测 Codex CLI 0.147.0 `codex app-server`，默认 transport stdio://）：
- 帧格式：换行分隔 JSON（JSON Lines），不是 LSP 的 Content-Length 头。
- 请求：{"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
- 响应按 id 关联（可乱序）；无 id 的消息是通知（如 account/login/completed）。
- 关闭：关闭 stdin（EOF）即优雅退出（exit 0）；close() 兜底 terminate/kill。

本模块只做「子进程生命周期 + JSON-RPC 搬运」，不包含任何业务语义；
账户/模型能力、登录状态机与脱敏在 provider.py。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# initialize 的 clientInfo 标识（app-server 用来拼 userAgent，非秘密）
_OPENBREP_CLIENT_NAME = "openbrep"
_OPENBREP_CLIENT_VERSION = "0.1.0"
# app-server 启动后默认 EOF 等待时间（秒），超时后 terminate/kill
_CLOSE_GRACE_SECONDS = 5.0


class CodexCliUnavailableError(RuntimeError):
    """Codex CLI 不存在/不可执行——登录与模型能力都不可用（fail closed）。"""

    code = "codex_cli_unavailable"


class CodexAppServerError(RuntimeError):
    """app-server 进程级错误：RPC 错误、超时、进程退出等。"""

    code = "codex_app_server"


def default_codex_home() -> Path:
    """独立用户级 CODEX_HOME：~/.openbrep/codex。

    与 obr7 的 ~/.openbrep/{run,logs} 同一用户数据目录，但绝不使用 ~/.codex，
    因此不会继承开发者/日常 Codex CLI 的登录态。
    """
    return Path.home() / ".openbrep" / "codex"


class StdioJsonRpcTransport:
    """spawn `codex app-server` 并通过 stdio JSONL-RPC 通信的传输层。

    ``codex_binary``/``extra_args`` 可注入，测试用 fake app-server
    （任意语言写的 JSONL-RPC 脚本）替换真实二进制。
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
        self._responses: dict[int, dict] = {}
        self._notifications: list[dict] = []
        self._cv = threading.Condition()
        self._next_id = itertools.count(1)

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
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            self._proc = None
            raise CodexCliUnavailableError(
                f"未检测到 Codex CLI（{self.codex_binary}）。请先安装 Codex CLI 后重试。"
            ) from exc
        self._reader = threading.Thread(target=self._read_loop, name="codex-app-server-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.logger.warning("codex app-server: 非 JSON 输出行被忽略: %r", line[:200])
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("id") is not None:
                with self._cv:
                    self._responses[msg["id"]] = msg
                    self._cv.notify_all()
            else:
                with self._cv:
                    self._notifications.append(msg)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()  # EOF → app-server 优雅退出
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=_CLOSE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=2.0)
        self.logger.info("codex app-server closed (exit=%s)", proc.returncode)

    # ── JSON-RPC ─────────────────────────────────────────────

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        proc = self._proc
        if proc is None:
            raise CodexAppServerError("codex app-server 尚未启动（先调用 start()）。")
        if proc.poll() is not None:
            raise CodexAppServerError(
                f"codex app-server 进程已退出（exit={proc.returncode}）。"
            )
        req_id = next(self._next_id)
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
            raise CodexAppServerError(
                f"codex app-server 写入失败（进程可能已退出）：{exc}"
            ) from exc

        deadline = time.monotonic() + self.rpc_timeout
        with self._cv:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexAppServerError(
                        f"codex app-server 请求超时（>{self.rpc_timeout:.0f}s）：{method}"
                    )
                if req_id in self._responses:
                    resp = self._responses.pop(req_id)
                    break
                self._cv.wait(remaining)
        if "error" in resp and resp.get("error"):
            err = resp["error"]
            message = ""
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            raise CodexAppServerError(
                f"codex app-server {method} 返回错误：{message or err}"
            )
        result = resp.get("result") or {}
        return result if isinstance(result, dict) else {"data": result}

    def drain_notifications(self) -> list[dict]:
        with self._cv:
            out, self._notifications = self._notifications, []
        return out


class CodexAppServerClient:
    """Codex app-server 的最小 JSON-RPC 客户端（D1 要求的方法面）。

    支持注入 transport（fake app-server），生产环境默认 StdioJsonRpcTransport。
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

    @property
    def transport(self) -> Any:
        return self._transport

    def start(self) -> dict[str, Any]:
        """启动 app-server 子进程并完成 initialize 握手。"""
        self._transport.start()
        return self.initialize()

    def initialize(self) -> dict[str, Any]:
        return self._transport.call(
            "initialize",
            {
                "clientInfo": {
                    "name": _OPENBREP_CLIENT_NAME,
                    "version": _OPENBREP_CLIENT_VERSION,
                },
                "capabilities": None,
            },
        )

    def account_read(self) -> dict[str, Any]:
        """account/read：{account: {...}|null, requiresOpenaiAuth: bool}。"""
        return self._transport.call("account/read", {})

    def account_login_start_chatgpt(self) -> dict[str, Any]:
        """account/login/start(type=chatgpt)：返回 {loginId, authUrl}（浏览器 OAuth）。"""
        return self._transport.call("account/login/start", {"type": "chatgpt"})

    def account_logout(self) -> dict[str, Any]:
        """account/logout：清除当前登录态（返回空 dict）。"""
        return self._transport.call("account/logout", {})

    def model_list(self) -> dict[str, Any]:
        """model/list：{data: [Model...], nextCursor}。"""
        return self._transport.call("model/list", {})

    def close(self) -> None:
        """关闭 app-server：stdin EOF → 等待退出 → 兜底 terminate/kill。"""
        self._transport.close()
