import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ui.views.workspace_tools_panel import render_preview_workbench, render_workspace_tools_panel


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

    def metric(self, *_args, **_kwargs):
        return None


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
        self.captions = []
        self.markdowns = []

    def markdown(self, *_args, **_kwargs):
        if _args:
            self.markdowns.append(str(_args[0]))
        return None

    def divider(self):
        return None

    def caption(self, message):
        self.captions.append(str(message))

    def success(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
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
    def test_workspace_panel_does_not_render_archicad_live_link_controls(self):
        st = _FakeStreamlit("")
        st.session_state.work_dir = "/tmp/openbrep-workspace"
        st.session_state.confirm_clear_memory = False
        memory_status = SimpleNamespace(
            memory_root="/tmp/openbrep-workspace/.openbrep/memory",
            chat_count=0,
            lesson_count=0,
            has_learned_skill=False,
            total_bytes=0,
        )

        with patch("ui.views.workspace_tools_panel.ErrorLearningStore") as store_class:
            store_class.return_value.memory_status.return_value = memory_status
            with patch("ui.views.workspace_tools_panel._render_tapir_controls") as tapir_controls:
                render_workspace_tools_panel(
                    st,
                    SimpleNamespace(name="Chair"),
                    tapir_import_ok=True,
                    get_bridge_fn=lambda: (_ for _ in ()).throw(AssertionError("should not open Tapir bridge")),
                )

        tapir_controls.assert_not_called()
        self.assertNotIn("Archicad 实机联动", "\n".join(st.markdowns))

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
