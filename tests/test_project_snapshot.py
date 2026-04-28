import unittest

from ui.project_snapshot import capture_last_project_snapshot, restore_last_project_snapshot


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class TestProjectSnapshot(unittest.TestCase):
    def test_capture_ignores_missing_project(self):
        state = _State(last_project_snapshot=None)

        capture_last_project_snapshot(state, "AI 写入")

        self.assertIsNone(state.last_project_snapshot)

    def test_capture_and_restore_project_snapshot(self):
        state = _State(
            project={"scripts": {"3D": "BLOCK 1, 1, 1"}},
            pending_gsm_name="Chair",
            script_revision=3,
            pending_diffs={"scripts/3d.gdl": "new"},
            pending_ai_label="3D",
            preview_2d_data=object(),
            preview_3d_data=object(),
            preview_warnings=["old"],
            preview_meta={"kind": "old"},
            last_project_snapshot=None,
            last_project_snapshot_meta={},
            last_project_snapshot_label="",
        )
        bumps = []

        capture_last_project_snapshot(state, "AI 确认写入")
        state.project["scripts"]["3D"] = "CYLIND 1, 1"
        state.pending_gsm_name = "Changed"
        state.script_revision = 9

        ok, msg = restore_last_project_snapshot(
            state,
            bump_main_editor_version_fn=lambda: bumps.append("bump") or len(bumps),
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "✅ 已撤销上次 AI 确认写入")
        self.assertEqual(state.project["scripts"]["3D"], "BLOCK 1, 1, 1")
        self.assertEqual(state.pending_gsm_name, "Chair")
        self.assertEqual(state.script_revision, 3)
        self.assertEqual(state.pending_diffs, {})
        self.assertEqual(state.pending_ai_label, "")
        self.assertIsNone(state.preview_2d_data)
        self.assertIsNone(state.preview_3d_data)
        self.assertEqual(state.preview_warnings, [])
        self.assertEqual(state.preview_meta, {"kind": "", "timestamp": ""})
        self.assertIsNone(state.last_project_snapshot)
        self.assertEqual(bumps, ["bump"])

    def test_restore_returns_error_when_missing_snapshot(self):
        ok, msg = restore_last_project_snapshot(
            _State(last_project_snapshot=None),
            bump_main_editor_version_fn=lambda: 1,
        )

        self.assertFalse(ok)
        self.assertIn("没有可恢复", msg)


if __name__ == "__main__":
    unittest.main()
