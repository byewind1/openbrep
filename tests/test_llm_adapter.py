import unittest
import warnings
from unittest.mock import MagicMock

from openbrep.config import LLMConfig
from openbrep.llm import LLMAdapter

class TestLLMAdapterVision(unittest.TestCase):
    def _mock_response(self, model_name="openai/gpt-4o"):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.model = model_name
        mock_response.usage = {"prompt_tokens": 1}
        return mock_response

    def test_generate_with_image_passes_timeout_and_api_settings(self):
        config = LLMConfig(
            model="gpt-4o",
            api_key="test-key",
            api_base="https://example.com/v1",
            timeout=12,
        )
        adapter = LLMAdapter(config)
        built_response = self._mock_response()
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
        )

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 12)
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertNotIn("api_base", kwargs)

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
        message = str(cm.exception)
        self.assertIn("API Key", message)
        self.assertIn("LLM 认证失败", message)
        self.assertIn("无效、已过期", message)
        self.assertIn("底层错误：bad key", message)
        self.assertIn("resolved_model=openai/gpt-4o", message)

    def test_generate_wraps_auth_error_with_invalid_key_hint(self):
        config = LLMConfig(model="gpt-4o", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)

        class FakeAuthError(Exception):
            pass

        adapter._litellm = MagicMock()
        adapter._litellm.exceptions = MagicMock(AuthenticationError=FakeAuthError, BadRequestError=ValueError)
        adapter._litellm.completion.side_effect = FakeAuthError("invalid api key")

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("LLM 认证失败", message)
        self.assertIn("无效、已过期", message)
        self.assertIn("resolved_model=openai/gpt-4o", message)

    def test_generate_wraps_bad_request_for_builtin_model_with_model_hint(self):
        config = LLMConfig(model="gpt-bad-name", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)

        class FakeBadRequestError(Exception):
            pass

        adapter._litellm = MagicMock()
        adapter._litellm.exceptions = MagicMock(AuthenticationError=PermissionError, BadRequestError=FakeBadRequestError)
        adapter._litellm.completion.side_effect = FakeBadRequestError("model_not_found")

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("模型 `gpt-bad-name`", message)
        self.assertIn("model 名称填写不正确", message)
        self.assertIn("底层错误：model_not_found", message)
        self.assertIn("resolved_model=openai/gpt-bad-name", message)

    def test_generate_wraps_bad_request_for_custom_provider_with_provider_hint(self):
        config = LLMConfig(
            model="glm-5.1",
            timeout=10,
            custom_providers=[
                {
                    "name": "ymg",
                    "base_url": "https://api.airsim.eu.cc/v1",
                    "api_key": "test-key",
                    "models": ["glm-5.1"],
                    "protocol": "openai",
                }
            ],
        )
        adapter = LLMAdapter(config)

        class FakeBadRequestError(Exception):
            pass

        adapter._litellm = MagicMock()
        adapter._litellm.exceptions = MagicMock(AuthenticationError=PermissionError, BadRequestError=FakeBadRequestError)
        adapter._litellm.completion.side_effect = FakeBadRequestError("unsupported model")

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("自定义 provider `ymg`", message)
        self.assertIn("协议、base_url 或模型名配置", message)
        self.assertIn("provider=ymg", message)
        self.assertIn("api_base=https://api.airsim.eu.cc/v1", message)
        self.assertIn("resolved_model=openai/glm-5.1", message)

    def test_gpt5_custom_provider_model_resolves_with_protocol_prefix(self):
        config = LLMConfig(
            model="gpt-5.4",
            custom_providers=[{"name": "ymg", "models": ["gpt-5.4"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/gpt-5.4")

    def test_non_gpt_custom_model_resolves_with_protocol_prefix(self):
        config = LLMConfig(
            model="ymg-chat",
            custom_providers=[{"name": "ymg", "models": ["ymg-chat"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/ymg-chat")

    def test_custom_alias_with_provider_prefix_resolves_to_underlying_model(self):
        config = LLMConfig(
            model="ymg-gpt-5.3-codex",
            custom_providers=[{"name": "ymg", "models": ["ymg-gpt-5.3-codex"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/gpt-5.3-codex")

    def test_adapter_registers_response_api_usage_warning_filter(self):
        LLMAdapter(LLMConfig(model="gpt-5.4", api_key="test-key"))
        self.assertTrue(
            any(
                f[0] == "ignore"
                and f[2] is UserWarning
                and "ResponseAPIUsage" in str(f[1])
                for f in warnings.filters
            )
        )

    def test_adapter_does_not_suppress_unrelated_user_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.warn("other-warning", UserWarning)
        self.assertEqual(len(caught), 1)

    def test_builtin_gpt5_model_keeps_openai_prefix(self):
        config = LLMConfig(model="gpt-5.4")
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/gpt-5.4")

    def test_generate_with_non_gpt_custom_model_uses_prefixed_model_and_keeps_api_base(self):
        config = LLMConfig(
            model="ymg-chat",
            api_key="test-key",
            api_base="https://api.airsim.eu.cc/v1",
            temperature=0.2,
            max_tokens=9999,
            timeout=22,
            custom_providers=[{"name": "ymg", "models": ["ymg-chat"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        built_response = self._mock_response(model_name="openai/ymg-chat")
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/ymg-chat")
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertEqual(kwargs["max_tokens"], 9999)
        self.assertEqual(kwargs["timeout"], 22)
        self.assertEqual(kwargs["api_base"], "https://api.airsim.eu.cc/v1")
        self.assertNotIn("drop_params", kwargs)

    def test_generate_with_model_override_keeps_provider_fields_consistent(self):
        config = LLMConfig(
            model="ymg-gpt-5.3-codex",
            api_key="top-level-key",
            api_base="https://integrate.api.nvidia.com/v1",
            temperature=0.2,
            max_tokens=512,
            timeout=12,
            custom_providers=[
                {
                    "name": "ymg",
                    "base_url": "https://api.ymg.com/v1",
                    "api_key": "ymg-key",
                    "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                    "protocol": "openai",
                },
                {
                    "name": "nvidia",
                    "base_url": "https://integrate.api.nvidia.com/v1",
                    "api_key": "nvidia-key",
                    "models": [{"alias": "moonshotai/kimi-k2.5", "model": "openai/moonshotai/kimi-k2.5"}],
                    "protocol": "openai",
                },
            ],
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="openai/moonshotai/kimi-k2.5")

        adapter.generate(
            [{"role": "user", "content": "hi"}],
            stream=False,
            model="moonshotai/kimi-k2.5",
        )

        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/moonshotai/kimi-k2.5")
        self.assertEqual(kwargs["api_base"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(kwargs["api_key"], "nvidia-key")

    def test_builtin_gpt5_generate_enables_stream_by_default(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            temperature=0.2,
            max_tokens=4096,
            timeout=33,
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="openai/gpt-5.4")

        adapter.generate([{"role": "user", "content": "hi"}])

        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertTrue(kwargs["stream"])

    def test_builtin_gpt5_generate_uses_stream_chunk_builder(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            temperature=0.2,
            max_tokens=4096,
            timeout=33,
        )
        adapter = LLMAdapter(config)
        chunk1 = MagicMock()
        chunk2 = MagicMock()
        built_response = self._mock_response(model_name="openai/gpt-5.4")
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [chunk1, chunk2]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "ok")
        adapter._litellm.stream_chunk_builder.assert_called_once_with([chunk1, chunk2])

    def test_builtin_gpt5_generate_streams_and_aggregates_delta_content(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            temperature=0.2,
            max_tokens=4096,
            timeout=33,
        )
        adapter = LLMAdapter(config)
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock(delta=MagicMock(content="hello"))]
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock(delta=MagicMock(content=" world"))]
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock(delta=MagicMock(content=None))]
        built_response = self._mock_response(model_name="openai/gpt-5.4")
        built_response.choices[0].message.content = "hello world"
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [chunk1, chunk2, chunk3]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "hello world")
        self.assertEqual(result.model, "openai/gpt-5.4")
        self.assertEqual(result.usage, {"prompt_tokens": 1})
        self.assertEqual(result.finish_reason, "stop")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-5.4")
        self.assertTrue(kwargs["stream"])
        self.assertTrue(kwargs["drop_params"])

    def test_builtin_gpt5_generate_keeps_configured_parameters(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            temperature=0.2,
            max_tokens=4096,
            timeout=33,
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="openai/gpt-5.4")

        result = adapter.generate([{"role": "user", "content": "hi"}], stream=False)

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-5.4")
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertEqual(kwargs["max_tokens"], 4096)
        self.assertEqual(kwargs["timeout"], 33)
        self.assertFalse(kwargs["stream"])
        self.assertTrue(kwargs["drop_params"])

    def test_builtin_gpt5_vision_enables_stream_by_default(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            api_base="https://example.com/v1",
            temperature=0.2,
            max_tokens=512,
            timeout=12,
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="openai/gpt-5.4")

        adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
        )

        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertTrue(kwargs["stream"])

    def test_builtin_gpt5_vision_uses_stream_chunk_builder(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            api_base="https://example.com/v1",
            temperature=0.2,
            max_tokens=512,
            timeout=12,
        )
        adapter = LLMAdapter(config)
        chunk1 = MagicMock()
        chunk2 = MagicMock()
        built_response = self._mock_response(model_name="openai/gpt-5.4")
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [chunk1, chunk2]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
        )

        self.assertEqual(result.content, "ok")
        adapter._litellm.stream_chunk_builder.assert_called_once_with([chunk1, chunk2])

    def test_builtin_gpt5_vision_sets_drop_params_without_changing_temperature(self):
        config = LLMConfig(
            model="gpt-5.4",
            api_key="test-key",
            api_base="https://example.com/v1",
            temperature=0.2,
            max_tokens=512,
            timeout=12,
        )
        adapter = LLMAdapter(config)
        built_response = self._mock_response(model_name="openai/gpt-5.4")
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built_response

        result = adapter.generate_with_image(
            text_prompt="describe",
            image_b64="YWJj",
            image_mime="image/png",
        )

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertTrue(kwargs["drop_params"])


