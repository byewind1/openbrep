from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def choose_directory(*, title: str = "选择 HSF 项目目录", initial_dir: str | None = None) -> str | None:
    """
    Open a native directory chooser for local Streamlit sessions.

    Browsers cannot expose arbitrary local folder paths to web apps. OpenBrep is
    normally run on the same Mac as Archicad, so this helper opens the chooser
    on the local Python process and returns the selected POSIX path.
    """
    if platform.system() == "Darwin":
        return _choose_directory_macos(title=title, initial_dir=initial_dir)
    return _choose_directory_tk(title=title, initial_dir=initial_dir)


def _choose_directory_macos(*, title: str, initial_dir: str | None = None) -> str | None:
    script = f'POSIX path of (choose folder with prompt "{_escape_applescript(title)}"'
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        if initial_path.exists():
            script += f' default location POSIX file "{_escape_applescript(str(initial_path))}"'
    script += ")"

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    return selected or None


def _choose_directory_tk(*, title: str, initial_dir: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(
            title=title,
            initialdir=str(Path(initial_dir).expanduser()) if initial_dir else None,
            mustexist=True,
        )
    finally:
        root.destroy()
    return selected or None


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
