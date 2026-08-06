"""
Tests for openbrep.semantic_verifier — geometry-level sanity checks that run
the lightweight gdl_previewer and compare the resulting mesh against the
script's own declared A/B/ZZYZX dimensions.
"""

import unittest

from openbrep.gdl_previewer import PreviewMesh3D, Preview3DResult
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.semantic_verifier import (
    _perturb_value,
    check_bounding_box_against_dimensions,
    check_mesh_health,
    sweep_parameters,
    verify_semantics,
)


def _box_mesh(dx: float, dy: float, dz: float) -> PreviewMesh3D:
    return PreviewMesh3D(
        name="box",
        x=[0, dx, dx, 0, 0, dx, dx, 0],
        y=[0, 0, dy, dy, 0, 0, dy, dy],
        z=[0, 0, 0, 0, dz, dz, dz, dz],
        i=[0], j=[1], k=[2],
    )


class TestCheckMeshHealth(unittest.TestCase):
    def test_no_geometry_command_is_a_noop(self):
        issues = check_mesh_health("! comment only\nADD 1,0,0\nDEL 1\n", Preview3DResult(meshes=[]))
        self.assertEqual(issues, [])

    def test_geometry_command_with_zero_meshes_flags_mesh_empty(self):
        issues = check_mesh_health("BLOCK A, B, ZZYZX\n", Preview3DResult(meshes=[]))
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check_type, "mesh_empty")
        self.assertTrue(issues[0].blocking)

    def test_healthy_box_passes(self):
        result = Preview3DResult(meshes=[_box_mesh(1, 1, 1)])
        issues = check_mesh_health("BLOCK A, B, ZZYZX\n", result)
        self.assertEqual(issues, [])

    def test_collapsed_on_one_axis_flags_mesh_degenerate(self):
        result = Preview3DResult(meshes=[_box_mesh(0, 1, 1)])
        issues = check_mesh_health("BLOCK A, B, ZZYZX\n", result)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check_type, "mesh_degenerate")
        self.assertIn("x", issues[0].detail)


class TestCheckBoundingBoxAgainstDimensions(unittest.TestCase):
    def test_matching_box_passes(self):
        result = Preview3DResult(meshes=[_box_mesh(1.0, 1.0, 1.0)])
        issues = check_bounding_box_against_dimensions(result, {"A": 1.0, "B": 1.0, "ZZYZX": 1.0})
        self.assertEqual(issues, [])

    def test_within_tolerance_passes(self):
        result = Preview3DResult(meshes=[_box_mesh(1.2, 1.0, 1.0)])
        issues = check_bounding_box_against_dimensions(
            result, {"A": 1.0, "B": 1.0, "ZZYZX": 1.0}, tolerance=0.5,
        )
        self.assertEqual(issues, [])

    def test_grossly_undersized_box_flags_bbox_mismatch(self):
        result = Preview3DResult(meshes=[_box_mesh(0.05, 0.05, 0.05)])
        issues = check_bounding_box_against_dimensions(
            result, {"A": 1.0, "B": 1.0, "ZZYZX": 1.0}, tolerance=0.5,
        )
        self.assertEqual(len(issues), 3)
        self.assertTrue(all(i.check_type == "bbox_mismatch" for i in issues))

    def test_rotated_axes_still_match_via_sorted_dims(self):
        # dims swapped between axes (e.g. a rotated object) should still match
        # since comparison is order-independent (sorted largest-to-smallest).
        result = Preview3DResult(meshes=[_box_mesh(1.0, 2.0, 3.0)])
        issues = check_bounding_box_against_dimensions(result, {"A": 3.0, "B": 1.0, "ZZYZX": 2.0})
        self.assertEqual(issues, [])

    def test_no_declared_dims_is_a_noop(self):
        result = Preview3DResult(meshes=[_box_mesh(1.0, 1.0, 1.0)])
        issues = check_bounding_box_against_dimensions(result, {})
        self.assertEqual(issues, [])

    def test_empty_mesh_is_a_noop_here(self):
        # mesh_empty is check_mesh_health's job; this check should not pile on.
        issues = check_bounding_box_against_dimensions(Preview3DResult(meshes=[]), {"A": 1.0})
        self.assertEqual(issues, [])


