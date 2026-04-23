"""Tests for openbrep.skill_creator."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbrep.skill_creator import SkillCreator, SkillCreationResult


def _extract_user_input(last: str) -> str:
    """Extract the actual user input from the classification prompt."""
    marker = "用户输入："
    if marker in last:
        idx = last.index(marker) + len(marker)
        return last[idx:].strip()
    return last


@pytest.fixture
def mock_llm():
    """Create a mock LLMAdapter that returns predictable responses."""
    llm = MagicMock()

    def generate(messages):
        resp = MagicMock()
        last = messages[-1]["content"] if messages else ""

        if "分类器" in last or "classifier" in last or "CREATE_SKILL" in str(messages):
            user_input = _extract_user_input(last)
            if "create" in user_input.lower() or "创建" in user_input:
                resp.content = "CREATE_SKILL"
            elif "list" in user_input.lower() or "查看" in user_input:
                resp.content = "LIST_SKILLS"
            else:
                resp.content = "NONE"
        elif "FILENAME:" in str(messages) or "FILENAME:" in last:
            resp.content = (
                "FILENAME: my_project_skill.md\n"
                "---\n"
                "# My Project Skill\n\n"
                "## 项目描述\n"
                "A custom skill for GDL generation.\n\n"
                "## 代码规范\n"
                "- Use 4-space indentation\n"
                "- Hungarian notation for variables\n\n"
                "## 常用模式\n"
                "- Window frame with PRISM_\n"
                "- Door panel with BLOCK\n"
            )
        elif "技能" in last or "skill" in last.lower():
            resp.content = "好的，请告诉我你的项目名称和主要用途？"
        else:
            resp.content = "Hi! Let's create a skill. What is your project about?"

        return resp

    llm.generate = generate
    return llm


@pytest.fixture
def skills_dir():
    """Create a temporary skills directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestSkillCreator:
    def test_classify_intent_create(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        intent = creator.classify_intent("我想创建一个门窗技能")
        assert intent == "CREATE_SKILL"

    def test_classify_intent_list(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        intent = creator.classify_intent("查看已有技能")
        assert intent == "LIST_SKILLS"

    def test_classify_intent_none(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        intent = creator.classify_intent("你好，今天天气不错")
        assert intent == "NONE"

    def test_start_conversation(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        reply = creator.start_conversation("门窗项目")
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_process_turn_before_generate(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        creator.start_conversation()
        reply = creator.process_turn("我们做窗户构件")
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_process_turn_generate(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        creator.start_conversation("门窗项目")
        creator.conversation.append({"role": "user", "content": "我们做窗户构件，使用 standard naming"})
        creator.conversation.append({"role": "assistant", "content": "好的，还有其他规范吗？"})

        reply = creator.process_turn("生成")
        assert "技能文件已创建" in reply

        # Verify file was created
        files = list(skills_dir.glob("*.md"))
        assert len(files) > 0

    def test_finalize_only_when_ready(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        creator.start_conversation()
        result = creator.finalize()
        assert result is None  # not ready

    def test_list_skills_empty(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        result = creator.list_skills()
        assert "尚无" in result or "不存在" in result

    def test_list_skills_with_files(self, mock_llm, skills_dir):
        (skills_dir / "test_skill.md").write_text("# Test Skill\n\ncontent")
        creator = SkillCreator(mock_llm, str(skills_dir))
        result = creator.list_skills()
        assert "test_skill" in result

    def test_is_generate_request(self):
        assert SkillCreator._is_generate_request("生成")
        assert SkillCreator._is_generate_request("好了")
        assert SkillCreator._is_generate_request("create")
        assert SkillCreator._is_generate_request("done")
        assert not SkillCreator._is_generate_request("我要创建一个门窗技能")

    def test_parse_generation(self):
        raw = (
            "FILENAME: window_skill.md\n"
            "---\n"
            "# Window Skill\n\n"
            "Content here.\n"
        )
        name, content = SkillCreator._parse_generation(raw)
        assert name == "window_skill.md"
        assert "# Window Skill" in content

    def test_parse_generation_fallback(self):
        raw = "# Just Content\n\nNo filename marker."
        name, content = SkillCreator._parse_generation(raw)
        assert name == "custom_skill"
        assert content == raw

    def test_skill_file_creation(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        creator.start_conversation("test")
        creator.conversation.append({"role": "user", "content": "some details"})
        creator.conversation.append({"role": "assistant", "content": "ok"})
        creator._ready_to_generate = True
        result = creator.finalize()
        assert result is not None
        assert Path(result.file_path).exists()
        assert "My Project Skill" in result.content
