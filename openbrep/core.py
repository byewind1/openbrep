"""
GDL Agent Core v0.4 — HSF-native agent loop.

The agent operates on HSFProject objects instead of raw XML strings.
Context surgery is built into HSF's file structure — each script is
a separate file, so only relevant files are fed to the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.compiler import CompileResult, HSFCompiler, MockHSFCompiler
from openbrep.paramlist_builder import validate_paramlist


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
    ):
        self.llm = llm
        self.compiler = compiler or MockHSFCompiler()
        self.max_iterations = max_iterations
        self.on_event = on_event or (lambda *a: None)

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

            # 2. GENERATE — Build focused context
            context = self._build_context(project, affected)
            messages = self._build_messages(
                instruction, context, knowledge, skills, prev_error
            )

            # Call LLM
            raw_response = self.llm.generate(messages)
            # Handle both string (MockLLM in tests) and LLMResponse objects
            if isinstance(raw_response, str):
                response = raw_response
            elif hasattr(raw_response, 'content'):
                response = raw_response.content
            else:
                response = str(raw_response)
            self.on_event("llm_response", {"length": len(response)})

            # Parse LLM output → apply to project
            changes = self._parse_response(response)
            if not changes:
                history.append({
                    "attempt": attempt,
                    "stage": "parse",
                    "error": "LLM output could not be parsed into file changes",
                })
                prev_error = "Your output could not be parsed. Use [FILE: path] format."
                continue

            # Anti-loop: check for identical output
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

            # Apply changes to project
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

            # 3. COMPILE — Write to disk and compile
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

            # 4. Compile failed — prepare error feedback
            error_msg = result.stderr
            self.on_event("compile_error", {
                "attempt": attempt,
                "error": error_msg,
            })
            history.append({
                "attempt": attempt,
                "stage": "compile",
                "error": error_msg,
            })
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
    ) -> tuple[dict, str]:
        """
        Generate code changes OR plain-text analysis WITHOUT compiling.

        Returns (file_changes, plain_text):
          - file_changes: {fpath: content} if LLM wrote [FILE: ...] blocks
          - plain_text:   raw LLM reply if no [FILE: ...] blocks (debug/analysis mode)
        Both can be non-empty if LLM mixes analysis text with code fixes.

        last_code_context: raw content of last assistant message (for [DEBUG:last] mode).
        """
        affected = project.get_affected_scripts(instruction)
        self.on_event("analyze", {"affected_scripts": [s.value for s in affected]})
        self.on_event("attempt", {"attempt": 1})

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

        raw = self.llm.generate(messages)
        response = raw.content if hasattr(raw, "content") else str(raw)

        self.on_event("llm_response", {"length": len(response)})
        changes = self._parse_response(response)
        # Plain text = everything BEFORE the first [FILE: ...] block (or full response if none)
        first_file = response.find("[FILE:")
        plain_text = response[:first_file].strip() if first_file > 0 else (response.strip() if not changes else "")
        return changes, plain_text

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
            "Do NOT use markdown code fences (```). Output raw GDL code directly.\n\n"
        )

        if chat_mode:
            prompt += (
                "## RESPONSE MODE\n"
                "The user may ask you to debug, analyze, or explain the current scripts.\n"
                "- If you can write a concrete fix → use [FILE: path] format as above.\n"
                "- If you're analyzing, explaining, or need more info → respond in plain Chinese.\n"
                "- You may combine both: write analysis in Chinese first, then [FILE: ...] blocks for the fix.\n\n"
                "## FULL-SCRIPT DEBUG CHECKLIST\n"
                "When asked to do a full check, inspect ALL of the following in order:\n"
                "1. paramlist.xml — are all parameter names/types valid? Any duplicates?\n"
                "2. scripts/1d.gdl (Master) — do all calculations use declared parameters? Any undefined variables?\n"
                "3. scripts/3d.gdl — ADD/DEL balanced? FOR/NEXT paired? IF/ENDIF paired? Ends with END?\n"
                "4. scripts/2d.gdl — contains PROJECT2? HOTSPOT2 present?\n"
                "5. Cross-script consistency — do 3d/2d scripts reference parameters that exist in paramlist?\n"
                "   Do they use variables computed in Master script correctly?\n"
                "Report findings in Chinese, grouped by script. If no issues found, say so explicitly.\n\n"
            )

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
