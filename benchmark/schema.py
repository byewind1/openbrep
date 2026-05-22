from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SuccessCriteria:
    compile_pass: bool = True
    required_params: list[str] = field(default_factory=list)
    required_scripts: list[str] = field(default_factory=list)
    geometry_check: str = ""


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    category: str
    complexity: str
    description: str
    success_criteria: SuccessCriteria
    expected_difficulty: str = ""
    expected_pass: str | bool | None = None


def load_benchmark_task(path: str | Path) -> BenchmarkTask:
    """Load a benchmark task YAML file into a typed task object."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return benchmark_task_from_dict(data)


def benchmark_task_from_dict(data: dict[str, Any]) -> BenchmarkTask:
    criteria = data.get("success_criteria") or {}
    return BenchmarkTask(
        id=str(data["id"]),
        category=str(data.get("category") or ""),
        complexity=str(data.get("complexity") or ""),
        description=str(data.get("description") or ""),
        success_criteria=SuccessCriteria(
            compile_pass=bool(criteria.get("compile_pass", True)),
            required_params=list(criteria.get("required_params") or []),
            required_scripts=list(criteria.get("required_scripts") or []),
            geometry_check=str(criteria.get("geometry_check") or ""),
        ),
        expected_difficulty=str(data.get("expected_difficulty") or ""),
        expected_pass=data.get("expected_pass"),
    )
