import os
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
        self.assertIn("API Key 无效或被拒绝", message)
        self.assertIn("已过期", message)
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
        self.assertIn("API Key 无效或被拒绝", message)
        self.assertIn("已过期", message)
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

    def test_generate_wraps_insufficient_balance_with_quota_hint(self):
        config = LLMConfig(
            model="DeepSeek-V4-Pro",
            timeout=10,
            custom_providers=[
                {
                    "name": "scnet",
                    "base_url": "https://api.scnet.cn/api/llm/v1",
                    "api_key": "test-key",
                    "models": ["DeepSeek-V4-Pro"],
                    "protocol": "openai",
                }
            ],
        )
        adapter = LLMAdapter(config)

        class FakeBadRequestError(Exception):
            pass

        adapter._litellm = MagicMock()
        adapter._litellm.exceptions = MagicMock(AuthenticationError=PermissionError, BadRequestError=FakeBadRequestError)
        adapter._litellm.completion.side_effect = FakeBadRequestError("OpenAIException - Insufficient Balance")

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("LLM 账户余额或额度不足", message)
        self.assertIn("provider `scnet`", message)
        self.assertIn("provider=scnet", message)
        self.assertIn("resolved_model=openai/DeepSeek-V4-Pro", message)

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

    def _mock_response_with_reasoning(self, model_name="openai/gpt-5.4"):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_choice.message.reasoning_content = "thinking only"
        mock_choice.finish_reason = "length"
        mock_response.choices = [mock_choice]
        mock_response.model = model_name
        mock_response.usage = {"prompt_tokens": 1, "completion_tokens": 10}
        return mock_response

    def test_generate_raises_when_model_outputs_only_reasoning(self):
        """部分 deepseek-v4-flash 端点会把全部 token 用于 reasoning_content，导致 content 为空。"""
        config = LLMConfig(model="deepseek-v4-flash", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = self._mock_response_with_reasoning(
            model_name="openai/deepseek-v4-flash"
        )

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "generate code"}])
        message = str(cm.exception)
        self.assertIn("只输出了思考过程", message)
        self.assertIn("thinking 模式消耗了全部 output token", message)
        self.assertIn("extra_body = { thinking = { type = \"disabled\" } }", message)
        self.assertIn("max_tokens 提高到 16384", message)

    def test_generate_raises_when_content_and_reasoning_are_both_empty(self):
        """content 和 reasoning 都为空时给出通用错误提示。"""
        config = LLMConfig(model="some-model", api_key="test-key", timeout=10)
        adapter = LLMAdapter(config)
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_choice.message.reasoning_content = ""
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.model = "openai/some-model"
        mock_response.usage = {}
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = mock_response

        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        self.assertIn("返回了空内容", str(cm.exception))

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



