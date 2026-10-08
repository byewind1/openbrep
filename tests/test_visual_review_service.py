import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench.visual_review_service import WorkbenchVisualReviewService


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def generate_with_images(self, **kwargs):
        self.calls.append(kwargs)
        content = {
            "coverage": [{"target_id": "overall-object", "status": "covered"}],
            "findings": [{
                "finding_id": "visible-overall",
                "target_id": "overall-object",
                "outcome": "pass",
                "severity": "info",
                "summary": "The visible generated shape is represented in the provided views.",
                "failure_layer": "unknown",
                "uncertainty": "",
                "evidence": [{
                    "frame_id": "view-material_iso",
                    "view_id": "material_iso",
                    "region": [0.1, 0.1, 0.8, 0.8],
                    "note": "Visible model region.",
                }],
            }],
        }
        return SimpleNamespace(content=json.dumps(content), model="fake-vision")


def _source(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "paramlist.xml").write_text("<ParamSection><Parameters/></ParamSection>", encoding="utf-8")
    (root / "scripts" / "3d.gdl").write_text("BLOCK 1, 1, 1\n", encoding="utf-8")


def _capture(payload, *, source_fingerprint, out_dir, view_names):
    out_dir = Path(out_dir)
    views = []
    for view_name in view_names:
        data = f"screenshot-{view_name}".encode()
        path = out_dir / f"{view_name}.png"
        path.write_bytes(data)
        views.append({
            "name": view_name,
            "status": "pass",
            "screenshot": str(path),
            "image_sha256": hashlib.sha256(data).hexdigest(),
        })
    return {"status": "pass", "source_fingerprint": source_fingerprint, "views": views}


def _service(tmp_path, *, llm=None, capture=_capture, state="completed", stale=False):
    project_root = tmp_path / "Object.hsf"
    project_root.mkdir()
    _source(project_root)
    source_fp = compute_source_fingerprint(project_root)
    session = SimpleNamespace(
        project=SimpleNamespace(root=project_root),
        project_epoch=4,
        project_service=SimpleNamespace(preview=lambda _request: {"ok": True, "preview": {"meshes": []}}),
        conversation_service=SimpleNamespace(turns={}),
        reference_service=None,
    )
    run_fp = "sha256:stale" if stale else source_fp
    turn = SimpleNamespace(
        turn_id="turn-1",
        state=state,
        body={"message": "Make an object from this image", "images": [{"name": "source.png", "mime": "image/png", "b64": base64.b64encode(b"source").decode()}]},
        result={
            "ok": True,
            "current_project_epoch": 4,
            "assistant": {"run_id": "run-1", "source_fingerprint": run_fp},
            "events": [],
        },
        plan=None,
        reference_asset_ids=[],
    )
    session.conversation_service.turns[turn.turn_id] = turn
    llm = llm or _FakeLLM()
    service = WorkbenchVisualReviewService(session, llm_factory=lambda: llm, capture_views=capture)
    return service, session, llm, project_root


def test_explicit_review_turn_uses_bound_reference_and_material_views(tmp_path):
    service, session, llm, project_root = _service(tmp_path)
    source_before = (project_root / "scripts" / "3d.gdl").read_bytes()

    response = service.route("POST", "/api/vision/review", {"turn_id": "turn-1", "project_epoch": 4})

    assert response["ok"] is True
    assert response["review"]["status"] == "complete"
    assert response["capture_status"] == "pass"
    assert len(response["capture_views"]) == 4
    assert {image["mime"] for image in llm.calls[0]["images"]} == {"image/png"}
    assert len(llm.calls[0]["images"]) == 5  # original reference plus four rendered views
    assert response["report_path"].startswith(".openbrep/visual-reviews/")
    assert all((project_root / frame["artifact_path"]).is_file() for frame in response["review"]["frame_manifest"])
    restored = service.route("GET", "/api/vision/reviews/run-1", {})
    assert restored["ok"] is True
    assert restored["reports"][0]["review_id"] == response["review"]["review_id"]
    assert restored["reports"][0]["validity"]["status"] == "current"
    assert llm.calls[0]["codex_intent"] == "IMAGE"
    assert (project_root / "scripts" / "3d.gdl").read_bytes() == source_before
    assert session.project_epoch == 4


def test_source_change_during_model_review_discards_result(tmp_path):
    service, _, _llm, project_root = _service(tmp_path)

    class MutatingLLM(_FakeLLM):
        def generate_with_images(self, **kwargs):
            response = super().generate_with_images(**kwargs)
            (project_root / "scripts" / "3d.gdl").write_text("BLOCK 2, 2, 2\n", encoding="utf-8")
            return response

    service._llm_factory = MutatingLLM
    response = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})

    assert response["ok"] is False
    assert response["code"] == "SOURCE_STALE"
    assert not list((project_root / ".openbrep" / "visual-reviews").glob("review-*.json"))


