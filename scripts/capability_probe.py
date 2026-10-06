#!/usr/bin/env python3
"""U00-B 环境能力探针：记录"实际可执行接口 + 有效回读"，不是"已安装"。

探测项（派单 U00-B）：
- LP_XMLConverter：路径存在 + 实际可执行（--version/短调用回读退出码）；
- 浏览器（截图引擎）：Playwright + Chromium 实际启动；
- Codex：openbrep.codex.provider 状态枚举（no_cli/unconfigured/signed_out/ready…，
  绝不携带 auth 路径/token，D1）；
- Archicad/Add-On：离线探针不冒充实测——记录 not_checked 并指向
  workbench/host_verification_service（真机验收的唯一入口，U16 执行）。

输出能力表 JSON（stdout 或 --output）。退出码恒 0（测量工具）；--strict 时
任何 not_ready 退出码 1。结果交 U03/U10/U13/U16/U17 做环境决策。

用法：python scripts/capability_probe.py [--output r.json] [--strict]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 配置隔离先于 openbrep 导入：绝不读取开发者 ./config.toml
_ISOLATED_CONFIG = Path(tempfile.mkdtemp(prefix="openbrep_cap_cfg_")) / "config.toml"
_ISOLATED_CONFIG.write_text('[llm]\nmodel = "mock-model"\n', encoding="utf-8")
import os as _os

_os.environ.setdefault("GDL_AGENT_CONFIG", str(_ISOLATED_CONFIG))

NOW = lambda: datetime.now(timezone.utc).isoformat()


def probe_lp() -> dict[str, Any]:
    """LP_XMLConverter：存在 + 实际可执行（--version 回读），不是"已安装"。"""
    from openbrep.config import _auto_detect_converter

    entry: dict[str, Any] = {"name": "LP_XMLConverter", "ready": False}
    path = _auto_detect_converter()
    if not path:
        entry["status"] = "missing"
        entry["note"] = "PATH 与常见 Archicad 安装位置均未找到；可在 config.toml [compiler] path 指定"
        return entry
    entry["path"] = str(path)
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True, text=True, timeout=30,
        )
        entry["invocation_exit_code"] = proc.returncode
        entry["version_stdout_head"] = (proc.stdout or proc.stderr or "").strip()[:200]
        # LP_XMLConverter 对未知参数可能非零退出——能启动并回话即视为可用
        entry["status"] = "ready" if proc.returncode in (0, 1, 2) else "unusable"
        entry["ready"] = entry["status"] == "ready"
    except (OSError, subprocess.TimeoutExpired) as exc:
        entry["status"] = "unusable"
        entry["note"] = f"{type(exc).__name__}: {exc}"
    return entry


def probe_browser() -> dict[str, Any]:
    """截图引擎：Playwright + Chromium 实际启动 + 画布读回。"""
    entry: dict[str, Any] = {"name": "browser_playwright_chromium", "ready": False}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--enable-unsafe-swiftshader", "--allow-file-access-from-files"]
            )
            page = browser.new_page(viewport={"width": 200, "height": 150})
            page.goto("about:blank")
            page.set_content("<canvas></canvas>")
            gl_ok = page.evaluate(
                "() => { const c = document.querySelector('canvas');"
                " return !!(c.getContext('webgl2') || c.getContext('webgl')); }"
            )
            browser.close()
        entry.update(status="ready", ready=True, webgl_available=bool(gl_ok))
    except Exception as exc:
        entry.update(status="unavailable", note=f"{type(exc).__name__}: {exc}".splitlines()[0])
    return entry


def probe_codex() -> dict[str, Any]:
    """Codex 状态枚举（local/managed 双入口）；绝不携带 auth 路径/token。"""
    entry: dict[str, Any] = {"name": "codex", "ready": False}
    try:
        from openbrep.codex.provider import CodexProvider
        from openbrep.config import GDLAgentConfig

        config = GDLAgentConfig()
        provider = CodexProvider(config.llm)
        status = provider.status()
        entry["state"] = str(status.get("state") or "unknown")
        entry["entry"] = str(status.get("entry") or "")
        entry["codex_home_kind"] = str(status.get("codex_home_kind") or "")
        entry["auth_source"] = str(status.get("auth_source") or "")
        entry["ready"] = entry["state"] == "ready"
        entry["note"] = "状态为符号枚举；认证内容绝不入探测结果（D1）"
    except Exception as exc:
        entry["state"] = "error"
        entry["note"] = f"{type(exc).__name__}: {exc}".splitlines()[0]
    return entry


def probe_archicad() -> dict[str, Any]:
    """Archicad/Add-On：离线探针不冒充实测——真机验收唯一入口是
    workbench.host_verification_service（U16 真机执行）。"""
    return {
        "name": "archicad_addon",
        "status": "not_checked",
        "ready": False,
        "note": "宿主实测必须经 workbench/host_verification_service.run() 在真机会话执行"
                "（打开项目→编译→host 验证→证据落盘）；离线探测不得写'已安装'。",
        "entry": "POST /api/host/verify（WorkbenchSession 内）",
    }


def probe_python_env() -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "python_runtime",
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    from openbrep.config import _auto_detect_converter  # noqa: F401  (import sanity)

    entry["status"] = "ready"
    entry["ready"] = True
    return entry


PROBES = (probe_python_env, probe_lp, probe_browser, probe_codex, probe_archicad)


def main() -> int:
    parser = argparse.ArgumentParser(description="U00-B 环境能力探针（实际回读，非'已安装'）")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true", help="任何 not ready 退出码 1")
    args = parser.parse_args()

    result = {
        "probe": "openbrep-capability/U00-B",
        "schema_version": 1,
        "created_at": NOW(),
        "capabilities": [],
        "manifest_validator": "benchmark/asset_manifest.py (validate_manifest)",
        "note": "素材 manifest 与构件预期为 HITL：需要维护者提供素材与建筑师确认记录；"
                "缺素材只阻塞依赖素材的卡，不阻塞工程链。",
    }
    for probe in PROBES:
        try:
            result["capabilities"].append(probe())
        except Exception as exc:
            result["capabilities"].append({
                "name": probe.__name__,
                "status": "error",
                "ready": False,
                "note": f"{type(exc).__name__}: {exc}".splitlines()[0],
            })
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(f"capability result → {args.output}")
    for cap in result["capabilities"]:
        state = "READY" if cap.get("ready") else str(cap.get("status") or "not_ready").upper()
        print(f"  {cap['name']:<32} {state}")
    if args.strict and not all(c.get("ready") for c in result["capabilities"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
