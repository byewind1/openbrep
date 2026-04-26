from pathlib import Path
import tempfile
import unittest
import base64
import json
import warnings
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from openbrep.config import LLMConfig, GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMAdapter
from ui.app import (
    _apply_generation_result,
    _handle_hsf_directory_load,
    _handle_unified_import,
    _begin_generation_state,
    _build_assistant_settings_prompt,
    _build_intent_clarification_message,
    _build_model_options,
    _build_model_source_state,
    _key_for_model,
    _build_modify_bridge_prompt,
    _build_post_clarification_input,
    _build_assistant_chat_message,
    _clear_pending_intent_clarification,
    _consume_intent_clarification_choice,
    _detect_image_task_mode,
    _finish_generation_state,
    _find_latest_bridgeable_explainer_message,
    _is_bridgeable_explainer_message,
    _is_explainer_followup_modify_request,
    _is_explainer_intent,
    _is_generation_locked,
    _is_modify_bridge_prompt,
    _is_modify_or_check_intent,
    _is_post_clarification_prompt,
    _maybe_build_followup_bridge_input,
    _maybe_build_intent_clarification,
    _request_generation_cancel,
    _resolve_selected_model,
    _should_accept_generation_result,
    _should_clarify_intent,
    _should_persist_assistant_settings,
    _should_skip_elicitation_for_gdl_request,
    _should_start_elicitation,
    _validate_chat_image_size,
    _capture_last_project_snapshot,
    _restore_last_project_snapshot,
    _route_main_input,
    _verify_pro_code,
    _license_record_is_active,
    _import_pro_knowledge_zip,
    _sync_llm_top_level_fields_for_model,
    _should_show_copyable_chat_content,
    _copyable_chat_text,
    _copy_text_to_system_clipboard,
    _normalize_converter_path,
    classify_and_extract,
)
from ui.revision_controller import (
    restore_project_revision,
    save_current_project_revision,
)

class TestRunAgentGenerateResultPlan(unittest.TestCase):
    def test_run_agent_generate_consumes_runtime_generation_plan(self):
        from ui.app import run_agent_generate

        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        fake_result = MagicMock(
            success=True,
            intent="MODIFY",
            scripts={"scripts/3d.gdl": "BLOCK 2,2,2\nEND\n"},
            plain_text="修改完成",
            project=project,
        )
        fake_plan = MagicMock(
            has_changes=True,
            mode="pending_review",
            label="脚本 [3D]",
            reply_prefix="🤖 **AI 已生成 脚本 [3D]** — 请在下方确认是否写入编辑器。\n\n",
            code_blocks=[{
                "path": "scripts/3d.gdl",
                "label": "3D",
                "language": "gdl",
                "content": "BLOCK 2,2,2\nEND\n",
            }],
        )

        class _StatusCol:
            def empty(self):
                return MagicMock()

        with patch("ui.app.TaskPipeline.execute", return_value=fake_result):
            with patch("ui.app.build_generation_result_plan", return_value=fake_plan) as mock_build_plan:
                with patch("ui.app._begin_generation_state", return_value="gen-1"):
                    with patch("ui.app._is_active_generation", return_value=True):
                        with patch("ui.app._consume_generation_result", return_value=True):
                            with patch("ui.app._finalize_generation"):
                                with patch.dict("ui.app.st.session_state", {
                                    "chat_history": [],
                                    "work_dir": "./workdir",
                                }, clear=False):
                                    reply = run_agent_generate(
                                        "把它改宽一点",
                                        project,
                                        _StatusCol(),
                                        gsm_name="chair",
                                        auto_apply=False,
                                    )

        mock_build_plan.assert_called_once()
        self.assertIn("AI 已生成", reply)
        self.assertIn("```gdl", reply)


