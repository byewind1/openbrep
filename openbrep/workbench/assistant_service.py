from __future__ import annotations

import logging
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
from openbrep.feedback import append_feedback
from openbrep.learning import ErrorLearningStore
from openbrep.runtime.pipeline import ImageRef, TaskRequest
from openbrep.workbench.delivery_presentation import (
    delivery_payload_from_result,
    normalize_continue_from,
)
from openbrep.workbench.preview_service import preview_payload
from openbrep.workbench.project_service import validate_image_payload
from openbrep.workbench.settings_service import effective_session_reasoning_effort
from openbrep.workbench.view_models import classify_code_blocks, classify_vision_error

logger = logging.getLogger(__name__)


class WorkbenchAssistantService:
    def __init__(self, session: Any) -> None:
        self.session = session

    @staticmethod
    def _merge_continue_from(result: Any, continue_from: dict[str, Any] | None) -> Any:
        """ST03：继续操作显式关联原 run（只写 metadata，不改 prompt）。"""
        if not continue_from:
            return result
        try:
            metadata = dict(getattr(result, "metadata", None) or {})
            metadata["continue_from"] = dict(continue_from)
            result.metadata = metadata
        except Exception:
            logger.debug("continue_from metadata attach skipped", exc_info=True)
        return result

    @staticmethod
    def _assistant_delivery_block(
        result: Any,
        *,
        instruction: str = "",
        continue_from: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return delivery_payload_from_result(
            result,
            original_instruction=instruction or None,
            continue_from=continue_from,
        )

    @staticmethod
    def _generate_assistant_dict(
        result: Any,
        *,
        instruction: str = "",
        continue_from: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """三处 generate 路径共用的 assistant 载荷（含 ST03 delivery）。"""
        scripts = result.scripts or {}
        delivery = WorkbenchAssistantService._assistant_delivery_block(
            result,
            instruction=instruction,
            continue_from=continue_from,
        )
        presentation = delivery.get("presentation") or {}
        # partial_change / snapshot_failed：已改文件以 delivery_source 为准，
        # 不再只报 handler 声称的 scripts keys（避免把空 scripts 说成无修改）。
        if presentation.get("state") in {"partial_change", "snapshot_failed"}:
            changed_files = list(presentation.get("changed_files") or [])
            if not changed_files:
                changed_files = list(scripts.keys())
        else:
            changed_files = list(scripts.keys())
        return {
            "kind": "generate",
            "reply": result.plain_text,
            "changed_files": changed_files,
            "intent": result.intent,
            "verification": result.verification,
            "acceptance": result.metadata.get("acceptance"),
            "delivery_source": delivery.get("delivery_source"),
            "delivery": presentation,
            "run_id": presentation.get("run_id"),
            "continue_from": delivery.get("continue_from"),
        }

    def _new_pipeline(self):
        """构造 pipeline 时显式传 session 解析出的 config 路径（B3）。

        pipeline 每请求新建，TaskPipeline 默认 GDLAgentConfig.load(None) 只有
        env/cwd 逻辑；git worktree 下直接启动时会与 session 的
        resolve_workbench_config_path（含 git-common-dir 逻辑）解析到不同的
        config.toml，丢自定义 provider 的 alias→target_model 映射。config_path
        为 None（旧式替身 session）时不传，保持原行为。
        """
        cfg_path = getattr(self.session, "config_path", None)
        if cfg_path is None:
            return self.session.pipeline_class(trace_dir="./traces")
        return self.session.pipeline_class(trace_dir="./traces", config_path=str(cfg_path))

    def assistant_reply(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Assistant message is empty."}

        # ST04：显式"沉淀成 skill / 保存为技能"走提案路径，绕过修改代码流程
        if self._is_explicit_skill_request(message):
            return self._explicit_skill_response(message)

        # D3：用户选中的是 ChatGPT Codex（openai-codex）订阅模型 → CHAT/EXPLAIN
        # 由该模型完成（ephemeral thread + 临时只读 cwd + approval never）。
        # 无项目（CHAT）不创建任何目录；有项目（EXPLAIN）经 pipeline 只读摘要
        # 注入 prompt，绝不创建 revision。非 codex 模型走下方原有本地解释器，
        # 逐字节不变。
        from openbrep.config import is_codex_qualified_model

        if is_codex_qualified_model(self.session.llm_model):
            return self._codex_assistant_reply(body, message)

        if self.session.project is None:
            # 既有本地解释器需要项目上下文；无项目时给出可操作提示而不是 500
            # （D3 顺手收口：CHAT 无项目的稳定语义）。
            return {
                "ok": False,
                "error": "请先创建或打开一个 GDL 项目，再进行解释。",
            }

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

    def _codex_assistant_reply(self, body: dict[str, Any], message: str) -> dict[str, Any]:
        """D3：Codex 模型 CHAT/EXPLAIN——复用 pipeline 的 CHAT intent 契约。

        EXPLAIN 只读项目摘要注入 prompt（pipeline._handle_codex_chat）；
        CHAT（无项目）不创建任何目录；错误映射为稳定文案（上游原文零回显）。
        """
        from openbrep.runtime.pipeline import TaskRequest

        pipeline = self._new_pipeline()
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            # D6+D16：Fixed 模式 effort 从会话生效值同步（会话覆盖优先，否则 config 已保存值）
            pipeline.config.llm.reasoning_effort = effective_session_reasoning_effort(self.session)
            pipeline.config.llm.codex_routing_mode = (
                self.session.config.llm.effective_codex_routing_mode()
            )
            try:
                pipeline.codex_provider = self.session.settings_service._codex_provider()
            except Exception:  # noqa: BLE001 —— provider 不可用留给 LLMAdapter fail closed
                pipeline.codex_provider = None
        request = TaskRequest(
            user_input=message,
            intent="CHAT",
            project=self.session.project,
            assistant_settings=self.session.assistant_settings,
            history=list(body.get("history") or []),
        )
        result = pipeline.execute(request)
        if result.success:
            return {
                "ok": True,
                "assistant": {"kind": "chat", "reply": result.plain_text},
            }
        error = result.error or "对话失败，请稍后重试。"
        return {"ok": False, "error": error}

    def list_assistant_history(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": True, "messages": []}
        try:
            entries = ErrorLearningStore(self.session.source_path).list_chat_transcript()
            messages = []
            for entry in entries:
                if not entry.content:
                    continue
                role = entry.role if entry.role in {"user", "assistant"} else "assistant"
                item: dict[str, Any] = {"role": role, "content": entry.content}
                meta = entry.meta if isinstance(entry.meta, dict) else None
                if meta:
                    # ST03 F2：把持久化的 delivery / continue 关联原样带回前端
                    for key in (
                        "delivery",
                        "delivery_source",
                        "delivery_continue_from",
                        "original_instruction",
                        "run_id",
                        "changed_files",
                        "error_category",
                    ):
                        if key in meta and meta[key] is not None:
                            item[key] = meta[key]
                messages.append(item)
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

    def import_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        """P6a：把另一个项目的聊天记录追加合并进当前项目（纯文件操作，无 LLM）。

        追加语义：源 transcript 清洗为 {role, content}（role 非 user/assistant →
        assistant，空 content 跳过，与 list_assistant_history 同规则），
        通过 append_chat_messages 追加到当前项目 transcript 尾部，source 标记
        "imported:<源目录名>"。空源不算错误（imported=0）。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before importing assistant history."}

        source_path = str(body.get("source_path") or "").strip()
        if not source_path:
            return {"ok": False, "error": "source_path is required."}
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            return {"ok": False, "error": f"Source project path does not exist: {source_path}"}
        if not source.is_dir():
            return {"ok": False, "error": f"Source project path is not a directory: {source_path}"}
        if source == self.session.source_path.resolve():
            return {"ok": False, "error": "Source project is the same as the current project."}

        try:
            entries = ErrorLearningStore(source).list_chat_transcript()
            messages = [
                {"role": entry.role if entry.role in {"user", "assistant"} else "assistant", "content": entry.content}
                for entry in entries
                if entry.content
            ]
            if not messages:
                return {"ok": True, "imported": 0, "source_name": source.name}
            count = ErrorLearningStore(self.session.source_path).append_chat_messages(
                messages,
                project_name=self.session.project.name,
                source=f"imported:{source.name}",
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to import assistant history: {exc}"}
        return {"ok": True, "imported": count, "source_name": source.name}

    # ── P6b：聊天记录整理成指令（distill，LLM 只读，不写任何文件）────────────
    # system prompt 一处定义：保留原始意图 / 明确约束 / GDL 代码线索，
    # 不编造记录外要求，只输出指令文本本身。
    DISTILL_HISTORY_SYSTEM_PROMPT = (
        "你是一个 GDL 工作台意图整理器。把以下 GDL 工作台对话整理成一段"
        "可直接发给 AI 的指令：保留用户的原始意图、明确提到的尺寸/数量/样式约束、"
        "以及记录中出现过的 GDL 代码线索（代码用 ```gdl 围栏原样保留）；"
        "不要编造记录里没有的要求；输出只要指令文本本身。"
    )
    DISTILL_HISTORY_LIMIT = 30

    def distill_history_intent(self, body: dict[str, Any]) -> dict[str, Any]:
        """P6b：LLM 把当前项目最近 N 条聊天记录整理成一段可直接发送的指令。

        只读 transcript + 一次 LLM 调用，不写任何脚本/项目文件；前端拿到
        instruction 后填入 AI 面板输入框草稿，由用户审阅后手动发送（绝不自动发）。
        """
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before distilling assistant history."}
        try:
            entries = ErrorLearningStore(self.session.source_path).list_chat_transcript()
            messages = [
                {"role": entry.role if entry.role in {"user", "assistant"} else "assistant", "content": entry.content}
                for entry in entries
                if entry.content
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load assistant history: {exc}"}
        if not messages:
            return {"ok": False, "error": "当前项目没有聊天记录可整理。"}
        recent = messages[-self.DISTILL_HISTORY_LIMIT:]
        dialogue = "\n".join(f"{item['role']}: {item['content']}" for item in recent)
        user_content = f"以下是当前 GDL 项目的聊天记录（最近 {len(recent)} 条）：\n\n{dialogue}"
        try:
            llm = self._build_distill_llm()
            resp = llm.generate(
                [
                    {"role": "system", "content": self.DISTILL_HISTORY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ]
            )
            instruction = (resp.content or "").strip()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to distill assistant history: {exc}"}
        if not instruction:
            return {"ok": False, "error": "Failed to distill assistant history: LLM returned an empty instruction."}
        return {"ok": True, "instruction": instruction, "message_count": len(recent)}

    def _build_distill_llm(self):
        """按 _build_generate_pipeline 同款配置构造 distill 用的 LLMAdapter。

        先建 pipeline 并灌 session 的 llm_model/api_key/api_base/assistant_settings，
        再经 pipeline._make_llm 拿适配器（与 generate 路径共用同一套配置解析）。
        """
        pipeline = self._new_pipeline()
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            # D6+D16：adapter 工厂同样同步 Fixed 模式 effort（会话生效值）
            pipeline.config.llm.reasoning_effort = effective_session_reasoning_effort(self.session)
            pipeline.config.llm.codex_routing_mode = (
                self.session.config.llm.effective_codex_routing_mode()
            )
        request = TaskRequest(
            user_input="",
            assistant_settings=self.session.assistant_settings,
        )
        return pipeline._make_llm(request)

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

    def _codex_modify_gate(self, body: dict[str, Any]) -> str | None:
        """保留 API 层扩展点；Codex 能力检查由动态桥接统一负责。"""
        return None

    def generate_with_assistant(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Generation message is empty."}

        # ST04：显式 skill 沉淀请求绕开 MODIFY/编译工具链，直接产出持久候选
        if self._is_explicit_skill_request(message):
            return self._explicit_skill_response(message)

        gate_error = self._codex_modify_gate(body)
        if gate_error is not None:
            return {"ok": False, "error": gate_error}

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

        continue_from = normalize_continue_from(body.get("continue_from"))
        pipeline, request = self._build_generate_pipeline(
            body, image_payload, on_event=on_event
        )
        result = pipeline.execute(request)
        result = self._merge_continue_from(result, continue_from)
        # skill 效果回写（GUI 侧通道，best-effort）：失败任务按注入 skill 计 fail_count
        self._safe_skill_outcome(result)
        # success=False 只在"无可交付物"时才视为硬失败；验证未过但有产出时
        # 照常交付（verification 报告会如实显示 FAIL），避免丢掉用户的生成结果
        if not result.success and result.project is None and not (result.plain_text or result.scripts):
            error = result.error or "Generation failed."
            if image_payload["image_b64"] or image_payload.get("images"):
                error = classify_vision_error(Exception(error))
            return {"ok": False, "error": error, "events": events}

        if result.project is not None:
            self.session.project = result.project
        self.session.project.save_to_disk()
        # 模式级 skill 提案（GUI 侧通道，best-effort；提炼失败不影响交付）
        proposal = self._safe_harvest(result, message)
        response: dict[str, Any] = {
            "ok": True,
            "assistant": self._generate_assistant_dict(
                result,
                instruction=message,
                continue_from=continue_from,
            ),
            "preview": preview_payload(self.session.project),
            "warnings": [],
            "events": events,
        }
        if proposal:
            response["skill_proposal"] = proposal
        return response

    def _generate_with_confirmation(self, body: dict[str, Any]) -> dict[str, Any]:
        """confirm_plan=True 的 MODIFY 请求：先做一次计划调用，返回 awaiting_confirmation。

        计划调用失败/JSON 不合法 → pipeline 已回落为直接执行（旧行为），
        本方法原样返回执行结果并带 plan_failed 标记，不卡死用户。
        """
        gate_error = self._codex_modify_gate(body)
        if gate_error is not None:
            return {"ok": False, "error": gate_error}
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before generating changes."}
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        continue_from = normalize_continue_from(body.get("continue_from"))
        pipeline, request = self._build_generate_pipeline(body, image_payload, on_event=on_event)
        result = pipeline.execute(request)
        result = self._merge_continue_from(result, continue_from)
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
        # skill 效果回写（GUI 侧通道，best-effort）
        self._safe_skill_outcome(result)
        if result.project is not None:
            self.session.project = result.project
        self.session.project.save_to_disk()
        # 模式级 skill 提案（best-effort；提炼失败不影响交付）
        instruction = str(body.get("message") or "").strip()
        proposal = self._safe_harvest(result, instruction)
        response: dict[str, Any] = {
            "ok": True,
            "assistant": self._generate_assistant_dict(
                result,
                instruction=instruction,
                continue_from=continue_from,
            ),
            "preview": preview_payload(self.session.project),
            "warnings": [],
            "events": events,
            "plan_failed": True,
        }
        if proposal:
            response["skill_proposal"] = proposal
        return response

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
            # 反馈信号采集（best-effort，不改判定）：用户拒绝计划 → plan_rejected
            plan = pending.get("plan") or {}
            intent_summary = str(plan.get("intent_summary") or "").strip()
            append_feedback(self.session.source_path, {
                "kind": "plan_rejected",
                "summary": intent_summary or "用户拒绝了修改计划",
                "detail": {"intent_summary": intent_summary},
            })
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

    # ── 模式级 skill 提案（GUI 侧通道，best-effort；不进 pipeline 默认路径）──

    def _safe_skill_outcome(self, result) -> None:
        """best-effort：任务结束时按注入 skill 回写 fail_count / last_failed。

        与 _safe_harvest 同落点，任何异常静默，绝不阻塞已完成的修改交付。
        """
        try:
            from openbrep.runtime.skill_harvest import record_skill_outcome

            record_skill_outcome(self.session, result)
        except Exception as exc:
            logger.warning("skill outcome record skipped (best-effort): %s", exc)

    def _safe_harvest(self, result, instruction: str) -> dict[str, Any] | None:
        """调用边界兜底：提炼任何异常都静默，绝不阻塞已完成的修改交付。"""
        try:
            return self._harvest_skill_proposal(result, instruction)
        except Exception as exc:
            logger.warning("skill harvest skipped (best-effort): %s", exc)
            return None

    def _harvest_skill_proposal(self, result, instruction: str) -> dict[str, Any] | None:
        """成功 TaskResult → 提炼模式级 skill 提案并存 session.pending_skill_proposal。

        返回提案（响应携带 skill_proposal 字段）；门禁不过/提炼失败/去重命中
        返回 None。任何异常静默，绝不阻塞已完成的修改交付。
        """
        from openbrep.runtime.skill_harvest import harvest_for_session

        return harvest_for_session(self.session, result, instruction)

    def confirm_skill_proposal(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/skill/confirm：审批 skill 提案/候选。

        带 proposal_id → 从持久候选 store 读取并审批（校验项目身份，draft →
        approved/rejected）；不带 → 保留原 session.pending_skill_proposal 行为。
        """
        try:
            return self._skill_proposal_service().confirm(body)
        except Exception as exc:
            logger.warning("skill proposal confirm failed: %s", exc)
            return {"ok": False, "error": f"Skill proposal confirm failed: {exc}"}

    # ── ST04：显式 skill 沉淀（assistant 文本路由与 REST 路由共用同一 service）──

    def _skill_proposal_service(self):
        from openbrep.workbench.skill_proposal_service import SkillProposalService

        return SkillProposalService(self.session)

    def propose_skill_candidate(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/skill/proposals 的 assistant 侧入口。"""
        try:
            return self._skill_proposal_service().propose(body)
        except Exception as exc:
            logger.warning("skill proposal failed: %s", exc)
            return {"ok": False, "code": "SKILL_PROPOSAL_FAILED", "error": f"skill 沉淀失败：{exc}"}

    def list_skill_proposals(self) -> dict[str, Any]:
        """GET /api/skill/proposals 的 assistant 侧入口。"""
        try:
            return self._skill_proposal_service().list_proposals()
        except Exception as exc:
            logger.warning("skill proposal list failed: %s", exc)
            return {"ok": False, "error": f"skill 候选列表读取失败：{exc}", "proposals": []}

    @staticmethod
    def is_explicit_skill_request(message: str) -> bool:
        """公开判定：显式"沉淀成 skill"请求（workbench_api 流式前置用）。"""
        from openbrep.skill_proposals import detect_explicit_skill_request

        return detect_explicit_skill_request(message)

    @staticmethod
    def _is_explicit_skill_request(message: str) -> bool:
        return WorkbenchAssistantService.is_explicit_skill_request(message)

    def generate_with_assistant_route(self, body: dict[str, Any]):
        """统一入口：显式沉淀同步处理（SSE 仍收 done），其余按 stream 分流。

        workbench_api.route 在锁内调用本方法，因此显式沉淀的候选写入发生在返回
        生成器之前——不会出现"锁已释放、生成器迭代期间项目已切换"的写入漂移。
        """
        message = str(body.get("message") or "").strip()
        if body.get("stream") and message and self.is_explicit_skill_request(message):
            result = self.generate_with_assistant({**body, "stream": False})

            def _explicit_done():
                yield {"type": "done", "data": result}

            return _explicit_done()
        if body.get("stream"):
            import threading

            return self.generate_with_assistant_stream(body, cancel_event=threading.Event())
        return self.generate_with_assistant(body)

    def _explicit_skill_response(self, message: str) -> dict[str, Any]:
        """显式沉淀的 assistant 载荷（ok/失败都带明确 code，不报 completed）。"""
        result = self.propose_skill_candidate({"instruction": message})
        if not result.get("ok"):
            error = str(result.get("error") or "skill 沉淀失败。")
            return {
                "ok": False,
                "code": result.get("code"),
                "error": error,
                "assistant": {"kind": "skill_proposal", "reply": error},
                "skill_proposal": None,
                "events": [],
            }
        proposal_id = result.get("proposal_id")
        name = result.get("name")
        path = f".openbrep/memory/skill-proposals/{proposal_id}.json"
        evidence = result.get("evidence") or {}
        evidence_note = (
            "证据完整" if evidence.get("evidence_complete") else "证据不完整（旧资料/未绑定 after，未核验）"
        )
        reply = (
            f"已生成待审 skill 候选「{name}」（{proposal_id}）。\n"
            f"候选路径：{path}\n"
            f"状态：draft（需你确认后才沉淀）。{evidence_note}。"
        )
        return {
            "ok": True,
            "assistant": {"kind": "skill_proposal", "reply": reply},
            "skill_proposal": result,
            "events": [],
        }

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

        # ST04：显式 skill 沉淀请求 → 直接产出候选，不启动 pipeline / 工具链
        if self._is_explicit_skill_request(message):
            result = self._explicit_skill_response(message)
            result.setdefault("events", [])
            yield {"type": "done", "data": result}
            return

        gate_error = self._codex_modify_gate(body)
        if gate_error is not None:
            yield {"type": "error", "data": {"error": gate_error}}
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

        continue_from = normalize_continue_from(body.get("continue_from"))

        def run_pipeline():
            try:
                pipeline, request = self._build_generate_pipeline(
                    body, image_payload, on_event=on_event, should_cancel=should_cancel
                )
                result = pipeline.execute(request)
                result = self._merge_continue_from(result, continue_from)
                if not result.success and result.project is None and not (result.plain_text or result.scripts):
                    error = result.error or "Generation failed."
                    if image_payload["image_b64"] or image_payload.get("images"):
                        error = classify_vision_error(Exception(error))
                    q.put({"type": "error", "data": {"error": error}})
                    return

                # skill 效果回写（GUI 侧通道，best-effort）
                self._safe_skill_outcome(result)

                if result.project is not None:
                    self.session.project = result.project
                self.session.project.save_to_disk()
                done_data: dict[str, Any] = {
                    "ok": True,
                    "assistant": self._generate_assistant_dict(
                        result,
                        instruction=message,
                        continue_from=continue_from,
                    ),
                    "preview": preview_payload(self.session.project),
                    "warnings": [],
                }
                # 模式级 skill 提案（best-effort；提炼失败不影响交付）
                proposal = self._safe_harvest(result, message)
                if proposal:
                    done_data["skill_proposal"] = proposal
                q.put({"type": "done", "data": done_data})
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
        pipeline = self._new_pipeline()
        intent = str(body.get("intent") or "MODIFY")
        epoch_at_start = getattr(self.session, "project_epoch", None)
        request = TaskRequest(
            user_input=str(body.get("message") or "").strip(),
            intent=intent,
            project=self.session.project,
            work_dir=str(self.session.source_path.parent),
            output_dir=str(self.session.source_path.parent / "output"),
            gsm_name=self.session.project.name,
            image_b64=image_payload["image_b64"],
            image_mime=image_payload["image_mime"],
            images=[
                ImageRef(token=str(img["token"]), path=img["path"], b64=str(img["b64"]), mime=str(img["mime"]))
                for img in image_payload.get("images") or []
            ],
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
            # ST03 F2：继续关联进入 TaskRequest → pipeline metadata（先于 finalize）
            continue_from=normalize_continue_from(body.get("continue_from")),
        )
        # D10：会话层 project epoch 守卫（Codex modify 桥接在长任务中拒绝
        # 项目切换后的后续 mutation；非 codex 路径不使用该字段）
        from openbrep.config import is_codex_qualified_model

        request.epoch_guard = (
            (lambda: self.session.project_epoch == epoch_at_start)
            if is_codex_qualified_model(self.session.llm_model)
            else None
        )
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            # D6+D16：Fixed 模式 effort 从会话生效值同步（CREATE/IMAGE 的 codex kwargs 读取它）
            pipeline.config.llm.reasoning_effort = effective_session_reasoning_effort(self.session)
            pipeline.config.llm.codex_routing_mode = (
                self.session.config.llm.effective_codex_routing_mode()
            )
            pipeline.config.agent.max_iterations = self.session.max_retries
            # D12：MODIFY 工具预算可配置化 — 与 max_iterations 同款同步模式
            # （只读 config.toml，设置页不暴露；0 = 各路径既有默认值）
            pipeline.config.agent.agent_loop_budget = (
                self.session.config.agent.agent_loop_budget or 0
            )
        # D3：codex 模型才注入共享 provider（非 codex 不拉起 app-server）
        from openbrep.config import is_codex_qualified_model

        if is_codex_qualified_model(self.session.llm_model):
            try:
                pipeline.codex_provider = self.session.settings_service._codex_provider()
            except Exception:  # noqa: BLE001 —— provider 不可用留给 LLMAdapter fail closed
                pipeline.codex_provider = None
        return pipeline, request
