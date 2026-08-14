"""Fake Codex app-server for unit tests（D1 + D2）。

Speaks the same wire protocol as `codex app-server` (newline-delimited JSON-RPC
over stdio). The client under test cannot tell it apart from the real binary,
which is exactly the point: framing, id association, notifications, EOF-close,
crash/restart, device-code, cancellation, rate-limits and CODEX_HOME env
wiring are exercised for real.

D2 failure/edge modes are driven by environment variables:

- FAKE_CODEX_VERSION          initialize.userAgent 版本（默认 0.147.0；
                              "old" → 0.50.0；"garbage" → 无版本号）
- FAKE_CODEX_LOGIN_TYPE       account/login/start 返回类型：
                              chatgpt（默认）/ chatgptDeviceCode / apiKey /
                              chatgptAuthTokens / unexpected
- FAKE_CODEX_LOGIN_CANCEL     account/login/cancel 返回 status：canceled/notFound
- FAKE_CODEX_RATE_LIMITS      account/rateLimits/read：
                              ok（默认未登录错误）/ reached / normal / unauth
- FAKE_CODEX_GARBAGE_LINE     启动时先输出一行非 JSON（协议污染测试）
- FAKE_CODEX_STDERR_SECRET    启动时向 stderr 输出一行秘密（脱敏测试）
- FAKE_CODEX_DELAY_RESPONSE_SECONDS  响应前 sleep（超时/迟到响应测试）
- FAKE_CODEX_CRASH_IMMEDIATELY      启动即退出非零（崩溃检测）
- FAKE_CODEX_CRASH_AFTER_REQUESTS   处理 N 个请求后崩溃（含 stderr 诊断）
- FAKE_CODEX_NOTIFY           每个请求响应前发送一条通知
                              （loginCompleted / rateLimitsUpdated / accountUpdated）
- FAKE_CODEX_SPAWN_CHILD_PID_FILE   启动时 spawn 一个 sleep 子进程并把 PID
                              写入该文件（进程组清理测试）

Run as: python fake_codex_app_server.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _notify_payload(name: str) -> dict:
    if name == "loginCompleted":
        return {
            "method": "account/login/completed",
            "params": {"loginId": "fake-login-id", "success": True, "error": None},
        }
    if name == "rateLimitsUpdated":
        return {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "limitId": "codex",
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                    "planType": "pro",
                    "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 0},
                }
            },
        }
    if name == "accountUpdated":
        return {
            "method": "account/updated",
            "params": {"authMode": "chatgpt", "planType": "pro"},
        }
    return {"method": "test/notification", "params": {"value": 1}}


def main() -> None:
    version = os.environ.get("FAKE_CODEX_VERSION", "0.147.0")
    login_type = os.environ.get("FAKE_CODEX_LOGIN_TYPE", "chatgpt")
    cancel_status = os.environ.get("FAKE_CODEX_LOGIN_CANCEL", "canceled")
    rate_mode = os.environ.get("FAKE_CODEX_RATE_LIMITS", "unauth")
    notify = os.environ.get("FAKE_CODEX_NOTIFY", "")
    delay = float(os.environ.get("FAKE_CODEX_DELAY_RESPONSE_SECONDS", "0"))
    crash_after = int(os.environ.get("FAKE_CODEX_CRASH_AFTER_REQUESTS", "0"))
    crash_marker = os.environ.get("FAKE_CODEX_CRASH_MARKER", "")
    if crash_after and crash_marker and os.path.exists(crash_marker):
        crash_after = 0  # 已崩溃过一次（restart 测试），本次不崩
    child_pid_file = os.environ.get("FAKE_CODEX_SPAWN_CHILD_PID_FILE", "")

    garbage = os.environ.get("FAKE_CODEX_GARBAGE_LINE")
    if garbage:
        sys.stdout.write(garbage + "\n")
        sys.stdout.flush()
    stderr_secret = os.environ.get("FAKE_CODEX_STDERR_SECRET")
    if stderr_secret:
        sys.stderr.write(stderr_secret + "\n")
        sys.stderr.flush()
    if child_pid_file:
        # 不设 start_new_session：子进程加入 fake app-server 的进程组
        # （transport 用 start_new_session=True 启动 fake），进程组回收时一并清理。
        if os.environ.get("FAKE_CODEX_CHILD_IGNORE_TERM"):
            child_code = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"
            )
        else:
            child_code = "import time; time.sleep(300)"
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(child_pid_file, "w", encoding="utf-8") as f:
            f.write(str(child.pid))
        # 保持子进程引用，避免 GC 干扰（Popen 析构不杀进程）
        _CHILD = child

    if os.environ.get("FAKE_CODEX_CRASH_IMMEDIATELY"):
        sys.stderr.write("fake app-server crash at startup\n")
        sys.stderr.flush()
        sys.exit(42)

    handled = 0
    delay_first = float(os.environ.get("FAKE_CODEX_DELAY_FIRST_RESPONSE_SECONDS", "0"))
    malformed_sent = False
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("id")
        method = msg.get("method") or ""
        delay_method = os.environ.get("FAKE_CODEX_DELAY_METHOD", "")
        if delay and (not delay_method or method == delay_method):
            time.sleep(delay)
        elif delay_first and handled == 0:
            time.sleep(delay_first)
        if notify:
            _send(_notify_payload(notify))
        handled += 1

        if method == "initialize":
            if version == "garbage":
                user_agent = "openbrep-garbage no-version-token"
            elif version == "old":
                user_agent = "openbrep/0.50.0 (Mac OS 15.7.5; arm64)"
            else:
                user_agent = f"openbrep/{version} (Mac OS 15.7.5; arm64)"
            result = {
                "userAgent": user_agent,
                "codexHome": os.environ.get("CODEX_HOME", ""),
                "platformFamily": "unix",
                "platformOs": "macos",
            }
            if os.environ.get("FAKE_CODEX_MALFORMED_FRAMES") and not malformed_sent:
                malformed_sent = True
                # P0-5：畸形帧批次——list/dict id、无 method 通知、非对象帧、非 JSON
                _send({"jsonrpc": "2.0", "id": [1, 2], "result": {}})
                _send({"jsonrpc": "2.0", "id": {"a": 1}, "result": {}})
                _send({"jsonrpc": "2.0", "params": {}})  # 无 method 的通知
                _send(["not", "a", "dict"])
                _send("not json at all {{{{")  # 非 JSON 行
        elif method == "account/read":
            if os.environ.get("FAKE_CODEX_SIGNED_IN"):
                result = {
                    "account": {"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
                    "requiresOpenaiAuth": False,
                }
            else:
                result = {"account": None, "requiresOpenaiAuth": True}
        elif method == "account/login/start":
            if login_type == "chatgpt":
                result = {
                    "type": "chatgpt",
                    "loginId": "fake-login-id",
                    "authUrl": "https://example.test/auth/callback?state=fake",
                }
            elif login_type == "chatgptDeviceCode":
                result = {
                    "type": "chatgptDeviceCode",
                    "loginId": "fake-login-id",
                    "verificationUrl": "https://example.test/device",
                    "userCode": "ABCD-EFGH",
                }
            else:
                result = {"type": login_type, "loginId": "fake-login-id"}
        elif method == "account/login/cancel":
            result = {"status": cancel_status}
        elif method == "account/logout":
            result = {}
        elif method == "account/rateLimits/read":
            if rate_mode == "unauth":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {
                            "code": -32600,
                            "message": "codex account authentication required to read rate limits",
                        },
                    }
                )
                continue
            if rate_mode == "reached":
                rl = {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {"usedPercent": 100, "windowDurationMins": 360, "resetsAt": 1786800000},
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    "spendControlReached": False,
                    "planType": "pro",
                    "rateLimitReachedType": "rate_limit_reached",
                }
            else:  # normal
                rl = {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 1786800000},
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "123.45"},
                    "spendControlReached": False,
                    "planType": "pro",
                    "rateLimitReachedType": None,
                }
            result = {
                "rateLimits": rl,
                "rateLimitsByLimitId": {"codex": rl},
                "rateLimitResetCredits": None,
            }
        elif method == "model/list":
            result = {
                "data": [
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
                        "modelSpecialty": None,
                    },
                ],
                "nextCursor": None,
            }
        elif method == "never/respond":
            continue  # 专门用于超时测试：不发响应
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )
            continue
        _send({"jsonrpc": "2.0", "id": rid, "result": result})

        if crash_after and handled >= crash_after:
            if crash_marker:
                try:
                    with open(crash_marker, "w", encoding="utf-8") as f:
                        f.write("1")
                except OSError:
                    pass
            sys.stderr.write("fake app-server crash: simulated failure\n")
            sys.stderr.flush()
            sys.exit(43)

    if os.environ.get("FAKE_CODEX_IGNORE_EOF"):
        # P0-3：stdin EOF 后不退出（父进程对 SIGTERM 响应退出），用于验证
        # close() 进程组兜底回收。
        import signal as _signal

        def _on_term(signum, frame):
            sys.exit(0)

        _signal.signal(_signal.SIGTERM, _on_term)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
