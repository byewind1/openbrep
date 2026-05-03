import unittest

from ui.session_defaults import ensure_session_defaults


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class TestSessionDefaults(unittest.TestCase):
    def test_ensure_session_defaults_fills_missing_fields(self):
        state = _State()

        ensure_session_defaults(state, work_dir_default="/tmp/openbrep-workspace")

        self.assertEqual(state.project, None)
        self.assertEqual(state.work_dir, "/tmp/openbrep-workspace")
        self.assertEqual(state.chat_history, [])
        self.assertEqual(state.project_activity_log, [])
        self.assertFalse(state.confirm_clear_memory)
        self.assertEqual(state.editor_open_path, "")
        self.assertEqual(state.editor_hsf_dir, "")
        self.assertEqual(state.active_hsf_source_dir, "")
        self.assertFalse(state.hsf_save_dialog_open)
        self.assertEqual(state.hsf_save_dialog_mode, "")
        self.assertEqual(state.hsf_save_parent_dir, "")
        self.assertEqual(state.hsf_save_name, "")
        self.assertIsNone(state.pending_preview_2d_data)
        self.assertIsNone(state.pending_preview_3d_data)
        self.assertIsNone(state.pending_current_preview_2d_data)
        self.assertIsNone(state.pending_current_preview_3d_data)
        self.assertEqual(state.pending_preview_warnings, [])
        self.assertEqual(state.pending_preview_meta, {"kind": "", "timestamp": "", "source": ""})
        self.assertEqual(state.pending_preview_diff_summary, {})
        self.assertIsNone(state.pending_compile_result)
        self.assertEqual(state.pending_compile_meta, {})
        self.assertEqual(state.elicitation_state, "idle")

    def test_ensure_session_defaults_keeps_existing_values(self):
        state = _State(work_dir="/custom", chat_history=[{"role": "user", "content": "hi"}])

        ensure_session_defaults(state, work_dir_default="/tmp/openbrep-workspace")

        self.assertEqual(state.work_dir, "/custom")
        self.assertEqual(state.chat_history, [{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
