from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable


class WorkbenchCompilerService:
    def __init__(
        self,
        session: Any,
        *,
        real_compiler_factory: Callable[[str | None], Any],
        mock_compiler_factory: Callable[[], Any],
    ) -> None:
        self.session = session
        self.real_compiler_factory = real_compiler_factory
        self.mock_compiler_factory = mock_compiler_factory

    def compile_mock(self, body: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter()
        self.session.project.save_to_disk()
        hsf_dir = self.session.project.root if self.session.source_path is None else self.session.source_path
        output_dir = resolve_output_dir(body, self.session.output_dir, hsf_dir.parent / "output")
        output_dir = output_dir.expanduser().resolve()
        output_gsm = output_dir / f"{self.session.project.name}.gsm"
        result = self.mock_compiler_factory().hsf2libpart(str(hsf_dir), str(output_gsm))
        duration_ms = int((time.perf_counter() - start) * 1000)
        output_path = result.output_path or str(output_gsm)
        self.session.last_compile_output_path = output_path
        return {
            "ok": True,
            "success": bool(result.success),
            "mode": "mock",
            "issues": compile_issues_from_result(result),
            "duration_ms": duration_ms,
            "output_path": output_path,
            "gsm_size_bytes": file_size_or_none(output_gsm),
            "parameter_count": len(self.session.project.parameters or []),
        }

    def compile_project(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before compiling."}

        output_dir = resolve_output_dir(body, self.session.output_dir, self.session.source_path.parent / "output")
        output_dir = output_dir.expanduser().resolve()
        output_gsm = output_dir / f"{self.session.project.name}.gsm"
        compiler_mode = str(body.get("compiler_mode") or self.session.compiler_mode)
        converter_path = body.get("converter_path")
        if converter_path is None:
            converter_path = self.session.converter_path
        compiler = (
            self.real_compiler_factory(str(converter_path) if converter_path else None)
            if compiler_mode == "lp"
            else self.mock_compiler_factory()
        )

        self.session.project.save_to_disk()
        result = compiler.hsf2libpart(str(self.session.source_path), str(output_gsm))
        output_path = result.output_path or str(output_gsm)
        self.session.last_compile_output_path = output_path
        return {
            "ok": bool(result.success),
            "compile": {
                "success": bool(result.success),
                "mode": result.mode or ("lp" if compiler_mode == "lp" else "mock"),
                "output_path": output_path,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "errors": result.errors,
                "warnings": result.warnings,
                "gsm_size_bytes": file_size_or_none(output_gsm),
                "parameter_count": len(self.session.project.parameters or []),
            },
            **({} if result.success else {"error": result.stderr or "Compile failed"}),
        }


def compile_issues_from_result(result: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for severity, lines in (("error", result.errors), ("warning", result.warnings)):
        for raw in lines or []:
            script, line, message = parse_compile_issue(str(raw))
            issues.append({
                "severity": severity,
                "script": script,
                "line": line,
                "message": message,
            })
    return issues


def resolve_output_dir(body: dict[str, Any], session_output_dir: str, fallback: Path) -> Path:
    configured = str(body.get("output_dir") or session_output_dir or "").strip()
    if configured:
        return Path(configured)
    return fallback


def parse_compile_issue(raw: str) -> tuple[str, int | None, str]:
    match = re.search(r"Error in ([^:]+):\s*(.+)", raw)
    if match:
        return f"scripts/{match.group(1)}", None, match.group(2)
    match = re.search(r"([^:\s]+\.gdl).*?line\s+(\d+)[:\s-]*(.+)", raw, re.IGNORECASE)
    if match:
        return f"scripts/{Path(match.group(1)).name}", int(match.group(2)), match.group(3).strip()
    return "", None, raw


def file_size_or_none(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None
