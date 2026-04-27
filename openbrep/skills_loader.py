"""
Skills loader for GDL Agent.

Skills are task-specific prompt strategies stored as Markdown files
in the skills/ directory. Unlike knowledge/ (which provides reference facts),
skills/ provides methodology — HOW the LLM should approach a task.

The skills/ directory is intentionally shipped empty (with README only).
Users develop their own skills based on their ArchiCAD version,
coding standards, and observed LLM behavior.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Task type → skill filename mapping
# Users can add their own skill files; these are the default mappings
_TASK_SKILL_MAP: dict[str, list[str]] = {
    "create":   ["create_object"],
    "modify":   ["modify_parameter"],
    "debug":    ["fix_compile_error"],
    "optimize": ["optimize_geometry"],
    "2d":       ["create_2d_symbol"],
    "ui":       ["create_ui_panel"],
}

_DEFAULT_SKILL_NAMES = {name for names in _TASK_SKILL_MAP.values() for name in names}

# Keywords that trigger specific task types
_TASK_KEYWORDS: dict[str, list[str]] = {
    "create":   ["create", "new", "build", "generate", "从零", "新建", "创建", "生成"],
    "modify":   ["add", "change", "modify", "update", "增加", "修改", "添加", "调整"],
    "debug":    ["fix", "error", "bug", "repair", "修复", "报错", "错误", "修正"],
    "optimize": ["optimize", "improve", "simplify", "performance", "优化", "简化", "性能"],
    "2d":       ["2d", "plan", "symbol", "平面", "符号"],
    "ui":       ["ui", "panel", "dialog", "界面", "面板", "对话框"],
}


class SkillsLoader:
    """
    Loads and manages prompt engineering skills.

    Skills are Markdown files in the skills/ directory that contain
    task-specific strategies, rules, examples, and pitfall warnings.
    """

    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, str] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all .md files from the skills directory (excluding README)."""
        self._skills.clear()

        if not self.skills_dir.exists():
            self._loaded = True
            return

        for md_file in sorted(self.skills_dir.glob("*.md")):
            if md_file.stem.upper() == "README":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                self._skills[md_file.stem] = content
            except Exception:
                continue

        self._loaded = True

    def detect_task_type(self, instruction: str) -> list[str]:
        """
        Detect task types from user instruction.

        Returns list of task type strings (e.g., ["create"], ["modify", "2d"]).
        """
        instruction_lower = instruction.lower()
        detected = []

        for task_type, keywords in _TASK_KEYWORDS.items():
            if any(kw in instruction_lower for kw in keywords):
                detected.append(task_type)

        return detected if detected else ["modify"]  # default

    def get_for_task(self, instruction: str, error: Optional[str] = None) -> str:
        """
        Get relevant skills for a task.

        Args:
            instruction: User's instruction text.
            error: Compile error message (if retrying).

        Returns:
            Concatenated skill content, or empty string if no skills found.
        """
        if not self._loaded:
            self.load()

        if not self._skills:
            return ""

        # Detect task types
        task_types = self.detect_task_type(instruction)

        # If retrying with an error, always include debug skill
        if error and "debug" not in task_types:
            task_types.append("debug")

        # Collect matching skill files
        skill_names: list[str] = []
        for task_type in task_types:
            if task_type in _TASK_SKILL_MAP:
                skill_names.extend(_TASK_SKILL_MAP[task_type])

        # Also check for exact filename matches
        # (user might have custom skills like "curtain_wall.md")
        for word in instruction.lower().split():
            if len(word) > 3 and word in self._skills:
                if word not in skill_names:
                    skill_names.append(word)

        for name in self._match_custom_skills(instruction, set(skill_names)):
            skill_names.append(name)

        # Load and concatenate
        parts = []
        seen = set()
        for name in skill_names:
            if name in self._skills and name not in seen:
                parts.append(f"## Skill: {name}\n\n{self._skills[name]}")
                seen.add(name)

        return "\n\n---\n\n".join(parts)

    def _match_custom_skills(self, instruction: str, selected: set[str], *, limit: int = 2) -> list[str]:
        instruction_lower = instruction.lower()
        instruction_tokens = set(_tokenize(instruction_lower))
        matches: list[tuple[int, str]] = []

        for name, content in self._skills.items():
            if name in selected or name in _DEFAULT_SKILL_NAMES:
                continue
            score = _score_custom_skill_match(name, content, instruction_lower, instruction_tokens)
            if score >= 1:
                matches.append((score, name))

        matches.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in matches[:limit]]

    def get_by_name(self, name: str) -> Optional[str]:
        """Get a specific skill by filename (without extension)."""
        if not self._loaded:
            self.load()
        return self._skills.get(name)

    @property
    def skill_count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._skills)

    @property
    def skill_names(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list(self._skills.keys())


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9_一-鿿]+", text.lower()) if len(token) >= 2]


def _score_custom_skill_match(name: str, content: str, instruction_lower: str, instruction_tokens: set[str]) -> int:
    score = 0
    name_tokens = set(_tokenize(name.replace("_", " ").replace("-", " ")))
    score += 2 * len(name_tokens & instruction_tokens)

    activation_text = _extract_activation_text(content).lower()
    activation_terms = _activation_terms(activation_text)
    for term in activation_terms:
        if term and term in instruction_lower:
            score += 3

    body = content[:3000].lower()
    for token in instruction_tokens:
        if token in body:
            score += 1
    for token in set(_tokenize(body)):
        if token in instruction_lower:
            score += 1

    return score


def _extract_activation_text(content: str) -> str:
    lines = content.splitlines()
    chunks: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            lower = stripped.lower()
            collecting = any(marker in lower for marker in ("触发关键词", "activation keywords", "适用场景", "when to use"))
            continue
        if collecting:
            if stripped.startswith("#"):
                collecting = False
            elif stripped:
                chunks.append(stripped)
    return "\n".join(chunks)


def _activation_terms(text: str) -> list[str]:
    terms: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*").strip()
        if cleaned:
            terms.append(cleaned)
            terms.extend(_tokenize(cleaned))
    return terms
