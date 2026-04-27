"""Tests for pipeline skill creator routing in _handle_chat."""

import unittest
from unittest.mock import MagicMock, patch

from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse


class TestPipelineSkillCreator(unittest.TestCase):
    """Verify _handle_chat() routes correctly through skill creator paths."""

    def _make_pipeline(self) -> TaskPipeline:
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content="fallback reply",
            model="mock",
            usage={},
            finish_reason="stop",
        )
        pipeline._make_llm = lambda req: mock_llm
        return pipeline

    # ── Active session ─────────────────────────────────────

    def test_active_session_routes_to_process_turn(self):
        """Existing _skill_creator → process_turn() called, result returned."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.process_turn.return_value = "这是 skill 的回复"
        mock_creator._ready_to_generate = False
        pipeline._skill_creator = mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="加一个材质参数",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "这是 skill 的回复")
        mock_creator.process_turn.assert_called_once_with("加一个材质参数")

    def test_active_session_resets_on_generate(self):
        """process_turn sets _ready_to_generate → _skill_creator cleared."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.process_turn.return_value = "技能已生成"
        mock_creator._ready_to_generate = True
        pipeline._skill_creator = mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="生成",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        self.assertIsNone(pipeline._skill_creator)
        mock_creator.process_turn.assert_called_once_with("生成")

    def test_active_session_clears_skills_loader_cache_on_generate(self):
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.process_turn.return_value = "技能已生成"
        mock_creator._ready_to_generate = True
        pipeline._skill_creator = mock_creator
        pipeline._skills_loader = MagicMock()

        pipeline.execute(TaskRequest(user_input="生成", intent="CHAT"))

        self.assertIsNone(pipeline._skills_loader)

    def test_next_generation_loads_new_custom_skill_without_filename(self):
        pipeline = self._make_pipeline()
        captured = {}

        skills_dir = self.enterContext(__import__("tempfile").TemporaryDirectory())
        from pathlib import Path
        skill_root = Path(skills_dir)
        (skill_root / "project_style.md").write_text(
            "# 门窗项目规范\n\n"
            "## 触发关键词 / Activation Keywords\n"
            "- 窗户\n"
            "- 门窗\n\n"
            "## 常用模式\n"
            "铝合金窗框统一使用 frame_width 参数。\n",
            encoding="utf-8",
        )
        pipeline._resolve_skills_dir = lambda: skill_root
        pipeline._load_knowledge = lambda: ""

        with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.generate_only.return_value = ({}, "ok")
            mock_agent_cls.return_value = mock_agent
            result = pipeline.execute(TaskRequest(
                user_input="生成一个铝合金窗户",
                intent="CREATE",
            ))
            captured["skills"] = mock_agent.generate_only.call_args.kwargs["skills"]

        self.assertTrue(result.success)
        self.assertIn("project_style", captured["skills"])
        self.assertIn("铝合金窗框", captured["skills"])

    # ── CREATE_SKILL intent ────────────────────────────────

    def test_create_skill_starts_conversation(self):
        """classify_intent returns CREATE_SKILL → start_conversation, session set."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.classify_intent.return_value = "CREATE_SKILL"
        mock_creator.start_conversation.return_value = "好的，请告诉我你的项目名称？"
        pipeline._get_skill_creator = lambda req: mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="创建一个门窗技能",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "好的，请告诉我你的项目名称？")
        self.assertIsNotNone(pipeline._skill_creator)
        mock_creator.classify_intent.assert_called_once_with("创建一个门窗技能")
        mock_creator.start_conversation.assert_called_once_with("创建一个门窗技能")

    # ── LIST_SKILLS intent ─────────────────────────────────

    def test_list_skills_returns_list(self):
        """classify_intent returns LIST_SKILLS → list_skills, no session set."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.classify_intent.return_value = "LIST_SKILLS"
        mock_creator.list_skills.return_value = "已有技能：\n  - window_skill"
        pipeline._get_skill_creator = lambda req: mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="查看技能",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "已有技能：\n  - window_skill")
        self.assertIsNone(pipeline._skill_creator)
        mock_creator.classify_intent.assert_called_once_with("查看技能")
        mock_creator.list_skills.assert_called_once()

    # ── Non-skill fallthrough ──────────────────────────────

    def test_non_skill_falls_through_to_llm(self):
        """classify_intent returns NONE → fall through to LLM chat."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        mock_creator.classify_intent.return_value = "NONE"
        pipeline._get_skill_creator = lambda req: mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="今天天气如何",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "fallback reply")
        self.assertIsNone(pipeline._skill_creator)
        mock_creator.classify_intent.assert_called_once_with("今天天气如何")

    def test_greeting_skips_skill_detection(self):
        """Greeting-only input → skip classify_intent entirely."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        pipeline._get_skill_creator = lambda req: mock_creator

        result = pipeline.execute(TaskRequest(
            user_input="你好",
            intent="CHAT",
        ))

        self.assertTrue(result.success)
        mock_creator.classify_intent.assert_not_called()

    # ── Non-CHAT intent unaffected ─────────────────────────

    def test_skill_creator_not_invoked_for_non_chat_intent(self):
        """CREATE intent → _handle_gdl, not _handle_chat, so skill creator untouched."""
        pipeline = self._make_pipeline()
        mock_creator = MagicMock()
        pipeline._get_skill_creator = lambda req: mock_creator

        with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.generate_only.return_value = ({}, "created")
            mock_agent_cls.return_value = mock_agent
            result = pipeline.execute(TaskRequest(
                user_input="做一个书架",
                intent="CREATE",
            ))

        self.assertTrue(result.success)
        mock_creator.classify_intent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
