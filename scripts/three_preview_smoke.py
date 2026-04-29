#!/usr/bin/env python3
"""Browser smoke test for the Three.js preview renderer."""

from __future__ import annotations

import json
import argparse
from pathlib import Path

from openbrep.gdl_previewer import preview_3d_script
from ui.three_preview import render_three_preview_html


def main() -> int:
    parser = argparse.ArgumentParser(description="Run browser smoke test for the Three.js preview UI")
    parser.add_argument("--out", default="/tmp/openbrep_three_preview_smoke", help="Artifacts output directory")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("Missing playwright. Install it with: python -m pip install playwright")
        return 2

    data = preview_3d_script(
        """\
BLOCK 1, 0.7, 0.5
ADD 1.3, 0, 0
CYLIND 0.8, 0.25
DEL 1
""",
        quality="fast",
    )

    summary = {"viewports": [], "errors": []}
    viewports = [
        {"name": "desktop", "width": 1280, "height": 760},
        {"name": "mobile", "width": 390, "height": 720},
    ]

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "three_preview.html"
    html_path.write_text(render_three_preview_html(data), encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for viewport in viewports:
            page = browser.new_page(
                viewport={"width": viewport["width"], "height": viewport["height"]}
            )
            try:
                page.goto(html_path.as_uri(), wait_until="networkidle", timeout=30000)
                page.wait_for_selector("canvas", timeout=30000)
                page.wait_for_timeout(1500)
                status_text = page.locator("#status").inner_text(timeout=1000)
                pixels = page.evaluate(
                    """() => {
                      const canvas = document.querySelector("canvas");
                      const probe = document.createElement("canvas");
                      probe.width = Math.min(canvas.width, 160);
                      probe.height = Math.min(canvas.height, 120);
                      const ctx = probe.getContext("2d");
                      ctx.drawImage(canvas, 0, 0, probe.width, probe.height);
                      const data = ctx.getImageData(0, 0, probe.width, probe.height).data;
                      let nonBlank = 0;
                      for (let i = 0; i < data.length; i += 4) {
                        if (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245) {
                          nonBlank += 1;
                        }
                      }
                      return {
                        width: canvas.width,
                        height: canvas.height,
                        nonBlank
                      };
                    }"""
                )
                screenshot = out_dir / f"{viewport['name']}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                summary["viewports"].append(
                    {
                        **viewport,
                        "status": status_text,
                        "canvas": pixels,
                        "screenshot": str(screenshot),
                    }
                )
                if pixels["nonBlank"] <= 0:
                    summary["errors"].append(f"{viewport['name']}: canvas appears blank")
            except Exception as exc:
                summary["errors"].append(f"{viewport['name']}: {exc}")
            finally:
                page.close()
        browser.close()

    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Artifacts: {out_dir}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
