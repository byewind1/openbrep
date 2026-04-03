from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from openbrep.config import LLMConfig
from openbrep.core import GDLAgent
from openbrep.llm import LLMAdapter
from ui.app import (
    _handle_unified_import,
    _begin_generation_state,
    _build_assistant_settings_prompt,
    _build_model_options,
    _build_model_source_state,
    _detect_image_task_mode,
    _finish_generation_state,
    _is_generation_locked,
    _request_generation_cancel,
    _resolve_selected_model,
    _should_accept_generation_result,
    _should_persist_assistant_settings,
    _validate_chat_image_size,
)


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
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response()

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

    def test_gpt5_custom_provider_model_stays_unprefixed(self):
        config = LLMConfig(
            model="gpt-5.4",
            custom_providers=[{"name": "ymg", "models": ["gpt-5.4"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "gpt-5.4")

    def test_non_gpt_custom_model_stays_unprefixed(self):
        config = LLMConfig(
            model="ymg-chat",
            custom_providers=[{"name": "ymg", "models": ["ymg-chat"], "protocol": "openai"}],
        )
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "ymg-chat")

    def test_builtin_gpt5_model_keeps_openai_prefix(self):
        config = LLMConfig(model="gpt-5.4")
        adapter = LLMAdapter(config)
        self.assertEqual(adapter._resolve_model_string(), "openai/gpt-5.4")

    def test_generate_with_non_gpt_custom_model_uses_plain_kwargs(self):
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
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="ymg-chat")

        result = adapter.generate([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "ok")
        kwargs = adapter._litellm.completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "ymg-chat")
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertEqual(kwargs["max_tokens"], 9999)
        self.assertEqual(kwargs["timeout"], 22)
        self.assertEqual(kwargs["api_base"], "https://api.airsim.eu.cc/v1")
        self.assertNotIn("drop_params", kwargs)

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
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = [chunk1, chunk2, chunk3]

        result = adapter.generate([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "hello world")
        self.assertEqual(result.model, "openai/gpt-5.4")
        self.assertEqual(result.usage, {})
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
        adapter._litellm = MagicMock()
        adapter._litellm.completion.return_value = self._mock_response(model_name="openai/gpt-5.4")

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




class TestImportFlows(unittest.TestCase):
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

        agent = GDLAgent(llm=MagicMock(), assistant_settings="我是 GDL 初学者")

        prompt = agent._build_system_prompt("", "", chat_mode=False)

        self.assertIn("AI助手设置", prompt)
        self.assertIn("我是 GDL 初学者", prompt)

    def test_system_prompt_omits_assistant_settings_when_blank(self):
        agent = GDLAgent(llm=MagicMock(), assistant_settings="")

        prompt = agent._build_system_prompt("", "", chat_mode=False)

        self.assertNotIn("AI助手设置", prompt)


if __name__ == "__main__":
    unittest.main()
