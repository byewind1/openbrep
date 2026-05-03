import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ui.views.project_tools_panel import _render_compile_section, _render_hsf_save_section


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
    def __init__(self, *, clicked_label: str, state: dict):
        self.clicked_label = clicked_label
        self.session_state = _State(state)
        self.toasts = []
        self.errors = []
        self.dialog_titles = []
        self.rerun_called = False

    def subheader(self, *_args, **_kwargs):
        return None

    def caption(self, *_args, **_kwargs):
        return None

    def columns(self, spec):
        count = len(spec) if hasattr(spec, "__len__") else int(spec)
        return tuple(_Col() for _ in range(count))

    def button(self, label, **_kwargs):
        return label == self.clicked_label

    def dialog(self, title):
        self.dialog_titles.append(title)

        def _decorator(fn):
            return fn

        return _decorator

    def text_input(self, _label, *, key, **_kwargs):
        return self.session_state.get(key, "")

    def toast(self, msg, **_kwargs):
        self.toasts.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def rerun(self):
        self.rerun_called = True

    def spinner(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestProjectToolsPanel(unittest.TestCase):
    def test_save_hsf_saves_current_hsf_dir_without_opening_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            hsf_root = Path(tmp) / "Chair"
            state = {
                "active_hsf_source_dir": str(hsf_root),
                "hsf_save_dialog_open": False,
                "hsf_save_dialog_mode": "",
            }
            st = _FakeStreamlit(clicked_label="保存 HSF", state=state)
            calls = []

            def save_hsf(_proj, parent_dir, hsf_name):
                calls.append((Path(parent_dir), hsf_name))
                return True, "已保存"

            _render_hsf_save_section(
                st,
                proj=SimpleNamespace(name="Chair"),
                is_generation_locked_fn=lambda: False,
                choose_hsf_save_parent_dir_fn=lambda: None,
                save_hsf_project_fn=save_hsf,
            )

            self.assertEqual(calls, [(hsf_root.parent, "Chair")])
            self.assertFalse(st.session_state.hsf_save_dialog_open)
            self.assertEqual(st.session_state.hsf_save_dialog_mode, "")
            self.assertEqual(st.dialog_titles, [])
            self.assertEqual(st.toasts, ["已保存"])

    def test_save_hsf_opens_save_as_flow_when_project_has_no_hsf_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "active_hsf_source_dir": "",
                "work_dir": tmp,
                "pending_gsm_name": "DraftChair",
                "hsf_save_dialog_open": False,
                "hsf_save_dialog_mode": "",
            }
            st = _FakeStreamlit(clicked_label="保存 HSF", state=state)
            calls = []

            _render_hsf_save_section(
                st,
                proj=SimpleNamespace(name="Chair"),
                is_generation_locked_fn=lambda: False,
                choose_hsf_save_parent_dir_fn=lambda: None,
                save_hsf_project_fn=lambda *_args: calls.append(_args) or (True, "saved"),
            )

            self.assertEqual(calls, [])
            self.assertTrue(st.session_state.hsf_save_dialog_open)
            self.assertEqual(st.session_state.hsf_save_dialog_mode, "save_as")
            self.assertEqual(st.session_state.hsf_save_parent_dir, tmp)
            self.assertEqual(st.session_state.hsf_save_name, "DraftChair")
            self.assertEqual(st.dialog_titles, ["📂 另存为 HSF"])

    def test_compile_success_does_not_save_hidden_revision_snapshot(self):
        state = {
            "pending_gsm_name": "Chair",
            "agent_running": False,
            "compile_result": None,
            "revision_auto_snapshot": True,
            "revision_notice": "",
        }
        st = _FakeStreamlit(clicked_label="🔧 编译 GSM", state=state)
        calls = []

        _render_compile_section(
            st,
            SimpleNamespace(name="Chair"),
            choose_compile_output_dir_fn=lambda: None,
            do_compile_fn=lambda project, gsm_name, instruction, output_dir: calls.append(
                (project.name, gsm_name, instruction, output_dir)
            )
            or (True, "compiled"),
        )

        self.assertEqual(calls, [("Chair", "Chair", "(toolbar compile)", None)])
        self.assertEqual(st.session_state.compile_result, (True, "compiled"))
        self.assertEqual(st.session_state.revision_notice, "")
        self.assertEqual(st.toasts, ["✅ 编译成功"])
        self.assertTrue(st.rerun_called)


if __name__ == "__main__":
    unittest.main()
