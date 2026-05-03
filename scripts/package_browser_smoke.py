#!/usr/bin/env python3
"""Browser-test a packaged OpenBrep zip without using the local dev install."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path


ERROR_MARKERS = (
    "Uncaught app execution",
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "ImportError",
    "FileNotFoundError",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, timeout_seconds: float) -> bool:
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                body = response.read().decode("utf-8").strip()
                if response.status == 200 and body == "ok":
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _resolve_launcher(package_dir: Path) -> Path:
    candidates = [
        package_dir / "OpenBrep.command",
        package_dir / "OpenBrep",
        package_dir / "OpenBrep.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No OpenBrep launcher found in {package_dir}")


def _ensure_executable(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(path.stat().st_mode | 0o755)


def _terminate(process: subprocess.Popen[str]) -> None:
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


def _start_log_reader(process: subprocess.Popen[str], logs: list[str]) -> threading.Thread:
    def _read() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            logs.append(line)

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    return thread


def browser_smoke_package(
    zip_path: Path,
    timeout_seconds: float,
    headed: bool = False,
    keep_temp: bool = False,
) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "error": "missing_playwright",
            "detail": str(exc),
            "hint": "Install with: python -m pip install playwright && python -m playwright install chromium",
        }

    port = _find_free_port()
    tmp_root = Path(tempfile.mkdtemp(prefix="openbrep_package_browser_smoke_"))
    process: subprocess.Popen[str] | None = None
    logs: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_root)

        package_dir = tmp_root / "OpenBrep"
        launcher = _resolve_launcher(package_dir)
        _ensure_executable(package_dir / "OpenBrep.command")
        _ensure_executable(package_dir / "OpenBrep")

        env = os.environ.copy()
        env["OPENBREP_PORT"] = str(port)
        env["OPENBREP_NO_BROWSER"] = "1"

        process = subprocess.Popen(
            [str(launcher)],
            cwd=str(package_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        log_thread = _start_log_reader(process, logs)
        health_ok = _wait_for_health(port, timeout_seconds)

        page_title = ""
        body_prefix = ""
        page_ok = False
        browser_error = ""
        url = f"http://127.0.0.1:{port}"
        if health_ok:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=not headed)
                    page = browser.new_page(viewport={"width": 1600, "height": 1200})
                    page.goto(url, wait_until="networkidle", timeout=int(timeout_seconds * 1000))
                    page.wait_for_timeout(5000)
                    page_title = page.title()
                    body_prefix = page.locator("body").inner_text(timeout=5000)[:800]
                    page_ok = "OpenBrep" in body_prefix
                    browser.close()
            except Exception as exc:
                browser_error = f"{type(exc).__name__}: {exc}"

        _terminate(process)
        log_thread.join(timeout=2)
        output = "".join(logs)
        error_markers = [marker for marker in ERROR_MARKERS if marker in output]
        ok = health_ok and page_ok and not error_markers

        return {
            "ok": ok,
            "health_ok": health_ok,
            "page_ok": page_ok,
            "error_markers": error_markers,
            "zip": str(zip_path),
            "launcher": str(launcher),
            "port": port,
            "url": url,
            "title": page_title,
            "body_prefix": body_prefix,
            "browser_error": browser_error,
            "returncode": process.poll() if process else None,
            "temp_dir": str(tmp_root) if keep_temp else "",
            "output": output[-4000:],
        }
    finally:
        if process is not None and process.poll() is None:
            _terminate(process)
        if not keep_temp:
            shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser-smoke-test an OpenBrep release zip")
    parser.add_argument("zip", type=Path, help="Path to OpenBrep-free-*.zip")
    parser.add_argument("--timeout", type=float, default=90.0, help="Timeout seconds")
    parser.add_argument("--headed", action="store_true", help="Show Chromium while testing")
    parser.add_argument("--keep-temp", action="store_true", help="Keep extracted temp package")
    args = parser.parse_args()

    result = browser_smoke_package(
        args.zip.resolve(),
        timeout_seconds=args.timeout,
        headed=args.headed,
        keep_temp=args.keep_temp,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
