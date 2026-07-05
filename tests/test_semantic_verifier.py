"""
Tests for openbrep.semantic_verifier — geometry-level sanity checks that run
the lightweight gdl_previewer and compare the resulting mesh against the
script's own declared A/B/ZZYZX dimensions.
"""

import unittest

from openbrep.gdl_previewer import PreviewMesh3D, Preview3DResult
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.semantic_verifier import (
    check_bounding_box_against_dimensions,
    check_mesh_health,
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


if __name__ == "__main__":
    unittest.main()