class TestRunAgentGenerateRouting(unittest.TestCase):
    def test_run_agent_generate_uses_repair_intent_for_debug_requests(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            return MagicMock(success=True, scripts={}, plain_text="ok", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            with patch.dict("ui.app.st.session_state", {
                                "chat_history": [],
                                "work_dir": "./workdir",
                            }, clear=False):
                                result = run_agent_generate(
                                    "修复这个脚本里的错误",
                                    project,
                                    _StatusCol(),
                                    gsm_name="chair",
                                    auto_apply=True,
                                )

        self.assertEqual(captured.get("intent"), "REPAIR")
        self.assertEqual(result, "ok")

    def test_run_agent_generate_uses_chat_intent_for_explainer_requests(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            return MagicMock(success=True, scripts={}, plain_text="简要拆解", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            with patch.dict("ui.app.st.session_state", {
                                "chat_history": [],
                                "work_dir": "./workdir",
                            }, clear=False):
                                result = run_agent_generate(
                                    "这是什么对象？",
                                    project,
                                    _StatusCol(),
                                    gsm_name="chair",
                                    auto_apply=True,
                                )

        self.assertEqual(captured.get("intent"), "CHAT")
        self.assertEqual(result, "简要拆解")

    def test_run_agent_generate_uses_chat_intent_for_targeted_explainer_requests(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            return MagicMock(success=True, scripts={}, plain_text="A 参数拆解", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            with patch.dict("ui.app.st.session_state", {
                                "chat_history": [],
                                "work_dir": "./workdir",
                            }, clear=False):
                                result = run_agent_generate(
                                    "A/B/ZZYZX 分别控制什么",
                                    project,
                                    _StatusCol(),
                                    gsm_name="chair",
                                    auto_apply=True,
                                )

        self.assertEqual(captured.get("intent"), "CHAT")
        self.assertEqual(result, "A 参数拆解")

    def test_run_agent_generate_uses_chat_intent_for_script_analysis_request(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            return MagicMock(success=True, scripts={}, plain_text="3D 拆解", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            with patch.dict("ui.app.st.session_state", {
                                "chat_history": [],
                                "work_dir": "./workdir",
                            }, clear=False):
                                result = run_agent_generate(
                                    "分析这段 3D 代码逻辑",
                                    project,
                                    _StatusCol(),
                                    gsm_name="chair",
                                    auto_apply=True,
                                )

        self.assertEqual(captured.get("intent"), "CHAT")
        self.assertEqual(result, "3D 拆解")

    def test_followup_bridge_uses_modify_intent_via_existing_generate_path(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            captured["user_input"] = request.user_input
            return MagicMock(success=True, scripts={}, plain_text="修改完成", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            prompt = _maybe_build_followup_bridge_input(
                                user_input="按你刚才说的改",
                                history=[
                                    _build_assistant_chat_message(
                                        content="3D 脚本主要负责主体几何。",
                                        intent="CHAT",
                                        has_project=True,
                                        source_user_input="解释一下 3D 脚本",
                                    )
                                ],
                                has_project=True,
                            )
                            result = run_agent_generate(
                                prompt,
                                project,
                                _StatusCol(),
                                gsm_name="chair",
                                auto_apply=True,
                            )

        self.assertEqual(captured.get("intent"), "MODIFY")
        self.assertIn("用户修改要求：按你刚才说的改", captured.get("user_input", ""))
        self.assertEqual(result, "修改完成")

    def test_clarification_confirmation_routes_to_chat_for_explain_choice(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            captured["user_input"] = request.user_input
            return MagicMock(success=True, scripts={}, plain_text="简要拆解", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            clarified = _build_post_clarification_input(
                                "解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
                                "1",
                            )
                            result = run_agent_generate(
                                clarified,
                                project,
                                _StatusCol(),
                                gsm_name="chair",
                                auto_apply=True,
                            )

        self.assertEqual(captured.get("intent"), "CHAT")
        self.assertIn("本次确认目标：先快速解释脚本结构", captured.get("user_input", ""))
        self.assertEqual(result, "简要拆解")

    def test_clarification_confirmation_routes_to_modify_for_check_choice(self):
        from ui.app import run_agent_generate

        captured = {}
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1\nEND\n"

        class _StatusCol:
            def empty(self):
                return MagicMock()

        def fake_execute(self, request):
            captured["intent"] = request.intent
            captured["user_input"] = request.user_input
            return MagicMock(success=True, scripts={}, plain_text="检查完成", project=project)

        with patch("ui.app.TaskPipeline.execute", fake_execute):
            with patch("ui.app._begin_generation_state", return_value="gen-1"):
                with patch("ui.app._is_active_generation", return_value=True):
                    with patch("ui.app._consume_generation_result", return_value=True):
                        with patch("ui.app._finalize_generation"):
                            clarified = _build_post_clarification_input(
                                "解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
                                "2",
                            )
                            result = run_agent_generate(
                                clarified,
                                project,
                                _StatusCol(),
                                gsm_name="chair",
                                auto_apply=True,
                            )

        self.assertEqual(captured.get("intent"), "MODIFY")
        self.assertIn("本次确认目标：先检查明显错误/风险", captured.get("user_input", ""))
        self.assertEqual(result, "检查完成")


class TestExplainerModifyBridgeHelpers(unittest.TestCase):
    def test_bridgeable_explainer_message_requires_metadata(self):
        self.assertTrue(_is_bridgeable_explainer_message({
            "role": "assistant",
            "content": "简要拆解",
            "bridgeable_action": "modify_from_explainer",
        }))
        self.assertFalse(_is_bridgeable_explainer_message({
            "role": "assistant",
            "content": "普通回复",
        }))
        self.assertFalse(_is_bridgeable_explainer_message({
            "role": "user",
            "content": "简要拆解",
            "bridgeable_action": "modify_from_explainer",
        }))

    def test_explainer_followup_modify_request_matches_high_confidence_phrases(self):
        self.assertTrue(_is_explainer_followup_modify_request("按你刚才说的改"))
        self.assertTrue(_is_explainer_followup_modify_request("按这个思路改"))
        self.assertTrue(_is_explainer_followup_modify_request("那就改吧"))
        self.assertTrue(_is_explainer_followup_modify_request("就按这个改"))
        self.assertTrue(_is_explainer_followup_modify_request("按这个修改"))

    def test_explainer_followup_modify_request_rejects_normal_chat(self):
        self.assertFalse(_is_explainer_followup_modify_request("解释得不错"))
        self.assertFalse(_is_explainer_followup_modify_request("再详细讲讲"))
        self.assertFalse(_is_explainer_followup_modify_request("为什么这么改"))
        self.assertFalse(_is_explainer_followup_modify_request("把宽度改成 1200"))

    def test_find_latest_bridgeable_explainer_message_returns_latest_assistant_match(self):
        history = [
            {"role": "assistant", "content": "普通回复"},
            _build_assistant_chat_message(
                content="先前解释",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下 3D",
            ),
            {"role": "user", "content": "收到"},
            _build_assistant_chat_message(
                content="最近解释",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下参数",
            ),
        ]

        message = _find_latest_bridgeable_explainer_message(history)

        self.assertIsNotNone(message)
        self.assertEqual(message["content"], "最近解释")
        self.assertEqual(message["bridge_source_user_input"], "解释一下参数")

    def test_build_modify_bridge_prompt_includes_source_request(self):
        prompt = _build_modify_bridge_prompt({
            "role": "assistant",
            "content": "3D 脚本主要负责主体几何。",
            "bridge_source_user_input": "解释一下 3D 脚本",
        })
        self.assertIn("原解释问题：解释一下 3D 脚本", prompt)
        self.assertIn("解释结论：3D 脚本主要负责主体几何。", prompt)
        self.assertIn("用户修改要求：请按上面的解释做最小必要修改。", prompt)

    def test_maybe_build_followup_bridge_input_returns_prompt_for_whitelist_match(self):
        history = [
            _build_assistant_chat_message(
                content="3D 脚本主要负责主体几何。",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下 3D 脚本",
            )
        ]

        prompt = _maybe_build_followup_bridge_input(
            user_input="按你刚才说的改",
            history=history,
            has_project=True,
        )

        self.assertIn("原解释问题：解释一下 3D 脚本", prompt)
        self.assertIn("解释结论：3D 脚本主要负责主体几何。", prompt)
        self.assertIn("用户修改要求：按你刚才说的改", prompt)

    def test_maybe_build_followup_bridge_input_returns_none_without_bridgeable_message(self):
        prompt = _maybe_build_followup_bridge_input(
            user_input="按你刚才说的改",
            history=[{"role": "assistant", "content": "普通聊天"}],
            has_project=True,
        )

        self.assertIsNone(prompt)

    def test_maybe_build_followup_bridge_input_returns_none_without_project(self):
        history = [
            _build_assistant_chat_message(
                content="3D 脚本主要负责主体几何。",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下 3D 脚本",
            )
        ]

        prompt = _maybe_build_followup_bridge_input(
            user_input="按你刚才说的改",
            history=history,
            has_project=False,
        )

        self.assertIsNone(prompt)

    def test_maybe_build_followup_bridge_input_uses_latest_bridgeable_message_only(self):
        history = [
            _build_assistant_chat_message(
                content="旧解释",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下旧脚本",
            ),
            {"role": "assistant", "content": "普通回复"},
            _build_assistant_chat_message(
                content="新解释",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下新脚本",
            ),
        ]

        prompt = _maybe_build_followup_bridge_input(
            user_input="就按这个改",
            history=history,
            has_project=True,
        )

        self.assertIn("原解释问题：解释一下新脚本", prompt)
        self.assertIn("解释结论：新解释", prompt)
        self.assertNotIn("旧解释", prompt)

    def test_button_bridge_prompt_still_uses_default_fallback_request(self):
        prompt = _build_modify_bridge_prompt({
            "role": "assistant",
            "content": "3D 脚本主要负责主体几何。",
            "bridgeable_action": "modify_from_explainer",
            "bridge_source_user_input": "解释一下 3D 脚本",
        })

        self.assertIn("用户修改要求：请按上面的解释做最小必要修改。", prompt)

    def test_modify_bridge_prompt_is_detected_as_modify(self):
        prompt = _build_modify_bridge_prompt({
            "role": "assistant",
            "content": "3D 脚本主要负责主体几何。",
            "bridgeable_action": "modify_from_explainer",
            "bridge_source_user_input": "解释一下 3D 脚本",
        }, fallback_request="按你刚才说的改")

        self.assertTrue(_is_modify_bridge_prompt(prompt))
        self.assertFalse(_is_modify_bridge_prompt("解释一下 3D 脚本"))

    def test_followup_bridge_does_not_change_repair_routing(self):
        self.assertFalse(_is_explainer_followup_modify_request("修复这个脚本里的错误"))

    def test_build_assistant_chat_message_marks_only_project_chat_as_bridgeable(self):
        bridgeable = _build_assistant_chat_message(
            content="简要拆解",
            intent="CHAT",
            has_project=True,
            source_user_input="解释一下 3D 脚本",
        )
        plain_chat = _build_assistant_chat_message(
            content="普通闲聊",
            intent="CHAT",
            has_project=False,
            source_user_input="你是谁",
        )
        modify_reply = _build_assistant_chat_message(
            content="修改完成",
            intent="MODIFY",
            has_project=True,
            source_user_input="把它改宽一点",
        )

        self.assertEqual(bridgeable["bridgeable_action"], "modify_from_explainer")
        self.assertEqual(bridgeable["bridge_source_user_input"], "解释一下 3D 脚本")
        self.assertNotIn("bridgeable_action", plain_chat)
        self.assertNotIn("bridge_source_user_input", plain_chat)
        self.assertNotIn("bridgeable_action", modify_reply)
        self.assertNotIn("bridge_source_user_input", modify_reply)

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


class TestIntentRoutingHelpers(unittest.TestCase):
    def test_modify_or_check_intent_matches_fix_request(self):
        self.assertTrue(_is_modify_or_check_intent("把 3D 脚本改一下"))

    def test_modify_or_check_intent_does_not_match_generation_request(self):
        self.assertFalse(_is_modify_or_check_intent("创建一个书架"))

    def test_modify_or_check_intent_does_not_match_general_question(self):
        self.assertFalse(_is_modify_or_check_intent("为什么这个对象要用 GDL"))

    def test_explainer_intent_matches_explanation_request(self):
        self.assertTrue(_is_explainer_intent("这是什么对象？"))
        self.assertTrue(_is_explainer_intent("详细讲讲这个对象"))
        self.assertTrue(_is_explainer_intent("A/B/ZZYZX 分别控制什么"))
        self.assertTrue(_is_explainer_intent("3D 脚本负责什么"))
        self.assertTrue(_is_explainer_intent("分析这段 3D 代码逻辑"))

    def test_explainer_intent_does_not_match_modify_or_repair_request(self):
        self.assertFalse(_is_explainer_intent("把 3D 脚本改一下"))
        self.assertFalse(_is_explainer_intent("修复这个脚本里的错误"))

    def test_should_clarify_intent_matches_mixed_request(self):
        self.assertTrue(_should_clarify_intent(
            "解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
            has_project=True,
            history=[],
        ))

    def test_should_clarify_intent_matches_ambiguous_short_request(self):
        self.assertTrue(_should_clarify_intent(
            "看看这个",
            has_project=True,
            history=[],
        ))

    def test_should_clarify_intent_skips_high_confidence_explainer(self):
        self.assertFalse(_should_clarify_intent(
            "解释一下 3D 脚本",
            has_project=True,
            history=[],
        ))

    def test_should_clarify_intent_skips_high_confidence_modify(self):
        self.assertFalse(_should_clarify_intent(
            "把宽度改成 1200",
            has_project=True,
            history=[],
        ))

    def test_should_clarify_intent_skips_followup_bridge_phrase(self):
        history = [
            _build_assistant_chat_message(
                content="3D 脚本主要负责主体几何。",
                intent="CHAT",
                has_project=True,
                source_user_input="解释一下 3D 脚本",
            )
        ]
        self.assertFalse(_should_clarify_intent(
            "按你刚才说的改",
            has_project=True,
            history=history,
        ))

    def test_maybe_build_intent_clarification_returns_payload_for_mixed_request(self):
        payload = _maybe_build_intent_clarification(
            user_input="解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
            has_project=True,
            history=[],
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["recommended_option"], "2")
        self.assertIn("我猜你现在更像是想先检查", payload["message"])
        self.assertIn("1. 先快速解释脚本结构", payload["message"])
        self.assertIn("2. 先检查明显错误/风险", payload["message"])
        self.assertIn("回复数字就行", payload["message"])

    def test_maybe_build_intent_clarification_returns_none_for_clear_request(self):
        payload = _maybe_build_intent_clarification(
            user_input="修复这个脚本里的错误",
            has_project=True,
            history=[],
        )
        self.assertIsNone(payload)

    def test_consume_intent_clarification_choice_returns_none_without_pending_state(self):
        self.assertIsNone(_consume_intent_clarification_choice("2", None))

    def test_consume_intent_clarification_choice_returns_prompt_for_numeric_reply(self):
        pending = {
            "original_user_input": "解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
            "recommended_option": "2",
            "options": {
                "1": "explain",
                "2": "check",
                "3": "suggest",
                "4": "review_summary",
            },
        }

        clarified = _consume_intent_clarification_choice("2", pending)

        self.assertIsNotNone(clarified)
        self.assertIn("用户原始请求：解释一下导入的自动扶梯脚本，并检查错误，提出修改意见", clarified)
        self.assertIn("本次确认目标：先检查明显错误/风险", clarified)

    def test_consume_intent_clarification_choice_returns_none_for_non_numeric_reply(self):
        pending = {
            "original_user_input": "看看这个",
            "recommended_option": "2",
            "options": {"1": "explain", "2": "check"},
        }
        self.assertIsNone(_consume_intent_clarification_choice("先看 3D", pending))

    def test_build_intent_clarification_message_uses_numbered_choices(self):
        message = _build_intent_clarification_message("2")
        self.assertIn("1. 先快速解释脚本结构", message)
        self.assertIn("2. 先检查明显错误/风险", message)
        self.assertIn("3. 先给修改建议", message)
        self.assertIn("4. 按顺序都做，但先给简版总检", message)

    def test_build_post_clarification_input_contains_original_request(self):
        prompt = _build_post_clarification_input(
            "看看这个",
            "2",
        )
        self.assertIn("用户原始请求：看看这个", prompt)
        self.assertIn("本次确认目标：先检查明显错误/风险", prompt)


    def test_route_main_input_returns_debug_for_error_text(self):
        intent, _obj_name = _route_main_input("Error in 3D script, line 12", project_loaded=True)
        self.assertEqual(intent, "DEBUG")

    def test_route_main_input_returns_modify_for_loaded_project(self):
        intent, _obj_name = _route_main_input("把层板改成 6 个", project_loaded=True)
        self.assertEqual(intent, "MODIFY")

    def test_should_skip_elicitation_for_modify_request(self):
        self.assertTrue(_should_skip_elicitation_for_gdl_request("帮我检查这段脚本语法", "MODIFY"))

    def test_should_skip_elicitation_for_debug_request(self):
        self.assertTrue(_should_skip_elicitation_for_gdl_request("Error in 3D script, line 12", "DEBUG"))

    def test_should_not_skip_elicitation_for_generate_request(self):
        self.assertFalse(_should_skip_elicitation_for_gdl_request("创建一个书架", "CREATE"))

    def test_should_start_elicitation_for_generation_text(self):
        self.assertTrue(_should_start_elicitation("创建一个书架"))

    def test_should_not_start_elicitation_for_modify_text(self):
        self.assertFalse(_should_start_elicitation("帮我检查这段脚本语法"))

    def test_pure_chat_still_classifies_as_chat(self):
        llm = MagicMock()
        intent, _obj_name = classify_and_extract("你能做什么", llm, project_loaded=True)
        self.assertEqual(intent, "CHAT")

    def test_post_clarification_prompt_is_detected(self):
        prompt = _build_post_clarification_input(
            "解释一下导入的自动扶梯脚本，并检查错误，提出修改意见",
            "2",
        )
        self.assertTrue(_is_post_clarification_prompt(prompt))
        self.assertFalse(_is_post_clarification_prompt("解释一下 3D 脚本"))

    def test_clear_pending_intent_clarification_resets_session_state(self):
        with patch.dict("ui.app.st.session_state", {"pending_intent_clarification": {"original_user_input": "看看这个"}}, clear=False):
            _clear_pending_intent_clarification()
            self.assertIsNone(__import__("ui.app", fromlist=["st"]).st.session_state["pending_intent_clarification"])

    def test_explainer_intent_still_skips_clarification(self):
        self.assertFalse(_should_clarify_intent("分析这段 3D 代码逻辑", True, []))

    def test_repair_intent_still_skips_clarification(self):
        self.assertFalse(_should_clarify_intent("修复这个脚本里的错误", True, []))

    def test_non_numeric_followup_clears_pending_and_does_not_retrigger_clarification(self):
        """非数字补充句既不被消费，也不会再次触发澄清，确保新输入能直接进入正常路由。"""
        pending = {
            "original_user_input": "看看这个",
            "recommended_option": "2",
            "options": {"1": "explain", "2": "check", "3": "suggest", "4": "review_summary"},
        }
        followup = "我想先看 3D 脚本结构"

        # 非数字 → 不消费 pending，主流程应清除旧 pending
        self.assertIsNone(_consume_intent_clarification_choice(followup, pending))
        # 新输入本身属于高置信 explainer，不会再触发澄清
        self.assertFalse(_should_clarify_intent(followup, has_project=True, history=[]))


def _build_signed_license_code(payload: dict) -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(
        canonical_payload,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    record = {
        "payload": payload,
        "signature": base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("="),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    return public_pem.decode("utf-8"), f"OBRLIC-{encoded}"


class TestProLicenseVerification(unittest.TestCase):
    def test_verify_pro_code_accepts_valid_signed_code(self):
        payload = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, code = _build_signed_license_code(payload)

        with patch("ui.knowledge_access._load_pro_public_key") as load_key:
            load_key.return_value = serialization.load_pem_public_key(public_pem.encode("utf-8"))
            ok, msg, record = _verify_pro_code(code)

        self.assertTrue(ok)
        self.assertEqual(msg, "授权码有效")
        self.assertIsNotNone(record)
        self.assertTrue(record["pro_unlocked"])
        self.assertEqual(record["buyer_id"], "buyer-001")
        self.assertEqual(record["plan"], "annual")

    def test_verify_pro_code_rejects_tampered_payload(self):
        payload = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, code = _build_signed_license_code(payload)
        raw = code[len("OBRLIC-"):]
        record = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        record["payload"]["plan"] = "lifetime"
        tampered = "OBRLIC-" + base64.urlsafe_b64encode(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("utf-8").rstrip("=")

        with patch("ui.knowledge_access._load_pro_public_key") as load_key:
            load_key.return_value = serialization.load_pem_public_key(public_pem.encode("utf-8"))
            ok, msg, record = _verify_pro_code(tampered)

        self.assertFalse(ok)
        self.assertEqual(msg, "授权签名无效")
        self.assertIsNone(record)

    def test_verify_pro_code_rejects_expired_license(self):
        payload = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "product": "openbrep-pro",
            "version": "0.6",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2020-01-01",
        }
        public_pem, code = _build_signed_license_code(payload)

        with patch("ui.knowledge_access._load_pro_public_key") as load_key:
            load_key.return_value = serialization.load_pem_public_key(public_pem.encode("utf-8"))
            ok, msg, record = _verify_pro_code(code)

        self.assertFalse(ok)
        self.assertEqual(msg, "授权码已过期")
        self.assertIsNone(record)

    def test_license_record_is_active_revalidates_saved_record(self):
        payload = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, code = _build_signed_license_code(payload)
        raw = code[len("OBRLIC-"):]
        record_blob = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        saved = {
            "pro_unlocked": True,
            "license_payload": record_blob["payload"],
            "license_signature": record_blob["signature"],
        }

        with patch("ui.knowledge_access._load_pro_public_key") as load_key:
            load_key.return_value = serialization.load_pem_public_key(public_pem.encode("utf-8"))
            ok, msg, record = _license_record_is_active(saved)

        self.assertTrue(ok)
        self.assertEqual(msg, "授权码有效")
        self.assertIsNotNone(record)
        self.assertEqual(record["buyer_id"], "buyer-001")


class TestProKnowledgePackageVerification(unittest.TestCase):
    def _build_signed_package_bytes(self, manifest: dict, tamper_signature: bool = False) -> bytes:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        signature = private_key.sign(
            manifest_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        if tamper_signature:
            signature = signature[:-1] + bytes([signature[-1] ^ 0x01])

        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_bytes)
            zf.writestr("signature.sig", signature)
            zf.writestr("docs/test.md", "# test\n")
        return public_pem, buf.getvalue()

    def test_import_pro_knowledge_zip_accepts_signed_obrk(self):
        manifest = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, package_bytes = self._build_signed_package_bytes(manifest)

        with tempfile.TemporaryDirectory() as tmpdir:
            license_record = {
                "pro_unlocked": True,
                "buyer_id": "buyer-001",
                "license_payload": manifest,
                "license_signature": "dummy-signature",
            }
            normalized_license = dict(license_record)
            with patch("ui.knowledge_access._load_pro_public_key") as load_key:
                with patch("ui.knowledge_access._load_license", return_value=license_record):
                    with patch("ui.knowledge_access._license_record_is_active", return_value=(True, "授权码有效", normalized_license)):
                        with patch("ui.knowledge_access._save_license"):
                            load_key.return_value = serialization.load_pem_public_key(public_pem)
                            ok, msg = _import_pro_knowledge_zip(package_bytes, "demo.obrk", tmpdir)

            self.assertTrue(ok)
            self.assertIn("导入完成", msg)
            self.assertTrue((Path(tmpdir) / "pro_knowledge" / "test.md").exists())

    def test_import_pro_knowledge_zip_rejects_invalid_signature(self):
        manifest = {
            "buyer_id": "buyer-001",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, package_bytes = self._build_signed_package_bytes(manifest, tamper_signature=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            license_record = {
                "pro_unlocked": True,
                "buyer_id": "buyer-001",
                "license_payload": manifest,
                "license_signature": "dummy-signature",
            }
            normalized_license = dict(license_record)
            with patch("ui.knowledge_access._load_pro_public_key") as load_key:
                with patch("ui.knowledge_access._load_license", return_value=license_record):
                    with patch("ui.knowledge_access._license_record_is_active", return_value=(True, "授权码有效", normalized_license)):
                        with patch("ui.knowledge_access._save_license"):
                            load_key.return_value = serialization.load_pem_public_key(public_pem)
                            ok, msg = _import_pro_knowledge_zip(package_bytes, "demo.obrk", tmpdir)

            self.assertFalse(ok)
            self.assertIn("知识包签名无效", msg)
            self.assertFalse((Path(tmpdir) / "pro_knowledge").exists())

    def test_import_pro_knowledge_zip_rejects_mismatched_buyer(self):
        manifest = {
            "buyer_id": "buyer-002",
            "email": "buyer@example.com",
            "plan": "annual",
            "issued_at": "2026-04-06T12:00:00",
            "expire_date": "2099-12-31",
        }
        public_pem, package_bytes = self._build_signed_package_bytes(manifest)

        with tempfile.TemporaryDirectory() as tmpdir:
            license_record = {
                "pro_unlocked": True,
                "buyer_id": "buyer-001",
                "license_payload": {
                    "buyer_id": "buyer-001",
                    "email": "buyer@example.com",
                    "plan": "annual",
                    "issued_at": "2026-04-06T12:00:00",
                    "expire_date": "2099-12-31",
                },
                "license_signature": "dummy-signature",
            }
            normalized_license = dict(license_record)
            with patch("ui.knowledge_access._load_pro_public_key") as load_key:
                with patch("ui.knowledge_access._load_license", return_value=license_record):
                    with patch("ui.knowledge_access._license_record_is_active", return_value=(True, "授权码有效", normalized_license)):
                        with patch("ui.knowledge_access._save_license"):
                            load_key.return_value = serialization.load_pem_public_key(public_pem)
                            ok, msg = _import_pro_knowledge_zip(package_bytes, "demo.obrk", tmpdir)

            self.assertFalse(ok)
            self.assertIn("知识包不属于当前授权用户", msg)
            self.assertFalse((Path(tmpdir) / "pro_knowledge").exists())


class TestUndoLastAIWrite(unittest.TestCase):
    def test_restore_last_project_snapshot_returns_error_when_missing(self):
        with patch.dict("ui.app.st.session_state", {
            "last_project_snapshot": None,
            "last_project_snapshot_meta": {},
            "last_project_snapshot_label": "",
        }, clear=False):
            ok, msg = _restore_last_project_snapshot()

        self.assertFalse(ok)
        self.assertIn("没有可恢复的上一次 AI 写入", msg)

    def test_apply_generation_result_auto_apply_captures_snapshot_before_write(self):
        proj = MagicMock()
        cleaned = {"scripts/3d.gdl": "BLOCK 1,1,1\nEND"}
        calls = []

        with patch("ui.app._parse_paramlist_text", return_value=[]):
            with patch("ui.app._capture_last_project_snapshot", side_effect=lambda label: calls.append(("capture", label))):
                with patch("ui.app._apply_scripts_to_project", side_effect=lambda proj_arg, cleaned_arg: calls.append(("apply", cleaned_arg))):
                    with patch("ui.app._bump_main_editor_version"):
                        with patch.dict("ui.app.st.session_state", {
                            "pending_gsm_name": "",
                        }, clear=False):
                            _apply_generation_result(cleaned, proj, "chair", auto_apply=True, already_applied=False)

        self.assertEqual(calls[0], ("capture", "AI 自动写入"))
        self.assertEqual(calls[1], ("apply", cleaned))

    def test_capture_and_restore_last_project_snapshot(self):
        original = MagicMock()
        original.name = "chair"
        original.parameters = []
        original.scripts = {"scripts/3d.gdl": "OLD"}

        current = MagicMock()
        current.name = "chair"
        current.parameters = []
        current.scripts = {"scripts/3d.gdl": "NEW"}

        with patch("ui.app.deepcopy", side_effect=lambda x: x):
            with patch("ui.app._bump_main_editor_version") as bump_editor:
                with patch.dict("ui.app.st.session_state", {
                    "project": original,
                    "pending_gsm_name": "chair",
                    "script_revision": 3,
                    "pending_diffs": {"scripts/3d.gdl": "NEW"},
                    "pending_ai_label": "脚本 [3D]",
                    "preview_2d_data": object(),
                    "preview_3d_data": object(),
                    "preview_warnings": ["old"],
                    "preview_meta": {"kind": "old", "timestamp": "old"},
                    "last_project_snapshot": None,
                    "last_project_snapshot_meta": {},
                    "last_project_snapshot_label": "",
                }, clear=False):
                    _capture_last_project_snapshot("AI 确认写入")
                    __import__("ui.app", fromlist=["st"]).st.session_state.project = current
                    ok, msg = _restore_last_project_snapshot()
                    state = __import__("ui.app", fromlist=["st"]).st.session_state

                    self.assertTrue(ok)
                    self.assertIn("已撤销上次", msg)
                    self.assertIn("AI 确认写入", msg)
                    self.assertIs(state.project, original)
                    self.assertEqual(state.pending_gsm_name, "chair")
                    self.assertEqual(state.script_revision, 3)
                    self.assertEqual(state.pending_diffs, {})
                    self.assertEqual(state.pending_ai_label, "")
                    self.assertIsNone(state.preview_2d_data)
                    self.assertIsNone(state.preview_3d_data)
                    self.assertEqual(state.preview_warnings, [])
                    self.assertEqual(state.preview_meta, {"kind": "", "timestamp": ""})
                    self.assertIsNone(state.last_project_snapshot)
                    self.assertEqual(state.last_project_snapshot_meta, {})
                    self.assertEqual(state.last_project_snapshot_label, "")
                    bump_editor.assert_called_once_with()

    def test_restore_last_project_snapshot_uses_label_in_success_message(self):
        project = MagicMock()
        project.name = "chair"
        project.parameters = []
        project.scripts = {"scripts/3d.gdl": "OLD"}

        with patch("ui.app.deepcopy", side_effect=lambda x: x):
            with patch("ui.app._bump_main_editor_version"):
                with patch.dict("ui.app.st.session_state", {
                    "last_project_snapshot": {
                        "project": project,
                        "pending_gsm_name": "chair",
                        "script_revision": 1,
                    },
                    "last_project_snapshot_meta": {"label": "AI 自动写入"},
                    "last_project_snapshot_label": "AI 自动写入",
                    "pending_diffs": {},
                    "pending_ai_label": "",
                }, clear=False):
                    ok, msg = _restore_last_project_snapshot()

        self.assertTrue(ok)
        self.assertIn("AI 自动写入", msg)


class TestImportFlows(unittest.TestCase):
    def test_save_current_project_revision_persists_hsf_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("Chair", work_dir=tmpdir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nEND\n")

            ok, msg = save_current_project_revision(project, "initial")

            self.assertTrue(ok, msg)
            self.assertIn("r0001", msg)
            self.assertTrue((Path(tmpdir) / "Chair" / ".openbrep" / "revisions" / "r0001").exists())

    def test_restore_project_revision_reloads_project_and_resets_editor_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("Chair", work_dir=tmpdir)
            project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nEND\n")
            ok, _msg = save_current_project_revision(project, "initial")
            self.assertTrue(ok)

            project.set_script(ScriptType.SCRIPT_3D, "CYLIND 1, 1\nEND\n")
            project.save_to_disk()

            with patch("ui.app._reset_tapir_p0_state") as reset_tapir:
                with patch("ui.app._bump_main_editor_version") as bump_editor:
                    with patch.dict("ui.app.st.session_state", {
                        "project": project,
                        "pending_diffs": {"old": 1},
                        "pending_ai_label": "old",
                        "compile_result": (True, "old"),
                        "preview_2d_data": object(),
                        "preview_3d_data": object(),
                        "preview_warnings": ["old"],
                        "preview_meta": {"kind": "old", "timestamp": "old"},
                    }, clear=False):
                        ok, msg = restore_project_revision(
                            project,
                            "r0001",
                            session_state=__import__("ui.app", fromlist=["st"]).st.session_state,
                            load_project_from_disk_fn=HSFProject.load_from_disk,
                            reset_tapir_p0_state_fn=reset_tapir,
                            bump_main_editor_version_fn=bump_editor,
                        )
                        state = __import__("ui.app", fromlist=["st"]).st.session_state
                        restored_script = state.project.get_script(ScriptType.SCRIPT_3D)
                        pending_gsm_name = state.pending_gsm_name
                        pending_diffs = dict(state.pending_diffs)
                        pending_ai_label = state.pending_ai_label
                        compile_result = state.compile_result
                        preview_2d_data = state.preview_2d_data
                        preview_3d_data = state.preview_3d_data
                        preview_warnings = list(state.preview_warnings)
                        preview_meta = dict(state.preview_meta)

            self.assertTrue(ok, msg)
            self.assertIn("r0002", msg)
            self.assertEqual(restored_script, "BLOCK A, B, ZZYZX\nEND\n")
            self.assertEqual(pending_gsm_name, "Chair")
            self.assertEqual(pending_diffs, {})
            self.assertEqual(pending_ai_label, "")
            self.assertIsNone(compile_result)
            self.assertIsNone(preview_2d_data)
            self.assertIsNone(preview_3d_data)
            self.assertEqual(preview_warnings, [])
            self.assertEqual(preview_meta, {"kind": "", "timestamp": ""})
            reset_tapir.assert_called_once_with()
            bump_editor.assert_called_once_with()

    def test_hsf_directory_load_sets_current_project_and_resets_editor_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Chair"
            project_dir.mkdir()
            loaded_proj = MagicMock()
            loaded_proj.name = "untitled"
            loaded_proj.parameters = []
            loaded_proj.scripts = {}

            with patch("ui.app.HSFProject.load_from_disk", return_value=loaded_proj) as load_from_disk:
                with patch("ui.app._reset_tapir_p0_state") as reset_tapir:
                    with patch("ui.app._bump_main_editor_version") as bump_editor:
                        with patch.dict("ui.app.st.session_state", {
                            "work_dir": tmpdir,
                            "project": None,
                            "pending_diffs": {"old": 1},
                            "chat_history": [],
                            "preview_2d_data": object(),
                            "preview_3d_data": object(),
                            "preview_warnings": ["old"],
                            "preview_meta": {"kind": "old", "timestamp": "old"},
                            "pending_gsm_name": "old-name",
                            "script_revision": 9,
                        }, clear=False):
                            ok, msg = _handle_hsf_directory_load(str(project_dir))

            self.assertTrue(ok)
            self.assertIn("已加载 HSF 项目", msg)
            load_from_disk.assert_called_once_with(str(project_dir))
            self.assertEqual(loaded_proj.work_dir, Path(tmpdir))
            self.assertEqual(loaded_proj.root, Path(tmpdir) / "untitled")
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.pending_diffs, {})
            self.assertIsNone(__import__("ui.app", fromlist=["st"]).st.session_state.preview_2d_data)
            self.assertIsNone(__import__("ui.app", fromlist=["st"]).st.session_state.preview_3d_data)
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.preview_warnings, [])
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.preview_meta, {"kind": "", "timestamp": ""})
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.pending_gsm_name, "untitled")
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.script_revision, 0)
            reset_tapir.assert_called_once_with()
            bump_editor.assert_called_once_with()

    def test_hsf_directory_load_rejects_missing_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_dir = Path(tmpdir) / "missing"

            with patch("ui.app.HSFProject.load_from_disk") as load_from_disk:
                with patch.dict("ui.app.st.session_state", {
                    "work_dir": tmpdir,
                    "chat_history": [],
                }, clear=False):
                    ok, msg = _handle_hsf_directory_load(str(missing_dir))

            self.assertFalse(ok)
            self.assertIn("目录不存在", msg)
            load_from_disk.assert_not_called()

    def test_hsf_directory_load_accepts_single_quoted_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Quoted Chair"
            project_dir.mkdir()
            loaded_proj = MagicMock()
            loaded_proj.name = "quoted-chair"
            loaded_proj.parameters = []
            loaded_proj.scripts = {}

            with patch("ui.app.HSFProject.load_from_disk", return_value=loaded_proj) as load_from_disk:
                with patch.dict("ui.app.st.session_state", {
                    "work_dir": tmpdir,
                    "chat_history": [],
                }, clear=False):
                    ok, _msg = _handle_hsf_directory_load(f"'{project_dir}'")

            self.assertTrue(ok)
            load_from_disk.assert_called_once_with(str(project_dir))

    def test_hsf_directory_load_sets_pending_name_from_project_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Desk"
            project_dir.mkdir()
            loaded_proj = MagicMock()
            loaded_proj.name = "untitled"
            loaded_proj.parameters = []
            loaded_proj.scripts = {}

            with patch("ui.app.HSFProject.load_from_disk", return_value=loaded_proj):
                with patch.dict("ui.app.st.session_state", {
                    "work_dir": tmpdir,
                    "chat_history": [],
                }, clear=False):
                    ok, _msg = _handle_hsf_directory_load(str(project_dir))

            self.assertTrue(ok)
            self.assertEqual(__import__("ui.app", fromlist=["st"]).st.session_state.pending_gsm_name, "untitled")

    def test_gsm_import_saves_hsf_into_work_dir_immediately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploaded = MagicMock()
            uploaded.name = "chair.gsm"
            uploaded.read.return_value = b"fake-gsm"

            proj = MagicMock()
            proj.name = "chair"
            proj.parameters = []
            proj.scripts = {}

            with patch("ui.app.import_gsm", return_value=(proj, "ok")):
                with patch("ui.app.st.spinner") as spinner:
                    spinner.return_value.__enter__.return_value = None
                    spinner.return_value.__exit__.return_value = None
                    with patch.dict("ui.app.st.session_state", {
                        "work_dir": tmpdir,
                        "project": None,
                        "pending_diffs": {"old": 1},
                        "editor_version": 0,
                        "pending_gsm_name": "",
                    }, clear=False):
                        ok, _msg = _handle_unified_import(uploaded)

            self.assertTrue(ok)
            self.assertEqual(proj.work_dir, Path(tmpdir))
            self.assertEqual(proj.root, Path(tmpdir) / "chair")
            proj.save_to_disk.assert_called_once_with()

    def test_import_gsm_copies_hsf_root_into_work_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_compiler = MagicMock()
            fake_compiler.is_available = True
            fake_compiler.converter_path = "/tmp/LP_XMLConverter"
            fake_compiler.libpart2hsf.return_value = MagicMock(success=True, stdout="", stderr="", exit_code=0)

            created_temp = Path(tmpdir) / "temp-import"
            created_temp.mkdir()

            def fake_libpart2hsf(_gsm_path, hsf_out_path):
                hsf_out = Path(hsf_out_path)
                hsf_root = hsf_out / "Chair"
                scripts_dir = hsf_root / "scripts"
                scripts_dir.mkdir(parents=True)
                (hsf_root / "libpartdata.xml").write_text("<Symbol></Symbol>", encoding="utf-8")
                (scripts_dir / "3d.gdl").write_text("BLOCK 1,1,1\nEND", encoding="utf-8")
                return MagicMock(success=True, stdout="", stderr="", exit_code=0)

            fake_compiler.libpart2hsf.side_effect = fake_libpart2hsf

            with patch("ui.app.get_compiler", return_value=fake_compiler):
                with patch("ui.app.HSFProject.load_from_disk") as load_from_disk:
                    loaded_proj = MagicMock()
                    loaded_proj.name = "Chair"
                    loaded_proj.parameters = []
                    script = MagicMock()
                    script.value = "3d.gdl"
                    loaded_proj.scripts = {script: "BLOCK 1,1,1\nEND"}
                    load_from_disk.return_value = loaded_proj
                    with patch("tempfile.mkdtemp", return_value=str(created_temp)):
                        with patch.dict("ui.app.st.session_state", {"work_dir": tmpdir}, clear=False):
                            project_dir, msg = __import__("ui.app", fromlist=["import_gsm"]).import_gsm(b"fake", "Chair.gsm")

            self.assertTrue(project_dir, msg)
            self.assertEqual(Path(project_dir), Path(tmpdir) / "Chair")
            self.assertTrue((Path(project_dir) / "libpartdata.xml").exists())
            self.assertTrue((Path(project_dir) / "scripts" / "3d.gdl").exists())
            self.assertIn("已导入", msg)

    def test_import_gsm_avoids_overwriting_existing_workdir_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing = Path(tmpdir) / "Chair"
            existing.mkdir()
            (existing / "keep.txt").write_text("old", encoding="utf-8")

            fake_compiler = MagicMock()
            fake_compiler.is_available = True
            fake_compiler.converter_path = "/tmp/LP_XMLConverter"

            created_temp = Path(tmpdir) / "temp-import"
            created_temp.mkdir()

            def fake_libpart2hsf(_gsm_path, hsf_out_path):
                hsf_out = Path(hsf_out_path)
                hsf_root = hsf_out / "Chair"
                scripts_dir = hsf_root / "scripts"
                scripts_dir.mkdir(parents=True)
                (hsf_root / "libpartdata.xml").write_text("<Symbol></Symbol>", encoding="utf-8")
                (scripts_dir / "3d.gdl").write_text("BLOCK 1,1,1\nEND", encoding="utf-8")
                return MagicMock(success=True, stdout="", stderr="", exit_code=0)

            fake_compiler.libpart2hsf.side_effect = fake_libpart2hsf

            with patch("ui.app.get_compiler", return_value=fake_compiler):
                with patch("ui.app.HSFProject.load_from_disk") as load_from_disk:
                    loaded_proj = MagicMock()
                    loaded_proj.name = "Chair"
                    loaded_proj.parameters = []
                    script = MagicMock()
                    script.value = "3d.gdl"
                    loaded_proj.scripts = {script: "BLOCK 1,1,1\nEND"}
                    load_from_disk.return_value = loaded_proj
                    with patch("tempfile.mkdtemp", return_value=str(created_temp)):
                        with patch.dict("ui.app.st.session_state", {"work_dir": tmpdir}, clear=False):
                            project_dir, _msg = __import__("ui.app", fromlist=["import_gsm"]).import_gsm(b"fake", "Chair.gsm")

            self.assertEqual(Path(project_dir).name, "Chair_imported_2")
            self.assertTrue((existing / "keep.txt").exists())

    def test_gsm_import_reloads_project_from_imported_workdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploaded = MagicMock()
            uploaded.name = "chair.gsm"
            uploaded.read.return_value = b"fake-gsm"

            imported_dir = Path(tmpdir) / "chair-imported"
            reloaded_proj = MagicMock()
            reloaded_proj.name = "chair"
            reloaded_proj.parameters = []
            reloaded_proj.scripts = {}

            with patch("ui.app.import_gsm", return_value=(imported_dir, "ok")):
                with patch("ui.app.HSFProject.load_from_disk", return_value=reloaded_proj) as load_from_disk:
                    with patch("ui.app.st.spinner") as spinner:
                        spinner.return_value.__enter__.return_value = None
                        spinner.return_value.__exit__.return_value = None
                        with patch.dict("ui.app.st.session_state", {
                            "work_dir": tmpdir,
                            "project": None,
                            "pending_diffs": {"old": 1},
                            "editor_version": 0,
                            "pending_gsm_name": "",
                            "chat_history": [],
                            "preview_2d_data": object(),
                            "preview_3d_data": object(),
                            "preview_warnings": ["old"],
                            "preview_meta": {"kind": "old", "timestamp": "old"},
                            "script_revision": 7,
                        }, clear=False):
                            ok, _msg = _handle_unified_import(uploaded)

            self.assertTrue(ok)
            load_from_disk.assert_called_once_with(str(imported_dir))
            self.assertEqual(reloaded_proj.work_dir, Path(tmpdir))
            self.assertEqual(reloaded_proj.root, Path(tmpdir) / "chair")
            reloaded_proj.save_to_disk.assert_called_once_with()


class TestGenerationStateHelpers(unittest.TestCase):
    def test_begin_generation_state_creates_running_session(self):
        state = {}

        generation_id = _begin_generation_state(state)

        self.assertTrue(generation_id)
        self.assertEqual(state["active_generation_id"], generation_id)
        self.assertEqual(state["generation_status"], "running")
        self.assertFalse(state["generation_cancel_requested"])
        self.assertTrue(state["agent_running"])

    def test_begin_generation_state_replaces_previous_session(self):
        state = {}
        first_id = _begin_generation_state(state)

        second_id = _begin_generation_state(state)

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(state["active_generation_id"], second_id)
        self.assertEqual(state["generation_status"], "running")

    def test_request_generation_cancel_marks_matching_session(self):
        state = {}
        generation_id = _begin_generation_state(state)

        cancelled = _request_generation_cancel(state, generation_id)

        self.assertTrue(cancelled)
        self.assertTrue(state["generation_cancel_requested"])
        self.assertEqual(state["generation_status"], "cancelling")
        self.assertTrue(state["agent_running"])

    def test_request_generation_cancel_ignores_stale_session(self):
        state = {}
        _begin_generation_state(state)
        active_id = _begin_generation_state(state)

        cancelled = _request_generation_cancel(state, "stale-id")

        self.assertFalse(cancelled)
        self.assertEqual(state["active_generation_id"], active_id)
        self.assertFalse(state["generation_cancel_requested"])
        self.assertEqual(state["generation_status"], "running")

    def test_should_accept_generation_result_rejects_cancelled_session(self):
        state = {}
        generation_id = _begin_generation_state(state)
        _request_generation_cancel(state, generation_id)

        self.assertFalse(_should_accept_generation_result(state, generation_id))

    def test_should_accept_generation_result_rejects_stale_session(self):
        state = {}
        stale_id = _begin_generation_state(state)
        _begin_generation_state(state)

        self.assertFalse(_should_accept_generation_result(state, stale_id))

    def test_finish_generation_state_only_active_session_unlocks(self):
        state = {}
        stale_id = _begin_generation_state(state)
        active_id = _begin_generation_state(state)

        stale_finished = _finish_generation_state(state, stale_id, "completed")

        self.assertFalse(stale_finished)
        self.assertTrue(state["agent_running"])
        self.assertEqual(state["active_generation_id"], active_id)
        self.assertEqual(state["generation_status"], "running")

        active_finished = _finish_generation_state(state, active_id, "completed")

        self.assertTrue(active_finished)
        self.assertFalse(state["agent_running"])
        self.assertIsNone(state["active_generation_id"])
        self.assertEqual(state["generation_status"], "completed")

    def test_clear_project_can_call_editor_bump_function(self):
        from ui.app import _bump_main_editor_version

        self.assertTrue(callable(_bump_main_editor_version))

        self.assertEqual(_build_assistant_settings_prompt("  \n  "), "")

    def test_build_assistant_settings_prompt_wraps_user_preferences(self):
        prompt = _build_assistant_settings_prompt("我是 GDL 初学者，请先解释再给最小修改。")
        self.assertIn("AI助手设置", prompt)
        self.assertIn("GDL 初学者", prompt)
        self.assertIn("不能覆盖", prompt)

    def test_should_persist_assistant_settings_detects_real_change(self):
        self.assertTrue(_should_persist_assistant_settings("旧值", "新值"))

    def test_should_persist_assistant_settings_ignores_same_value(self):
        self.assertFalse(_should_persist_assistant_settings("同一个值", "同一个值"))

    def test_should_persist_assistant_settings_uses_config_value_not_session_value(self):
        self.assertTrue(_should_persist_assistant_settings("", "用户刚填的内容"))

    def test_build_model_options_keeps_builtin_label(self):
        options = _build_model_options(["gpt-5.4"], [])
        self.assertEqual(options[0]["label"], "gpt-5.4")
        self.assertEqual(options[0]["actual_model"], "gpt-5.4")
        self.assertFalse(options[0]["is_custom"])

    def test_build_model_options_aliases_custom_models(self):
        options = _build_model_options(
            ["foo-model", "bar-model", "gpt-5.4"],
            [{"models": ["foo-model", "bar-model"]}],
        )
        self.assertEqual(options[0]["label"], "自定义1")
        self.assertEqual(options[1]["label"], "自定义2")
        self.assertEqual(options[0]["actual_model"], "foo-model")
        self.assertEqual(options[1]["actual_model"], "bar-model")
        self.assertTrue(options[0]["is_custom"])

    def test_build_model_options_avoids_exposing_custom_raw_name_when_conflicts_with_builtin(self):
        options = _build_model_options(
            ["gpt-5.4", "gpt-5.4"],
            [{"models": ["gpt-5.4"]}],
        )
        labels = [o["label"] for o in options]
        self.assertEqual(labels, ["自定义1", "自定义2"])

    def test_build_model_source_state_defaults_to_custom_when_custom_providers_exist(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[{"models": ["foo-model", "bar-model"]}],
            saved_model="",
        )
        self.assertEqual(state["default_source"], "自定义")
        self.assertEqual([o["label"] for o in state["custom_options"]], ["自定义1", "自定义2"])
        self.assertEqual(state["default_model_label"], "自定义1")

    def test_build_model_source_state_defaults_to_official_without_custom_providers(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[],
            saved_model="",
        )
        self.assertEqual(state["default_source"], "官方供应商")
        self.assertEqual([o["label"] for o in state["builtin_options"]], ["gpt-5.4", "glm-4-flash"])
        self.assertEqual(state["default_model_label"], "gpt-5.4")

    def test_build_model_source_state_restores_custom_saved_model(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[{"models": ["foo-model", "bar-model"]}],
            saved_model="bar-model",
        )
        self.assertEqual(state["default_source"], "自定义")
        self.assertEqual(state["default_model_label"], "自定义2")

    def test_build_model_source_state_restores_official_saved_model(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[{"models": ["foo-model"]}],
            saved_model="glm-4-flash",
        )
        self.assertEqual(state["default_source"], "官方供应商")
        self.assertEqual(state["default_model_label"], "glm-4-flash")

    def test_build_model_source_state_uses_custom_provider_name_as_label(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[{"name": "ymg", "models": ["gpt-5.4"]}],
            saved_model="",
        )
        self.assertEqual([o["label"] for o in state["custom_options"]], ["ymg"])
        self.assertEqual(state["default_model_label"], "ymg")

    def test_build_model_source_state_uses_provider_name_with_model_when_provider_has_multiple_models(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4"],
            custom_providers=[{"name": "ymg", "models": ["gpt-5.4", "gpt-4o"]}],
            saved_model="gpt-4o",
        )
        self.assertEqual([o["label"] for o in state["custom_options"]], ["ymg / gpt-5.4", "ymg / gpt-4o"])
        self.assertEqual(state["default_model_label"], "ymg / gpt-4o")

    def test_build_model_source_state_falls_back_to_custom_alias_when_name_missing(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4"],
            custom_providers=[{"models": ["foo-model"]}],
            saved_model="",
        )
        self.assertEqual([o["label"] for o in state["custom_options"]], ["自定义1"])

    def test_build_model_source_state_uses_alias_for_custom_object_entries(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4"],
            custom_providers=[{"name": "ymg", "models": [{"alias": "ymg-glm-5.4", "model": "glm-5.4"}]}],
            saved_model="ymg-glm-5.4",
        )
        self.assertEqual([o["label"] for o in state["custom_options"]], ["ymg"])
        self.assertEqual(state["default_source"], "自定义")
        self.assertEqual(state["default_model_label"], "ymg")

    def test_build_model_source_state_tolerates_missing_config_object(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4"],
            custom_providers=[],
            saved_model="",
        )
        self.assertEqual(state["source_options"], ["官方供应商"])

    def test_build_model_source_state_keeps_conflicting_builtin_and_custom_in_separate_buckets(self):
        state = _build_model_source_state(
            builtin_models=["gpt-5.4", "glm-4-flash"],
            custom_providers=[{"models": ["gpt-5.4"]}],
            saved_model="",
        )
        self.assertEqual([o["label"] for o in state["custom_options"]], ["自定义1"])
        self.assertEqual([o["label"] for o in state["builtin_options"]], ["gpt-5.4", "glm-4-flash"])

    def test_assistant_message_is_directly_copyable(self):
        self.assertTrue(_should_show_copyable_chat_content({"role": "assistant", "content": "hello"}))

    def test_user_message_is_not_directly_copyable(self):
        self.assertFalse(_should_show_copyable_chat_content({"role": "user", "content": "hi"}))

    def test_copyable_chat_text_returns_assistant_content(self):
        self.assertEqual(_copyable_chat_text({"role": "assistant", "content": "hello"}), "hello")

    def test_copyable_chat_text_returns_empty_for_user_message(self):
        self.assertEqual(_copyable_chat_text({"role": "user", "content": "hi"}), "")

    def test_normalize_converter_path_preserves_backslashes_on_windows(self):
        with patch("ui.app.sys.platform", "win32"):
            normalized = _normalize_converter_path(r'"C:\Program Files\GRAPHISOFT\ArchiCAD 29\LP_XMLConverter.exe"')
        self.assertEqual(normalized, r"C:\Program Files\GRAPHISOFT\ArchiCAD 29\LP_XMLConverter.exe")

    def test_normalize_converter_path_normalizes_slashes_on_macos(self):
        with patch("ui.app.sys.platform", "darwin"):
            normalized = _normalize_converter_path(r'"C:\Program Files\GRAPHISOFT\ArchiCAD 29\LP_XMLConverter.exe"')
        self.assertEqual(normalized, "C:/Program Files/GRAPHISOFT/ArchiCAD 29/LP_XMLConverter.exe")

    def test_copy_text_to_system_clipboard_uses_pbcopy_on_macos(self):
        with patch("ui.app.sys.platform", "darwin"), patch("ui.app.subprocess.run") as run:
            run.return_value = MagicMock()
            ok, msg = _copy_text_to_system_clipboard("hello")
        self.assertTrue(ok)
        self.assertIn("已复制", msg)
        run.assert_called_once_with(
            ["pbcopy"],
            input="hello",
            text=True,
            check=True,
            timeout=2,
        )

    def test_copy_text_to_system_clipboard_rejects_empty_text(self):
        ok, msg = _copy_text_to_system_clipboard("")
        self.assertFalse(ok)
        self.assertIn("无可复制", msg)

    def test_sync_llm_top_level_fields_for_custom_model_only_updates_model(self):
        cfg = GDLAgentConfig(
            llm=LLMConfig(
                model="old-model",
                api_key="top-level-old",
                api_base="https://old-base/v1",
                custom_providers=[
                    {
                        "name": "ymg",
                        "base_url": "https://api.ymg.com/v1",
                        "api_key": "ymg-key",
                        "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                        "protocol": "openai",
                    }
                ],
            )
        )

        changed = _sync_llm_top_level_fields_for_model(cfg, "ymg-gpt-5.3-codex")

        self.assertTrue(changed)
        self.assertEqual(cfg.llm.model, "ymg-gpt-5.3-codex")
        self.assertEqual(cfg.llm.api_key, "top-level-old")
        self.assertEqual(cfg.llm.api_base, "https://old-base/v1")

    def test_key_for_model_matches_custom_alias_object_entry(self):
        with patch("ui.app._custom_providers", [{
            "name": "nvidia",
            "api_key": "nv-key",
            "models": [{"alias": "moonshotai/kimi-k2.5", "model": "openai/moonshotai/kimi-k2.5"}],
        }]), patch("ui.app._provider_keys", {}):
            self.assertEqual(_key_for_model("moonshotai/kimi-k2.5"), "nv-key")

    def test_key_for_model_matches_custom_model_object_entry(self):
        with patch("ui.app._custom_providers", [{
            "name": "nvidia",
            "api_key": "nv-key",
            "models": [{"alias": "moonshotai/kimi-k2.5", "model": "openai/moonshotai/kimi-k2.5"}],
        }]), patch("ui.app._provider_keys", {}):
            self.assertEqual(_key_for_model("openai/moonshotai/kimi-k2.5"), "nv-key")

    def test_key_for_model_keeps_builtin_provider_fallback(self):
        with patch("ui.app._custom_providers", []), patch("ui.app._provider_keys", {"openai": "openai-key"}):
            self.assertEqual(_key_for_model("gpt-5.4"), "openai-key")

        agent = GDLAgent(llm=MagicMock(), assistant_settings="我是 GDL 初学者")

        prompt = agent._build_system_prompt("", "", chat_mode=False)

        self.assertIn("AI助手设置", prompt)
        self.assertIn("我是 GDL 初学者", prompt)

    def test_system_prompt_omits_assistant_settings_when_blank(self):
        agent = GDLAgent(llm=MagicMock(), assistant_settings="")

        prompt = agent._build_system_prompt("", "", chat_mode=False)

        self.assertNotIn("AI助手设置", prompt)
class TestLengthUnitNormalization(unittest.TestCase):
    def test_parse_param_text_converts_mm_suffix_to_meters(self):
        agent = GDLAgent(llm=MagicMock())
        params = agent._parse_param_text("Length A = 600mm ! width")
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0].name, "A")
        self.assertEqual(params[0].value, "0.6")

    def test_parse_param_text_converts_large_length_number_as_mm(self):
        agent = GDLAgent(llm=MagicMock())
        params = agent._parse_param_text("Length B = 1200 ! depth")
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0].name, "B")
        self.assertEqual(params[0].value, "1.2")


if __name__ == "__main__":
    unittest.main()
