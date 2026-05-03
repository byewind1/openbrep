import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openbrep.hsf_project import HSFProject
from ui.revision_controller import restore_project_revision


class _State(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class TestRevisionController(unittest.TestCase):
    def test_restore_project_revision_closes_chat_record_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = HSFProject.create_new("Chair", work_dir=tmp)
            state = _State(
                project=proj,
                chat_record_browser_open=True,
                chat_record_open_idx=4,
                chat_record_delete_idx=4,
                pending_diffs={"old": True},
                pending_ai_label="old",
                compile_result=(True, "old"),
                preview_2d_data=object(),
                preview_3d_data=object(),
                preview_warnings=["old"],
                preview_meta={"kind": "old", "timestamp": "old"},
            )

            with patch("ui.revision_controller.restore_revision", return_value=SimpleNamespace(revision_id="r0002")):
                ok, _msg = restore_project_revision(
                    proj,
                    "r0001",
                    session_state=state,
                    load_project_from_disk_fn=lambda _path: proj,
                    reset_tapir_p0_state_fn=lambda: None,
                    bump_main_editor_version_fn=lambda: None,
                )

            self.assertTrue(ok)
            self.assertFalse(state.chat_record_browser_open)
            self.assertIsNone(state.chat_record_open_idx)
            self.assertIsNone(state.chat_record_delete_idx)


if __name__ == "__main__":
    unittest.main()
