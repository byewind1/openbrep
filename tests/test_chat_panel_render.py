import unittest
from unittest.mock import patch

from ui.views.chat_panel import _render_chat_history


class _Col:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummySt:
    def __init__(self, chat_history):
        class _State(dict):
            def __getattr__(self, key):
                try:
                    return self[key]
                except KeyError as exc:
                    raise AttributeError(key) from exc

            def __setattr__(self, key, value):
                self[key] = value

        self.session_state = _State(chat_history=chat_history)
        self.container_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def columns(self, _spec):
        count = len(_spec) if hasattr(_spec, "__len__") else int(_spec)
        return tuple(_Col() for _ in range(count))

    def markdown(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def button(self, *args, **kwargs):
        return False

    def container(self, **kwargs):
        self.container_calls.append(kwargs)
        return self

    def text_area(self, *args, **kwargs):
        return ""


class TestChatPanelRender(unittest.TestCase):
    def test_history_render_uses_shared_renderer(self):
        st = _DummySt(
            [
                {"role": "user", "content": "hello", "image_b64": "fake"},
                {"role": "assistant", "content": "reply"},
            ]
        )

        with patch("ui.views.chat_panel.render_user_bubble") as render_user, patch("ui.views.chat_panel.render_assistant_block") as render_assistant:
            _render_chat_history(
                st,
                thumb_image_bytes_fn=lambda _b64: b"img",
                save_feedback_fn=lambda *_a, **_k: None,
                copyable_chat_text_fn=lambda _msg: "",
                copy_text_to_system_clipboard_fn=lambda _text: (True, "ok"),
                is_bridgeable_explainer_message_fn=lambda _msg: False,
            )

        self.assertTrue(render_user.called)
        self.assertTrue(render_assistant.called)

    def test_import_status_message_is_omitted_from_chat_history(self):
        st = _DummySt(
            [
                {
                    "role": "assistant",
                    "content": "✅ 已导入 `Chair` — 12 参数，6 脚本\n\n**HSF 文件列表**: `['a', 'b']`",
                },
            ]
        )

        with patch("ui.views.chat_panel.render_assistant_block") as render_assistant:
            _render_chat_history(
                st,
                thumb_image_bytes_fn=lambda _b64: None,
                save_feedback_fn=lambda *_a, **_k: None,
                copyable_chat_text_fn=lambda _msg: "",
                copy_text_to_system_clipboard_fn=lambda _text: (True, "ok"),
                is_bridgeable_explainer_message_fn=lambda _msg: False,
            )

        self.assertFalse(render_assistant.called)


if __name__ == "__main__":
    unittest.main()
