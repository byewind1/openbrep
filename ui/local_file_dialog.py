from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def choose_file(*, title: str = "打开 GDL / GSM 文件", initial_dir: str | None = None) -> str | None:
    """
    Open a native file chooser for local Streamlit sessions.

    This is intended for local OpenBrep sessions where the Streamlit process
    runs on the same machine as the browser.
    """
    if platform.system() == "Darwin":
        return _choose_file_macos(title=title, initial_dir=initial_dir)
    return _choose_file_tk(title=title, initial_dir=initial_dir)


def choose_path(*, title: str = "打开 GDL / GSM 文件", initial_dir: str | None = None) -> str | None:
    """Compatibility wrapper for older callers that opened project source files."""
    return choose_file(title=title, initial_dir=initial_dir)


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
    script = _choose_directory_macos_script(title=title, initial_dir=initial_dir)

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


def _choose_directory_macos_script(*, title: str, initial_dir: str | None = None) -> str:
    default_location_arg = ""
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        if initial_path.exists():
            default_location = initial_path if initial_path.is_dir() else initial_path.parent
            default_location_arg = f' default location POSIX file "{_escape_applescript(str(default_location))}"'

    return (
        "use scripting additions\n"
        "activate\n"
        f'return POSIX path of (choose folder with prompt "{_escape_applescript(title)}"{default_location_arg})'
    )


def _choose_file_macos(*, title: str, initial_dir: str | None = None) -> str | None:
    script = _choose_file_macos_script(title=title, initial_dir=initial_dir)

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


def _choose_file_macos_script(*, title: str, initial_dir: str | None = None) -> str:
    default_location_arg = ""
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        if initial_path.exists():
            default_location = initial_path if initial_path.is_dir() else initial_path.parent
            default_location_arg = f' default location POSIX file "{_escape_applescript(str(default_location))}"'

    return (
        "use scripting additions\n"
        "activate\n"
        f'return POSIX path of (choose file with prompt "{_escape_applescript(title)}"{default_location_arg})'
    )


def _choose_directory_tk(*, title: str, initial_dir: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    _raise_tk_dialog_root(root)
    try:
        selected = filedialog.askdirectory(
            title=title,
            initialdir=str(Path(initial_dir).expanduser()) if initial_dir else None,
            mustexist=True,
        )
    finally:
        root.destroy()
    return selected or None


def _choose_file_tk(*, title: str, initial_dir: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    _raise_tk_dialog_root(root)
    try:
        selected = filedialog.askopenfilename(
            title=title,
            initialdir=str(Path(initial_dir).expanduser()) if initial_dir else None,
            filetypes=[
                ("OpenBrep sources", "*.gdl *.txt *.gsm"),
                ("GDL scripts", "*.gdl"),
                ("Text files", "*.txt"),
                ("GSM objects", "*.gsm"),
                ("All files", "*.*"),
            ],
        )
    finally:
        root.destroy()
    return selected or None


def _raise_tk_dialog_root(root) -> None:
    try:
        root.lift()
        root.attributes("-topmost", True)
        root.after_idle(root.attributes, "-topmost", False)
        root.update()
    except Exception:
        pass


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
