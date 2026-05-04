from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.project_reports import write_object_plan_report
from openbrep.runtime.pipeline import TaskRequest


FORCE_GENERATE_PREFIX = "[GENERATE]"


@dataclass
class GenerationService:
    session_state: object
    pipeline_class: type
    config_loader_fn: Callable[[], object]
    build_generation_result_plan_fn: Callable[..., object]
    begin_generation_state_fn: Callable[[object], str]
    is_active_generation_fn: Callable[[object, str], bool]
    should_accept_generation_result_fn: Callable[[object, str], bool]
    finish_generation_state_fn: Callable[[object, str, str], bool]
    generation_cancelled_message_fn: Callable[[], str]
    trim_history_fn: Callable[[list[dict]], list[dict]]
    is_debug_intent_fn: Callable[[str], bool]
    get_debug_mode_fn: Callable[[str], str]
    is_explainer_intent_fn: Callable[[str], bool]
    is_modify_bridge_prompt_fn: Callable[[str], bool]
    is_post_clarification_prompt_fn: Callable[[str], bool]
    apply_generation_plan_fn: Callable[..., tuple[str, list[str]]]
    build_generation_reply_fn: Callable[[str, str, list[str] | None], str]
    logger: logging.Logger = logging.getLogger(__name__)

    def run_agent_generate(
        self,
        user_input: str,
        proj: HSFProject,
        status_col,
        gsm_name: str | None = None,
        auto_apply: bool = True,
        debug_image_b64: str | None = None,
        debug_image_mime: str = "image/png",
    ) -> str:
        self.logger.debug("run_agent_generate called, instruction: %s", user_input[:100])
        request_input, force_generate = _strip_force_generate_prefix(user_input)
        generation_id = self.begin_generation_state_fn(self.session_state)
        status_ph = status_col.empty()
        debug_mode = (
            not force_generate
            and self.is_debug_intent_fn(request_input)
            and not self.is_explainer_intent_fn(request_input)
            and not self.is_post_clarification_prompt_fn(request_input)
        )
        debug_type = self.get_debug_mode_fn(request_input)

        def on_event(event_type, data):
            self._handle_generation_event(
                event_type,
                data,
                status_ph=status_ph,
                generation_id=generation_id,
                debug_mode=debug_mode,
                debug_type=debug_type,
            )

        try:
            recent_history = self.trim_history_fn([
                m for m in self.session_state.chat_history[-8:]
                if m["role"] in ("user", "assistant")
            ])

            pipeline_project = proj if auto_apply else deepcopy(proj)
            intent = self._resolve_generation_intent(request_input, pipeline_project, debug_mode)

            self.logger.info(
                "pipeline generate route=%s image_name=%s has_project=%s prompt_len=%d",
                intent.lower(),
                "inline-image" if debug_image_b64 else "none",
                bool(proj),
                len(request_input or ""),
            )

            pipeline = self.pipeline_class(trace_dir="./traces")
            pipeline.config = self.config_loader_fn()
            request = TaskRequest(
                user_input=request_input,
                intent=intent,
                project=pipeline_project,
                work_dir=self.session_state.work_dir,
                gsm_name=gsm_name or pipeline_project.name,
                output_dir=str(Path(self.session_state.work_dir) / "output"),
                assistant_settings=self.session_state.get("assistant_settings", ""),
                on_event=on_event,
                history=recent_history,
                last_code_context=None,
                should_cancel=lambda: not self.should_accept_generation_result_fn(self.session_state, generation_id),
                image_b64=debug_image_b64,
                image_mime=debug_image_mime,
            )

            result = pipeline.execute(request)
            if not result.success:
                status_ph.empty()
                self.finish_generation_state_fn(self.session_state, generation_id, "failed")
                return f"❌ **错误**: {result.error}"

            if not self.should_accept_generation_result_fn(self.session_state, generation_id):
                status_ph.empty()
                self.finish_generation_state_fn(self.session_state, generation_id, "cancelled")
                return self.generation_cancelled_message_fn()

            status_ph.empty()
            result_prefix = ""
            code_blocks: list[str] = []
            plan = self.build_generation_result_plan_fn(result, auto_apply=auto_apply, gsm_name=gsm_name)
            if plan.has_changes:
                if auto_apply and result.project is not None:
                    if proj is not result.project:
                        self.session_state.project = result.project
                        proj = result.project
                    result_prefix, code_blocks = self.apply_generation_plan_fn(
                        plan,
                        proj,
                        gsm_name,
                        already_applied=True,
                    )
                    self._persist_object_plan_report(result, request_input, intent)
                else:
                    result_prefix, code_blocks = self.apply_generation_plan_fn(
                        plan,
                        proj,
                        gsm_name,
                        already_applied=False,
                    )
            self.finish_generation_state_fn(self.session_state, generation_id, "completed")
            return self.build_generation_reply_fn(result.plain_text, result_prefix, code_blocks)

        except Exception as e:
            status_ph.empty()
            self.finish_generation_state_fn(self.session_state, generation_id, "failed")
            return f"❌ **错误**: {str(e)}"

    def _persist_object_plan_report(self, result, instruction: str, intent: str) -> None:
        project = getattr(result, "project", None)
        object_plan = getattr(result, "object_plan", None)
        if project is None or not object_plan:
            return
        try:
            write_object_plan_report(
                project,
                object_plan,
                instruction=instruction,
                intent=intent,
            )
        except Exception as exc:
            self.logger.warning("failed to write object plan report: %s", exc)

    def _resolve_generation_intent(self, user_input: str, pipeline_project: HSFProject, debug_mode: bool) -> str:
        if debug_mode:
            return "REPAIR"
        if self.is_modify_bridge_prompt_fn(user_input):
            return "MODIFY"
        if self.is_post_clarification_prompt_fn(user_input):
            if "本次确认目标：先快速解释脚本结构" in user_input:
                return "CHAT"
            return "MODIFY"
        if self.is_explainer_intent_fn(user_input):
            return "CHAT"
        if any(pipeline_project.get_script(st) for st in ScriptType):
            return "MODIFY"
        return "CREATE"

    def _guarded_event_update(self, status_ph, generation_id: str, method_name: str, message: str) -> None:
        if not self.is_active_generation_fn(self.session_state, generation_id):
            return
        getattr(status_ph, method_name)(message)

    def _handle_generation_event(
        self,
        event_type,
        data,
        *,
        status_ph,
        generation_id: str,
        debug_mode: bool,
        debug_type: str,
    ) -> None:
        if not self.is_active_generation_fn(self.session_state, generation_id):
            return
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            mode_tag = f" [Debug:{debug_type}]" if debug_mode else ""
            self._guarded_event_update(status_ph, generation_id, "info", f"🔍 分析中{mode_tag}... 脚本: {', '.join(scripts)}")
        elif event_type == "attempt":
            self._guarded_event_update(status_ph, generation_id, "info", "🧠 调用 AI...")
        elif event_type == "llm_response":
            self._guarded_event_update(status_ph, generation_id, "info", f"✏️ 收到 {data['length']} 字符，解析中...")
        elif event_type == "validate":
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])
            if errors:
                self._guarded_event_update(status_ph, generation_id, "error", f"❌ 发现 {len(errors)} 个错误，AI 自动修复中...")
            elif warnings:
                self._guarded_event_update(status_ph, generation_id, "warning", f"⚠️ 发现 {len(warnings)} 条建议，已附在结果中")
            else:
                self._guarded_event_update(status_ph, generation_id, "success", "✅ 校验通过")
        elif event_type == "rewrite":
            round_num = data.get("round", 2)
            self._guarded_event_update(status_ph, generation_id, "info", f"🔄 第 {round_num} 轮修复中...")
        elif event_type == "cancelled":
            self._guarded_event_update(status_ph, generation_id, "warning", "⏹️ 正在停止当前生成...")
        elif event_type == "compile_result":
            if data.get("success"):
                self._guarded_event_update(status_ph, generation_id, "success", "✅ 编译验证通过")
            elif data.get("error"):
                self._guarded_event_update(status_ph, generation_id, "warning", "⚠️ 编译验证失败，已附在结果中")
        elif event_type == "status":
            self._guarded_event_update(status_ph, generation_id, "info", data.get("message", ""))
        elif event_type == "vision_analysis_done":
            component = data.get("component_type", "")
            label = f"「{component}」" if component and component != "未知构件" else ""
            self._guarded_event_update(status_ph, generation_id, "info", f"🖼️ 图像分析完成{label}，正在生成 GDL…")
        elif event_type == "object_plan_done":
            object_type = data.get("object_type", "")
            label = f"「{object_type}」" if object_type else ""
            self._guarded_event_update(status_ph, generation_id, "info", f"📐 对象规划完成{label}，正在生成 GDL…")


def _strip_force_generate_prefix(user_input: str) -> tuple[str, bool]:
    text = user_input or ""
    if not text.startswith(FORCE_GENERATE_PREFIX):
        return text, False
    return text[len(FORCE_GENERATE_PREFIX):].lstrip(), True
