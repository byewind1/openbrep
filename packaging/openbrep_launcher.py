"""OpenBrep desktop launcher for packaged builds."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import streamlit  # noqa: F401  # ensure bundled by PyInstaller


def _resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def main() -> int:
    app_py = _resource_path("ui/app.py")
    if not app_py.exists():
        print(f"[OpenBrep] app.py not found: {app_py}")
        return 1

    env = os.environ.copy()
    root = str(_resource_path("."))
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_py),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
