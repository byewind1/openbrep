import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ui.views.workspace_tools_panel import render_preview_workbench


class _State(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class _Col:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self, clicked_label: str):
        self.clicked_label = clicked_label
        self.session_state = _State(
            chat_record_browser_open=True,
            chat_record_open_idx=1,
            chat_record_delete_idx=1,
        )
        self.toasts = []
        self.errors = []

    def markdown(self, *_args, **_kwargs):
        return None

    def columns(self, spec):
        count = len(spec) if hasattr(spec, "__len__") else int(spec)
        return tuple(_Col() for _ in range(count))

    def button(self, label, **_kwargs):
        return label == self.clicked_label

    def toast(self, msg, **_kwargs):
        self.toasts.append(msg)

    def error(self, msg):
        self.errors.append(msg)


class TestWorkspaceToolsPanel(unittest.TestCase):
    def test_preview_click_closes_chat_record_browser_before_running_preview(self):
        st = _FakeStreamlit("🧊 预览 3D")
        calls = []

        def run_preview(_proj, target):
            calls.append(
                (
                    target,
                    st.session_state.chat_record_browser_open,
                    st.session_state.chat_record_open_idx,
                    st.session_state.chat_record_delete_idx,
                )
            )
            return True, "ok"

        with patch("ui.views.workspace_tools_panel._render_preview_panel"):
            render_preview_workbench(
                st,
                SimpleNamespace(name="Chair"),
                run_preview_fn=run_preview,
                render_preview_2d_fn=lambda _data: None,
                render_preview_3d_fn=lambda _data: None,
            )

        self.assertEqual(calls, [("3d", False, None, None)])
        self.assertFalse(st.session_state.chat_record_browser_open)
        self.assertIsNone(st.session_state.chat_record_open_idx)
        self.assertIsNone(st.session_state.chat_record_delete_idx)


if __name__ == "__main__":
    unittest.main()
