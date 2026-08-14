"""Fake Codex app-server for unit tests（D1：所有单测使用 fake，不碰真实账号/网络）。

Speaks the same wire protocol as `codex app-server` (newline-delimited JSON-RPC
over stdio). The client under test cannot tell it apart from the real binary,
which is exactly the point: framing, id association, notifications, EOF-close
and CODEX_HOME env wiring are exercised for real.

Run as: python fake_codex_app_server.py
"""

from __future__ import annotations

import json
import os
import sys


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
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
        if method == "initialize":
            result = {"codexHome": os.environ.get("CODEX_HOME", "")}
        elif method == "account/read":
            result = {"account": None, "requiresOpenaiAuth": True}
        elif method == "account/login/start":
            result = {
                "type": "chatgpt",
                "loginId": "fake-login-id",
                "authUrl": "https://example.test/auth/callback?state=fake",
            }
        elif method == "account/logout":
            result = {}
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


if __name__ == "__main__":
    main()
