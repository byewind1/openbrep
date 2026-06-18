#!/usr/bin/env python3
"""Smoke test React Workbench's image-to-project path through WorkbenchSession.

This is an opt-in real-model smoke. It does not mock the pipeline when run from
the CLI; if the configured LLM/provider cannot handle vision, the JSON output
will expose the normalized Workbench API error.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openbrep.workbench_api import WorkbenchSession


_ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01"
    b"\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def build_test_image_b64() -> str:
    return base64.b64encode(_ONE_BY_ONE_PNG).decode("ascii")


def run_smoke(
    *,
    output_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    require_config: bool = True,
    session_factory: Callable[..., Any] = WorkbenchSession,
) -> dict[str, Any]:
    config = Path(config_path).expanduser() if config_path else Path.home() / ".openbrep" / "config.toml"
    if require_config and not config.exists():
        return {
            "ok": True,
            "status": "skip",
            "reason": f"config not found: {config}",
        }

    if output_dir is None:
        temp_root = tempfile.TemporaryDirectory(prefix="openbrep_vision_smoke_")
        root = Path(temp_root.name)
    else:
        temp_root = None
        root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    try:
        session = session_factory(config_path=config)
        response = session.route(
            "POST",
            "/api/project/create",
            {
                "prompt": "根据这张极简参考图生成一个参数化小方块对象，用 BLOCK 实现。",
                "output_dir": str(root),
                "project_name": "VisionSmokeBlock",
                "image_b64": build_test_image_b64(),
                "image_mime": "image/png",
            },
        )
    finally:
        if temp_root is not None:
            temp_root.cleanup()

    if not response.get("ok"):
        return {
            "ok": False,
            "status": "fail",
            "error": response.get("error") or "vision smoke failed",
            "events": response.get("events", []),
        }

    return {
        "ok": True,
        "status": "pass",
        "project_path": (response.get("project") or {}).get("path", ""),
        "changed_files": (response.get("assistant") or {}).get("changed_files", []),
        "reply": (response.get("assistant") or {}).get("reply", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OpenBrep Workbench vision smoke.")
    parser.add_argument("--config", default=None, help="Path to OpenBrep config.toml")
    parser.add_argument("--output-dir", default=None, help="Directory for generated smoke project")
    parser.add_argument("--no-require-config", action="store_true", help="Run even when config.toml is missing")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    result = run_smoke(
        output_dir=args.output_dir,
        config_path=args.config,
        require_config=not args.no_require_config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
