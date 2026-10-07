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
import shutil
import tempfile
from pathlib import Path
from typing import Any

# Vite builds a self-contained renderer bundle into frontend/dist/capture. The
# PyInstaller desktop sidecar already includes frontend/dist, so capture stays
# offline and uses the same production camera/geometry/material modules.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPTURE_BUNDLE = _REPO_ROOT / "frontend" / "dist" / "capture" / "preview_capture.js"


def _install_capture_bundle(directory: Path) -> Path:
    source = _CAPTURE_BUNDLE
    if not source.is_file():
        raise FileNotFoundError(
            "offline preview renderer is missing; build the frontend capture bundle first"
        )
    target = directory / "preview_capture.js"
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return target

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
    if not meshes:
        return {
            "status": CAPTURE_UNVERIFIED,
            "reason": "没有可截图的网格",
            "screenshot": None,
            "image_sha256": "",
            "non_blank": False,
            "capture": {"canvas_found": False, "readable": False},
            "unique_material_colors": unique_colors,
            "unresolved_meshes": unresolved,
            "diagnostics": ["没有可截图的网格"],
            "views": [],
        }
    manifest = capture_preview_views(
        payload,
        out_dir=out_dir,
        view_names=("material_iso",),
    )
    view = next(iter(manifest.get("views") or []), {})
    stats = view.get("capture") or {}
    return {
        "status": manifest.get("status", CAPTURE_UNVERIFIED),
        "reason": view.get("reason") or manifest.get("reason", "截图采集不可用"),
        "screenshot": view.get("screenshot"),
        "image_sha256": view.get("image_sha256", ""),
        "non_blank": view.get("status") == CAPTURE_PASS,
        "capture": stats or {"canvas_found": False, "readable": False},
        "unique_material_colors": unique_colors,
        "unresolved_meshes": len(manifest.get("unresolved_material_meshes") or []) or unresolved,
        "diagnostics": list(manifest.get("diagnostics") or []) + (
            [f"{unresolved} 个网格没有解析材质"] if unresolved else []
        ),
        "views": list(manifest.get("views") or []),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "payload_sha256": manifest.get("payload_sha256"),
    }


