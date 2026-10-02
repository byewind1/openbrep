"""Native file/directory chooser dialogs for local desktop sessions.

Return contract of choose_file()/choose_directory():

- ``str`` path — the user confirmed a selection;
- ``None`` — the user cancelled the dialog (a native-helper timeout is also
  mapped to cancel, matching the macOS behavior);
- raises :class:`DialogUnavailableError` — no dialog backend is usable on
  this platform (e.g. PowerShell missing on Windows *and* tkinter
  unavailable). Callers must surface this as an error, never as a cancel.

Platform dispatch: macOS → osascript (120s timeout), Windows → PowerShell
``System.Windows.Forms`` dialogs (``-STA``, 120s timeout) with a tkinter
fallback, other platforms → tkinter.
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

_DIALOG_TIMEOUT = 120


class DialogUnavailableError(RuntimeError):
    """No native dialog backend is usable; distinct from a user cancel."""


def choose_file(
    *,
    title: str = "打开 GDL / GSM 文件",
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    normalized_extensions = _normalize_extensions(extensions)
    system = platform.system()
    if system == "Darwin":
        return _choose_file_macos(title=title, initial_dir=initial_dir, extensions=normalized_extensions)
    if system == "Windows":
        try:
            return _choose_file_windows(title=title, initial_dir=initial_dir, extensions=normalized_extensions)
        except DialogUnavailableError:
            pass
    return _choose_file_tk(title=title, initial_dir=initial_dir, extensions=normalized_extensions)


def choose_path(*, title: str = "打开 GDL / GSM 文件", initial_dir: str | None = None) -> str | None:
    return choose_file(title=title, initial_dir=initial_dir)


def choose_directory(*, title: str = "选择 HSF 项目目录", initial_dir: str | None = None) -> str | None:
    system = platform.system()
    if system == "Darwin":
        return _choose_directory_macos(title=title, initial_dir=initial_dir)
    if system == "Windows":
        try:
            return _choose_directory_windows(title=title, initial_dir=initial_dir)
        except DialogUnavailableError:
            pass
    return _choose_directory_tk(title=title, initial_dir=initial_dir)


def _choose_directory_macos(*, title: str, initial_dir: str | None = None) -> str | None:
    script = _choose_directory_macos_script(title=title, initial_dir=initial_dir)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_DIALOG_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


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


def _choose_file_macos(
    *,
    title: str,
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    script = _choose_file_macos_script(title=title, initial_dir=initial_dir, extensions=extensions)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_DIALOG_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _choose_file_macos_script(
    *,
    title: str,
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str:
    default_location_arg = ""
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        if initial_path.exists():
            default_location = initial_path if initial_path.is_dir() else initial_path.parent
            default_location_arg = f' default location POSIX file "{_escape_applescript(str(default_location))}"'
    file_type_arg = ""
    normalized_extensions = _normalize_extensions(extensions)
    if normalized_extensions:
        quoted = ", ".join(f'"{_escape_applescript(ext)}"' for ext in normalized_extensions)
        file_type_arg = f" of type {{{quoted}}}"
    return (
        "use scripting additions\n"
        "activate\n"
        f'return POSIX path of (choose file with prompt "{_escape_applescript(title)}"{default_location_arg}{file_type_arg})'
    )


def _choose_directory_windows(*, title: str, initial_dir: str | None = None) -> str | None:
    return _run_powershell_dialog(_choose_directory_windows_script(title=title, initial_dir=initial_dir))


def _choose_directory_windows_script(*, title: str, initial_dir: str | None = None) -> str:
    lines = [
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
        f"$dialog.Description = '{_escape_powershell(title)}'",
        "$dialog.ShowNewFolderButton = $true",
    ]
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        initial_dir_path = initial_path if initial_path.is_dir() else initial_path.parent
        if initial_dir_path.exists():
            lines.append(f"$dialog.SelectedPath = '{_escape_powershell(str(initial_dir_path))}'")
    lines += [
        # ShowDialog needs a topmost owner window to beat the Windows
        # foreground lock when the backend runs as a background process.
        "$owner = New-Object System.Windows.Forms.Form",
        "$owner.TopMost = $true",
        "$result = $dialog.ShowDialog($owner)",
        "$owner.Dispose()",
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($dialog.SelectedPath) }",
    ]
    return "\n".join(lines)


def _choose_file_windows(
    *,
    title: str,
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    return _run_powershell_dialog(
        _choose_file_windows_script(title=title, initial_dir=initial_dir, extensions=extensions)
    )


def _choose_file_windows_script(
    *,
    title: str,
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str:
    lines = [
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
        f"$dialog.Title = '{_escape_powershell(title)}'",
        "$dialog.CheckFileExists = $true",
        f"$dialog.Filter = '{_escape_powershell(_powershell_filter_for_extensions(extensions))}'",
    ]
    if initial_dir:
        initial_path = Path(initial_dir).expanduser()
        initial_dir_path = initial_path if initial_path.is_dir() else initial_path.parent
        if initial_dir_path.exists():
            lines.append(f"$dialog.InitialDirectory = '{_escape_powershell(str(initial_dir_path))}'")
    lines += [
        "$owner = New-Object System.Windows.Forms.Form",
        "$owner.TopMost = $true",
        "$result = $dialog.ShowDialog($owner)",
        "$owner.Dispose()",
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($dialog.FileName) }",
    ]
    return "\n".join(lines)


def _run_powershell_dialog(script: str) -> str | None:
    """Run a PowerShell dialog script; cancel = empty stdout with exit 0."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            timeout=_DIALOG_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DialogUnavailableError("PowerShell is not available for native dialogs.") from exc
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise DialogUnavailableError(
            f"PowerShell dialog failed (exit {result.returncode}): {stderr or 'no error output'}"
        )
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def _powershell_filter_for_extensions(extensions: list[str] | tuple[str, ...] | None) -> str:
    parts = []
    for label, patterns in _filetypes_for_extensions(extensions):
        normalized_patterns = patterns.replace(" ", ";")
        parts.append(f"{label} ({normalized_patterns})|{normalized_patterns}")
    return "|".join(parts)