class TestLLMErrorClassification(unittest.TestCase):
    """断点 1/2 修复：认证特征优先于异常类型；网络类异常给中文引导。"""

    def _adapter(self, model="deepseek-chat", api_key="sk-test", api_base=None):
        config = LLMConfig(model=model, api_key=api_key, api_base=api_base, timeout=20)
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        return adapter

    def test_bad_request_with_auth_signature_points_to_key_not_model(self):
        """deepseek 式 401（BadRequestError 外壳）必须指向 key。"""
        adapter = self._adapter()

        class FakeBadRequestError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=FakeBadRequestError
        )
        adapter._litellm.completion.side_effect = FakeBadRequestError(
            'DeepseekException - {"error":{"message":"Authentication Fails, '
            'Your api key: ****fake is invalid","code":"invalid_request_error"}}'
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("API Key 无效或被拒绝", message)
        self.assertIn("platform.deepseek.com/api_keys", message)
        self.assertNotIn("model 名称填写不正确", message)

    def test_bad_request_model_not_found_still_points_to_model(self):
        """真·模型名错误不能被断点 1 的改动误伤。"""
        adapter = self._adapter()

        class FakeBadRequestError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=FakeBadRequestError
        )
        adapter._litellm.completion.side_effect = FakeBadRequestError(
            "DeepseekException - model_not_found: The model `deepseek-chat-999` does not exist"
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("model 名称填写不正确", message)
        self.assertNotIn("API Key 无效或被拒绝", message)

    def test_missing_key_message_preserved(self):
        import os
        env_names = (
            "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
        )
        snapshot = {n: os.environ.get(n) for n in env_names}
        for n in env_names:
            os.environ.pop(n, None)
        try:
            adapter = self._adapter(api_key=None)

            class FakeAuthError(Exception):
                pass

            adapter._litellm.exceptions = MagicMock(
                AuthenticationError=FakeAuthError, BadRequestError=ValueError
            )
            adapter._litellm.completion.side_effect = FakeAuthError("no api key provided")
            with self.assertRaises(RuntimeError) as cm:
                adapter.generate([{"role": "user", "content": "hi"}])
            self.assertIn("未找到可用 API Key", str(cm.exception))
        finally:
            for n, v in snapshot.items():
                if v is None:
                    os.environ.pop(n, None)
                else:
                    os.environ[n] = v

    def test_bad_gateway_gets_chinese_network_guidance(self):
        adapter = self._adapter()

        class BadGatewayError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = BadGatewayError(
            "OpenAIException - Error code: 502"
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("服务端错误", message)
        self.assertIn("服务商临时故障", message)

    def test_connection_error_gets_network_guidance_with_api_base(self):
        adapter = self._adapter(api_base="http://proxy.example.com/v1")

        class APIConnectionError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = APIConnectionError(
            "Connection error."
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("无法连接到 http://proxy.example.com/v1", message)
        self.assertIn("检查网络连接", message)
        self.assertIn("检查代理设置", message)

    def test_timeout_gets_retry_and_config_guidance(self):
        adapter = self._adapter()

        class ReadTimeoutError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = ReadTimeoutError("timed out")
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("超时", message)
        self.assertIn("稍后重试", message)
        self.assertIn("增大 timeout", message)

    def test_ssl_error_gets_certificate_guidance(self):
        adapter = self._adapter()

        class SSLError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = SSLError("certificate verify failed")
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        self.assertIn("SSL 证书验证失败", str(cm.exception))

    def test_rate_limit_error_gets_quota_guidance_with_reset_hint(self):
        """429 / 周期配额耗尽必须指向配额，不能误诊为 key 或模型名问题。"""
        adapter = self._adapter()

        class FakeRateLimitError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = FakeRateLimitError(
            "OpenAIException - Your token-plan 1-week quota has been exhausted. "
            "The quota will reset at 07-28 18:37:00 UTC. "
            "{'type': 'insufficient_quota', 'code': 'insufficient_quota'}"
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("配额已用尽", message)
        self.assertIn("07-28 18:37:00 UTC", message)
        self.assertIn("等配额重置", message)
        self.assertNotIn("API Key 无效", message)
        self.assertNotIn("model 名称填写不正确", message)

    def test_rate_limit_text_signature_without_exception_type(self):
        """异常类型不含 ratelimit 时，靠 429/insufficient_quota 文本特征也要识别。"""
        adapter = self._adapter()

        class GenericAPIError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = GenericAPIError(
            "Client error '429 Too Many Requests' for url "
            "'https://example.com/v1/chat/completions'"
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("配额已用尽", message)
        self.assertNotIn("API Key 无效", message)

    def test_insufficient_balance_still_distinct_from_rate_limit(self):
        """402 余额不足是另一类，不能被 429 分支吞掉。"""
        adapter = self._adapter()

        class FakeRateLimitError(Exception):
            pass

        adapter._litellm.exceptions = MagicMock(
            AuthenticationError=PermissionError, BadRequestError=ValueError
        )
        adapter._litellm.completion.side_effect = FakeRateLimitError(
            'OpenAIException - insufficient balance, {"code":"402"}'
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.generate([{"role": "user", "content": "hi"}])
        message = str(cm.exception)
        self.assertIn("余额或额度不足", message)
        self.assertNotIn("配额已用尽", message)


class TestUnifiedProviderResolution(unittest.TestCase):
    """统一注册表（api/api_mode 新键名 + ${VAR} 插值）在 adapter 解析链路上的行为。"""

    def test_anthropic_messages_api_mode_resolves_anthropic_prefix(self):
        config = LLMConfig(
            model="claude-fable-5",
            custom_providers=[{
                "name": "openmodel",
                "api": "https://api.openmodel.ai/v1",
                "api_mode": "anthropic_messages",
                "api_key": "om-key",
                "models": ["claude-fable-5"],
            }],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "anthropic/claude-fable-5")

    def test_explicit_provider_model_ref_resolves_to_target_model(self):
        config = LLMConfig(
            model="opencode-go/kimi-k3",
            custom_providers=[{
                "name": "opencode-go",
                "api": "https://opencode.ai/zen/go/v1",
                "api_key": "oc-key",
                "models": ["deepseek-v4-flash", "kimi-k3"],
            }],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/kimi-k3")

    def test_env_ref_api_key_is_expanded_for_completion_call(self):
        os.environ["TEST_YMG_KEY"] = "env-ymg-key"
        self.addCleanup(lambda: os.environ.pop("TEST_YMG_KEY", None))
        config = LLMConfig(
            model="ymg-chat",
            custom_providers=[{
                "name": "ymg",
                "api": "https://api.ymg.com/v1",
                "api_key": "${TEST_YMG_KEY}",
                "models": ["ymg-chat"],
            }],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(config.resolve_api_key(), "env-ymg-key")

    def test_provider_level_extra_body_overrides_top_level_in_adapter(self):
        """adapter 侧生效逻辑：provider 条目级 extra_body 覆盖顶层（如 kimi-k2.6 关思考）。"""
        config = LLMConfig(
            model="kimi-k2.6",
            extra_body={"thinking": {"type": "enabled"}},
            custom_providers=[{
                "name": "kimi",
                "api": "https://api.moonshot.cn/v1",
                "api_key": "ms-key",
                "models": ["kimi-k2.6", "kimi-k2.7-code"],
                "extra_body": {"thinking": {"type": "disabled"}},
            }],
        )
        adapter = LLMAdapter(config)
        resolved = adapter._resolve_model_target("kimi-k2.6")
        self.assertEqual(adapter._effective_extra_body(resolved), {"thinking": {"type": "disabled"}})

    def test_adapter_falls_back_to_top_level_extra_body(self):
        """provider 条目无 extra_body 时回退顶层（如 deepseek 顶层关思考不变）。"""
        config = LLMConfig(
            model="deepseek-v4-flash",
            extra_body={"thinking": {"type": "disabled"}},
            custom_providers=[{
                "name": "deepseek",
                "api": "https://api.deepseek.com/v1",
                "api_key": "ds-key",
                "models": ["deepseek-v4-flash"],
            }],
        )
        adapter = LLMAdapter(config)
        resolved = adapter._resolve_model_target("deepseek-v4-flash")
        self.assertEqual(adapter._effective_extra_body(resolved), {"thinking": {"type": "disabled"}})
