"""
Tests for the BS2G execution shim layer:
  - mathutils_shim: Vector / Matrix / Quaternion semantics
  - mesh_capture v2: object placement, bpy.ops add-ops, sys.exit handling
"""

import math
import unittest

from openbrep.importers.blender_script.mathutils_shim import Matrix, Vector
from openbrep.importers.blender_script.mesh_capture import (
    MeshCaptureError,
    run_mesh_capture,
)


class TestShimVector(unittest.TestCase):

    def test_arithmetic_and_ops(self):
        a = Vector((1, 2, 3))
        b = Vector((4, 5, 6))
        self.assertEqual((a + b).to_tuple(), (5.0, 7.0, 9.0))
        self.assertEqual((b - a).to_tuple(), (3.0, 3.0, 3.0))
        self.assertEqual((a * 2).to_tuple(), (2.0, 4.0, 6.0))
        self.assertAlmostEqual(a.dot(b), 32.0)
        self.assertEqual(a.cross(Vector((0, 1, 0))).to_tuple(), (-3.0, 0.0, 1.0))
        self.assertAlmostEqual(Vector((3, 4, 0)).length, 5.0)
        self.assertAlmostEqual(Vector((3, 4, 0)).normalized().length, 1.0)

    def test_mutable_components(self):
        v = Vector((1, 2, 3))
        v.x = 10
        self.assertEqual(v.x, 10.0)
        v[1] = 20
        self.assertEqual(v.y, 20.0)
        v2 = Vector((3, 4, 0))
        v2.normalize()
        self.assertAlmostEqual(v2.length, 1.0)

    def test_to_track_quat_maps_axis(self):
        q = Vector((0, 0, 2)).to_track_quat("Z", "Y")
        m = q.to_matrix()
        z_col = Vector((m[0][2], m[1][2], m[2][2]))
        self.assertAlmostEqual(z_col.z, 1.0, places=6)
        # 90°-rotated vector: track axis follows
        q2 = Vector((1, 0, 0)).to_track_quat("Z", "Y")
        m2 = q2.to_matrix()
        z_col2 = Vector((m2[0][2], m2[1][2], m2[2][2]))
        self.assertAlmostEqual(z_col2.x, 1.0, places=6)


class TestShimMatrix(unittest.TestCase):

    def test_rotation_z90(self):
        m = Matrix.Rotation(math.radians(90), 4, "Z")
        v = m @ Vector((1, 0, 0))
        self.assertAlmostEqual(v.x, 0.0, places=6)
        self.assertAlmostEqual(v.y, 1.0, places=6)

    def test_translation_point_transform(self):
        m = Matrix.Translation((10, 20, 30))
        v = m @ Vector((1, 2, 3))
        self.assertEqual(v.to_tuple(), (11.0, 22.0, 33.0))

    def test_matmul_chain(self):
        m = Matrix.Translation((1, 0, 0)) @ Matrix.Rotation(math.radians(90), 4, "Z")
        v = m @ Vector((1, 0, 0))
        self.assertAlmostEqual(v.x, 1.0, places=6)
        self.assertAlmostEqual(v.y, 1.0, places=6)

    def test_inverted(self):
        m = Matrix.LocRotScale((1, 2, 3), Matrix.Rotation(0.5, 4, "Y"), (2, 2, 2))
        ident = m @ m.inverted()
        for i in range(4):
            for j in range(4):
                expect = 1.0 if i == j else 0.0
                self.assertAlmostEqual(ident[i][j], expect, places=5)

    def test_loc_rot_scale(self):
        m = Matrix.LocRotScale((5, 0, 0), Matrix.Identity(4), (2, 2, 2))
        v = m @ Vector((1, 1, 1))
        self.assertEqual(v.to_tuple(), (7.0, 2.0, 2.0))


class TestShimCapturePlacement(unittest.TestCase):

    def test_object_location_applied(self):
        code = """
import bpy, bmesh

mesh = bpy.data.meshes.new("M")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=2)
bm.to_mesh(mesh)
bm.free()
obj = bpy.data.objects.new("M", mesh)
obj.location = (100, 0, 0)
"""
        mesh = run_mesh_capture(code)
        xs = [v[0] for v in mesh.verts]
        self.assertAlmostEqual(min(xs), 99.0)
        self.assertAlmostEqual(max(xs), 101.0)

    def test_matrix_world_wins_over_location(self):
        code = """
import bpy, bmesh, mathutils

mesh = bpy.data.meshes.new("M")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=2)
bm.to_mesh(mesh)
bm.free()
obj = bpy.data.objects.new("M", mesh)
obj.location = (100, 0, 0)
obj.matrix_world = mathutils.Matrix.Translation((0, 50, 0))
"""
        mesh = run_mesh_capture(code)
        ys = [v[1] for v in mesh.verts]
        self.assertAlmostEqual(min(ys), 49.0)
        self.assertAlmostEqual(max(ys), 51.0)

    def test_primitive_ops_create_geometry(self):
        code = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(5, 0, 0))
"""
        mesh = run_mesh_capture(code)
        self.assertEqual(len(mesh.verts), 8)
        self.assertEqual(len(mesh.faces), 6)
        xs = [v[0] for v in mesh.verts]
        self.assertAlmostEqual(min(xs), 4.0)

    def test_camera_add_sets_active_object(self):
        code = """
import bpy, mathutils

bpy.ops.object.camera_add(location=(1, 2, 3))
cam = bpy.context.active_object
cam.name = "CAM"
d = mathutils.Vector((0, 0, 0)) - cam.location
cam.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

# geometry so capture has something
import bmesh
mesh = bpy.data.meshes.new("M")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1)
bm.to_mesh(mesh)
bm.free()
bpy.data.objects.new("M", mesh)
"""
        mesh = run_mesh_capture(code)
        self.assertEqual(len(mesh.verts), 8)

    def test_sys_exit_wrapped(self):
        code = "import bpy\nraise SystemExit('需要场景状态')\n"
        with self.assertRaises(MeshCaptureError) as ctx:
            run_mesh_capture(code)
        self.assertIn("sys.exit", str(ctx.exception))

    def test_boolean_modifier_warns(self):
        code = """
import bpy, bmesh

mesh = bpy.data.meshes.new("M")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1)
bm.to_mesh(mesh)
bm.free()
obj = bpy.data.objects.new("M", mesh)
obj.modifiers.new("Cut", 'BOOLEAN')
"""
        mesh = run_mesh_capture(code)
        self.assertTrue(any("布尔" in w for w in mesh.warnings))


if __name__ == "__main__":
    unittest.main()