class TestSweepParameters(unittest.TestCase):
    def _project(self, name: str = "T") -> HSFProject:
        return HSFProject.create_new(name)

    def test_none_project_is_a_noop(self):
        self.assertEqual(sweep_parameters(None), [])

    def test_empty_3d_script_is_a_noop(self):
        project = self._project()
        project.scripts[ScriptType.SCRIPT_3D] = ""
        self.assertEqual(sweep_parameters(project), [])

    def test_baseline_with_no_mesh_is_a_noop(self):
        # mesh_empty is check_mesh_health's job; sweeping a broken baseline adds no signal.
        project = self._project()
        project.scripts[ScriptType.SCRIPT_3D] = "CIRCLE2 0, 0, 1\n"  # a 2D-only command in 3d.gdl
        self.assertEqual(sweep_parameters(project), [])

    def test_all_params_wired_produces_no_issues(self):
        project = self._project()
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        self.assertEqual(sweep_parameters(project), [])

    def test_dead_parameter_flagged_as_unresponsive_non_blocking(self):
        project = self._project()
        project.add_parameter(GDLParameter("n_shelves", "Integer", "", "4"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        issues = sweep_parameters(project)
        matching = [i for i in issues if i.check_type == "sweep_unresponsive" and "N_SHELVES" in i.detail]
        self.assertEqual(len(matching), 1)
        self.assertFalse(matching[0].blocking)

    def test_perturb_value_scales_non_boolean_1_0_by_ratio(self):
        # Regression (P1-b+d): _perturb_value(1.0, ratio) used to flip 1.0 -> 0.0
        # for ANY parameter whose current value happened to be 1.0, treating it
        # as a boolean flag. A Length/RealNum at 1.0 must be scaled, not toggled.
        self.assertEqual(_perturb_value(1.0, 0.5), 1.5)
        self.assertNotEqual(_perturb_value(1.0, 0.5), 0.0)
        self.assertEqual(_perturb_value(1.0, 0.0), 1.0)

    def test_perturb_value_toggles_boolean_param(self):
        # Boolean flags are still toggled 0 <-> 1 (scaling 0 would stay 0).
        self.assertEqual(_perturb_value(1.0, 0.5, is_boolean=True), 0.0)
        self.assertEqual(_perturb_value(0.0, 0.5, is_boolean=True), 1.0)

    def test_length_param_at_1_0_is_scaled_not_toggled(self):
        # End-to-end: a Length param whose current value is exactly 1.0 must be
        # swept by ratio (1 -> 1.5), never flipped to 0.0.
        project = self._project()
        project.add_parameter(GDLParameter("dead_len", "Length", "", "1.0"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        issues = sweep_parameters(project)
        unresponsive = [i for i in issues if i.check_type == "sweep_unresponsive" and "DEAD_LEN" in i.detail]
        self.assertEqual(len(unresponsive), 1)
        self.assertIn("从 1 改为 1.5", unresponsive[0].detail)

    def test_boolean_param_at_1_still_toggles_to_0(self):
        # End-to-end: a genuinely Boolean param at 1 still toggles to 0.
        project = self._project()
        project.add_parameter(GDLParameter("dead_flag", "Boolean", "", "1"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        issues = sweep_parameters(project)
        unresponsive = [i for i in issues if i.check_type == "sweep_unresponsive" and "DEAD_FLAG" in i.detail]
        self.assertEqual(len(unresponsive), 1)
        self.assertIn("从 1 改为 0", unresponsive[0].detail)

    def test_toggling_hasxxx_flag_off_is_non_blocking(self):
        # Turning an optional feature OFF (1 -> 0) removing its geometry is
        # expected behavior, not a bug — must not block CREATE.
        project = self._project()
        project.add_parameter(GDLParameter("has_shelf", "Boolean", "", "1"))
        project.scripts[ScriptType.SCRIPT_3D] = (
            "BLOCK A, B, ZZYZX\n"
            "IF has_shelf = 1 THEN\n"
            "ADD 0, 0, ZZYZX\n"
            "BLOCK A, B, 0.02\n"
            "DEL 1\n"
            "ENDIF\n"
        )
        issues = sweep_parameters(project)
        vanished = [i for i in issues if i.check_type == "sweep_mesh_vanished"]
        # The always-present base BLOCK keeps the mesh non-empty either way,
        # so this scenario shouldn't even vanish — assert no blocking issues at all.
        self.assertFalse(any(i.blocking for i in issues), issues)

    def test_flag_turning_on_and_erasing_everything_is_blocking(self):
        # A flag starting at 0 that, when turned on, wipes out geometry that
        # existed at baseline is a genuine bug signal.
        project = self._project()
        project.add_parameter(GDLParameter("hide_all", "Boolean", "", "0"))
        project.scripts[ScriptType.SCRIPT_3D] = (
            "IF hide_all = 0 THEN\n"
            "BLOCK A, B, ZZYZX\n"
            "ENDIF\n"
        )
        issues = sweep_parameters(project)
        vanished = [i for i in issues if i.check_type == "sweep_mesh_vanished"]
        self.assertEqual(len(vanished), 1)
        self.assertTrue(vanished[0].blocking)

    def test_internal_only_dimension_change_is_detected_as_responsive(self):
        # Regression: a parameter that only changes an inner mesh's own
        # extent (e.g. a shelf's thickness) without moving the outer case's
        # combined bounding box must still be recognized as "responsive" —
        # the scene-level bbox alone would miss this.
        project = self._project()
        project.add_parameter(GDLParameter("shelf_thk", "Length", "", "0.02"))
        project.scripts[ScriptType.SCRIPT_3D] = (
            "BLOCK A, B, ZZYZX\n"
            "ADD 0, 0, 0.1\n"
            "BLOCK A, B, shelf_thk\n"
            "DEL 1\n"
        )
        issues = sweep_parameters(project)
        self.assertEqual(issues, [])

    def test_max_params_caps_number_of_sweeps(self):
        project = self._project()
        for i in range(20):
            project.add_parameter(GDLParameter(f"p{i:02d}", "Length", "", "1.0"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        issues = sweep_parameters(project, max_params=3)
        # 3 dummy params + A/B/ZZYZX all wired -> only the capped dummy subset
        # (alphabetically first) could ever be flagged, so at most 3 issues total.
        self.assertLessEqual(len(issues), 3)


class TestVerifySemantics(unittest.TestCase):
    def _project(self, name: str = "T") -> HSFProject:
        return HSFProject.create_new(name)

    def test_none_project_is_safe_noop(self):
        result = verify_semantics(None)
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])

    def test_empty_3d_script_is_safe_noop(self):
        project = self._project()
        project.scripts[ScriptType.SCRIPT_3D] = ""
        result = verify_semantics(project)
        self.assertTrue(result.passed)

    def test_default_box_matching_reserved_params_passes(self):
        project = self._project()
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        result = verify_semantics(project)
        self.assertTrue(result.passed, result.issues)

    def test_hardcoded_geometry_ignoring_params_fails(self):
        project = self._project()
        for p in project.parameters:
            p.value = "10.0"
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 0.1, 0.1, 0.1\n"
        result = verify_semantics(project)
        self.assertFalse(result.passed)
        self.assertTrue(any(i.check_type == "bbox_mismatch" for i in result.issues))

    def test_zero_dimension_param_produces_degenerate_mesh(self):
        project = self._project()
        for p in project.parameters:
            if p.name == "A":
                p.value = "0"
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        result = verify_semantics(project)
        self.assertFalse(result.passed)
        self.assertTrue(any(i.check_type == "mesh_degenerate" for i in result.issues))

    def test_dead_parameter_surfaces_but_does_not_fail(self):
        project = self._project()
        project.add_parameter(GDLParameter("n_shelves", "Integer", "", "4"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        result = verify_semantics(project)
        self.assertTrue(result.passed)  # non-blocking: informational only
        self.assertTrue(any(i.check_type == "sweep_unresponsive" for i in result.issues))

    def test_sweep_false_skips_parameter_sweep_entirely(self):
        project = self._project()
        project.add_parameter(GDLParameter("n_shelves", "Integer", "", "4"))
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
        result = verify_semantics(project, sweep=False)
        self.assertFalse(any(i.check_type.startswith("sweep_") for i in result.issues))


if __name__ == "__main__":
    unittest.main()
