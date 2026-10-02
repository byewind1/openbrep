"""Safe, explicit source-checkout updates for OpenBrep developers.

This is deliberately separate from the signed desktop updater. It never
replaces an installed application bundle: it fast-forwards a clean ``main``
checkout, synchronises Python/frontend dependencies, and rebuilds the frontend
served by ``obr serve``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


class SourceUpdateError(RuntimeError):
    """Raised when a checkout is unsafe or cannot be updated."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    runner: Runner,
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(command),
            cwd=cwd,
            check=True,
            text=True,
            capture_output=capture,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise SourceUpdateError(f"Command failed: {' '.join(command)}\n{detail}") from exc


def source_update_plan(*, restart_service: bool = False) -> list[list[str]]:
    """Return the deterministic mutation plan for a validated checkout."""
    commands = [
        ["git", "fetch", "origin", "main"],
        ["git", "merge", "--ff-only", "origin/main"],
        [sys.executable, "-m", "pip", "install", "-e", "."],
        ["npm", "ci"],
        ["npm", "run", "build"],
    ]
    if restart_service:
        commands.extend(
            [
                [sys.executable, "-m", "cli.main", "serve", "--stop"],
                [sys.executable, "-m", "cli.main", "serve"],
            ]
        )
    return commands


def validate_source_checkout(
    repo_root: Path,
    *,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    """Resolve and validate a clean OpenBrep ``main`` checkout."""
    root = repo_root.expanduser().resolve()
    required = [
        root / ".git",
        root / "pyproject.toml",
        root / "frontend" / "package-lock.json",
        root / "src-tauri" / "tauri.conf.json",
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
        raise SourceUpdateError(
            f"Not an OpenBrep source checkout ({root}); missing: {', '.join(missing)}"
        )

    missing_tools = [tool for tool in ("git", "npm") if which(tool) is None]
    if missing_tools:
        raise SourceUpdateError(f"Missing developer tools: {', '.join(missing_tools)}")

    branch = _run(
        runner,
        ["git", "branch", "--show-current"],
        cwd=root,
        capture=True,
    ).stdout.strip()
    if branch != "main":
        raise SourceUpdateError(
            f"Source update requires branch main; current branch is {branch or '(detached)'}"
        )

    dirty = _run(
        runner,
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture=True,
    ).stdout.strip()
    if dirty:
        raise SourceUpdateError(
            "Tracked files have local changes; commit or stash them before updating"
        )
    return root


def run_source_update(
    repo_root: Path,
    *,
    dry_run: bool = False,
    restart_service: bool = False,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[list[str]]:
    """Fast-forward and refresh a local checkout, returning executed/planned commands."""
    root = validate_source_checkout(repo_root, runner=runner, which=which)
    plan = source_update_plan(restart_service=restart_service)
    if dry_run:
        return plan

    for command in plan:
        cwd = root / "frontend" if command[0] == "npm" else root
        _run(runner, command, cwd=cwd)
    return plan
