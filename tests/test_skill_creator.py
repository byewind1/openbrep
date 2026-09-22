"""Tests for openbrep.skill_creator."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openbrep.skill_creator import (
    SkillCreator,
    SkillCreationResult,
    has_skill_signal,
)


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
    def test_codex_classification_uses_chat_intent(self, skills_dir):
        seen = {}

        class _CodexLLM:
            config = SimpleNamespace(
                model="openai-codex/gpt-5.6-sol",
                codex_reasoning_effort=lambda: "high",
            )

            def generate(self, _messages, **kwargs):
                seen.update(kwargs)
                return SimpleNamespace(content="NONE")

        creator = SkillCreator(_CodexLLM(), str(skills_dir))

        # 必须带技能信号才会走到 LLM；否则规则前置直接 NONE。
        assert creator.classify_intent("把这个做法存成技能") == "NONE"
        assert seen == {
            "codex_intent": "CHAT",
            "codex_reasoning_effort": "high",
        }

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

    def test_classify_intent_skips_llm_without_skill_signal(self, mock_llm, skills_dir):
        """无技能信号 → 直接 NONE，不产生任何 LLM 调用。"""
        creator = SkillCreator(mock_llm, str(skills_dir))
        mock_llm.generate = MagicMock(
            side_effect=AssertionError("rule prefilter must not call LLM")
        )
        for text in (
            "你好，今天天气不错",
            "修改楼梯",
            "把层板数改成 5",
            "解释一下 3d.gdl",
            "生成一个旋转楼梯，层高 3000",
            "",
            "   ",
        ):
            assert creator.classify_intent(text) == "NONE", text
        mock_llm.generate.assert_not_called()

    def test_classify_intent_calls_llm_when_skill_signal_present(self, mock_llm, skills_dir):
        """有技能信号 → 仍走 LLM，可得到 CREATE/LIST（与旧行为一致）。"""
        creator = SkillCreator(mock_llm, str(skills_dir))
        assert creator.classify_intent("我想创建一个门窗技能") == "CREATE_SKILL"
        assert creator.classify_intent("查看已有技能") == "LIST_SKILLS"

    def test_original_none_set_not_shrunk_by_prefilter(self, mock_llm, skills_dir):
        """原先会判 NONE 的消息，在规则前置后仍全部是 NONE。"""
        creator = SkillCreator(mock_llm, str(skills_dir))
        former_none = (
            "你好，今天天气不错",
            "修改楼梯的踏步数量",
            "这个构件的参数是什么意思",
            "把高度改成 3000",
            "今天帮我看看这段脚本",
        )
        for text in former_none:
            assert creator.classify_intent(text) == "NONE", text

    def test_has_skill_signal_covers_create_and_list_phrasings(self):
        assert has_skill_signal("把漏窗做法存成技能")
        assert has_skill_signal("列出技能")
        assert has_skill_signal("List Skills")
        assert has_skill_signal("save this as a skill")
        assert not has_skill_signal("改成 5 个层板")
        assert not has_skill_signal("")
        assert not has_skill_signal("   ")

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

    def test_conversation_initializes_as_list(self, mock_llm, skills_dir):
        creator = SkillCreator(mock_llm, str(skills_dir))
        assert creator.conversation == []

    def test_generation_prompt_requests_activation_metadata(self, mock_llm, skills_dir):
        calls = []

        def generate(messages):
            calls.append(messages)
            resp = MagicMock()
            resp.content = "FILENAME: test_skill.md\n---\n# Test Skill"
            return resp

        mock_llm.generate = generate
        creator = SkillCreator(mock_llm, str(skills_dir))
        creator.conversation = [{"role": "user", "content": "门窗项目"}]
        creator._ready_to_generate = True

        creator.finalize()

        prompt = calls[-1][-1]["content"]
        assert "Activation Keywords" in prompt
        assert "触发关键词" in prompt
        assert "When to Use" in prompt

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

    def test_generated_skill_proposed_frontmatter_not_injected_until_active(self, skills_dir):
        """SkillCreator 模板产物带 status: proposed frontmatter，默认不被注入；
        status 改 active 后即可注入（端到端小验证）。"""
        from unittest.mock import MagicMock

        def generate(messages):
            resp = MagicMock()
            resp.content = (
                "FILENAME: window_skill.md\n"
                "---\n"
                "---\n"
                "status: proposed\n"
                "skill_version: 1\n"
                "---\n"
                "# Window Skill\n\n"
                "## 触发关键词 / Activation Keywords\n"
                "- 窗户\n"
                "- window\n\n"
                "## 项目描述\n"
                "A window skill.\n"
            )
            return resp

        llm = MagicMock()
        llm.generate = generate
        creator = SkillCreator(llm, str(skills_dir))
        creator.conversation = [{"role": "user", "content": "门窗项目"}]
        creator._ready_to_generate = True

        result = creator.finalize()
        assert result is not None
        file_path = Path(result.file_path)

        content = file_path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "status: proposed" in content
        assert "skill_version: 1" in content
        assert "pattern_type:" in content

        from openbrep.skills_loader import SkillsLoader

        loader = SkillsLoader(str(skills_dir))
        injected = loader.get_for_task("生成一个铝合金窗户")
        assert "window_skill" not in injected
        assert "window_skill" in loader.skill_names  # 管理面可见
        assert loader.skill_meta("window_skill")["status"] == "proposed"

        # 对照：status 改 active 后重载即可注入 → 证明过滤来自状态而非匹配落空
        file_path.write_text(content.replace("status: proposed", "status: active"), encoding="utf-8")
        assert "window_skill" in SkillsLoader(str(skills_dir)).get_for_task("生成一个铝合金窗户")
