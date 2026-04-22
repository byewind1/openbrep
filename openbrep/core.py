"""
GDL Agent Core v0.4 — HSF-native agent loop.

The agent operates on HSFProject objects instead of raw XML strings.
Context surgery is built into HSF's file structure — each script is
a separate file, so only relevant files are fed to the LLM.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.compiler import CompileResult, HSFCompiler, MockHSFCompiler
from openbrep.paramlist_builder import validate_paramlist
from openbrep.validator import GDLValidator
from openbrep.error_classifier import ErrorCategory, ErrorClassifier
from openbrep.static_checker import StaticChecker
from openbrep.script_generator import ScriptGenerator, ScriptType as SGScriptType

logger = logging.getLogger(__name__)


class Status(Enum):
    SUCCESS   = "success"
    FAILED    = "failed"
    EXHAUSTED = "exhausted"
    BLOCKED   = "blocked"


@dataclass
class AgentResult:
    """Result of an agent run."""
    status: Status
    attempts: int = 0
    output_path: str = ""
    error_summary: str = ""
    project: Optional[HSFProject] = None
    history: list[dict] = field(default_factory=list)


class AgentCancelled(Exception):
    """Raised when the current generation is cooperatively cancelled."""


class GDLAgent:
    """
    HSF-native GDL Agent.

    Workflow:
    1. ANALYZE — Determine task type, affected scripts
    2. GENERATE — Call LLM with focused context
    3. COMPILE — Write HSF to disk, run hsf2libpart
    4. VERIFY — Check result, retry on failure
    """

    def __init__(
        self,
        llm,
        compiler=None,
        max_iterations: int = 5,
        on_event: Optional[Callable] = None,
        assistant_settings: str = "",
        should_cancel: Optional[Callable[[], bool]] = None,
    ):
        self.llm = llm
        self.compiler = compiler or MockHSFCompiler()
        self.max_iterations = max_iterations
        self.on_event = on_event or (lambda *a: None)
        self.validator = GDLValidator()
        self.auto_rewrite = False  # validator规则不足时暂时关闭，成熟后改回True
        self.error_classifier = ErrorClassifier()
        self.static_checker = StaticChecker()
        self.script_generator = ScriptGenerator(llm_caller=self._call_llm)
        self.use_context_surgery = True  # set False to fall back to single-call mode
        self.assistant_settings = assistant_settings.strip()
        self.should_cancel = should_cancel or (lambda: False)

    def _raise_if_cancelled(self) -> None:
        if self.should_cancel():
            self.on_event("cancelled", {})
            raise AgentCancelled("generation cancelled")

    def run(
        self,
        instruction: str,
        project: HSFProject,
        output_gsm: str,
        knowledge: str = "",
        skills: str = "",
    ) -> AgentResult:
        """
        Execute agent loop on an HSFProject.

        Args:
            instruction: User's natural language instruction
            project: HSFProject to modify
            output_gsm: Path for compiled .gsm output
            knowledge: Injected knowledge docs
            skills: Injected skill strategies
        """
        self.on_event("start", {
            "instruction": instruction,
            "project": project.name,
            "max_iterations": self.max_iterations,
        })

        # 1. ANALYZE
        affected = project.get_affected_scripts(instruction)
        self.on_event("analyze", {
            "affected_scripts": [s.value for s in affected],
        })

        prev_error = None
        prev_output = None
        history = []

        for attempt in range(1, self.max_iterations + 1):
            self.on_event("attempt", {"attempt": attempt})

            # 2. GENERATE
            if self.use_context_surgery:
                # Per-script independent calls (Context Surgery mode)
                sg_affected = self.script_generator.detect_affected_scripts(instruction)
                eff_instr = (
                    f"{instruction}\n\nFix previous error:\n{prev_error}"
                    if prev_error else instruction
                )
                sg_results = []
                for sg_st in sg_affected:
                    ctx_dict = self._build_script_context(sg_st, project)
                    r = self.script_generator.generate_script(
                        sg_st, eff_instr, ctx_dict, knowledge, skills
                    )
                    sg_results.append(r)
                    self.on_event("llm_response", {
                        "script": sg_st.value,
                        "length": len(r.content),
                        "success": r.success,
                    })

                if not any(r.success and r.content for r in sg_results):
                    prev_error = "ScriptGenerator produced no output — check LLM response."
                    history.append({"attempt": attempt, "stage": "generate", "error": prev_error})
                    continue

                # Build changes dict (same shape as _parse_response output)
                changes = {r.script_type.value: r.content
                           for r in sg_results if r.success and r.content}

                # Anti-loop check
                output_hash = hash(json.dumps(changes, sort_keys=True))
                if prev_output is not None and output_hash == prev_output:
                    self.on_event("anti_loop", {})
                    return AgentResult(
                        status=Status.FAILED,
                        attempts=attempt,
                        error_summary="Identical output detected, stopping",
                        project=project,
                        history=history,
                    )
                prev_output = output_hash

                # Apply params first (special path)
                params_result = next(
                    (r for r in sg_results
                     if r.script_type == SGScriptType.PARAMS and r.success and r.content),
                    None,
                )
                if params_result:
                    new_params = self._parse_param_text(params_result.content)
                    if new_params:
                        project.parameters = new_params

                # Apply remaining scripts via merge_results
                non_param = [r for r in sg_results
                             if r.script_type != SGScriptType.PARAMS and r.success]
                self.script_generator.merge_results(non_param, project)

            else:
                # Fallback: single LLM call (original behaviour)
                context = self._build_context(project, affected)
                messages = self._build_messages(
                    instruction, context, knowledge, skills, prev_error
                )
                raw_response = self.llm.generate(messages)
                if isinstance(raw_response, str):
                    response = raw_response
                elif hasattr(raw_response, 'content'):
                    response = raw_response.content
                else:
                    response = str(raw_response)
                self.on_event("llm_response", {"length": len(response)})

                changes = self._parse_response(response)
                if not changes:
                    history.append({
                        "attempt": attempt,
                        "stage": "parse",
                        "error": "LLM output could not be parsed into file changes",
                    })
                    prev_error = "Your output could not be parsed. Use [FILE: path] format."
                    continue

                output_hash = hash(json.dumps(changes, sort_keys=True))
                if prev_output is not None and output_hash == prev_output:
                    self.on_event("anti_loop", {})
                    return AgentResult(
                        status=Status.FAILED,
                        attempts=attempt,
                        error_summary="Identical output detected, stopping",
                        project=project,
                        history=history,
                    )
                prev_output = output_hash

                self._apply_changes(project, changes)

            # Validate parameters
            param_issues = validate_paramlist(project.parameters)
            if param_issues:
                err = "Parameter validation errors:\n" + "\n".join(param_issues)
                history.append({
                    "attempt": attempt,
                    "stage": "validate",
                    "error": err,
                })
                prev_error = err
                self.on_event("validation_error", {"errors": param_issues})
                continue

            # 3. STATIC CHECK — fast pre-compile analysis (no disk write needed)
            static_result = self.static_checker.check(project)
            if not static_result.passed:
                hint = "\n".join(
                    f"[{e.check_type}] {e.file}: {e.detail}"
                    for e in static_result.errors
                )
                logger.debug(f"StaticCheck: {static_result.errors}")
                self.on_event("static_check_error", {"errors": [e.detail for e in static_result.errors]})
                history.append({"attempt": attempt, "stage": "static_check", "error": hint})
                prev_error = hint
                continue

            # 4. COMPILE — Write to disk and compile
            hsf_dir = project.save_to_disk()
            self.on_event("compile_start", {"hsf_dir": str(hsf_dir)})

            result = self.compiler.hsf2libpart(str(hsf_dir), output_gsm)

            if result.success:
                self.on_event("success", {
                    "attempt": attempt,
                    "output": output_gsm,
                })
                history.append({
                    "attempt": attempt,
                    "stage": "compile",
                    "result": "success",
                })
                return AgentResult(
                    status=Status.SUCCESS,
                    attempts=attempt,
                    output_path=output_gsm,
                    project=project,
                    history=history,
                )

            # 4. Compile failed — classify error and prepare targeted feedback
            error_msg = result.stderr or result.stdout or ""
            self.on_event("compile_error", {
                "attempt": attempt,
                "error": error_msg,
            })
            history.append({
                "attempt": attempt,
                "stage": "compile",
                "error": error_msg,
            })

            error_case = self.error_classifier.classify(error_msg)
            logger.debug(f"ErrorCase: {error_case}")

            if error_case.category != ErrorCategory.UNKNOWN:
                # Inject targeted hint instead of raw stderr dump
                target_info = f" (file: {error_case.target_file})" if error_case.target_file else ""
                prev_error = (
                    f"Compile error [{error_case.category.value}]{target_info}:\n"
                    f"{error_case.hint}\n\n"
                    f"Raw error:\n{error_msg}"
                )
            else:
                prev_error = error_msg

        # Exhausted
        return AgentResult(
            status=Status.EXHAUSTED,
            attempts=self.max_iterations,
            error_summary=prev_error or "Unknown error",
            project=project,
            history=history,
        )

    def generate_only(
        self,
        instruction: str,
        project: HSFProject,
        knowledge: str = "",
        skills: str = "",
        include_all_scripts: bool = False,
        last_code_context: Optional[str] = None,
        syntax_report: str = "",
        history: Optional[list] = None,
        image_b64: Optional[str] = None,
        image_mime: str = "image/png",
    ) -> tuple[dict, str]:
        """
        Generate code changes OR plain-text analysis WITHOUT compiling.

        Returns (file_changes, plain_text):
          - file_changes: {fpath: content} if LLM wrote [FILE: ...] blocks
          - plain_text:   raw LLM reply if no [FILE: ...] blocks (debug/analysis mode)
        Both can be non-empty if LLM mixes analysis text with code fixes.

        last_code_context: raw content of last assistant message (for [DEBUG:last] mode).
        """
        if image_b64 and history:
            history = history[-4:]

        self._raise_if_cancelled()
        affected = project.get_affected_scripts(instruction)
        self.on_event("analyze", {"affected_scripts": [s.value for s in affected]})
        self.on_event("attempt", {"attempt": 1})

        self._raise_if_cancelled()
        context = self._build_context(
            project, affected,
            include_all=include_all_scripts,
            last_code_context=last_code_context,
        )
        chat_mode = include_all_scripts or (last_code_context is not None)
        messages = self._build_messages(
            instruction, context, knowledge, skills,
            error=None, history=history,
            chat_mode=chat_mode,
            syntax_report=syntax_report,
        )

        if image_b64:
            # generate_with_image 接口不支持多条历史消息，
            # 这里将 system 之外的上下文压平成一段文本，保留脚本上下文与历史对话。
            flattened_parts = []
            for msg in messages[1:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    flattened_parts.append(f"[{role}]\n{content}")

            image_prompt = "\n\n".join(flattened_parts + [
                "请结合这张调试截图（Archicad报错/视图）与以上上下文进行分析。"
            ])
            raw = self.llm.generate_with_image(
                text_prompt=image_prompt,
                image_b64=image_b64,
                image_mime=image_mime,
                system_prompt=messages[0]["content"],
                max_tokens=4096,
            )
        else:
            raw = self.llm.generate(messages)
        self._raise_if_cancelled()
        response = raw.content if hasattr(raw, "content") else str(raw)

        self.on_event("llm_response", {"length": len(response)})
        changes = self._parse_response(response)

        validation_feedback = ""
        validation_warnings: list[str] = []

        if changes:
            work_project = deepcopy(project)
            self._apply_changes(work_project, changes)
            all_issues = self.validator.validate_all_issues(work_project)
            validation_errors = [i.message for i in all_issues if i.level == "error"]
            validation_warnings = [i.message for i in all_issues if i.level == "warning"]
            self.on_event("validate", {
                "errors": validation_errors,
                "warnings": validation_warnings,
            })
            self._raise_if_cancelled()

            if validation_errors and self.auto_rewrite:
                rewrite_reason = "上次生成存在以下问题，请修复后重新输出完整脚本：\n" + "\n".join(validation_errors)
                self.on_event("rewrite", {"reason": rewrite_reason})
                self._raise_if_cancelled()

                rewrite_messages = self._build_messages(
                    instruction, context, knowledge, skills,
                    error=rewrite_reason,
                    history=history,
                    chat_mode=chat_mode,
                    syntax_report=syntax_report,
                )
                rewrite_raw = self.llm.generate(rewrite_messages)
                self._raise_if_cancelled()
                rewrite_response = rewrite_raw.content if hasattr(rewrite_raw, "content") else str(rewrite_raw)
                self.on_event("llm_response", {"length": len(rewrite_response), "rewrite": True})

                rewritten_changes = self._parse_response(rewrite_response)
                if rewritten_changes:
                    changes = rewritten_changes
                    response = rewrite_response

                    rewritten_project = deepcopy(project)
                    self._apply_changes(rewritten_project, changes)
                    second_issues = self.validator.validate_all_issues(rewritten_project)
                    second_errors = [i.message for i in second_issues if i.level == "error"]
                    second_warnings = [i.message for i in second_issues if i.level == "warning"]
                    self.on_event("validate", {
                        "errors": second_errors,
                        "warnings": second_warnings,
                    })

                    if second_errors:
                        # 第三轮：专项修复跨脚本逻辑一致性
                        cross_script_prompt = (
                            "请检查并修复以下跨脚本一致性问题，重新输出完整脚本：\n"
                            "1. 3D 脚本中使用的参数变量，必须在参数表中有对应定义，类型和默认值合理\n"
                            "2. Master 脚本中的参数赋值逻辑，必须与参数表定义和 3D 脚本使用一致\n"
                            "3. 参数表中的参数顺序和分组，需符合 GDL 规范\n"
                            "4. 修复以下校验错误：\n" + "\n".join(second_errors)
                        )
                        self.on_event("rewrite", {"reason": cross_script_prompt, "round": 3})
                        self._raise_if_cancelled()

                        third_messages = self._build_messages(
                            instruction, context, knowledge, skills,
                            error=cross_script_prompt,
                            history=history,
                            chat_mode=chat_mode,
                            syntax_report=syntax_report,
                        )
                        third_raw = self.llm.generate(third_messages)
                        self._raise_if_cancelled()
                        third_response = third_raw.content if hasattr(third_raw, "content") else str(third_raw)
                        self.on_event("llm_response", {"length": len(third_response), "rewrite": True})

                        third_changes = self._parse_response(third_response)
                        if third_changes:
                            changes = third_changes
                            response = third_response

                            third_project = deepcopy(project)
                            self._apply_changes(third_project, changes)
                            third_issues = self.validator.validate_all_issues(third_project)
                            third_errors = [i.message for i in third_issues if i.level == "error"]
                            third_warnings = [i.message for i in third_issues if i.level == "warning"]
                            self.on_event("validate", {
                                "errors": third_errors,
                                "warnings": third_warnings,
                            })
                            validation_warnings = third_warnings
                            if third_errors:
                                validation_feedback = "⚠️ 三轮校验后仍有问题：\n- " + "\n- ".join(third_errors)
                        else:
                            validation_feedback = "⚠️ 第三轮重写未返回可解析脚本，保留第二轮结果。"
                            validation_warnings = second_warnings
                    else:
                        validation_warnings = second_warnings
                    # 二轮通过则 validation_feedback 保持空字符串
                else:
                    validation_feedback = "⚠️ 自动重写未返回可解析脚本，保留首次生成结果。"

        # Plain text = everything BEFORE the first [FILE: ...] block (or full response if none)
        first_file = response.find("[FILE:")
        plain_text = response[:first_file].strip() if first_file > 0 else (response.strip() if not changes else "")
        if validation_feedback:
            plain_text = f"{plain_text}\n\n{validation_feedback}".strip()
        if validation_warnings:
            warning_text = "⚠️ 建议检查：\n- " + "\n- ".join(validation_warnings)
            plain_text = f"{plain_text}\n\n{warning_text}".strip()
        return changes, plain_text

    # ── LLM wrapper (for ScriptGenerator) ────────────────

    def _call_llm(self, messages: list[dict]) -> str:
        raw = self.llm.generate(messages)
        return raw.content if hasattr(raw, "content") else str(raw)

    # ── Script context builder (Context Surgery) ──────────

    def _build_script_context(self, script_type: SGScriptType, project: HSFProject) -> dict:
        """
        Return {file_path: content} for generating one specific script.

        Always includes paramlist.xml.
        3d/2d also receive 1d.gdl (for derived variable context).
        Never includes unrelated scripts.
        """
        # Param text
        param_lines = [
            f"{p.type_tag} {p.name} = {p.value}  ! {p.description}"
            + (" [FIXED]" if p.is_fixed else "")
            for p in project.parameters
        ]
        ctx: dict = {"paramlist.xml": "\n".join(param_lines)}

        def _get(hsf_value: str) -> str:
            for st in ScriptType:
                if st.value == hsf_value:
                    return project.get_script(st) or ""
            return ""

        if script_type == SGScriptType.PARAMS:
            pass  # paramlist only

        elif script_type == SGScriptType.MASTER:
            ctx["scripts/1d.gdl"] = _get("1d.gdl")

        elif script_type in (SGScriptType.SCRIPT_3D, SGScriptType.SCRIPT_2D):
            ctx["scripts/1d.gdl"] = _get("1d.gdl")
            hsf_val = script_type.value.replace("scripts/", "")
            ctx[script_type.value] = _get(hsf_val)

        elif script_type == SGScriptType.PARAM_SCRIPT:
            ctx[script_type.value] = _get("vl.gdl")

        elif script_type == SGScriptType.UI_SCRIPT:
            ctx[script_type.value] = _get("ui.gdl")

        return ctx

    # ── Context Building ──────────────────────────────────

    def _build_context(
        self, project: HSFProject, affected: list[ScriptType],
        include_all: bool = False,
        last_code_context: Optional[str] = None,
    ) -> str:
        """Build focused context from project state.

        include_all=True: inject every non-empty script (for debug/analysis).
        include_all=False: inject only 'affected' scripts (for generation).
        last_code_context: if set, inject as "last AI output" block instead of editor scripts.
        """
        parts = []

        if last_code_context is not None:
            # [DEBUG:last] mode: show last AI-generated code, not editor state
            parts.append("=== Last AI-generated code (subject of this debug session) ===")
            parts.append(last_code_context)
            # Still include current params for reference
            parts.append("\n=== Current Parameters (editor) ===")
            if project.parameters:
                for p in project.parameters:
                    fixed = " [FIXED]" if p.is_fixed else ""
                    parts.append(f"  {p.type_tag} {p.name} = {p.value}  ! {p.description}{fixed}")
            else:
                parts.append("  (none)")
            return "\n".join(parts)

        # Always include paramlist
        parts.append("=== Parameters ===")
        if project.parameters:
            for p in project.parameters:
                fixed = " [FIXED]" if p.is_fixed else ""
                parts.append(f"  {p.type_tag} {p.name} = {p.value}  ! {p.description}{fixed}")
        else:
            parts.append("  (none)")

        # Script selection
        script_types = list(ScriptType) if include_all else affected
        for script_type in script_types:
            content = project.get_script(script_type)
            if include_all and not content:
                continue   # skip empty scripts in full-dump mode
            if content:
                parts.append(f"\n=== {script_type.value} ===")
                parts.append(content)
            else:
                parts.append(f"\n=== {script_type.value} === (empty)")

        return "\n".join(parts)

    def _build_messages(
        self,
        instruction: str,
        context: str,
        knowledge: str,
        skills: str,
        error: Optional[str],
        history: Optional[list] = None,
        chat_mode: bool = False,
        syntax_report: str = "",
    ) -> list[dict]:
        """Build LLM message list.

        chat_mode=True: prepend recent history; allow plain-text analysis reply.
        history: list of {"role": "user"/"assistant", "content": str} from UI.
        """
        system = self._build_system_prompt(knowledge, skills, chat_mode=chat_mode)
        messages = [{"role": "system", "content": system}]

        # Inject recent conversation history (last 6 turns) for multi-turn context
        if history:
            for msg in history[-6:]:
                role = msg.get("role")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content.strip():
                    if role == "assistant" and "```" in content:
                        # Replace code blocks with placeholder to save tokens.
                        # Current scripts are always injected fresh via context,
                        # so omitting history code does NOT lose information.
                        import re as _re
                        content = _re.sub(
                            r"```[a-zA-Z]*\n.*?```",
                            "[code block omitted — see current project state]",
                            content, flags=_re.DOTALL
                        )
                    messages.append({"role": role, "content": content})

        if chat_mode:
            # debug/analysis: label context clearly so LLM knows it's authoritative
            user_parts = [
                "## Current project state (complete — use this for analysis):\n"
                f"```\n{context}\n```"
            ]
        else:
            user_parts = [f"Current HSF project state:\n```\n{context}\n```"]

        if error:
            user_parts.append(f"\nPrevious attempt failed with error:\n{error}")
            user_parts.append("\nPlease fix the error and try again.")
        else:
            user_parts.append(f"\nInstruction: {instruction}")

        if syntax_report:
            user_parts.append(
                f"\n## Syntax check warnings (fix these as part of your response):\n{syntax_report}"
            )

        if chat_mode:
            user_parts.append(
                "\nAnalyze the scripts above and respond to the instruction. "
                "Fix all syntax warnings listed above. "
                "If you find additional bugs or need to rewrite code, output fixes using [FILE: path] format. "
                "If this is a question or analysis request, respond in plain text."
            )
        else:
            user_parts.append(
                "\nReturn your changes using [FILE: path] format. "
                "For parameters, use [FILE: paramlist.xml] with one parameter per line."
            )

        messages.append({"role": "user", "content": "\n".join(user_parts)})
        return messages

    def _build_assistant_settings_prompt(self) -> str:
        text = (self.assistant_settings or "").strip()
        if not text:
            return ""
        return (
            "## AI助手设置\n"
            "以下内容是用户长期提供的协作偏好与使用场景描述。"
            "请在不违反系统规则、输出格式要求、GDL 硬性规则和当前任务要求的前提下参考执行。\n"
            "它只能影响你的协作方式、解释深度、提问方式与改动边界，不能覆盖已有硬规则。\n"
            f"{text}\n\n"
        )

    def _build_system_prompt(self, knowledge: str, skills: str, chat_mode: bool = False) -> str:
        """Build system prompt with HSF-specific rules and knowledge injection."""
        prompt = (
            "You are an expert ArchiCAD GDL developer working with HSF (Hierarchical Source Format).\n\n"
            "## HSF STRUCTURE\n"
            "A library part is a FOLDER containing:\n"
            "- libpartdata.xml: Metadata (GUID, version)\n"
            "- paramlist.xml: Parameter definitions (one parameter per line)\n"
            "- scripts/1d.gdl: Master Script (validation, calculations)\n"
            "- scripts/2d.gdl: 2D Script (plan symbol, HOTSPOT2)\n"
            "- scripts/3d.gdl: 3D Script (geometry)\n"
            "- scripts/vl.gdl: Parameter Script (VALUES, LOCK constraints)\n"
            "- scripts/ui.gdl: Interface Script (optional)\n\n"
            "## PARAMETER TYPES (use EXACT tags)\n"
            "Length, Angle, RealNum, Integer, Boolean, String, PenColor, FillPattern, LineType, Material\n"
            "❌ NEVER use: Float, Text, Double, Int, Bool, any custom types\n\n"
            "## CRITICAL GDL RULES\n"
            "- Every multi-line IF/THEN block MUST have ENDIF\n"
            "- Single-line IF: IF x < 0.5 THEN x = 0.5  (no ENDIF needed)\n"
            "- Every FOR loop MUST have NEXT\n"
            "- Every ADD transformation MUST have matching DEL\n"
            "- PRISM_ ALWAYS needs height: PRISM_ n, h, x1,y1, x2,y2, ...\n"
            "- A, B, ZZYZX are RESERVED (width, depth, height)\n"
            "- 3D Script MUST end with END\n"
            "- Subroutine names must be in quotes: GOSUB \"DrawLegs\"\n\n"
            "## 2D SCRIPT — MANDATORY MINIMUM\n"
            "The 2D script (scripts/2d.gdl) MUST always include at minimum:\n"
            "  PROJECT2 3, 270, 2\n"
            "This projects the 3D geometry onto the floor plan. Without it the object\n"
            "is invisible in ArchiCAD's 2D plan view — making it unusable.\n"
            "If the object has bounding parameters A and B, also add bounding box and hotspots:\n"
            "  [FILE: scripts/2d.gdl]\n"
            "  HOTSPOT2 0, 0\n"
            "  HOTSPOT2 A, 0\n"
            "  HOTSPOT2 0, B\n"
            "  HOTSPOT2 A, B\n"
            "  PROJECT2 3, 270, 2\n"
            "NEVER leave scripts/2d.gdl empty.\n\n"
            "## OUTPUT FORMAT - CRITICAL\n"
            "Return changes using [FILE: path] format. Each file section ends when next [FILE:] appears.\n\n"
            "For GDL scripts (scripts/1d.gdl, scripts/2d.gdl, scripts/3d.gdl, etc):\n"
            "[FILE: scripts/3d.gdl]\n"
            "BLOCK A, B, ZZYZX\n"
            "ADD 0, 0, ZZYZX\n"
            "  BLOCK 0.1, 0.1, 0.1\n"
            "DEL 1\n\n"
            "For parameters (paramlist.xml), list one parameter per line:\n"
            "[FILE: paramlist.xml]\n"
            "Length A = 0.60 ! Shelf width\n"
            "Length B = 0.40 ! Shelf depth\n"
            "Length ZZYZX = 0.80 ! Total height\n"
            "Integer iShelves = 3 ! Number of shelves\n"
            "Boolean bHasBack = 1 ! Has back panel\n\n"
            "Do NOT include XML tags in [FILE: paramlist.xml] — just parameter lines.\n"
            "Do NOT use markdown code fences (```). Output raw GDL code directly.\n"
            "In each [FILE: scripts/*.gdl] block, output ONLY pure GDL statements and GDL comments (! ...).\n"
            "Never include prose, markdown headings, bullets, or explanatory text inside script blocks.\n\n"
        )

        if chat_mode:
            prompt += (
                "## RESPONSE MODE\n"
                "You are in DEBUG mode. 你的任务是：基于原脚本做最小改动，输出修复后的完整脚本。\n\n"
                "严格遵守以下规则：\n"
                "1. 必须输出完整脚本（用户需要直接可用的结果，不是补丁）\n"
                "2. 只改有问题的行，其余行原样保留，不得改动用户原有结构和注释\n"
                "3. 在改动行附近加注释说明改了什么：! Fixed: 原因\n"
                "4. 用 [FILE: path] 格式输出所有改动过的脚本文件\n"
                "5. 没有问题的脚本不需要输出\n"
                "6. 先用中文简要说明发现了什么问题、改了哪里，再输出 [FILE: ...] 块\n\n"
                "## FULL-SCRIPT DEBUG CHECKLIST\n"
                "全检查时，按以下顺序逐项检查，每项结论用✅或❌标注：\n"
                "1. paramlist.xml — 参数名/类型是否合法？有无重复？\n"
                "2. 1d.gdl (Master) — 变量计算是否引用了已声明参数？有无未定义变量？\n"
                "3. 3d.gdl — FOR/NEXT配对？IF/ENDIF配对？末尾有END？\n"
                "4. 2d.gdl — 有无绘图命令？HOTSPOT2是否存在？\n"
                "5. 跨脚本一致性 — 3d/2d脚本引用的变量是否在参数表或Master中定义？\n"
                "发现问题后，输出修复后的完整脚本（只含有改动的文件）。\n"
                "若无问题，明确说明✅全部通过，不输出任何 [FILE: ...] 块。\n\n"
            )

        prompt += self._build_assistant_settings_prompt()

        if knowledge:
            prompt += f"## REFERENCE DOCUMENTATION\n{knowledge}\n\n"
        if skills:
            prompt += f"## TASK STRATEGY\n{skills}\n\n"

        if chat_mode:
            prompt += "Now, read the current HSF project state and help the user debug or analyze their scripts."
        else:
            prompt += "Now, read the current HSF project state and make the requested changes."

        return prompt

    # ── Response Parsing ──────────────────────────────────

    def _parse_response(self, response: str) -> dict[str, str]:
        """
        Parse LLM response into file changes.

        Expected format:
        [FILE: scripts/3d.gdl]
        BLOCK A, B, ZZYZX

        [FILE: paramlist.xml]
        Length bShelfWidth = 0.80  ! Shelf width
        """
        changes = {}
        current_file = None
        current_lines = []

        for line in response.splitlines():
            stripped = line.strip()

            # Check for file header
            file_match = _FILE_HEADER_RE.match(stripped)
            if file_match:
                # Save previous file
                if current_file and current_lines:
                    changes[current_file] = "\n".join(current_lines).strip()
                current_file = file_match.group(1).strip()
                current_lines = []
                continue

            # Skip markdown code fences
            if stripped.startswith("```"):
                continue

            if current_file is not None:
                current_lines.append(line)

        # Save last file
        if current_file and current_lines:
            changes[current_file] = "\n".join(current_lines).strip()

        return changes

    def _apply_changes(self, project: HSFProject, changes: dict[str, str]) -> None:
        """Apply parsed changes to HSFProject."""
        for file_path, content in changes.items():
            # Parameter changes
            if "paramlist" in file_path.lower():
                new_params = self._parse_param_text(content)
                if new_params:
                    project.parameters = new_params
                continue

            # Script changes
            for script_type in ScriptType:
                if script_type.value in file_path:
                    project.scripts[script_type] = content + "\n"
                    break

    def _parse_param_text(self, text: str) -> list[GDLParameter]:
        """Parse simplified parameter text format from LLM output."""
        import re

        def _normalize_length_value(raw: str) -> str:
            token = str(raw or "").strip().lower()
            if token.endswith("mm"):
                num = token[:-2].strip()
                try:
                    return f"{float(num) / 1000.0:.6g}"
                except ValueError:
                    return raw
            try:
                value = float(token)
            except ValueError:
                return raw
            if abs(value) >= 20:
                return f"{value / 1000.0:.6g}"
            return f"{value:.6g}"

        params = []
        pattern = re.compile(
            r'^(Length|Angle|RealNum|Integer|Boolean|String|Material|'
            r'FillPattern|LineType|PenColor)\s+'
            r'(\w+)\s*=\s*("[^"]*"|\S+)'
            r'(?:\s+!\s*(.+))?',
            re.IGNORECASE
        )

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("!"):
                continue

            match = pattern.match(stripped)
            if match:
                type_tag = match.group(1)
                name = match.group(2)
                value = match.group(3).strip('"')
                if type_tag.lower() == "length":
                    value = _normalize_length_value(value)
                desc = (match.group(4) or "").strip()
                is_fixed = name in ("A", "B", "ZZYZX")

                params.append(GDLParameter(
                    name=name,
                    type_tag=type_tag,
                    description=desc,
                    value=value,
                    is_fixed=is_fixed,
                ))

        return params


# Regex for [FILE: path] headers
_FILE_HEADER_RE = __import__("re").compile(
    r'^\[FILE:\s*(.+?)\]\s*$', __import__("re").IGNORECASE
)
