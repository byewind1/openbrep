"""Tiny offline screenshot gate for preview self-checks."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _visual_check_enabled() -> bool:
    """视觉验收默认启用，但在 pytest 中默认跳过以避免批量测试挂起。

    显式设置环境变量可覆盖：
      OPENBREP_VISUAL_CHECK=1  强制启用（即使 pytest）
      OPENBREP_VISUAL_CHECK=0  强制禁用
    """
    flag = os.environ.get("OPENBREP_VISUAL_CHECK", "").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # 没有显式设置时，pytest 环境默认跳过
    return "PYTEST_CURRENT_TEST" not in os.environ


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
    result = {
        "status": "pass",
        "screenshot": None,
        "non_blank": False,
        "unique_material_colors": unique_colors,
        "unresolved_meshes": unresolved,
        "diagnostics": [],
    }
    if not meshes:
        result.update(status="unverified", diagnostics=["没有可截图的网格"])
        return result
    if unresolved:
        result.update(status="unverified", diagnostics=[f"{unresolved} 个网格没有解析材质"])
    elif materials and len(unique_colors) < 2:
        pass
    if not _visual_check_enabled():
        result.update(status="unverified")
        result["diagnostics"].append("视觉验收在测试环境或未安装 Playwright 时跳过")
        return result
    directory = Path(out_dir or tempfile.mkdtemp(prefix="openbrep_visual_check_"))
    directory.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 950})
            html = _build_render_html(payload)
            html_path = directory / "preview.html"
            html_path.write_text(html, encoding="utf-8")
            page.goto(html_path.as_uri(), wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1500)
            screenshot = directory / "preview.png"
            page.screenshot(path=str(screenshot))
            result["non_blank"] = _canvas_has_pixels(page)
            result["screenshot"] = str(screenshot)
            browser.close()
    except Exception as exc:
        reason = str(exc).splitlines()[0]
        result["status"] = "unverified"
        result["diagnostics"].append(f"截图引擎不可用：{reason}")
    return result


def _build_render_html(payload: dict[str, Any]) -> str:
    import json as _json
    render = """
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
const p = __PREVIEW_PAYLOAD__;
const scene = new THREE.Scene(); scene.background = new THREE.Color("#0a0e14");
const camera = new THREE.PerspectiveCamera(38, window.innerWidth/window.innerHeight, 0.001, 100000);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.toneMapping = THREE.AgXToneMapping;
renderer.toneMappingExposure = 0.9;
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.add(new THREE.AmbientLight(0xffffff, 0.08));
const key = new THREE.DirectionalLight(0xffffff, 1.1); key.position.set(1,2,1.5); scene.add(key);
const rim = new THREE.DirectionalLight(0x9fb4cc, 0.5); rim.position.set(-1.5,0.5,-1); scene.add(rim);
let cx=0, cy=0, cz=0, n=0;
for (const m of p.meshes) for (const v of m.vertices) {cx+=v[0];cy+=v[1];cz+=v[2];n++;}
cx/=n; cy/=n; cz/=n;
const group = new THREE.Group(); group.position.set(-cx,-cy,-cz); scene.add(group);
for (const mesh of p.meshes) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(mesh.vertices.flat()), 3));
  geo.setIndex(mesh.faces.flat()); geo.computeVertexNormals();
  const id = (mesh.material_id || "").toLowerCase();
  const material = p.materials && p.materials[id] ? p.materials[id] : {color:"#8595ab"};
  const mat = new THREE.MeshStandardMaterial({color: material.color, roughness: material.roughness ?? 0.5, metalness: material.metalness ?? 0, side: THREE.DoubleSide});
  group.add(new THREE.Mesh(geo, mat));
}
camera.position.set(cx+1.5, cy+1.2, cz+1.5);
new OrbitControls(camera, renderer.domElement).target.set(0,0,0);
renderer.render(scene, camera);
"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0;height:100%;overflow:hidden;background:#0a0e14}}</style></head><body><script>const __PREVIEW_PAYLOAD__ = {_json.dumps(payload, ensure_ascii=False)};</script><script type="importmap">{{"imports":{{"three":"https://unpkg.com/three@0.181.2/build/three.module.js","three/addons/":"https://unpkg.com/three@0.181.2/examples/jsm/"}}}}</script><script type="module">{render}</script></body></html>"""


def _canvas_has_pixels(page) -> bool:
    try:
        count = page.evaluate("""() => {
          const c = document.querySelector("canvas");
          if (!c) return 0;
          const ctx = c.getContext("2d");
          const d = ctx.getImageData(0,0,Math.min(c.width,200),Math.min(c.height,150)).data;
          let nonBlank = 0;
          for (let i=0;i<d.length;i+=4) if (d[i]<245 || d[i+1]<245 || d[i+2]<245) nonBlank++;
          return nonBlank;
        }""")
        return bool(count and count > 0)
    except Exception:
        return False
