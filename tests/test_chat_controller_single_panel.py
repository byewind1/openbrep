import unittest
from unittest.mock import patch

from ui.chat_controller import run_normal_text_path, run_vision_path


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _DummyContainer:
    def __init__(self):
        self.entered = 0

    def container(self):
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestChatControllerSinglePanel(unittest.TestCase):
    def test_run_normal_text_path_does_not_use_streamlit_chat_message(self):
        session_state = _State(
            chat_history=[],
            project=object(),
            pending_gsm_name="demo",
            agent_running=False,
        )
        live_output = _DummyContainer()

        with patch("ui.chat_controller.render_user_bubble") as render_user, patch("ui.chat_controller.render_assistant_block") as render_assistant, patch("streamlit.chat_message", side_effect=AssertionError("should not call st.chat_message")):
            ok, should_rerun, _ = run_normal_text_path(
                effective_input="你好",
                redo_input=None,
                bridge_input=None,
                session_state=session_state,
                api_key="k",
                model_name="glm-4-flash",
                route_main_input_fn=lambda _u, **_k: ("CHAT", "demo"),
                live_output=live_output,
                chat_respond_fn=lambda *_a, **_k: "助手回复",
                should_skip_elicitation_fn=lambda *_a, **_k: True,
                create_project_fn=lambda _n: object(),
                has_any_script_content_fn=lambda _p: True,
                run_agent_generate_fn=lambda *_a, **_k: "GEN",
                handle_elicitation_route_fn=lambda *_a, **_k: ("", False),
                markdown_fn=lambda *_a, **_k: None,
                info_fn=lambda *_a, **_k: None,
                build_assistant_chat_message_fn=lambda **kwargs: {"role": "assistant", "content": kwargs["content"]},
            )

        self.assertTrue(ok)
        self.assertTrue(should_rerun)
        self.assertEqual([m["role"] for m in session_state["chat_history"]], ["user", "assistant"])
        self.assertTrue(render_user.called)
        self.assertTrue(render_assistant.called)

    def test_run_normal_text_path_stages_gdl_changes_even_for_first_create(self):
        session_state = _State(
            chat_history=[],
            project=None,
            pending_gsm_name="",
            agent_running=False,
            script_revision=0,
        )
        live_output = _DummyContainer()
        captured = {}

        with patch("ui.chat_controller.render_user_bubble"), patch("ui.chat_controller.render_assistant_block"):
            ok, should_rerun, _ = run_normal_text_path(
                effective_input="生成一个椅子",
                redo_input=None,
                bridge_input=None,
                session_state=session_state,
                api_key="k",
                model_name="glm-4-flash",
                route_main_input_fn=lambda _u, **_k: ("GDL", "chair"),
                live_output=live_output,
                chat_respond_fn=lambda *_a, **_k: "助手回复",
                should_skip_elicitation_fn=lambda *_a, **_k: True,
                create_project_fn=lambda name: {"name": name},
                has_any_script_content_fn=lambda _p: False,
                run_agent_generate_fn=lambda _text, _proj, _status, _gsm, auto_apply: captured.setdefault("auto_apply", auto_apply) or "GEN",
                handle_elicitation_route_fn=lambda *_a, **_k: ("", False),
                markdown_fn=lambda *_a, **_k: None,
                info_fn=lambda *_a, **_k: None,
                build_assistant_chat_message_fn=lambda **kwargs: {"role": "assistant", "content": kwargs["content"]},
            )

        self.assertTrue(ok)
        self.assertTrue(should_rerun)
        self.assertFalse(captured["auto_apply"])

    def test_run_vision_path_does_not_use_streamlit_chat_message(self):
        session_state = _State(
            chat_history=[],
            project=object(),
            pending_gsm_name="demo",
            agent_running=False,
            chat_image_route_mode="自动",
        )
        live_output = _DummyContainer()

        with patch("ui.chat_controller.render_user_bubble") as render_user, patch("ui.chat_controller.render_assistant_block") as render_assistant, patch("streamlit.chat_message", side_effect=AssertionError("should not call st.chat_message")):
            ok, should_rerun, _ = run_vision_path(
                has_image_input=True,
                vision_mime="image/png",
                vision_name="a.png",
                user_input="请生成",
                active_debug_mode=None,
                vision_b64="ZmFrZQ==",
                session_state=session_state,
                api_key="k",
                model_name="glm-4-flash",
                resolve_image_route_mode_fn=lambda *_a, **_k: "generate",
                build_image_user_display_fn=lambda *_a, **_k: "用户图像输入",
                live_output=live_output,
                create_project_fn=lambda _n: object(),
                has_any_script_content_fn=lambda _p: True,
                thumb_image_bytes_fn=lambda _b64: b"img",
                image_fn=lambda *_a, **_k: None,
                markdown_fn=lambda *_a, **_k: None,
                run_vision_generate_fn=lambda *_a, **_k: "视觉回复",
                run_agent_generate_with_debug_image_fn=lambda *_a, **_k: "DEBUG",
            )

        self.assertTrue(ok)
        self.assertTrue(should_rerun)
        self.assertEqual([m["role"] for m in session_state["chat_history"]], ["user", "assistant"])
        self.assertTrue(render_user.called)
        self.assertTrue(render_assistant.called)

    def test_run_vision_path_stages_generated_changes_even_for_first_image_create(self):
        session_state = _State(
            chat_history=[],
            project=None,
            pending_gsm_name="",
            agent_running=False,
            script_revision=0,
            chat_image_route_mode="自动",
        )
        live_output = _DummyContainer()
        captured = {}

        with patch("ui.chat_controller.render_user_bubble"), patch("ui.chat_controller.render_assistant_block"):
            ok, should_rerun, _ = run_vision_path(
                has_image_input=True,
                vision_mime="image/png",
                vision_name="a.png",
                user_input="请生成",
                active_debug_mode=None,
                vision_b64="ZmFrZQ==",
                session_state=session_state,
                api_key="k",
                model_name="glm-4-flash",
                resolve_image_route_mode_fn=lambda *_a, **_k: "generate",
                build_image_user_display_fn=lambda *_a, **_k: "用户图像输入",
                live_output=live_output,
                create_project_fn=lambda name: {"name": name},
                has_any_script_content_fn=lambda _p: False,
                thumb_image_bytes_fn=lambda _b64: b"img",
                image_fn=lambda *_a, **_k: None,
                markdown_fn=lambda *_a, **_k: None,
                run_vision_generate_fn=lambda _b64, _mime, _text, _proj, _status, auto_apply: captured.setdefault("auto_apply", auto_apply) or "视觉回复",
                run_agent_generate_with_debug_image_fn=lambda *_a, **_k: "DEBUG",
            )

        self.assertTrue(ok)
        self.assertTrue(should_rerun)
        self.assertFalse(captured["auto_apply"])


if __name__ == "__main__":
    unittest.main()
