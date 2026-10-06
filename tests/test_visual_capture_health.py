"""U02-B 截图采集健康门测试（openbrep/runtime/visual_self_check.py）。

验收口径（派单 U02-B）：正常模型有可核截图；纯背景/黑屏/无canvas/出框/
全透明均不误 pass；引擎不可用可见；**实际浏览器测，而非仅 mock page**。

浏览器用例需要 Playwright + Chromium：不可用时 skip（CI 无头沙箱兜底），
本机开发者环境必须真机执行。修复的假通过（U00-A 反例 P1）：WebGL canvas
取 2D context 读回恒 False 而 status 仍 pass——本文件防止回归到旧假 pass。
"""

from __future__ import annotations

import pytest

from openbrep.runtime import visual_self_check as vsc
from openbrep.runtime.visual_self_check import (
    CAPTURE_FAILED,
    CAPTURE_PASS,
    CAPTURE_UNVERIFIED,
    check_preview_visual,
)


def _cube_payload(*, color_a: str = "#aa5533", color_b: str = "#3377aa") -> dict:
    """faces 形状与真实 preview_3d_to_three_payload 一致：单层三角 [i,j,k]。"""

    def cube(material_id: str, offset: float = 0.0) -> dict:
        return {
            "vertices": [
                [offset + 0.0, 0.0, 0.0], [offset + 1.0, 0.0, 0.0],
                [offset + 1.0, 1.0, 0.0], [offset + 0.0, 1.0, 0.0],
                [offset + 0.0, 0.0, 1.0], [offset + 1.0, 0.0, 1.0],
                [offset + 1.0, 1.0, 1.0], [offset + 0.0, 1.0, 1.0],
            ],
            "faces": [
                [0, 1, 2], [0, 2, 3],  # 前
                [4, 6, 5], [4, 7, 6],  # 后
                [0, 1, 5], [0, 5, 4],  # 下
                [2, 3, 7], [2, 7, 6],  # 上
                [1, 2, 6], [1, 6, 5],  # 右
                [3, 0, 4], [3, 4, 7],  # 左
            ],
            "material_id": material_id,
        }

    return {
        "meshes": [cube("mat_a"), cube("mat_b", 1.5)],
        "materials": {"mat_a": {"color": color_a}, "mat_b": {"color": color_b}},
    }


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            browser.close()
        return True
    except Exception:
        return False


_CHROMIUM = _chromium_available()

pytestmark = pytest.mark.skipif(
    not _CHROMIUM, reason="Playwright/Chromium 不可用（CI 无头沙箱兜底）；本机须真机执行"
)


def test_healthy_model_passes_with_verifiable_screenshot(tmp_path, monkeypatch):
    """正常模型：status=pass 且截图可核（像素读回非背景达标）。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")
    result = check_preview_visual(_cube_payload(), out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_PASS, result
    assert result["non_blank"] is True
    assert result["capture"]["readable"] is True
    assert result["capture"]["canvas_found"] is True
    assert result["capture"]["non_bg"] > 0
    from pathlib import Path

    assert result["screenshot"] and Path(result["screenshot"]).exists()
    assert len(result["image_sha256"]) == 64


def test_empty_scene_is_not_passed(tmp_path, monkeypatch):
    """纯背景/出框/全透明同族：画布像素与背景不可区分 → unverified，不误 pass。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")
    payload = _cube_payload()
    payload["meshes"][0]["faces"] = []  # 有顶点无三角面 → 什么都不画
    payload["meshes"][1]["faces"] = []
    result = check_preview_visual(payload, out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_UNVERIFIED, result
    assert result["non_blank"] is False
    assert "背景" in result["reason"]


def test_unresolved_materials_reported_but_health_independent(tmp_path, monkeypatch):
    """材质解析诊断保留；capture 健康判定独立于材质解析。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")
    payload = _cube_payload()
    payload["materials"] = {}  # 全部未解析
    result = check_preview_visual(payload, out_dir=tmp_path / "cap")
    assert result["unresolved_meshes"] == 2
    assert any("没有解析材质" in d for d in result["diagnostics"])
    # 未解析材质走默认色仍可画 → capture 健康与材质解析是两件事
    assert result["status"] in (CAPTURE_PASS, CAPTURE_UNVERIFIED)


def test_engine_unavailable_is_failed_and_visible(tmp_path, monkeypatch):
    """引擎不可用 = failed（可见），不得静默当 unverified 混过。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")

    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise RuntimeError("playwright not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    result = check_preview_visual(_cube_payload(), out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_FAILED
    assert "截图引擎不可用" in result["reason"]
    assert any("截图引擎不可用" in d for d in result["diagnostics"])


def test_unreadable_canvas_is_unverified(tmp_path, monkeypatch):
    """无 canvas / 读不到像素 → unverified（无法读取），不误 pass。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")
    monkeypatch.setattr(vsc, "_capture_pixel_stats", lambda page: None)
    result = check_preview_visual(_cube_payload(), out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_UNVERIFIED
    assert "无法读取" in result["reason"]


def test_disabled_check_never_passes(tmp_path, monkeypatch):
    """引擎禁用时保持 unverified（skip 可见），绝不绿灯。"""
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "0")
    result = check_preview_visual(_cube_payload(), out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_UNVERIFIED
    assert any("跳过" in d for d in result["diagnostics"])


def test_no_meshes_unverified_without_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBREP_VISUAL_CHECK", "1")
    result = check_preview_visual({"meshes": [], "materials": {}}, out_dir=tmp_path / "cap")
    assert result["status"] == CAPTURE_UNVERIFIED
    assert "没有可截图的网格" in result["reason"]
