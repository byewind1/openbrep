"""截图采集健康门（U02-B）：capture 健康 ≠ 视觉符合。

修复的假通过（U00-A 反例 P1）：旧实现用 ``canvas.getContext("2d")`` 读
WebGL canvas——同 canvas 已持有 webgl context 时 2D context 返回 null，
读回恒为 False，而 ``status`` 不消费 ``non_blank`` → 正常模型也以
``status=pass, non_blank=false`` 绿灯。

现实现（U02-B）：
- 渲染器 ``preserveDrawingBuffer: true`` + WebGL ``readPixels`` 读回像素
  （浏览器内抽样统计，只回传统计量，不传全量像素）；
- 像素与四角背景估计对比：纯背景 / 出框 / 全透明 / 黑屏 → ``unverified``；
- 找不到 canvas 或读不到像素 → ``unverified``（无法读取，诊断可见）；
- 截图引擎异常 → ``failed``（引擎不可用必须可见，不得当 unverified 混过）；
- 只有像素读回确认非背景内容 ≥ 阈值才 ``pass``。

结果形状（CaptureResult 初版，U11 扩展不重建）::

    {status: pass|unverified|failed, reason, screenshot, image_sha256,
     non_blank, capture: {canvas_found, readable, width, height, non_bg, sampled},
     unique_material_colors, unresolved_meshes, diagnostics}

消费方：``modify_acceptance.build_modify_acceptance``（截图视觉验收 check
直接透传 status）；三态语义：capture 健康不代表视觉符合——符合性归
U12 视觉审查。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

# 渲染页的 three.js 来源：优先仓库内 frontend/node_modules（离线确定性，
# 不受 CDN 抖动影响）；打包环境缺失时回退 CDN（引擎异常会如实进 failed）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_THREE_LOCAL = _REPO_ROOT / "frontend" / "node_modules" / "three"
_THREE_CDN_IMPORTS = {
    "three": "https://unpkg.com/three@0.181.2/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.181.2/examples/jsm/",
}


def _three_importmap() -> str:
    imports = _THREE_CDN_IMPORTS
    if (_THREE_LOCAL / "build" / "three.module.js").is_file():
        base = _THREE_LOCAL.as_uri()
        imports = {
            "three": f"{base}/build/three.module.js",
            "three/addons/": f"{base}/examples/jsm/",
        }
    return json.dumps({"imports": imports})

# capture 健康三态（U02-B）：与 CheckResult 的 pass/unverified/failed 同词汇
CAPTURE_PASS = "pass"
CAPTURE_UNVERIFIED = "unverified"
CAPTURE_FAILED = "failed"

# 非背景像素判定：与背景估计逐通道差异 > 容差
_PIXEL_TOLERANCE = 24
# pass 阈值：抽样像素中非背景占比 ≥ 0.2% 且绝对数 ≥ 64（细长模型也不漏判）
_NON_BG_MIN_COUNT = 64
_NON_BG_MIN_RATIO = 0.002


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
    """preview payload → 截图采集健康结果（真浏览器；只测健康，不判视觉符合）。"""
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
    result: dict[str, Any] = {
        "status": CAPTURE_UNVERIFIED,
        "reason": "",
        "screenshot": None,
        "image_sha256": "",
        "non_blank": False,
        "capture": {"canvas_found": False, "readable": False},
        "unique_material_colors": unique_colors,
        "unresolved_meshes": unresolved,
        "diagnostics": [],
    }
    if not meshes:
        result["reason"] = "没有可截图的网格"
        result["diagnostics"].append(result["reason"])
        return result
    if unresolved:
        result["diagnostics"].append(f"{unresolved} 个网格没有解析材质")
    if not _visual_check_enabled():
        result["diagnostics"].append("视觉验收在测试环境或未安装 Playwright 时跳过")
        return result
    directory = Path(out_dir or tempfile.mkdtemp(prefix="openbrep_visual_check_"))
    directory.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            # --allow-file-access-from-files：渲染页是 file:// 本地页，需放行
            # 本地 three ES module 的 CORS（否则模块脚本被拦、canvas 不创建）。
            browser = playwright.chromium.launch(
                headless=True,
                args=["--enable-unsafe-swiftshader", "--allow-file-access-from-files"],
            )
            page = browser.new_page(viewport={"width": 1400, "height": 950})
            html = _build_render_html(payload)
            html_path = directory / "preview.html"
            html_path.write_text(html, encoding="utf-8")
            page.goto(html_path.as_uri(), wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1500)
            screenshot = directory / "preview.png"
            page.screenshot(path=str(screenshot))
            result["screenshot"] = str(screenshot)
            try:
                result["image_sha256"] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
            except OSError:
                pass
            stats = _capture_pixel_stats(page)
            browser.close()
    except Exception as exc:
        # U02-B：引擎不可用/渲染异常 = failed（可见），不得静默当 unverified
        reason = str(exc).splitlines()[0]
        result.update(status=CAPTURE_FAILED, reason=f"截图引擎不可用：{reason}")
        result["diagnostics"].append(result["reason"])
        return result

    if stats is None:
        result.update(
            status=CAPTURE_UNVERIFIED,
            reason="无法读取画布像素（无 canvas 或 WebGL context 不可用）",
        )
        result["diagnostics"].append(result["reason"])
        return result
    non_bg = int(stats.get("non_bg") or 0)
    sampled = int(stats.get("sampled") or 0)
    result["capture"].update({
        "canvas_found": True,
        "readable": True,
        "width": stats.get("w"),
        "height": stats.get("h"),
        "non_bg": non_bg,
        "sampled": sampled,
    })
    healthy = sampled > 0 and non_bg >= max(_NON_BG_MIN_COUNT, _NON_BG_MIN_RATIO * sampled)
    result["non_blank"] = bool(healthy)
    if not healthy:
        # 纯背景 / 出框 / 全透明 / 黑屏在此收敛为 unverified（不误 pass）
        result.update(
            status=CAPTURE_UNVERIFIED,
            reason=f"画布内容与背景不可区分（非背景像素 {non_bg}/{sampled}）",
        )
        result["diagnostics"].append(result["reason"])
        return result
    result.update(status=CAPTURE_PASS, reason="截图可核验（非背景内容达标）")
    return result


def _capture_pixel_stats(page) -> dict[str, Any] | None:
    """浏览器内 WebGL readPixels 抽样统计：返回 {w, h, sampled, non_bg} 或 None。

    旧实现 ``getContext("2d")`` 在 WebGL canvas 上恒返回 null（U00-A 反例）；
    现实现取同一 webgl context（同参数重复调用返回原 context），并依赖
    ``preserveDrawingBuffer: true`` 保证合成后 readPixels 仍有效。
    """
    try:
        return page.evaluate("""() => {
          const c = document.querySelector("canvas");
          if (!c) return null;
          const gl = c.getContext("webgl2") || c.getContext("webgl");
          if (!gl) return null;
          const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
          if (!w || !h) return null;
          const px = new Uint8Array(w * h * 4);
          gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
          const corner = (x, y) => {
            const i = (y * w + x) * 4;
            return [px[i], px[i + 1], px[i + 2]];
          };
          const bgPts = [corner(0, 0), corner(w - 1, 0), corner(0, h - 1), corner(w - 1, h - 1)];
          const bg = [0, 1, 2].map((k) => bgPts.reduce((s, p) => s + p[k], 0) / bgPts.length);
          const step = Math.max(1, Math.floor(Math.min(w, h) / 400));
          let sampled = 0, nonBg = 0;
          for (let y = 0; y < h; y += step) {
            for (let x = 0; x < w; x += step) {
              const i = (y * w + x) * 4;
              sampled++;
              if (Math.abs(px[i] - bg[0]) > 24 ||
                  Math.abs(px[i + 1] - bg[1]) > 24 ||
                  Math.abs(px[i + 2] - bg[2]) > 24) nonBg++;
            }
          }
          return { w, h, sampled, non_bg: nonBg };
        }""")
    except Exception:
        return None


def _build_render_html(payload: dict[str, Any]) -> str:
    import json as _json
    render = """
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
const p = __PREVIEW_PAYLOAD__;
const scene = new THREE.Scene(); scene.background = new THREE.Color("#0a0e14");
const camera = new THREE.PerspectiveCamera(38, window.innerWidth/window.innerHeight, 0.001, 100000);
const renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
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
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0;height:100%;overflow:hidden;background:#0a0e14}}</style></head><body><script>const __PREVIEW_PAYLOAD__ = {_json.dumps(payload, ensure_ascii=False)};</script><script type="importmap">{_three_importmap()}</script><script type="module">{render}</script></body></html>"""
