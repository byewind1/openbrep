"""
Tests for BS2G mesh/loft mode:
  - mesh_capture: bpy/bmesh shim execution + mesh recording
  - loft_detect: ring recovery, cap-center exclusion, rejection of non-lofts
  - loft_gdl: RULED{2} chain emission (stack-balanced, mask rules)
  - converter routing: auto mode split + the no-convertible-geometry guard
"""

import sys
import tempfile
import unittest
from pathlib import Path

from openbrep.importers.blender_script.converter import (
    convert_blender_script,
    probe_object_name,
)
from openbrep.importers.blender_script.loft_detect import (
    LoftDetectError,
    detect_loft,
)
from openbrep.importers.blender_script.loft_gdl import generate_loft_3d
from openbrep.importers.blender_script.mesh_capture import (
    CapturedMesh,
    MeshCaptureError,
    run_mesh_capture,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blender"


def _flat(gdl: str) -> str:
    """Collapse continuation-line whitespace for sequence assertions."""
    import re as _re
    return _re.sub(r"\s+", " ", gdl)

TWO_RING_CODE = """
import bpy, bmesh

mesh = bpy.data.meshes.new("T")
bm = bmesh.new()
square_lo = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
square_hi = [(0, 0, 2), (1, 0, 2), (1, 1, 2), (0, 1, 2)]
ra = [bm.verts.new(p) for p in square_lo]
rb = [bm.verts.new(p) for p in square_hi]
for i in range(4):
    j = (i + 1) % 4
    bm.faces.new([ra[i], ra[j], rb[j], rb[i]])
bm.to_mesh(mesh)
bm.free()
"""


class TestMeshCapture(unittest.TestCase):

    def test_captures_verts_and_faces(self):
        mesh = run_mesh_capture(TWO_RING_CODE)
        self.assertEqual(len(mesh.verts), 8)
        self.assertEqual(len(mesh.faces), 4)
        self.assertEqual(mesh.verts[0], (0.0, 0.0, 0.0))
        self.assertEqual(mesh.faces[0], [0, 1, 5, 4])

    def test_sys_modules_restored(self):
        before_bpy = sys.modules.get("bpy")
        before_bmesh = sys.modules.get("bmesh")
        run_mesh_capture(TWO_RING_CODE)
        self.assertIs(sys.modules.get("bpy"), before_bpy)
        self.assertIs(sys.modules.get("bmesh"), before_bmesh)

    def test_sys_modules_restored_on_error(self):
        before = sys.modules.get("bpy")
        with self.assertRaises(MeshCaptureError):
            run_mesh_capture("import bpy\nraise RuntimeError('boom')\n")
        self.assertIs(sys.modules.get("bpy"), before)

    def test_script_exception_wrapped(self):
        with self.assertRaises(MeshCaptureError) as ctx:
            run_mesh_capture("import bpy\nx = 1 / 0\n")
        self.assertIn("脚本执行失败", str(ctx.exception))

    def test_no_geometry_raises(self):
        with self.assertRaises(MeshCaptureError) as ctx:
            run_mesh_capture("import bpy\nprint('nothing')\n")
        self.assertIn("未捕获到", str(ctx.exception))


class TestLoftDetect(unittest.TestCase):

    def test_two_ring_loft(self):
        mesh = run_mesh_capture(TWO_RING_CODE)
        model = detect_loft(mesh)
        self.assertEqual(model.axis, 2)
        self.assertEqual(len(model.rings), 2)
        self.assertEqual(len(model.rings[0]), 4)

    def test_cap_centers_excluded(self):
        code = (FIXTURES_DIR / "loft_mini.py").read_text(encoding="utf-8")
        mesh = run_mesh_capture(code)
        # 3 rings x 8 + 2 cap centers
        self.assertEqual(len(mesh.verts), 26)
        model = detect_loft(mesh)
        self.assertEqual(len(model.rings), 3)
        self.assertTrue(all(len(r) == 8 for r in model.rings))
        # Cap centers (0,0,0) / (0,0,1) must not appear in rings
        flat = {p for ring in model.rings for p in ring}
        self.assertNotIn((0.0, 0.0, 0.0), flat)

    def test_non_loft_rejected(self):
        # Single ring with a fan only (no quad strips)
        code = """
import bpy, bmesh
bm = bmesh.new()
vs = [bm.verts.new(p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0)]]
bm.faces.new(vs)
"""
        mesh = run_mesh_capture(code)
        with self.assertRaises(LoftDetectError):
            detect_loft(mesh)

    def test_y_axis_loft_detected(self):
        # Triangle rings at y=0 / y=3 with varying z — not z-groupable,
        # so detection must fall through to the Y axis.
        code = """
import bpy, bmesh
bm = bmesh.new()
lo = [(0, 0, 0), (1, 0, 0.5), (0, 0, 1)]
hi = [(0, 3, 0), (1, 3, 0.5), (0, 3, 1)]
ra = [bm.verts.new(p) for p in lo]
rb = [bm.verts.new(p) for p in hi]
for i in range(3):
    j = (i + 1) % 3
    bm.faces.new([ra[i], ra[j], rb[j], rb[i]])
"""
        mesh = run_mesh_capture(code)
        model = detect_loft(mesh)
        self.assertEqual(model.axis, 1)
        self.assertEqual(len(model.rings), 2)


class TestLoftGdl(unittest.TestCase):

    def test_ruled_chain_exact(self):
        mesh = run_mesh_capture(TWO_RING_CODE)
        model = detect_loft(mesh)
        gdl = generate_loft_3d(model, source_name="T")
        # One segment: first == last → mask 52 + 1 + 2 = 55
        self.assertIn("RULED{2} 4, 55,", gdl)
        self.assertIn("ADDZ 0", gdl)
        # Base ring (u,v,0) then top ring (x,y,dz=2), in ring order
        flat = _flat(gdl)
        self.assertIn("0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,", flat)
        self.assertIn("0, 0, 2, 1, 0, 2, 1, 1, 2, 0, 1, 2", flat)
        self.assertTrue(gdl.rstrip().endswith("END"))

    def test_segment_masks(self):
        code = (FIXTURES_DIR / "loft_mini.py").read_text(encoding="utf-8")
        model = detect_loft(run_mesh_capture(code))
        gdl = generate_loft_3d(model, source_name="mini")
        # 2 segments: masks 53 (j1) and 54 (j2), balanced stack
        self.assertIn("RULED{2} 8, 53,", gdl)
        self.assertIn("RULED{2} 8, 54,", gdl)
        self.assertEqual(gdl.count("ADDZ"), gdl.count("DEL 1"))
        self.assertEqual(gdl.count("RULED{2}"), 2)

    def test_scale_applied(self):
        mesh = run_mesh_capture(TWO_RING_CODE)
        model = detect_loft(mesh)
        gdl = generate_loft_3d(model, scale=0.001, source_name="T")
        self.assertIn("0, 0, 0.002, 0.001, 0, 0.002, 0.001, 0.001, 0.002", _flat(gdl))

    def test_y_axis_permuted_to_z(self):
        code = """
import bpy, bmesh
bm = bmesh.new()
lo = [(0, 0, 0), (1, 0, 0.5), (0, 0, 1)]
hi = [(0, 3, 0), (1, 3, 0.5), (0, 3, 1)]
ra = [bm.verts.new(p) for p in lo]
rb = [bm.verts.new(p) for p in hi]
for i in range(3):
    j = (i + 1) % 3
    bm.faces.new([ra[i], ra[j], rb[j], rb[i]])
"""
        model = detect_loft(run_mesh_capture(code))
        gdl = generate_loft_3d(model, source_name="Y")
        # After permutation (x,-z,y): second ring z-distance = 3
        self.assertIn("ADDZ 0", gdl)
        self.assertIn("0, 0, 3", gdl)


class TestConverterRouting(unittest.TestCase):

    def test_mesh_mode_conversion(self):
        code = (FIXTURES_DIR / "loft_mini.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            gdl = project.get_script(ScriptType.SCRIPT_3D)
            self.assertIn("RULED{2}", gdl)
            self.assertNotIn("bmesh.", gdl)
            self.assertNotIn("bpy.", gdl)
            # Bbox params: 2 x 2 x 1
            by_name = {p.name: p for p in project.parameters}
            self.assertEqual(by_name["A"].value, "2")
            self.assertEqual(by_name["ZZYZX"].value, "1")

    def test_mesh_mode_uses_obj_name_constant(self):
        code = 'import bpy, bmesh\nOBJ_NAME = "WING"\n' + TWO_RING_CODE.split(
            'import bpy, bmesh\n', 1)[1]
        self.assertEqual(probe_object_name(code), "WING")

    def test_guard_raises_on_no_convertible_geometry(self):
        code = "def f(frac):\n    if frac <= 0.016:\n        return 0.0\n    return 1.0\n"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                convert_blender_script(code, output_dir=tmp)
            self.assertIn("没有可转换的几何操作", str(ctx.exception))

    def test_guard_raises_on_syntax_error_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                convert_blender_script("def f(:\n    pass", output_dir=tmp)

    def test_guard_raises_on_no_function_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                convert_blender_script("x = 1\ny = 2\n", output_dir=tmp)

    def test_primitive_mode_unchanged(self):
        code = "def make_box(w=1.0):\n    bpy.ops.mesh.primitive_cube_add(size=w)\n"
        self.assertEqual(probe_object_name(code), "make_box")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            self.assertEqual(project.name, "make_box")
            self.assertEqual(len(ir.warnings), 0)


# ── Arbitrary-mesh fallback (VERT/EDGE/PGON/BODY) ───────────


NON_LOFT_CODE = """
import bpy, bmesh

mesh = bpy.data.meshes.new("T")
bm = bmesh.new()
v = [bm.verts.new(p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0.5,0.5,1)]]
bm.faces.new([v[0], v[3], v[2], v[1]])
bm.faces.new([v[0], v[1], v[4]])
bm.faces.new([v[1], v[2], v[4]])
bm.faces.new([v[2], v[3], v[4]])
bm.faces.new([v[3], v[0], v[4]])
bm.to_mesh(mesh)
bm.free()
"""


class TestArbitraryMeshFallback(unittest.TestCase):

    def test_emit_topology_body(self):
        from openbrep.importers.blender_script.mesh_gdl import generate_mesh_3d

        mesh = run_mesh_capture(NON_LOFT_CODE)
        gdl = generate_mesh_3d(mesh, source_name="T")
        # 5 verts, 5 faces (1 quad + 4 tris), 8 unique edges
        self.assertEqual(gdl.count("\nVERT "), 5)
        self.assertEqual(gdl.count("\nEDGE "), 8)
        self.assertEqual(gdl.count("\nPGON "), 5)
        self.assertIn("PGON 4, 0, -1,", gdl)
        self.assertIn("PGON 3, 0, -1,", gdl)
        self.assertIn("BODY -1", gdl)
        self.assertTrue(gdl.rstrip().endswith("END"))
        # Interior edges (quad↔tri) are used once forward and once reversed
        self._assert_manifold_signs(gdl)

    def _assert_manifold_signs(self, gdl: str):
        import re
        usage: dict[int, int] = {}
        for m in re.finditer(r"^PGON \d+, 0, -1, (.+)$", gdl, re.MULTILINE):
            for tok in m.group(1).split(","):
                eid = int(tok.strip())
                usage[abs(eid)] = usage.get(abs(eid), 0) + (1 if eid > 0 else -1)
        self.assertIn(0, usage.values())

    def test_fallback_preview_roundtrip(self):
        from openbrep.importers.blender_script.mesh_gdl import generate_mesh_3d
        from openbrep.gdl_previewer import preview_3d_script

        mesh = run_mesh_capture(NON_LOFT_CODE)
        res = preview_3d_script(generate_mesh_3d(mesh, source_name="T"))
        self.assertEqual(len(res.meshes), 1)
        # 1 quad (2 tris) + 4 tris = 6 triangles
        self.assertEqual(len(res.meshes[0].i), 6)
        self.assertEqual(res.warnings, [])

    def test_converter_falls_back_for_non_loft(self):
        from openbrep.hsf_project import ScriptType

        with tempfile.TemporaryDirectory() as tmp:
            project, _ir = convert_blender_script(NON_LOFT_CODE, output_dir=tmp)
            gdl = project.get_script(ScriptType.SCRIPT_3D)
            self.assertIn("VERT ", gdl)
            self.assertIn("BODY -1", gdl)
            self.assertIn("非放样结构", gdl)
            from openbrep.static_checker import StaticChecker
            result = StaticChecker().check(project)
            self.assertEqual(result.errors, [])

    def test_loft_still_preferred_over_fallback(self):
        code = (FIXTURES_DIR / "loft_mini.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, _ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            gdl = project.get_script(ScriptType.SCRIPT_3D)
            self.assertIn("RULED{2}", gdl)
            self.assertNotIn("PGON", gdl)


if __name__ == "__main__":
    unittest.main()
