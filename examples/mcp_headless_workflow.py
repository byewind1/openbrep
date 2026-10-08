#!/usr/bin/env python3
"""Run OpenBrep's deterministic MCP engineering checks without model credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openbrep import mcp_tools


def run_headless_workflow(project_path: str, *, compile_mode: str = "mock") -> dict[str, Any]:
    """Load an HSF project and collect compile, semantic and local preview evidence."""
    capabilities = mcp_tools.capabilities()
    loaded = mcp_tools.load_project(project_path)
    if not loaded.get("ok"):
        return {"ok": False, "capabilities": capabilities, "load": loaded}

    compile_result = mcp_tools.compile_hsf(project_path, mode=compile_mode)
    semantic_result = mcp_tools.semantic_verify(project_path)
    visual_result = mcp_tools.render_evidence(project_path)
    return {
        "ok": all(item.get("ok") for item in (compile_result, semantic_result, visual_result)),
        "capabilities": capabilities,
        "project": loaded,
        "checks": {
            "compile": compile_result,
            "semantic": semantic_result,
            "visual": visual_result,
        },
        "delivery": {
            "source_fingerprint": loaded.get("source_fingerprint"),
            "compile_mode": compile_result.get("mode"),
            "compile_success": compile_result.get("success"),
            "compile_is_real": compile_result.get("mode") == "real",
            "semantic_passed": semantic_result.get("passed"),
            "visual_evidence_id": visual_result.get("evidence_id"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="HSF project directory")
    parser.add_argument(
        "--compile-mode", choices=("mock", "auto", "real"), default="mock",
        help="mock is deterministic structural validation, not a real Archicad compile (default: mock)",
    )
    args = parser.parse_args(argv)
    result = run_headless_workflow(str(args.project), compile_mode=args.compile_mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
