"""
TaskPipeline — unified task execution pipeline for OpenBrep.

Phase 1: thin wrapper around GDLAgent.generate_only().
- No Streamlit dependencies
- Usable from CLI, tests, and future API server
- app.py continues to use GDLAgent directly for now (Strangler Fig)

Intent dispatch:
  CREATE  → _handle_gdl()     (inject affected scripts, standard prompt)
  MODIFY  → _handle_modify()  (inject ALL scripts, minimal-change prompt, static check, compile)
  DEBUG   → _handle_modify()  (same as MODIFY but framed as error analysis)
  REPAIR  → _handle_repair()  (compile/runtime repair with error-log context)
  IMAGE   → _handle_gdl()     (vision mode, inject all scripts)
  CHAT    → _handle_chat()
"""

from __future__ import annotations

import difflib
import logging
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from openbrep.explainer.chat_adapter import build_chat_explanation_reply
from openbrep.explainer.context_builder import (
    build_project_context,
    build_project_parameter_context,
    build_project_script_context,
    resolve_parameter_targets,
    resolve_script_target,
)
from openbrep.explainer.service import explain_parameter_context, explain_project_context, explain_script_context
from openbrep.compiler import CompileComparison, CompileResult, CompileSnapshot, HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.feedback import append_feedback
from openbrep.gdl_sanitizer import sanitize_llm_script_output, strip_md_fences
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.knowledge import KnowledgeBase
from openbrep.knowledge_selector import KnowledgeSelection, select_gdl_knowledge
from openbrep.learning import ErrorLearningStore, looks_like_error_report
from openbrep.llm import LLMAdapter
from openbrep.object_planner import plan_gdl_object
from openbrep.project_context import (
    ProjectContext,
    append_project_decision,
    build_project_context_prompt,
    load_project_memory,
    load_project_knowledge,
    load_project_skills,
    resolve_project_context,
)
from openbrep.skill_creator import SkillCreator
from openbrep.user_knowledge import load_user_knowledge
from openbrep.wiki_knowledge import WikiKnowledge
from openbrep.skills_loader import SkillsLoader
from openbrep.runtime.router import IntentRouter
from openbrep.runtime.tracer import Tracer
from openbrep.preflight import PreflightAnalyzer
from openbrep.revisions import create_revision, get_latest_revision_id, is_hsf_project_dir
from openbrep.vision.image_to_plan import analyze_reference_image, visual_structure_to_gdl_hint


# ── 多图摄取通道（Vision Harness S0，P5a）──────────────────
# ImageRef 契约见设计文档 §6：token 与用户文本中的 [图N] 引用对应；
# path 由后端读取后置 None（防泄露进 prompt）；b64/mime 为预处理后的字节；
# role 由 S1 分型推导（outline/pattern/material/auto，P5b）；sha256 为预处理
# 字节哈希（P5b 只算不存，为 P5d 存储/复用铺路）。


@dataclass
class ImageRef:
    token: str = ""                  # "图1" / "图2"，与用户文本中的 [图N] 引用对应
    path: Optional[str] = None       # 本地路径来源（后端读取后置 None，防泄露进 prompt）
    b64: str = ""                    # base64 图像字节（预处理后）
    mime: str = "image/png"          # MIME（image/png / image/jpeg / image/webp）
    role: str = "auto"               # outline | pattern | material | auto（S1 推导）
    sha256: str = ""                 # 预处理字节哈希（P5b 只算不存）


_GENERATION_ROLES = ("outline", "pattern", "auto")


def _generation_images(multi_images: list[ImageRef], config) -> list[dict]:
    """多图生成调用图片过滤（P5b，设计 D5/D9）。

    pass_raw_image=on → 只带 role ∈ {outline, pattern, auto} 的图（material 只参与提取）；
    off → 生成不带原图（只靠 hint）。旧单图字段（image_b64）不受此开关影响。
    """
    pass_raw = bool(getattr(getattr(config, "vision", None), "pass_raw_image", True))
    if not pass_raw:
        return []
    return [
        {"b64": img.b64, "mime": img.mime, "token": img.token}
        for img in multi_images
        if img.b64 and img.role in _GENERATION_ROLES
    ]


# ── Modify-specific skill instructions ───────────────────
# These are prepended to skills_text for MODIFY/DEBUG tasks.
# They ride in the ## TASK STRATEGY section of the system prompt.

_DEFAULT_MODIFY_SKILLS_PROMPT = """\
## 修改任务规则（必须遵守）
你正在修改一个已有的 GDL 对象。严格遵守以下规则：
1. 只修改需要修改的部分，不要重写整个脚本（除非整个脚本都需要变）
2. 保留原有的注释、代码风格和命名规范，不要"顺手优化"无关代码
3. 先用中文简要说明：做了什么修改、改了哪个文件、为什么
4. 如果修改了 3D 脚本中的参数引用，检查 paramlist.xml 是否需要同步修改
5. 如果新增了参数，必须同时输出更新后的 paramlist.xml
6. 不需要修改的文件不要输出
7. 用 [FILE: path] 格式输出每个改动文件的完整修改后内容
"""

def _load_prompt_body(relative_path: str, fallback: str) -> str:
    """Load a prompt file body, ignoring frontmatter and falling back safely."""
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / relative_path
    try:
        content = prompt_path.read_text(encoding="utf-8").strip()
    except Exception:
        return fallback
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3 :].strip()
    return content or fallback


_MODIFY_SKILLS_PROMPT = _load_prompt_body("tasks/modify_skills.md", _DEFAULT_MODIFY_SKILLS_PROMPT)


logger = logging.getLogger(__name__)


_GREETING_ONLY_PATTERNS = (
    r"^(你好|您好|hello|hi|hey|嗨|哈喽|bonjour|hola|ciao|こんにちは|안녕)[!！。.\s]*$",
)


