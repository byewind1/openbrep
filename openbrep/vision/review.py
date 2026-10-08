"""Read-only comparison of reference images, a frozen Plan, and rendered views.

The review model returns evidence-bound candidates. This module has no HSF
mutation or automatic acceptance path; missing or unobservable targets stay
unknown so a caller can present them for human review.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

_SYSTEM_PROMPT = """\
You are a read-only visual reviewer for parametric GDL objects.
Compare the supplied original reference images with the supplied rendered views,
using the user's request, observations, and frozen Plan as context.
Do not generate or edit GDL. Do not infer hidden structure or claim a feature is
correct when the supplied views cannot show it. Return strict JSON only.
For every target, state coverage as covered, partial, unknown, or not_observable.
Findings are evidence-bound candidates, not authoritative acceptance decisions.
"""

_SEVERITIES = {"info", "minor", "major", "critical"}
_COVERAGE = {"covered", "partial", "unknown", "not_observable"}
_OUTCOMES = {"pass", "fail", "unknown"}
_FAILURE_LAYERS = {"recognition", "plan", "implementation", "rendering", "reference", "unobservable", "unknown"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUPPORTED_MIMES = {"image/png", "image/jpeg", "image/webp"}
_REVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReviewFrame:
    """One source or rendered image made available to the read-only reviewer."""

    frame_id: str
    kind: str  # reference | rendered
    mime: str
    b64: str
    view_id: str = ""


@dataclass(frozen=True)
class ReviewTarget:
    target_id: str
    description: str


@dataclass(frozen=True)
class VisualReviewRequest:
    review_id: str
    run_id: str
    source_fingerprint: str
    plan_id: str
    user_request: str
    observation: Mapping[str, Any]
    plan: Mapping[str, Any]
    targets: tuple[ReviewTarget, ...]
    frames: tuple[ReviewFrame, ...]


@dataclass(frozen=True)
class VisualReviewResult:
    review_id: str
    run_id: str
    source_fingerprint: str
    plan_id: str
    model: str
    schema_version: int
    input_fingerprint: str
    status: str  # complete | partial; never means visually passed
    coverage: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    missing_target_ids: tuple[str, ...]
    frame_manifest: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _frame_manifest(frames: tuple[ReviewFrame, ...]) -> list[dict[str, Any]]:
    manifest = []
    seen_ids: set[str] = set()
    seen_views: set[str] = set()
    for frame in frames:
        if not _SAFE_ID.fullmatch(frame.frame_id) or frame.frame_id in seen_ids:
            raise ValueError(f"invalid or duplicate review frame id: {frame.frame_id!r}")
        seen_ids.add(frame.frame_id)
        if frame.kind not in {"reference", "rendered"}:
            raise ValueError(f"unsupported frame kind for {frame.frame_id}: {frame.kind}")
        mime = frame.mime.lower()
        if mime not in _SUPPORTED_MIMES:
            raise ValueError(f"unsupported image MIME for {frame.frame_id}: {frame.mime}")
        if frame.kind == "rendered":
            if not frame.view_id or not _SAFE_ID.fullmatch(frame.view_id) or frame.view_id in seen_views:
                raise ValueError(f"invalid or duplicate rendered view id: {frame.view_id!r}")
            seen_views.add(frame.view_id)
        elif frame.view_id:
            raise ValueError("reference frames must not claim a rendered view_id")
        try:
            content = base64.b64decode(frame.b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid base64 image for {frame.frame_id}") from exc
        if not content:
            raise ValueError(f"empty image for {frame.frame_id}")
        manifest.append(
            {
                "frame_id": frame.frame_id,
                "kind": frame.kind,
                "mime": mime,
                "sha256": hashlib.sha256(content).hexdigest(),
                **({"view_id": frame.view_id} if frame.kind == "rendered" else {}),
            }
        )
    if not any(frame.kind == "reference" for frame in frames):
        raise ValueError("visual review requires at least one original reference frame")
    if not any(frame.kind == "rendered" for frame in frames):
        raise ValueError("visual review requires at least one rendered view")
    return manifest


def _validate_request(request: VisualReviewRequest) -> list[dict[str, Any]]:
    for name, value in (
        ("review_id", request.review_id),
        ("run_id", request.run_id),
        ("plan_id", request.plan_id),
    ):
        if not _SAFE_ID.fullmatch(value or ""):
            raise ValueError(f"invalid {name}")
    if not request.source_fingerprint:
        raise ValueError("source_fingerprint is required")
    if not request.user_request.strip():
        raise ValueError("user_request is required")
    if not request.targets:
        raise ValueError("visual review requires at least one frozen Plan target")
    target_ids: set[str] = set()
    for target in request.targets:
        if not _SAFE_ID.fullmatch(target.target_id) or target.target_id in target_ids:
            raise ValueError(f"invalid or duplicate target id: {target.target_id!r}")
        if not target.description.strip():
            raise ValueError(f"target description is required: {target.target_id}")
        target_ids.add(target.target_id)
    return _frame_manifest(request.frames)


def _prompt(request: VisualReviewRequest, manifest: list[dict[str, Any]]) -> str:
    frames = [
        {
            "frame_id": frame["frame_id"],
            "kind": frame["kind"],
            **({"view_id": frame["view_id"]} if "view_id" in frame else {}),
        }
        for frame in manifest
    ]
    payload = {
        "user_request": request.user_request,
        "observation": request.observation,
        "frozen_plan": request.plan,
        "targets": [asdict(target) for target in request.targets],
        "frames": frames,
        "required_output": {
            "coverage": [{"target_id": "...", "status": "covered|partial|unknown|not_observable"}],
            "findings": [
                {
                    "finding_id": "...",
                    "target_id": "...",
                    "outcome": "pass|fail|unknown",
                    "severity": "info|minor|major|critical",
                    "summary": "...",
                    "failure_layer": "recognition|plan|implementation|rendering|reference|unobservable|unknown",
                    "uncertainty": "...",
                    "evidence": [
                        {"frame_id": "...", "view_id": "...", "region": [0.0, 0.0, 1.0, 1.0], "note": "..."}
                    ],
                }
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _response_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise ValueError("visual review model response must be text")
    return content


def _parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid visual review JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("visual review response must be a JSON object")
    return parsed


def _validate_output(
    raw: str,
    request: VisualReviewRequest,
    manifest: list[dict[str, Any]],
    model: str,
) -> VisualReviewResult:
    output = _parse_json(raw)
    targets = {target.target_id for target in request.targets}
    frames = {frame["frame_id"]: frame for frame in manifest}
    coverage_by_target: dict[str, dict[str, Any]] = {}
    raw_coverage = output.get("coverage")
    if not isinstance(raw_coverage, list):
        raise ValueError("visual review coverage must be an array")
    for item in raw_coverage:
        if not isinstance(item, dict):
            raise ValueError("each coverage item must be an object")
        target_id = str(item.get("target_id") or "")
        status = str(item.get("status") or "")
        if target_id not in targets:
            raise ValueError(f"coverage refers to unknown Plan target: {target_id}")
        if target_id in coverage_by_target:
            raise ValueError(f"duplicate coverage for Plan target: {target_id}")
        if status not in _COVERAGE:
            raise ValueError(f"unsupported coverage status for {target_id}: {status}")
        coverage_by_target[target_id] = {"target_id": target_id, "status": status}

    findings: list[dict[str, Any]] = []
    finding_ids: set[str] = set()
    raw_findings = output.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("visual review findings must be an array")
    for item in raw_findings:
        if not isinstance(item, dict):
            raise ValueError("each finding must be an object")
        finding_id = str(item.get("finding_id") or "")
        target_id = str(item.get("target_id") or "")
        outcome = str(item.get("outcome") or "")
        severity = str(item.get("severity") or "")
        failure_layer = str(item.get("failure_layer") or "unknown")
        summary = str(item.get("summary") or "").strip()
        uncertainty = str(item.get("uncertainty") or "").strip()
        if not _SAFE_ID.fullmatch(finding_id) or finding_id in finding_ids:
            raise ValueError(f"invalid or duplicate finding id: {finding_id!r}")
        finding_ids.add(finding_id)
        if target_id not in targets:
            raise ValueError(f"finding refers to unknown Plan target: {target_id}")
        if outcome not in _OUTCOMES or severity not in _SEVERITIES:
            raise ValueError(f"invalid outcome or severity for finding {finding_id}")
        if failure_layer not in _FAILURE_LAYERS or not summary:
            raise ValueError(f"invalid failure layer or empty summary for finding {finding_id}")
        if outcome == "unknown" and not uncertainty:
            raise ValueError(f"unknown finding requires an uncertainty reason: {finding_id}")

        evidence_items = item.get("evidence")
        if not isinstance(evidence_items, list):
            raise ValueError(f"finding evidence must be an array: {finding_id}")
        evidence: list[dict[str, Any]] = []
        for evidence_item in evidence_items:
            if not isinstance(evidence_item, dict):
                raise ValueError(f"invalid evidence item in {finding_id}")
            frame_id = str(evidence_item.get("frame_id") or "")
            frame = frames.get(frame_id)
            if frame is None:
                raise ValueError(f"finding {finding_id} cites an unavailable frame: {frame_id}")
            evidence_view_id = str(evidence_item.get("view_id") or "")
            if frame["kind"] == "rendered":
                if evidence_view_id != frame.get("view_id"):
                    raise ValueError(f"finding {finding_id} cites a mismatched rendered view")
            elif evidence_view_id:
                raise ValueError(f"reference evidence cannot claim rendered view_id: {finding_id}")
            normalized = {"frame_id": frame_id, "note": str(evidence_item.get("note") or "")[:500]}
            if frame["kind"] == "rendered":
                normalized["view_id"] = evidence_view_id
            region = evidence_item.get("region")
            if region is not None:
                if (
                    not isinstance(region, list)
                    or len(region) != 4
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in region)
                ):
                    raise ValueError(f"finding {finding_id} region must be [x,y,w,h]")
                x, y, width, height = (float(value) for value in region)
                if (
                    not all(0.0 <= value <= 1.0 for value in (x, y, width, height))
                    or x + width > 1.0
                    or y + height > 1.0
                ):
                    raise ValueError(f"finding {finding_id} region is outside normalized image bounds")
                normalized["region"] = [x, y, width, height]
            evidence.append(normalized)
        if outcome in {"pass", "fail"} and not evidence:
            raise ValueError(f"pass/fail finding requires image evidence: {finding_id}")
        findings.append(
            {
                "finding_id": finding_id,
                "target_id": target_id,
                "outcome": outcome,
                "severity": severity,
                "summary": summary[:1000],
                "failure_layer": failure_layer,
                "uncertainty": uncertainty[:1000],
                "evidence": evidence,
            }
        )

    missing_targets = sorted(targets - set(coverage_by_target))
    coverage = [coverage_by_target[target_id] for target_id in sorted(coverage_by_target)]
    coverage.extend({"target_id": target_id, "status": "unknown"} for target_id in missing_targets)
    partial = bool(missing_targets) or any(item["status"] in {"unknown", "not_observable"} for item in coverage)
    partial = partial or any(item["outcome"] == "unknown" for item in findings)
    input_fingerprint = _canonical_hash(
        {
            "run_id": request.run_id,
            "source_fingerprint": request.source_fingerprint,
            "plan_id": request.plan_id,
            "review_schema_version": _REVIEW_SCHEMA_VERSION,
            "system_prompt_sha256": hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "user_prompt_sha256": hashlib.sha256(_prompt(request, manifest).encode("utf-8")).hexdigest(),
            "model": model,
            "user_request": request.user_request,
            "observation": request.observation,
            "plan": request.plan,
            "targets": [asdict(target) for target in request.targets],
            "frames": manifest,
        }
    )
    return VisualReviewResult(
        review_id=request.review_id,
        run_id=request.run_id,
        source_fingerprint=request.source_fingerprint,
        plan_id=request.plan_id,
        model=model,
        schema_version=_REVIEW_SCHEMA_VERSION,
        input_fingerprint=input_fingerprint,
        status="partial" if partial else "complete",
        coverage=tuple(coverage),
        findings=tuple(findings),
        missing_target_ids=tuple(missing_targets),
        frame_manifest=tuple(manifest),
    )


def review_visual_result(llm: Any, request: VisualReviewRequest, **llm_kwargs: Any) -> VisualReviewResult:
    """Run one bounded, read-only model review and validate every returned reference."""

    manifest = _validate_request(request)
    ordered_frames = [
        {"b64": frame.b64, "mime": frame.mime}
        for frame in request.frames
    ]
    response = llm.generate_with_images(
        text_prompt=_prompt(request, manifest),
        images=ordered_frames,
        system_prompt=_SYSTEM_PROMPT,
        **llm_kwargs,
    )
    model = str(getattr(response, "model", "") or "unknown")
    return _validate_output(_response_content(response), request, manifest, model)


def save_visual_review(
    project_root: str | Path,
    result: VisualReviewResult,
    *,
    frames: tuple[ReviewFrame, ...],
) -> Path:
    """Atomically persist review evidence outside HSF source; write errors propagate."""

    root = Path(project_root).resolve()
    review_root = root / ".openbrep" / "visual-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    target = review_root / f"{result.review_id}.json"
    if target.exists():
        raise FileExistsError(target)
    report = result.to_dict()
    report_manifest = report["frame_manifest"]
    manifest_by_id = {item["frame_id"]: item for item in report_manifest}
    if len(manifest_by_id) != len(result.frame_manifest) or set(manifest_by_id) != {frame.frame_id for frame in frames}:
        raise ValueError("persisted review frames do not match the result manifest")
    frame_root = review_root / f"{result.review_id}.frames"
    frame_root.mkdir()
    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    temp_name = ""
    try:
        for frame in frames:
            content = base64.b64decode(frame.b64, validate=True)
            digest = hashlib.sha256(content).hexdigest()
            if digest != manifest_by_id[frame.frame_id]["sha256"]:
                raise ValueError(f"persisted review frame hash mismatch: {frame.frame_id}")
            frame_path = frame_root / f"{frame.frame_id}{extensions[frame.mime.lower()]}"
            fd, temp_name = tempfile.mkstemp(prefix=f".{frame.frame_id}.", suffix=".tmp", dir=frame_root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp_name, frame_path)
            os.unlink(temp_name)
            manifest_by_id[frame.frame_id]["artifact_path"] = frame_path.relative_to(root).as_posix()

        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=f".{result.review_id}.", suffix=".tmp", dir=review_root)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and refuses to overwrite an earlier
        # immutable report with a reused review id.
        os.link(temp_name, target)
        os.unlink(temp_name)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        shutil.rmtree(frame_root, ignore_errors=True)
        raise
    return target
