"""http_server 的 HTTP 方法支持：PATCH/PUT/DELETE 必须进入路由分发。

回归背景：模型切换走 PATCH /api/settings/llm/model，但 handler 只实现了
do_GET/do_POST，BaseHTTPRequestHandler 对其他方法一律 501，前端 fetch 拿到
非 JSON 的 501 页面 → 误报 "OpenBrep local API is not available"。
"""

from __future__ import annotations

import json
import threading
import urllib.request

from openbrep.workbench.http_server import _WorkbenchRequestHandler, ThreadingHTTPServer


def _serve_once():
    """起一个只处理一个请求周期、端口随机的测试服务器。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WorkbenchRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _request(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data if method != "GET" else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # ok:false 会映射成 404，从异常里读响应体
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_patch_routes_to_dispatch():
    server, port = _serve_once()
    try:
        status, payload = _request(port, "PATCH", "/api/settings/llm/model", {"model": ""})
        # 501 时代返回的是 HTML 错误页；现在必须得到 JSON 路由响应
        assert status == 404  # ok:false → _send 映射 404
        assert payload["ok"] is False
        assert "Model is required" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_put_routes_to_dispatch():
    server, port = _serve_once()
    try:
        status, payload = _request(port, "PUT", "/api/settings/llm/model", {"model": ""})
        assert status == 404
        assert payload["ok"] is False
        assert "Model is required" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_delete_routes_to_dispatch():
    server, port = _serve_once()
    try:
        status, payload = _request(port, "DELETE", "/api/assistant/history")
        # 路由存在：删除历史，应返回 ok 或业务错误，而不是 501 HTML
        assert isinstance(payload, dict)
        assert "ok" in payload
    finally:
        server.shutdown()
        server.server_close()


def test_options_advertises_write_methods():
    server, port = _serve_once()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/snapshot", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as resp:
            allow = resp.headers.get("Access-Control-Allow-Methods", "")
        assert "PATCH" in allow and "PUT" in allow and "DELETE" in allow
    finally:
        server.shutdown()
        server.server_close()
