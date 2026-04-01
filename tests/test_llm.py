import unittest
from unittest.mock import MagicMock, patch

from openbrep.config import LLMConfig
from openbrep.llm import LLMAdapter
from ui.app import _detect_image_task_mode, _validate_chat_image_size


class TestLLMAdapterVision(unittest.TestCase):
    def test_generate_with_image_passes_timeout_and_api_settings(self):
        config = LLMConfig(
            model="gpt-4o",
            api_key="test-key",
            api_base="https://example.com/v1",
            timeout=12,
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.model = "openai/gpt-4o"
        mock_response.usage = {"prompt_tokens": 1}
        adapter._litellm.completion.return_value = mock_response

        result = adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
        )

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 12)
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["api_base"], "https://example.com/v1")

    def test_generate_with_image_wraps_auth_error(self):
        config = LLMConfig(model="gpt-4o", timeout=10)
        adapter = LLMAdapter(config)

        class FakeAuthError(Exception):
            pass

        adapter._litellm = MagicMock()
        adapter._litellm.exceptions = MagicMock(AuthenticationError=FakeAuthError, BadRequestError=ValueError)
        adapter._litellm.completion.side_effect = FakeAuthError("bad key")

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate_with_image("describe", "YWJj")
        self.assertIn("LLM 配置错误", str(cm.exception))


class TestVisionHelpers(unittest.TestCase):
    def test_detect_image_task_mode_debug_tokens(self):
        self.assertEqual(_detect_image_task_mode("这个截图报错了", "screen.png"), "debug")

    def test_detect_image_task_mode_generate_tokens(self):
        self.assertEqual(_detect_image_task_mode("根据这张参考图生成", "chair.jpg"), "generate")

    def test_validate_chat_image_size_rejects_large_file(self):
        raw = b"x" * (5 * 1024 * 1024 + 1)
        msg = _validate_chat_image_size(raw, "big.png")
        self.assertIn("5 MB", msg)
        self.assertIn("big.png", msg)

    def test_validate_chat_image_size_accepts_small_file(self):
        self.assertIsNone(_validate_chat_image_size(b"small", "small.png"))


if __name__ == "__main__":
    unittest.main()
