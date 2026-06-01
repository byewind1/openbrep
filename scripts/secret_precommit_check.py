from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
from pathlib import Path


BLOCKED_FILENAMES = {"config.toml", ".env"}
BLOCKED_SUFFIXES = (".env",)
SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(api[_-]?key|secret|token)\b\s*=\s*['\"]([^'\"]+)['\"]")
PLACEHOLDER_RE = re.compile(r"(?i)^(|your[-_ ].*|test[-_ ].*|old[-_ ].*|new[-_ ].*|fake[-_ ].*|example[-_ ].*|placeholder|key)$")


def staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        check=True,
        capture_output=True,
    )
    return [path for path in result.stdout.decode("utf-8", errors="replace").split("\0") if path]


def staged_added_lines() -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        check=True,
        capture_output=True,
        text=True,
    )
    current_file = ""
    lines: list[tuple[str, str]] = []
    for raw in result.stdout.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw.removeprefix("+++ b/")
            continue
        if not raw.startswith("+") or raw.startswith("+++") or not current_file:
            continue
        lines.append((current_file, raw[1:]))
    return lines


def blocked_path_reason(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    if normalized.startswith(".worktrees/"):
        return "staged files under .worktrees/ are blocked"
    if name in BLOCKED_FILENAMES or any(name.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        if name.startswith("config.example") or name.endswith(".example"):
            return None
        return f"staged personal config file is blocked: {path}"
    return None


def secret_line_reason(path: str, line: str) -> str | None:
    if Path(path).name in {"config.example.toml", ".env.example"}:
        return None
    match = SECRET_ASSIGNMENT_RE.search(line)
    if not match:
        return None
    value = match.group(2).strip()
    if PLACEHOLDER_RE.match(value):
        return None
    if len(value) < 12:
        return None
    return f"possible secret assignment in {path}: {match.group(1)}"


def find_violations() -> list[str]:
    violations: list[str] = []
    for path in staged_paths():
        reason = blocked_path_reason(path)
        if reason:
            violations.append(reason)
    for path, line in staged_added_lines():
        reason = secret_line_reason(path, line)
        if reason:
            violations.append(reason)
    return violations


def install_hook() -> Path:
    git_dir = subprocess.check_output(["git", "rev-parse", "--git-common-dir"], text=True).strip()
    hook_dir = Path(git_dir).expanduser()
    if not hook_dir.is_absolute():
        root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
        hook_dir = root / hook_dir
    hooks_dir = hook_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    hook.write_text(
        "#!/usr/bin/env sh\n"
        "if [ -f scripts/secret_precommit_check.py ]; then\n"
        "  python scripts/secret_precommit_check.py\n"
        "elif [ -f .worktrees/react-workbench/scripts/secret_precommit_check.py ]; then\n"
        "  python .worktrees/react-workbench/scripts/secret_precommit_check.py\n"
        "else\n"
        "  echo 'secret pre-commit check is missing' >&2\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block personal config and obvious API keys from commits.")
    parser.add_argument("--install", action="store_true", help="Install this check as .git/hooks/pre-commit.")
    args = parser.parse_args(argv)

    if args.install:
        hook = install_hook()
        print(f"Installed pre-commit hook: {hook}")
        return 0

    violations = find_violations()
    if not violations:
        return 0

    print("Secret pre-commit check failed:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)
    print("Use config.example.toml or placeholders for committed examples.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
