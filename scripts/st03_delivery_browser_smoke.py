#!/usr/bin/env python3
"""ST03 U01–U06 真浏览器冒烟（Playwright + workbench）。

覆盖验收退回项：
- F1：partial 卡「查看差异」请求 before→工作源，且 UI 非空 diff
- F2：刷新后 delivery 卡恢复 / 旧记录 unlinked / continue 关联可追溯
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def post_json(url: str, body: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"ok": False, "error": f"HTTP {exc.code}: {detail[:300]}", "url": url}


def wait_for_url(url: str, *, timeout_seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        try:
            process.wait(timeout=5)
        except Exception:
            pass


def create_fixture_project(work_dir: str | Path) -> tuple[Path, str]:
    from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
    from openbrep.revisions import create_revision

    project = HSFProject.create_new("St03DeliveryUI", str(work_dir))
    project.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层板数", value="3"))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")
    root = Path(project.save_to_disk())
    # before revision（干净源）
    before = create_revision(root, message="before partial", trigger="manual")
    # 工作树部分修改（无 after revision）—— F1 差异应能看到 PRIM
    script = root / "scripts" / "3d.gdl"
    script.write_text("BLOCK A, B, ZZYZX\nPRIM 1, 1, 1, A/2\n", encoding="utf-8")
    return root, before.revision_id


def seed_history(api_url: str, before_id: str) -> dict[str, Any]:
    """通过 API 写入带 delivery meta 的聊天历史（模拟超时 partial 与旧记录）。"""
    partial_presentation = {
        "state": "partial_change",
        "status": "incomplete",
        "unlinked": False,
        "headline": "未完成，存在部分修改",
        "reason": "任务中断或验证未完成，存在部分修改；after 版本未创建",
        "show_success_badge": False,
        "show_before_after": False,
        "show_changed_files": True,
        "can_recover": True,
        "can_continue": True,
        "can_view_diff": True,
        "diff_target": "working",
        "recover_revision_id": before_id,
        "before_revision_id": before_id,
        "after_revision_id": None,
        "changed_files": ["scripts/3d.gdl"],
        "run_id": "r_st03_browser_partial",
        "error_code": None,
        "check_status": "unknown",
        "version_status": "skipped",
        "original_instruction": "把层板数改成 5",
        "continued_from": None,
    }
    unchanged_presentation = {
        **partial_presentation,
        "state": "unchanged",
        "status": "no_change",
        "headline": "未产生源码变化",
        "reason": "请求了修改，但未检测到源码差异",
        "show_changed_files": False,
        "can_recover": False,
        "can_continue": True,
        "can_view_diff": False,
        "diff_target": None,
        "recover_revision_id": None,
        "before_revision_id": None,
        "changed_files": [],
        "run_id": "r_st03_browser_unchanged",
        "original_instruction": "把层板数改成 5",
    }
    continued_presentation = {
        **partial_presentation,
        "run_id": "r_st03_browser_continue",
        "continued_from": {
            "origin_run_id": "r_st03_browser_partial",
            "original_instruction": "把层板数改成 5",
        },
    }
    messages = [
        {"role": "user", "content": "把层板数改成 5"},
        {
            "role": "assistant",
            "content": "未完成，存在部分修改\n\nChanged files: scripts/3d.gdl",
            "delivery": partial_presentation,
            "delivery_source": {
                "schema_version": 1,
                "run_id": "r_st03_browser_partial",
                "state": "partial_change",
                "before_revision_id": before_id,
                "after_revision_id": None,
                "source_fingerprint": None,
                "changed_files": ["scripts/3d.gdl"],
                "snapshot_status": "skipped",
                "error_code": None,
            },
            "delivery_continue_from": {
                "origin_run_id": "r_st03_browser_partial",
                "original_instruction": "把层板数改成 5",
            },
            "original_instruction": "把层板数改成 5",
            "run_id": "r_st03_browser_partial",
            "changed_files": ["scripts/3d.gdl"],
        },
        {"role": "user", "content": "把层板数改成 5"},
        {
            "role": "assistant",
            "content": "未产生源码变化\n\nChanged files: scripts/3d.gdl",
            "delivery": unchanged_presentation,
            "original_instruction": "把层板数改成 5",
            "run_id": "r_st03_browser_unchanged",
        },
        {"role": "user", "content": "把层板数改成 5"},
        {
            "role": "assistant",
            "content": "未完成，存在部分修改（继续轮）\n\nChanged files: scripts/3d.gdl",
            "delivery": continued_presentation,
            "delivery_continue_from": continued_presentation["continued_from"],
            "original_instruction": "把层板数改成 5",
            "run_id": "r_st03_browser_continue",
        },
        {
            "role": "assistant",
            "content": "历史遗留解释，没有交付卡。",
        },
        # F2 旧记录：任务痕迹但无 delivery meta → 刷新后应显示 unlinked
        {
            "role": "assistant",
            "content": "Changed files: scripts/legacy.gdl",
        },
    ]
    return post_json(f"{api_url}/api/assistant/history", {"messages": messages})


def ready_script() -> str:
    return """
        () => {
            return document.title.trim() === 'OpenBrep Workbench'
                && (document.body.innerText.includes('3d.gdl') || document.body.innerText.includes('脚本'));
        }
    """


def run_smoke(*, root: Path | None = None, timeout: float = 60.0, headed: bool = False) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "status": "skip",
            "reason": "missing_playwright",
            "detail": str(exc),
        }

    root_path = Path(root or ROOT)
    api_port = find_free_port()
    web_port = find_free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"
    command = [str(root_path / "obr7"), "--no-open"]
    env = os.environ.copy()
    env.update({"OBR7_API_PORT": str(api_port), "OBR7_WEB_PORT": str(web_port)})

    cases: list[dict[str, Any]] = []
    process: subprocess.Popen[str] | None = None
    temp_root: tempfile.TemporaryDirectory[str] | None = None
    screenshots_dir = root_path / ".openbrep" / "evidence" / "st03_browser"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    try:
        temp_root = tempfile.TemporaryDirectory(prefix="openbrep_st03_ui_")
        project_root, before_id = create_fixture_project(temp_root.name)
        # Never inherit the developer's configuration or use a live model.
        config_path = Path(temp_root.name) / "smoke.toml"
        config_path.write_text(
            '[llm]\nmodel = "st03/smoke"\n'
            '[[llm.providers]]\nname = "st03"\napi = "http://127.0.0.1:1/v1"\n'
            'api_key = "smoke-only"\nmodels = ["smoke"]\n'
            '[compiler]\nmode = "mock"\npath = ""\n',
            encoding="utf-8",
        )
        env["GDL_AGENT_CONFIG"] = str(config_path)
        process = subprocess.Popen(
            command,
            cwd=str(root_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        api_ready = wait_for_url(f"{api_url}/api/snapshot", timeout_seconds=timeout)
        if not api_ready:
            return {"ok": False, "status": "fail", "reason": "api_not_ready"}
        load_result = post_json(f"{api_url}/api/project/load", {"path": str(project_root)})
        if not load_result.get("ok"):
            return {"ok": False, "status": "fail", "reason": "project_load_failed", "detail": load_result}
        seed = seed_history(api_url, before_id)
        if not seed.get("ok"):
            return {"ok": False, "status": "fail", "reason": "history_seed_failed", "detail": seed}
        web_ready = wait_for_url(web_url, timeout_seconds=timeout)
        if not web_ready:
            return {"ok": False, "status": "fail", "reason": "web_not_ready"}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(web_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            page.wait_for_function(ready_script(), timeout=int(timeout * 1000))

            def close_settings_drawer() -> None:
                for selector in (
                    '.settings-drawer button[aria-label*="关闭"]',
                    '.settings-drawer button:has-text("关闭")',
                    '.settings-drawer .settings-close',
                    '[aria-label="工作台设置"] button',
                ):
                    try:
                        loc = page.locator(selector).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=1500)
                            page.wait_for_timeout(200)
                    except Exception:
                        continue
                # Esc 兜底
                try:
                    if page.locator(".settings-drawer.open").count():
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                except Exception:
                    pass

            close_settings_drawer()
            # 打开 AI 面板（避免误点设置）
            try:
                ai_btn = page.locator("button", has_text=re.compile(r"^AI$|对话|助手", re.I)).first
                if ai_btn.count():
                    ai_btn.click(timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            close_settings_drawer()

            # Vite module import exposes the actual store for observations, not a fake.
            page.evaluate("""async () => {
                window.st03Store = (await import('/src/state/workbenchStore.ts')).workbenchStore;
            }""")
            def draft_state():
                return page.evaluate("""() => {
                    const s = window.st03Store.getState();
                    return {scripts: s.scriptContents, dirty: s.dirtyScripts,
                            params: s.draftParameters, project: s.project?.path};
                }""")

            # ── U01 partial 卡 ──────────────────────────────────────────
            u01_card = page.locator('[data-delivery-state="partial_change"]').first
            u01_visible = u01_card.count() > 0 and u01_card.is_visible()
            u01_headline = ""
            u01_no_green = True
            u01_diff_btn = page.locator('[data-testid="delivery-view-diff"]').first
            u01_recover_btn = page.locator('[data-testid="delivery-recover"]').first
            u01_files = page.locator('[data-testid="delivery-changed-files"]').first
            if u01_visible:
                u01_headline = u01_card.locator('[data-testid="delivery-headline"]').inner_text()
                u01_no_green = u01_card.locator('[data-testid="delivery-success-badge"]').count() == 0
            cases.append(
                {
                    "case": "U01",
                    "passed": bool(
                        u01_visible
                        and "未完成" in u01_headline
                        and u01_no_green
                        and u01_diff_btn.count() > 0
                        and u01_recover_btn.count() > 0
                        and u01_files.count() > 0
                    ),
                    "visible": u01_visible,
                    "headline": u01_headline,
                    "no_green": u01_no_green,
                    "diff_btn": u01_diff_btn.count() > 0,
                    "recover_btn": u01_recover_btn.count() > 0,
                    "files": u01_files.count() > 0,
                }
            )

            # ── F1/U01：点差异 → before→工作源真实内容 ──────────────────
            f1_diff_text = ""
            f1_to_working = False
            if u01_diff_btn.count():
                # 只点 partial 卡上的 diff（第一个）
                close_settings_drawer()
                try:
                    u01_card.locator('[data-testid="delivery-view-diff"]').click(timeout=8000, force=True)
                except Exception:
                    page.locator('[data-testid="delivery-view-diff"]').first.click(timeout=8000, force=True)
                page.wait_for_timeout(600)
                panel = page.locator('[data-testid="delivery-diff-panel"]').first
                if panel.count():
                    f1_diff_text = panel.inner_text()
                    f1_to_working = "__working__" in f1_diff_text or "PRIM" in f1_diff_text
            # API 层再钉一次：partial 的 diff 请求不得是 before→before
            api_diff = post_json(
                f"{api_url}/api/project/revision/diff",
                {"from_revision_id": before_id},  # 无 to → 工作源
            )
            api_diff_ok = bool(
                api_diff.get("ok")
                and api_diff.get("to_working_tree") is True
                and api_diff.get("changed") is True
                and "PRIM" in (api_diff.get("diff") or "")
            )
            cases.append(
                {
                    "case": "F1/U01-diff",
                    "passed": api_diff_ok and (f1_to_working or "PRIM" in f1_diff_text),
                    "api_working_diff": api_diff_ok,
                    "ui_diff_contains_prim_or_working": f1_to_working or "PRIM" in f1_diff_text,
                    "api_diff_prefix": (api_diff.get("diff") or "")[:160],
                    "api_to_working_tree": api_diff.get("to_working_tree"),
                }
            )

            # ── F2/U04：刷新后 delivery 卡仍在 / 旧记录 unlinked ────────
            page.reload(wait_until="domcontentloaded")
            page.evaluate("""async () => {
                window.st03Store = (await import('/src/state/workbenchStore.ts')).workbenchStore;
            }""")
            page.wait_for_function(ready_script(), timeout=int(timeout * 1000))
            close_settings_drawer()
            try:
                ai_btn = page.locator("button", has_text=re.compile(r"^AI$|对话|助手", re.I)).first
                if ai_btn.count():
                    ai_btn.click(timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(900)
            close_settings_drawer()
            body1 = page.locator("body").inner_text(timeout=5000)
            f2_partial = page.locator('[data-delivery-state="partial_change"]').count() > 0
            f2_unlinked = page.locator('[data-delivery-status="unlinked"]').count() > 0
            f2_unlinked_text = "旧记录" in body1 or "未关联" in body1
            cases.append(
                {
                    "case": "F2/U04-reload",
                    "passed": bool(f2_partial and (f2_unlinked or f2_unlinked_text)),
                    "partial_card_after_reload": f2_partial,
                    "unlinked_card": f2_unlinked,
                    "unlinked_text": f2_unlinked_text,
                }
            )

            # ── U05 无 diff 文案 ────────────────────────────────────────
            u05_card = page.locator('[data-delivery-state="unchanged"]').first
            u05_ok = False
            u05_headline = ""
            if u05_card.count():
                u05_headline = u05_card.locator('[data-testid="delivery-headline"]').inner_text()
                u05_ok = "未产生源码变化" in u05_headline and "已修复" not in u05_headline
                u05_ok = u05_ok and u05_card.locator('[data-testid="delivery-success-badge"]').count() == 0
            cases.append(
                {
                    "case": "U05",
                    "passed": u05_ok,
                    "headline": u05_headline,
                    "in_body": "未产生源码变化" in body1,
                }
            )

            # ── U06 continue 关联可追溯（先断言可见性，点击放到恢复用例之后）──
            u06_cont = page.locator('[data-testid="delivery-continued-from"]').first
            u06_run = page.locator('[data-testid="delivery-run"]').first
            u06_instr = page.locator('[data-testid="delivery-original-instruction"]').first
            u06_ok = False
            u06_detail = {}
            if u06_cont.count():
                u06_detail["continued_from"] = u06_cont.inner_text()
                u06_ok = "r_st03_browser_partial" in u06_detail["continued_from"]
            if u06_run.count():
                u06_detail["run"] = u06_run.get_attribute("title") or u06_run.inner_text()
            if u06_instr.count():
                u06_instr.locator("xpath=..").locator("summary").click()
                u06_detail["instruction"] = u06_instr.inner_text()
                u06_ok = u06_ok or "把层板数改成 5" in u06_detail["instruction"]
            u06_ok = (
                "r_st03_browser_partial" in u06_detail.get("continued_from", "")
                and u06_detail.get("instruction") == "把层板数改成 5"
                and page.locator('[data-testid="delivery-run"]').count() >= 2
            )
            cases.append({"case": "U06-visible", "passed": u06_ok, **u06_detail})

            # ── U02/U03 恢复草稿保护 ────────────────────────────────────
            close_settings_drawer()
            # 切到脚本编辑舞台并制造脏稿
            edit_note = ""
            try:
                # 左侧脚本树
                script_item = page.locator(".script-tree-item", has_text="3d.gdl").first
                script_item.click(timeout=4000)
                page.wait_for_timeout(400)
                editor = page.locator(".monaco-editor").first
                editor.wait_for(timeout=5000)
                editor.click()
                page.keyboard.press("ControlOrMeta+End")
                page.keyboard.type("\n! st03 draft\n")
                page.wait_for_timeout(600)
                edit_note = page.locator("body").inner_text(timeout=3000)
            except Exception as edit_exc:
                edit_note = str(edit_exc)

            # 切到 AI 面板
            try:
                page.locator("button", has_text=re.compile(r"^AI$|对话|助手", re.I)).first.click(timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            close_settings_drawer()

            def js_click(testid: str) -> bool:
                return bool(
                    page.evaluate(
                        """
                        (tid) => {
                            const el = document.querySelector(`[data-testid="${tid}"]`);
                            if (!el) return false;
                            el.click();
                            return true;
                        }
                        """,
                        testid,
                    )
                )

            page.evaluate("""() => window.st03Store.setState({draftParameters: {A: 2.5}})""")
            drafts_before = draft_state()
            source_before = (project_root / "scripts/3d.gdl").read_text()
            draft_created = "! st03 draft" in drafts_before["scripts"].get("3d.gdl", "")
            # U02：恢复 → 取消
            u02_detail = {
                "edit_error": None if "st03 draft" in (edit_note or "") else (edit_note or "")[:180],
                "recover_clicked": js_click("delivery-recover"),
            }
            page.wait_for_timeout(600)
            dialog = page.locator('[role="dialog"]').first
            u02_detail["dialog"] = dialog.count()
            u02_passed = False
            if dialog.count():
                try:
                    dialog.locator("button", has_text="取消").first.click(timeout=3000)
                except Exception as cancel_exc:
                    u02_detail["cancel_error"] = str(cancel_exc)[:160]
                page.wait_for_timeout(500)
                body2 = page.locator("body").inner_text(timeout=3000)
                u02_detail["no_restore"] = "Restored revision" not in body2
                u02_detail["card_still_partial"] = page.locator('[data-delivery-state="partial_change"]').count() > 0
                u02_detail["drafts_unchanged"] = draft_state() == drafts_before
                u02_detail["source_unchanged"] = (project_root / "scripts/3d.gdl").read_text() == source_before
                u02_passed = bool(draft_created and u02_detail["no_restore"]
                                  and u02_detail["drafts_unchanged"] and u02_detail["source_unchanged"])
            cases.append({"case": "U02", "passed": u02_passed, **u02_detail})

            # U03：保留草稿并恢复
            page.evaluate("""() => window.st03Store.setState({
                previewGhost: {meshes: [], wires: [], warnings: ['st03-stale-preview']}
            })""")
            u03_detail = {"keep_clicked": js_click("delivery-recover-keep")}
            page.wait_for_timeout(600)
            dialog = page.locator('[role="dialog"]').first
            u03_detail["dialog"] = dialog.count()
            u03_passed = False
            if dialog.count():
                try:
                    dialog.locator("button", has_text="保留草稿并恢复").first.click(timeout=3000)
                except Exception as ok_exc:
                    u03_detail["ok_error"] = str(ok_exc)[:160]
                page.wait_for_timeout(1600)
                body3 = page.locator("body").inner_text(timeout=4000)
                u03_detail["restore_log"] = "Restored revision" in body3
                u03_detail["drafts_kept"] = "drafts kept" in body3
                u03_detail["body_snip"] = body3[-500:]
                u03_detail["drafts_preserved"] = draft_state() == drafts_before
                u03_detail["source_is_before"] = (project_root / "scripts/3d.gdl").read_bytes() == (project_root / ".openbrep/revisions" / before_id / "scripts/3d.gdl").read_bytes()
                u03_detail["stale_preview_cleared"] = page.evaluate(
                    "() => window.st03Store.getState().previewGhost === null"
                )
                u03_passed = all(u03_detail.get(k) for k in (
                    "restore_log", "drafts_kept", "drafts_preserved", "source_is_before", "stale_preview_cleared"
                ))
            cases.append({"case": "U03", "passed": u03_passed, **u03_detail})

            # ── U06：点击继续（放在恢复用例之后，避免 busy 遮挡恢复入口）──
            close_settings_drawer()
            try:
                page.locator("button", has_text=re.compile(r"^AI$|对话|助手", re.I)).first.click(timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(400)
            continue_btn = page.locator('[data-testid="delivery-continue"]').first
            u06_click = {"continue_btn": continue_btn.count(), "clicked_continue": False}
            if continue_btn.count():
                with page.expect_response(
                    lambda response: response.url.endswith('/api/assistant/generate')
                    and response.request.method == 'POST', timeout=20000
                ) as response_info:
                    continue_btn.click(force=True)
                response = response_info.value
                request_body = response.request.post_data_json
                result = response.json()
                ds = (result.get("assistant") or {}).get("delivery_source") or {}
                u06_click.update({
                    "request": {k: request_body.get(k) for k in ("message", "continue_from")},
                    "delivery_source": ds,
                    "new_run": ds.get("run_id") not in (None, "r_st03_browser_partial"),
                    "own_revisions": bool(ds.get("before_revision_id") and ds.get("after_revision_id")
                                          and ds["before_revision_id"] != ds["after_revision_id"]),
                })
                u06_click["clicked_continue"] = True
                page.wait_for_timeout(700)
            cases.append({
                "case": "U06-click",
                "passed": bool(u06_click.get("new_run") and u06_click.get("own_revisions")
                               and u06_click.get("request", {}).get("message") == "把层板数改成 5"
                               and (u06_click.get("request", {}).get("continue_from") or {}).get("origin_run_id") == "r_st03_browser_partial"
                               and (u06_click.get("request", {}).get("continue_from") or {}).get("original_instruction") == "把层板数改成 5"),
                **u06_click,
            })

            # U04: hold an old task response, open another real project, then release it.
            other_root, _ = create_fixture_project(Path(temp_root.name) / "other")
            held = []
            def hold_generate(route):
                held.append(route)
            page.route("**/api/assistant/generate", hold_generate)
            page.evaluate("""() => {
                window.st03OldTask = window.st03Store.getState().sendChat('把颜色改成红色');
            }""")
            deadline = time.monotonic() + 10
            while not held and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            if not held:
                raise AssertionError("U04: old task request was not captured")
            page.evaluate("async path => await window.st03Store.getState().loadProjectPath(path)", str(other_root))
            def project_state():
                return page.evaluate("""() => {
                    const s = window.st03Store.getState();
                    return {project: s.project, scripts: s.scriptContents,
                            revisions: s.revisions, panel: s.activeRailPanel,
                            messages: s.assistantMessages, preview: s.preview};
                }""")
            switched = project_state()
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "assistant": {"kind": "generate", "reply": "STALE_ST03_RESULT",
                "changed_files": ["scripts/3d.gdl"], "intent": "MODIFY"},
                "preview": {"meshes": [], "wires": [], "warnings": ["STALE_ST03_PREVIEW"]},
            }))
            page.evaluate("async () => await window.st03OldTask")
            new_project_preserved = Path(project_state()["project"]["path"]).resolve() == other_root.resolve()
            cases.append({"case": "U04-project-switch", "passed": project_state() == switched and new_project_preserved,
                          "new_project_preserved": new_project_preserved})

            # 截图证据
            try:
                page.screenshot(path=str(screenshots_dir / "st03-u01-u06-final.png"), full_page=True)
            except Exception:
                pass
            browser.close()

        all_passed = all(c.get("passed") for c in cases)
        return {
            "ok": all_passed,
            "status": "pass" if all_passed else "fail",
            "api_url": api_url,
            "web_url": web_url,
            "project": str(project_root),
            "before_revision_id": before_id,
            "screenshots_dir": str(screenshots_dir),
            "cases": cases,
        }
    finally:
        if process is not None:
            terminate_process(process)
        if temp_root is not None:
            temp_root.cleanup()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    headed = "--headed" in args
    timeout = 60.0
    for i, token in enumerate(args):
        if token == "--timeout" and i + 1 < len(args):
            timeout = float(args[i + 1])
    result = run_smoke(timeout=timeout, headed=headed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = ROOT / ".openbrep" / "evidence" / "st03_browser"
    out.mkdir(parents=True, exist_ok=True)
    (out / "st03-u01-u06.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
