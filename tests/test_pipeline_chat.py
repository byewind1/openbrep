import unittest
from unittest.mock import MagicMock

from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMResponse


class TestPipelineChat(unittest.TestCase):
    def _make_pipeline(self, response_text: str = "你好") -> TaskPipeline:
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content=response_text,
            model="mock",
            usage={},
            finish_reason="stop",
        )
        pipeline._make_llm = lambda req: mock_llm
        return pipeline

    def test_chat_includes_recent_history(self):
        pipeline = self._make_pipeline("ok")
        request = TaskRequest(
            user_input="再详细一点",
            intent="CHAT",
            history=[
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "第一答"},
            ],
        )

        result = pipeline.execute(request)

        self.assertTrue(result.success)
        messages = pipeline._make_llm(request).generate.call_args.args[0]
        self.assertEqual(messages[1]["content"], "第一句")
        self.assertEqual(messages[2]["content"], "第一答")
        self.assertEqual(messages[3]["content"], "再详细一点")

    def test_chat_prepends_assistant_settings_prompt(self):
        pipeline = self._make_pipeline("ok")
        request = TaskRequest(
            user_input="你好",
            intent="CHAT",
            assistant_settings="回答简短一点",
        )

        pipeline.execute(request)

        messages = pipeline._make_llm(request).generate.call_args.args[0]
        self.assertIn("AI助手设置", messages[0]["content"])
        self.assertIn("回答简短一点", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
