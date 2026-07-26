import unittest

from openbrep import gdl_previewer
from openbrep.gdl_previewer import preview_3d_script


class TestGDLPreviewerPhase1(unittest.TestCase):
    def test_unknown_command_policy_warn_ignore_error(self):
        script = "BLOCK 1,1,1\nFOO_CMD 1\n"

        res_warn = preview_3d_script(script, unknown_command_policy="warn")
        self.assertTrue(any("未支持命令 FOO_CMD" in w for w in res_warn.warnings))

        res_ignore = preview_3d_script(script, unknown_command_policy="ignore")
        self.assertFalse(any("未支持命令 FOO_CMD" in w for w in res_ignore.warnings))

        with self.assertRaises(ValueError):
            preview_3d_script(script, unknown_command_policy="error")

    def test_warning_includes_line_and_command_structured(self):
        script = "BLOCK 1,1,1\nFOO_CMD 1\n"
        res = preview_3d_script(script, unknown_command_policy="warn")

        self.assertTrue(any(w.startswith("line 2:") for w in res.warnings))
        self.assertTrue(res.warnings_structured)
        item = res.warnings_structured[-1]
        self.assertEqual(item.line, 2)
        self.assertEqual(item.command, "FOO_CMD")
        self.assertEqual(item.code, "UNKNOWN_COMMAND")

    def test_quality_fast_vs_accurate_density(self):
        script = "SPHERE 1\n"
        fast = preview_3d_script(script, quality="fast")
        accurate = preview_3d_script(script, quality="accurate")

        self.assertEqual(len(fast.meshes), 1)
        self.assertEqual(len(accurate.meshes), 1)
        self.assertGreater(len(accurate.meshes[0].x), len(fast.meshes[0].x))
        self.assertGreater(len(accurate.meshes[0].i), len(fast.meshes[0].i))

    def test_transform_rot_mul_commands(self):
        script = """\
MULX 2
ROTZ 90
BLOCK 1, 1, 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(min(mesh.x), -1.0, places=6)
        self.assertAlmostEqual(max(mesh.x), 0.0, places=6)
        self.assertAlmostEqual(min(mesh.y), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 2.0, places=6)
        self.assertFalse(any("未支持命令 ROT" in w for w in res.warnings))

    def test_quality_profile_baseline_values(self):
        fast = gdl_previewer._quality_profile("fast")
        accurate = gdl_previewer._quality_profile("accurate")

        self.assertEqual(fast["frustum_seg"], 24)
        self.assertEqual(fast["sphere_steps"], (10, 20))
        self.assertEqual(accurate["frustum_seg"], 48)
        self.assertEqual(accurate["sphere_steps"], (20, 40))

    def test_unknown_quality_falls_back_to_fast_profile(self):
        script = "CYLIND 2, 1\n"
        fast = preview_3d_script(script, quality="fast")
        bad = preview_3d_script(script, quality="unexpected")

        self.assertEqual(len(fast.meshes[0].x), len(bad.meshes[0].x))
        self.assertEqual(len(fast.meshes[0].i), len(bad.meshes[0].i))

    def test_mesh_source_ref_tracks_command_and_line(self):
        script = """\
