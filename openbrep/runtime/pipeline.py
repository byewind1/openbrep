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
  IMAGE   → _handle_gdl()     (vision mode, inject all scripts)
  CHAT    → _handle_chat()
"""

from __future__ import annotations

import difflib
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from openbrep.compiler import CompileResult, HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.knowledge import KnowledgeBase
from openbrep.llm import LLMAdapter
from openbrep.skills_loader import SkillsLoader
from openbrep.runtime.router import IntentRouter
from openbrep.runtime.tracer import Tracer
from openbrep.preflight import PreflightAnalyzer


# ── Modify-specific skill instructions ───────────────────
# These are prepended to skills_text for MODIFY/DEBUG tasks.
# They ride in the ## TASK STRATEGY section of the system prompt.

_MODIFY_SKILLS_PROMPT = """\
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

logger = logging.getLogger(__name__)


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
    assistant_settings: str = ""           # injected into GDL system prompt
    on_event: Optional[Callable] = None    # progress callback (event_type, data) -> None


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
    ):
        self.config = config or GDLAgentConfig.load(config_path)
        self.router = IntentRouter()
        self.tracer = Tracer(trace_dir=trace_dir)
        # Cached after first load (knowledge can be large)
        self._knowledge_text: Optional[str] = None
        self._skills_loader: Optional[SkillsLoader] = None

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
                has_image=request.image_path is not None,
            )

        # 2. Execute
        try:
            if request.intent == "CHAT":
                result = self._handle_chat(request)
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

        # 3. Trace (never blocks execution)
        try:
            trace_path = self.tracer.record(request, result)
            result.trace_path = str(trace_path)
        except Exception:
            pass

        return result

    # ── Handlers ─────────────────────────────────────────

    def _handle_chat(self, request: TaskRequest) -> TaskResult:
        """Simple conversational reply — no GDL code output."""
        llm = self._make_llm(request)
        system_content = (
            "你是 openbrep 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
            "【重要约束】绝对禁止在回复中输出任何 GDL 代码、代码块或脚本片段。"
            "如果用户想创建或修改 GDL 对象，告诉他直接描述需求，AI 会自动生成。\n"
            "回复简洁，使用中文，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
        )
        system_content = _build_assistant_settings_prompt(request.assistant_settings) + system_content
        history = _trim_history(request.history, limit=6)
        messages = [{"role": "system", "content": system_content}]
        messages.extend({"role": item.get("role", "user"), "content": item.get("content", "")} for item in history)
        messages.append({"role": "user", "content": request.user_input})
        try:
            resp = llm.generate(messages)
            return TaskResult(
                success=True,
                intent="CHAT",
                plain_text=resp.content,
            )
        except Exception as exc:
            return TaskResult(success=False, intent="CHAT", error=str(exc))

    def _handle_gdl(self, request: TaskRequest) -> TaskResult:
        """GDL generation / modification via GDLAgent.generate_only()."""
        llm = self._make_llm(request)
        compiler = self._make_compiler()
        knowledge = self._load_knowledge()
        skills_text = self._load_skills(request.user_input)

        # Ensure project exists
        project = request.project
        if project is None:
            gsm_name = request.gsm_name or "untitled"
            project = HSFProject.create_new(
                gsm_name,
                work_dir=request.work_dir,
            )

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

        on_event = request.on_event or (lambda *_: None)
        debug_mode = request.intent == "DEBUG"

        agent = GDLAgent(
            llm=llm,
            compiler=compiler,
            on_event=on_event,
            assistant_settings=request.assistant_settings,
            should_cancel=request.should_cancel,
        )

        changes, plain_text = agent.generate_only(
            instruction=request.user_input,
            project=project,
            knowledge=knowledge,
            skills=skills_text,
            include_all_scripts=debug_mode,
            last_code_context=request.last_code_context,
            syntax_report=request.syntax_report,
            history=request.history,
            image_b64=image_b64,
            image_mime=image_mime,
        )

        # Strip markdown fences the LLM sometimes leaks into scripts
        cleaned = {k: _strip_md_fences(v) for k, v in changes.items()} if changes else {}

        # Apply changes to the project in-place
        if cleaned:
            agent._apply_changes(project, cleaned)

        return TaskResult(
            success=True,
            intent=request.intent or "CREATE",
            scripts=cleaned,
            plain_text=plain_text or "",
            project=project,
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
        llm = self._make_llm(request)
        compiler = self._make_compiler()
        knowledge = self._load_knowledge()
        clean_instruction, syntax_report = _normalize_modify_request(request)
        skills_text = _MODIFY_SKILLS_PROMPT + "\n\n" + self._load_skills(clean_instruction)

        # Prepare project — create empty one if none provided
        project = request.project
        if project is None:
            gsm_name = request.gsm_name or "untitled"
            project = HSFProject.create_new(gsm_name, work_dir=request.work_dir)

        # Snapshot BEFORE state for diff
        before_scripts = _snapshot_scripts(project)

        on_event = request.on_event or (lambda *_: None)

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
        )

        cleaned = {k: _strip_md_fences(v) for k, v in changes.items()} if changes else {}

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

        # Build output text: LLM analysis + diff summary + preflight/static/compile status
        diff_summary = _build_diff_summary(before_scripts, cleaned)
        output_parts: list[str] = []
        if plain_text:
            output_parts.append(plain_text)
        if diff_summary:
            output_parts.append(diff_summary)
        if preflight_summary:
            output_parts.append(preflight_summary)
        if not static_result.passed:
            warnings = "\n".join(f"  ⚠️  {e.detail}" for e in static_result.errors)
            output_parts.append(f"**静态检查发现问题：**\n{warnings}")
        if compile_result is not None:
            if compile_result.success:
                output_parts.append("✅ 编译通过")
            else:
                short_err = compile_result.stderr[:400].strip()
                output_parts.append(f"❌ 编译失败：\n```\n{short_err}\n```")

        return TaskResult(
            success=True,
            intent=request.intent or "MODIFY",
            scripts=cleaned,
            plain_text="\n\n".join(output_parts),
            project=project,
            compile_result=compile_result,
        )

    # ── Initialization Helpers ────────────────────────────

    def _make_llm(self, request: TaskRequest) -> LLMAdapter:
        """
        Build LLMAdapter with the correct API key for the current model.

        Mirrors app.py's _key_for_model() logic: provider_keys lookup by
        model prefix takes priority over the generic [llm] api_key field.
        This ensures deepseek-* uses provider_keys.deepseek, not a generic key.
        """
        import dataclasses
        cfg = self.config.llm

        # Prefer provider_keys / custom_providers key over generic api_key
        resolved = _key_for_model(cfg.model, cfg.provider_keys, cfg.custom_providers)
        if resolved:
            cfg = dataclasses.replace(cfg, api_key=resolved)

        if request.assistant_settings and not cfg.assistant_settings:
            cfg = dataclasses.replace(cfg, assistant_settings=request.assistant_settings)

        return LLMAdapter(cfg)

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
            self._knowledge_text = kb.get_by_task_type("all")
        return self._knowledge_text

    def _load_skills(self, instruction: str) -> str:
        """Load skills relevant to instruction (loader cached)."""
        if self._skills_loader is None:
            project_root = Path(__file__).parent.parent.parent
            sk_dir = project_root / "skills"
            self._skills_loader = SkillsLoader(str(sk_dir))
            self._skills_loader.load()
        return self._skills_loader.get_for_task(instruction)


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
    """Run lightweight, non-blocking preflight analysis for modify/debug tasks."""
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


def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that LLMs sometimes include."""
    code = re.sub(r'^```[a-zA-Z]*\s*\n?', '', code.strip(), flags=re.MULTILINE)
    code = re.sub(r'\n?```\s*$', '', code.strip(), flags=re.MULTILINE)
    return code.strip()


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
