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

from gdl_agent.hsf_project import HSFProject, ScriptType, GDLParameter
from gdl_agent.compiler import CompileResult, HSFCompiler, MockHSFCompiler
from gdl_agent.paramlist_builder import validate_paramlist


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

    # ── Context Building ──────────────────────────────────

    def _build_context(
        self, project: HSFProject, affected: list[ScriptType]
    ) -> str:
        """Build focused context from project state."""
        parts = []

        # Always include paramlist
        parts.append("=== Parameters ===")
        for p in project.parameters:
            fixed = " [FIXED]" if p.is_fixed else ""
            parts.append(f"  {p.type_tag} {p.name} = {p.value}  ! {p.description}{fixed}")

        # Include affected scripts only
        for script_type in affected:
            content = project.get_script(script_type)
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
    ) -> list[dict]:
        """Build LLM message list."""
        system = self._build_system_prompt(knowledge, skills)

        user_parts = [f"Current HSF project state:\n```\n{context}\n```"]

        if error:
            user_parts.append(f"\nPrevious attempt failed with error:\n{error}")
            user_parts.append("\nPlease fix the error and try again.")
        else:
            user_parts.append(f"\nInstruction: {instruction}")

        user_parts.append(
            "\nReturn your changes using [FILE: path] format. "
            "For parameters, use [FILE: paramlist.xml] with one parameter per line."
        )

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    def _build_system_prompt(self, knowledge: str, skills: str) -> str:
        """Build system prompt with HSF-specific rules."""
        prompt = (
            "You are an expert ArchiCAD GDL developer working with HSF format.\n\n"
            "HSF STRUCTURE:\n"
            "A library part is a FOLDER containing:\n"
            "- libpartdata.xml: Metadata (GUID, version)\n"
            "- paramlist.xml: Parameter definitions\n"
            "- scripts/1d.gdl: Master Script\n"
            "- scripts/2d.gdl: 2D Script (plan symbol)\n"
            "- scripts/3d.gdl: 3D Script (3D model)\n"
            "- scripts/vl.gdl: Parameter Script (VALUES, LOCK)\n"
            "- scripts/ui.gdl: Interface Script\n\n"
            "PARAMETER TYPES (use EXACT tags):\n"
            "Length, Angle, RealNum, Integer, Boolean, String, "
            "PenColor, FillPattern, LineType, Material\n"
            "NEVER use: Float, Text, Double, Int, Bool\n\n"
            "CRITICAL RULES:\n"
            "- Every multi-line IF needs ENDIF\n"
            "- Every FOR needs NEXT\n"
            "- Every ADD needs matching DEL\n"
            "- PRISM_ always needs height: PRISM_ n, h, x1,y1,...\n"
            "- A, B, ZZYZX are reserved (width, depth, height)\n\n"
            "OUTPUT FORMAT:\n"
            "Return code blocks with [FILE: path] headers:\n"
            "[FILE: scripts/3d.gdl]\n"
            "BLOCK A, B, ZZYZX\n\n"
            "[FILE: paramlist.xml]\n"
            "Length bShelfWidth = 0.80  ! Shelf width\n"
        )

        if knowledge:
            prompt += f"\nREFERENCE KNOWLEDGE:\n{knowledge}\n"
        if skills:
            prompt += f"\nTASK STRATEGY:\n{skills}\n"

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
