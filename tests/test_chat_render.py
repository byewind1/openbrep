import unittest

from ui.chat_render import render_assistant_block, render_user_bubble


class _DummySt:
    def __init__(self):
        self.markdown_calls = []
        self.image_calls = []

    def markdown(self, text, **kwargs):
        self.markdown_calls.append((text, kwargs))

    def image(self, *args, **kwargs):
        self.image_calls.append((args, kwargs))


class TestChatRender(unittest.TestCase):
    def test_render_user_bubble_escapes_text_and_renders_image(self):
        st = _DummySt()

        render_user_bubble(st, "a < b\nnext", image_bytes=b"img", image_width=180)

        self.assertEqual(len(st.markdown_calls), 1)
        rendered_html, kwargs = st.markdown_calls[0]
        self.assertIn("a &lt; b<br>next", rendered_html)
        self.assertTrue(kwargs.get("unsafe_allow_html"))
        self.assertEqual(len(st.image_calls), 1)
        self.assertEqual(st.image_calls[0][1]["width"], 180)

    def test_render_assistant_block_renders_text_and_optional_image(self):
        st = _DummySt()

        render_assistant_block(st, "hello", image_bytes=b"img", image_width=200)

        self.assertEqual(st.markdown_calls, [("hello", {})])
        self.assertEqual(len(st.image_calls), 1)
        self.assertEqual(st.image_calls[0][1]["width"], 200)


if __name__ == "__main__":
    unittest.main()
