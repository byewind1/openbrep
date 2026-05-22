"""OpenBrep GDL quality scorecard.

Usage:
  python evals/scorecards/run_scorecard.py
  python evals/scorecards/run_scorecard.py --mode mock
  python evals/scorecards/run_scorecard.py --output evals/scorecards/results/
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.prepare_fixtures import BROKEN_DIR, VALID_DIR, prepare_broken_fixtures
from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.static_checker import StaticChecker


@dataclass(frozen=True)
class ScorecardCase:
    id: str
    fixture_path: Path
    expected_ok: bool


def run_scorecard(
    *,
    mode: str = "mock",
    valid_dir: Path = VALID_DIR,
    broken_dir: Path = BROKEN_DIR,
    output_dir: Path | None = None,
) -> dict:
    """Run all scorecard suites and optionally write a timestamped JSON report."""
    if mode not in {"mock", "real", "auto"}:
        raise ValueError("mode must be one of: mock, real, auto")

    compiler, effective_mode, skip_reason = _resolve_compiler(mode)
    generated_at = datetime.now().isoformat(timespec="seconds")
    result = {
        "generated_at": generated_at,
        "mode": mode,
        "effective_mode": effective_mode,
        "skipped": bool(skip_reason),
        "skip_reason": skip_reason,
        "environment": _environment_metadata(compiler),
    }
    if skip_reason:
        result["suites"] = {}
        result["summary"] = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 1,
            "pass_rate": 0.0,
        }
        _write_result_if_requested(result, output_dir)
        return result

    prepare_broken_fixtures(valid_dir, broken_dir)
    valid_cases = [
        ScorecardCase(path.stem, path, True)
        for path in sorted(valid_dir.glob("*.gdl"))
    ]
    broken_cases = [
        ScorecardCase(path.stem, path, False)
        for path in sorted(broken_dir.glob("*.gdl"))
    ]
    result["suites"] = {
        "fixture_compile": _run_cases(valid_cases, compiler),
        "broken_detection": _run_cases(broken_cases, compiler),
    }
    result["summary"] = _summarize(result["suites"].values())
    result["summary"]["skipped"] = 0

    _write_result_if_requested(result, output_dir)
    return result


def _write_result_if_requested(result: dict, output_dir: Path | None) -> None:
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"scorecard_{stamp}.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["output_path"] = str(output_path)


def _resolve_compiler(mode: str):
    if mode == "mock":
        return MockHSFCompiler(), "mock", ""

    real_compiler = HSFCompiler()
    if real_compiler.is_available:
        return real_compiler, "real", ""

    reason = "LP_XMLConverter not found. Install Archicad or set CONVERTER_PATH/config compiler.path."
    if mode == "real":
        return real_compiler, "real", reason
    return MockHSFCompiler(), "mock", ""


def _environment_metadata(compiler) -> dict:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "converter_path": getattr(compiler, "converter_path", None),
    }


def _run_cases(cases: list[ScorecardCase], compiler) -> dict:
    records = []
    for case in cases:
        records.append(_run_case(case, compiler))
    total = len(records)
    passed = sum(1 for record in records if record["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": records,
    }


def _run_case(case: ScorecardCase, compiler) -> dict:
    with tempfile.TemporaryDirectory(prefix="openbrep-scorecard-") as tmpdir:
        project = _project_from_fixture(case.fixture_path, tmpdir)
        hsf_dir = project.save_to_disk()
        static_result = StaticChecker().check(project)
        output_gsm = Path(tmpdir) / "output" / f"{project.name}.gsm"
        compile_result = compiler.hsf2libpart(str(hsf_dir), str(output_gsm))

    actual_ok = static_result.passed and compile_result.success
    passed = actual_ok is case.expected_ok
    return {
        "id": case.id,
        "fixture": str(case.fixture_path),
        "expected_ok": case.expected_ok,
        "actual_ok": actual_ok,
        "passed": passed,
        "static_ok": static_result.passed,
        "static_errors": [
            {"type": err.check_type, "file": err.file, "detail": err.detail}
            for err in static_result.errors
        ],
        "compile_ok": compile_result.success,
        "compile_mode": compile_result.mode,
        "compile_exit_code": compile_result.exit_code,
        "compile_stderr": compile_result.stderr,
        "compile_output_path": compile_result.output_path,
    }


def _project_from_fixture(fixture_path: Path, work_dir: str | Path) -> HSFProject:
    project = HSFProject.create_new(fixture_path.stem, work_dir=str(work_dir))
    project.scripts[ScriptType.SCRIPT_3D] = fixture_path.read_text(encoding="utf-8")
    project.scripts[ScriptType.SCRIPT_2D] = (
        "HOTSPOT2 0, 0\n"
        "HOTSPOT2 A, 0\n"
        "HOTSPOT2 0, B\n"
        "HOTSPOT2 A, B\n"
        "PROJECT2 3, 270, 2\n"
    )
    return project


def _summarize(suites: Iterable[dict]) -> dict:
    total = 0
    passed = 0
    for suite in suites:
        total += int(suite.get("total", 0))
        passed += int(suite.get("passed", 0))
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OpenBrep GDL scorecards")
    parser.add_argument("--mode", default="mock", choices=["mock", "real", "auto"], help="scorecard execution mode")
    parser.add_argument("--output", type=Path, default=None, help="directory for JSON result")
    args = parser.parse_args(argv)

    result = run_scorecard(mode=args.mode, output_dir=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("skipped"):
        return 0
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
