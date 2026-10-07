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
    assert restored["reports"] == [response["review"]]
    assert llm.calls[0]["codex_intent"] == "IMAGE"
    assert (project_root / "scripts" / "3d.gdl").read_bytes() == source_before
    assert session.project_epoch == 4


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