def _escape_powershell(value: str) -> str:
    return value.replace("'", "''")


def _choose_directory_tk(*, title: str, initial_dir: str | None = None) -> str | None:
    # tkinter modal dialogs run in-process: no subprocess timeout is possible,
    # so this path cannot enforce the 120s bound the native helpers have.
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise DialogUnavailableError(f"tkinter is not available for native dialogs: {exc}") from exc
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


def _choose_file_tk(
    *,
    title: str,
    initial_dir: str | None = None,
    extensions: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    # See _choose_directory_tk: modal in-process dialog, no timeout possible.
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise DialogUnavailableError(f"tkinter is not available for native dialogs: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    _raise_tk_dialog_root(root)
    try:
        selected = filedialog.askopenfilename(
            title=title,
            initialdir=str(Path(initial_dir).expanduser()) if initial_dir else None,
            filetypes=_filetypes_for_extensions(extensions),
        )
    finally:
        root.destroy()
    return selected or None


def _filetypes_for_extensions(extensions: list[str] | tuple[str, ...] | None):
    normalized_extensions = _normalize_extensions(extensions)
    if normalized_extensions:
        patterns = " ".join(f"*.{ext}" for ext in normalized_extensions)
        return [("Supported files", patterns), ("All files", "*.*")]
    return [
        ("OpenBrep sources", "*.gdl *.txt *.gsm"),
        ("GDL scripts", "*.gdl"),
        ("Text files", "*.txt"),
        ("GSM objects", "*.gsm"),
        ("All files", "*.*"),
    ]


def _normalize_extensions(extensions: list[str] | tuple[str, ...] | None) -> list[str]:
    if not extensions:
        return []
    normalized = []
    for ext in extensions:
        cleaned = str(ext).strip().lower().lstrip(".")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


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


# Internal helpers exposed for testing
_choose_directory_macos_script = _choose_directory_macos_script
_choose_file_macos_script = _choose_file_macos_script
_choose_directory_windows_script = _choose_directory_windows_script
_choose_file_windows_script = _choose_file_windows_script
_filetypes_for_extensions = _filetypes_for_extensions
