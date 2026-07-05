"""ThreadingHTTPServer transport for the React workbench local API.

Route dispatch lives in openbrep.workbench_api.route_rpc / WorkbenchSession;
this module only owns the HTTP request/response plumbing and static file
serving (Tauri single-port mode).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openbrep.workbench_api import _default_session, route_rpc

_STATIC_DIR: Path | None = None


def run_server(host: str = "127.0.0.1", port: int = 8765, static_dir: str | None = None) -> None:
    global _STATIC_DIR
    if static_dir:
        _STATIC_DIR = Path(static_dir).resolve()
    restored = _default_session().restore_last_project()
    if restored.get("restored"):
        print(f"Restored last project: {restored.get('project', {}).get('path', '')}")
    server = ThreadingHTTPServer((host, port), _WorkbenchRequestHandler)
    print(f"OpenBrep workbench API listening on http://{host}:{port}")
    server.serve_forever()


class _WorkbenchRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if not route.startswith("/api/") and _STATIC_DIR is not None:
            self._serve_static(route)
            return
        self._send(route_rpc("GET", self.path))

    def _serve_static(self, route: str) -> None:
        rel = route.lstrip("/") or "index.html"
        candidate = (_STATIC_DIR / rel).resolve()
        # Prevent path traversal
        try:
            candidate.relative_to(_STATIC_DIR)
        except ValueError:
            self.send_error(403)
            return
        # SPA fallback: non-existent paths or paths without extension → index.html
        if not candidate.is_file() or "." not in candidate.name:
            candidate = _STATIC_DIR / "index.html"
        if not candidate.is_file():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(candidate))
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        import threading

        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._send({"ok": False, "error": "Invalid JSON"}, status=400)
            return

        if urlparse(self.path).path == "/api/shutdown":
            self._send({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._send(route_rpc("POST", self.path, body))

    def do_OPTIONS(self) -> None:
        self._send({}, status=204)

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, payload: dict[str, Any], status: int | None = None) -> None:
        ok = payload.get("ok", True)
        response_status = status or (200 if ok else 404)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(response_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if response_status != 204:
            self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBrep React workbench local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static-dir", default=None, help="Serve built frontend from this directory (Tauri/desktop mode)")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, static_dir=args.static_dir)


if __name__ == "__main__":
    main()
