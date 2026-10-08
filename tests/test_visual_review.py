import base64
import json
from types import SimpleNamespace

import pytest

from openbrep.vision.review import (
    ReviewFrame,
    ReviewTarget,
    VisualReviewRequest,
    review_visual_result,
    save_visual_review,
)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _request(*, frames=None):
    return VisualReviewRequest(
        review_id="review-1",
        run_id="run-1",
        source_fingerprint="source-sha256",
        plan_id="plan-1",
        user_request="Create a parametric object matching this reference.",
        observation={"opening.shape": "rectangular", "hidden_parts": None},
        plan={"object_type": "generic_object", "requirements": ["opening"]},
        targets=(
            ReviewTarget("opening", "Visible opening geometry matches the frozen requirement"),
            ReviewTarget("hidden_parts", "Do not infer parts hidden from the supplied views"),
        ),
        frames=tuple(frames or (
            ReviewFrame("ref-1", "reference", "image/png", _b64(b"original-image")),
            ReviewFrame("render-1", "rendered", "image/png", _b64(b"rendered-image"), "view-front"),
        )),
    )


def _response(**overrides):
    value = {
        "coverage": [
            {"target_id": "opening", "status": "covered"},
            {"target_id": "hidden_parts", "status": "not_observable"},
        ],
        "findings": [
            {
                "finding_id": "f-1",
                "target_id": "opening",
                "outcome": "pass",
                "severity": "info",
                "summary": "The visible opening is rectangular.",
                "failure_layer": "unknown",
                "uncertainty": "",
                "evidence": [
                    {
                        "frame_id": "render-1",
                        "view_id": "view-front",
                        "region": [0.1, 0.2, 0.5, 0.6],
                        "note": "Visible opening boundary.",
                    },
                    {"frame_id": "ref-1", "note": "Reference opening."},
                ],
            }
        ],
    }
    value.update(overrides)
    return json.dumps(value)


class _FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def generate_with_images(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content, model="test-vision-model")


def test_review_binds_images_targets_plan_and_readonly_evidence():
    llm = _FakeLLM(_response())
    request = _request()

    result = review_visual_result(llm, request, timeout=3)

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert [image["b64"] for image in call["images"]] == [frame.b64 for frame in request.frames]
    assert "hidden_parts" in call["text_prompt"]
    assert "view-front" in call["text_prompt"]
    assert call["timeout"] == 3
    assert result.status == "partial"
    assert result.missing_target_ids == ()
    assert {item["target_id"]: item["status"] for item in result.coverage}["hidden_parts"] == "not_observable"
    assert result.findings[0]["evidence"][0]["view_id"] == "view-front"
    assert result.frame_manifest[0]["sha256"]
    assert result.model == "test-vision-model"
    assert result.schema_version == 1
    assert "passed" not in result.to_dict()


def test_missing_plan_target_is_explicit_unknown_not_dropped():
    payload = _response(coverage=[{"target_id": "opening", "status": "covered"}])
    result = review_visual_result(_FakeLLM(payload), _request())

    assert result.status == "partial"
    assert result.missing_target_ids == ("hidden_parts",)
    assert {item["target_id"]: item["status"] for item in result.coverage}["hidden_parts"] == "unknown"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"coverage": [{"target_id": "invented", "status": "covered"}]}, "unknown Plan target"),
        ({"coverage": [{"target_id": "opening", "status": "covered"}] * 2}, "duplicate coverage"),
        ({"coverage": [{"target_id": "opening", "status": "pass"}]}, "unsupported coverage status"),
        ({"findings": [{"finding_id": "f-1", "target_id": "invented"}]}, "unknown Plan target"),
    ],
)
def test_rejects_unbound_or_malformed_target_claims(override, message):
    with pytest.raises(ValueError, match=message):
        review_visual_result(_FakeLLM(_response(**override)), _request())


@pytest.mark.parametrize(
    "evidence, message",
    [
        ([{"frame_id": "missing"}], "unavailable frame"),
        ([{"frame_id": "render-1", "view_id": "invented"}], "mismatched rendered view"),
        ([{"frame_id": "ref-1", "view_id": "view-front"}], "reference evidence cannot claim"),
        ([{"frame_id": "render-1", "view_id": "view-front", "region": [0.8, 0.2, 0.5, 0.2]}], "outside normalized"),
    ],
)
def test_rejects_fabricated_view_or_invalid_region(evidence, message):
    findings = _response_dict()["findings"]
    findings[0]["evidence"] = evidence
    with pytest.raises(ValueError, match=message):
        review_visual_result(_FakeLLM(json.dumps({**_response_dict(), "findings": findings})), _request())


def test_unknown_finding_requires_reason_and_pass_fail_require_evidence():
    payload = _response_dict()
    payload["findings"][0].update(outcome="unknown", uncertainty="")
    with pytest.raises(ValueError, match="requires an uncertainty reason"):
        review_visual_result(_FakeLLM(json.dumps(payload)), _request())

    payload = _response_dict()
    payload["findings"][0]["evidence"] = []
    with pytest.raises(ValueError, match="requires image evidence"):
        review_visual_result(_FakeLLM(json.dumps(payload)), _request())


def _response_dict():
    return json.loads(_response())


@pytest.mark.parametrize("frames", [
    (ReviewFrame("ref-1", "reference", "image/png", _b64(b"ref")),),
    (ReviewFrame("render-1", "rendered", "image/png", _b64(b"view"), "view-front"),),
    (
        ReviewFrame("ref-1", "reference", "image/png", "not-base64!"),
        ReviewFrame("render-1", "rendered", "image/png", _b64(b"view"), "view-front"),
    ),
])
def test_request_requires_valid_reference_and_rendered_frames(frames):
    with pytest.raises(ValueError):
        review_visual_result(_FakeLLM(_response()), _request(frames=frames))


def test_request_rejects_unsupported_or_active_content_mime():
    frames = (
        ReviewFrame("ref-1", "reference", "image/svg+xml", _b64(b"<svg/>")),
        ReviewFrame("render-1", "rendered", "image/png", _b64(b"view"), "view-front"),
    )
    with pytest.raises(ValueError, match="unsupported image MIME"):
        review_visual_result(_FakeLLM(_response()), _request(frames=frames))


def test_persists_report_atomically_outside_hsf_and_failure_never_claims_success(tmp_path):
    project = tmp_path / "Object.hsf"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    source = scripts / "3d.gdl"
    source.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
    before = source.read_bytes()
    request = _request()
    result = review_visual_result(_FakeLLM(_response()), request)

    target = save_visual_review(project, result, frames=request.frames)

    assert target.relative_to(project).as_posix() == ".openbrep/visual-reviews/review-1.json"
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["input_fingerprint"] == result.input_fingerprint
    assert all((project / frame["artifact_path"]).is_file() for frame in report["frame_manifest"])
    assert source.read_bytes() == before
    assert not list(target.parent.glob("*.tmp"))
    with pytest.raises(FileExistsError):
        save_visual_review(project, result, frames=request.frames)
    assert json.loads(target.read_text(encoding="utf-8"))["review_id"] == result.review_id


def test_report_write_failure_does_not_leave_partial_file(tmp_path, monkeypatch):
    project = tmp_path / "Object.hsf"
    request = _request()
    result = review_visual_result(_FakeLLM(_response()), request)

    def fail_link(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr("openbrep.vision.review.os.link", fail_link)
    with pytest.raises(OSError, match="disk full"):
        save_visual_review(project, result, frames=request.frames)
    review_root = project / ".openbrep" / "visual-reviews"
    assert list(review_root.iterdir()) == []
