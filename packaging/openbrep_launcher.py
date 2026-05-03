"""OpenBrep desktop launcher for packaged builds."""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import streamlit  # noqa: F401  # ensure bundled by PyInstaller


def _resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def _find_free_port(preferred: int = 8501, attempts: int = 50) -> int:
    explicit_port = os.environ.get("OPENBREP_PORT") or os.environ.get("STREAMLIT_SERVER_PORT")
    if explicit_port:
        return int(explicit_port)

    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server_and_open_browser(port: int, timeout_seconds: float = 45.0) -> None:
    url = f"http://127.0.0.1:{port}"
    health_url = f"{url}/_stcore/health"
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    webbrowser.open(url, new=2)
                    return
        except Exception:
            time.sleep(0.5)


def _schedule_browser_open(port: int) -> None:
    if os.environ.get("OPENBREP_NO_BROWSER", "").strip().lower() in {"1", "true", "yes"}:
        return

    thread = threading.Thread(
        target=_wait_for_server_and_open_browser,
        args=(port,),
        daemon=True,
    )
    thread.start()


def _build_streamlit_args(app_py: Path, port: int) -> list[str]:
    return [
        "run",
        str(app_py),
        "--server.headless",
        "true",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--browser.gatherUsageStats",
        "false",
    ]


def _run_streamlit_cli(args: list[str]) -> int:
    from streamlit.web import cli as streamlit_cli

    result = streamlit_cli.main.main(args=args, standalone_mode=False)
    return int(result or 0)


def main() -> int:
    app_py = _resource_path("ui/app.py")
    if not app_py.exists():
        print(f"[OpenBrep] app.py not found: {app_py}")
        return 1

    env = os.environ.copy()
    root = str(_resource_path("."))
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    os.environ.update(env)

    port = _find_free_port()
    print(f"[OpenBrep] starting at http://127.0.0.1:{port}")
    _schedule_browser_open(port)
    return _run_streamlit_cli(_build_streamlit_args(app_py, port))


if __name__ == "__main__":
    raise SystemExit(main())
