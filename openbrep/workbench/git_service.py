from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


GIT_SETTINGS_PATH = Path(".openbrep") / "git.json"


class WorkbenchGitService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def status(self) -> dict[str, Any]:
        project_path = self._project_path()
        if project_path is None:
            return {"ok": False, "error": "Load an HSF project before using Git.", "git": empty_git_status()}
        return {"ok": True, "git": git_status(project_path)}

    def initialize(self) -> dict[str, Any]:
        project_path = self._project_path()
        if project_path is None:
            return {"ok": False, "error": "Load an HSF project before initializing Git.", "git": empty_git_status()}
        result = run_git(project_path, ["init"])
        if result.returncode != 0:
            return {"ok": False, "error": git_error(result), "git": git_status(project_path)}
        write_git_settings(project_path, enabled=True)
        return {"ok": True, "git": git_status(project_path)}

    def set_enabled(self, body: dict[str, Any]) -> dict[str, Any]:
        project_path = self._project_path()
        if project_path is None:
            return {"ok": False, "error": "Load an HSF project before changing Git settings.", "git": empty_git_status()}
        enabled = bool(body.get("enabled"))
        if enabled and not is_git_repo(project_path):
            return {"ok": False, "error": "Initialize Git before enabling it.", "git": git_status(project_path)}
        write_git_settings(project_path, enabled=enabled)
        return {"ok": True, "git": git_status(project_path)}

    def commit(self, body: dict[str, Any]) -> dict[str, Any]:
        project_path = self._project_path()
        if project_path is None:
            return {"ok": False, "error": "Load an HSF project before committing.", "git": empty_git_status()}
        status = git_status(project_path)
        if not status["initialized"]:
            return {"ok": False, "error": "Initialize Git before committing.", "git": status}
        if not status["enabled"]:
            return {"ok": False, "error": "Enable Git before committing.", "git": status}
        message = str(body.get("message") or "").strip() or "OpenBrep HSF checkpoint"
        if self.session.project is not None:
            self.session.project.save_to_disk()
        add_result = run_git(project_path, ["add", "-A"])
        if add_result.returncode != 0:
            return {"ok": False, "error": git_error(add_result), "git": git_status(project_path)}
        if not git_status(project_path)["dirty"]:
            return {"ok": True, "message": "No changes to commit.", "git": git_status(project_path)}
        commit_result = run_git(
            project_path,
            [
                "-c",
                "user.name=OpenBrep Workbench",
                "-c",
                "user.email=openbrep-workbench@local.invalid",
                "commit",
                "-m",
                message,
            ],
        )
        if commit_result.returncode != 0:
            return {"ok": False, "error": git_error(commit_result), "git": git_status(project_path)}
        return {"ok": True, "git": git_status(project_path)}

    def _project_path(self) -> Path | None:
        if self.session.source_path is None:
            return None
        return Path(self.session.source_path)


def git_status(project_path: Path) -> dict[str, Any]:
    initialized = is_git_repo(project_path)
    enabled = read_git_settings(project_path).get("enabled", False)
    changes = git_changes(project_path) if initialized else []
    return {
        "enabled": bool(enabled),
        "initialized": initialized,
        "dirty": bool(changes),
        "changes": changes,
        "last_commit": last_commit(project_path) if initialized else "",
    }


def empty_git_status() -> dict[str, Any]:
    return {
        "enabled": False,
        "initialized": False,
        "dirty": False,
        "changes": [],
        "last_commit": "",
    }


def is_git_repo(project_path: Path) -> bool:
    return (project_path / ".git").exists()


def read_git_settings(project_path: Path) -> dict[str, Any]:
    path = project_path / GIT_SETTINGS_PATH
    if not path.exists():
        return {"enabled": False}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": False}


def write_git_settings(project_path: Path, *, enabled: bool) -> None:
    path = project_path / GIT_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": enabled}, indent=2, sort_keys=True), encoding="utf-8")


def git_changes(project_path: Path) -> list[str]:
    result = run_git(project_path, ["status", "--porcelain"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def last_commit(project_path: Path) -> str:
    result = run_git(project_path, ["rev-parse", "--short", "HEAD"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def run_git(project_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_path,
        text=True,
        capture_output=True,
        check=False,
    )


def git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "Git command failed.").strip()
