from __future__ import annotations

from pathlib import Path
from typing import Any

from openbrep.explainer.chat_adapter import build_chat_explanation_reply
from openbrep.explainer.context_builder import (
    build_project_context,
    build_project_parameter_context,
    build_project_script_context,
    resolve_parameter_targets,
    resolve_script_target,
)
from openbrep.explainer.service import (
    explain_parameter_context,
    explain_project_context,
    explain_script_context,
)
from openbrep.learning import ErrorLearningStore
from openbrep.runtime.pipeline import TaskRequest
from openbrep.workbench.preview_service import preview_payload
from openbrep.workbench.project_service import validate_image_payload
from openbrep.workbench.view_models import classify_code_blocks, classify_vision_error


class WorkbenchAssistantService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def assistant_reply(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Assistant message is empty."}

        parameter_targets = resolve_parameter_targets(self.session.project, message)
        if parameter_targets:
            context = build_project_parameter_context(self.session.project, parameter_targets[0])
            if context is not None:
                explanation = explain_parameter_context(context)
                return {
                    "ok": True,
                    "assistant": {
                        "kind": "explain_parameter",
                        "reply": build_chat_explanation_reply(explanation, user_input=message),
                    },
                }

        script_target = resolve_script_target(message)
        if script_target:
            context = build_project_script_context(self.session.project, script_target)
            if context is not None:
                explanation = explain_script_context(context)
                return {
                    "ok": True,
                    "assistant": {
                        "kind": "explain_script",
                        "reply": build_chat_explanation_reply(explanation, user_input=message),
                    },
                }

        explanation = explain_project_context(build_project_context(self.session.project))
        return {
            "ok": True,
            "assistant": {
                "kind": "explain_project",
                "reply": build_chat_explanation_reply(explanation, user_input=message),
            },
        }

    def list_assistant_history(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": True, "messages": []}
        try:
            entries = ErrorLearningStore(self.session.source_path).list_chat_transcript()
            messages = [
                {"role": entry.role if entry.role in {"user", "assistant"} else "assistant", "content": entry.content}
                for entry in entries
                if entry.content
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load assistant history: {exc}", "messages": []}
        return {"ok": True, "messages": messages}

    def save_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before saving assistant history."}
        messages = body.get("messages") or []
        if not isinstance(messages, list):
            return {"ok": False, "error": "Assistant history messages must be a list."}
        try:
            count = ErrorLearningStore(self.session.source_path).rewrite_chat_transcript(
                messages,
                project_name=self.session.project.name,
                source="react_workbench",
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save assistant history: {exc}"}
        return {"ok": True, "count": count}

    def clear_assistant_history(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": True, "count": 0}
        try:
            count = ErrorLearningStore(self.session.source_path).rewrite_chat_transcript(
                [],
                project_name=self.session.project.name,
                source="react_workbench",
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to clear assistant history: {exc}"}
        return {"ok": True, "count": count}

    def extract_assistant_code_blocks(self, body: dict[str, Any]) -> dict[str, Any]:
        content = str(body.get("content") or "")
        try:
            extracted = classify_code_blocks(content)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to extract assistant code blocks: {exc}", "blocks": []}
        blocks = [
            {
                "path": path,
                "script_name": Path(path).name,
                "content": script,
            }
            for path, script in extracted.items()
        ]
        return {"ok": True, "blocks": blocks}

    def generate_with_assistant(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Generation message is empty."}

        # 计划确认门（V3）：仅 GUI MODIFY（非 DEBUG/REPAIR）请求，先出计划等确认
        if body.get("confirm_plan") and str(body.get("intent") or "MODIFY") == "MODIFY":
            return self._generate_with_confirmation(body)

        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before generating changes."}
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline, request = self._build_generate_pipeline(
            body, image_payload, on_event=on_event
        )
        result = pipeline.execute(request)
        # success=False 只在"无可交付物"时才视为硬失败；验证未过但有产出时
        # 照常交付（verification 报告会如实显示 FAIL），避免丢掉用户的生成结果
        if not result.success and result.project is None and not (result.plain_text or result.scripts):
            error = result.error or "Generation failed."
            if image_payload["image_b64"]:
                error = classify_vision_error(Exception(error))
            return {"ok": False, "error": error, "events": events}

        if result.project is not None:
            self.session.project = result.project
        self.session.project.save_to_disk()
        return {
            "ok": True,
            "assistant": {
                "kind": "generate",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
                "verification": result.verification,
                "acceptance": result.metadata.get("acceptance"),
            },
            "preview": preview_payload(self.session.project),
            "warnings": [],
            "events": events,
        }

    def _generate_with_confirmation(self, body: dict[str, Any]) -> dict[str, Any]:
        """confirm_plan=True 的 MODIFY 请求：先做一次计划调用，返回 awaiting_confirmation。

        计划调用失败/JSON 不合法 → pipeline 已回落为直接执行（旧行为），
        本方法原样返回执行结果并带 plan_failed 标记，不卡死用户。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before generating changes."}
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline, request = self._build_generate_pipeline(body, image_payload, on_event=on_event)
        result = pipeline.execute(request)
        if result.metadata.get("awaiting_confirmation"):
            # 存 session pending_plan（含原始 body 与项目代次，确认时校验不跨项目）
            self.session.pending_plan = {
                "plan": result.metadata["pending_plan"],
                "body": body,
                "project_epoch": self.session.project_epoch,
            }
            return {
                "ok": True,
                "awaiting_confirmation": True,
                "pending_plan": result.metadata["pending_plan"],
                "events": events,
            }

        # 计划失败回落 / micro_modify / V1 DSL 命中：直接交付执行结果
        if result.project is not None:
            self.session.project = result.project
        self.session.project.save_to_disk()
        return {
            "ok": True,
            "assistant": {
                "kind": "generate",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
                "verification": result.verification,
                "acceptance": result.metadata.get("acceptance"),
            },
            "preview": preview_payload(self.session.project),
            "warnings": [],
            "events": events,
            "plan_failed": True,
        }

    def confirm_modify(self, body: dict[str, Any]):
        """POST /api/modify/confirm：审批待确认计划。

        approve=True → 清 pending_plan 并带已确认计划执行（stream=True 走 SSE）；
        approve=False → 清 pending_plan 返回已取消；无 pending → NO_PENDING_PLAN。
        """
        pending = getattr(self.session, "pending_plan", None)
        if pending is None:
            return {"ok": False, "code": "NO_PENDING_PLAN", "error": "没有待确认的修改计划，请先发起一次修改。"}
        if pending.get("project_epoch") != getattr(self.session, "project_epoch", None):
            self.session.pending_plan = None
            return {"ok": False, "code": "NO_PENDING_PLAN", "error": "待确认的修改计划已失效（项目已切换），请重新发起修改。"}
        self.session.pending_plan = None
        if body.get("approve") is not True:
            return {"ok": True, "cancelled": True, "message": "已取消本次修改。"}

        plan = pending["plan"]
        request_body = dict(pending["body"])
        request_body["confirm_plan"] = False
        request_body["confirmed_plan"] = plan
        if body.get("stream"):
            import threading

            cancel_event = threading.Event()
            return self.generate_with_assistant_stream(request_body, cancel_event=cancel_event)
        return self.generate_with_assistant(request_body)

    def generate_with_assistant_stream(
        self, body: dict[str, Any], cancel_event: Any | None = None
    ):
        """流式生成：通过 SSE 实时输出 agent loop 的每一步事件。

        返回一个 generator，产出 `{type, data}` 事件。客户端断连后 generator
        被 close()，finally 块设置 cancel_event，通知 agent loop 的 should_cancel。
        """
        import queue
        import threading

        message = str(body.get("message") or "").strip()
        if not message:
            yield {"type": "error", "data": {"error": "Generation message is empty."}}
            return

        if self.session.source_path is None:
            yield {"type": "error", "data": {"error": "Load an HSF project before generating changes."}}
            return
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            yield {"type": "error", "data": {"error": image_payload["error"]}}
            return

        q: queue.Queue = queue.Queue()

        def on_event(event_type, data):
            q.put({"type": event_type, "data": data})

        def should_cancel():
            return cancel_event is not None and cancel_event.is_set()

        def run_pipeline():
            try:
                pipeline, request = self._build_generate_pipeline(
                    body, image_payload, on_event=on_event, should_cancel=should_cancel
                )
                result = pipeline.execute(request)
                if not result.success and result.project is None and not (result.plain_text or result.scripts):
                    error = result.error or "Generation failed."
                    if image_payload["image_b64"]:
                        error = classify_vision_error(Exception(error))
                    q.put({"type": "error", "data": {"error": error}})
                    return

                if result.project is not None:
                    self.session.project = result.project
                self.session.project.save_to_disk()
                q.put({
                    "type": "done",
                    "data": {
                        "ok": True,
                        "assistant": {
                            "kind": "generate",
                            "reply": result.plain_text,
                            "changed_files": list((result.scripts or {}).keys()),
                            "intent": result.intent,
                            "verification": result.verification,
                            "acceptance": result.metadata.get("acceptance"),
                        },
                        "preview": preview_payload(self.session.project),
                        "warnings": [],
                    },
                })
            except Exception as exc:
                q.put({"type": "error", "data": {"error": str(exc)}})

        threading.Thread(target=run_pipeline, daemon=True).start()

        try:
            while True:
                try:
                    event = q.get(timeout=0.5)
                except queue.Empty:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    continue
                yield event
                if event["type"] in ("done", "error"):
                    break
        finally:
            if cancel_event is not None:
                cancel_event.set()

    def _build_generate_pipeline(
        self,
        body: dict[str, Any],
        image_payload: dict[str, Any],
        on_event: Any,
        should_cancel: Any | None = None,
    ) -> tuple[Any, TaskRequest]:
        """构造 generate 用的 pipeline 与 TaskRequest，供同步/流式复用。"""
        pipeline = self.session.pipeline_class(trace_dir="./traces")
        intent = str(body.get("intent") or "MODIFY")
        request = TaskRequest(
            user_input=str(body.get("message") or "").strip(),
            intent=intent,
            project=self.session.project,
            work_dir=str(self.session.source_path.parent),
            output_dir=str(self.session.source_path.parent / "output"),
            gsm_name=self.session.project.name,
            image_b64=image_payload["image_b64"],
            image_mime=image_payload["image_mime"],
            assistant_settings=str(body.get("assistant_settings") or self.session.assistant_settings),
            history=list(body.get("history") or []),
            on_event=on_event,
            should_cancel=should_cancel,
            # agent_loop 默认 None：由 pipeline 按 intent 默认策略启用
            agent_loop=body.get("agent_loop") if "agent_loop" in body else None,
            # 流式请求默认开启 plan 阶段，让前端可展示可审查计划；非流式保持兼容
            agent_loop_plan=body.get("agent_loop_plan", should_cancel is not None),
            # 计划确认门（V3）：仅 GUI MODIFY 请求置 True；确认后经 confirmed_plan 注入
            confirm_plan=bool(body.get("confirm_plan")) and intent == "MODIFY",
            confirmed_plan=body.get("confirmed_plan") if isinstance(body.get("confirmed_plan"), dict) else None,
        )
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            pipeline.config.agent.max_iterations = self.session.max_retries
        return pipeline, request