! comment
ADDZ 1
BLOCK 1, 2, 3
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        ref = res.meshes[0].source_ref
        self.assertIsNotNone(ref)
        self.assertEqual(ref.script_type, "3d")
        self.assertEqual(ref.line, 3)
        self.assertEqual(ref.command, "BLOCK")
        self.assertEqual(ref.label, "3D line 3 BLOCK")

    def test_basic_3d_mesh_commands_include_source_ref(self):
        script = """\
CYLIND 1, 0.5
SPHERE 0.25
PRISM 3, 1, 0,0, 1,0, 0,1
PRISM_ 3, 1, 0,0, 1,0, 0,1
"""
        res = preview_3d_script(script)

        self.assertEqual([m.source_ref.command for m in res.meshes], ["CYLIND", "SPHERE", "PRISM", "PRISM_"])
        self.assertEqual([m.source_ref.line for m in res.meshes], [1, 2, 3, 4])

    def test_if_block_executes_true_branch_and_skips_false_branch(self):
        script = """\
IF has_back_panel = 1 THEN
    BLOCK 1, 1, 1
ENDIF
IF has_back_panel = 0 THEN
    BLOCK 9, 9, 9
ENDIF
"""
        res = preview_3d_script(script, {"has_back_panel": 1})

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].source_ref.line, 2)
        self.assertFalse(any("IF 条件解析失败" in w for w in res.warnings))

    def test_setup_script_supports_bookshelf_derived_variables(self):
        script = """\
TOLER 0.001
MATERIAL mat_frame
BLOCK frame_thk, B, ZZYZX
ADDX A - frame_thk
BLOCK frame_thk, B, ZZYZX
DEL 1
MATERIAL mat_shelf
ADDX frame_thk
BLOCK _inner_w, B, shelf_thickness
DEL 1
ADDX frame_thk
ADDZ ZZYZX - shelf_thickness
BLOCK _inner_w, B, shelf_thickness
DEL 2
FOR i = 1 TO shelf_count - 2
    _z = shelf_thickness + i * _shelf_gap
    ADDX frame_thk
    ADDZ _z
    BLOCK _inner_w, B, shelf_thickness
    DEL 2
NEXT i
IF has_back_panel = 1 THEN
    MATERIAL mat_frame
    ADDY B - back_thk
    BLOCK A, back_thk, ZZYZX
    DEL 1
ENDIF
END
"""
        setup = """\
_inner_w = A - 2 * frame_thk
_shelf_gap = (ZZYZX - shelf_thickness * shelf_count) / (shelf_count - 1)
"""
        params = {
            "A": 2,
            "B": 0.4,
            "ZZYZX": 2,
            "frame_thk": 0.05,
            "shelf_thickness": 0.04,
            "shelf_count": 5,
            "has_back_panel": 1,
            "back_thk": 0.02,
        }

        res = preview_3d_script(script, params, setup_script=setup)

        self.assertEqual(len(res.meshes), 8)
        self.assertEqual([m.source_ref.line for m in res.meshes], [3, 5, 9, 13, 19, 19, 19, 25])
        self.assertFalse(any("未定义变量 _inner_w" in w for w in res.warnings))
        self.assertFalse(any("未支持命令 TOLER" in w for w in res.warnings))
        self.assertFalse(any("mat_frame" in w or "mat_shelf" in w for w in res.warnings))

    def test_prism_status_triplets_use_gdl_x_y_status_order(self):
        script = """\
PRISM_ 3, 0.04,
    1 * COS(0), 1 * SIN(0), 15,
    1 * COS(30), 1 * SIN(30), 15,
    0.1 * COS(0), 0.1 * SIN(0), 15
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(max(mesh.x), 1.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 0.5, places=6)
        self.assertLess(max(abs(y) for y in mesh.y), 1.0)

    def test_spiral_stair_fan_steps_preview(self):
        script = """\
TOLER 0.001
RESOL 36

IF r_outer <= 0 THEN r_outer = 0.8
IF r_inner <= 0 THEN r_inner = 0.06
IF r_outer <= r_inner THEN r_outer = r_inner + 0.5
IF h_total <= 0 THEN h_total = 2.90
IF n_step < 2 THEN n_step = 16
IF step_thk <= 0 THEN step_thk = 0.04
IF ang_total <= 0 THEN ang_total = 180
IF col_h <= 0 THEN col_h = h_total

_stepH = h_total / n_step
_stepAng = ang_total / n_step

IF mat_col > 0 THEN
    MATERIAL mat_col
ELSE
    MATERIAL SYMB_MAT
ENDIF

CYLIND col_h, r_inner

IF mat_step > 0 THEN
    MATERIAL mat_step
ELSE
    MATERIAL SYMB_MAT
ENDIF