def _is_greeting_only(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return any(re.search(pattern, raw, re.IGNORECASE) for pattern in _GREETING_ONLY_PATTERNS)


# ── Data Contracts ────────────────────────────────────────

@dataclass
class TaskRequest:
    """Unified input for a pipeline task."""

    user_input: str
    intent: Optional[str] = None           # pre-set if known; router fills it otherwise
    project: Optional[HSFProject] = None   # existing project (modify / debug)
    work_dir: str = "./workdir"
    output_dir: str = "./output"
    gsm_name: Optional[str] = None         # output .gsm filename stem
    image_path: Optional[str] = None       # path to image for vision tasks
    history: Optional[list[dict]] = None   # recent conversation history
    syntax_report: str = ""                # debug syntax checker output
    last_code_context: Optional[str] = None
    error_log: str = ""                    # structured compile/runtime error text for repair
    should_cancel: Optional[Callable[[], bool]] = None
    image_b64: Optional[str] = None
    image_mime: str = "image/png"
    images: list[ImageRef] = field(default_factory=list)  # 新：有序多图（P5a），仅非空时走多图通道
    assistant_settings: str = ""           # injected into GDL system prompt
    on_event: Optional[Callable] = None    # progress callback (event_type, data) -> None
    compare_compile: str = "off"           # off / mock / real
    agent_loop: Optional[bool] = None      # None = 按 intent 默认策略；True/False = 显式开关
    agent_loop_budget: int = 0             # agent loop 工具调用预算，0 = 用默认值
    agent_loop_plan: bool = False          # agent loop 是否先输出可审查计划（流式/SSE 默认开）
    confirm_plan: bool = False             # 计划确认门：GUI MODIFY 置 True（先出计划，确认后才执行）
    confirmed_plan: Optional[dict] = None  # 已确认的计划（/api/modify/confirm approve 后注入 agent loop）
    confirm_extraction: bool = False       # 提取确认门（P5d-2）：GUI CREATE 带图置 True（提取后早退等确认）
    confirmed_extractions: Optional[list[dict]] = None  # 用户确认/编辑后的提取 dict 列表（跳过 harness 重建 plans）


@dataclass
class TaskResult:
    """Output from a pipeline task."""

    success: bool
    intent: str = ""
    scripts: dict = field(default_factory=dict)   # {file_path: content}
    plain_text: str = ""                           # LLM analysis / explanation
    project: Optional[HSFProject] = None
    compile_result: Optional[CompileResult] = None
    trace_path: Optional[str] = None
    error: Optional[str] = None
    lint_summary: str = ""
    object_plan: dict = field(default_factory=dict)
    revision_warnings: list[str] = field(default_factory=list)
    compile_comparison: Optional[CompileComparison] = None
    verification: Optional[dict] = None          # unified VerificationReport (Phase 3/4)
    semantic_repair: dict = field(default_factory=dict)  # {"attempted": n, "accepted": m}（S1 可观测性）
    metadata: dict = field(default_factory=dict)  # 结构化元数据（如 param_modify 的 plan 与校验结果）


@dataclass(frozen=True)
class AssembledContext:
    """Prompt context assembled for one pipeline task."""

    project_context: Optional[ProjectContext]
    knowledge_selection: KnowledgeSelection
    skills_text: str

    @property
    def generation_context(self) -> str:
        return self.knowledge_selection.generation_context

    @property
    def planner_context(self) -> str:
        return self.knowledge_selection.planner_context

    @property
    def source_ids(self) -> list[str]:
        return self.knowledge_selection.source_ids


@dataclass
class GenerationResultPlan:
    """UI-facing plan for how to present a generation result."""

    has_changes: bool
    changed_files: list[str] = field(default_factory=list)
    label: str = ""
    mode: str = "plain_text_only"
    code_blocks: list[dict[str, str]] = field(default_factory=list)
    reply_prefix: str = ""


# ── Pipeline ──────────────────────────────────────────────

class TaskPipeline:
    """
    Unified execution pipeline.

    Wires together: router → LLM → GDLAgent → tracer.

    Usage::

        pipeline = TaskPipeline()
        result = pipeline.execute(TaskRequest(user_input="做一个书架"))
        print(result.scripts)
    """

    def __init__(
        self,
        config: Optional[GDLAgentConfig] = None,
        config_path: Optional[str] = None,
        trace_dir: str = "./traces",
        include_learned_skills: bool = True,
        codex_provider: Any = None,
    ):
        self.config = config or GDLAgentConfig.load(config_path)
        self.router = IntentRouter()
        self.tracer = Tracer(trace_dir=trace_dir)
        # D3：Codex CHAT/EXPLAIN 的 provider（workbench 注入 session 共享实例；
        # 未注入时 LLMAdapter 走进程共享默认注册表）。非 codex 模型从不触碰。
        self.codex_provider = codex_provider
        # benchmark 传 False：错误学习记忆是累积态，会让 prompt 随运行历史漂移，
        # 破坏黄金语料可复现性；生产默认 True，行为不变
        self.include_learned_skills = include_learned_skills
        # Cached after first load (knowledge can be large)
        self._knowledge_text: Optional[str] = None
        self._skills_loader: Optional[SkillsLoader] = None
        self._skill_creator: Optional[SkillCreator] = None

    def _resolve_skills_dir(self) -> Path:
        project_root = Path(__file__).parent.parent.parent
        return project_root / "skills"

    # ── Public API ────────────────────────────────────────

    def execute(self, request: TaskRequest) -> TaskResult:
        """
        Execute a task end-to-end.

        Steps:
          1. Classify intent (if not pre-set)
          2. Dispatch to CHAT or GDL handler
          3. Record trace
          4. Return TaskResult
        """
        # 1. Classify
        if not request.intent:
            request.intent = self.router.classify(
                request.user_input,
                has_project=request.project is not None,
                has_image=bool(request.image_path or request.image_b64 or request.images),
            )

        # 2. Apply agent-loop default policy
        # MODIFY/DEBUG/REPAIR 默认启用预算制 agent loop；显式 False 可回退旧路径。
        if request.agent_loop is None and request.intent in ("MODIFY", "DEBUG", "REPAIR"):
            request.agent_loop = True

        # 注入侧通道清零（不调用 get_for_task 的任务不会残留上一任务的注入名单；
        # get_for_task 每次调用也会在开头重置，这里是双保险）
        if self._skills_loader is not None:
            self._skills_loader.last_injected = []

        # 2. Execute
        try:
            if request.intent == "CHAT":
                result = self._handle_chat(request)
            elif request.intent in ("MODIFY", "DEBUG", "REPAIR") and request.agent_loop:
                # 默认路径：预算制 agent loop（LLM 通过工具调用自主迭代）
                # 先试确定性微修改（零 token）、再试结构化 skill 模板、再试参数级
                # 修改 DSL（一次 LLM 意图解析 + 确定性应用），都不命中才进 agent loop
                micro_result = self._try_micro_modify(request)
                if micro_result is not None:
                    result = micro_result
                else:
                    skill_ops_result = self._try_skill_ops(request)
                    if skill_ops_result is not None:
                        result = skill_ops_result
                    else:
                        dsl_result = self._try_param_modify(request)
                        if dsl_result is not None:
                            result = dsl_result
                        else:
                            result = self._handle_modify_agent_loop(request)
            elif request.intent == "REPAIR":
                result = self._handle_repair(request)
            elif request.intent in ("MODIFY", "DEBUG"):
                result = self._handle_modify(request)
            else:
                result = self._handle_gdl(request)
        except Exception as exc:
            logger.exception("Pipeline execution failed: %s", exc)
            result = TaskResult(
                success=False,
                intent=request.intent or "",
                error=str(exc),
            )

        # 3. 注入名单透出（只加 metadata、不改任何 prompt）：把本次实际注入的
        # skill 名写入 TaskResult.metadata["injected_skills"]（无注入记 []）。
        # 只在内存旁路记录；fail_count 回写由 GUI 侧通道完成，绝不经此路径。
        try:
            injected: list[str] = []
            if self._skills_loader is not None:
                injected = list(self._skills_loader.last_injected or [])
            merged = dict(result.metadata or {})
            merged["injected_skills"] = injected
            result.metadata = merged
        except Exception:
            pass

        # 4. Trace (never blocks execution)
        try:
            trace_path = self.tracer.record(request, result)
            result.trace_path = str(trace_path)
        except Exception:
            pass

        return result

    # ── Handlers ─────────────────────────────────────────

    def _handle_chat(self, request: TaskRequest) -> TaskResult:
        """Simple conversational reply — no GDL code output."""
        is_greeting = _is_greeting_only(request.user_input)

        # ── Skill creator: active session takes priority ──
        if self._skill_creator is not None:
            reply = self._skill_creator.process_turn(request.user_input)
            # Reset session if skill was just generated
            if self._skill_creator._ready_to_generate:
                self._skills_loader = None
                self._skill_creator = None
            return TaskResult(success=True, intent="CHAT", plain_text=reply)

        # ── Wiki teaching / GDL knowledge answers ──
        # Explicit GDL command/syntax questions may include code examples. Keep
        # them in chat before project explanation or modify/compile workflows.
        if not is_greeting and self._has_gdl_keyword(request.user_input):
            wiki_result = self._handle_wiki_knowledge(request)
            if wiki_result is not None:
                return wiki_result

        # ── Skill intent detection ──
        if not is_greeting:
            creator = self._get_skill_creator(request)
            skill_intent = creator.classify_intent(request.user_input)
            if skill_intent == "CREATE_SKILL":
                reply = creator.start_conversation(request.user_input)
                self._skill_creator = creator
                return TaskResult(success=True, intent="CHAT", plain_text=reply)
            elif skill_intent == "LIST_SKILLS":
                reply = creator.list_skills()
                return TaskResult(success=True, intent="CHAT", plain_text=reply)

        # ── D3：Codex 模型 CHAT/EXPLAIN（ephemeral thread + 临时只读 cwd +
        # approval never，见 openbrep/codex/turn.py）。项目内容只经 prompt 注入，
        # 绝不修改 HSF / 创建 revision；无项目时不创建任何目录。非 codex 模型
        # 走下方原有路径，逐字节不变。
        if self._is_codex_model_selected():
            return self._handle_codex_chat(request)

        # ── Existing: project context explanation ──
        if request.project is not None and not is_greeting:
            script_target = resolve_script_target(request.user_input)
            if script_target is not None:
                script_context = build_project_script_context(request.project, script_target)
                if script_context is not None:
                    explanation = explain_script_context(script_context)
                    reply = build_chat_explanation_reply(explanation, user_input=request.user_input)
                    return TaskResult(
                        success=True,
                        intent="CHAT",
                        plain_text=reply,
                    )

            parameter_targets = resolve_parameter_targets(request.project, request.user_input)
            if parameter_targets:
                explanations = []
                for param_name in parameter_targets:
                    param_context = build_project_parameter_context(request.project, param_name)
                    if param_context is None:
                        continue
                    explanations.append(
                        build_chat_explanation_reply(
                            explain_parameter_context(param_context),
                            user_input=request.user_input,
                        )
                    )
                if explanations:
                    return TaskResult(
                        success=True,
                        intent="CHAT",
                        plain_text="\n\n".join(explanations),
                    )

            explanation = explain_project_context(build_project_context(request.project))
            reply = build_chat_explanation_reply(explanation, user_input=request.user_input)
            return TaskResult(
                success=True,
                intent="CHAT",
                plain_text=reply,
            )

        # Check if this is a GDL knowledge question → answer from wiki
        wiki_result = self._handle_wiki_knowledge(request)
        if wiki_result is not None:
            return wiki_result

        llm = self._make_llm(request)
        system_content = (
            "你是 openbrep 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
            "【重要约束】绝对禁止在回复中输出任何 GDL 代码、代码块或脚本片段。"
            "如果用户想创建或修改 GDL 对象，告诉他直接描述需求，AI 会自动生成。\n"
            "当用户是问候语时，先做一句简短自我介绍，再问“我可以帮你做什么？”。"
            "回复语言必须与用户输入语言一致（中文就中文，英文就英文）。"
            "回复简洁，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
        )
        system_content = _build_assistant_settings_prompt(request.assistant_settings) + system_content
        history = _trim_history(request.history, limit=6)
        messages = [{"role": "system", "content": system_content}]
        messages.extend({"role": item.get("role", "user"), "content": item.get("content", "")} for item in history)
        messages.append({"role": "user", "content": request.user_input})
        codex_kwargs = {}
        if self._is_codex_model_selected():
            codex_kwargs = {
                "codex_intent": "CHAT",
                "codex_should_cancel": request.should_cancel,
                "codex_on_event": request.on_event,
            }
        try:
            resp = llm.generate(messages, **codex_kwargs)
            return TaskResult(
                success=True,
                intent="CHAT",
                plain_text=resp.content,
            )
        except Exception as exc:
            return TaskResult(success=False, intent="CHAT", error=str(exc))

    # ── D3：Codex 模型 CHAT/EXPLAIN ─────────────────────────

    def _is_codex_model_selected(self) -> bool:
        """当前 pipeline 配置选中的模型是否走 ChatGPT Codex（openai-codex）订阅路线。"""
        try:
            return bool(self.config.llm._is_codex_app_server_model())
        except Exception:  # noqa: BLE001 —— 配置异常按非 codex 处理（不阻塞现有路径）
            return False

    def _handle_codex_chat(self, request: TaskRequest) -> TaskResult:
        """Codex 模型 CHAT/EXPLAIN（D3）：ephemeral thread + 临时只读 cwd +
        approval never + 无工具面，见 openbrep/codex/turn.py。

        - CHAT（无项目）：只回复，不创建任何目录/文件。
        - EXPLAIN（有项目）：项目只读摘要经 prompt 注入，绝不修改 HSF、
          绝不创建 revision。
        - 系统提示与现有 chat 路径一致（assistant_settings 前置）；错误全部
          映射为稳定文案（上游原文零回显）。
        """
        llm = self._make_llm(request)
        system_content = (
            "你是 openbrep 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
            "【重要约束】绝对禁止在回复中输出任何 GDL 代码、代码块或脚本片段。"
            "如果用户想创建或修改 GDL 对象，告诉他直接描述需求，AI 会自动生成。\n"
            "当用户是问候语时，先做一句简短自我介绍，再问“我可以帮你做什么？”。"
            "回复语言必须与用户输入语言一致（中文就中文，英文就英文）。"
            "回复简洁，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
        )
        if request.project is not None:
            # EXPLAIN：只读项目摘要（脚本只取前几行，参数/构件名完整）
            system_content += "\n\n" + _build_chat_project_context(request.project)
        system_content = _build_assistant_settings_prompt(request.assistant_settings) + system_content
        history = _trim_history(request.history, limit=6)
        messages = [{"role": "system", "content": system_content}]
        messages.extend(
            {"role": item.get("role", "user"), "content": item.get("content", "")}
            for item in history
        )
        messages.append({"role": "user", "content": request.user_input})
        try:
            resp = llm.generate(
                messages,
                codex_intent="CHAT",
                codex_should_cancel=request.should_cancel,
                codex_on_event=request.on_event,
            )
            return TaskResult(success=True, intent="CHAT", plain_text=resp.content)
        except Exception as exc:
            # D3：错误已由 LLMAdapter 映射为稳定文案（error_response / turn 层
            # 稳定文案）；此处只兜底，绝不把上游原文透传给用户。
            return TaskResult(success=False, intent="CHAT", error=str(exc))

    def _handle_gdl(self, request: TaskRequest) -> TaskResult:
        """GDL generation / modification via GDLAgent.generate_only()."""
        llm = self._make_llm(request)
        compiler = self._make_compiler()

        # D4：Codex 文本 CREATE——Codex 只负责生成 final text（[FILE:] 协议），
        # [FILE:] 解析 / HSFProject 落盘 / 命名 / 编译 / 静态检查 / 语义验证 /
        # 修复 / delivery gate 全部由 OpenBrep 负责（见 llm.py codex_intent="CREATE"
        # 分派与 turn 层临时只读 cwd 隔离）。codex kwargs 只注入文本生成类意图
        # （CREATE/IMAGE 无图路径）；MODIFY/DEBUG 不注入 → llm.py 保持 fail closed。
        codex_kwargs: dict = {}
        if self._is_codex_model_selected() and request.intent in ("CREATE", "IMAGE"):
            codex_kwargs = {
                "codex_intent": "CREATE",
                "codex_should_cancel": request.should_cancel,
                "codex_on_event": request.on_event,
            }

        # Ensure project exists
        project = request.project
        if project is None:
            gsm_name = request.gsm_name or "untitled"
            project = HSFProject.create_new(
                gsm_name,
                work_dir=request.work_dir,
            )
        request.project = project
        assembled_context = self._assemble_context(request, project)
        knowledge = assembled_context.generation_context
        skills_text = assembled_context.skills_text

        # Load image if provided
        image_b64: Optional[str] = request.image_b64
        image_mime = request.image_mime or "image/png"
        if request.image_path and not image_b64:
            import base64
            img_path = Path(request.image_path)
            if img_path.exists():
                image_b64 = base64.b64encode(img_path.read_bytes()).decode()
                if img_path.suffix.lower() in (".jpg", ".jpeg"):
                    image_mime = "image/jpeg"

        # ── 多图通道（P5a）：仅当 request.images 非空时生效的新路径 ────────
        # 单图旧字段（image_b64 / image_path）存在时完全走旧路径，不经过这里。
        multi_images: list[ImageRef] = []
        if request.images and not image_b64:
            from openbrep.vision.multi_image import resolve_and_preprocess

            multi_images = resolve_and_preprocess(request.images)

        on_event = request.on_event or (lambda *_: None)
        debug_mode = request.intent == "DEBUG"
        # P5d-1：vision 提取透出（多图分支填充；无图/单图旧路径保持空列表）
        vision_extractions: list[dict] = []

        # ── Phase 1 Vision Pre-analysis ──────────────────────────────────────
        # 有图 + 生成类意图（CREATE / IMAGE）→ 先结构化再生成
        # MODIFY / DEBUG 有图时直接传图作上下文，不跑结构化分析
        enriched_instruction = request.user_input
        if image_b64 and request.intent in ("CREATE", "IMAGE"):
            try:
                on_event("status", {"message": "正在分析参考图结构…"})
                vs = analyze_reference_image(image_b64, image_mime, request.user_input, llm)
                gdl_hint = visual_structure_to_gdl_hint(vs)
                enriched_instruction = f"{request.user_input}\n\n{gdl_hint}"
                on_event("vision_analysis_done", {"component_type": vs.component_type})
                logger.info("Vision pre-analysis done: %s", vs.component_type)
            except Exception as exc:
                logger.warning("Vision pre-analysis failed, falling back to direct vision: %s", exc)
                # fallback: 原始 instruction + image，行为与 Phase 1 之前一致
        elif multi_images and request.intent in ("CREATE", "IMAGE"):
            # 多图：Vision Harness（P5b）——S1 分型 + S2 定向提取（schema 驱动）+ S4 合成。
            # generic schema 平移现有 analyze_reference_image（原函数原 prompt），
            # 各图 hint 以 【图N】 前缀标注后拼入 enriched_instruction（与 P5a 逐字节一致）。
            from openbrep.vision.harness import run as vision_harness_run
            from openbrep.vision.extraction_store import plan_to_dict

            # P5d-2 提取确认门：confirmed_extractions 非空 = 用户确认后的重发。
            # 跳过 harness（零 vision 重调），从确认的 dict 重建 ModelingPlan
            # （用户编辑值经 from_dict → to_hint 自然生效）；空则走正常 harness。
            confirmed = request.confirmed_extractions or []
            try:
                if confirmed:
                    from openbrep.vision.modeling_plan import ModelingPlan

                    on_event("status", {"message": "正在按已确认的读图结果生成…"})
                    plans = [ModelingPlan.from_dict(entry) for entry in confirmed]
                else:
                    on_event("status", {"message": f"正在分析 {len(multi_images)} 张参考图…"})
                    plans = vision_harness_run(
                        multi_images,
                        request.intent,
                        request.user_input,
                        llm,
                        on_event=on_event,
                        critic_pass=self.config.vision.critic_pass,
                    )
                # P5d-1：plans 序列化进 metadata（设计 D7 存储 + 前端只读卡片数据源）。
                # 每图一条：schema/fields/confidence/corrections/降级标记 + sha256；
                # 无字节或分析失败的图记 {token, skipped: true}。字段形状与
                # vision.extraction_store.plan_to_dict 同构（事件 payload 同源）。
                for idx, (plan, img) in enumerate(zip(plans, multi_images), start=1):
                    token = img.token or f"图{idx}"
                    if plan is None:
                        vision_extractions.append({"token": token, "skipped": True})
                        continue
                    entry = plan_to_dict(plan)
                    entry["token"] = token
                    vision_extractions.append(entry)
                hint_parts: list[str] = []
                for idx, plan in enumerate(plans, start=1):
                    if plan is None:
                        logger.warning("multi_image: skip image %s (no plan after analysis)", multi_images[idx - 1].token or idx)
                        continue
                    hint_parts.append(f"【图{idx}】\n{plan.to_hint()}")
                if hint_parts:
                    enriched_instruction = f"{request.user_input}\n\n" + "\n\n".join(hint_parts)

                # P5d-2 提取确认门：confirm_extraction=True 且无已确认提取时，
                # harness 提取完成后**早退**——不进 plan_gdl_object / 生成 / 编译，
                # 把提取结果交还前端等用户确认/编辑（产品心脏：模型读错，生成前拦住）。
                # 全部 skipped（无字节图）→ 无提取可确认，照旧流程降级继续。
                if request.confirm_extraction and not confirmed:
                    non_skipped = [e for e in vision_extractions if not e.get("skipped")]
                    if non_skipped:
                        return TaskResult(
                            success=True,
                            intent=request.intent or "CREATE",
                            project=project,
                            metadata={
                                "awaiting_extraction_confirmation": True,
                                "vision_extractions": vision_extractions,
                            },
                        )
            except Exception as exc:
                logger.warning("Vision harness failed, falling back to direct vision: %s", exc)
        # ─────────────────────────────────────────────────────────────────────

        object_plan = None
        if request.intent in ("CREATE", "IMAGE"):
            on_event("status", {"message": "正在规划 GDL 对象结构…"})
            object_plan = plan_gdl_object(
                llm,
                instruction=enriched_instruction,
                knowledge=assembled_context.planner_context,
                skills=skills_text,
                llm_kwargs=codex_kwargs or None,
            )
            object_plan = replace(
                object_plan,
                knowledge_sources=_merge_list_values(
                    object_plan.knowledge_sources,
                    assembled_context.source_ids,
                ),
            )
            enriched_instruction = (
                f"{enriched_instruction}\n\n"
                f"{object_plan.to_prompt()}\n\n"
                "请严格按上述规划生成可继续工程化修改的 HSF/GDL 源码。"
            )
            on_event("object_plan_done", {"object_type": object_plan.object_type})

        # ── 图谱注入（阶段2）：两层叠加，均有异常保护，失败静默降级 ──────────
        # 层1：API 白名单（全量，来自 gdl_keywords.py，每次 CREATE 都注入）
        #      解决"幻觉命令名"问题——LLM 不可能使用列表外的命令
        # 层2：BIM 概念约束（仅当意图命中图谱别名时）
        #      提供命中概念的 init_pattern 和必需 API 示例
        _graph_constraint_injected = False
        try:
            from openbrep.gdl_keywords import (
                GEOMETRY_COMMANDS, TRANSFORM_COMMANDS, ATTRIBUTE_COMMANDS,
                TWO_D_COMMANDS, MISC_COMMANDS, CONTROL_FLOW, PARAMETER_COMMANDS,
                GROUP_COMMANDS, LOW_LEVEL_BODY_COMMANDS, BUILTIN_FUNCTIONS,
                SYSTEM_IDENTIFIERS,
            )
            # 按类别组织，让 LLM 更容易理解结构
            _whitelist_sections = [
                ("几何命令", sorted(GEOMETRY_COMMANDS | LOW_LEVEL_BODY_COMMANDS)),
                ("坐标变换（入栈/出栈必须配平）", sorted(TRANSFORM_COMMANDS)),
                ("属性设置", sorted(ATTRIBUTE_COMMANDS | PARAMETER_COMMANDS)),
                ("2D 命令", sorted(TWO_D_COMMANDS)),
                ("控制流", sorted(CONTROL_FLOW)),
                ("分组操作", sorted(GROUP_COMMANDS)),
                ("杂项", sorted(MISC_COMMANDS)),
                ("内置函数", sorted(BUILTIN_FUNCTIONS)),
            ]
            _wl_lines = ["【GDL API 合法命令白名单】只能使用以下 GDL 命令，禁止自造不存在的命令名："]
            for _section_name, _cmds in _whitelist_sections:
                _wl_lines.append(f"{_section_name}: {', '.join(_cmds)}")
            # 系统变量白名单：防止 LLM 自造 GLOB_/SYMB_ 前缀变量
            _wl_lines.append(
                "【合法全局变量白名单】除以下系统变量外，禁止自造 GLOB_ 或 SYMB_ 前缀的变量：\n"
                + ", ".join(sorted(SYSTEM_IDENTIFIERS))
            )
            # 高复杂度命令参数格式备忘（最易产生幻觉参数）
            _wl_lines.append(
                "【高复杂度命令参数格式】严格按以下格式使用，顶点数 n 必须与后续坐标组数一致：\n"
                "  PRISM_ n, h, x1, y1, b1, x2, y2, b2, ...（b=曲率，每顶点一个）\n"
                "  BPRISM_ n, h, x1, y1, b1, x2, y2, b2, ...\n"
                "  EXTRUDE_ n, h, mat_top, mat_side, mat_bot, mask, x1, y1, b1, ...\n"
                "  REVOLVE_ n, phi, mat_top, mat_side, mat_bot, mask, x1, y1, b1, ...\n"
                "  RULED_ n, m, mat_top, mat_side, mat_bot, mask, x1, y1, z1, ...\n"
                "  MESH n, m, mask, x11, y11, z11, x12, y12, z12, ...\n"
                "  PGON_ n, mat_id, mask, x1, y1, z1, ...\n"
                "  POLY_ n, mask, x1, y1, b1, ...\n"
                "  TUBE_ n_path, n_sect, booleans, closed, mat_id, p1x, p1y, p1z, ..."
            )
            enriched_instruction = enriched_instruction + "\n\n" + "\n".join(_wl_lines)
            _graph_constraint_injected = True
        except Exception as _wl_exc:
            logger.debug("API whitelist injection skipped: %s", _wl_exc)

        _graph_constraint = ""
        try:
            from openbrep.knowledge_graph import get_graph_manager
            _graph_mgr = get_graph_manager()
            _graph_constraint = _graph_mgr.build_constraint_prompt(enriched_instruction, log_miss=True)
            if _graph_constraint:
                enriched_instruction = f"{enriched_instruction}\n\n{_graph_constraint}"
                on_event("status", {"message": "📐 图谱概念约束已注入"})
                logger.info("[graph] concept constraint injected for intent: %s", request.user_input[:60])
        except Exception as _graph_exc:
            logger.debug("Graph concept constraint injection skipped: %s", _graph_exc)

        # 无概念命中时注入通用 3D/2D 隔离约束，防止 2D 专属命令误入 3D 脚本
        if not _graph_constraint:
            enriched_instruction = (
                enriched_instruction + "\n\n"
                "【领域约束】未匹配特定 BIM 概念，请严格遵循 GDL 语法规范，"
                "禁止在 3D 脚本中使用 2D 专属命令（如 LINE2、RECT2、CIRCLE2、"
                "ARC2、HOTSPOT2、POLY2、PROJECT2）。"
            )
        # ─────────────────────────────────────────────────────────────────────

        agent = GDLAgent(
            llm=llm,
            compiler=compiler,
            on_event=on_event,
            assistant_settings=request.assistant_settings,
            should_cancel=request.should_cancel,
            llm_kwargs=codex_kwargs or None,
        )

        changes, plain_text = agent.generate_only(
            instruction=enriched_instruction,
            project=project,
            knowledge=knowledge,
            skills=skills_text,
            include_all_scripts=debug_mode,
            last_code_context=request.last_code_context,
            syntax_report=request.syntax_report,
            history=request.history,
            image_b64=image_b64,
            image_mime=image_mime,
            # 多图通道：仅 images 非空时生效（生成调用改用多图 content 数组）。
            # P5b 角色过滤（设计 D5/D9）：pass_raw_image=on 时只带
            # role ∈ {outline, pattern, auto} 的图（material 只参与提取）；
            # off 时生成不带原图（只靠 ModelingPlan hint）。单图旧路径不受影响。
            images=_generation_images(multi_images, self.config),
        )

        # Strip markdown fences the LLM sometimes leaks into scripts
        cleaned = {k: sanitize_llm_script_output(v, k) for k, v in changes.items()} if changes else {}
        cleaned, lint_summary = _run_gdl_linter(cleaned, on_event=on_event)

        # ── P8 CREATE 零产出守卫：首轮解析零 [FILE:] → 重试一次；重试仍零 → 硬失败 ──
        # 事故回归：CREATE 零产出（模型只输出规划+提问）时旧代码静默交付 create_new
        # 占位项目（BLOCK A,B,ZZYZX），验证报告对占位脚本空转全绿。
        # 重试仍零产出 → project=None，service 走既有"只有无产出才算硬失败"路径：
        # 占位项目不落盘、不挂载、不跑空转验证。
        # 回放安全：重试只在首轮零产出时触发；黄金语料 CREATE 全部首轮有产出 →
        # 调用序列不变 → 回放零 miss。
        if not cleaned and request.intent in ("CREATE", "IMAGE"):
            on_event("status", {"message": "⚠️ 模型首轮未输出 [FILE:] 代码块，正在重试…"})
            logger.warning(
                "[create-zero-output] intent=%s first round produced no [FILE:] blocks; retrying once",
                request.intent,
            )
            retry_instruction = (
                f"{enriched_instruction}\n\n"
                "你上一次回复没有包含任何 [FILE:] 代码块。"
                "不要提问、不要只输出计划，直接输出完整代码文件。"
            )
            retry_changes, retry_plain = agent.generate_only(
                instruction=retry_instruction,
                project=project,
                knowledge=knowledge,
                skills=skills_text,
                include_all_scripts=debug_mode,
                last_code_context=request.last_code_context,
                syntax_report=request.syntax_report,
                history=request.history,
                image_b64=image_b64,
                image_mime=image_mime,
                images=_generation_images(multi_images, self.config),
            )
            retry_cleaned = (
                {k: sanitize_llm_script_output(v, k) for k, v in retry_changes.items()}
                if retry_changes else {}
            )
            retry_cleaned, retry_lint = _run_gdl_linter(retry_cleaned, on_event=on_event)
            if retry_cleaned:
                cleaned = retry_cleaned
                plain_text = retry_plain
                if retry_lint:
                    lint_summary = "\n\n".join(
                        p for p in [lint_summary, retry_lint] if p
                    )
                on_event("status", {"message": "✅ 重试后模型已输出 [FILE:] 代码块"})
                logger.info("[create-zero-output] retry round produced code; continuing")
            else:
                model_text = retry_plain or plain_text
                on_event("status", {"message": "❌ 重试后仍无代码产出，任务以硬失败结束"})
                logger.warning(
                    "[create-zero-output] retry round also produced no [FILE:] blocks; hard fail"
                )
                return TaskResult(
                    success=False,
                    intent=request.intent or "CREATE",
                    plain_text=(
                        f"{model_text}\n\n"
                        "⚠️ 模型未产出代码（可能在提问或规划）。"
                        "请检查助手设置中是否有「先规划后生成」类指令，或补充说明后重发。"
                    ),
                    project=None,
                    error="模型未产出代码（两轮均无 [FILE:] 代码块）",
                )

        # Apply changes to the project in-place
        if cleaned:
            agent._apply_changes(project, cleaned)

        # ── Static check: catch undefined_var / forward_decl before returning ──
        # Only trigger repair for these two checks (uninitialized variable errors).
        # stack_imbalance / block_mismatch are left for the user to fix manually.
        from openbrep.static_checker import StaticChecker
        static_result = StaticChecker().check(project)
        undef_errors = [
            e for e in static_result.errors
            if e.check_type in ("undefined_var", "forward_decl")
        ]
        if undef_errors:
            error_detail = "\n".join(f"  - [{e.file}] {e.detail}" for e in undef_errors)
            on_event("status", {"message": f"🔍 发现 {len(undef_errors)} 个变量问题，自动修复中…"})
            logger.info("Static check found %d undefined/forward-decl errors; triggering repair", len(undef_errors))
            repair_instruction = (
                f"{enriched_instruction}\n\n"
                f"生成后静态检查发现以下变量问题，请修正脚本（只修这些问题，不改其他）：\n"
                f"{error_detail}"
            )
            try:
                repair_changes, _repair_plain = agent.generate_only(
                    instruction=repair_instruction,
                    project=project,
                    knowledge=knowledge,
                    skills=skills_text,
                    include_all_scripts=True,
                    history=request.history,
                    # 不重传图片，repair 只需文字上下文
                )
                repair_cleaned = (
                    {k: sanitize_llm_script_output(v, k) for k, v in repair_changes.items()}
                    if repair_changes else {}
                )
                repair_cleaned, repair_lint_summary = _run_gdl_linter(repair_cleaned, on_event=on_event)
                if repair_cleaned:
                    agent._apply_changes(project, repair_cleaned)
                    cleaned.update(repair_cleaned)
                if repair_lint_summary:
                    lint_summary = "\n\n".join(part for part in [lint_summary, repair_lint_summary] if part)
            except Exception as exc:
                logger.warning("Static-check repair failed: %s", exc)
        # ─────────────────────────────────────────────────────────────────────

        # ── CREATE 路径：编译验证 + 多轮自愈闭环 ────────────────────────────
        # 仅当真实编译器（LP_XMLConverter）已配置时执行；
        # 未配置时明确标记 SKIPPED_NO_COMPILER，不再返回含糊的 NOT_RUN。
        compile_result: Optional[CompileResult] = None
        auto_repair_info = ""
        _graph_powered_repair = False
        _MAX_CREATE_REPAIR = 3
        _create_repair_rounds = 0

        if not self.config.compiler.path:
            compile_not_run_reason = (
                "SKIPPED_NO_COMPILER（未配置 LP_XMLConverter，仅依赖静态检查）"
            )
        else:
            compile_not_run_reason = ""
            try:
                import tempfile as _tempfile
                gsm_name = request.gsm_name or project.name
                out_dir_str = request.output_dir or ""
                if out_dir_str:
                    create_out_dir = Path(out_dir_str)
                else:
                    create_out_dir = Path(_tempfile.mkdtemp(prefix="obr_create_"))
                create_out_dir.mkdir(parents=True, exist_ok=True)
                create_gsm_path = str(create_out_dir / f"{gsm_name}.gsm")

                hsf_dir_c = project.save_to_disk()
                compile_result = compiler.hsf2libpart(str(hsf_dir_c), create_gsm_path)
                on_event("compile_result", {
                    "success": compile_result.success,
                    "error": compile_result.stderr if not compile_result.success else "",
                })

                while (
                    compile_result is not None
                    and not compile_result.success
                    and _create_repair_rounds < _MAX_CREATE_REPAIR
                ):
                    _create_repair_rounds += 1
                    error_parts = [
                        p.strip()
                        for p in [compile_result.stderr or "", compile_result.stdout or ""]
                        if p.strip()
                    ]
                    error_log = "\n".join(error_parts)
                    on_event("status", {
                        "message": f"🔧 编译失败（第 {_create_repair_rounds} 轮），正在自动修复…"
                    })
                    logger.info(
                        "CREATE compile fail round %d; error_log=%d chars",
                        _create_repair_rounds, len(error_log),
                    )

                    # 图谱诊断：复用 diagnose_error（ErrorClassifier + 图谱 concept 归因）
                    graph_diagnosis = ""
                    try:
                        from openbrep.knowledge_graph import get_graph_manager
                        graph_diagnosis = get_graph_manager().diagnose_error(error_log)
                        if graph_diagnosis:
                            _graph_powered_repair = True
                    except Exception as _g_exc:
                        logger.debug("Graph diagnosis skipped in CREATE repair: %s", _g_exc)

                    graph_hint = f"\n\n{graph_diagnosis}" if graph_diagnosis else ""
                    repair_instruction = (
                        f"{enriched_instruction}\n\n"
                        f"编译失败（第 {_create_repair_rounds} 轮），请基于当前脚本进行最小改动修复以下错误：\n"
                        f"```\n{error_log[:800]}\n```"
                        f"{graph_hint}"
                    )
                    try:
                        repair_changes, _repair_plain = agent.generate_only(
                            instruction=repair_instruction,
                            project=project,
                            knowledge=knowledge,
                            skills=skills_text,
                            include_all_scripts=True,
                            history=request.history,
                        )
                        repair_cleaned = (
                            {k: sanitize_llm_script_output(v, k) for k, v in repair_changes.items()}
                            if repair_changes else {}
                        )
                        repair_cleaned, repair_lint = _run_gdl_linter(repair_cleaned, on_event=on_event)
                        if repair_cleaned:
                            agent._apply_changes(project, repair_cleaned)
                            cleaned.update(repair_cleaned)
                        if repair_lint:
                            lint_summary = "\n\n".join(p for p in [lint_summary, repair_lint] if p)

                        hsf_dir_c2 = project.save_to_disk()
                        compile_result = compiler.hsf2libpart(str(hsf_dir_c2), create_gsm_path)
                        on_event("compile_result", {
                            "success": compile_result.success,
                            "error": compile_result.stderr if not compile_result.success else "",
                        })
                        if compile_result.success:
                            auto_repair_info = (
                                f"🔧 第 {_create_repair_rounds} 轮自动修复后编译通过"
                            )
                            break
                        elif _create_repair_rounds >= _MAX_CREATE_REPAIR:
                            short_err = compile_result.stderr[:300].strip()
                            auto_repair_info = (
                                f"🔧 {_MAX_CREATE_REPAIR} 轮自动修复后仍编译失败，"
                                f"已标记为需人工复查：\n```\n{short_err}\n```"
                            )
                    except Exception as exc:
                        logger.warning(
                            "CREATE auto-repair round %d exception: %s", _create_repair_rounds, exc
                        )
                        auto_repair_info = f"🔧 第 {_create_repair_rounds} 轮自动修复异常：{exc}"
                        break

                # 编译通过后创建 Revision（与 MODIFY 路径对称）
                if compile_result is not None and compile_result.success and cleaned:
                    _create_auto_revision(
                        project,
                        message="auto: after create (compile ok)",
                        trigger="create",
                        intent=request.intent or "CREATE",
                        user_instruction=request.user_input,
                        changed_files=list(cleaned.keys()),
                        parent_revision_id=(
                            get_latest_revision_id(project.root)
                            if _can_revision_project(project) else None
                        ),
                        metadata={
                            "compile": _compile_revision_metadata(compile_result, project),
                            "explanation": "",
                        },
                    )
                    logger.info(
                        "[create] compile ok after %d repair round(s); revision created",
                        _create_repair_rounds,
                    )

            except Exception as exc:
                logger.warning("CREATE compile verification failed: %s", exc)
                compile_not_run_reason = f"编译调用异常：{exc}"
        # ─────────────────────────────────────────────────────────────────────

        # ── 语义验证（Phase 1）：轻量 previewer 检查几何是否非空/非退化、
        # 包围盒是否匹配声明的 A/B/ZZYZX；不依赖 LP_XMLConverter，never raises ──
        from openbrep.semantic_verifier import verify_semantics
        semantic_result = verify_semantics(project)
        # ─────────────────────────────────────────────────────────────────────

        # ── 语义修复闭环（S1）：编译通过但几何验证有 blocking issue 时，
        # 把确定性证据喂回 LLM 做有界修复；接受/回退判定在 semantic_repair 模块 ──
        from openbrep.runtime.semantic_repair import run_semantic_repair_loop
        _sem_gsm_path: Optional[str] = None
        if self.config.compiler.path:
            _sem_out_dir = Path(request.output_dir) if request.output_dir else Path(
                tempfile.mkdtemp(prefix="obr_create_sem_")
            )
            _sem_out_dir.mkdir(parents=True, exist_ok=True)
            _sem_gsm_path = str(_sem_out_dir / f"{request.gsm_name or project.name}.gsm")
        _sem_outcome = run_semantic_repair_loop(
            agent=agent,
            project=project,
            cleaned=cleaned,
            compile_result=compile_result,
            semantic_result=semantic_result,
            instruction=enriched_instruction,
            knowledge=knowledge,
            skills_text=skills_text,
            history=request.history,
            compiler=compiler,
            compiler_configured=bool(self.config.compiler.path),
            gsm_path=_sem_gsm_path,
            lint_summary=lint_summary,
            auto_repair_info=auto_repair_info,
            on_event=on_event,
            lint_fn=_run_gdl_linter,
        )
        cleaned = _sem_outcome.cleaned
        compile_result = _sem_outcome.compile_result
        semantic_result = _sem_outcome.semantic_result
        lint_summary = _sem_outcome.lint_summary
        auto_repair_info = _sem_outcome.auto_repair_info

        # 语义修复被接受后补一个 revision（编译成功时的主 revision 已在前面创建）
        if (
            _sem_outcome.accepted_rounds > 0
            and self.config.compiler.path
            and compile_result is not None
            and compile_result.success
            and cleaned
        ):
            _create_auto_revision(
                project,
                message="auto: after create (semantic repair)",
                trigger="create",
                intent=request.intent or "CREATE",
                user_instruction=request.user_input,
                changed_files=list(cleaned.keys()),
                parent_revision_id=(
                    get_latest_revision_id(project.root)
                    if _can_revision_project(project) else None
                ),
                metadata={
                    "compile": _compile_revision_metadata(compile_result, project),
                    "explanation": "",
                },
            )
        # ─────────────────────────────────────────────────────────────────────

        # 反馈信号采集（只采集，best-effort；不改判定）：
        # 语义修复闭环实际跑了轮次 → semantic_repair_outcome
        if _sem_outcome.rounds_attempted > 0:
            append_feedback(project.root, {
                "kind": "semantic_repair_outcome",
                "summary": (
                    f"语义修复跑了 {_sem_outcome.rounds_attempted} 轮，"
                    f"接受 {_sem_outcome.accepted_rounds} 轮"
                ),
                "detail": {
                    "attempted": _sem_outcome.rounds_attempted,
                    "accepted": _sem_outcome.accepted_rounds,
                    "intent": request.intent or "CREATE",
                },
            })

        create_text_parts = []
        if object_plan is not None:
            create_text_parts.append(object_plan.to_user_summary())
        if plain_text:
            create_text_parts.append(plain_text)
        if lint_summary:
            create_text_parts.append(lint_summary)
        if auto_repair_info:
            create_text_parts.append(auto_repair_info)

        # ── Verification report ──────────────────────────────────────────────
        from openbrep.verification import build_verification_report
        from openbrep.naming_alignment import detect_reserved_param_misuse
        verification_report = build_verification_report(
            intent=request.intent or "CREATE",
            user_input=request.user_input,
            project=project,
            object_plan=object_plan,
            static_result=static_result,
            semantic_result=semantic_result,
            lint_summary=lint_summary,
            compile_result=compile_result,
            compile_not_run_reason=compile_not_run_reason,
            static_repair_triggered=bool(undef_errors),
            auto_repair_info=auto_repair_info,
            graph_powered=_graph_constraint_injected or _graph_powered_repair,
            reserved_conflicts=detect_reserved_param_misuse(project),
            # P8 交付完整性（CREATE/IMAGE 专属；MODIFY 路径不传 → 不启用）
            enable_delivery_integrity=(request.intent in ("CREATE", "IMAGE")),
        )
        create_text_parts.append(verification_report.to_summary_text())
        # ─────────────────────────────────────────────────────────────────────

        return TaskResult(
            success=verification_report.passed,
            intent=request.intent or "CREATE",
            scripts=cleaned,
            plain_text="\n\n".join(create_text_parts),
            project=project,
            compile_result=compile_result,
            lint_summary=lint_summary,
            object_plan=object_plan.to_dict() if object_plan is not None else {},
            verification=verification_report.to_dict(),
            semantic_repair={
                "attempted": _sem_outcome.rounds_attempted,
                "accepted": _sem_outcome.accepted_rounds,
            },
            # P5d-1：vision 提取透出（无提取时为空 dict，避免污染 metadata）
            metadata={"vision_extractions": vision_extractions} if vision_extractions else {},
        )

    def _handle_modify(self, request: TaskRequest) -> TaskResult:
        """
        Modify an existing GDL project.

        Differences from _handle_gdl (CREATE):
        - include_all_scripts=True  → injects ALL scripts into LLM context
        - Prepends _MODIFY_SKILLS_PROMPT to reinforce minimal-change discipline
        - Snapshots project state before changes for diff summary
        - Runs preflight and StaticChecker after applying changes
        - Attempts compile validation (real or mock)
        """
        micro_result = self._try_micro_modify(request)
        if micro_result is not None:
            return micro_result
        skill_ops_result = self._try_skill_ops(request)
        if skill_ops_result is not None:
            return skill_ops_result
        dsl_result = self._try_param_modify(request)
        if dsl_result is not None:
            return dsl_result
        return self._handle_script_update(request)

    def _try_micro_modify(self, request: TaskRequest) -> Optional[TaskResult]:
        """P2 确定性微修改：纯参数值设置直接落 paramlist，不调用 LLM。

        识别不出或场景不适用时返回 None，正常走 _handle_script_update。
        语义与参数面板直接改值一致：值变更按用户显式意图落盘，
        编译照常验证（benchmark 契约要求 compile_result），
        几何语义验证只做 advisory 警告、不拦截。
        """
        from openbrep.runtime.micro_modify import apply_parameter_value, detect_micro_modify

        if (request.intent or "MODIFY") != "MODIFY":
            return None  # DEBUG/REPAIR 带错误上下文，必须走 LLM
        if request.image_path or request.image_b64 or request.images:
            return None
        project = request.project
        if project is None or not project.parameters:
            return None
        instruction = (request.user_input or "").strip()
        if not instruction or instruction.startswith("["):
            return None

        micro = detect_micro_modify(instruction, project)
        if micro is None:
            return None
        param = project.get_parameter(micro.param_name)
        if param is None:
            return None

        on_event = request.on_event or (lambda *args: None)
        on_event("status", {"stage": "understand", "message": "🤔 正在理解你的修改意图…"})
        on_event("status", {"stage": "locate", "message": f"🎯 定位到参数：{micro.param_name}"})
        on_event("plan", {
            "intent_summary": f"把参数 {micro.param_name} 从 {micro.old_value} 改为 {micro.new_value}",
            "affected_files": ["paramlist.xml"],
            "parameter_changes": [{"name": micro.param_name, "from": micro.old_value, "to": micro.new_value}],
            "strategy": "直接修改参数默认值并编译验证",
        })

        # 快照"修改前"状态 + 改值 + 落盘：复用与 MCP apply_edit 同一落盘语义
        # （openbrep/runtime/micro_modify.apply_parameter_value）。
        # revision 拷的是磁盘状态，必须先于内存修改 + save_to_disk。
        from openbrep.runtime.modify_acceptance import preview_geometry_summary
        before_preview = preview_geometry_summary(project)  # before 预览必须在应用前取
        _revision_id, revision_warnings = apply_parameter_value(
            project,
            micro.param_name,
            micro.new_value,
            user_instruction=instruction,
            changed_files=["paramlist.xml"],
            metadata={
                "micro_modify": {
                    "param": micro.param_name,
                    "old_value": micro.old_value,
                    "new_value": micro.new_value,
                    "matched_via": micro.matched_via,
                }
            },
            create_revision=create_revision,
        )
        on_event("status", {"stage": "modify", "message": f"✏️ 已更新参数 {micro.param_name}"})

        # 修改后预览摘要（before 已在应用前取）
        from openbrep.runtime.modify_acceptance import preview_geometry_summary
        after_preview = preview_geometry_summary(project)

        compile_result: Optional[CompileResult] = None
        try:
            compiler = self._make_compiler()
            out_dir = Path(request.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            gsm_path = str(out_dir / f"{request.gsm_name or project.name}.gsm")
            compile_result = compiler.hsf2libpart(str(project.root), gsm_path)
            on_event("compile_result", {
                "success": compile_result.success,
                "error": compile_result.stderr if not compile_result.success else "",
            })
        except Exception as exc:
            logger.warning("micro-modify compile failed: %s", exc)

        semantic_note = ""
        semantic_issues: list[str] = []
        try:
            from openbrep.semantic_verifier import verify_semantics

            semantic_result = verify_semantics(project)
            blocking = [issue for issue in semantic_result.issues if issue.blocking]
            semantic_issues = [issue.detail for issue in blocking]
            if blocking:
                semantic_note = "⚠️ 几何验证警告：\n" + "\n".join(f"- {issue.detail}" for issue in blocking)
        except Exception:
            pass

        # 确定性验收摘要（不调 LLM）：参数变更 + 前后几何对比 + 验证结论
        from openbrep.runtime.modify_acceptance import build_modify_acceptance
        acceptance = build_modify_acceptance(
            before=before_preview,
            after=after_preview,
            parameter_changes=[{"name": micro.param_name, "from": micro.old_value, "to": micro.new_value}],
            changed_files=["paramlist.xml"],
            compile_result=compile_result,
            semantic_issues=semantic_issues,
            revision_id=_revision_id,
            revision_warnings=revision_warnings,
        )

        output_parts = [
            f"✅ 已将参数 `{micro.param_name}` 从 `{micro.old_value}` 改为 `{micro.new_value}`"
            "（确定性微修改，未调用 LLM）",
        ]
        if compile_result is not None:
            if compile_result.success:
                output_parts.append("编译：✅ 通过")
            else:
                output_parts.append(f"编译：❌ 失败\n{compile_result.stderr[:800]}")
        if semantic_note:
            output_parts.append(semantic_note)
        if revision_warnings:
            output_parts.append("**版本快照提示：**\n" + "\n".join(f"- {w}" for w in revision_warnings))

        return TaskResult(
            success=compile_result.success if compile_result is not None else True,
            intent="MODIFY",
            project=project,
            compile_result=compile_result,
            plain_text="\n\n".join(output_parts),
            revision_warnings=revision_warnings,
            metadata={"acceptance": acceptance},
        )

    def _try_param_modify(self, request: TaskRequest) -> Optional[TaskResult]:
        """V1 参数级修改 DSL：LLM 只做意图解析，应用全确定性。

        顺序在正则 micro_modify / skill_ops 之后、agent loop / 全文改写之前：
        micro_modify（单参数设值）→ skill_ops（结构化 skill 模板）→
        param_modify DSL（参数操作 JSON）→ LLM 路径。
        解析失败 / JSON 不合法 / 任一 op 校验不过 / 守护回滚 → 返回 None 回落。
        语义与微修改一致：编译必跑（benchmark 契约要求 compile_result），
        几何语义验证只做 advisory 警告、不拦截；plan 与校验结果写入
        TaskResult.metadata 与版本快照 metadata。
        """
        from openbrep.runtime.param_modify import (
            apply_param_modify,
            format_op_summary,
            parse_param_modify,
        )

        if (request.intent or "MODIFY") != "MODIFY":
            return None  # DEBUG/REPAIR 带错误上下文，必须走 LLM
        if request.image_path or request.image_b64 or request.images:
            return None
        project = request.project
        if project is None or not project.parameters:
            return None
        instruction = (request.user_input or "").strip()
        if not instruction or instruction.startswith("["):
            return None

        on_event = request.on_event or (lambda *args: None)
        on_event("status", {"stage": "understand", "message": "🤔 正在理解你的修改意图…"})

        # 一次 LLM 调用做意图解析；失败/校验不过即回落，不重试
        llm = self._make_llm(request)
        fallback_reasons: list[str] = []
        plan = parse_param_modify(instruction, project, llm, on_fallback=fallback_reasons.append)
        if plan is None:
            # 采集 DSL 回落信号（best-effort；reason 由 parse_param_modify 透出）
            reason = fallback_reasons[-1] if fallback_reasons else "unknown"
            append_feedback(project.root, {
                "kind": "dsl_fallback",
                "summary": f"参数级修改 DSL 回落 LLM 路径（{reason}）",
                "detail": {"reason": reason, "instruction": instruction},
            })
            return None

        op_lines = [format_op_summary(op) for op in plan.operations]
        revision_metadata = {"param_modify": {"plan": plan.to_dict()}}
        return self._finish_param_plan(
            request,
            project,
            plan,
            instruction=instruction,
            on_event=on_event,
            op_lines=op_lines,
            revision_metadata=revision_metadata,
            output_header="✅ 已执行确定性参数修改（LLM 仅做意图解析，未改写 GDL 代码）",
        )

    def _try_skill_ops(self, request: TaskRequest) -> Optional[TaskResult]:
        """结构化 skill operations 模板走确定性路径（S3）。

        插在 micro_modify 之后、param_modify DSL 之前：micro → skill_ops → DSL → LLM。
        恰好一个带 operations 模板的 active/verified skill 高精度命中指令，且
        占位符填值 + param_modify 校验全过 → 应用 ParamModifyPlan（与 DSL 路径
        完全同码：快照/守护/编译/语义 advisory）；任何一步失败 → None 回落。
        result.metadata 记 {"modify_path": "skill_ops", "skill": <name>}。
        """
        from openbrep.runtime.param_modify import format_op_summary
        from openbrep.runtime.skill_ops import try_skill_ops

        if (request.intent or "MODIFY") != "MODIFY":
            return None  # DEBUG/REPAIR 带错误上下文，必须走 LLM
        if request.image_path or request.image_b64 or request.images:
            return None
        project = request.project
        if project is None or not project.parameters:
            return None
        instruction = (request.user_input or "").strip()
        if not instruction or instruction.startswith("["):
            return None

        on_event = request.on_event or (lambda *args: None)
        on_event("status", {"stage": "understand", "message": "🤔 正在检索技能模板…"})

        # loader 未建则惰性建（与 _load_skills 同源）；LLM 只在占位符需要填值时构造
        if self._skills_loader is None:
            sk_dir = self._resolve_skills_dir()
            from openbrep.skills_loader import SkillsLoader

            self._skills_loader = SkillsLoader(str(sk_dir))
            self._skills_loader.load()
        loader = self._skills_loader

        hit = try_skill_ops(
            instruction,
            project,
            loader,
            make_llm=lambda: self._make_llm(request),
        )
        if hit is None:
            return None  # 无命中 / 歧义 / 填值或校验不过 → 回落原路径

        plan, skill_name = hit
        op_lines = [format_op_summary(op) for op in plan.operations]
        revision_metadata = {
            "param_modify": {"plan": plan.to_dict()},
            "skill_ops": {"skill": skill_name},
        }
        return self._finish_param_plan(
            request,
            project,
            plan,
            instruction=instruction,
            on_event=on_event,
            op_lines=op_lines,
            revision_metadata=revision_metadata,
            result_metadata_extra={"modify_path": "skill_ops", "skill": skill_name},
            output_header=f"✅ 已按 skill 模板「{skill_name}」执行确定性参数修改（未改写 GDL 代码）",
        )

    def _finish_param_plan(
        self,
        request: TaskRequest,
        project: HSFProject,
        plan: Any,
        *,
        instruction: str,
        on_event,
        op_lines: list[str],
        revision_metadata: dict,
        output_header: str,
        result_metadata_extra: Optional[dict] = None,
    ) -> Optional[TaskResult]:
        """DSL / skill_ops 共用：快照→应用→守护→编译→语义 advisory→验收→TaskResult。

        快照（create_revision）→ apply_param_modify（守护回滚）→ 编译（必跑，
        benchmark 契约）→ 几何语义仅 advisory → 确定性验收摘要 → TaskResult。
        守护回滚（计划外文件变更）返回 None（调用方回落 LLM 路径）。
        result_metadata_extra 并入 result.metadata（skill_ops 记 modify_path/skill）。
        """
        from openbrep.runtime.param_modify import apply_param_modify

        on_event("status", {"stage": "locate", "message": "🎯 已解析为参数操作：" + "；".join(op_lines)})
        on_event("plan", {
            "intent_summary": "；".join(op_lines),
            "affected_files": ["paramlist.xml"] + [
                f"scripts/{st.value}" for op in plan.operations
                if op.op == "rename_param"
                for st in project.scripts
            ],
            "parameter_changes": [
                {
                    "name": op.param or op.from_name or op.name,
                    "from": op.old_value or (op.from_name or ""),
                    "to": op.value if op.value is not None else (op.name or ""),
                }
                for op in plan.operations
            ],
            "strategy": "确定性参数修改（未改写 GDL 代码）",
        })

        # 快照→应用→落盘（与微修改同一落盘语义）+ 变更守护；
        # revision 拷的是磁盘状态，必须先于内存修改 + save_to_disk。
        from openbrep.runtime.modify_acceptance import preview_geometry_summary
        before_preview = preview_geometry_summary(project)  # before 预览必须在应用前取
        outcome = apply_param_modify(
            project,
            plan,
            user_instruction=instruction,
            metadata=revision_metadata,
            create_revision=create_revision,
        )
        if not outcome.applied:
            # 守护回滚：计划外文件被改动，按"识别不出"回落 LLM 路径
            return None
        on_event("status", {"stage": "modify", "message": "✏️ 已应用参数操作：" + "；".join(op_lines)})

        compile_result: Optional[CompileResult] = None
        try:
            compiler = self._make_compiler()
            out_dir = Path(request.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            gsm_path = str(out_dir / f"{request.gsm_name or project.name}.gsm")
            compile_result = compiler.hsf2libpart(str(project.root), gsm_path)
            on_event("compile_result", {
                "success": compile_result.success,
                "error": compile_result.stderr if not compile_result.success else "",
            })
        except Exception as exc:
            logger.warning("param-modify compile failed: %s", exc)

        semantic_issues: list[str] = []
        try:
            from openbrep.semantic_verifier import verify_semantics

            semantic_result = verify_semantics(project)
            semantic_issues = [issue.detail for issue in semantic_result.issues if issue.blocking]
        except Exception:
            pass

        # 确定性验收摘要（不调 LLM）：参数变更 + 前后几何对比 + 验证结论
        from openbrep.runtime.modify_acceptance import build_modify_acceptance, preview_geometry_summary
        after_preview = preview_geometry_summary(project)
        acceptance = build_modify_acceptance(
            before=before_preview,
            after=after_preview,
            parameter_changes=[
                {
                    "name": op.param or op.from_name or op.name,
                    "from": op.old_value or (op.from_name or ""),
                    "to": op.value if op.value is not None else (op.name or ""),
                }
                for op in plan.operations
            ],
            changed_files=outcome.changed_files or [],
            compile_result=compile_result,
            semantic_issues=semantic_issues,
            revision_id=outcome.revision_id,
            revision_warnings=outcome.warnings,
        )

        output_parts = [
            output_header,
            "\n".join(f"- {line}" for line in op_lines),
        ]
        if compile_result is not None:
            if compile_result.success:
                output_parts.append("编译：✅ 通过")
            else:
                output_parts.append(f"编译：❌ 失败\n{compile_result.stderr[:800]}")
        if semantic_issues:
            output_parts.append("⚠️ 几何验证警告：\n" + "\n".join(f"- {detail}" for detail in semantic_issues))
        if outcome.warnings:
            output_parts.append("**版本快照提示：**\n" + "\n".join(f"- {w}" for w in outcome.warnings))

        return TaskResult(
            success=compile_result.success if compile_result is not None else True,
            intent="MODIFY",
            project=project,
            compile_result=compile_result,
            plain_text="\n\n".join(output_parts),
            revision_warnings=outcome.warnings,
            metadata={
                "param_modify": {
                    "plan": plan.to_dict(),
                    "compile_success": compile_result.success if compile_result is not None else None,
                    "semantic_issues": semantic_issues,
                    "changed_files": outcome.changed_files or [],
                },
                "acceptance": acceptance,
                **(result_metadata_extra or {}),
            },
        )

    def _handle_modify_agent_loop(self, request: TaskRequest) -> TaskResult:
        """默认路径：预算制、工具调用驱动的 MODIFY/DEBUG/REPAIR agent loop。

        LLM 通过 `update_script` / `compile_script` / `preview_geometry` 等工具
        自主迭代，直到通过完成门禁（编译 + 几何语义）或预算耗尽。
        与 `_handle_script_update` 完全独立；显式 `agent_loop=False` 可回退旧路径。
        实现见 runtime/modify_agent_loop.py。
        """
        from openbrep.runtime.modify_agent_loop import run_modify_agent_loop
        return run_modify_agent_loop(self, request)

    def _handle_repair(self, request: TaskRequest) -> TaskResult:
        """Repair an existing GDL project using compile/runtime error context."""
        repair_request = deepcopy(request)
        repair_request.intent = "REPAIR"
        return self._handle_script_update(repair_request)

    def _handle_script_update(self, request: TaskRequest) -> TaskResult:
        """Shared implementation for MODIFY / DEBUG / REPAIR tasks."""
        llm = self._make_llm(request)
        compiler = self._make_compiler()
        clean_instruction, syntax_report = _normalize_modify_request(request)

        # Prepare project — create empty one if none provided
        project = request.project
        if project is None:
            gsm_name = request.gsm_name or "untitled"
            project = HSFProject.create_new(gsm_name, work_dir=request.work_dir)
        request.project = project
        assembled_context = self._assemble_context(
            request,
            project,
            instruction=clean_instruction,
            include_modify_rules=True,
        )
        knowledge = assembled_context.generation_context
        skills_text = assembled_context.skills_text
        self._record_user_error_learning(request, project, clean_instruction)

        # Snapshot BEFORE state for rule-based summary and optional compile comparison.
        before_project_snapshot = deepcopy(project)
        compare_mode = _normalize_compare_compile_mode(request.compare_compile)
        before_compile_snapshot = _compile_snapshot_for_project(
            before_project_snapshot,
            mode=compare_mode,
            config=self.config,
            label="before",
        )

        on_event = request.on_event or (lambda *_: None)

        # 多图通道（P5a）：路径来源读取 + 预处理（仅 images 非空时生效；旧字段不受影响）
        multi_images: list[ImageRef] = []
        if request.images and not request.image_b64:
            from openbrep.vision.multi_image import resolve_and_preprocess

            multi_images = resolve_and_preprocess(request.images)

        agent = GDLAgent(
            llm=llm,
            compiler=compiler,
            on_event=on_event,
            assistant_settings=request.assistant_settings,
            should_cancel=request.should_cancel,
        )

        # Key: include_all_scripts=True injects every non-empty script,
        # which also enables chat_mode (debug-style minimal-change prompt).
        changes, plain_text = agent.generate_only(
            instruction=clean_instruction,
            project=project,
            knowledge=knowledge,
            skills=skills_text,
            include_all_scripts=True,
            history=request.history,
            syntax_report=syntax_report,
            last_code_context=request.last_code_context,
            image_b64=request.image_b64,
            image_mime=request.image_mime,
            # MODIFY/DEBUG：维持现状语义（图作上下文直传），扩为多图数组
            images=[
                {"b64": img.b64, "mime": img.mime, "token": img.token}
                for img in multi_images
                if img.b64
            ],
        )

        cleaned = {k: sanitize_llm_script_output(v, k) for k, v in changes.items()} if changes else {}
        cleaned, lint_summary = _run_gdl_linter(cleaned, on_event=on_event)

        before_revision_id: str | None = None
        revision_warnings: list[str] = []
        if cleaned:
            before_revision_id, before_revision_warning = _create_auto_revision(
                project,
                message=f"auto: before {(request.intent or 'MODIFY').lower()}",
                trigger=(request.intent or "MODIFY").lower(),
                intent=request.intent or "MODIFY",
                user_instruction=clean_instruction,
                changed_files=list(cleaned.keys()),
                parent_revision_id=get_latest_revision_id(project.root) if _can_revision_project(project) else None,
            )
            if before_revision_warning:
                revision_warnings.append(before_revision_warning)

        # Apply changes to project in-place
        if cleaned:
            agent._apply_changes(project, cleaned)

        preflight_summary = _run_modify_preflight(clean_instruction, project)

        # Static check
        from openbrep.static_checker import StaticChecker
        static_result = StaticChecker().check(project)

        # Compile validation
        compile_result: Optional[CompileResult] = None
        gsm_name = request.gsm_name or project.name
        gsm_path: Optional[str] = None
        try:
            out_dir = Path(request.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            gsm_path = str(out_dir / f"{gsm_name}.gsm")
            hsf_dir = project.save_to_disk()
            compile_result = compiler.hsf2libpart(str(hsf_dir), gsm_path)
            on_event("compile_result", {
                "success": compile_result.success,
                "error": compile_result.stderr if not compile_result.success else "",
            })
        except Exception as exc:
            logger.warning("Compile step failed: %s", exc)

        # Auto-repair on compile failure (max 2 rounds)
        _MAX_MODIFY_REPAIR = 2
        _modify_repair_round = 0
        auto_repair_info: str = ""
        _graph_powered_repair = False
        while (
            compile_result is not None
            and not compile_result.success
            and gsm_path is not None
            and _modify_repair_round < _MAX_MODIFY_REPAIR
        ):
            _modify_repair_round += 1
            error_parts = [
                p.strip()
                for p in [compile_result.stderr or "", compile_result.stdout or ""]
                if p.strip()
            ]
            error_log = "\n".join(error_parts)
            self._record_error_learning(
                request.work_dir,
                error_log,
                source="compile_result",
                project_name=project.name,
                instruction=clean_instruction,
                project=project,
            )
            on_event("status", {"message": f"🔧 编译失败（第 {_modify_repair_round} 轮），正在自动修复…"})
            logger.info(
                "Compile failed; triggering auto-repair round %d/%d. error_log=%d chars",
                _modify_repair_round, _MAX_MODIFY_REPAIR, len(error_log),
            )

            # ── 图谱诊断（阶段3）：从错误归因，注入结构化修复提示 ──────────
            graph_diagnosis = ""
            try:
                from openbrep.knowledge_graph import get_graph_manager
                graph_diagnosis = get_graph_manager().diagnose_error(error_log)
                if graph_diagnosis:
                    _graph_powered_repair = True
                    logger.info("[graph] diagnose_error provided diagnosis for repair")
            except Exception as _gexc:
                logger.debug("Graph diagnosis skipped: %s", _gexc)
            # ─────────────────────────────────────────────────────────────────

            graph_hint = f"\n\n{graph_diagnosis}" if graph_diagnosis else ""
            # 第 2 轮追加跨文件同步提示，防止参数名不一致导致反复失败
            cross_file_note = (
                "\n\n第 2 轮修复要点：在修复上述错误的同时，必须检查 3d.gdl 与 "
                "paramlist.xml 的跨文件参数名是否完全同步（大小写须一致）。"
                if _modify_repair_round >= 2 else ""
            )
            repair_instruction = (
                f"{clean_instruction}\n\n"
                f"编译失败（第 {_modify_repair_round} 轮），请基于当前脚本进行最小改动修复以下错误：\n"
                f"```\n{error_log[:800]}\n```"
                f"{graph_hint}{cross_file_note}"
            )
            try:
                repair_changes, _repair_plain = agent.generate_only(
                    instruction=repair_instruction,
                    project=project,
                    knowledge=knowledge,
                    skills=skills_text,
                    include_all_scripts=True,
                    history=request.history,
                )
                repair_cleaned = (
                    {k: sanitize_llm_script_output(v, k) for k, v in repair_changes.items()}
                    if repair_changes else {}
                )
                repair_cleaned, repair_lint_summary = _run_gdl_linter(repair_cleaned, on_event=on_event)
                if repair_cleaned:
                    agent._apply_changes(project, repair_cleaned)
                    cleaned.update(repair_cleaned)
                if repair_lint_summary:
                    lint_summary = "\n\n".join(part for part in [lint_summary, repair_lint_summary] if part)

                # Re-compile after repair
                hsf_dir2 = project.save_to_disk()
                compile_result = compiler.hsf2libpart(str(hsf_dir2), gsm_path)
                on_event("compile_result", {
                    "success": compile_result.success,
                    "error": compile_result.stderr if not compile_result.success else "",
                })
                if compile_result.success:
                    auto_repair_info = f"🔧 第 {_modify_repair_round} 轮自动修复后编译通过"
                    break
                elif _modify_repair_round >= _MAX_MODIFY_REPAIR:
                    short_err = compile_result.stderr[:300].strip()
                    auto_repair_info = (
                        f"🔧 {_MAX_MODIFY_REPAIR} 轮自动修复后仍编译失败：\n```\n{short_err}\n```"
                    )
            except Exception as exc:
                logger.warning("Auto-repair attempt round %d failed: %s", _modify_repair_round, exc)
                auto_repair_info = f"🔧 第 {_modify_repair_round} 轮自动修复尝试失败：{exc}"
                break

        # ── 语义验证 + 语义修复闭环（S1，与 CREATE 共用同一实现）────────────
        # MODIFY / DEBUG / REPAIR 此前完全没有几何验证：编译通过但几何为空 /
        # 尺寸错 / 参数是哑的都会直接交付。判决者 verify_semantics 是纯确定性
        # previewer，与生成上下文独立（防自我确认）；修复轮接受/回退语义与
        # CREATE 一致：编译（若配置）仍通过且 blocking issue 数严格下降。
        from openbrep.semantic_verifier import verify_semantics
        from openbrep.runtime.semantic_repair import run_semantic_repair_loop
        semantic_result = verify_semantics(project)
        _sem_outcome = run_semantic_repair_loop(
            agent=agent,
            project=project,
            cleaned=cleaned,
            compile_result=compile_result,
            semantic_result=semantic_result,
            instruction=clean_instruction,
            knowledge=knowledge,
            skills_text=skills_text,
            history=request.history,
            compiler=compiler,
            compiler_configured=bool(self.config.compiler.path),
            gsm_path=gsm_path,
            lint_summary=lint_summary,
            auto_repair_info=auto_repair_info,
            on_event=on_event,
            lint_fn=_run_gdl_linter,
        )
        cleaned = _sem_outcome.cleaned
        compile_result = _sem_outcome.compile_result
        semantic_result = _sem_outcome.semantic_result
        lint_summary = _sem_outcome.lint_summary
        auto_repair_info = _sem_outcome.auto_repair_info
        # ─────────────────────────────────────────────────────────────────────

        # 反馈信号采集（只采集，best-effort；不改判定）：
        # 语义修复闭环实际跑了轮次 → semantic_repair_outcome
        if _sem_outcome.rounds_attempted > 0:
            append_feedback(project.root, {
                "kind": "semantic_repair_outcome",
                "summary": (
                    f"语义修复跑了 {_sem_outcome.rounds_attempted} 轮，"
                    f"接受 {_sem_outcome.accepted_rounds} 轮"
                ),
                "detail": {
                    "attempted": _sem_outcome.rounds_attempted,
                    "accepted": _sem_outcome.accepted_rounds,
                    "intent": request.intent or "MODIFY",
                },
            })

        compile_comparison: CompileComparison | None = None
        if before_compile_snapshot is not None:
            after_compile_snapshot = _compile_snapshot_from_result(
                compile_result,
                project,
                mode=compare_mode,
            )
            if after_compile_snapshot is not None:
                compile_comparison = CompileComparison(
                    before=before_compile_snapshot,
                    after=after_compile_snapshot,
                )

        # Build output text: LLM analysis + structured summary + preflight/static/compile status
        all_scripts = [f"scripts/{stype.value}" for stype in ScriptType if project.get_script(stype)]
        structured_summary = _build_structured_summary(
            before_project=before_project_snapshot,
            after_project=project,
            changed_files=list(cleaned.keys()),
            all_scripts=all_scripts,
            compile_result=compile_result,
            linter_result=lint_summary,
        )
        contract_result = _run_contract_check(project) if cleaned else None
        contract_summary = _format_contract_summary(contract_result)
        output_parts: list[str] = []
        if plain_text:
            output_parts.append(plain_text)
        if lint_summary:
            output_parts.append(lint_summary)
        if structured_summary:
            output_parts.append(structured_summary)
        if contract_summary:
            output_parts.append(contract_summary)
        if preflight_summary:
            output_parts.append(preflight_summary)
        if not static_result.passed:
            warnings = "\n".join(f"  ⚠️  {e.detail}" for e in static_result.errors)
            output_parts.append(f"**静态检查发现问题：**\n{warnings}")
        if auto_repair_info:
            # auto_repair_info already contains the final compile status after repair
            output_parts.append(auto_repair_info)
        elif compile_result is not None:
            if compile_result.success:
                output_parts.append("✅ 编译通过")
            else:
                short_err = compile_result.stderr[:400].strip()
                output_parts.append(f"❌ 编译失败：\n```\n{short_err}\n```")
        comparison_summary = compile_comparison.summary() if compile_comparison else ""
        if comparison_summary:
            output_parts.append(comparison_summary)

        if cleaned and compile_result is not None and compile_result.success:
            _after_revision_id, after_revision_warning = _create_auto_revision(
                project,
                message=f"auto: after {(request.intent or 'MODIFY').lower()} (compile ok)",
                trigger=(request.intent or "MODIFY").lower(),
                intent=request.intent or "MODIFY",
                user_instruction=clean_instruction,
                changed_files=list(cleaned.keys()),
                parent_revision_id=before_revision_id,
                metadata={
                    "compile": _compile_revision_metadata(compile_result, project),
                    "explanation": structured_summary,
                    "compile_comparison": compile_comparison.to_dict() if compile_comparison else None,
                    "contract_issues": _contract_error_count(contract_result),
                },
            )
            if after_revision_warning:
                revision_warnings.append(after_revision_warning)
            _append_project_decision_from_update(
                assembled_context.project_context,
                summary=structured_summary,
                intent=request.intent or "MODIFY",
                instruction=clean_instruction,
                changed_files=list(cleaned.keys()),
                revision_id=_after_revision_id,
            )

        if revision_warnings:
            output_parts.append("**版本快照提示：**\n" + "\n".join(f"- {warning}" for warning in revision_warnings))

        # ── Verification report (Phase 3/4): aggregate static/lint/compile
        # (and any compile auto-repair) into a proof-oriented report. ────────
        from openbrep.verification import build_verification_report
        from openbrep.naming_alignment import detect_reserved_param_misuse
        verification_report = build_verification_report(
            intent=request.intent or "MODIFY",
            user_input=request.user_input,
            project=project,
            object_plan=None,
            static_result=static_result,
            semantic_result=semantic_result,
            lint_summary=lint_summary,
            compile_result=compile_result,
            auto_repair_info=auto_repair_info,
            graph_powered=_graph_powered_repair,
            reserved_conflicts=detect_reserved_param_misuse(project),
        )
        output_parts.append(verification_report.to_summary_text())
        # ─────────────────────────────────────────────────────────────────────

        return TaskResult(
            success=verification_report.passed,
            intent=request.intent or "MODIFY",
            scripts=cleaned,
            plain_text="\n\n".join(output_parts),
            project=project,
            compile_result=compile_result,
            lint_summary=lint_summary,
            revision_warnings=revision_warnings,
            compile_comparison=compile_comparison,
            verification=verification_report.to_dict(),
            semantic_repair={
                "attempted": _sem_outcome.rounds_attempted,
                "accepted": _sem_outcome.accepted_rounds,
            },
        )

    # ── Initialization Helpers ────────────────────────────

    def _make_llm(self, request: TaskRequest) -> LLMAdapter:
        """
        Build LLMAdapter with config-level key resolution.

        Key/base selection is centralized in LLMConfig.resolve_api_key/
        resolve_api_base to avoid diverging UI/runtime routing behavior.
        """
        import dataclasses
        cfg = self.config.llm

        resolved = cfg.resolve_api_key(cfg.model)
        if resolved:
            cfg = dataclasses.replace(cfg, api_key=resolved)

        if request.assistant_settings and not cfg.assistant_settings:
            cfg = dataclasses.replace(cfg, assistant_settings=request.assistant_settings)

        adapter = LLMAdapter(cfg)
        # D3：注入 workbench 共享的 CodexProvider（None = 走默认注册表）。
        if self.codex_provider is not None:
            adapter.codex_provider = self.codex_provider
        return adapter

    def _make_compiler(self):
        """Return real compiler if path configured, otherwise MockHSFCompiler."""
        if self.config.compiler.path:
            return HSFCompiler(
                converter_path=self.config.compiler.path,
                timeout=self.config.compiler.timeout,
            )
        return MockHSFCompiler()

    def _load_knowledge(self) -> str:
        """Load knowledge base from project knowledge/ dir (cached)."""
        if self._knowledge_text is None:
            project_root = Path(__file__).parent.parent.parent
            kb_dir = project_root / "knowledge"
            kb = KnowledgeBase(str(kb_dir))
            kb.load()
            builtin = kb.get_by_task_type("all")

            # Append user knowledge if configured
            user_dir = self.config.user_knowledge_dir
            if user_dir:
                user_text = load_user_knowledge(user_dir)
                if user_text:
                    builtin = builtin + "\n\n---\n\n" + user_text if builtin else user_text

            self._knowledge_text = builtin
        return self._knowledge_text

    def _load_knowledge_for_request(self, request: TaskRequest) -> str:
        """Load global knowledge plus optional project-scoped context."""
        return self._select_knowledge_for_request(request).generation_context

    def _assemble_context(
        self,
        request: TaskRequest,
        project: HSFProject | None,
        *,
        instruction: str | None = None,
        include_modify_rules: bool = False,
    ) -> AssembledContext:
        """
        Assemble task context in one place.

        Priority:
        1. project-level metadata and knowledge;
        2. project-level durable memory;
        3. global/user knowledge selected for the request;
        4. global/project skills plus learned error avoidance.
        """
        task_request = replace(request, project=project, user_input=instruction or request.user_input)
        project_context = resolve_project_context(project)
        knowledge_selection = self._select_knowledge_for_request(task_request, context=project_context)
        skill_parts = [
            _MODIFY_SKILLS_PROMPT if include_modify_rules else "",
            self._load_skills_for_request(instruction or request.user_input, task_request, context=project_context),
            # benchmark 需要 prompt 可复现：学习记忆是累积态，会污染黄金语料，
            # include_learned_skills=False 时不注入（生产默认 True 不受影响）
            self._build_learned_error_skill_prompt(
                work_dir=request.work_dir,
                project=project,
            ) if self.include_learned_skills else "",
        ]
        return AssembledContext(
            project_context=project_context,
            knowledge_selection=knowledge_selection,
            skills_text="\n\n---\n\n".join(part for part in skill_parts if part),
        )

    def _select_knowledge_for_request(
        self,
        request: TaskRequest,
        *,
        context: ProjectContext | None = None,
    ) -> KnowledgeSelection:
        """Load request-aware GDL knowledge for planning and generation."""
        project_root = Path(__file__).parent.parent.parent
        context = context if context is not None else resolve_project_context(request.project)
        project_context_parts = [
            build_project_context_prompt(context),
            load_project_memory(context),
        ]
        return select_gdl_knowledge(
            instruction=request.user_input,
            intent=(request.intent or "all").lower(),
            knowledge_dir=project_root / "knowledge",
            base_context=self._load_knowledge(),
            project_context="\n\n---\n\n".join(part for part in project_context_parts if part),
            project_knowledge=load_project_knowledge(context, task_type=(request.intent or "all").lower()),
        )

    def _load_legacy_knowledge_for_request(self, request: TaskRequest) -> str:
        """Load global knowledge plus optional project-scoped context using the old concatenation path."""
        parts = [self._load_knowledge()]
        context = resolve_project_context(request.project)
        parts.append(build_project_context_prompt(context))
        parts.append(load_project_knowledge(context, task_type=(request.intent or "all").lower()))
        return "\n\n---\n\n".join(part for part in parts if part)

    def _load_skills(self, instruction: str) -> str:
        """Load skills relevant to instruction (loader cached)."""
        if self._skills_loader is None:
            sk_dir = self._resolve_skills_dir()
            self._skills_loader = SkillsLoader(str(sk_dir))
            self._skills_loader.load()
        return self._skills_loader.get_for_task(instruction)

    def _load_skills_for_request(
        self,
        instruction: str,
        request: TaskRequest,
        *,
        context: ProjectContext | None = None,
    ) -> str:
        """Load global skills plus optional project-scoped skills."""
        context = context if context is not None else resolve_project_context(request.project)
        return "\n\n---\n\n".join(
            part
            for part in [
                self._load_skills(instruction),
                load_project_skills(context, instruction),
            ]
            if part
        )

    def _build_learned_error_skill_prompt(self, *, work_dir: str = "", project: HSFProject | None = None) -> str:
        project_name = getattr(project, "name", "") if project is not None else ""
        parts: list[str] = []
        if project is not None and _can_revision_project(project):
            try:
                parts.append(ErrorLearningStore(project.root).build_skill_prompt(project_name=project_name))
            except Exception:
                pass
        if work_dir:
            try:
                parts.append(ErrorLearningStore(work_dir).build_skill_prompt(project_name=project_name))
            except Exception:
                pass
        return "\n\n---\n\n".join(part for part in parts if part)

    def _record_user_error_learning(self, request: TaskRequest, project: HSFProject, instruction: str) -> None:
        raw_error = request.error_log.strip() if request.error_log else ""
        source = "user_error_log"
        if not raw_error and looks_like_error_report(request.user_input):
            raw_error = request.user_input
            source = "user_or_tapir_error_report"
        if not raw_error:
            return
        self._record_error_learning(
            request.work_dir,
            raw_error,
            source=source,
            project_name=project.name,
            instruction=instruction,
            project=project,
        )

    def _record_error_learning(
        self,
        work_dir: str,
        raw_error: str,
        *,
        source: str,
        project_name: str,
        instruction: str = "",
        project: HSFProject | None = None,
    ) -> None:
        roots: list[str | Path] = []
        if work_dir:
            roots.append(work_dir)
        if project is not None and _can_revision_project(project):
            roots.append(project.root)
        for root in _unique_paths(roots):
            try:
                ErrorLearningStore(root).record_error(
                    raw_error,
                    source=source,
                    project_name=project_name,
                    instruction=instruction,
                )
            except Exception:
                logger.debug("Failed to record GDL error learning", exc_info=True)

    # ── Skill creator ────────────────────────────────────

    def _get_skill_creator(self, request: TaskRequest) -> SkillCreator:
        """Get a SkillCreator instance (fresh per call, no caching across conversations)."""
        llm = self._make_llm(request)
        skills_dir = str(self._resolve_skills_dir())
        return SkillCreator(llm, skills_dir=skills_dir)

    # ── Wiki knowledge ───────────────────────────────────

    def _load_wiki_knowledge(self) -> WikiKnowledge:
        """Load wiki knowledge base (cached)."""
        if self._wiki_knowledge is None:
            project_root = Path(__file__).parent.parent.parent
            wiki_dir = project_root / "knowledge" / "wiki"
            wk = WikiKnowledge(str(wiki_dir))
            wk.load()
            self._wiki_knowledge = wk
        return self._wiki_knowledge

    _wiki_knowledge: WikiKnowledge | None = None

    _GDL_KNOWLEDGE_KEYWORDS: set[str] = {
        "gdl", "命令", "语法", "syntax", "command",
        "参数", "parameter", "paramlist",
        "3d", "2d", "脚本", "script",
        "prism", "block", "body", "edge", "pgon",
        "hotspot", "project", "add", "del", "rot",
        "if", "endif", "for", "next", "elsif",
        "编译", "compile", "error", "错误",
        "材质", "material", "attribute",
    }

    @staticmethod
    def _has_gdl_keyword(text: str) -> bool:
        """Quick heuristic: check for GDL-related keywords."""
        lower = text.lower()
        for kw in TaskPipeline._GDL_KNOWLEDGE_KEYWORDS:
            if kw in lower:
                return True
        return False

    def _classify_gdl_knowledge_question(self, request: TaskRequest) -> bool:
        """LLM-based classification: is this a GDL knowledge question?"""
        llm = self._make_llm(request)
        prompt = (
            "你是一个分类器。判断用户问题是否涉及 GDL 知识（语法、命令、参数、概念、编写技巧、调试等）。\n"
            "只需回复 YES 或 NO。\n\n"
            f"用户问题：{request.user_input}"
        )
        try:
            resp = llm.generate([{"role": "user", "content": prompt}])
            return resp.content.strip().upper().startswith("YES")
        except Exception:
            return False

    def _handle_wiki_knowledge(self, request: TaskRequest) -> TaskResult | None:
        """Try to answer from wiki knowledge. Returns None if not a knowledge question."""
        user_input = request.user_input

        # Phase 1: quick heuristic
        if not self._has_gdl_keyword(user_input):
            # Phase 2: LLM classification for ambiguous cases
            if not self._classify_gdl_knowledge_question(request):
                return None

        # Retrieve relevant wiki pages
        wk = self._load_wiki_knowledge()
        wiki_context = wk.format_relevant_context(user_input, max_pages=3)
        if not wiki_context:
            return None

        # Synthesize answer
        llm = self._make_llm(request)
        system_content = _build_assistant_settings_prompt(request.assistant_settings) + (
            "你是 openbrep 的 GDL 知识助手。使用以下 wiki 内容回答用户的 GDL 知识问题。\n"
            "如果 wiki 内容不足以回答，可以结合你的知识补充，但不要编造 GDL 命令语法。\n"
            "回复简洁准确，使用用户输入的语言。必要时可以给出代码示例。\n\n"
            f"Wiki 参考资料：\n{wiki_context}"
        )
        history = _trim_history(request.history, limit=6)
        messages = [{"role": "system", "content": system_content}]
        messages.extend(
            {"role": item.get("role", "user"), "content": item.get("content", "")}
            for item in history
        )
        messages.append({"role": "user", "content": user_input})
        try:
            resp = llm.generate(messages)
            return TaskResult(success=True, intent="CHAT", plain_text=resp.content)
        except Exception as exc:
            return None


_SCRIPT_TYPE_MAP: dict[str, str] = {
    "scripts/3d.gdl": "3D",
    "scripts/2d.gdl": "2D",
    "scripts/1d.gdl": "Master",
    "scripts/vl.gdl": "Properties",
    "scripts/ui.gdl": "UI",
}


def _run_gdl_linter(cleaned: dict[str, str], on_event: Callable | None = None) -> tuple[dict[str, str], str]:
    """Run deterministic linter on generated scripts and return updated scripts + summary."""
    if not cleaned:
        return cleaned, ""

    from openbrep.gdl_linter import GDLLinter

    fixed_total = 0
    summary_lines: list[str] = []
    updated = dict(cleaned)
    for path, code in cleaned.items():
        if not path.startswith("scripts/"):
            continue
        script_type = _SCRIPT_TYPE_MAP.get(path, "3D")
        result = GDLLinter(script_type=script_type).fix(code)
        if result.fix_count > 0:
            updated[path] = result.fixed_code
            fixed_total += result.fix_count
            rules = sorted({issue.rule for issue in result.issues if issue.fixed})
            summary_lines.append(f"- {path}: 修复 {result.fix_count} 处（{', '.join(rules)}）")

    if fixed_total and on_event:
        on_event("status", {"message": f"🔧 Linter 自动修复了 {fixed_total} 个问题"})

    if not fixed_total:
        return updated, ""

    summary = "🔧 Linter 修复了以下问题：\n" + "\n".join(summary_lines)
    return updated, summary


def _normalize_modify_request(request: TaskRequest) -> tuple[str, str]:
    """Strip debug prefixes and merge structured repair/debug context."""
    clean_instruction = request.user_input or ""
    syntax_report = request.syntax_report or ""

    if clean_instruction.startswith("[DEBUG:editor]"):
        after_prefix = clean_instruction.split("]", 1)[-1].strip()
        if "[SYNTAX CHECK REPORT]" in after_prefix:
            parts = after_prefix.split("[SYNTAX CHECK REPORT]", 1)
            clean_instruction = parts[0].strip()
            if not syntax_report:
                syntax_report = parts[1].strip()
        else:
            clean_instruction = after_prefix

    if request.error_log:
        error_block = f"错误日志：\n{request.error_log.strip()}"
        if error_block not in clean_instruction:
            clean_instruction = f"{clean_instruction.strip()}\n\n{error_block}".strip()

    return clean_instruction, syntax_report


def _run_modify_preflight(instruction: str, project: HSFProject) -> str:
    """Run lightweight, non-blocking preflight analysis for modify/debug/repair tasks."""
    xml_like_context = []
    for stype in ScriptType:
        content = project.get_script(stype)
        if content:
            xml_like_context.append(f"<!-- {stype.value} -->\n{content}")
    xml_content = "\n".join(xml_like_context)

    analysis = PreflightAnalyzer().analyze(instruction=instruction, xml_content=xml_content)
    parts: list[str] = []
    if analysis.summary:
        parts.append(f"**Preflight：** {analysis.summary}")
    if analysis.blockers:
        parts.append("\n".join(f"- {item}" for item in analysis.blockers))
    return "\n".join(parts).strip()


def _build_chat_project_context(project: HSFProject) -> str:
    parameter_lines = [
        f"- {param.name}: type={param.type_tag}, value={param.value}, desc={param.description or '无'}, fixed={'yes' if param.is_fixed else 'no'}"
        for param in project.parameters
    ] or ["- 无参数"]

    script_lines = []
    for script_type in ScriptType:
        content = project.get_script(script_type)
        if not content:
            continue
        snippet_lines = [line.strip() for line in content.splitlines() if line.strip()]
        snippet = "\n".join(snippet_lines[:6])
        script_lines.append(f"### scripts/{script_type.value}\n{snippet}")

    scripts_text = "\n\n".join(script_lines) if script_lines else "无脚本内容"
    return (
        "## 当前工程解释上下文\n"
        "以下是当前 HSF/GDL 工程的只读摘要，仅用于解释，不用于修改。\n"
        f"构件名：{project.name}\n"
        f"参数：\n" + "\n".join(parameter_lines) + "\n\n"
        f"脚本摘要：\n{scripts_text}"
    )


def _build_assistant_settings_prompt(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return (
        "## AI助手设置\n"
        "以下内容是用户长期提供的协作偏好与使用场景描述。"
        "请在不违反系统规则、输出格式要求、GDL 硬性规则和当前任务要求的前提下参考执行。\n"
        f"{raw}\n\n"
    )


def _trim_history(history: Optional[list[dict]], limit: int = 6) -> list[dict]:
    if not history:
        return []
    return history[-limit:]


def _code_block_language(path: str) -> str:
    if path.startswith("scripts/"):
        return "gdl"
    if path.endswith(".xml"):
        return "xml"
    return "text"


def _code_block_label(path: str) -> str:
    if path.startswith("scripts/"):
        return path.replace("scripts/", "").replace(".gdl", "").upper()
    if "paramlist" in path:
        return "PARAMLIST"
    return path


def _build_generation_label(changed_files: list[str], scripts: dict[str, str]) -> str:
    script_names = [
        _code_block_label(path)
        for path in changed_files
        if path.startswith("scripts/")
    ]
    label_parts = []
    if script_names:
        label_parts.append(f"脚本 [{', '.join(script_names)}]")
    if "paramlist.xml" in scripts:
        param_text = scripts.get("paramlist.xml", "")
        param_lines = [
            line for line in str(param_text).splitlines()
            if line.strip() and not line.strip().startswith(("!", "#", "<", "</"))
        ]
        param_count = len(param_lines)
        label_parts.append(f"{param_count} 个参数")
    return " + ".join(label_parts) if label_parts else "内容"


def build_generation_result_plan(
    result: TaskResult,
    auto_apply: bool,
    gsm_name: Optional[str],
) -> GenerationResultPlan:
    changed_files = list((result.scripts or {}).keys())
    if not changed_files:
        return GenerationResultPlan(has_changes=False)

    label = _build_generation_label(changed_files, result.scripts)
    code_blocks = [
        {
            "path": path,
            "label": _code_block_label(path),
            "language": _code_block_language(path),
            "content": content,
        }
        for path, content in result.scripts.items()
    ]
    reply_prefix = f"✏️ **已写入 {label}** — 可直接「🔧 编译」\n\n"

    return GenerationResultPlan(
        has_changes=True,
        changed_files=changed_files,
        label=label,
        mode="auto_apply",
        code_blocks=code_blocks,
        reply_prefix=reply_prefix,
    )


def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that LLMs sometimes include."""
    return strip_md_fences(code)


def _can_revision_project(project: HSFProject) -> bool:
    root = Path(getattr(project, "root", "") or "")
    try:
        return root.is_dir() and is_hsf_project_dir(root)
    except Exception:
        return False


def _create_auto_revision(
    project: HSFProject,
    *,
    message: str,
    trigger: str,
    intent: str,
    user_instruction: str,
    changed_files: list[str],
    parent_revision_id: str | None,
    metadata: dict | None = None,
) -> tuple[str | None, str]:
    if not _can_revision_project(project):
        return None, "项目尚未保存为 HSF 目录，已跳过自动版本快照"
    try:
        revision = create_revision(
            project.root,
            message=message,
            gsm_name=project.name,
            metadata=metadata,
            trigger=trigger,
            intent=intent,
            user_instruction=user_instruction,
            changed_files=changed_files,
            parent_revision_id=parent_revision_id,
        )
        return revision.revision_id, ""
    except Exception as exc:
        logger.warning("Auto revision failed: %s", exc)
        return None, f"自动版本快照失败：{exc}"


def _compile_revision_metadata(compile_result: CompileResult, project: HSFProject) -> dict:
    output_path = compile_result.output_path or ""
    output = Path(output_path) if output_path else None
    return {
        "mode": compile_result.mode,
        "success": compile_result.success,
        "gsm_size_bytes": output.stat().st_size if output is not None and output.exists() else None,
        "gsm_path": output_path or None,
        "parameter_count": len(project.parameters),
        "exit_code": compile_result.exit_code,
    }


def _append_project_decision_from_update(
    context: ProjectContext | None,
    *,
    summary: str,
    intent: str,
    instruction: str,
    changed_files: list[str],
    revision_id: str | None,
) -> None:
    try:
        append_project_decision(
            context,
            summary=summary,
            intent=intent,
            instruction=instruction,
            changed_files=changed_files,
            revision_id=revision_id,
        )
    except Exception:
        logger.debug("Failed to append project decision memory", exc_info=True)


def _unique_paths(paths: list[str | Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for raw in paths:
        try:
            path = Path(raw).expanduser().resolve()
        except Exception:
            path = Path(raw).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _normalize_compare_compile_mode(mode: str | None) -> str:
    value = str(mode or "off").strip().lower()
    if value in {"", "off", "none", "false", "0", "no"}:
        return "off"
    if value in {"mock", "true", "1", "yes", "on"}:
        return "mock"
    if value == "real":
        return "real"
    return "off"


def _compiler_for_compare_mode(mode: str, config: GDLAgentConfig):
    if mode == "mock":
        return MockHSFCompiler()
    if mode == "real":
        return HSFCompiler(
            converter_path=config.compiler.path,
            timeout=config.compiler.timeout,
        )
    return None


def _compile_snapshot_from_result(
    compile_result: CompileResult | None,
    project: HSFProject,
    *,
    mode: str,
) -> CompileSnapshot | None:
    if mode == "off" or compile_result is None:
        return None
    output_path = Path(compile_result.output_path) if compile_result.output_path else None
    return CompileSnapshot(
        success=compile_result.success,
        gsm_size_bytes=output_path.stat().st_size if output_path is not None and output_path.exists() else None,
        parameter_count=len(project.parameters),
        exit_code=compile_result.exit_code,
        mode=mode,
    )


def _compile_snapshot_for_project(
    project: HSFProject,
    *,
    mode: str,
    config: GDLAgentConfig,
    label: str,
) -> CompileSnapshot | None:
    if mode == "off":
        return None
    compiler = _compiler_for_compare_mode(mode, config)
    if compiler is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix=f"openbrep_compare_{label}_") as temp_dir:
            temp_root = Path(temp_dir)
            project_copy = deepcopy(project)
            project_copy.work_dir = temp_root
            project_copy.root = temp_root / project_copy.name
            hsf_dir = project_copy.save_to_disk()
            gsm_path = temp_root / f"{label}.gsm"
            result = compiler.hsf2libpart(str(hsf_dir), str(gsm_path))
            return _compile_snapshot_from_result(result, project_copy, mode=mode)
    except Exception as exc:
        logger.warning("Compare compile failed for %s snapshot: %s", label, exc)
        return CompileSnapshot(
            success=False,
            gsm_size_bytes=None,
            parameter_count=len(project.parameters),
            exit_code=-1,
            mode=mode,
        )


def _snapshot_scripts(project: HSFProject) -> dict[str, str]:
    """
    Capture current project scripts as {file_path: content}.

    Uses the same path keys as GDLAgent._apply_changes() output
    (e.g. "scripts/3d.gdl", "paramlist.xml") so diffs are easy to compute.
    """
    snap: dict[str, str] = {}
    for stype in ScriptType:
        content = project.get_script(stype)
        if content:
            snap[f"scripts/{stype.value}"] = content
    # Represent paramlist as plain-text parameter lines for readable diff
    if project.parameters:
        lines = [
            f"{p.type_tag} {p.name} = {p.value}  ! {p.description}"
            + (" [FIXED]" if p.is_fixed else "")
            for p in project.parameters
        ]
        snap["paramlist.xml"] = "\n".join(lines)
    return snap


def _diff_parameters(before: HSFProject, after: HSFProject) -> dict:
    """Compare HSF project parameters and return added, removed, and changed items."""
    try:
        if before.parameters is None or after.parameters is None:
            return {}
        before_params = {p.name: p for p in before.parameters}
        after_params = {p.name: p for p in after.parameters}
    except Exception:
        return {}

    added = []
    removed = []
    changed = []

    for name, param in after_params.items():
        if name not in before_params:
            added.append({
                "name": name,
                "type": str(getattr(param, "type_tag", "")),
                "default": str(getattr(param, "value", "")),
            })
            continue

        before_param = before_params[name]
        changes = {}
        before_type = str(getattr(before_param, "type_tag", ""))
        after_type = str(getattr(param, "type_tag", ""))
        if after_type != before_type:
            changes["type"] = (before_type, after_type)

        before_default = str(getattr(before_param, "value", ""))
        after_default = str(getattr(param, "value", ""))
        if after_default != before_default:
            changes["default"] = (before_default, after_default)

        if changes:
            changed.append({"name": name, **changes})

    for name in before_params:
        if name not in after_params:
            removed.append({"name": name})

    return {"added": added, "removed": removed, "changed": changed}


def _linter_fix_count(linter_result) -> int:
    if linter_result is None:
        return 0
    value = getattr(linter_result, "fix_count", None)
    if isinstance(value, int):
        return value
    if isinstance(linter_result, str):
        matches = re.findall(r"修复\s+(\d+)\s+处", linter_result)
        return sum(int(item) for item in matches)
    return 0


def _run_contract_check(project: "HSFProject"):
    """
    Run GDLContractChecker and return GDLContractResult or None.

    Contract checks are explanatory only in the modify path; failures should not
    block generation, compile, revision creation, or user-visible output.
    """
    try:
        from openbrep.gdl_contract_checker import GDLContractChecker

        return GDLContractChecker().check(project)
    except Exception:
        return None


def _format_contract_summary(result) -> str:
    """
    Format GDLContractResult into a concise user-facing summary.

    Only error/warning issues are shown, capped at five entries to avoid
    drowning the main change explanation.
    """
    if not result:
        return ""
    try:
        issues = [
            issue
            for issue in getattr(result, "issues", [])
            if str(getattr(issue, "severity", "")).lower() in ("error", "warning")
        ]
        if not issues:
            return ""
        lines = ["**合规检查：**"]
        for issue in issues[:5]:
            severity = str(getattr(issue, "severity", "")).lower()
            icon = "❌" if severity == "error" else "⚠️"
            detail = str(getattr(issue, "detail", "") or "")
            file_hint = str(getattr(issue, "file", "") or "")
            text = f"{detail}（{file_hint}）" if file_hint else detail
            lines.append(f"- {icon} {text}")
        if len(issues) > 5:
            lines.append(f"- ... 还有 {len(issues) - 5} 个问题")
        return "\n".join(lines)
    except Exception:
        return ""


def _contract_error_count(result) -> int:
    if not result:
        return 0
    try:
        return sum(
            1
            for issue in getattr(result, "issues", [])
            if str(getattr(issue, "severity", "")).lower() == "error"
        )
    except Exception:
        return 0


def _build_structured_summary(
    before_project,
    after_project,
    changed_files: list,
    all_scripts: list,
    compile_result,
    linter_result,
) -> str:
    """Build a rule-based project-level change summary."""
    try:
        lines = ["**变更摘要：**"]

        if changed_files:
            lines.append(f"- 修改脚本：{', '.join(changed_files)}")
        unchanged = [script for script in all_scripts if script not in changed_files]
        if unchanged:
            lines.append(f"- 未改脚本：{', '.join(unchanged)}")

        if before_project and after_project:
            diff = _diff_parameters(before_project, after_project)
            for param in diff.get("added", []):
                suffix = f"（{param['type']}，默认 {param['default']}）" if param["type"] else ""
                lines.append(f"- 新增参数：{param['name']}{suffix}")
            for param in diff.get("removed", []):
                lines.append(f"- 删除参数：{param['name']}")
            for param in diff.get("changed", []):
                parts = []
                if "type" in param:
                    parts.append(f"类型 {param['type'][0]} → {param['type'][1]}")
                if "default" in param:
                    parts.append(f"默认值 {param['default'][0]} → {param['default'][1]}")
                lines.append(f"- 修改参数：{param['name']}（{', '.join(parts)}）")

        fix_count = _linter_fix_count(linter_result)
        if fix_count > 0:
            lines.append(f"- 自动修复：linter 修复了 {fix_count} 个问题")

        if compile_result is not None:
            status = "✅ 通过" if compile_result.success else "❌ 失败"
            lines.append(f"- 编译结果：{status}")

        return "\n".join(lines)
    except Exception:
        return ""


def _build_diff_summary(before: dict[str, str], changed_files: dict[str, str]) -> str:
    """
    Generate a human-readable line-count diff summary.

    Args:
        before:        snapshot from _snapshot_scripts() before apply
        changed_files: {file_path: new_content} dict from LLM output

    Returns:
        Markdown string like "**变更摘要：**\n  3D: +12行 / -5行\n  PARAMLIST: +2行 / -0行"
        or empty string if nothing changed.
    """
    if not changed_files:
        return ""

    parts = ["**变更摘要：**"]
    for fpath, new_content in changed_files.items():
        label = fpath.replace("scripts/", "").replace(".gdl", "").upper()
        if "paramlist" in fpath:
            label = "PARAMLIST"

        old_content = before.get(fpath, "")
        old_lines = old_content.splitlines() if old_content else []
        new_lines = new_content.splitlines() if new_content else []

        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        if diff:
            added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
            parts.append(f"  {label}: +{added} 行 / -{removed} 行")
        else:
            parts.append(f"  {label}: 内容未变化")

    return "\n".join(parts)


def _merge_list_values(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def _key_for_model(model: str, provider_keys: dict, custom_providers: list) -> str:
    """
    Resolve the correct API key for a given model.

    Mirrors app.py's _key_for_model() logic:
    1. Custom providers (exact model match in their models list)
    2. Known provider prefix mapping via provider_keys
    """
    m = (model or "").lower()

    # 1. Custom providers — exact model match
    for pcfg in custom_providers or []:
        for cm in pcfg.get("models", []) or []:
            if m == str(cm).lower():
                key = str(pcfg.get("api_key", "") or "")
                if key:
                    return key

    # 2. Known provider prefixes
    if "glm" in m:
        return provider_keys.get("zhipu", "")
    if "deepseek" in m and "ollama" not in m:
        return provider_keys.get("deepseek", "")
    if "claude" in m:
        return provider_keys.get("anthropic", "")
    if "gpt" in m or "o3" in m or "o1" in m or "o4" in m:
        return provider_keys.get("openai", "")
    if "gemini" in m:
        return provider_keys.get("google", "")
    if "qwen" in m or "qwq" in m:
        return provider_keys.get("aliyun", "")
    if "moonshot" in m:
        return provider_keys.get("kimi", "")

    return ""
