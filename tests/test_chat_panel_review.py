import unittest

from ui.views.chat_panel import (
    _apply_compile_and_maybe_snapshot,
    _pending_static_issues,
)


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _DummyStreamlit:
    def __init__(self):
        self.session_state = _State(
            project=object(),
            pending_diffs={"scripts/3d.gdl": "BLOCK 1, 1, 1"},
            pending_ai_label="脚本 [3D]",
            pending_gsm_name="Demo",
            compile_result=("old", "stale"),
            revision_notice="",
            pending_preview_2d_data=object(),
            pending_preview_3d_data=object(),
            pending_current_preview_2d_data=object(),
            pending_current_preview_3d_data=object(),
            pending_preview_warnings=["old preview warning"],
            pending_preview_meta={"kind": "3D", "timestamp": "old", "source": "pending"},
            pending_preview_diff_summary={"delta": {"mesh": 1}},
            pending_compile_result=(True, "old preflight"),
            pending_compile_meta={"compiler_mode": "Mock"},
        )
        self.errors = []
        self.toasts = []

    def error(self, msg):
        self.errors.append(msg)

    def toast(self, msg, icon=None):
        self.toasts.append((msg, icon))


class TestChatPanelReviewWorkflow(unittest.TestCase):
    def test_pending_static_issues_ignores_success_messages(self):
        issues = _pending_static_issues(
            {"scripts/3d.gdl": "BLOCK 1, 1, 1"},
            script_map=[(object(), "scripts/3d.gdl", "3D")],
            check_gdl_script_fn=lambda _content, _key: ["✅ OK", "⚠️ missing END"],
        )

        self.assertEqual(issues, ["3D: ⚠️ missing END"])

    def test_apply_compile_and_snapshot_promotes_pending_review(self):
        st = _DummyStreamlit()
        calls = []

        def capture(label):
            calls.append(("capture", label))

        def apply(project, diffs):
            calls.append(("apply", project, dict(diffs)))
            return 1, 0

        def bump():
            calls.append(("bump",))
            return 2

        def compile_project(project, gsm_name, instruction):
            calls.append(("compile", project, gsm_name, instruction))
            return True, "compiled"

        def save_revision(project, message, gsm_name):
            calls.append(("save", project, message, gsm_name))
            return True, "✅ saved"

        _apply_compile_and_maybe_snapshot(
            st,
            pending_diffs=st.session_state.pending_diffs,
            script_count=1,
            param_count=0,
            capture_last_project_snapshot_fn=capture,
            apply_scripts_to_project_fn=apply,
            bump_main_editor_version_fn=bump,
            do_compile_fn=compile_project,
            save_revision_fn=save_revision,
        )

        self.assertEqual(st.session_state.pending_diffs, {})
        self.assertEqual(st.session_state.pending_ai_label, "")
        self.assertEqual(st.session_state.compile_result, (True, "compiled"))
        self.assertEqual(st.session_state.revision_notice, "✅ saved")
        self.assertIsNone(st.session_state.pending_preview_2d_data)
        self.assertIsNone(st.session_state.pending_preview_3d_data)
        self.assertIsNone(st.session_state.pending_current_preview_2d_data)
        self.assertIsNone(st.session_state.pending_current_preview_3d_data)
        self.assertEqual(st.session_state.pending_preview_warnings, [])
        self.assertEqual(st.session_state.pending_preview_meta, {"kind": "", "timestamp": "", "source": ""})
        self.assertEqual(st.session_state.pending_preview_diff_summary, {})
        self.assertIsNone(st.session_state.pending_compile_result)
        self.assertEqual(st.session_state.pending_compile_meta, {})
        self.assertEqual(
            [call[0] for call in calls],
            ["capture", "apply", "bump", "compile", "save"],
        )
        self.assertEqual(calls[3][2:], ("Demo", "(review promote)"))
        self.assertEqual(calls[4][2:], ("AI review promote Demo", "Demo"))


if __name__ == "__main__":
    unittest.main()
