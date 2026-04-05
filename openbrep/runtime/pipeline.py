"""
TaskPipeline — unified task execution pipeline for OpenBrep.

Phase 1: thin wrapper around GDLAgent.generate_only().
- No Streamlit dependencies
- Usable from CLI, tests, and future API server
- app.py continues to use GDLAgent directly for now (Strangler Fig)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from openbrep.compiler import CompileResult, HSFCompiler, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import HSFProject
from openbrep.knowledge import KnowledgeBase
from openbrep.llm import LLMAdapter
from openbrep.skills_loader import SkillsLoader
from openbrep.runtime.router import IntentRouter
from openbrep.runtime.tracer import Tracer

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
        try:
            resp = llm.generate([
                {"role": "system", "content": system_content},
                {"role": "user", "content": request.user_input},
            ])
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
        image_b64: Optional[str] = None
        image_mime = "image/png"
        if request.image_path:
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
        )

        changes, plain_text = agent.generate_only(
            instruction=request.user_input,
            project=project,
            knowledge=knowledge,
            skills=skills_text,
            include_all_scripts=debug_mode,
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

    # ── Initialization Helpers ────────────────────────────

    def _make_llm(self, request: TaskRequest) -> LLMAdapter:
        """Build LLMAdapter, optionally injecting assistant_settings from request."""
        import dataclasses
        cfg = self.config.llm
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


# ── Utilities ─────────────────────────────────────────────

def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that LLMs sometimes include."""
    code = re.sub(r'^```[a-zA-Z]*\s*\n?', '', code.strip(), flags=re.MULTILINE)
    code = re.sub(r'\n?```\s*$', '', code.strip(), flags=re.MULTILINE)
    return code.strip()
