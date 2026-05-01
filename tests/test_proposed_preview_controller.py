import unittest

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from ui.proposed_preview_controller import (
    build_project_with_pending_diffs,
    run_pending_preview,
)


class TestProposedPreviewController(unittest.TestCase):
    def setUp(self):
        self.script_map = [
            (ScriptType.SCRIPT_3D, "scripts/3d.gdl", "3D"),
            (ScriptType.SCRIPT_2D, "scripts/2d.gdl", "2D"),
        ]

    def test_build_project_with_pending_diffs_does_not_mutate_original(self):
        proj = HSFProject.create_new("Demo")
        original_3d = proj.get_script(ScriptType.SCRIPT_3D)

        proposed = build_project_with_pending_diffs(
            proj,
            {"scripts/3d.gdl": "BLOCK 2, 3, 4"},
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
        )

        self.assertEqual(proj.get_script(ScriptType.SCRIPT_3D), original_3d)
        self.assertEqual(proposed.get_script(ScriptType.SCRIPT_3D), "BLOCK 2, 3, 4")
        self.assertIsNot(proposed, proj)

    def test_build_project_with_pending_paramlist_uses_temporary_project(self):
        proj = HSFProject.create_new("Demo")
        proposed = build_project_with_pending_diffs(
            proj,
            {"paramlist.xml": "Length W = 2.0 ! Width"},
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [GDLParameter("W", "Length", "Width", "2.0")],
        )

        self.assertEqual([p.name for p in proj.parameters], ["A", "B", "ZZYZX"])
        self.assertEqual([p.name for p in proposed.parameters], ["W"])

    def test_run_pending_preview_3d_sets_pending_preview_data(self):
        proj = HSFProject.create_new("Demo")
        state = {}

        ok, msg = run_pending_preview(
            proj,
            {"scripts/3d.gdl": "BLOCK 2, 3, 4"},
            "3d",
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
            preview_param_values_fn=lambda _proj: {"A": 1, "B": 1, "ZZYZX": 1},
            collect_preview_prechecks_fn=lambda _proj, _target: [],
            dedupe_keep_order_fn=lambda items: items,
            set_pending_preview_2d_data_fn=lambda data: state.__setitem__("p2d", data),
            set_pending_preview_3d_data_fn=lambda data: state.__setitem__("p3d", data),
            set_pending_preview_warnings_fn=lambda warns: state.__setitem__("warnings", warns),
            set_pending_preview_meta_fn=lambda meta: state.__setitem__("meta", meta),
            set_pending_current_preview_3d_data_fn=lambda data: state.__setitem__("current_3d", data),
            set_pending_preview_diff_summary_fn=lambda summary: state.__setitem__("summary", summary),
            script_type_2d=ScriptType.SCRIPT_2D,
            script_type_3d=ScriptType.SCRIPT_3D,
        )

        self.assertTrue(ok)
        self.assertIn("3D", msg)
        self.assertEqual(len(state["p3d"].meshes), 1)
        self.assertEqual(state["warnings"], [])
        self.assertEqual(state["meta"]["kind"], "3D")
        self.assertEqual(state["meta"]["source"], "pending")
        self.assertEqual(len(state["current_3d"].meshes), 1)
        self.assertEqual(state["summary"]["current"]["mesh"], 1)
        self.assertEqual(state["summary"]["proposed"]["mesh"], 1)
        self.assertEqual(state["summary"]["delta"]["mesh"], 0)
        self.assertEqual(state["summary"]["changed_paths"], ["scripts/3d.gdl"])
        self.assertEqual(state["summary"]["current_status"], "ok")
        self.assertEqual(state["summary"]["proposed_status"], "ok")
        self.assertEqual(state["summary"]["proposed_facts"]["total_primitives"], 13)
        self.assertFalse(state["summary"]["proposed_facts"]["is_empty"])

    def test_run_pending_preview_summary_reports_mesh_delta(self):
        proj = HSFProject.create_new("Demo")
        state = {}

        ok, _msg = run_pending_preview(
            proj,
            {"scripts/3d.gdl": "BLOCK 2, 3, 4\nSPHERE 0.5"},
            "3d",
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
            preview_param_values_fn=lambda _proj: {"A": 1, "B": 1, "ZZYZX": 1},
            collect_preview_prechecks_fn=lambda _proj, _target: [],
            dedupe_keep_order_fn=lambda items: items,
            set_pending_preview_2d_data_fn=lambda data: state.__setitem__("p2d", data),
            set_pending_preview_3d_data_fn=lambda data: state.__setitem__("p3d", data),
            set_pending_preview_warnings_fn=lambda warns: state.__setitem__("warnings", warns),
            set_pending_preview_meta_fn=lambda meta: state.__setitem__("meta", meta),
            set_pending_current_preview_3d_data_fn=lambda data: state.__setitem__("current_3d", data),
            set_pending_preview_diff_summary_fn=lambda summary: state.__setitem__("summary", summary),
            script_type_2d=ScriptType.SCRIPT_2D,
            script_type_3d=ScriptType.SCRIPT_3D,
        )

        self.assertTrue(ok)
        self.assertEqual(state["summary"]["current"]["mesh"], 1)
        self.assertEqual(state["summary"]["proposed"]["mesh"], 2)
        self.assertEqual(state["summary"]["delta"]["mesh"], 1)
        self.assertEqual(state["summary"]["target"], "3D")
        self.assertEqual(state["summary"]["proposed_status"], "ok")

    def test_run_pending_preview_uses_master_setup_for_proposed_project(self):
        proj = HSFProject.create_new("Demo")
        proj.set_script(ScriptType.MASTER, "_inner_w = A - 2 * frame_thk\n")
        state = {}

        ok, _msg = run_pending_preview(
            proj,
            {"scripts/3d.gdl": "BLOCK _inner_w, B, ZZYZX"},
            "3d",
            script_map=[
                *self.script_map,
                (ScriptType.MASTER, "scripts/1d.gdl", "Master"),
            ],
            parse_paramlist_text_fn=lambda _text: [],
            preview_param_values_fn=lambda _proj: {"A": 2, "B": 1, "ZZYZX": 1, "FRAME_THK": 0.1},
            collect_preview_prechecks_fn=lambda _proj, _target: [],
            dedupe_keep_order_fn=lambda items: items,
            set_pending_preview_2d_data_fn=lambda data: state.__setitem__("p2d", data),
            set_pending_preview_3d_data_fn=lambda data: state.__setitem__("p3d", data),
            set_pending_preview_warnings_fn=lambda warns: state.__setitem__("warnings", warns),
            set_pending_preview_meta_fn=lambda meta: state.__setitem__("meta", meta),
            set_pending_current_preview_3d_data_fn=lambda data: state.__setitem__("current_3d", data),
            set_pending_preview_diff_summary_fn=lambda summary: state.__setitem__("summary", summary),
            script_type_2d=ScriptType.SCRIPT_2D,
            script_type_3d=ScriptType.SCRIPT_3D,
        )

        self.assertTrue(ok)
        self.assertEqual(len(state["p3d"].meshes), 1)
        self.assertEqual(state["warnings"], [])

    def test_run_pending_preview_summary_marks_empty_and_warn_states(self):
        proj = HSFProject.create_new("Demo")
        state = {}

        ok, _msg = run_pending_preview(
            proj,
            {"scripts/3d.gdl": "HOTSPOT 0,0,0"},
            "3d",
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
            preview_param_values_fn=lambda _proj: {"A": 1, "B": 1, "ZZYZX": 1},
            collect_preview_prechecks_fn=lambda _proj, _target: ["preview warning"],
            dedupe_keep_order_fn=lambda items: items,
            set_pending_preview_2d_data_fn=lambda data: state.__setitem__("p2d", data),
            set_pending_preview_3d_data_fn=lambda data: state.__setitem__("p3d", data),
            set_pending_preview_warnings_fn=lambda warns: state.__setitem__("warnings", warns),
            set_pending_preview_meta_fn=lambda meta: state.__setitem__("meta", meta),
            set_pending_current_preview_3d_data_fn=lambda data: state.__setitem__("current_3d", data),
            set_pending_preview_diff_summary_fn=lambda summary: state.__setitem__("summary", summary),
            script_type_2d=ScriptType.SCRIPT_2D,
            script_type_3d=ScriptType.SCRIPT_3D,
        )

        self.assertTrue(ok)
        self.assertEqual(state["summary"]["current_status"], "warn")
        self.assertEqual(state["summary"]["proposed_status"], "empty")
        self.assertTrue(state["summary"]["proposed_facts"]["is_empty"])
        self.assertEqual(state["summary"]["proposed_facts"]["warning_count"], 2)

    def test_run_pending_preview_failure_is_isolated_to_pending_state(self):
        proj = HSFProject.create_new("Demo")
        state = {}

        ok, msg = run_pending_preview(
            proj,
            {"scripts/3d.gdl": "UNKNOWN_CMD 1"},
            "3d",
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
            preview_param_values_fn=lambda _proj: {},
            collect_preview_prechecks_fn=lambda _proj, _target: ["precheck warning"],
            dedupe_keep_order_fn=lambda items: list(dict.fromkeys(items)),
            set_pending_preview_2d_data_fn=lambda data: state.__setitem__("p2d", data),
            set_pending_preview_3d_data_fn=lambda data: state.__setitem__("p3d", data),
            set_pending_preview_warnings_fn=lambda warns: state.__setitem__("warnings", warns),
            set_pending_preview_meta_fn=lambda meta: state.__setitem__("meta", meta),
            script_type_2d=ScriptType.SCRIPT_2D,
            script_type_3d=ScriptType.SCRIPT_3D,
            unknown_command_policy="error",
        )

        self.assertFalse(ok)
        self.assertIn("失败", msg)
        self.assertIn("precheck warning", state["warnings"])
        self.assertTrue(any("[pending preview] 执行失败" in item for item in state["warnings"]))
        self.assertEqual(state["meta"]["kind"], "3D")
        self.assertEqual(state["meta"]["source"], "pending")


if __name__ == "__main__":
    unittest.main()