def test_stale_source_is_rejected_before_preview_capture_or_model_call(tmp_path):
    llm = _FakeLLM()
    called = []

    def capture(*args, **kwargs):
        called.append(True)
        return _capture(*args, **kwargs)

    service, _, _, _ = _service(tmp_path, llm=llm, capture=capture, stale=True)
    response = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})

    assert response["ok"] is False
    assert response["code"] == "SOURCE_STALE"
    assert called == []
    assert llm.calls == []


def test_saved_visual_review_is_projected_stale_after_source_changes(tmp_path):
    service, _, _, project_root = _service(tmp_path)
    created = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})
    assert created["ok"] is True
    (project_root / "scripts" / "3d.gdl").write_text("BLOCK 2, 2, 2\n", encoding="utf-8")

    restored = service.saved_reviews("run-1")

    assert restored["ok"] is True
    assert restored["reports"][0]["status"] == "complete"
    assert restored["reports"][0]["validity"]["status"] == "stale"
    assert "source_changed" in restored["reports"][0]["validity"]["stale_reasons"]


def test_unfinished_or_cross_project_turn_is_rejected(tmp_path):
    service, session, _, _ = _service(tmp_path, state="executing")
    response = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})
    assert response["code"] == "TURN_NOT_REVIEWABLE"

    session.conversation_service.turns["turn-1"].state = "completed"
    response = service.review_turn({"turn_id": "turn-1", "project_epoch": 3})
    assert response["code"] == "PROJECT_CHANGED"


def test_unhealthy_rendered_view_stops_before_model_review(tmp_path):
    llm = _FakeLLM()

    def bad_capture(payload, *, source_fingerprint, out_dir, view_names):
        result = _capture(payload, source_fingerprint=source_fingerprint, out_dir=out_dir, view_names=view_names)
        result["views"][0]["status"] = "unverified"
        return result

    service, _, _, _ = _service(tmp_path, llm=llm, capture=bad_capture)
    response = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})

    assert response["ok"] is False
    assert response["code"] == "PREVIEW_CAPTURE_INCOMPLETE"
    assert llm.calls == []


def test_failed_current_finding_prepares_one_scoped_approved_repair(tmp_path):
    service, session, _, project_root = _service(tmp_path)
    reviewed = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})["review"]
    reviewed["findings"] = [{
        "finding_id": "visible-overall", "target_id": "overall-object", "outcome": "fail",
        "severity": "major", "summary": "模型底座缺失", "failure_layer": "implementation",
        "uncertainty": "", "evidence": [{"frame_id": reviewed["frame_manifest"][0]["frame_id"]}],
    }]
    report_path = project_root / ".openbrep" / "visual-reviews" / f"{reviewed['review_id']}.json"
    report_path.write_text(json.dumps(reviewed), encoding="utf-8")
    calls = []
    def prepare(body):
        calls.append(body)
        session.conversation_service.turns["repair-turn"] = SimpleNamespace(body={})
        return {"ok": True, "state": "pending", "turn_id": "repair-turn"}
    session.conversation_service.route = prepare

    response = service.route("POST", "/api/vision/repair", {
        "review_id": reviewed["review_id"], "finding_id": "visible-overall", "project_epoch": 4,
    })

    assert response["ok"] is True
    assert response["repair"]["state"] == "awaiting_approval"
    assert response["repair"]["decision"]["action"] == "modify"
    assert len(calls) == 1
    assert calls[0]["requested_mode"] == "plan"
    assert calls[0]["confirm_before_execute"] is True
    assert "目标 overall-object" in calls[0]["message"]
    review_turn = session.conversation_service.turns["repair-turn"]
    assert review_turn.review_reference_images == session.conversation_service.turns["turn-1"].body["images"]
    frames, error = service._reference_frames(review_turn)
    assert error is None
    assert frames[0].kind == "reference"


def test_repair_rejects_stale_source_without_preparing_turn(tmp_path):
    service, session, _, project_root = _service(tmp_path)
    reviewed = service.review_turn({"turn_id": "turn-1", "project_epoch": 4})["review"]
    reviewed["findings"] = [{
        "finding_id": "visible-overall", "target_id": "overall-object", "outcome": "fail",
        "severity": "major", "summary": "问题", "failure_layer": "implementation",
        "uncertainty": "", "evidence": [{"frame_id": reviewed["frame_manifest"][0]["frame_id"]}],
    }]
    (project_root / ".openbrep" / "visual-reviews" / f"{reviewed['review_id']}.json").write_text(
        json.dumps(reviewed), encoding="utf-8"
    )
    calls = []
    session.conversation_service.route = lambda body: calls.append(body) or {"ok": True}
    (project_root / "scripts" / "3d.gdl").write_text("BLOCK 2, 2, 2\n", encoding="utf-8")

    response = service.prepare_repair({
        "review_id": reviewed["review_id"], "finding_id": "visible-overall", "project_epoch": 4,
    })

    assert response["ok"] is False
    assert response["decision"]["reason"] == "review_source_stale"
    assert calls == []
