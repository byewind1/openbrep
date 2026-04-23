"""
Skill creator — conversational LLM-guided skill authoring.

Walks the user through defining project-specific GDL conventions, code
patterns, and rules, then writes a structured skill file to skills/{name}.md
that the SkillsLoader can pick up for generation tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openbrep.llm import LLMAdapter

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────

_CLASSIFICATION_PROMPT = (
    "你是一个分类器。判断用户是否想要创建或管理一个 GDL 技能（skill）。\n"
    "技能是项目级别的代码规范、案例模式或编写规则，用于指导 AI 生成更准确的 GDL 代码。\n"
    '如果用户想创建新技能、管理已有技能、或输入项目文档让 AI 学习，回复 "CREATE_SKILL"。\n'
    '如果用户想查看已有技能列表，回复 "LIST_SKILLS"。\n'
    '如果都不相关，回复 "NONE"。\n\n'
    "只回复以上三种之一。\n\n"
    "用户输入：{user_input}"
)

_GUIDE_PROMPT = (
    "你是 openbrep 的技能创建助手。你正在引导用户创建一个 GDL 项目技能（skill）。\n\n"
    "一个技能包含以下信息：\n"
    "1. 项目名称和描述 — 这是什么项目？用什么类型的 GDL 构件？\n"
    "2. 代码规范 — 变量命名规则、缩进风格、注释风格、文件结构等。\n"
    "3. 常用模式和案例 — 项目中反复出现的 GDL 代码模式。\n"
    "4. 材质和属性约定 — 项目使用的特定材质、笔号、线型等。\n"
    "5. 参数命名约定 — 项目级参数命名规范。\n\n"
    "你需要通过对话一步步收集这些信息。每次只问 1-2 个问题。\n"
    "当收集到足够信息后，询问用户是否要生成技能文件。\n"
    "回复使用用户输入的语言。"
)

_GENERATE_PROMPT = (
    "基于以下对话中收集的信息，生成一个 GDL 技能文件。\n"
    "格式要求：\n"
    "```markdown\n"
    "# {{Skill Name}}\n\n"
    "## 项目描述\n"
    "{{description}}\n\n"
    "## 代码规范\n"
    "{{conventions}}\n\n"
    "## 常用模式\n"
    "{{patterns}}\n\n"
    "## 材质与属性\n"
    "{{materials}}\n\n"
    "## 参数约定\n"
    "{{parameters}}\n"
    "```\n\n"
    "确保 content 只包含有效的 Markdown，不包含额外的包裹格式。\n\n"
    "对话记录：\n{conversation}\n\n"
    "请只输出技能文件名和内容，格式为：\n"
    "FILENAME: {suggested_name}.md\n"
    "---\n"
    "[content here]"
)


@dataclass
class SkillCreationResult:
    """Result of a skill creation session."""

    skill_name: str
    content: str
    file_path: str
    conversation: list[dict] = field(default_factory=list)


class SkillCreator:
    """
    Conversational skill authoring for project-specific GDL conventions.

    Usage:
        creator = SkillCreator(llm, skills_dir="./skills")
        # Step 1: classify user intent
        intent = creator.classify_intent(user_input)
        # Step 2: if CREATE_SKILL, start conversation
        reply = creator.process_turn(user_input)
        # Step 3: when ready, generate and save
        result = creator.finalize()
    """

    def __init__(
        self,
        llm: LLMAdapter,
        skills_dir: str = "./skills",
    ):
        self.llm = llm
        self.skills_dir = Path(skills_dir)
        self.conversation: list[dict] = field(default_factory=list)
        self._ready_to_generate = False
        self._suggested_name: str = ""

    # ── Intent classification ────────────────────────────

    def classify_intent(self, user_input: str) -> str:
        """
        Classify user intent related to skill management.

        Returns: "CREATE_SKILL", "LIST_SKILLS", or "NONE".
        """
        prompt = _CLASSIFICATION_PROMPT.format(user_input=user_input)
        try:
            resp = self.llm.generate([{"role": "user", "content": prompt}])
            text = resp.content.strip().upper()
            if "CREATE_SKILL" in text:
                return "CREATE_SKILL"
            if "LIST_SKILLS" in text:
                return "LIST_SKILLS"
            return "NONE"
        except Exception as exc:
            logger.warning("Skill intent classification failed: %s", exc)
            return "NONE"

    # ── Conversation ─────────────────────────────────────

    def start_conversation(self, user_hint: str = "") -> str:
        """Start or reset the skill creation conversation."""
        self.conversation = []
        self._ready_to_generate = False
        self._suggested_name = ""

        if user_hint:
            self.conversation.append({"role": "user", "content": user_hint})

        messages = self._build_guide_messages()
        try:
            resp = self.llm.generate(messages)
            reply = resp.content
        except Exception as exc:
            reply = f"无法启动技能创建对话：{exc}"

        self.conversation.append({"role": "assistant", "content": reply})
        return reply

    def process_turn(self, user_input: str) -> str:
        """Process one turn in the skill creation conversation."""
        self.conversation.append({"role": "user", "content": user_input})

        # Check if user wants to generate
        if self._is_generate_request(user_input):
            self._ready_to_generate = True
            result = self._do_generate()
            self.conversation.append(
                {"role": "assistant", "content": result.skill_name}
            )
            return f"技能文件已创建：{result.file_path}\n\n{result.content[:200]}..."

        messages = self._build_guide_messages()
        try:
            resp = self.llm.generate(messages)
            reply = resp.content
        except Exception as exc:
            reply = f"对话出错：{exc}"

        self.conversation.append({"role": "assistant", "content": reply})
        return reply

    def list_skills(self) -> str:
        """List existing skill files."""
        if not self.skills_dir.is_dir():
            return "技能目录不存在。"

        files = sorted(self.skills_dir.glob("*.md"))
        if not files:
            return "尚无已创建的技能。你可以说“创建技能”来开始。"

        lines = ["已有技能文件："]
        for f in files:
            lines.append(f"  - {f.stem}")
        return "\n".join(lines)

    # ── Generate ─────────────────────────────────────────

    def finalize(self) -> Optional[SkillCreationResult]:
        """Generate and save the skill file. Call when conversation is done."""
        if not self._ready_to_generate:
            return None
        return self._do_generate()

    def _do_generate(self) -> SkillCreationResult:
        """Generate skill content from conversation history."""
        conv_text = self._format_conversation()
        prompt = _GENERATE_PROMPT.format(
            conversation=conv_text,
            suggested_name=self._suggested_name or "custom_skill",
        )

        resp = self.llm.generate([{"role": "user", "content": prompt}])
        raw = resp.content

        # Parse filename and content
        name, content = self._parse_generation(raw)

        # Ensure filename has .md suffix
        if not name.endswith(".md"):
            name = name + ".md"

        file_path = self.skills_dir / name

        # Write skill file
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        return SkillCreationResult(
            skill_name=name.replace(".md", ""),
            content=content,
            file_path=str(file_path),
            conversation=self.conversation,
        )

    # ── Internals ────────────────────────────────────────

    def _build_guide_messages(self) -> list[dict]:
        """Build messages for the guide LLM."""
        messages = [{"role": "system", "content": _GUIDE_PROMPT}]
        if self.conversation:
            messages.extend(self.conversation)
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": "你好！我来帮你创建 GDL 项目技能。请先告诉我你主要做什么类型的 GDL 构件？",
                }
            )
        return messages

    @staticmethod
    def _is_generate_request(text: str) -> bool:
        """Check if user wants to generate/finalize the skill."""
        text_lower = text.lower()
        generate_keywords = [
            "生成", "创建", "保存", "完成", "好了", "可以了",
            "generate", "create", "save", "finish", "done",
            "finalize", "write it", "make it",
        ]
        return any(kw in text_lower for kw in generate_keywords) and len(text) < 9

    @staticmethod
    def _parse_generation(raw: str) -> tuple[str, str]:
        """Parse LLM output into (filename, content)."""
        lines = raw.splitlines()
        name = "custom_skill"
        content_start = 0

        for i, line in enumerate(lines):
            if line.upper().startswith("FILENAME:"):
                name = line.split(":", 1)[1].strip()
            if line.strip() == "---":
                content_start = i + 1

        content = "\n".join(lines[content_start:]).strip()
        if not content:
            content = raw  # fallback: use entire output

        return name, content

    def _format_conversation(self) -> str:
        """Format conversation history for the generate prompt."""
        parts = []
        for msg in self.conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"[{role.upper()}]\n{content}")
        return "\n\n".join(parts)
