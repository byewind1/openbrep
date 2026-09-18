"""ST03 U01–U06 契约级冒烟（service + presentation + store action）。

浏览器真 UI 步骤由验收方在 workbench 上复跑；本脚本把 AC 的可断言
行为钉在可复现的自动化结果里，供实施回执引用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from openbrep.workbench.delivery_presentation import (
    build_delivery_presentation,
    delivery_payload_from_result,
)
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from openbrep.workbench.revision_service import WorkbenchRevisionService
from openbrep.revisions import create_revision


def _u01() -> dict:
    ds = {
        "schema_version": 1,
        "run_id": "r_20260918_timeout",
        "state": "partial_change",
        "before_revision_id": "r0001",
        "after_revision_id": None,
        "source_fingerprint": "sha256:partial",
        "changed_files": ["scripts/3d.gdl"],
        "snapshot_status": "skipped",
        "error_code": None,
    }
    p = build_delivery_presentation(ds, intent="MODIFY", claimed_change=True, verification_passed=False)
    assert p["status"] == "incomplete"
    assert p["show_success_badge"] is False
    assert p["can_recover"] is True and p["can_view_diff"] is True
    assert "scripts/3d.gdl" in p["changed_files"]
    assert "未完成" in p["headline"]
    return {"case": "U01", "passed": True, "headline": p["headline"], "recover": p["recover_revision_id"]}


def _u02() -> dict:
    # 服务层不负责前端确认；断言 draft_policy 非法时拒绝，合法时回显
    class Session:
        source_path = object()

    svc = WorkbenchRevisionService(Session())
    bad = svc.restore_project_revision({"revision_id": "r0001", "draft_policy": "nope"})
    assert bad["ok"] is False and bad["code"] == "INVALID_DRAFT_POLICY"
    return {"case": "U02", "passed": True, "note": "invalid policy rejected; cancel path is frontend-only"}


def _u03(tmp_path: Path) -> dict:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "3d.gdl").write_text("BODY\n", encoding="utf-8")
    rev = create_revision(tmp_path, message="before", trigger="manual")
    (scripts / "3d.gdl").write_text("CHANGED\n", encoding="utf-8")

    class Project:
        name = "Chair"

    class Session:
        source_path = tmp_path
        project = Project()
        project_epoch = 1

        def snapshot(self):
            return {
                "project": {"name": "Chair", "path": str(tmp_path)},
                "parameters": [],
                "preview": {"meshes": [], "warnings": ["cleared"]},
            }

    svc = WorkbenchRevisionService(Session())
    result = svc.restore_project_revision({"revision_id": rev.revision_id, "draft_policy": "keep"})
    assert result["ok"] is True
    assert result["restore"]["hsf_reloaded"] is True
    assert result["restore"]["preview_cleared"] is True
    assert result["restore"]["draft_policy"] == "keep"
    assert (scripts / "3d.gdl").read_text(encoding="utf-8") == "BODY\n"
    return {"case": "U03", "passed": True, "restore": result["restore"]}


def _u04() -> dict:
    # hydrateSnapshot 是前端：此处断言 presentation 在 unlinked 时不提供跨项目 recover 动作
    p = build_delivery_presentation(None, intent="MODIFY")
    assert p["status"] == "unlinked"
    assert p["can_recover"] is False
    assert p["can_continue"] is False
    return {"case": "U04", "passed": True, "status": p["status"]}


def _u05() -> dict:
    p = build_delivery_presentation(
        {
            "schema_version": 1,
            "run_id": "r_unchanged",
            "state": "unchanged",
            "before_revision_id": "r0001",
            "after_revision_id": None,
            "source_fingerprint": None,
            "changed_files": [],
            "snapshot_status": "skipped",
            "error_code": None,
        },
        intent="MODIFY",
        claimed_change=True,
    )
    assert p["status"] == "no_change"
    assert "未产生源码变化" in p["headline"]
    assert p["show_success_badge"] is False
    assert "已修复" not in p["headline"]
    result = SimpleNamespace(
        intent="MODIFY",
        verification={"passed": True, "fixes_applied": ["x"]},
        metadata={"delivery_source": {"state": "unchanged", "run_id": "r_unchanged", "schema_version": 1}},
        plain_text="ok",
        scripts={},
    )
    assistant = WorkbenchAssistantService._generate_assistant_dict(result)
    assert assistant["delivery"]["headline"] == "未产生源码变化"
    assert assistant["delivery"]["show_success_badge"] is False
    return {"case": "U05", "passed": True, "headline": assistant["delivery"]["headline"]}


def _u06() -> dict:
    cf = {"origin_run_id": "r_old", "original_instruction": "把层板数改成 5"}
    result = SimpleNamespace(
        intent="MODIFY",
        verification={"passed": False},
        metadata={
            "delivery_source": {
                "schema_version": 1,
                "run_id": "r_new",
                "state": "partial_change",
                "before_revision_id": "r0010",
                "after_revision_id": None,
                "source_fingerprint": None,
                "changed_files": ["scripts/3d.gdl"],
                "snapshot_status": "skipped",
                "error_code": None,
            },
            "continue_from": cf,
        },
        plain_text="partial",
        scripts={"scripts/3d.gdl": "x"},
    )
    WorkbenchAssistantService._merge_continue_from(result, cf)
    assistant = WorkbenchAssistantService._generate_assistant_dict(
        result, instruction="把层板数改成 5", continue_from=cf
    )
    assert assistant["continue_from"]["origin_run_id"] == "r_old"
    assert assistant["delivery"]["continued_from"]["origin_run_id"] == "r_old"
    assert assistant["run_id"] == "r_new"  # 新 run 自己的 id
    assert assistant["delivery"]["before_revision_id"] == "r0010"
    payload = delivery_payload_from_result(result)
    assert payload["original_instruction"] == "把层板数改成 5"
    return {
        "case": "U06",
        "passed": True,
        "origin_run": cf["origin_run_id"],
        "new_run": assistant["run_id"],
        "new_before": assistant["delivery"]["before_revision_id"],
    }


def main() -> int:
    import tempfile

    cases = [_u01(), _u02(), _u04(), _u05(), _u06()]
    with tempfile.TemporaryDirectory() as tmp:
        cases.append(_u03(Path(tmp)))
    out = {
        "suite": "st03_delivery_ui",
        "all_passed": all(c.get("passed") for c in cases),
        "cases": cases,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
