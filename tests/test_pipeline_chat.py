import unittest
from unittest.mock import MagicMock, patch

from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.explainer.schema import ProjectExplanation


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

    def test_chat_with_project_uses_explainer_adapter(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"

        fake_explanation = ProjectExplanation(overall_goal="chair")
        with patch("openbrep.runtime.pipeline.build_project_context", return_value={"gsm_name": "chair"}) as mock_context:
            with patch("openbrep.runtime.pipeline.explain_project_context", return_value=fake_explanation) as mock_explain:
                with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="简要拆解") as mock_reply:
                    result = pipeline.execute(TaskRequest(
                        user_input="这是什么对象？",
                        intent="CHAT",
                        project=project,
                    ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "简要拆解")
        mock_context.assert_called_once_with(project)
        mock_explain.assert_called_once_with({"gsm_name": "chair"})
        mock_reply.assert_called_once_with(fake_explanation, user_input="这是什么对象？")

    def test_chat_with_project_does_not_call_raw_llm(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")

        with patch("openbrep.runtime.pipeline.build_project_context", return_value={"gsm_name": "chair"}):
            with patch("openbrep.runtime.pipeline.explain_project_context", return_value=ProjectExplanation(overall_goal="chair")):
                with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="简要拆解"):
                    pipeline.execute(TaskRequest(user_input="解释一下", intent="CHAT", project=project))

        self.assertIsNone(pipeline._make_llm(TaskRequest(user_input="x")).generate.call_args)

    def test_chat_with_project_adds_explainer_constraint(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        request = TaskRequest(
            user_input="解释一下",
            intent="CHAT",
            project=project,
            assistant_settings="回答简短一点",
        )

        with patch("openbrep.runtime.pipeline.build_project_context", return_value={"gsm_name": "chair"}):
            with patch("openbrep.runtime.pipeline.explain_project_context", return_value=ProjectExplanation(overall_goal="chair")):
                with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="简要拆解"):
                    pipeline.execute(request)

        messages = pipeline._make_llm(request).generate.call_args
        self.assertIsNone(messages)


if __name__ == "__main__":
    unittest.main()
