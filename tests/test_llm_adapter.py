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

    def test_provider_level_temperature_overrides_top_level_in_adapter(self):
        """adapter 侧生效逻辑：provider 条目级 temperature 覆盖顶层（kimi 端点约束配套）。"""
        config = LLMConfig(
            model="kimi-k2.6",
            temperature=0.2,
            custom_providers=[{
                "name": "kimi-vision",
                "api": "https://api.moonshot.cn/v1",
                "api_key": "ms-key",
                "models": ["kimi-k2.6"],
                "temperature": 0.6,
                "extra_body": {"thinking": {"type": "disabled"}},
            }],
        )
        adapter = LLMAdapter(config)
        resolved = adapter._resolve_model_target("kimi-k2.6")
        self.assertEqual(adapter._effective_temperature(resolved), 0.6)


class TestCodexAppServerModelFailClosed(unittest.TestCase):
    """D1+D3：openai-codex 订阅模型——只有 CHAT/EXPLAIN 走 app-server turn；
    其余意图必须 fail closed，绝不落到 litellm/API-key。"""

    def _adapter(self, model="openai-codex/gpt-5.6-luna"):
        config = LLMConfig(
            model=model,
            api_key="fake-codex-key-never-used",
            custom_providers=[
                {
                    "name": "openai-codex",
                    "api_mode": "codex_app_server",
                    "api_key": "",
                    "models": [],
                }
            ],
        )
        adapter = LLMAdapter(config)
        # 即使 litellm 可用且配置了 key，也不允许走到生成
        adapter._litellm = MagicMock()
        return adapter

    def test_generate_without_chat_intent_raises_and_does_not_call_litellm(self):
        adapter = self._adapter()
        with self.assertRaisesRegex(RuntimeError, "openai-codex"):
            adapter.generate([{"role": "user", "content": "hi"}])
        adapter._litellm.completion.assert_not_called()

    def test_generate_with_explicit_codex_model_without_chat_intent_raises(self):
        adapter = self._adapter(model="glm-4-flash")
        with self.assertRaisesRegex(RuntimeError, "openai-codex"):
            adapter.generate(
                [{"role": "user", "content": "hi"}],
                model="openai-codex/gpt-5.6-luna",
            )
        adapter._litellm.completion.assert_not_called()

    def test_generate_chat_intent_without_provider_fails_closed(self):
        from openbrep.codex.provider import get_default_codex_provider, set_default_codex_provider

        saved = get_default_codex_provider()
        set_default_codex_provider(None)
        adapter = self._adapter()
        adapter.codex_provider = None  # 显式不注入
        try:
            with self.assertRaisesRegex(RuntimeError, "openai-codex|Codex"):
                adapter.generate(
                    [{"role": "user", "content": "hi"}],
                    codex_intent="CHAT",
                )
        finally:
            set_default_codex_provider(saved)
        adapter._litellm.completion.assert_not_called()

    def test_generate_chat_intent_routes_to_codex_provider(self):
        from openbrep.codex.turn import CodexTurnResult

        calls = {}

        class _FakeProvider:
            def chat(self, messages, model, **kwargs):
                calls["messages"] = messages
                calls["model"] = model
                return CodexTurnResult(
                    content="你好，我是 Codex。",
                    model=model,
                    finish_reason="stop",
                )

        adapter = self._adapter()
        adapter.codex_provider = _FakeProvider()
        result = adapter.generate(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            codex_intent="CHAT",
        )
        self.assertEqual(result.content, "你好，我是 Codex。")
        self.assertEqual(result.model, "openai-codex/gpt-5.6-luna")
        self.assertEqual(calls["model"], "openai-codex/gpt-5.6-luna")
        self.assertEqual(calls["messages"][1]["content"], "hi")
        adapter._litellm.completion.assert_not_called()

    def test_generate_chat_intent_signed_out_maps_to_stable_message(self):
        from openbrep.codex.provider import CodexNotSignedInError

        class _SignedOutProvider:
            def chat(self, messages, model, **kwargs):
                raise CodexNotSignedInError("尚未连接 ChatGPT。")

        adapter = self._adapter()
        adapter.codex_provider = _SignedOutProvider()
        with self.assertRaises(RuntimeError) as ctx:
            adapter.generate([{"role": "user", "content": "hi"}], codex_intent="CHAT")
        self.assertIn("ChatGPT", str(ctx.exception))
        adapter._litellm.completion.assert_not_called()

    def test_generate_chat_intent_no_final_maps_to_stable_message(self):
        from openbrep.codex.turn import NO_FINAL_MESSAGE_TEXT, CodexTurnResult

        class _NoFinalProvider:
            def chat(self, messages, model, **kwargs):
                return CodexTurnResult(
                    model=model,
                    finish_reason="no_final_message",
                    error=NO_FINAL_MESSAGE_TEXT,
                )

        adapter = self._adapter()
        adapter.codex_provider = _NoFinalProvider()
        with self.assertRaisesRegex(RuntimeError, "未返回最终回复"):
            adapter.generate([{"role": "user", "content": "hi"}], codex_intent="CHAT")
        adapter._litellm.completion.assert_not_called()

    # ── D4：文本 CREATE 意图归一化（同一 turn 契约，只收集 final text）──────

    def test_generate_create_intent_routes_to_codex_provider(self):
        from openbrep.codex.turn import CodexTurnResult

        calls = {}

        class _FakeProvider:
            def chat(self, messages, model, **kwargs):
                calls["messages"] = messages
                calls["model"] = model
                calls["kwargs"] = kwargs
                return CodexTurnResult(
                    content="[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n",
                    model=model,
                    finish_reason="stop",
                )

        adapter = self._adapter()
        adapter.codex_provider = _FakeProvider()
        result = adapter.generate(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "生成书架"}],
            codex_intent="CREATE",
            codex_should_cancel=lambda: False,
            codex_on_event=lambda *a: None,
        )
        self.assertIn("[FILE: scripts/3d.gdl]", result.content)
        self.assertEqual(result.model, "openai-codex/gpt-5.6-luna")
        self.assertEqual(calls["model"], "openai-codex/gpt-5.6-luna")
        self.assertEqual(calls["messages"][1]["content"], "生成书架")
        # turn 参数透传：取消回调与事件回调原样到达 provider.chat
        self.assertIsNotNone(calls["kwargs"]["should_cancel"])
        self.assertIsNotNone(calls["kwargs"]["on_event"])
        adapter._litellm.completion.assert_not_called()

    def test_generate_create_intent_no_final_maps_to_stable_message(self):
        from openbrep.codex.turn import NO_FINAL_MESSAGE_TEXT, CodexTurnResult

        class _NoFinalProvider:
            def chat(self, messages, model, **kwargs):
                return CodexTurnResult(
                    model=model,
                    finish_reason="no_final_message",
                    error=NO_FINAL_MESSAGE_TEXT,
                )

        adapter = self._adapter()
        adapter.codex_provider = _NoFinalProvider()
        with self.assertRaisesRegex(RuntimeError, "未返回最终回复"):
            adapter.generate([{"role": "user", "content": "hi"}], codex_intent="CREATE")
        adapter._litellm.completion.assert_not_called()

    def test_generate_create_intent_signed_out_maps_to_stable_message(self):
        from openbrep.codex.provider import CodexNotSignedInError

        class _SignedOutProvider:
            def chat(self, messages, model, **kwargs):
                raise CodexNotSignedInError("尚未连接 ChatGPT。")

        adapter = self._adapter()
        adapter.codex_provider = _SignedOutProvider()
        with self.assertRaises(RuntimeError) as ctx:
            adapter.generate([{"role": "user", "content": "hi"}], codex_intent="CREATE")
        self.assertIn("ChatGPT", str(ctx.exception))
        adapter._litellm.completion.assert_not_called()

    def test_generate_modify_intent_still_fails_closed(self):
        """D4 只开文本 CREATE：MODIFY/DEBUG 意图绝不经 turn 发给订阅模型。"""
        adapter = self._adapter()
        adapter.codex_provider = MagicMock()
        for intent in ("MODIFY", "DEBUG", "REPAIR", "IMAGE"):
            with self.assertRaisesRegex(RuntimeError, "openai-codex"):
                adapter.generate(
                    [{"role": "user", "content": "hi"}],
                    codex_intent=intent,
                )
        adapter.codex_provider.chat.assert_not_called()
        adapter._litellm.completion.assert_not_called()

    def test_generate_with_image_codex_fails_closed(self):
        adapter = self._adapter()
        with self.assertRaisesRegex(RuntimeError, "openai-codex"):
            adapter.generate_with_image("描述这张图", "aGVsbG8=", system_prompt="sys")
        adapter._litellm.completion.assert_not_called()

    def test_non_codex_models_unaffected(self):
        adapter = self._adapter(model="glm-4-flash")
        built_response = MagicMock()
        built_response.choices = [MagicMock()]
        built_response.choices[0].message.content = "ok"
        built_response.choices[0].finish_reason = "stop"
        built_response.model = "glm-4-flash"
        built_response.usage = {"prompt_tokens": 1}
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built_response
        result = adapter.generate([{"role": "user", "content": "hi"}])
        self.assertEqual(result.content, "ok")
        adapter._litellm.completion.assert_called_once()

    def test_codex_intent_kwargs_never_reach_litellm_for_non_codex(self):
        adapter = self._adapter(model="glm-4-flash")
        built_response = MagicMock()
        built_response.choices = [MagicMock()]
        built_response.choices[0].message.content = "ok"
        built_response.choices[0].finish_reason = "stop"
        built_response.model = "glm-4-flash"
        built_response.usage = {"prompt_tokens": 1}
        adapter._litellm.completion.return_value = [MagicMock(), MagicMock()]
        adapter._litellm.stream_chunk_builder.return_value = built_response
        result = adapter.generate(
            [{"role": "user", "content": "hi"}],
            codex_intent="CHAT",
            codex_should_cancel=lambda: False,
            codex_on_event=lambda *a: None,
        )
        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertNotIn("codex_intent", kwargs)
        self.assertNotIn("codex_should_cancel", kwargs)
        self.assertNotIn("codex_on_event", kwargs)