FOR i = 0 TO n_step - 1
    _z = i * _stepH
    _baseAng = i * _stepAng

    a0 = 0
    a1 = _stepAng * 1 / 6
    a2 = _stepAng * 2 / 6
    a3 = _stepAng * 3 / 6
    a4 = _stepAng * 4 / 6
    a5 = _stepAng * 5 / 6
    a6 = _stepAng

    ADDZ _z
    ROTZ _baseAng

    PRISM_ 14, step_thk,
        r_outer * COS(a0), r_outer * SIN(a0), 15,
        r_outer * COS(a1), r_outer * SIN(a1), 15,
        r_outer * COS(a2), r_outer * SIN(a2), 15,
        r_outer * COS(a3), r_outer * SIN(a3), 15,
        r_outer * COS(a4), r_outer * SIN(a4), 15,
        r_outer * COS(a5), r_outer * SIN(a5), 15,
        r_outer * COS(a6), r_outer * SIN(a6), 15,
        r_inner * COS(a6), r_inner * SIN(a6), 15,
        r_inner * COS(a5), r_inner * SIN(a5), 15,
        r_inner * COS(a4), r_inner * SIN(a4), 15,
        r_inner * COS(a3), r_inner * SIN(a3), 15,
        r_inner * COS(a2), r_inner * SIN(a2), 15,
        r_inner * COS(a1), r_inner * SIN(a1), 15,
        r_inner * COS(a0), r_inner * SIN(a0), 15

    DEL 2
NEXT i

ADDZ h_total
ROTZ ang_total
ADD r_inner, -0.04, 0
BLOCK r_outer * 0.95, 0.08, step_thk
DEL 3
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 18)
        self.assertEqual([mesh.source_ref.command for mesh in res.meshes[:2]], ["CYLIND", "PRISM_"])
        self.assertFalse(any("IF 缺少匹配 ENDIF" in w for w in res.warnings))
        self.assertFalse(any("未定义变量" in w for w in res.warnings))
        self.assertLess(max(max(abs(x) for x in mesh.x) for mesh in res.meshes), 1.0)
        self.assertLess(max(max(abs(y) for y in mesh.y) for mesh in res.meshes), 1.0)


class TestRuledPreview(unittest.TestCase):

    def test_ruled_basic_square_loft(self):
        """RULED{2} between two squares → one mesh, closed quad strip."""
        script = """\
RULED{2} 4, 52,
    0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
    0, 0, 2, 1, 0, 2, 1, 1, 2, 0, 1, 2
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertEqual(mesh.name, "RULED")
        self.assertEqual(len(mesh.x), 8)
        # Closed strip: 4 quads → 8 triangles, no caps (mask 52 = j3+j5+j6)
        self.assertEqual(len(mesh.i), 8)
        self.assertEqual(res.warnings, [])

    def test_ruled_caps_add_centroid_fans(self):
        """Mask j1+j2 adds base/top cap fans with centroid vertices."""
        script = """\
RULED{2} 4, 55,
    0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
    0, 0, 2, 1, 0, 2, 1, 1, 2, 0, 1, 2
"""
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        # 8 ring verts + 2 cap centroids
        self.assertEqual(len(mesh.x), 10)
        # 8 side tris + 4 base cap + 4 top cap
        self.assertEqual(len(mesh.i), 16)

    def test_ruled_multiline_continuation_and_addz(self):
        """Trailing-comma continuation + ADDZ offset apply."""
        script = """\
ADDZ 100
RULED 4, 52,
    0, 0, 0,
    1, 0, 0,
    1, 1, 0,
    0, 1, 0,
    0, 0, 2,
    1, 0, 2,
    1, 1, 2,
    0, 1, 2
DEL 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        self.assertAlmostEqual(min(res.meshes[0].z), 100.0)
        self.assertAlmostEqual(max(res.meshes[0].z), 102.0)

    def test_ruled_no_more_unsupported_warning(self):
        """Generated loft GDL (BS2G mesh mode) previews without warnings."""
        from pathlib import Path
        from openbrep.importers.blender_script.converter import convert_blender_script
        import tempfile

        code = (Path(__file__).parent / "fixtures" / "blender" / "loft_mini.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, _ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            res = preview_3d_script(project.get_script(ScriptType.SCRIPT_3D))
        # loft_mini: 3 rings → 2 RULED segments
        self.assertEqual(len(res.meshes), 2)
        self.assertEqual(res.warnings, [])
        # 8 pts/ring, closed, caps on first/last segment
        self.assertEqual(len(res.meshes[0].x), 17)  # 16 ring verts + base centroid
        self.assertEqual(len(res.meshes[1].x), 17)  # 16 + top centroid
        self.assertAlmostEqual(min(res.meshes[0].z), 0.0)
        self.assertAlmostEqual(max(res.meshes[-1].z), 1.0)


if __name__ == "__main__":
    unittest.main()
