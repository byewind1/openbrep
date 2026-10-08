"""Workbench entry for explicitly reviewing a completed CREATE/IMAGE turn."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.vision.review import (
    ReviewFrame,
    ReviewTarget,
    VisualReviewRequest,
    review_visual_result,
    save_visual_review,
)


class WorkbenchVisualReviewService:
    """Read-only review service; project source is only read for preview/hash."""

    REQUIRED_VIEWS = ("material_iso", "front", "side", "neutral")

    def __init__(
        self,
        session: Any,
        *,
        llm_factory: Callable[[], Any] | None = None,
        capture_views: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.session = session
        self._llm_factory = llm_factory
        self._capture_views = capture_views

    def route(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if method.upper() == "POST" and path == "/api/vision/review":
            return self.review_turn(body)
        prefix = "/api/vision/reviews/"
        if method.upper() == "GET" and path.startswith(prefix):
            return self.saved_reviews(path[len(prefix):])
        return {"ok": False, "error": f"Unknown visual review route: {method} {path}"}

    def saved_reviews(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", run_id):
            return {"ok": False, "code": "INVALID_RUN_ID", "error": "运行编号无效。"}
        if self.session.project is None:
            return {"ok": False, "code": "PROJECT_UNAVAILABLE", "error": "当前没有可读取视觉记录的项目。"}
        root = Path(self.session.project.root).resolve() / ".openbrep" / "visual-reviews"
        current_fingerprint = compute_source_fingerprint(self.session.project.root)
        reports = []
        for path in sorted(root.glob("review-*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(report, dict) and report.get("run_id") == run_id:
                projected = dict(report)
                bound = str(report.get("source_fingerprint") or "")
                projected["validity"] = {
                    "status": "current" if bound and bound == current_fingerprint else "stale",
                    "stale_reasons": [] if bound and bound == current_fingerprint else ["source_changed"],
                    "current_source_fingerprint": current_fingerprint,
                }
                reports.append(projected)
        return {"ok": True, "reports": reports}

    def review_turn(self, body: dict[str, Any]) -> dict[str, Any]:
        turn_id = str(body.get("turn_id") or "")
        turn = getattr(self.session.conversation_service, "turns", {}).get(turn_id)
        if turn is None:
            return {"ok": False, "code": "TURN_NOT_FOUND", "error": "找不到这次生成任务。"}
        if getattr(turn, "state", "") != "completed":
            return {"ok": False, "code": "TURN_NOT_REVIEWABLE", "error": "只有已完成的生成任务可以对照。"}
        if self.session.project is None or not isinstance(getattr(turn, "result", None), dict):
            return {"ok": False, "code": "PROJECT_UNAVAILABLE", "error": "当前没有可检查的构件项目。"}
        if body.get("project_epoch") != self.session.project_epoch:
            return {"ok": False, "code": "PROJECT_CHANGED", "error": "项目已切换，请从当前任务重新发起对照。"}
        result = turn.result
        if not result.get("ok"):
            return {"ok": False, "code": "TURN_FAILED", "error": "生成任务没有成功交付，无法进行对照。"}
        if result.get("current_project_epoch") != self.session.project_epoch:
            return {"ok": False, "code": "PROJECT_CHANGED", "error": "任务完成后项目身份发生变化，请从当前任务重新发起对照。"}

        assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
        run_id = str(assistant.get("run_id") or "")
        if not run_id:
            return {"ok": False, "code": "RUN_ID_MISSING", "error": "任务缺少运行编号，无法绑定审查证据。"}
        project_root = Path(self.session.project.root)
        source_fingerprint = compute_source_fingerprint(project_root)
        delivered_fingerprint = str(assistant.get("source_fingerprint") or "")
        if not delivered_fingerprint or delivered_fingerprint != source_fingerprint:
            return {"ok": False, "code": "SOURCE_STALE", "error": "生成后源码已变化；请先重新预览并基于当前任务重新对照。"}

        planning_artifact, object_plan, plan_id = self._plan_context(turn)
        targets = self._targets(planning_artifact, object_plan, turn.body)
        reference_frames, error = self._reference_frames(turn)
        if error:
            return {"ok": False, "code": "REFERENCE_UNAVAILABLE", "error": error}

        preview_response = self.session.project_service.preview({"quality": "accurate"})
        if not isinstance(preview_response, dict) or preview_response.get("ok") is not True:
            return {"ok": False, "code": "PREVIEW_UNAVAILABLE", "error": "当前 GDL 无法生成用于对照的预览。"}
        preview_payload = preview_response.get("preview")
        if not isinstance(preview_payload, dict):
            return {"ok": False, "code": "PREVIEW_UNAVAILABLE", "error": "预览器未返回有效图像数据。"}

        with tempfile.TemporaryDirectory(prefix="openbrep_visual_review_") as temp_dir:
            capture = self._capture(
                preview_payload,
                source_fingerprint=source_fingerprint,
                out_dir=Path(temp_dir),
                view_names=self.REQUIRED_VIEWS,
            )
            rendered_frames, capture_error = self._rendered_frames(capture, Path(temp_dir))
            if capture_error:
                return {"ok": False, "code": "PREVIEW_CAPTURE_INCOMPLETE", "error": capture_error}
            request = VisualReviewRequest(
                review_id=f"review-{uuid.uuid4().hex}",
                run_id=run_id,
                source_fingerprint=source_fingerprint,
                plan_id=plan_id,
                user_request=str(turn.body.get("message") or turn.body.get("prompt") or "").strip()
                or "Review this generated GDL object against its original reference.",
                observation={
                    "vision_extractions": result.get("extractions") or [],
                    "candidate_spec": planning_artifact.get("candidate_spec") if isinstance(planning_artifact, dict) else None,
                },
                plan={"object_plan": object_plan, "planning_artifact": planning_artifact},
                targets=tuple(targets),
                frames=tuple([*reference_frames, *rendered_frames]),
            )
            try:
                llm = self._llm()
                review = review_visual_result(llm, request, codex_intent="IMAGE")
                if (
                    self.session.project is None
                    or self.session.project_epoch != body.get("project_epoch")
                    or Path(self.session.project.root).resolve() != project_root.resolve()
                    or compute_source_fingerprint(project_root) != source_fingerprint
                ):
                    return {
                        "ok": False,
                        "code": "SOURCE_STALE",
                        "error": "审查期间项目或源码发生变化；本次结果未保存，请基于当前版本重新对照。",
                    }
                report_path = save_visual_review(project_root, review, frames=request.frames)
                saved_review = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception as exc:  # provider and storage failures must be visible
                return {"ok": False, "code": "VISUAL_REVIEW_FAILED", "error": str(exc)}

        return {
            "ok": True,
            "review": saved_review,
            "report_path": report_path.relative_to(project_root).as_posix(),
            "capture_status": capture.get("status"),
            "capture_views": [
                {"view_id": view.get("name"), "image_sha256": view.get("image_sha256")}
                for view in capture.get("views", [])
            ],
        }

    def _llm(self) -> Any:
        if self._llm_factory is not None:
            return self._llm_factory()
        return self.session.conversation_service._llm()

    def _capture(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if self._capture_views is not None:
            return self._capture_views(payload, **kwargs)
        from openbrep.runtime.visual_self_check import capture_preview_views

        return capture_preview_views(payload, **kwargs)

    @staticmethod
    def _plan_context(turn: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        plan = getattr(turn, "plan", None)
        if isinstance(plan, dict):
            typed = plan.get("typed_plan") if isinstance(plan.get("typed_plan"), dict) else {}
            object_plan = plan.get("object_plan") if isinstance(plan.get("object_plan"), dict) else {}
            plan_id = str(plan.get("plan_id") or "")
            if typed:
                return typed, object_plan, plan_id or WorkbenchVisualReviewService._derived_plan_id(typed, turn.turn_id)

        result = getattr(turn, "result", {}) or {}
        events = result.get("events") if isinstance(result, dict) else []
        for event in events or []:
            if not isinstance(event, dict) or event.get("type") != "object_plan_done":
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            artifact = data.get("planning_artifact") if isinstance(data.get("planning_artifact"), dict) else {}
            object_plan = data.get("object_plan") if isinstance(data.get("object_plan"), dict) else {}
            return artifact, object_plan, WorkbenchVisualReviewService._derived_plan_id(artifact, turn.turn_id)
        return {}, {}, f"plan-{turn.turn_id}"

    @staticmethod
    def _derived_plan_id(artifact: dict[str, Any], turn_id: str) -> str:
        encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
        return f"plan-{turn_id}-{digest}"

    @staticmethod
    def _targets(artifact: dict[str, Any], object_plan: dict[str, Any], turn_body: dict[str, Any]) -> list[ReviewTarget]:
        execution = artifact.get("execution_plan") if isinstance(artifact, dict) else {}
        requirements = execution.get("requirements") if isinstance(execution, dict) else []
        targets: list[ReviewTarget] = []
        for index, item in enumerate(requirements or []):
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("requirement_id") or item.get("id") or f"requirement-{index + 1}")
            description = str(item.get("text") or item.get("description") or "").strip()
            if description:
                targets.append(ReviewTarget(target_id, description))
        if targets:
            return targets
        request = str(turn_body.get("message") or turn_body.get("prompt") or "").strip()
        object_type = str(object_plan.get("object_type") or "GDL构件") if isinstance(object_plan, dict) else "GDL构件"
        return [ReviewTarget("overall-object", f"{object_type} 是否在可观察范围内符合用户需求：{request or '参考图和生成结果'}")]

    def _reference_frames(self, turn: Any) -> tuple[list[ReviewFrame], str | None]:
        frames: list[ReviewFrame] = []
        seen: set[str] = set()
        body = getattr(turn, "body", {}) or {}
        images = body.get("images") if isinstance(body.get("images"), list) else []
        legacy_b64 = body.get("image_b64")
        if legacy_b64:
            images = [{"b64": legacy_b64, "mime": body.get("image_mime") or "image/png", "name": "attachment-1"}, *images]
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict) or not image.get("b64"):
                continue
            raw_id = str(image.get("name") or f"attachment-{index}")
            frame_id = f"ref-{index}-{hashlib.sha256(raw_id.encode()).hexdigest()[:8]}"
            frames.append(ReviewFrame(frame_id, "reference", str(image.get("mime") or "image/png"), str(image["b64"])))
            seen.add(frame_id)
        service = getattr(self.session, "reference_service", None)
        for index, asset_id in enumerate(getattr(turn, "reference_asset_ids", []) or [], start=1):
            stored = service.asset_bytes(str(asset_id)) if service is not None else None
            if stored is None:
                return [], f"生成时采用的参考图 {asset_id} 已无法读取，不能拿其他图片代替。"
            data, mime = stored
            frame_id = f"ref-asset-{index}-{hashlib.sha256(str(asset_id).encode()).hexdigest()[:8]}"
            if frame_id not in seen:
                frames.append(ReviewFrame(frame_id, "reference", mime, base64.b64encode(data).decode("ascii")))
                seen.add(frame_id)
        if not frames:
            return [], "这次生成没有可用的原始参考图。"
        return frames, None

    @staticmethod
    def _rendered_frames(capture: dict[str, Any], capture_root: Path) -> tuple[list[ReviewFrame], str | None]:
        if not isinstance(capture, dict):
            return [], "预览截图服务没有返回多视图清单。"
        views = capture.get("views") if isinstance(capture.get("views"), list) else []
        by_name = {str(view.get("name")): view for view in views if isinstance(view, dict)}
        frames: list[ReviewFrame] = []
        for view_id in WorkbenchVisualReviewService.REQUIRED_VIEWS:
            view = by_name.get(view_id)
            if not view or view.get("status") != "pass" or not view.get("screenshot"):
                return [], f"{view_id} 视图没有通过截图健康检查，暂不能进行视觉对照。"
            path = Path(str(view["screenshot"])).resolve()
            try:
                path.relative_to(capture_root.resolve())
            except ValueError:
                return [], f"{view_id} 截图路径不在本次临时捕获目录中。"
            try:
                data = path.read_bytes()
            except OSError:
                return [], f"{view_id} 截图文件无法读取。"
            digest = hashlib.sha256(data).hexdigest()
            if digest != str(view.get("image_sha256") or ""):
                return [], f"{view_id} 截图哈希不匹配。"
            frames.append(ReviewFrame(f"view-{view_id}", "rendered", "image/png", base64.b64encode(data).decode("ascii"), view_id))
        return frames, None
