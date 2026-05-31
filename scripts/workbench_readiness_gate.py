#!/usr/bin/env python3
"""Run the React Workbench readiness gate.

Default mode is intentionally targeted and fast enough for frequent local use.
Use ``--full`` before merge/release-level decisions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    cwd: Path = ROOT


@dataclass(frozen=True)
class StepResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "returncode": self.returncode,
            "command": self.command,
            "stdout_tail": self.stdout[-2000:],
            "stderr_tail": self.stderr[-2000:],
        }


def build_plan(*, full: bool = False) -> list[Step]:
    backend = (
        Step("backend full tests", ["python", "-m", "pytest", "tests/", "-q"])
        if full
        else Step(
            "backend workbench api",
            ["python", "-m", "pytest", "tests/test_workbench_api.py", "tests/test_workbench_api_parameters.py", "-q"],
        )
    )
    steps = [
        backend,
        Step("backend vision smoke tests", ["python", "-m", "pytest", "tests/test_workbench_vision_smoke.py", "-q"]),
        Step("frontend tests", ["npm", "run", "test", "--", "--run"], cwd=ROOT / "frontend"),
        Step("frontend build", ["npm", "run", "build"], cwd=ROOT / "frontend"),
        Step("vision smoke", ["python", "scripts/workbench_vision_smoke.py", "--pretty"]),
    ]
    if full:
        steps.append(Step("browser smoke", ["python", "scripts/workbench_browser_smoke.py", "--pretty"]))
    return steps


def run_step(step: Step) -> StepResult:
    completed = subprocess.run(
        step.command,
        cwd=str(step.cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return StepResult(
        name=step.name,
        command=step.command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_gate(
    *,
    root: str | Path = ROOT,
    full: bool = False,
    runner: Callable[[Step], StepResult] = run_step,
) -> dict:
    # root is kept for testability and future workspace overrides.
    _root = Path(root)
    steps = build_plan(full=full)
    results = [runner(step) for step in steps]
    return {
        "ok": all(result.ok for result in results),
        "mode": "full" if full else "targeted",
        "root": str(_root),
        "steps": [result.to_dict() for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run React Workbench readiness gate.")
    parser.add_argument("--full", action="store_true", help="Run full Python test suite instead of targeted backend tests")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    result = run_gate(full=args.full)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
