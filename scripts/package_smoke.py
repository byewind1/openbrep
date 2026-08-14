#!/usr/bin/env python3
"""Smoke-test a packaged OpenBrep zip without using the local dev install.

D7: by default the smoke runs under a clean HOME with OpenAI/Codex env vars
stripped, so a developer machine's accounts / keys / caches cannot leak into
the packaged app's first-run state. Pass ``--no-clean-env`` to opt out.
"""

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

# 首跑环境里必须剥离的 OpenAI / Codex / 本机工作台相关变量（D7）：
# 打包产物首次启动不得继承开发机账号、密钥或登录态。
CLEAN_ENV_STRIP = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_ORG_ID",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY_PATH",
        "OPENAI_CHATGPT_AUTH",
        "OPENAI_SKIP_PROXY",
        "OPENAI_LOG_LEVEL",
        "CODEX_ACCESS_TOKEN",
        "CODEX_HOME",
        "GDL_AGENT_CONFIG",
        "GDL_AGENT_API_KEY",
        "GDL_AGENT_API_BASE",
        "OBR7_API_PORT",
        "OBR7_WEB_PORT",
        "OBR7_TAURI_MODE",
        "OPENBREP_RELEASE_CANARY",
    }
)


def clean_package_env() -> tuple[dict[str, str], Path]:
    """返回 (env, tmp_home)：全新 HOME + 剥离 OpenAI/Codex/本机配置变量。

    调用方负责在 finally 里删除 tmp_home。
    """
    tmp_home = Path(tempfile.mkdtemp(prefix="openbrep_clean_home_"))
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_STRIP}
    env["HOME"] = str(tmp_home)
    if os.name == "nt":
        env["USERPROFILE"] = str(tmp_home)
    return env, tmp_home


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


def smoke_package(
    zip_path: Path, timeout_seconds: float, *, clean_env: bool = True
) -> dict[str, object]:
    port = _find_free_port()
    tmp_root = Path(tempfile.mkdtemp(prefix="openbrep_package_smoke_"))
    process: subprocess.Popen[str] | None = None
    clean_home: Path | None = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_root)

        package_dir = tmp_root / "OpenBrep"
        launcher = _resolve_launcher(package_dir)
        _restore_package_permissions(package_dir)
        env, clean_home = clean_package_env() if clean_env else (os.environ.copy(), None)
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
            "clean_env": clean_env,
            "clean_home": str(clean_home) if clean_home else None,
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
        if clean_home is not None:
            shutil.rmtree(clean_home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an OpenBrep release zip")
    parser.add_argument("zip", type=Path, help="Path to OpenBrep-free-*.zip")
    parser.add_argument("--timeout", type=float, default=60.0, help="Health-check timeout seconds")
    parser.add_argument(
        "--no-clean-env",
        action="store_true",
        help=(
            "Run with the caller's HOME and OpenAI/Codex env vars "
            "(default: clean HOME + stripped env)"
        ),
    )
    args = parser.parse_args()

    result = smoke_package(args.zip.resolve(), args.timeout, clean_env=not args.no_clean_env)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
