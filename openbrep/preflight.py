"""
Pre-flight Analyzer — the "read before you write" phase.

Before generating any code, the Agent should understand:
1. What sections of the XML are involved
2. What macro dependencies exist
3. What parameters are referenced in the target scripts
4. Whether the task is feasible with current context

This implements the "analyze" state of the FSM. If analysis finds
blockers (e.g., referencing a non-existent macro), it aborts early
instead of burning tokens on doomed attempts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from openbrep.context import detect_relevant_sections, slice_context, ContextSlice
from openbrep.dependencies import DependencyResolver


@dataclass
class AnalysisResult:
    """Result of pre-flight analysis."""
    feasible: bool = True
    blockers: list[str] = field(default_factory=list)    # Hard blocks
    warnings: list[str] = field(default_factory=list)    # Soft warnings
    relevant_sections: set[str] = field(default_factory=set)
    context_slice: Optional[ContextSlice] = None
    unresolved_macros: list[str] = field(default_factory=list)
    complexity: str = "simple"  # "simple" | "medium" | "complex"

    @property
    def summary(self) -> str:
        parts = [f"Complexity: {self.complexity}"]
        if self.relevant_sections:
            parts.append(f"Sections: {', '.join(sorted(self.relevant_sections))}")
        if self.context_slice and not self.context_slice.is_full:
            parts.append(f"Context savings: {self.context_slice.savings_pct}%")
        if self.unresolved_macros:
            parts.append(f"Unresolved macros: {', '.join(self.unresolved_macros)}")
        for b in self.blockers:
            parts.append(f"BLOCKER: {b}")
        for w in self.warnings:
            parts.append(f"WARNING: {w}")
        return " | ".join(parts)


class PreflightAnalyzer:
    """
    Analyzes the task before code generation begins.

    This is the FSM "analyze" state. It determines:
    - Which XML sections are relevant (for context surgery)
    - Whether macro dependencies can be resolved
    - Task complexity (for model selection hints)
    - Whether to proceed or abort
    """

    def __init__(self, resolver: Optional[DependencyResolver] = None):
        self.resolver = resolver

    def analyze(
        self,
        instruction: str,
        xml_content: str = "",
    ) -> AnalysisResult:
        """
        Run pre-flight analysis on a task.

        Args:
            instruction: User's natural language instruction.
            xml_content: Current XML source content (empty for new files).

        Returns:
            AnalysisResult with feasibility assessment and context info.
        """
        result = AnalysisResult()

        # 1. Detect relevant sections
        result.relevant_sections = detect_relevant_sections(instruction)

        # 2. Context surgery
        if xml_content:
            result.context_slice = slice_context(xml_content, instruction)

        # 3. Check macro dependencies
        if xml_content and self.resolver:
            call_names = self.resolver.extract_call_names(xml_content)
            for name in call_names:
                sig = self.resolver._resolve_macro(name)
                if sig is None:
                    result.unresolved_macros.append(name)
                    result.warnings.append(
                        f'Macro "{name}" is CALLed but not found in workspace. '
                        f"LLM will have to guess its parameters."
                    )

        # 4. Estimate complexity
        result.complexity = self._estimate_complexity(instruction, xml_content)

        # 5. Check for hard blockers
        # (Currently no hard blockers — all issues are warnings.
        #  We could add blockers for things like "file is binary, not XML")
        if xml_content and not xml_content.strip().startswith("<"):
            result.feasible = False
            result.blockers.append("Source file does not appear to be XML")

        return result

    def _estimate_complexity(self, instruction: str, xml_content: str) -> str:
        """
        Estimate task complexity for model selection and retry budget hints.

        Heuristics:
        - Simple: parameter changes, value tweaks, single-section edits
        - Medium: new parameters + geometry, multi-section changes
        - Complex: CALL macros, new objects from scratch, UI + 3D coordination
        """
        score = 0
        inst_lower = instruction.lower()

        # Instruction complexity signals
        complex_keywords = [
            "create", "new object", "from scratch", "门窗", "系统",
            "curtain wall", "幕墙", "push-pull", "推拉",
        ]
        medium_keywords = [
            "add parameter", "加参数", "geometry", "几何", "loop", "循环",
            "hotspot", "热点", "ui", "界面",
        ]

        for kw in complex_keywords:
            if kw in inst_lower:
                score += 3
        for kw in medium_keywords:
            if kw in inst_lower:
                score += 1

        # Multi-section involvement
        sections = detect_relevant_sections(instruction)
        score += max(0, len(sections) - 1) * 2

        # CALL dependencies
        if xml_content:
            calls = re.findall(r'\bCALL\s+"[^"]+"', xml_content, re.IGNORECASE)
            score += len(calls)

        # New file = inherently more complex
        if not xml_content:
            score += 2

        if score >= 5:
            return "complex"
        elif score >= 2:
            return "medium"
        return "simple"
