import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openbrep.hsf_project import HSFProject, ScriptType
from ui.preview_controller import run_preview


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


if __name__ == "__main__":
    unittest.main()
