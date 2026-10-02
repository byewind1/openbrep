#!/usr/bin/env python3
"""ST04 审批卡真浏览器冒烟（Playwright + workbench）。

覆盖：
- 审批卡渲染 draft 状态、来源 run、证据完整性（K08 标注）；
- 点「批准沉淀」时请求体带 proposal_id（走持久候选 store，而非旧 pending）；
- verify 通过/失败两种响应分别给出「已通过验证」/「未激活产物」文案（K04）。

安全：本脚本用 Playwright route 拦截 /api/skill/confirm 并以假响应 fulfill，
绝不触发真实 propose_skill / verify_skill，因此不会写入仓库 skills/ 目录。

用法：
    python scripts/st04_skill_proposal_browser_smoke.py [--headed] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# 确保用仓库代码而不是 site-packages 里的旧 openbrep（脚本 cwd 可能不同）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def post_json(url: str, body: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
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
        return {"ok": False, "error": f"HTTP {exc.code}: {detail[:300]}"}


def get_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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


def create_fixture_project(work_dir: str | Path) -> Path:
    from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType

    project = HSFProject.create_new("St04SkillProposal", str(work_dir))
    project.parameters.append(
        GDLParameter(name="step_count", type_tag="Integer", description="总步数", value="16")
    )
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    return Path(project.save_to_disk())


def seed_restore_candidate(project_root: Path) -> str:
    """把一条 draft 候选直接写进项目 store，用于验证"重启/加载后恢复审批卡"。"""
    from openbrep.skill_proposals import project_identity, save_candidate

    identity = project_identity(project_root, "St04SkillProposal")
    proposal_id = "sp_st04_smoke_restore"
    candidate = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "project": identity,
        "project_epoch": None,
        "instruction": "把这轮修改沉淀成楼梯skill",
        "name": "restored_stair_pattern",
        "pattern_type": "repeating_geometry",
        "content": "## 适用场景 / When to Use\n加载后应恢复的持久候选。\n\n## 写法要点\n- 用 FOR 循环堆叠。",
        "slice": None,
        "fingerprint": "sha256:" + "0" * 64,
        "source_refs": [],
        "evidence_complete": False,
        "claims": {"unverified": [], "project_selection": None},
        "artifact": None,
        "protection": None,
        "status": "draft",
        "verification": {"state": "unverified"},
        "created_at": "2026-09-18T00:00:00+00:00",
        "updated_at": "2026-09-18T00:00:00+00:00",
        "error": None,
        "store_error": None,
        "approved_path": None,
    }
    save_candidate(project_root, candidate)
    return proposal_id


CANDIDATE = {
    "proposal_id": "sp_st04_smoke_0001",
    "status": "draft",
    "name": "spiral_stair_stack",
    "pattern_type": "repeating_geometry",
    "content": "## 适用场景 / When to Use\n螺旋楼梯踏步按总步数参数化堆叠时。\n\n## 写法要点\n- FOR 循环堆叠 + ROT 均分。",
    "evidence": {
        "source": "explicit",
        "intent": "MODIFY",
        "project": "St04SkillProposal",
        "changed_files": ["scripts/3d.gdl"],
        "source_run_ids": ["r_st04_smoke"],
        "revisions": ["r0002"],
        "evidence_complete": True,
    },
}

VERIFIED_REPLY = {
    "ok": True,
    "proposal_id": "sp_st04_smoke_0001",
    "skill": "spiral_stair_stack",
    "verified": True,
    "gate": "structural",
    "status": "verified",
}
FAILED_REPLY = {
    "ok": True,
    "proposal_id": "sp_st04_smoke_0001",
    "skill": "spiral_stair_stack",
    "verified": False,
    "gate": "full",
    "status": "proposed",
}


def _store_import_script() -> str:
    return """async () => {
        window.st04Store = (await import('/src/state/workbenchStore.ts')).workbenchStore;
    }"""


def _render_proposal_script(proposal: dict[str, Any]) -> str:
    """安装 /api/assistant/generate 拦截并调用真实 store 动作，让审批卡落进真实渲染路径。

    候选与应答都直接内联进脚本常量，避免 page.evaluate 参数传递的边界问题。
    走真实动作（而不是裸 setState）是因为 pending 是项目级临时态，会被 load 类
    动作的 hydrateSnapshot 视为跨项目状态清空；真实交付路径才是生产出现方式。
    """
    payload = json.dumps(proposal, ensure_ascii=False)
    return """async () => {
        const proposal = %s;
        if (!window.__st04Installed) {
            window.__st04Installed = true;
            const original = window.fetch;
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : (input && input.url) || '';
                if (url.includes('/api/assistant/generate')) {
                    return new Response(JSON.stringify({
                        ok: true,
                        assistant: { kind: 'generate', reply: '已生成待审 skill 候选。', changed_files: [], intent: 'MODIFY' },
                        skill_proposal: proposal,
                        preview: null,
                        warnings: [],
                        events: [],
                    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                return original(input, init);
            };
        }
        window.__st04Log = [];
        const unsub = window.st04Store.subscribe((s) => {
            window.__st04Log.push(s.pendingSkillProposal ? s.pendingSkillProposal.proposal_id : null);
        });
        try {
            await window.st04Store.getState().generateAssistantChanges('把这轮修改沉淀成楼梯skill', []);
        } catch (err) {
            window.__st04Error = String(err && err.stack ? err.stack : err);
        }
        unsub();
        const s = window.st04Store.getState();
        return { pending: s.pendingSkillProposal, error: window.__st04Error || null, log: window.__st04Log };
    }""" % payload


def run_smoke(*, timeout: float = 60.0, headed: bool = False) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "status": "skip", "reason": "missing_playwright", "detail": str(exc)}

    api_port = find_free_port()
    web_port = find_free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"
    command = [str(ROOT / "obr7"), "--no-open"]
    env = os.environ.copy()
    env.update({"OBR7_API_PORT": str(api_port), "OBR7_WEB_PORT": str(web_port)})

    cases: list[dict[str, Any]] = []
    process: subprocess.Popen[str] | None = None
    temp_root: tempfile.TemporaryDirectory[str] | None = None
    try:
        temp_root = tempfile.TemporaryDirectory(prefix="openbrep_st04_ui_")
        project_root = create_fixture_project(temp_root.name)
        config_path = Path(temp_root.name) / "smoke.toml"
        config_path.write_text(
            '[llm]\nmodel = "st04/smoke"\n'
            '[[llm.providers]]\nname = "st04"\napi = "http://127.0.0.1:1/v1"\n'
            'api_key = "smoke-only"\nmodels = ["smoke"]\n'
            '[compiler]\nmode = "mock"\npath = ""\n',
            encoding="utf-8",
        )
        env["GDL_AGENT_CONFIG"] = str(config_path)
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        if not wait_for_url(f"{api_url}/api/snapshot", timeout_seconds=timeout):
            return {"ok": False, "status": "fail", "reason": "api_not_ready"}
        load_result = post_json(f"{api_url}/api/project/load", {"path": str(project_root)})
        if not load_result.get("ok"):
            return {"ok": False, "status": "fail", "reason": "project_load_failed", "detail": load_result}
        # 空候选列表（API 层合同）
        listing = get_json(f"{api_url}/api/skill/proposals")
        cases.append({
            "case": "API-list-empty",
            "passed": bool(listing.get("ok") and listing.get("total") == 0),
            "listing": listing,
        })
        if not wait_for_url(web_url, timeout_seconds=timeout):
            return {"ok": False, "status": "fail", "reason": "web_not_ready"}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            captured: list[dict[str, Any]] = []
            reply = {"body": VERIFIED_REPLY}

            def handle_confirm(route):
                try:
                    captured.append(json.loads(route.request.post_data or "{}"))
                except Exception:
                    captured.append({})
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(reply["body"]),
                )

            page.route("**/api/skill/confirm", handle_confirm)
            page.goto(web_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            page.wait_for_function(
                "() => document.title.trim() === 'OpenBrep Workbench'", timeout=int(timeout * 1000)
            )
            page.evaluate(_store_import_script())
            # 等 store 完成初始 load（否则 load() 落地时会重置 pending）
            page.wait_for_function(
                "() => { const s = window.st04Store && window.st04Store.getState(); return !!(s && s.project); }",
                timeout=int(timeout * 1000),
            )
            # 打开 AI 面板（审批卡挂在该 rail panel 内）
            try:
                ai_btn = page.locator("button", has_text=re.compile(r"AI")).first
                if ai_btn.count():
                    ai_btn.click(timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            render_result = page.evaluate(_render_proposal_script(CANDIDATE))
            page.wait_for_timeout(900)
            probe = page.evaluate(
                """() => {
                    const s = window.st04Store.getState();
                    return {
                        pending: s.pendingSkillProposal,
                        panel: s.activeRailPanel,
                        hasProject: !!s.project,
                        cardCount: document.querySelectorAll('.skill-proposal-card').length,
                    };
                }"""
            )

            card = page.locator(".skill-proposal-card").first
            card_visible = card.count() > 0 and card.is_visible()
            card_text = card.inner_text() if card_visible else ""
            body_dump = page.locator("body").inner_text(timeout=5000)
            cases.append({
                "case": "ST04-card-render",
                "passed": bool(
                    card_visible
                    and "spiral_stair_stack" in card_text
                    and "draft" in card_text
                    and "证据完整" in card_text
                    and "r_st04_smoke" in card_text
                ),
                "visible": card_visible,
                "text": card_text[:400],
                "probe": probe,
                "render_result": render_result,
                "body_head": "" if card_visible else body_dump[:400],
            })

            # ── 批准：请求必须带 proposal_id，成功文案不声称未验证可用 ──
            approve_btn = page.get_by_role("button", name="批准沉淀").first
            if approve_btn.count():
                approve_btn.click(timeout=5000)
            page.wait_for_timeout(700)
            approve_body = captured[-1] if captured else {}
            body_text = page.locator("body").inner_text(timeout=5000)
            cases.append({
                "case": "ST04-approve-verified",
                "passed": bool(
                    approve_body.get("approve") is True
                    and approve_body.get("proposal_id") == "sp_st04_smoke_0001"
                    and "已沉淀并通过验证" in body_text
                ),
                "request": approve_body,
                "reply_contains_passed": "已沉淀并通过验证" in body_text,
            })

            # ── 失败验证：仍落盘但未激活，不声称可用 ──
            reply["body"] = FAILED_REPLY
            page.evaluate(_render_proposal_script(CANDIDATE))
            page.wait_for_timeout(700)
            failed_btn = page.get_by_role("button", name="批准沉淀").first
            if failed_btn.count():
                failed_btn.click(timeout=5000)
            page.wait_for_timeout(700)
            failed_last = page.evaluate(
                "() => { const m = window.st04Store.getState().assistantMessages; return m.length ? m[m.length - 1].content : ''; }"
            )
            cases.append({
                "case": "ST04-approve-verify-failed",
                "passed": bool(
                    "未激活" in failed_last and "已沉淀并通过验证" not in failed_last
                ),
                "last_message": failed_last,
            })

            # ── 忽略：approve=false，卡片清空 ──
            reply["body"] = {"ok": True, "discarded": True, "proposal_id": "sp_st04_smoke_0001"}
            page.evaluate(_render_proposal_script(CANDIDATE))
            page.wait_for_timeout(700)
            ignore_btn = page.get_by_role("button", name="忽略").first
            if ignore_btn.count():
                ignore_btn.click(timeout=5000)
            page.wait_for_timeout(700)
            reject_body = captured[-1] if captured else {}
            cases.append({
                "case": "ST04-reject",
                "passed": bool(
                    reject_body.get("approve") is False
                    and reject_body.get("proposal_id") == "sp_st04_smoke_0001"
                ),
                "request": reject_body,
            })

            # ── ST04-restore-on-load：预置持久候选 → 刷新（≈重启）→ 自动恢复审批卡 ──
            restore_id = seed_restore_candidate(project_root)
            seeded_listing = get_json(f"{api_url}/api/skill/proposals")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.title.trim() === 'OpenBrep Workbench'", timeout=int(timeout * 1000)
            )
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll('.skill-proposal-card'))
                    .some((node) => (node.textContent || '').includes('restored_stair_pattern'))""",
                timeout=int(timeout * 1000),
            )
            restored_card = page.locator(".skill-proposal-card").filter(
                has_text="restored_stair_pattern"
            )
            seeded_ids = [
                item.get("proposal_id")
                for item in seeded_listing.get("proposals", [])
                if isinstance(item, dict)
            ]
            cases.append({
                "case": "ST04-restore-on-load",
                "passed": restore_id in seeded_ids and restored_card.count() > 0,
                "api_proposal_ids": seeded_ids,
                "restored_card_text": restored_card.first.inner_text()[:300]
                if restored_card.count()
                else "",
                "card_count": restored_card.count(),
            })
            browser.close()

        return {"ok": all(c["passed"] for c in cases), "status": "pass" if all(c["passed"] for c in cases) else "fail", "cases": cases}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "fail", "reason": "exception", "detail": str(exc), "cases": cases}
    finally:
        if process is not None:
            terminate_process(process)
        if temp_root is not None:
            temp_root.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--json", type=str, default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    result = run_smoke(timeout=args.timeout, headed=args.headed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
