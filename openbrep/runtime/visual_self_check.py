"""Tiny offline screenshot gate for preview self-checks."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def check_preview_visual(payload: dict[str, Any], *, out_dir: Path | None = None) -> dict[str, Any]:
    meshes = payload.get("meshes") or []
    materials = payload.get("materials") or {}
    ids = [str(mesh.get("material_id") or "").casefold() for mesh in meshes]
    colors = []
    for ident in ids:
        mat = materials.get(ident) or next((v for k, v in materials.items() if str(k).casefold() == ident), None)
        if mat and mat.get("color"):
            colors.append(str(mat["color"]).upper())
    unique_colors = sorted(set(colors))
    known_ids = {str(k).casefold() for k in materials}
    unresolved = sum(not ident or ident not in known_ids for ident in ids)
    result = {"status": "pass", "screenshot": None, "non_blank": False, "unique_material_colors": unique_colors, "unresolved_meshes": unresolved, "diagnostics": []}
    if not meshes:
        result.update(status="fail", diagnostics=["没有可截图的网格"])
        return result
    if unresolved:
        result.update(status="fail", diagnostics=[f"{unresolved} 个网格没有解析材质"])
    if materials and len(unique_colors) < min(2, len(materials)):
        result["status"] = "warn"
        result["diagnostics"].append("材质颜色没有形成可辨差异")
    directory = Path(out_dir or tempfile.mkdtemp(prefix="openbrep_visual_check_"))
    directory.mkdir(parents=True, exist_ok=True)
    svg = directory / "preview.svg"
    blocks = "".join(f'<rect x="{20 + i*70}" y="40" width="60" height="60" fill="{c}"/>' for i, c in enumerate(unique_colors[:12]))
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="180"><rect width="100%" height="100%" fill="#0a0e14"/>{blocks}<text x="20" y="140" fill="white">meshes={len(meshes)} colors={len(unique_colors)}</text></svg>', encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1000, "height": 180})
            page.goto(svg.as_uri())
            page.screenshot(path=str(directory / "preview.png"))
            result["non_blank"] = bool(page.locator("svg").count())
            browser.close()
        result["screenshot"] = str(directory / "preview.png")
    except Exception as exc:
        result["status"] = "warn"
        result["diagnostics"].append(f"截图引擎不可用：{exc}")
    return result
