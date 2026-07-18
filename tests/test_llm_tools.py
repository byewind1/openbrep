"""llm.py tools 协议（Phase 3 前置）合同测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from openbrep.config import LLMConfig
from openbrep.llm import (
    LLMAdapter,
    MockLLM,
    ToolCall,
    ToolDefinition,
    assistant_tool_calls_message,
    tool_result_message,
)


def _make_tool_call_response(model="openai/gpt-4o", name="run_compile", args='{"script": "3d.gdl"}'):
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_choice.finish_reason = "tool_calls"
    tc = MagicMock()
    tc.id = "call_123"
    tc.function.name = name
    tc.function.arguments = args
    mock_choice.message.tool_calls = [tc]
    mock_response.choices = [mock_choice]
    mock_response.model = model
    mock_response.usage = {"prompt_tokens": 10}
    return mock_response


class TestToolDefinition(unittest.TestCase):
    def test_to_openai_dict(self):
        tool = ToolDefinition(
            name="compile_gdl",
            description="编译当前脚本",
            parameters={"type": "object", "properties": {"script": {"type": "string"}}},
        )
        d = tool.to_openai_dict()
        self.assertEqual(d["type"], "function")
        self.assertEqual(d["function"]["name"], "compile_gdl")
        self.assertIn("script", d["function"]["parameters"]["properties"])

    def test_empty_parameters_default_to_object_schema(self):
        d = ToolDefinition(name="noop").to_openai_dict()
        self.assertEqual(d["function"]["parameters"]["type"], "object")


class TestGenerateWithTools(unittest.TestCase):
    def _adapter(self, model="gpt-4o", **config_kwargs):
        config = LLMConfig(model=model, api_key="test-key", timeout=10, **config_kwargs)
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        return adapter

    def test_passes_tools_and_disables_stream(self):
        adapter = self._adapter()
        adapter._litellm.completion.return_value = _make_tool_call_response()

        tools = [ToolDefinition(name="run_compile", description="编译")]
        result = adapter.generate_with_tools(
            [{"role": "user", "content": "编译一下"}], tools=tools,
        )

        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertFalse(kwargs["stream"])
        self.assertEqual(kwargs["tool_choice"], "auto")
        self.assertEqual(kwargs["tools"][0]["function"]["name"], "run_compile")
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertTrue(result.has_tool_calls)
        self.assertEqual(result.tool_calls[0].name, "run_compile")
        self.assertEqual(result.tool_calls[0].arguments, {"script": "3d.gdl"})
        self.assertEqual(result.tool_calls[0].id, "call_123")

    def test_dict_tools_pass_through(self):
        adapter = self._adapter()
        adapter._litellm.completion.return_value = _make_tool_call_response()
        raw_tool = {"type": "function", "function": {"name": "x", "parameters": {}}}
        adapter.generate_with_tools([{"role": "user", "content": "hi"}], tools=[raw_tool])
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["tools"], [raw_tool])

    def test_invalid_tool_arguments_degrade_to_empty_dict(self):
        adapter = self._adapter()
        adapter._litellm.completion.return_value = _make_tool_call_response(args="{not json")
        result = adapter.generate_with_tools(
            [{"role": "user", "content": "hi"}], tools=[ToolDefinition(name="t")],
        )
        self.assertEqual(result.tool_calls[0].arguments, {})
        self.assertEqual(result.tool_calls[0].raw_arguments, "{not json")

    def test_custom_provider_anthropic_protocol_resolution(self):
        config = LLMConfig(
            model="my-claude",
            timeout=10,
            custom_providers=[{
                "name": "myproxy",
                "base_url": "https://proxy.example.com/v1",
                "api_key": "proxy-key",
                "models": ["my-claude"],
                "protocol": "anthropic",
            }],
        )
        adapter = LLMAdapter(config)
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = _make_tool_call_response(model="anthropic/my-claude")

        adapter.generate_with_tools([{"role": "user", "content": "hi"}], tools=[ToolDefinition(name="t")])

        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "anthropic/my-claude")
        self.assertEqual(kwargs["api_base"], "https://proxy.example.com/v1")
        self.assertEqual(kwargs["api_key"], "proxy-key")
        self.assertIn("tools", kwargs)

    def test_tool_role_messages_pass_through(self):
        adapter = self._adapter()
        adapter._litellm.completion.return_value = _make_tool_call_response()
        history = [
            {"role": "user", "content": "编译"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "run_compile", "arguments": "{}"}},
            ]},
            tool_result_message("call_1", "compile ok", name="run_compile"),
        ]
        adapter.generate_with_tools(history, tools=[ToolDefinition(name="run_compile")])
        sent = adapter._litellm.completion.call_args.kwargs["messages"]
        self.assertEqual(sent[1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(sent[2]["role"], "tool")
        self.assertEqual(sent[2]["tool_call_id"], "call_1")

    def test_no_tool_calls_returns_plain_content(self):
        adapter = self._adapter()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "done"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.model = "openai/gpt-4o"
        mock_response.usage = {}
        adapter._litellm.completion.return_value = mock_response

        result = adapter.generate_with_tools(
            [{"role": "user", "content": "hi"}], tools=[ToolDefinition(name="t")],
        )
        self.assertEqual(result.content, "done")
        self.assertFalse(result.has_tool_calls)


class TestToolMessageHelpers(unittest.TestCase):
    def test_tool_result_message_shape(self):
        msg = tool_result_message("call_9", "ok", name="run_compile")
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "call_9")
        self.assertEqual(msg["content"], "ok")
        self.assertEqual(msg["name"], "run_compile")

    def test_assistant_tool_calls_message_round_trip(self):
        from openbrep.llm import LLMResponse

        response = LLMResponse(
            content="",
            model="m",
            tool_calls=[ToolCall(id="call_1", name="run_compile",
                                 arguments={"a": 1}, raw_arguments='{"a": 1}')],
        )
        msg = assistant_tool_calls_message(response)
        self.assertEqual(msg["role"], "assistant")
        self.assertIsNone(msg["content"])
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "run_compile")
        self.assertEqual(msg["tool_calls"][0]["function"]["arguments"], '{"a": 1}')


class TestMockLLMTools(unittest.TestCase):
    def test_mock_scripted_tool_call_loop(self):
        mock = MockLLM(responses=[
            {"tool_calls": [{"name": "run_compile", "arguments": {"script": "3d.gdl"}}]},
            "编译通过，任务完成",
        ])
        first = mock.generate_with_tools([{"role": "user", "content": "编译"}], tools=[])
        self.assertTrue(first.has_tool_calls)
        self.assertEqual(first.finish_reason, "tool_calls")
        self.assertEqual(first.tool_calls[0].arguments, {"script": "3d.gdl"})

        second = mock.generate_with_tools(
            [tool_result_message(first.tool_calls[0].id, "ok")], tools=[],
        )
        self.assertFalse(second.has_tool_calls)
        self.assertEqual(second.content, "编译通过，任务完成")


if __name__ == "__main__":
    unittest.main()
