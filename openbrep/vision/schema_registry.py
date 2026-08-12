"""
schema_registry — Vision Harness Schema Registry（P5b，设计 §4）

每个 schema 一个 YAML（openbrep/vision/schemas/<name>.yaml），字段：

    name: str                      # 与文件名一致
    trigger.keywords: list[str]    # 用户文本命中即选该 schema（generic 恒为空）
    extract_prompt: str            # S2 定向提取的指令本体
    fields: dict                   # 字段定义 {key: {type, hint}}（顺序即渲染顺序）
    required: list[str]            # 必需字段（P5c 校验用；本单只记录）
    critic_checks: list[str]       # S3 必核字段（P5c 用；本单只记录）

加载即校验必需键；坏 YAML / 缺键 / 字段类型不符 → 报清晰错误（带文件名），
不静默吞掉。generic 是 registry 的一份普通 schema，但提取逻辑走
image_to_plan 平移路径（见 harness.py），本模块不特殊处理它。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_REQUIRED_KEYS = {"name", "trigger", "extract_prompt", "fields", "required", "critic_checks"}


@dataclass(frozen=True)
class VisionSchema:
    name: str
    trigger_keywords: list[str] = field(default_factory=list)
    extract_prompt: str = ""
    fields: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    critic_checks: list[str] = field(default_factory=list)


def load_schema(name: str) -> VisionSchema:
    """按名字典加载单个 schema（不做缓存，调用方自行管理）。"""
    return load_schemas_from_dir(_SCHEMAS_DIR)[name]


def load_all_schemas() -> dict[str, VisionSchema]:
    """加载 schemas/ 下全部 YAML，按文件名排序保证确定性（跨 schema 关键词优先级）。"""
    return load_schemas_from_dir(_SCHEMAS_DIR)


def load_schemas_from_dir(schema_dir: str | Path) -> dict[str, VisionSchema]:
    """从任意目录加载全部 schema YAML；坏文件报错并指名文件。"""
    directory = Path(schema_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Schema directory not found: {directory}")
    schemas: dict[str, VisionSchema] = {}
    for path in sorted(directory.glob("*.yaml")):
        schema = _load_schema_file(path)
        if schema.name in schemas:
            raise ValueError(
                f"Duplicate schema name {schema.name!r} in {directory} "
                f"({path.name} collides with an earlier file)"
            )
        schemas[schema.name] = schema
    return schemas


def get_schema(name: str, schemas: dict[str, VisionSchema] | None = None) -> VisionSchema:
    """按名字典查询；不存在 → KeyError（错误指名可用 schema）。"""
    if schemas is None:
        schemas = load_all_schemas()
    if name not in schemas:
        available = ", ".join(sorted(schemas))
        raise KeyError(f"Unknown vision schema {name!r}. Available schemas: {available}")
    return schemas[name]


def _load_schema_file(path: Path) -> VisionSchema:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Bad YAML in vision schema {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Vision schema {path.name} must be a YAML mapping, got {type(raw).__name__}")

    missing = _REQUIRED_KEYS - set(raw)
    if missing:
        raise ValueError(
            f"Vision schema {path.name} missing required keys: {', '.join(sorted(missing))}"
        )

    name = str(raw["name"] or "").strip()
    if not name:
        raise ValueError(f"Vision schema {path.name}: 'name' must be a non-empty string")
    if name != path.stem:
        raise ValueError(
            f"Vision schema {path.name}: 'name' ({name!r}) must match filename stem ({path.stem!r})"
        )

    trigger = raw.get("trigger") or {}
    if not isinstance(trigger, dict):
        raise ValueError(f"Vision schema {path.name}: 'trigger' must be a mapping")
    keywords = trigger.get("keywords") or []
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        raise ValueError(f"Vision schema {path.name}: 'trigger.keywords' must be a list of strings")

    extract_prompt = str(raw.get("extract_prompt") or "").strip()
    if not extract_prompt:
        raise ValueError(f"Vision schema {path.name}: 'extract_prompt' must be a non-empty string")

    fields = raw.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"Vision schema {path.name}: 'fields' must be a mapping")

    required = raw.get("required") or []
    if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
        raise ValueError(f"Vision schema {path.name}: 'required' must be a list of strings")
    unknown_required = [k for k in required if k not in fields]
    if unknown_required:
        raise ValueError(
            f"Vision schema {path.name}: required fields not declared in 'fields': {', '.join(unknown_required)}"
        )

    critic_checks = raw.get("critic_checks") or []
    if not isinstance(critic_checks, list) or not all(isinstance(k, str) for k in critic_checks):
        raise ValueError(f"Vision schema {path.name}: 'critic_checks' must be a list of strings")

    return VisionSchema(
        name=name,
        trigger_keywords=keywords,
        extract_prompt=extract_prompt,
        fields=fields,
        required=required,
        critic_checks=critic_checks,
    )
