#!/usr/bin/env python3
"""SF1 draft-safety browser smoke: replay F01-F05 through the real React workbench.

Launches the workbench through ./obr7 on random ports with a credential-free
config, drives real Chromium via Playwright, and asserts on disk state plus
editor state. Exit code reflects per-case ``passed`` flags; the JSON output is
the source of truth (do not treat exit 0 alone as product success).

Only temporary HSF fixtures under a TemporaryDirectory are touched. No real
LLM calls (mock compiler), no Archicad projects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.workbench_browser_smoke import (  # noqa: E402
    build_obr7_launch,
    find_free_port,
    terminate_process,
    wait_for_url,
)


def post_json(url: str, body: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def create_fixture(root: Path, name: str) -> Path:
    from openbrep.hsf_project import HSFProject, ScriptType

    project = HSFProject.create_new(name, str(root))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")
    return Path(project.save_to_disk())


def prepare_isolated_config(source: Path, root: Path) -> Path:
    """Copy the credential-free config into the temp root and force output_dir / mock compiler."""
    dest = root / "sf1-test-config.toml"
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Drop any existing output_dir lines to avoid duplicates, and force mock compiler.
    filtered = [line for line in lines if not line.strip().startswith("output_dir")]
    in_compiler = False
    forced: list[str] = []
    for line in filtered:
        stripped = line.strip()
        if stripped == "[compiler]":
            in_compiler = True
        elif stripped.startswith("[") and stripped.endswith("]"):
            in_compiler = False
        if in_compiler and stripped.startswith("mode"):
            line = 'mode = "mock"'
        forced.append(line)

    # Insert top-level output_dir right before the first section header so it stays
    # in the root table (not accidentally under [compiler] or another section).
    first_section_index = next(
        (i for i, line in enumerate(forced) if line.strip().startswith("[") and line.strip().endswith("]")),
        len(forced),
    )
    insertion = [
        "",
        "# SF1-R1: force all exports/GSM output into the isolated temp root",
        f'output_dir = "{root / "exports"}"',
    ]
    final = forced[:first_section_index] + insertion + forced[first_section_index:]
    dest.write_text("\n".join(final), encoding="utf-8")
    return dest


def sha256_tree(path: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for file in sorted(path.rglob("*")):
        if file.is_file():
            digests[str(file.relative_to(path))] = hashlib.sha256(file.read_bytes()).hexdigest()
    return digests


def project_name(page: Any) -> str:
    return page.locator(".brand-lockup strong").inner_text(timeout=5000)


class Probe:
    def __init__(self, page: Any, api_url: str, web_url: str, timeout: float) -> None:
        self.page = page
        self.api_url = api_url
        self.web_url = web_url
        self.timeout = timeout

    def snapshot(self) -> dict[str, Any]:
        return get_json(f"{self.api_url}/api/snapshot")

    def load_api(self, path: Path) -> None:
        result = post_json(f"{self.api_url}/api/project/load", {"path": str(path)})
        assert result.get("ok"), f"load failed: {result}"
        self.page.goto(self.web_url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
        self.page.wait_for_function(
            "() => document.querySelector('.brand-lockup strong') !== null",
            timeout=int(self.timeout * 1000),
        )

    def edit_script(self, name: str, content: str) -> None:
        page = self.page
        page.locator(".script-tree-item", has_text=name).first.click()
        editor = page.locator(".monaco-editor:visible").first
        editor.wait_for(timeout=int(self.timeout * 1000))
        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(content)
        marker = content.strip().splitlines()[-1]
        editor.wait_for(timeout=int(self.timeout * 1000))
        deadline = time.monotonic() + self.timeout
        last_text = ""
        while time.monotonic() < deadline:
            # Monaco renders spaces as non-breaking spaces in view lines.
            last_text = editor.inner_text().replace("\xa0", " ")
            if marker in last_text:
                break
            time.sleep(0.3)
        else:
            editors = page.evaluate(
                "() => Array.from(document.querySelectorAll('.monaco-editor')).length"
            )
            raise TimeoutError(
                f"edit marker {marker!r} did not appear in editor "
                f"(editor_count={editors}, last_text={last_text[:200]!r})"
            )

    def editor_text(self) -> str:
        return self.page.locator(".monaco-editor:visible").first.inner_text(timeout=5000)

    def open_script(self, name: str) -> None:
        self.page.locator(".script-tree-item", has_text=name).first.click()
        self.page.locator(".monaco-editor:visible").first.wait_for(timeout=int(self.timeout * 1000))

    def open_script_and_expect(self, name: str, marker: str) -> bool:
        """Open a script and poll until its visible editor shows the marker.

        The apply flow intentionally switches the editor to paramlist.xml
        (pre-existing refreshParameterSource behavior), so re-click the tree
        item until the expected content is visible.
        """
        deadline = time.monotonic() + self.timeout
        text = ""
        while time.monotonic() < deadline:
            self.open_script(name)
            text = self.editor_text()
            if marker in text:
                return True
            time.sleep(0.5)
        print(f"[smoke] marker {marker!r} not visible; last editor text: {text[:120]!r}")
        return False

    def click_top_save(self) -> None:
        pill = self.page.locator(".status-pill").first
        before = pill.inner_text() if pill.count() else ""
        self.page.get_by_test_id("save-script-button").click()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            pills = self.page.locator(".status-pill")
            texts = [pills.nth(i).inner_text() for i in range(pills.count())]
            saved = [t for t in texts if ("Saved" in t or "已保存" in t) and ":" in t]
            if saved and any(t != before for t in saved):
                return
            time.sleep(0.3)
        raise TimeoutError("top Save did not complete (saved-at status pill unchanged)")

    def snapshot_parameters(self) -> dict[str, str]:
        return {
            str(p.get("name")): str(p.get("value"))
            for p in self.snapshot().get("parameters", [])
        }

    def set_parameter(self, name: str, value: str) -> None:
        control = self.page.locator("label.parameter-control").filter(
            has=self.page.locator("span.parameter-name", has_text=name)
        ).first
        control.locator("input").fill(value)

    def open_project_menu(self) -> None:
        self.page.get_by_role("button", name="Project", exact=True).click()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def case_save_inactive_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """F01/AC01: inactive tab dirty -> Save persists it."""
    target = create_fixture(root, "F01SaveInactive")
    marker = "INACTIVE_DIRTY_F01"
    probe.load_api(target)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")
    probe.open_script("2d.gdl")  # switch to a clean tab
    probe.click_top_save()
    probe.open_script("3d.gdl")
    on_disk = marker in (target / "scripts" / "3d.gdl").read_text(encoding="utf-8")
    in_editor = marker in probe.editor_text()
    return {
        "case": "save_inactive_dirty",
        "expect": "inactive-tab draft saved to disk and still in editor",
        "actual": {"marker_on_disk": on_disk, "marker_in_editor": in_editor},
        "passed": on_disk and in_editor,
    }


def case_compile_multiple_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """Positive control (AC28): compile still flushes both dirty buffers."""
    target = create_fixture(root, "ControlCompile")
    probe.load_api(target)
    probe.edit_script("3d.gdl", "BLOCK A, B, ZZYZX\n! CONTROL_3D\n")
    probe.edit_script("2d.gdl", "PROJECT2 3, 270, 2\n! CONTROL_2D\n")
    probe.page.get_by_test_id("compile-button").click()
    probe.page.wait_for_function(
        "() => document.body.innerText.includes('Mock compile passed') || document.body.innerText.includes('编译通过')",
        timeout=int(probe.timeout * 1000),
    )
    ok = "CONTROL_3D" in (target / "scripts" / "3d.gdl").read_text(encoding="utf-8") and (
        "CONTROL_2D" in (target / "scripts" / "2d.gdl").read_text(encoding="utf-8")
    )
    return {
        "case": "compile_multiple_dirty",
        "expect": "both dirty tabs flushed to disk by compile",
        "actual": {"passed": ok},
        "passed": ok,
    }


def case_save_as_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """F02/AC15/AC20: Save As exports drafts to the copy; original untouched;
    unapplied parameter draft stays a draft."""
    target = create_fixture(root, "F02SaveAsSource")
    before = sha256_tree(target)
    marker_3d = "SAVE_AS_3D"
    marker_2d = "SAVE_AS_2D"
    probe.load_api(target)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker_3d}\n")
    probe.edit_script("2d.gdl", f"PROJECT2 3, 270, 2\n! {marker_2d}\n")
    probe.set_parameter("A", "2.5")  # parameter draft, NOT applied
    probe.open_project_menu()
    probe.page.get_by_role("button", name="Save As", exact=True).click()
    dialog = probe.page.get_by_role("dialog")
    dialog.get_by_role("textbox").fill("SaveAsCopy")
    dialog.get_by_role("button", name="Confirm", exact=True).click()
    probe.page.wait_for_function(
        "() => document.body.innerText.includes('Saved HSF source:')",
        timeout=int(probe.timeout * 1000),
    )
    snapshot = probe.snapshot()
    exported = Path(snapshot["project"]["path"])
    after = sha256_tree(target)
    body = probe.page.locator("body").inner_text(timeout=5000)
    exported_3d = (exported / "scripts" / "3d.gdl").read_text(encoding="utf-8")
    exported_2d = (exported / "scripts" / "2d.gdl").read_text(encoding="utf-8")
    new_params = probe.snapshot_parameters()
    exports_root = (root / "exports").resolve()
    actual = {
        "exported_path": str(exported),
        "exported_name": exported.name,
        "exported_is_new_dir": exported != target and exported.exists(),
        "exported_in_temp_root": exported.resolve().is_relative_to(exports_root),
        "exported_name_no_collision": exported.name == "SaveAsCopy",
        "marker_3d_in_export": marker_3d in exported_3d,
        "marker_2d_in_export": marker_2d in exported_2d,
        "original_unchanged": before == after,
        "kept_draft_notice": "Parameter drafts kept (not applied)" in body,
        "param_A_not_applied_in_copy": new_params.get("A") == "1",
        "param_input_still_draft": probe.page.locator("label.parameter-control")
        .filter(has=probe.page.locator("span.parameter-name", has_text="A"))
        .first.locator("input")
        .input_value()
        == "2.5",
    }
    passed = all(actual.values())
    return {
        "case": "save_as_dirty",
        "expect": "copy contains both drafts; original byte-identical; parameter draft kept, not applied",
        "actual": actual,
        "passed": passed,
    }


def case_first_save_after_new(probe: Probe, root: Path) -> dict[str, Any]:
    """R1-01: New project -> edit 3d.gdl -> first top Save -> name -> saved copy contains edit."""
    marker = "REVIEW_UNTITLED_KEEP"
    probe.page.goto(probe.web_url, wait_until="domcontentloaded", timeout=int(probe.timeout * 1000))
    probe.page.wait_for_function(
        "() => document.querySelector('.brand-lockup strong') !== null",
        timeout=int(probe.timeout * 1000),
    )
    probe.open_project_menu()
    probe.page.get_by_role("button", name="New", exact=True).click()
    # No drafts -> New should not show discard dialog
    probe.page.wait_for_timeout(800)
    if probe.page.get_by_role("dialog").count() > 0:
        raise AssertionError("New without drafts should not show discard dialog")
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")
    # 首次 Save（untitled 项目）→ 后端 needs_save_as → 前端弹命名对话框
    probe.page.get_by_test_id("save-script-button").click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("textbox").fill("ReviewFirstSave")
    dialog.get_by_role("button", name="Confirm", exact=True).click()
    probe.page.wait_for_function(
        "() => document.body.innerText.includes('Saved HSF source:')",
        timeout=int(probe.timeout * 1000),
    )
    snapshot = probe.snapshot()
    saved = Path(snapshot["project"]["path"])
    on_disk = marker in (saved / "scripts" / "3d.gdl").read_text(encoding="utf-8")
    in_temp_root = saved.resolve().is_relative_to((root / "exports").resolve())
    # Reload saved project and verify editor still shows the marker
    probe.load_api(saved)
    probe.open_script_and_expect("3d.gdl", marker)
    in_editor_after_reload = marker in probe.editor_text()
    actual = {
        "saved_path": str(saved),
        "saved_path_exists": saved.exists(),
        "saved_in_temp_root": in_temp_root,
        "marker_on_disk": on_disk,
        "marker_in_editor_after_reload": in_editor_after_reload,
    }
    passed = all(actual.values())
    return {
        "case": "first_save_after_new",
        "expect": "first Save of untitled project exports current script draft and reloads with it",
        "actual": actual,
        "passed": passed,
    }


def case_apply_parameter_while_script_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """F03/AC08: Apply flushes scripts first; both edits and parameter survive."""
    target = create_fixture(root, "F03ApplyDirty")
    marker = "APPLY_DIRTY_F03"
    probe.load_api(target)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")
    probe.set_parameter("A", "2.5")
    apply_button = probe.page.locator(".topbar .primary-action")
    apply_button.click()
    deadline = time.monotonic() + probe.timeout
    params: dict[str, str] = {}
    while time.monotonic() < deadline:
        params = probe.snapshot_parameters()
        if params.get("A") == "2.5":
            break
        time.sleep(0.4)
    else:
        raise TimeoutError(f"parameter A was not applied via backend snapshot: {params!r}")
    actual = {
        "marker_on_disk": marker in (target / "scripts" / "3d.gdl").read_text(encoding="utf-8"),
        "marker_in_editor": probe.open_script_and_expect("3d.gdl", marker),
        "param_A_applied": params.get("A") == "2.5",
    }
    passed = all(actual.values())
    return {
        "case": "apply_parameter_while_script_dirty",
        "expect": "script draft saved before parameter write; both survive",
        "actual": actual,
        "passed": passed,
    }


def case_open_while_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """F04/AC21-23: Open requires discard confirmation; cancel keeps drafts;
    discard+failure keeps original project and drafts."""
    first = create_fixture(root, "F04SwitchFirst")
    second = create_fixture(root, "F04SwitchSecond")
    marker = "SWITCH_DIRTY_F04"

    probe.load_api(first)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")

    # Cancel path: dialog appears, cancel keeps project + draft.
    probe.open_project_menu()
    probe.page.get_by_role("textbox", name="HSF project path").fill(str(second))
    probe.page.get_by_role("button", name="Open", exact=True).click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    probe.page.wait_for_timeout(500)
    cancel_kept = {
        "still_first_project": project_name(probe.page) == "F04SwitchFirst",
        "marker_in_editor": marker in probe.editor_text(),
        "marker_not_on_disk": marker not in (first / "scripts" / "3d.gdl").read_text(encoding="utf-8"),
    }

    # Discard path: confirm switches to second project; first project untouched.
    probe.open_project_menu()
    probe.page.get_by_role("textbox", name="HSF project path").fill(str(second))
    probe.page.get_by_role("button", name="Open", exact=True).click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("button", name="Discard and continue", exact=True).click()
    probe.page.wait_for_function(
        "() => document.querySelector('.brand-lockup strong')?.textContent === 'F04SwitchSecond'",
        timeout=int(probe.timeout * 1000),
    )
    discard_switched = {
        "switched_to_second": probe.snapshot()["project"]["name"] == "F04SwitchSecond",
        "first_project_unchanged": marker
        not in (first / "scripts" / "3d.gdl").read_text(encoding="utf-8"),
    }

    # Failure path: discard confirmed but target does not open -> stay on first with draft.
    third = create_fixture(root, "F04FailFirst")
    probe.load_api(third)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")
    probe.open_project_menu()
    probe.page.get_by_role("textbox", name="HSF project path").fill(str(root / "does-not-exist"))
    probe.page.get_by_role("button", name="Open", exact=True).click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("button", name="Discard and continue", exact=True).click()
    probe.page.wait_for_timeout(1500)
    failure_kept = {
        "still_third_project": project_name(probe.page) == "F04FailFirst",
        "marker_in_editor": marker in probe.editor_text(),
    }

    actual = {
        "cancel_kept": cancel_kept,
        "discard_switched": discard_switched,
        "failure_kept": failure_kept,
    }
    passed = all(all(group.values()) for group in actual.values())
    return {
        "case": "open_while_dirty",
        "expect": "cancel keeps drafts; discard switches; failed open keeps original project and drafts",
        "actual": actual,
        "passed": passed,
    }


def case_new_while_dirty(probe: Probe, root: Path) -> dict[str, Any]:
    """F04 positive/cancel control (AC22/AC24): New with drafts -> confirm;
    cancel keeps project and draft; no drafts -> no dialog."""
    target = create_fixture(root, "F04NewGuard")
    marker = "NEW_DIRTY_F04"
    probe.load_api(target)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")

    probe.open_project_menu()
    probe.page.get_by_role("button", name="New", exact=True).click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    probe.page.wait_for_timeout(500)
    cancel_kept = {
        "still_same_project": probe.snapshot()["project"]["name"] == "F04NewGuard",
        "marker_in_editor": marker in probe.editor_text(),
    }

    # No drafts -> New runs without any confirmation dialog.
    probe.click_top_save()
    probe.open_project_menu()
    probe.page.get_by_role("button", name="New", exact=True).click()
    probe.page.wait_for_timeout(500)
    no_dialog_when_clean = probe.page.get_by_role("dialog").count() == 0
    new_project_created = probe.snapshot()["project"]["name"] != "F04NewGuard"

    actual = {"cancel_kept": cancel_kept, "no_dialog_when_clean": no_dialog_when_clean, "new_project_created": new_project_created}
    passed = all(cancel_kept.values()) and no_dialog_when_clean and new_project_created
    return {
        "case": "new_while_dirty",
        "expect": "confirm+cancel keeps draft; clean New has no dialog and still works",
        "actual": actual,
        "passed": passed,
    }


def case_revision_dirty_restore(probe: Probe, root: Path) -> dict[str, Any]:
    """F05/AC12: Save Revision includes current drafts; restoring that
    revision brings the edited content back."""
    from openbrep.revisions import list_revisions

    target = create_fixture(root, "F05RevisionDirty")
    marker = "REVISION_DIRTY_F05"
    later = "REVISION_LATER_EDIT"
    probe.load_api(target)
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {marker}\n")

    probe.page.get_by_role("button", name="Revision", exact=True).click()
    probe.page.get_by_placeholder("Revision message").fill("sf1 draft revision")
    probe.page.get_by_role("button", name="Save Revision", exact=True).click()
    probe.page.wait_for_function(
        "() => document.body.innerText.includes('sf1 draft revision') && document.body.innerText.includes('Restore')",
        timeout=int(probe.timeout * 1000),
    )
    revisions = list_revisions(target)
    revision_with_marker = any(
        marker in f.read_text(encoding="utf-8")
        for f in revisions[-1].path.rglob("3d.gdl")
    )

    # Change the script again, save it, then restore the revision.
    probe.edit_script("3d.gdl", f"BLOCK A, B, ZZYZX\n! {later}\n")
    probe.click_top_save()
    probe.page.get_by_role("button", name="Restore", exact=True).first.click()
    dialog = probe.page.get_by_role("dialog")
    dialog.wait_for(timeout=int(probe.timeout * 1000))
    dialog.get_by_role("button", name="Confirm", exact=True).click()
    probe.page.wait_for_timeout(1500)
    probe.open_script("3d.gdl")

    disk_text = (target / "scripts" / "3d.gdl").read_text(encoding="utf-8")
    editor_text = probe.editor_text()
    actual = {
        "marker_in_revision": revision_with_marker,
        "restored_on_disk": marker in disk_text and later not in disk_text,
        "restored_in_editor": marker in editor_text and later not in editor_text,
    }
    passed = all(actual.values())
    return {
        "case": "revision_dirty_restore",
        "expect": "revision contains draft; restore reverts both disk and editor to revision content",
        "actual": actual,
        "passed": passed,
    }


CASES = [
    case_save_inactive_dirty,
    case_compile_multiple_dirty,
    case_save_as_dirty,
    case_first_save_after_new,
    case_apply_parameter_while_script_dirty,
    case_open_while_dirty,
    case_new_while_dirty,
    case_revision_dirty_restore,
]


def run_smoke(
    *,
    config: Path,
    headed: bool = False,
    timeout_seconds: float = 45.0,
    evidence_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "missing_playwright",
            "detail": str(exc),
        }

    api_port = find_free_port()
    web_port = find_free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"
    command, env_overrides = build_obr7_launch(ROOT, api_port=api_port, web_port=web_port)
    process: subprocess.Popen[str] | None = None
    results: list[dict[str, Any]] = []
    temp_root: tempfile.TemporaryDirectory[str] | None = None

    env = os.environ.copy()
    env.update(env_overrides)

    try:
        temp_root = tempfile.TemporaryDirectory(prefix="openbrep_sf1_smoke_")
        root = Path(temp_root.name)
        # R1-05：把传入的凭据无关配置复制到临时 root，并强制 output_dir 指向
        # root/exports；所有导出/GSM 产物必须落在此隔离目录下。
        isolated_config = prepare_isolated_config(config, root)
        env["GDL_AGENT_CONFIG"] = str(isolated_config)
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        api_ready = wait_for_url(f"{api_url}/api/snapshot", timeout_seconds=60)
        web_ready = wait_for_url(web_url, timeout_seconds=60)
        if not (api_ready and web_ready):
            return {
                "ok": False,
                "status": "fail",
                "reason": "server_not_ready",
                "api_ready": api_ready,
                "web_ready": web_ready,
                "output": (process.stdout.read()[-3000:] if process.stdout else ""),
            }

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.add_init_script(
                "localStorage.setItem('openbrep-ui-prefs', JSON.stringify({state:{locale:'en'},version:0}))"
            )
            page.set_default_timeout(int(timeout_seconds * 1000))
            probe = Probe(page, api_url, web_url, timeout_seconds)
            for case in CASES:
                entry: dict[str, Any] = {"started_at": time.strftime("%H:%M:%S")}
                try:
                    entry.update(case(probe, root))
                except Exception as exc:  # noqa: BLE001 - record and continue
                    entry.setdefault("case", case.__name__)
                    entry.update(
                        {
                            "expect": "case completes without probe exception",
                            "actual": {"probe_error": f"{type(exc).__name__}: {exc}"},
                            "passed": False,
                        }
                    )
                    if evidence_dir is not None:
                        evidence_dir.mkdir(parents=True, exist_ok=True)
                        safe = entry["case"].replace("/", "_")
                        page.screenshot(path=str(evidence_dir / f"{safe}.png"), full_page=True)
                results.append(entry)
            browser.close()
    finally:
        if process is not None and process.poll() is None:
            terminate_process(process)
        if temp_root is not None:
            temp_root.cleanup()

    passed = all(r.get("passed") for r in results)
    return {
        "ok": passed,
        "status": "pass" if passed else "fail",
        "tested_commit": _git_head(),
        "command": command,
        "config": str(config),
        "api_url": api_url,
        "web_url": web_url,
        "cases": results,
    }


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SF1 draft-safety browser smoke (F01-F05).")
    parser.add_argument("--config", required=True, help="Credential-free GDL_AGENT_CONFIG toml")
    parser.add_argument("--headed", action="store_true", help="Show Chromium while testing")
    parser.add_argument("--timeout", type=float, default=45.0, help="Timeout seconds")
    parser.add_argument("--evidence-dir", type=Path, default=None, help="Failure screenshots directory")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    result = run_smoke(
        config=Path(args.config),
        headed=args.headed,
        timeout_seconds=args.timeout,
        evidence_dir=args.evidence_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