def capture_preview_views(
    payload: dict[str, Any],
    *,
    out_dir: Path | None = None,
    source_fingerprint: str | None = None,
    view_names: tuple[str, ...] = ("material_iso", "front", "side", "neutral"),
) -> dict[str, Any]:
    """Capture a frozen set of comparable views from one immutable preview payload.

    This is a capture manifest, not a visual-quality judgment. Each view records
    its camera preset, screenshot hash, material mode and source fingerprint so a
    later reviewer can distinguish capture health from semantic correctness.
    """
    allowed = {"material_iso", "front", "side", "neutral"}
    if not view_names or any(name not in allowed for name in view_names):
        return {"status": CAPTURE_FAILED, "reason": "视图请求包含未知或空视图集", "diagnostics": ["视图请求包含未知或空视图集"], "views": []}
    meshes = payload.get("meshes") or []
    if not meshes:
        return {"status": CAPTURE_UNVERIFIED, "reason": "没有可截图的网格", "diagnostics": ["没有可截图的网格"], "views": []}
    if not _visual_check_enabled():
        return {
            "status": CAPTURE_UNVERIFIED,
            "reason": "视觉验收在测试环境或未安装 Playwright 时跳过",
            "diagnostics": ["视觉验收在测试环境或未安装 Playwright 时跳过"],
            "views": [],
        }

    directory = Path(out_dir or tempfile.mkdtemp(prefix="openbrep_preview_views_"))
    directory.mkdir(parents=True, exist_ok=True)
    payload_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    material_hash = hashlib.sha256(
        json.dumps(payload.get("materials") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    materials = payload.get("materials") or {}
    unresolved = [
        {"mesh": str(mesh.get("name") or ""), "material_id": mesh.get("material_id")}
        for mesh in meshes
        if not mesh.get("material_id") or not any(
            str(key).casefold() == str(mesh.get("material_id")).casefold() for key in materials
        )
    ]
    views: list[dict[str, Any]] = []
    try:
        from playwright.sync_api import sync_playwright

        _install_capture_bundle(directory)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--enable-unsafe-swiftshader", "--allow-file-access-from-files"],
            )
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 950}, device_scale_factor=1)
                for name in view_names:
                    html_path = directory / f"{name}.html"
                    screenshot = directory / f"{name}.png"
                    html_path.write_text(_build_render_html(payload, preset=name), encoding="utf-8")
                    page.goto(html_path.as_uri(), wait_until="load", timeout=15000)
                    page.wait_for_function("window.__OPENBREP_CAPTURE_READY__ === true", timeout=15000)
                    page.screenshot(path=str(screenshot))
                    camera = page.evaluate("window.__OPENBREP_CAPTURE_CAMERA__") or {}
                    stats = _capture_pixel_stats(page)
                    non_bg = int((stats or {}).get("non_bg") or 0)
                    sampled = int((stats or {}).get("sampled") or 0)
                    healthy = bool(stats and sampled > 0 and non_bg >= max(_NON_BG_MIN_COUNT, _NON_BG_MIN_RATIO * sampled))
                    views.append({
                        "name": name,
                        "status": CAPTURE_PASS if healthy else CAPTURE_UNVERIFIED,
                        "reason": (
                            "截图可核验（非背景内容达标）" if healthy else
                            "无法读取画布像素" if stats is None else
                            f"画布内容与背景不可区分（非背景像素 {non_bg}/{sampled}）"
                        ),
                        "screenshot": str(screenshot),
                        "image_sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
                        "camera": camera,
                        "material_mode": "neutral" if name == "neutral" else "semantic",
                        "capture": {
                            "canvas_found": stats is not None,
                            "readable": stats is not None,
                            "width": (stats or {}).get("w"),
                            "height": (stats or {}).get("h"),
                            "non_bg": non_bg,
                            "sampled": sampled,
                        },
                    })
            finally:
                browser.close()
    except Exception as exc:
        reason = str(exc).splitlines()[0]
        return {"status": CAPTURE_FAILED, "reason": f"截图引擎不可用：{reason}", "diagnostics": [f"截图引擎不可用：{reason}"], "views": views}

    status = CAPTURE_PASS if views and all(view["status"] == CAPTURE_PASS for view in views) else CAPTURE_UNVERIFIED
    return {
        "status": status,
        "reason": "多视图采集完成" if status == CAPTURE_PASS else "至少一个视图无法确认非背景画面",
        "diagnostics": [] if status == CAPTURE_PASS else [
            f"{view['name']}：{view.get('reason') or '画面像素无法确认非背景内容'}"
            for view in views if view["status"] != CAPTURE_PASS
        ],
        "source_fingerprint": source_fingerprint,
        "payload_sha256": payload_hash,
        "preview_warnings": list(payload.get("warnings") or []),
        "unresolved_material_meshes": unresolved,
        "renderer": {
            "three_version": "0.181.2",
            "viewport": {"width": 1400, "height": 950, "device_scale_factor": 1},
            "tone_mapping": "AgXToneMapping",
            "exposure": 0.9,
            "output_color_space": "SRGBColorSpace",
            "antialias": True,
            "preserve_drawing_buffer": True,
            "environment": "RoomEnvironment/PMREMGenerator(0.04)",
            "lights": [
                {"kind": "AmbientLight", "color": "#ffffff", "intensity": 0.08},
                {"kind": "DirectionalLight", "color": "#ffffff", "intensity": 1.1, "position": [3, -4, 5]},
                {"kind": "DirectionalLight", "color": "#9fb4cc", "intensity": 0.5, "position": [-4, 2, 3]},
            ],
            "materials_sha256": material_hash,
        },
        "views": views,
    }


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


def _build_render_html(payload: dict[str, Any], *, preset: str = "material_iso") -> str:
    import json as _json
    if preset not in {"material_iso", "front", "side", "neutral"}:
        raise ValueError(f"unsupported preview capture preset: {preset}")
    config = _json.dumps({"payload": payload, "preset": preset}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#0a0e14}}canvas{{display:block}}</style></head><body><script>window.__OPENBREP_CAPTURE__={config};</script><script src="./preview_capture.js"></script></body></html>"""
