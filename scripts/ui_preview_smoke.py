#!/usr/bin/env python3
"""Browser smoke test for OBR preview panel.

What it verifies:
1) Import .gdl via uploader
2) Trigger 3D / 2D preview buttons
3) Detect Plotly render container
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
        return False

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
        "plotly_canvas_detected": False,
        "warnings_contains_tube": False,
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

                uploader = page.locator('[data-testid="stFileUploader"]').filter(has_text="📂 导入 gdl / txt / gsm")
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

    required_true = [
        "upload_attempted",
        "tube_in_editor",
        "preview3d_clicked",
        "preview2d_clicked",
        "preview_header_3d_seen",
        "preview_header_2d_seen",
        "plotly_canvas_detected",
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
