from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from openbrep.developer_update import SourceUpdateError, run_source_update


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "openbrep"
    (root / ".git").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "src-tauri").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='openbrep'\n")
    (root / "frontend" / "package-lock.json").write_text("{}")
    (root / "src-tauri" / "tauri.conf.json").write_text('{"productName":"OpenBrep"}')
    return root


class FakeRunner:
    def __init__(self, *, branch: str = "main", dirty: str = "") -> None:
        self.branch = branch
        self.dirty = dirty
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, command, *, cwd, check, text, capture_output):
        self.calls.append((list(command), cwd))
        stdout = ""
        if command[:3] == ["git", "branch", "--show-current"]:
            stdout = self.branch + "\n"
        elif command[:3] == ["git", "status", "--porcelain"]:
            stdout = self.dirty
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_dry_run_validates_but_does_not_mutate(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    runner = FakeRunner()

    plan = run_source_update(root, dry_run=True, runner=runner, which=lambda _tool: "/bin/tool")

    assert [call[0] for call in runner.calls] == [
        ["git", "branch", "--show-current"],
        ["git", "status", "--porcelain", "--untracked-files=no"],
    ]
    assert plan[:3] == [
        ["git", "fetch", "origin", "main"],
        ["git", "merge", "--ff-only", "origin/main"],
        [sys.executable, "-m", "pip", "install", "-e", "."],
    ]


def test_update_runs_npm_steps_in_frontend_and_can_restart_service(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    runner = FakeRunner()

    plan = run_source_update(
        root,
        restart_service=True,
        runner=runner,
        which=lambda _tool: "/bin/tool",
    )

    mutation_calls = runner.calls[2:]
    assert [command for command, _cwd in mutation_calls] == plan
    assert [cwd for command, cwd in mutation_calls if command[0] == "npm"] == [
        root / "frontend",
        root / "frontend",
    ]
    assert plan[-2:] == [
        [sys.executable, "-m", "cli.main", "serve", "--stop"],
        [sys.executable, "-m", "cli.main", "serve"],
    ]


@pytest.mark.parametrize(
    ("branch", "dirty", "message"),
    [("feature", "", "requires branch main"), ("main", " M file.py", "local changes")],
)
def test_update_refuses_unsafe_checkout(
    tmp_path: Path,
    branch: str,
    dirty: str,
    message: str,
) -> None:
    root = _checkout(tmp_path)
    runner = FakeRunner(branch=branch, dirty=dirty)

    with pytest.raises(SourceUpdateError, match=message):
        run_source_update(root, runner=runner, which=lambda _tool: "/bin/tool")
