"""Read-only adapter from imported HSF facts to a reviewable ObjectSpec candidate.

This module records what the imported source says. It does not infer geometry,
materials, constraints, or domain behavior; those remain owned by a selected
domain Skill and its evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from openbrep.contracts.object_spec import parse_object_spec
from openbrep.source_fingerprint import compute_source_fingerprint


def build_import_candidate(project: Any) -> dict[str, Any]:
    """Build a deterministic observation/spec proposal from an HSF project.

    Current parameter values are facts in the Observation only. ObjectSpec
    carries types, units, descriptions and source identity, never a second
    mutable copy of current HSF values.
    """
    fingerprint = compute_source_fingerprint(project.root)
    source_ref = f"hsf-source:{fingerprint}"
    digest = hashlib.sha256(
        f"{project.name}\0{fingerprint}".encode("utf-8")
    ).hexdigest()
    observation_id = f"obs_import_{digest[:16]}"
    spec_id = f"spec_import_{digest[:16]}"

    items: list[dict[str, Any]] = [
        {"field_path": "source.object_name", "status": "observed", "value": str(project.name),
         "confidence": "high", "note": "来自已导入 HSF 项目名称"},
        {"field_path": "source.scripts", "status": "observed",
         "value": sorted(script.value for script, content in project.scripts.items() if content),
         "confidence": "high", "note": "只记录 HSF 中存在的脚本分支"},
        {"field_path": "source.subtype_guids", "status": "observed",
         "value": list(getattr(project, "subtype_guids", []) or []),
         "confidence": "high", "note": "来自 HSF subtype GUID 链"},
        {"field_path": "source.called_macros", "status": "observed",
         "value": [list(item) for item in (getattr(project, "called_macros", []) or [])],
         "confidence": "high", "note": "来自 HSF calledmacros.xml 映射"},
    ]
    params: list[dict[str, Any]] = []
    used_param_ids: set[str] = set()
    for index, parameter in enumerate(project.parameters):
        name = str(parameter.name)
        field_base = f"source.parameters.{name}"
        items.append({
            "field_path": field_base,
            "status": "observed",
            "value": {
                "type": str(parameter.type_tag),
                "current_value": str(parameter.value),
                "description": str(parameter.description or ""),
                "is_fixed": bool(parameter.is_fixed),
                "flags": list(parameter.flags or []),
            },
            "confidence": "high",
            "note": "来自已导入 paramlist.xml；current_value 不复制进 ObjectSpec",
        })
        type_tag = str(parameter.type_tag)
        if type_tag not in {"Length", "Angle", "RealNum", "Integer", "Boolean", "String"}:
            continue
        slug = re.sub(r"[^a-z0-9_]+", "_", name.casefold()).strip("_") or f"param_{index}"
        param_id = f"p.{slug}"
        if param_id in used_param_ids:
            param_id = f"{param_id}_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"
        used_param_ids.add(param_id)
        param: dict[str, Any] = {
            "param_id": param_id,
            "gdl_name": name,
            "type": type_tag,
            "description": str(parameter.description or ""),
            "default_value": None,
            "required": False,
        }
        if type_tag == "Length":
            param["unit"] = "m"
        elif type_tag == "Angle":
            param["unit"] = "deg"
        params.append(param)

    dependency_path = project.root / ".openbrep" / "dependencies" / "library-parts.json"
    if dependency_path.is_file():
        try:
            dependency_manifest = json.loads(dependency_path.read_text(encoding="utf-8"))
            items.append({
                "field_path": "source.library_dependencies",
                "status": "observed",
                "value": dependency_manifest,
                "confidence": "high",
                "note": "来自 OpenBrep 导入时记录的依赖清单；不包含或复制图库内容",
            })
        except (OSError, json.JSONDecodeError):
            items.append({
                "field_path": "source.library_dependencies",
                "status": "unknown", "value": None, "confidence": "unknown",
                "note": "依赖清单无法读取",
            })
    elif getattr(project, "called_macros", None):
        items.append({
            "field_path": "source.library_dependencies",
            "status": "unknown", "value": None, "confidence": "unknown",
            "note": "HSF 有 CALL 映射，但没有依赖解析清单",
        })
    else:
        items.append({
            "field_path": "source.library_dependencies",
            "status": "observed", "value": [], "confidence": "high",
            "note": "当前 HSF 没有声明 CALL 宏",
        })

    observation = {
        "schema_version": 1,
        "observation_id": observation_id,
        "source": "hsf_import",
        "source_refs": [source_ref],
        "items": items,
    }
    candidate_spec = {
        "schema_version": 1,
        "spec_id": spec_id,
        "object_type": str(project.name),
        "source_observation_id": observation_id,
        "params": params,
        "requirements": [],
        "relations": [],
    }
    parsed = parse_object_spec(candidate_spec)
    if not parsed.ok:
        raise ValueError("HSF import produced an invalid ObjectSpec candidate: " + "; ".join(
            f"{error.field_path}: {error.code}" for error in parsed.errors
        ))
    proposal = {
        "schema_version": 1,
        "status": "proposed",
        "source_fingerprint": fingerprint,
        "candidate_spec": parsed.value.to_dict(),
        "observation": observation,
        "warnings": [
            "候选规格只整理 HSF 已有事实；几何约束、材料含义和构件行为尚未由领域 Skill 证明。",
            "关系、要求与默认值保持空/未知，不从脚本外观推断。",
        ],
    }
    proposal["candidate_hash"] = hashlib.sha256(
        json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return proposal
