#!/usr/bin/env python3
"""Smoke-test a packaged OpenBrep zip without using the local dev install."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http_ok(url: str, timeout_seconds: float, validator) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if validator(response, response.read()):
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _wait_for_health(port: int, timeout_seconds: float) -> bool:
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    return _wait_for_http_ok(
        health_url,
        timeout_seconds,
        lambda response, body: response.status == 200 and body.decode("utf-8").strip() == "ok",
    )


def _wait_for_homepage(port: int, timeout_seconds: float) -> bool:
    root_url = f"http://127.0.0.1:{port}/"
    return _wait_for_http_ok(
        root_url,
        timeout_seconds,
        lambda response, body: response.status == 200 and bool(body.strip()),
    )


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def _resolve_launcher(package_dir: Path) -> Path:
    mac_command = package_dir / "OpenBrep.command"
    mac_binary = package_dir / "OpenBrep"
    win_binary = package_dir / "OpenBrep.exe"

    if mac_command.exists():
        return mac_command
    if win_binary.exists():
        return win_binary
    if mac_binary.exists():
        return mac_binary
    raise FileNotFoundError(f"No OpenBrep launcher found in {package_dir}")


def _ensure_executable(path: Path) -> None:
    if os.name == "nt" or not path.exists():
        return
    path.chmod(path.stat().st_mode | 0o755)


def _restore_package_permissions(package_dir: Path) -> None:
    _ensure_executable(package_dir / "OpenBrep.command")
    _ensure_executable(package_dir / "OpenBrep")
    _ensure_executable(package_dir / "OpenBrep.exe")


def smoke_package(zip_path: Path, timeout_seconds: float) -> dict[str, object]:
    port = _find_free_port()
    tmp_root = Path(tempfile.mkdtemp(prefix="openbrep_package_smoke_"))
    process: subprocess.Popen[str] | None = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_root)

        package_dir = tmp_root / "OpenBrep"
        launcher = _resolve_launcher(package_dir)
        _restore_package_permissions(package_dir)
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
        health_ok = _wait_for_health(port, timeout_seconds)
        homepage_ok = _wait_for_homepage(port, timeout_seconds) if health_ok else False
        ok = health_ok and homepage_ok
        output = ""
        if process.stdout:
            output = process.stdout.read(2000) if process.poll() is not None else ""
        return {
            "ok": ok,
            "health_ok": health_ok,
            "homepage_ok": homepage_ok,
            "zip": str(zip_path),
            "launcher": str(launcher),
            "port": port,
            "url": f"http://127.0.0.1:{port}",
            "returncode": process.poll(),
            "output": output,
        }
    finally:
        if process is not None:
            if os.name != "nt" and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=8)
            else:
                _terminate(process)
        shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an OpenBrep release zip")
    parser.add_argument("zip", type=Path, help="Path to OpenBrep-free-*.zip")
    parser.add_argument("--timeout", type=float, default=60.0, help="Health-check timeout seconds")
    args = parser.parse_args()

    result = smoke_package(args.zip.resolve(), args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
