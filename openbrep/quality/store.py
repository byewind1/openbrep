"""质量档案存储：不可变 per-run JSON，原子写，best-effort。

路径：``<project>/.openbrep/quality/runs/<run_id>.json``（source of truth）。
- 原子写：同目录 tmp 文件 + ``os.replace``；
- best-effort：任何失败只 ``logger.warning`` 并返回 None，绝不抛出；
- 项目未落盘（不是 HSF 目录结构）直接跳过（与 feedback 同一判定）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from openbrep.revisions import is_hsf_project_dir

if TYPE_CHECKING:
    from openbrep.quality.schema import QualityRecord

logger = logging.getLogger(__name__)


def quality_runs_dir(project_root: Any) -> Path:
    return Path(project_root) / ".openbrep" / "quality" / "runs"


def record_path(project_root: Any, run_id: str) -> Path:
    return quality_runs_dir(project_root) / f"{run_id}.json"


def write_record(project_root: Any, record: QualityRecord) -> Optional[Path]:
    """写一份质量档案。返回写盘路径；跳过/失败返回 None（只 warning）。"""
    try:
        root = Path(project_root)
        if not is_hsf_project_dir(root):
            logger.warning("quality store: project not on disk, skip: %s", root)
            return None
        record.validate()
        runs_dir = quality_runs_dir(root)
        runs_dir.mkdir(parents=True, exist_ok=True)
        target = record_path(root, record.run_id)
        tmp = runs_dir / f".{record.run_id}.tmp"
        tmp.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)  # 同目录 rename，原子
        return target
    except Exception as exc:  # best-effort：任何失败只降级为 warning
        logger.warning("quality store: write failed (best-effort): %s", exc)
        return None


def load_records(scan_root: Any) -> list[dict]:
    """扫描 ``scan_root`` 下所有项目的质量档案（含 scan_root 自身是项目的情况）。

    逐份 ``json.load``；解析失败的文件跳过并 warning（不中断整体扫描）。
    """
    root = Path(scan_root)
    records: list[dict] = []
    candidates: list[Path] = []
    direct = quality_runs_dir(root)
    if direct.is_dir():
        candidates.extend(sorted(direct.glob("*.json")))
    for runs in sorted(root.glob("**/.openbrep/quality/runs")):
        if runs.is_dir() and runs != direct:
            candidates.extend(sorted(runs.glob("*.json")))
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("quality store: skip unparsable record %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            records.append(data)
    return records
