#!/usr/bin/env python3
"""Browser-smoke-test the React Workbench launched through ./obr7."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_obr7_launch(root: str | Path, *, api_port: int, web_port: int) -> tuple[list[str], dict[str, str]]:
    root_path = Path(root)
    command = [str(root_path / "obr7"), "--no-open"]
    env = {
        "OBR7_API_PORT": str(api_port),
        "OBR7_WEB_PORT": str(web_port),
    }
    return command, env


def wait_for_url(url: str, *, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def page_has_workbench_markers(*, title: str, body: str) -> bool:
    if title.strip() != "OpenBrep Workbench":
        return False
    normalized = body.casefold()
    required = ("scripts", "3d.gdl", "save", "compile", "settings")
    return all(marker in normalized for marker in required)


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=8)


def collect_process_output(process: subprocess.Popen[str], *, limit: int = 4000) -> str:
    if process.poll() is None:
        terminate_process(process)
    if process.stdout is None:
        return ""
    return process.stdout.read()[-limit:]


def run_smoke(
    *,
    root: str | Path = ROOT,
    api_port: int | None = None,
    web_port: int | None = None,
    timeout_seconds: float = 45.0,
    headed: bool = False,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": True,
            "status": "skip",
            "reason": "missing_playwright",
            "detail": str(exc),
            "hint": "Install with: python -m pip install playwright && python -m playwright install chromium",
        }

    root_path = Path(root)
    api = api_port or find_free_port()
    web = web_port or find_free_port()
    api_url = f"http://127.0.0.1:{api}"
    web_url = f"http://127.0.0.1:{web}"
    command, env_overrides = build_obr7_launch(root_path, api_port=api, web_port=web)
    process: subprocess.Popen[str] | None = None
    browser_error = ""
    title = ""
    body = ""

    try:
        env = os.environ.copy()
        env.update(env_overrides)
        process = subprocess.Popen(
            command,
            cwd=str(root_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        api_ready = wait_for_url(f"{api_url}/api/snapshot", timeout_seconds=timeout_seconds)
        web_ready = wait_for_url(web_url, timeout_seconds=timeout_seconds)

        page_ok = False
        if api_ready and web_ready:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=not headed)
                    page = browser.new_page(viewport={"width": 1440, "height": 960})
                    page.goto(web_url, wait_until="networkidle", timeout=int(timeout_seconds * 1000))
                    title = page.title()
                    body = page.locator("body").inner_text(timeout=5000)
                    page_ok = page_has_workbench_markers(title=title, body=body)
                    browser.close()
            except Exception as exc:
                browser_error = f"{type(exc).__name__}: {exc}"

        output = collect_process_output(process)

        ok = api_ready and web_ready and page_ok
        return {
            "ok": ok,
            "status": "pass" if ok else "fail",
            "api_ready": api_ready,
            "web_ready": web_ready,
            "page_ok": page_ok,
            "api_url": api_url,
            "web_url": web_url,
            "title": title,
            "body_prefix": body[:800],
            "browser_error": browser_error,
            "command": command,
            "output": output[-4000:],
        }
    finally:
        if process is not None and process.poll() is None:
            terminate_process(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OpenBrep React Workbench browser smoke.")
    parser.add_argument("--api-port", type=int, default=None, help="API port to pass to obr7")
    parser.add_argument("--web-port", type=int, default=None, help="Web port to pass to obr7")
    parser.add_argument("--timeout", type=float, default=45.0, help="Timeout seconds")
    parser.add_argument("--headed", action="store_true", help="Show Chromium while testing")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    result = run_smoke(
        api_port=args.api_port,
        web_port=args.web_port,
        timeout_seconds=args.timeout,
        headed=args.headed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
