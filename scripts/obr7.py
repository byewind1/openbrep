from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_API_PORT = 8765
DEFAULT_WEB_PORT = 5174
FALLBACK_API_PORT = 19065
FALLBACK_WEB_PORT = 19074
HOST = "127.0.0.1"


def is_port_available(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(
    preferred: int,
    host: str = HOST,
    *,
    max_attempts: int = 50,
    fallback_start: int | None = None,
    fallback_attempts: int = 50,
) -> int:
    for port in range(preferred, preferred + max_attempts):
        if is_port_available(port, host):
            return port
    if fallback_start is not None:
        for port in range(fallback_start, fallback_start + fallback_attempts):
            if is_port_available(port, host):
                return port
        raise RuntimeError(
            "No available port found from "
            f"{preferred} to {preferred + max_attempts - 1}, "
            f"or from {fallback_start} to {fallback_start + fallback_attempts - 1}."
        )
    raise RuntimeError(f"No available port found from {preferred} to {preferred + max_attempts - 1}.")


def choose_port(
    *,
    explicit: int | None,
    env_name: str,
    default: int,
    fallback_start: int | None = None,
    host: str = HOST,
) -> tuple[int, bool]:
    raw_env = os.environ.get(env_name, "").strip()
    fixed = explicit is not None or bool(raw_env)
    port = explicit if explicit is not None else int(raw_env) if raw_env else default
    if is_port_available(port, host):
        return port, False
    if fixed:
        raise RuntimeError(f"{env_name or 'port'} {port} is already in use.")
    return find_available_port(port + 1, host, fallback_start=fallback_start), True


def wait_for_url(url: str, *, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def build_api_command(api_port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "openbrep.workbench_api",
        "--host",
        HOST,
        "--port",
        str(api_port),
    ]


def build_web_command(web_port: int) -> list[str]:
    return ["npm", "run", "dev", "--", "--host", HOST, "--port", str(web_port), "--strictPort"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the OpenBrep React workbench.")
    parser.add_argument("--api-port", type=int, default=None, help="Workbench API port. Overrides OBR7_API_PORT.")
    parser.add_argument("--web-port", type=int, default=None, help="Workbench web port. Overrides OBR7_WEB_PORT.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = Path(__file__).resolve().parents[1]
    frontend_dir = root / "frontend"
    if not frontend_dir.exists():
        print(f"[obr7] frontend directory not found: {frontend_dir}", file=sys.stderr)
        return 1

    try:
        api_port, api_shifted = choose_port(
            explicit=args.api_port,
            env_name="OBR7_API_PORT",
            default=DEFAULT_API_PORT,
            fallback_start=FALLBACK_API_PORT,
        )
        web_port, web_shifted = choose_port(
            explicit=args.web_port,
            env_name="OBR7_WEB_PORT",
            default=DEFAULT_WEB_PORT,
            fallback_start=FALLBACK_WEB_PORT,
        )
    except RuntimeError as exc:
        print(f"[obr7] {exc}", file=sys.stderr)
        return 1

    api_url = f"http://{HOST}:{api_port}"
    web_url = f"http://{HOST}:{web_port}"
    if api_shifted:
        print(f"[obr7] API port {DEFAULT_API_PORT} is in use; using {api_port}.")
    if web_shifted:
        print(f"[obr7] Web port {DEFAULT_WEB_PORT} is in use; using {web_port}.")

    env = os.environ.copy()
    env["VITE_OPENBREP_API"] = api_url

    processes: list[subprocess.Popen[bytes]] = []

    def stop_processes(*_: object) -> None:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()

    signal.signal(signal.SIGINT, stop_processes)
    signal.signal(signal.SIGTERM, stop_processes)

    try:
        print(f"[obr7] Starting API: {api_url}")
        processes.append(subprocess.Popen(build_api_command(api_port), cwd=root, env=env))
        if not wait_for_url(f"{api_url}/api/snapshot"):
            print("[obr7] API did not become ready in time.", file=sys.stderr)
            stop_processes()
            return 1

        print(f"[obr7] Starting React workbench: {web_url}")
        processes.append(subprocess.Popen(build_web_command(web_port), cwd=frontend_dir, env=env))
        print(f"[obr7] OpenBrep Workbench: {web_url}")
        print(f"[obr7] API: {api_url}")
        print("[obr7] Press Ctrl+C to stop.")

        if not args.no_open and os.environ.get("OBR7_NO_OPEN", "").strip() not in {"1", "true", "yes"}:
            webbrowser.open(web_url)

        while all(proc.poll() is None for proc in processes):
            time.sleep(0.5)
        return next((proc.returncode or 1 for proc in processes if proc.poll() is not None), 0)
    finally:
        stop_processes()


if __name__ == "__main__":
    raise SystemExit(main())
