"""
Tracer — records task execution traces as JSON files.

Each pipeline.execute() call writes one trace file to ./traces/.
Useful for debugging, benchmarking, and auditing generation quality.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Tracer:
    """Writes one JSON trace file per task execution."""

    def __init__(self, trace_dir: str = "./traces"):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def record(self, request, result) -> Path:
        """
        Write a trace JSON for a completed task.

        Args:
            request: TaskRequest
            result:  TaskResult

        Returns:
            Path to the written trace file.
        """
        task_id = f"t_{datetime.now():%Y%m%d_%H%M%S_%f}"
        trace = {
            "task_id": task_id,
            "intent": result.intent or (request.intent if hasattr(request, "intent") else ""),
            "input_summary": (request.user_input or "")[:200],
            "success": result.success,
            "scripts_generated": list(result.scripts.keys()) if result.scripts else [],
            "has_plain_text": bool(result.plain_text),
            "has_object_plan": bool(getattr(result, "object_plan", None)),
            "object_type": (getattr(result, "object_plan", {}) or {}).get("object_type"),
            "has_compile": result.compile_result is not None,
            "compile_pass": result.compile_result.success if result.compile_result else None,
            "error": result.error,
            "timestamp": datetime.now().isoformat(),
        }
        path = self.trace_dir / f"{task_id}.json"
        try:
            path.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Tracer: failed to write trace file: %s", exc)
        return path
