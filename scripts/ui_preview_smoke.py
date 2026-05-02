#!/usr/bin/env python3
"""Browser smoke test for OBR preview panel.

What it verifies:
1) Import .gdl via uploader
2) Trigger 3D / 2D preview buttons
3) Detect Three.js render canvas
4) Confirm unsupported TUBE emits warning (but app keeps working)
5) Save screenshots + JSON summary

Usage:
    python3 scripts/ui_preview_smoke.py
    python3 scripts/ui_preview_smoke.py --url http://localhost:8501 --out /tmp/obr_preview_smoke
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def _make_sample_gdl(path: Path) -> None:
    path.write_text(
        """! A Length 1.20 Width
! B Length 0.60 Depth
! ZZYZX Length 1.80 Height

! === 3D SCRIPT ===
BLOCK A, B, ZZYZX
TUBE 2, 1, 2,
  0, 0, 0, 0,
  1, 0, 0, 0
END

! === 2D SCRIPT ===
PROJECT2 3, 270, 2
RECT2 0, 0, A, B
""",
        encoding="utf-8",
    )


def _click_tab(page, name: str, prefer_last: bool = False, prefer_index: int | None = None) -> bool:
    tabs = page.get_by_role("tab", name=name)
    count = tabs.count()
    if count <= 0:
        fallback = page.locator(f'text="{name}"')
        fallback_count = fallback.count()
        if fallback_count <= 0:
            return False
        fallback.nth(fallback_count - 1 if prefer_last else 0).click()
        page.wait_for_timeout(700)
        return True

    if prefer_last:
        idx = count - 1
    elif prefer_index is not None and count > prefer_index:
        idx = prefer_index
    else:
        idx = 0

    tabs.nth(idx).click()
    page.wait_for_timeout(700)
    return True


def _collect_ace_text(page) -> str:
    parts: list[str] = []
    for frame in page.frames:
        if "streamlit_ace.streamlit_ace" not in frame.url:
            continue
        try:
            lines = frame.locator(".ace_line").all_inner_texts()
        except Exception:
            continue
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _detect_three_canvas(page) -> dict:
    result = {"detected": False, "nonblank": False, "status": "", "pixels": 0}
    for frame in page.frames:
        try:
            if frame.locator("#viewer canvas").count() <= 0:
                continue
            status = frame.locator("#status").inner_text(timeout=1000)
            pixels = frame.evaluate(
                """() => {
                  const canvas = document.querySelector("#viewer canvas");
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
                  return nonBlank;
                }"""
            )
            result = {
                "detected": True,
                "nonblank": pixels > 0,
                "status": status,
                "pixels": pixels,
            }
            break
        except Exception:
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run browser smoke test for OBR preview UI")
    parser.add_argument("--url", default="http://localhost:8501", help="Streamlit URL")
    parser.add_argument("--out", default="/tmp/obr_preview_smoke", help="Artifacts output directory")
    parser.add_argument("--timeout-ms", type=int, default=60000, help="Page timeout in ms")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("❌ 缺少 playwright。请先执行: python3 -m pip install playwright && python3 -m playwright install chromium")
        return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "url": args.url,
        "upload_attempted": False,
        "import_success_seen": False,
        "tube_in_editor": False,
        "preview3d_clicked": False,
        "preview2d_clicked": False,
        "preview_header_3d_seen": False,
        "preview_header_2d_seen": False,
        "three_canvas_detected": False,
        "three_canvas_nonblank": False,
        "three_canvas_status": "",
        "plotly_canvas_detected": False,
        "warnings_contains_tube": False,
        "native_open_present": False,
        "uploader_present": False,
        "import_flow_skipped": False,
        "warning_texts": [],
        "errors": [],
    }

    with tempfile.TemporaryDirectory(prefix="obr_preview_smoke_") as td:
        sample_path = Path(td) / "preview_sample.gdl"
        _make_sample_gdl(sample_path)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1800, "height": 1300})

            try:
                page.goto(args.url, wait_until="networkidle", timeout=args.timeout_ms)
                page.wait_for_timeout(1500)

                uploader = page.locator('[data-testid="stFileUploader"]').filter(has_text="上传 .gdl / .txt / .gsm")
                summary["uploader_present"] = uploader.count() > 0
                if not summary["uploader_present"]:
                    has_file_open = page.get_by_role("button", name="📄 打开文件").count() > 0
                    has_hsf_open = page.get_by_role("button", name="📂 打开 HSF 项目").count() > 0
                    summary["native_open_present"] = has_file_open and has_hsf_open
                    summary["import_flow_skipped"] = bool(summary["native_open_present"])
                    page.screenshot(path=str(out_dir / "native-open.png"), full_page=True)
                else:
                    uploader.locator('input[type="file"]').set_input_files(str(sample_path))
                    summary["upload_attempted"] = True
                    page.wait_for_timeout(2000)

                    if page.locator("text=已导入").count() > 0 or page.locator("text=导入成功").count() > 0:
                        summary["import_success_seen"] = True

                    # Ensure imported script actually reached editor.
                    _click_tab(page, "3D", prefer_index=0)
                    editor_text = _collect_ace_text(page)
                    if "TUBE" in editor_text.upper() and "BLOCK" in editor_text.upper():
                        summary["tube_in_editor"] = True

                    # Trigger 3D preview.
                    page.get_by_role("button", name="🧊 预览 3D").first.click()
                    page.wait_for_timeout(1800)
                    summary["preview3d_clicked"] = True
                    summary["preview_header_3d_seen"] = page.locator("text=最新预览：3D").count() > 0
                    _click_tab(page, "3D", prefer_last=True)
                    three_canvas = _detect_three_canvas(page)
                    summary["three_canvas_detected"] = three_canvas["detected"]
                    summary["three_canvas_nonblank"] = three_canvas["nonblank"]
                    summary["three_canvas_status"] = three_canvas["status"]

                    # Read warning tab.
                    _click_tab(page, "Warnings", prefer_last=True)
                    warnings: list[str] = []
                    alert_blocks = page.locator('[data-testid="stAlertContainer"]')
                    for i in range(min(30, alert_blocks.count())):
                        txt = alert_blocks.nth(i).inner_text().strip()
                        if txt:
                            warnings.append(txt)
                    summary["warning_texts"] = warnings
                    summary["warnings_contains_tube"] = any("TUBE" in w.upper() for w in warnings)

                    # Trigger 2D preview.
                    page.get_by_role("button", name="👁️ 预览 2D").first.click()
                    page.wait_for_timeout(1800)
                    summary["preview2d_clicked"] = True
                    summary["preview_header_2d_seen"] = page.locator("text=最新预览：2D").count() > 0

                    # Open preview 3D tab (second group usually index=1) and detect plotly.
                    _click_tab(page, "3D", prefer_index=1)
                    summary["plotly_canvas_detected"] = page.locator(".js-plotly-plot").count() > 0

                    # Artifacts
                    page.screenshot(path=str(out_dir / "full.png"), full_page=True)
                    if _click_tab(page, "2D", prefer_index=1):
                        page.screenshot(path=str(out_dir / "preview2d.png"), full_page=True)
                    if _click_tab(page, "3D", prefer_index=1):
                        page.screenshot(path=str(out_dir / "preview3d.png"), full_page=True)
                    if _click_tab(page, "Warnings", prefer_last=True):
                        page.screenshot(path=str(out_dir / "warnings.png"), full_page=True)

            except Exception as exc:
                summary["errors"].append(str(exc))
                try:
                    page.screenshot(path=str(out_dir / "error.png"), full_page=True)
                except Exception:
                    pass
            finally:
                browser.close()

    (out_dir / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Artifacts: {out_dir}")

    if summary.get("import_flow_skipped"):
        required_true = ["native_open_present"]
    else:
        required_true = [
            "upload_attempted",
            "tube_in_editor",
            "preview3d_clicked",
            "preview2d_clicked",
            "preview_header_3d_seen",
            "preview_header_2d_seen",
            "three_canvas_detected",
            "three_canvas_nonblank",
            "warnings_contains_tube",
        ]
    failed = [k for k in required_true if not summary.get(k)]
    if summary.get("errors") or failed:
        if failed:
            print(f"❌ Smoke check failed fields: {', '.join(failed)}")
        return 1

    print("✅ Browser smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
