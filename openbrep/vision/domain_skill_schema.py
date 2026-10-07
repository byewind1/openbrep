"""Adapt data-only Domain Skill manifests into the Vision Harness schema contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openbrep.domain_skills import DomainSkill, DomainSkillRegistry
from openbrep.vision.schema_registry import VisionSchema

_LATTICE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "opening.shape": ("opening.shape", "opening_shape"),
    "pattern.family": ("pattern.family", "pattern_family"),
    "pattern.kind": ("pattern.kind", "grid_topology.kind"),
    "pattern.rows": ("pattern.rows", "grid_topology.rows"),
    "pattern.cols": ("pattern.cols", "grid_topology.cols"),
    "pattern.cell_description": ("pattern.cell_description", "grid_topology.cell_desc"),
    "pattern.bar_width_ratio": ("pattern.bar_width_ratio", "bar_width_ratio"),
    "frame.bar_ratio": ("frame.bar_ratio", "frame_bar_ratio"),
    "symmetry.group": ("symmetry.group", "symmetry_group"),
    "pattern.motif_features": ("pattern.motif_features", "motif_features"),
}


def normalize_domain_observation_fields(skill_id: str, fields: dict) -> dict:
    """Map legacy extraction aliases onto stable Domain Skill field paths."""
    if skill_id != "lattice_window":
        return dict(fields)
    grid = fields.get("grid_topology") if isinstance(fields.get("grid_topology"), dict) else {}
    declarations = DomainSkillRegistry.builtin().load(skill_id)
    known_paths = (
        set(declarations.skill.manifest["observation"]["fields"])
        if declarations.ok and declarations.skill is not None else set()
    )
    normalized: dict[str, Any] = {}
    aliases = dict(_LATTICE_FIELD_ALIASES)
    aliases.update({path: (path,) for path in known_paths - set(aliases)})
    for path, candidates in aliases.items():
        if path not in known_paths:
            continue
        for candidate in candidates:
            if candidate in fields:
                normalized[path] = fields[candidate]
                break
            if candidate.startswith("grid_topology.") and candidate.split(".", 1)[1] in grid:
                normalized[path] = grid[candidate.split(".", 1)[1]]
                break
    return normalized


def domain_observation_aliases(skill_id: str) -> dict[str, tuple[str, ...]]:
    return dict(_LATTICE_FIELD_ALIASES) if skill_id == "lattice_window" else {}


@dataclass(frozen=True)
class DomainVisionSelection:
    schema: VisionSchema | None
    skill_id: str | None
    status: str  # active | unverified | unsupported | ambiguous | invalid
    version: str | None = None


def _schema_type(domain_type: str) -> str:
    return {
        "length": "number",
        "angle": "number",
        "count": "integer",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "enum": "enum",
        "string": "string",
        "material": "string",
        "state": "string",
        "array": "array",
    }.get(domain_type, "string")


def schema_from_skill(skill: DomainSkill) -> VisionSchema:
    """Build a strict extraction schema; Skill data supplies WHAT, never executable code."""
    declarations = skill.manifest["observation"]["fields"]
    from openbrep.vision.schema_registry import load_all_schemas

    legacy_schema = load_all_schemas().get(skill.skill_id)
    fields: dict[str, dict[str, Any]] = dict(legacy_schema.fields) if legacy_schema else {}
    fields.pop("gdl_strategy", None)  # Geometry command truth belongs to Plan, not image extraction.
    for path, declaration in declarations.items():
        field = {"type": _schema_type(str(declaration["type"]))}
        for key in ("unit", "enum_values"):
            if key in declaration:
                field["values" if key == "enum_values" else key] = declaration[key]
        fields[path] = field

    prompt_lines = [
        f"你正在使用领域 Skill `{skill.skill_id}` v{skill.version} 的视觉观察 schema。",
    ]
    if legacy_schema:
        # Retain proven first-party extraction coverage while adding typed domain fields.
        legacy_instructions = legacy_schema.extract_prompt.split("【P5c 输出契约】", 1)[0].strip()
        legacy_instructions = "\n".join(
            line for line in legacy_instructions.splitlines()
            if "gdl_strategy" not in line
        )
        if legacy_instructions:
            prompt_lines.extend(["兼容的既有构件观察要求：", legacy_instructions])
    prompt_lines.extend([
        "只记录原图能支持的构件事实，不生成 GDL 命令、尺寸猜测或不可见结构。",
        "计数和拓扑只在图像可辨时填写；遮挡、分辨率不足或视角缺失一律输出 null。",
        "长度使用米，角度使用度；图中没有标注的尺寸只能依据清晰比例关系描述，不能伪装为测量值。",
        "每个字段都需给出 confidence (high/low/unknown) 和简短 evidence；无法判断时值为 null。",
        "按下列字段逐项提取，不得增加字段：",
    ])
    for path, declaration in declarations.items():
        unit = declaration.get("unit")
        suffix = f"，单位 {unit}" if unit else ""
        policy = declaration.get("unknown_policy", "preserve")
        description = declaration.get("description")
        detail = f"：{description}" if description else ""
        prompt_lines.append(f"- {path}: {_schema_type(declaration['type'])}{suffix}{detail}；unknown_policy={policy}")
    for variation in skill.manifest.get("allowed_variations", []):
        prompt_lines.append(f"允许差异：{variation}")
    prompt_lines.extend([
        "输出 JSON envelope：{\"fields\":{字段路径:值},\"confidence\":{字段路径:\"high|low|unknown\"},",
        "\"evidence\":{字段路径:\"可见依据\"},\"raw_description\":\"简短补充\"}。",
    ])
    aliases = [str(value) for value in skill.manifest.get("aliases", [])]
    return VisionSchema(
        name=skill.skill_id,
        trigger_keywords=aliases,
        extract_prompt="\n".join(prompt_lines),
        fields=fields,
        required=list(dict.fromkeys(
            (legacy_schema.required if legacy_schema else [])
            + [path for path, declaration in declarations.items() if declaration.get("required") is True]
        )),
        critic_checks=list(legacy_schema.critic_checks) if legacy_schema else [],
        editable_fields=list(dict.fromkeys(
            (legacy_schema.editable_fields if legacy_schema else []) + list(fields)
        )),
        domain_skill_id=skill.skill_id,
        domain_skill_status="active" if skill.status in {"active", "verified"} else "unverified",
        domain_skill_version=skill.version,
    )


def select_domain_vision_schema(
    instruction: str,
    *,
    intent: str,
    project_hints: str = "",
    registry: DomainSkillRegistry | None = None,
) -> DomainVisionSelection:
    registry = registry or DomainSkillRegistry.builtin()
    query = "\n".join(part for part in (instruction, project_hints) if part)
    skills = registry.select(query, intent=intent)
    if not skills:
        return DomainVisionSelection(None, None, "unsupported")
    if len(skills) != 1:
        return DomainVisionSelection(None, None, "ambiguous")
    skill = skills[0]
    schema = schema_from_skill(skill)
    return DomainVisionSelection(
        schema=schema,
        skill_id=skill.skill_id,
        status=schema.domain_skill_status,
        version=skill.version,
    )
