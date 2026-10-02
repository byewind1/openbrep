#!/usr/bin/env python3
"""Run the ST09 offline spiral-stair workflow and print machine-readable evidence."""

from __future__ import annotations

import json
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _workflow():
    path = Path(__file__).resolve().parents[1] / "tests" / "test_spiral_stair_workflow.py"
    spec = importlib.util.spec_from_file_location("st09_workflow_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_continuous_workflow


def main() -> int:
    run_continuous_workflow = _workflow()
    with tempfile.TemporaryDirectory(prefix="st09_spiral_") as raw:
        result = run_continuous_workflow(Path(raw))
    print(json.dumps({"suite": "ST09", "workflow": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
