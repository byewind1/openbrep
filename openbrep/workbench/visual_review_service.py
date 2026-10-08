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

from openbrep.runtime.repair_policy import RepairContext, decide_repair
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
        if method.upper() == "POST" and path == "/api/vision/repair":
            return self.prepare_repair(body)
        if method.upper() == "POST" and path == "/api/vision/repair/resolve":
            return self.resolve_repair(body)
        prefix = "/api/vision/reviews/"
        if method.upper() == "GET" and path.startswith(prefix):
            return self.saved_reviews(path[len(prefix):])
        return {"ok": False, "error": f"Unknown visual review route: {method} {path}"}

    def prepare_repair(self, body: dict[str, Any]) -> dict[str, Any]:
        """Prepare one user-approved modify turn from a current failed finding."""
        review_id = str(body.get("review_id") or "")
        finding_id = str(body.get("finding_id") or "")
        if not re.fullmatch(r"review-[a-f0-9]{32}", review_id) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", finding_id
        ):
            return {"ok": False, "code": "INVALID_REPAIR_REFERENCE", "error": "修复证据编号无效。"}
        if self.session.project is None:
            return {"ok": False, "code": "PROJECT_UNAVAILABLE", "error": "当前没有可修复的项目。"}
        if body.get("project_epoch") != self.session.project_epoch:
            return {"ok": False, "code": "PROJECT_CHANGED", "error": "项目已切换，请从当前任务重新发起修复。"}
        root = Path(self.session.project.root).resolve()
        path = root / ".openbrep" / "visual-reviews" / f"{review_id}.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"ok": False, "code": "REVIEW_NOT_FOUND", "error": "找不到对应的视觉对照记录。"}
        turns = getattr(self.session.conversation_service, "turns", {})
        turn = next((candidate for candidate in turns.values() if
                     getattr(candidate, "state", "") == "completed" and
                     isinstance(getattr(candidate, "result", None), dict) and
                     (candidate.result.get("assistant") or {}).get("run_id") == report.get("run_id")), None)
        if turn is None:
            return {"ok": False, "code": "TURN_NOT_FOUND", "error": "原始生成任务已不在当前会话中。"}
        artifact, _object_plan, plan_id = self._plan_context(turn)
        fingerprint = compute_source_fingerprint(root)
        selected = [item for item in report.get("findings", []) if isinstance(item, dict) and item.get("finding_id") == finding_id]
        if not selected:
            return {"ok": False, "code": "FINDING_NOT_FOUND", "error": "该 finding 不属于这份对照记录。"}
        bound_report = {**report, "findings": selected}
        target_id = str(selected[0].get("target_id") or "")
        decision = decide_repair(
            bound_report,
            RepairContext(
                run_id=str((turn.result.get("assistant") or {}).get("run_id") or ""),
                source_fingerprint=fingerprint,
                plan_id=plan_id,
                approved_target_ids=frozenset({target_id}),
                remaining_budget=1,
                approval_mode="control",
                allow_reextract=False,
                allow_replan=False,
            ),
        )
        if decision.action != "modify":
            return {"ok": False, "code": "REPAIR_NOT_SUPPORTED", "decision": decision.to_dict(),
                    "error": "此 finding 当前不支持自动修复；需要重新观察或人工调整计划。"}
        summary = str(selected[0].get("summary") or "当前视觉对照发现的问题").strip()
        message = (
            f"针对视觉对照 finding {finding_id}，修复这一项：{summary}。"
            f"严格限制在目标 {target_id}；保留原计划中的其他构造、尺寸与约束。"
            "不要扩大范围；若现有信息不足以安全修改，请说明原因并停止。"
        )
        prepared = self.session.conversation_service.route({
            "phase": "prepare",
            "client_turn_id": f"visual-repair-{hashlib.sha256((review_id + finding_id).encode()).hexdigest()[:32]}",
            "message": message,
            "requested_mode": "plan",
            "confirm_before_execute": True,
            "project_epoch": self.session.project_epoch,
        })
        if isinstance(prepared, dict) and prepared.get("ok"):
            prepared_turn = turns.get(str(prepared.get("turn_id") or ""))
            repair_context = {
                "state": "awaiting_approval", "review_id": review_id,
                "finding_id": finding_id, "run_id": decision.run_id,
                "plan_id": decision.plan_id,
                "before_source_fingerprint": fingerprint,
                "project_root": str(root),
                "target_ids": [target_id],
                "target_summary": summary,
                "decision": decision.to_dict(),
            }
            if prepared_turn is not None:
                original_body = getattr(turn, "body", {}) or {}
                prepared_turn.review_reference_images = list(original_body.get("images") or [])
                prepared_turn.review_reference_image_b64 = original_body.get("image_b64")
                prepared_turn.review_reference_image_mime = original_body.get("image_mime")
                prepared_turn.reference_asset_ids = list(getattr(turn, "reference_asset_ids", []) or [])
                prepared_turn.repair_context = repair_context
            prepared["repair"] = {
                **repair_context,
                "review_id": review_id, "finding_id": finding_id,
            }
        return prepared

    def resolve_repair(self, body: dict[str, Any]) -> dict[str, Any]:
        """Accept a freshly rechecked repair or restore its exact before snapshot."""
        turn_id = str(body.get("turn_id") or "")
        review_id = str(body.get("review_id") or "")
        resolution = str(body.get("resolution") or "")
        if resolution not in {"accept", "restore"}:
            return {"ok": False, "code": "INVALID_REPAIR_RESOLUTION", "error": "修复决定必须是 accept 或 restore。"}
        if body.get("project_epoch") != self.session.project_epoch:
            return {"ok": False, "code": "PROJECT_CHANGED", "error": "项目已切换，请从当前任务重新处理。"}
        if self.session.project is None:
            return {"ok": False, "code": "PROJECT_UNAVAILABLE", "error": "当前没有可处理的项目。"}
        turn = getattr(self.session.conversation_service, "turns", {}).get(turn_id)
        context = getattr(turn, "repair_context", None) if turn is not None else None
        if not isinstance(context, dict) or context.get("state") != "recheck_required":
            return {"ok": False, "code": "REPAIR_NOT_READY", "error": "本轮修复尚未完成，或已处理。"}
        if not re.fullmatch(r"review-[a-f0-9]{32}", review_id):
            return {"ok": False, "code": "INVALID_REPAIR_REFERENCE", "error": "复查记录编号无效。"}
        root = Path(self.session.project.root).resolve()
        path = root / ".openbrep" / "visual-reviews" / f"{review_id}.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"ok": False, "code": "REVIEW_NOT_FOUND", "error": "找不到修复后的视觉复查记录。"}
        assistant = (getattr(turn, "result", None) or {}).get("assistant") or {}
        repair_run_id = str(context.get("repair_run_id") or assistant.get("run_id") or "")
        if not repair_run_id or report.get("run_id") != repair_run_id:
            return {"ok": False, "code": "REPAIR_REVIEW_MISMATCH", "error": "这份视觉复查不属于当前修复任务。"}
        current_fingerprint = compute_source_fingerprint(root)
        if report.get("source_fingerprint") != current_fingerprint:
            return {"ok": False, "code": "REPAIR_REVIEW_STALE", "error": "复查后源码又发生变化，请重新检查当前版本。"}
        target_ids = {str(item) for item in context.get("target_ids") or [] if item}
        coverage = {
            str(item.get("target_id") or ""): str(item.get("status") or "")
            for item in report.get("coverage", []) if isinstance(item, dict)
        }
        findings = [item for item in report.get("findings", []) if isinstance(item, dict)]
        if not target_ids or not target_ids.issubset(coverage):
            return {"ok": False, "code": "REPAIR_TARGET_UNCOVERED", "error": "复查没有覆盖本次修复目标。"}
        if resolution == "accept":
            passing = {
                str(item.get("target_id") or "")
                for item in findings if item.get("outcome") == "pass"
            }
            non_passing = [
                item for item in findings
                if str(item.get("target_id") or "") in target_ids and item.get("outcome") != "pass"
            ]
            if any(coverage.get(target_id) != "covered" for target_id in target_ids) or not target_ids.issubset(passing) or non_passing:
                return {"ok": False, "code": "REPAIR_RECHECK_NOT_PASSED", "error": "目标未获得完整 pass 证据，不能接受修复；可以恢复修复前版本。"}
            context["state"] = "accepted"
            context["resolved_review_id"] = review_id
            context["resolution"] = "accept"
            context["resolution_source_fingerprint"] = current_fingerprint
            turn.result["repair_context"] = dict(context)
            return {"ok": True, "repair_context": dict(context)}

        before_revision_id = str(context.get("before_revision_id") or "")
        if not before_revision_id:
            return {"ok": False, "code": "REPAIR_RESTORE_UNAVAILABLE", "error": "缺少修复前版本，无法安全恢复。"}
        from openbrep.project_write_lock import project_write_lock
        from openbrep.revisions import restore_revision

        try:
            with project_write_lock(root):
                if compute_source_fingerprint(root) != current_fingerprint:
                    return {"ok": False, "code": "REPAIR_REVIEW_STALE", "error": "恢复前源码又发生变化，请重新加载并核对。"}
                restored = restore_revision(root, before_revision_id, message="restore rejected visual repair")
                context["state"] = "restored"
                context["resolved_review_id"] = review_id
                context["resolution"] = "restore"
                context["restore_revision_id"] = restored.revision_id
                context["resolution_source_fingerprint"] = compute_source_fingerprint(root)
                refresh = getattr(self.session, "refresh_same_project", None)
                if callable(refresh):
                    from openbrep.hsf_project import HSFProject
                    refresh(HSFProject.load_from_disk(str(root)))
                turn.result["repair_context"] = dict(context)
        except Exception as exc:
            return {"ok": False, "code": "REPAIR_RESTORE_FAILED", "error": f"恢复修复前版本失败：{exc}"}
        return {"ok": True, "repair_context": dict(context)}

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
        target_context = {**turn.body, "_repair_context": getattr(turn, "repair_context", None)}
        targets = self._targets(planning_artifact, object_plan, target_context)
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
        repair_context = turn_body.get("_repair_context") if isinstance(turn_body, dict) else None
        if isinstance(repair_context, dict) and repair_context.get("target_ids"):
            summary = str(repair_context.get("target_summary") or "修复目标")
            return [
                ReviewTarget(str(target_id), f"复查本轮是否修复：{summary}")
                for target_id in repair_context["target_ids"]
                if isinstance(target_id, str) and target_id
            ]
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
        images = getattr(turn, "review_reference_images", None)
        if not isinstance(images, list):
            images = body.get("images") if isinstance(body.get("images"), list) else []
        legacy_b64 = getattr(turn, "review_reference_image_b64", None) or body.get("image_b64")
        if legacy_b64:
            legacy_mime = getattr(turn, "review_reference_image_mime", None) or body.get("image_mime") or "image/png"
            images = [{"b64": legacy_b64, "mime": legacy_mime, "name": "attachment-1"}, *images]
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
