"""
extraction_store — 提取工件存储（P5d-1，设计 D7 内容哈希寻址）

每次 schema 提取落盘为项目工件：``<root>/.openbrep/vision/extraction-<sha256[:12]>.json``。
键是**预处理字节的 sha256**（路径可变、内容不变应复用），不是路径。
JSON 内容（设计 D7）：``{schema_name, fields, confidence, corrections,
degraded, critic_degraded, model, created_at}``。

- ``save_extraction``：写盘（同 sha256 已存在 → 覆盖写，同内容幂等）；
  sha256 为空（无字节图）跳过落盘，不计错误。
- ``load_extraction``：按哈希读取（P5e 复用铺路，本单只写+读测试）。
- ``list_extraction_hashes``：当前项目全部 extraction 哈希
  （revision manifest 简化口径——记录当前项目全部，D7）。
- ``plan_to_dict``：ModelingPlan → JSON 可序列化 dict（generic 的
  fields 内嵌 VisualStructure dataclass，统一 asdict 化），供 pipeline
  metadata 透出与事件 payload 共用，保证三处（事件/元数据/落盘）同构。

不依赖 LLM / prompt，纯存储层；import 面为零副作用（无外部依赖）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

VISION_SUBDIR = ".openbrep/vision"
EXTRACTION_PREFIX = "extraction-"
EXTRACTION_SUFFIX = ".json"
EXTRACTION_HASH_LEN = 12  # sha256 前 12 位（文件名键，D7）


def _vision_dir(project_root: str | Path) -> Path:
    return Path(project_root) / VISION_SUBDIR


def _extraction_path(project_root: str | Path, sha256: str) -> Path:
    return _vision_dir(project_root) / (
        f"{EXTRACTION_PREFIX}{sha256[:EXTRACTION_HASH_LEN]}{EXTRACTION_SUFFIX}"
    )


def extraction_hash_from_filename(name: str) -> Optional[str]:
    """``extraction-<sha256[:12]>.json`` → 12 位哈希；格式不符返回 None。"""
    if not name.startswith(EXTRACTION_PREFIX) or not name.endswith(EXTRACTION_SUFFIX):
        return None
    key = name[len(EXTRACTION_PREFIX) : -len(EXTRACTION_SUFFIX)]
    return key if key and len(key) == EXTRACTION_HASH_LEN else None


def list_extraction_hashes(project_root: str | Path) -> list[str]:
    """当前项目全部 extraction 哈希（manifest 简化口径：当前全部，D7）。"""
    vision_dir = _vision_dir(project_root)
    if not vision_dir.is_dir():
        return []
    hashes: list[str] = []
    for path in sorted(vision_dir.glob(f"{EXTRACTION_PREFIX}*{EXTRACTION_SUFFIX}")):
        key = extraction_hash_from_filename(path.name)
        if key:
            hashes.append(key)
    return hashes


def _jsonable(value: Any) -> Any:
    """任意值 → JSON 可序列化（dataclass → asdict，dict/list 递归）。"""
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def plan_to_dict(plan) -> dict[str, Any]:
    """ModelingPlan（或等价对象）→ JSON 可序列化 dict。

    供三处共用：pipeline ``TaskResult.metadata["vision_extractions"]`` 条目、
    harness ``vision_analysis_done`` 事件 payload、落盘 JSON 内容。
    generic 路径的 fields 内嵌 VisualStructure dataclass → asdict 化，
    保证 metadata / 事件 / 磁盘都是纯 JSON 形状。
    """
    source = list(getattr(plan, "source_images", None) or [])
    return {
        "schema_name": str(getattr(plan, "schema_name", "") or ""),
        "fields": _jsonable(getattr(plan, "fields", {}) or {}),
        "confidence": dict(getattr(plan, "confidence", {}) or {}),
        "corrections": list(getattr(plan, "corrections", []) or []),
        "degraded": bool(getattr(plan, "degraded", False)),
        "critic_degraded": bool(getattr(plan, "critic_degraded", False)),
        "raw_description": str(getattr(plan, "raw_description", "") or ""),
        # P5d-2：schema 元数据随提取透出（前端可编辑卡片据此决定可编辑字段）。
        "required": list(getattr(plan, "required", []) or []),
        "critic_checks": list(getattr(plan, "critic_checks", []) or []),
        "sha256": str(source[0] or "") if source else "",
    }


def save_extraction(project_root: str | Path, plan, *, model: str) -> Optional[Path]:
    """写 ``<root>/.openbrep/vision/extraction-<sha256[:12]>.json``（设计 D7）。

    Args:
        project_root: HSF 项目根目录（``.openbrep`` 元数据目录挂在它下面）。
        plan: ModelingPlan 或等价对象（schema_name/fields/confidence/
              corrections/degraded/critic_degraded/source_images）。
        model: 提取所用模型名（D7：JSON 里存模型名，换模型命中旧提取时
               可标记"由 X 模型提取"，本单只写不读复用判定）。

    Returns:
        落盘路径；sha256 为空（无字节图）时返回 None（跳过落盘，不计错误）。

    Raises:
        OSError: 目录创建或写盘失败（由调用方按 warning 处理，不阻断主流程）。
    """
    sha = str((getattr(plan, "source_images", None) or [None])[0] or "")
    if not sha:
        return None
    data = plan_to_dict(plan)
    data["model"] = model
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    path = _extraction_path(project_root, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_extraction(project_root: str | Path, sha256: str) -> Optional[dict]:
    """按哈希读取提取工件（P5e 复用铺路，本单只写+读测试）。

    传完整 sha256 或前 12 位均可（文件名键取前 12 位）；不存在或
    解析失败返回 None（解析失败只记日志不上抛）。
    """
    path = _extraction_path(project_root, sha256)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("extraction_store: load failed %s: %s", path, exc)
        return None
