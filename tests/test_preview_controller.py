import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openbrep.hsf_project import HSFProject, ScriptType
from ui.preview_controller import run_preview, sync_visible_editor_buffers


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class TestPreviewControllerPhase1(unittest.TestCase):
    def _make_project(self):
        proj = HSFProject.create_new("PreviewCase")
        proj.set_script(ScriptType.SCRIPT_3D, "BLOCK 1,1,1")
        proj.set_script(ScriptType.SCRIPT_2D, "LINE2 0,0,1,1")
        return proj

    def test_run_preview_passes_preview_options_to_3d(self):
        proj = self._make_project()
        calls = {}

        def _capture(*args, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(warnings=[], meshes=[], wires=[])

        with patch("ui.preview_controller.preview_3d_script", side_effect=_capture):
            ok, _ = run_preview(
                proj,
                "3d",
                sync_visible_editor_buffers_fn=lambda p, v: True,
                editor_version=1,
                preview_param_values_fn=lambda p: {"A": 1.0},
                collect_preview_prechecks_fn=lambda p, t: [],
                dedupe_keep_order_fn=lambda xs: xs,
                set_preview_2d_data_fn=lambda data: None,
                set_preview_3d_data_fn=lambda data: None,
                set_preview_warnings_fn=lambda warns: None,
                set_preview_meta_fn=lambda meta: None,
                script_type_2d=ScriptType.SCRIPT_2D,
                script_type_3d=ScriptType.SCRIPT_3D,
                strict=True,
                unknown_command_policy="error",
                quality="accurate",
            )

        self.assertTrue(ok)
        self.assertTrue(calls.get("strict"))
        self.assertEqual(calls.get("unknown_command_policy"), "error")
        self.assertEqual(calls.get("quality"), "accurate")

    def test_sync_visible_editor_buffers_updates_project_and_clears_preview(self):
        proj = self._make_project()
        state = _State(
            _ace_pending_main_editor_keys=set(),
            preview_2d_data={"old": True},
            preview_3d_data={"old": True},
            preview_warnings=["old"],
            preview_meta={"kind": "old"},
            editor_scripts_1_3d="CYLIND 1, 1",
        )

        changed = sync_visible_editor_buffers(
            proj,
            1,
            session_state=state,
            script_map=[(ScriptType.SCRIPT_3D, "scripts/3d.gdl", "3D")],
            main_editor_state_key_fn=lambda _fpath, _version: "editor_scripts_1_3d",
            ace_available=True,
        )

        self.assertTrue(changed)
        self.assertEqual(proj.get_script(ScriptType.SCRIPT_3D), "CYLIND 1, 1")
        self.assertIsNone(state.preview_2d_data)
        self.assertIsNone(state.preview_3d_data)
        self.assertEqual(state.preview_warnings, [])
        self.assertEqual(state.preview_meta, {"kind": "", "timestamp": ""})

    def test_sync_visible_editor_buffers_keeps_pending_empty_ace_value(self):
        proj = self._make_project()
        state = _State(
            _ace_pending_main_editor_keys={"editor_scripts_1_3d"},
            preview_2d_data=None,
            preview_3d_data=None,
            preview_warnings=[],
            preview_meta={},
            editor_scripts_1_3d="",
        )

        changed = sync_visible_editor_buffers(
            proj,
            1,
            session_state=state,
            script_map=[(ScriptType.SCRIPT_3D, "scripts/3d.gdl", "3D")],
            main_editor_state_key_fn=lambda _fpath, _version: "editor_scripts_1_3d",
            ace_available=True,
        )

        self.assertFalse(changed)
        self.assertEqual(proj.get_script(ScriptType.SCRIPT_3D), "BLOCK 1,1,1")
        self.assertEqual(state._ace_pending_main_editor_keys, {"editor_scripts_1_3d"})


if __name__ == "__main__":
    unittest.main()
