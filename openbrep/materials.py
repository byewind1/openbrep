"""Small, offline material document used by preview and future Archicad binding."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

_PRESETS = {
    "wood": {"label": "木材", "color": "#A87848", "roughness": 0.62, "metalness": 0.0, "opacity": 1.0, "transmission": 0.0, "ior": 1.5},
    "metal": {"label": "金属", "color": "#9AA3AD", "roughness": 0.28, "metalness": 0.9, "opacity": 1.0, "transmission": 0.0, "ior": 1.5},
    "glass": {"label": "玻璃", "color": "#B9D9E8", "roughness": 0.08, "metalness": 0.0, "opacity": 1.0, "transmission": 0.9, "ior": 1.52},
    "plastic": {"label": "塑料", "color": "#B8BEC7", "roughness": 0.42, "metalness": 0.0, "opacity": 1.0, "transmission": 0.0, "ior": 1.46},
}
_DEFAULT = {"label": "未指定材质", "color": "#8595AB", "roughness": 0.5, "metalness": 0.0, "opacity": 1.0, "transmission": 0.0, "ior": 1.5}
_NUMERIC = ("roughness", "metalness", "opacity", "transmission", "ior")

_FALLBACK_HINTS = {
    "wood": ("wood", "木", "橡木", "桌面", "seat", "框架", "frame"),
    "metal": ("metal", "金属", "钢", "铝", "leg", "腿"),
    "glass": ("glass", "玻璃", "透明", "transparent"),
}


def _family_for_parameter(name: str, description: str, instruction: str = "") -> str:
    text = f"{name} {description} {instruction}".lower()
    instruction_lower = instruction.lower()
    if any(word in instruction_lower for word in ("metal", "金属", "钢", "铝")):
        return "metal"
    if any(word in instruction_lower for word in ("glass", "玻璃", "透明", "transparent")):
        return "glass"
    if any(word in instruction_lower for word in ("wood", "木", "橡木")):
        return "wood"
    if any(word in name.lower() for word in ("mat_wood", "wood", "木", "frame")):
        return "wood"
    if any(word in name.lower() for word in ("mat_metal", "metal", "钢", "铝", "leg", "腿")):
        return "metal"
    if any(word in name.lower() for word in ("mat_glass", "glass", "玻璃")):
        return "glass"
    if any(word in text for word in _FALLBACK_HINTS["metal"]):
        return "metal"
    if any(word in text for word in _FALLBACK_HINTS["glass"]):
        return "glass"
    if any(word in text for word in _FALLBACK_HINTS["wood"]):
        return "wood"
    return "generic"


def infer_material_slots(parameters: list[Any], instruction: str = "") -> dict[str, dict[str, Any]]:
    """Pick conservative presets from Material parameter names/descriptions."""
    text = (instruction or "").lower()
    result: dict[str, dict[str, Any]] = {}
    # Per-part semantic: try to match a substring of the instruction to this parameter.
    # Simple heuristic: split on common separators and look for nearby family words.
    instruction_parts = [part.strip() for part in text.replace("。", "，").replace("、", "，").split("，")]
    for parameter in parameters:
        if str(getattr(parameter, "type_tag", "")) != "Material":
            continue
        name = str(getattr(parameter, "name", "mat_main"))
        description = getattr(parameter, "description", "") or ""
        # Find the instruction segment that mentions this parameter by name or description.
        part_instruction = instruction
        name_key = name.lower().replace("mat_", "")
        desc_keys = [w for w in description.lower().split() if len(w) > 1]
        matched_parts = [
            part for part in instruction_parts
            if name_key in part or any(k in part for k in desc_keys)
        ]
        if matched_parts:
            part_instruction = matched_parts[0]
        family = _family_for_parameter(name, description, part_instruction)
        evidence = [{"kind": "text", "ref": part_instruction}] if part_instruction and part_instruction != instruction else ([{"kind": "text", "ref": instruction}] if instruction else [])
        result[name] = {"family": family, "confidence": "high" if family != "generic" else "low", "evidence": evidence}
    return result


def normalize_slots(raw: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    slots: dict[str, dict[str, Any]] = {}
    for key, value in (raw or {}).items():
        if not isinstance(key, str) or not key or not key.replace("_", "a").isalnum() or not isinstance(value, dict):
            warnings.append(f"忽略无效材质槽: {key!r}")
            continue
        family = str(value.get("family") or "").lower()
        base = dict(_PRESETS.get(family, _DEFAULT))
        if family not in _PRESETS and family:
            base["family"] = family
        base.update({k: value[k] for k in value if k in {"label", "color", "confidence", "evidence", "binding"} or k in _NUMERIC})
        base["family"] = family or "generic"
        color = str(base.get("color", _DEFAULT["color"]))
        if not _valid_color(color):
            warnings.append(f"材质 {key} 的颜色无效，已使用默认色")
            color = _DEFAULT["color"]
        base["color"] = color.upper()
        for field in _NUMERIC:
            default = float(_DEFAULT[field])
            try:
                number = float(base.get(field, default))
            except (TypeError, ValueError):
                number = default
                warnings.append(f"材质 {key} 的 {field} 无效，已使用默认值")
            if not math.isfinite(number):
                number = default
                warnings.append(f"材质 {key} 的 {field} 非有限值，已使用默认值")
            base[field] = max(0.0, min(1.0, number)) if field != "ior" else max(1.0, min(3.0, number))
        slots[key] = base
    return slots, warnings


def load_materials(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = Path(root) / ".openbrep" / "materials.json"
    if not path.exists():
        return {"version": 1, "slots": {}}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("根节点必须是对象")
        slots, warnings = normalize_slots(raw.get("slots"))
        return {"version": 1, "slots": slots}, warnings
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"version": 1, "slots": {}}, [f"材质文件读取失败: {exc}"]


def save_materials(root: Path, document: dict[str, Any]) -> None:
    slots, warnings = normalize_slots(document.get("slots") if isinstance(document, dict) else {})
    if warnings:
        raise ValueError("; ".join(warnings))
    directory = Path(root) / ".openbrep"
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="materials.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "slots": slots}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, directory / "materials.json")
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _valid_color(value: str) -> bool:
    return len(value) == 7 and value.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in value[1:])
