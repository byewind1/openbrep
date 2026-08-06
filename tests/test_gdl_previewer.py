import unittest
from pathlib import Path

from openbrep import gdl_previewer
from openbrep.gdl_parser import parse_gdl_source_with_warnings
from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.workbench.project_parameter_service import parameter_values


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
        """Generated loft GDL (BS2G mesh mode) previews without warnings,
        and consecutive segments weld into ONE smooth mesh."""
        from pathlib import Path
        from openbrep.importers.blender_script.converter import convert_blender_script
        import tempfile

        code = (Path(__file__).parent / "fixtures" / "blender" / "loft_mini.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, _ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            res = preview_3d_script(project.get_script(ScriptType.SCRIPT_3D))
        # loft_mini: 3 rings → 2 RULED segments welded into one chain mesh
        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.warnings, [])
        # 3 rings x 8 pts + base/top cap centroids
        self.assertEqual(len(res.meshes[0].x), 26)
        # 2 segments x 8 quads x 2 tris + 2 caps x 8 tris
        self.assertEqual(len(res.meshes[0].i), 48)
        self.assertAlmostEqual(min(res.meshes[0].z), 0.0)
        self.assertAlmostEqual(max(res.meshes[0].z), 1.0)


class TestGDLPreviewerStackAndSubroutines(unittest.TestCase):
    def test_put_get_expands_into_prism_arguments(self):
        script = """\
PUT 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0
PRISM_ 4, 0.5, GET(12)
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].name, "PRISM_")
        self.assertEqual(len(res.meshes[0].x), 8)  # 4 base + 4 top
        self.assertEqual(len(res.meshes[0].i), 12)  # 4 side quads + 2 caps
        self.assertEqual(res.warnings, [])

    def test_use_peeks_stack_without_consuming(self):
        script = """\
PUT 1, 2, 3
BLOCK USE(3)
BLOCK GET(3)
"""
        res = preview_3d_script(script)

        # USE peeks without popping, so GET can still retrieve the same values.
        self.assertEqual(len(res.meshes), 2)
        self.assertEqual(res.warnings, [])

    def test_gosub_numeric_labels_and_return(self):
        script = """\
GOSUB 100
GOSUB 200
END
100:
    BLOCK 1, 1, 1
    RETURN
200:
    ADDX 2
    BLOCK 1, 1, 1
    DEL 1
    RETURN
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 2)
        self.assertAlmostEqual(min(res.meshes[0].x), 0.0, places=6)
        self.assertAlmostEqual(max(res.meshes[0].x), 1.0, places=6)
        self.assertAlmostEqual(min(res.meshes[1].x), 2.0, places=6)
        self.assertAlmostEqual(max(res.meshes[1].x), 3.0, places=6)
        self.assertFalse(any("GOSUB" in w or "RETURN" in w for w in res.warnings))

    def test_gosub_string_labels(self):
        script = '''\
GOSUB "box"
END
"box":
    BLOCK 2, 2, 2
    RETURN
'''
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertAlmostEqual(max(res.meshes[0].x), 2.0, places=6)

    def test_end_terminates_script_no_double_subroutine_execution(self):
        script = """\
GOSUB 100
END
100:
    BLOCK 1, 1, 1
    RETURN
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)

    def test_tube_helix_sweep(self):
        script = """\
PUT 0,0,0, 0,0,1, 0,0,2
PUT 0.1,0, -0.1,0, 0,0.1, 0,-0.1
TUBE_ 3, 4, 127, GET(17)
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].name, "TUBE_")
        # 3 path rings x 4 section verts + 2 cap centroids = 14
        self.assertEqual(len(res.meshes[0].x), 14)
        # 2 segments x 4 quads x 2 tris + 2 caps x 4 tris = 24
        self.assertEqual(len(res.meshes[0].i), 24)
        self.assertEqual(res.warnings, [])

    def test_spiral_stair_put_get_gosub_tube(self):
        script = """\
TOLER 0.005
PEN 1
ADD A / 2, B / 2, 0
GOSUB 1000
GOSUB 2000
GOSUB 3000
GOSUB 4000
DEL 1
END

1000:
    CYLIND height, pole_radius
RETURN

2000:
    FOR i = 0 TO num_steps - 1
        ADDZ i * step_riser
        ROTZ i * step_angle
        FOR k = 0 TO _seg
            PUT tread_outer_radius * COS(k * step_angle / _seg), tread_outer_radius * SIN(k * step_angle / _seg), 0
        NEXT k
        FOR k = _seg TO 0 STEP -1
            PUT pole_radius * COS(k * step_angle / _seg), pole_radius * SIN(k * step_angle / _seg), 0
        NEXT k
        PRISM_ 2 * (_seg + 1), tread_thickness, GET(2 * (_seg + 1) * 3)
        DEL 2
    NEXT i
RETURN

3000:
    _path_pts = num_steps + 1
    _sec_pts = 8
    _tube_vals = _path_pts * 3 + _sec_pts * 2
    FOR i = 0 TO num_steps
        PUT tread_outer_radius * COS((i + 0.5) * step_angle), tread_outer_radius * SIN((i + 0.5) * step_angle), (i + 1) * step_riser + handrail_height
    NEXT i
    FOR k = 0 TO 7
        PUT _rail_r * COS(k * 45), _rail_r * SIN(k * 45)
    NEXT k
    TUBE_ _path_pts, _sec_pts, 127, GET(_tube_vals)
RETURN

4000:
    MATERIAL mat_landing
    ADDZ height - _landing_thk
    ROTZ num_steps * step_angle
    ADDY -_landing_wid / 2
    BLOCK _landing_len, _landing_wid, _landing_thk
    DEL 3
RETURN
"""
        params = {
            "A": 2.0,
            "B": 2.0,
            "height": 3.0,
            "num_steps": 12,
            "pole_radius": 0.06,
            "tread_outer_radius": 0.8,
            "step_riser": 0.25,
            "step_angle": 30.0,
            "tread_thickness": 0.04,
            "_seg": 8,
            "handrail_height": 0.9,
            "_rail_r": 0.02,
            "_landing_thk": 0.04,
            "_landing_len": 1.0,
            "_landing_wid": 0.8,
        }
        res = preview_3d_script(script, params)

        commands = [m.source_ref.command for m in res.meshes]
        # 1 pole + 12 treads + 1 handrail tube + 1 landing
        self.assertEqual(commands.count("CYLIND"), 1)
        self.assertEqual(commands.count("PRISM_"), 12)
        self.assertEqual(commands.count("TUBE_"), 1)
        self.assertEqual(commands.count("BLOCK"), 1)
        self.assertEqual(res.warnings, [])


class TestGDLPreviewer2DCommands(unittest.TestCase):
    """2D 绘图命令：坐标级断言 + 无 unsupported 警告。"""

    def test_2d_rect2_polygon_corners(self):
        res = preview_2d_script("RECT2 0, 0, 4, 2\n")
        self.assertEqual(len(res.polygons), 1)
        self.assertEqual(
            res.polygons[0],
            [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)],
        )
        self.assertFalse(any("RECT2" in w for w in res.warnings))

    def test_2d_line2_endpoints(self):
        res = preview_2d_script("LINE2 1, 2, 3, 4\n")
        self.assertEqual(len(res.lines), 1)
        self.assertEqual(res.lines[0], ((1.0, 2.0), (3.0, 4.0)))
        self.assertFalse(any("LINE2" in w for w in res.warnings))

    def test_2d_circle2_center_radius(self):
        res = preview_2d_script("CIRCLE2 0.5, 0.5, 0.3\n")
        self.assertEqual(len(res.circles), 1)
        self.assertEqual(res.circles[0], (0.5, 0.5, 0.3))
        self.assertFalse(any("CIRCLE2" in w for w in res.warnings))

    def test_2d_arc2_center_radius_angles(self):
        res = preview_2d_script("ARC2 0.5, 0.5, 0.3, 0, 90\n")
        self.assertEqual(len(res.arcs), 1)
        self.assertEqual(res.arcs[0], (0.5, 0.5, 0.3, 0.0, 90.0))
        self.assertFalse(any("ARC2" in w for w in res.warnings))

    def test_2d_poly2_vertices(self):
        res = preview_2d_script("POLY2 4, 0, 0, 1, 0, 1, 1, 0, 1\n")
        self.assertEqual(len(res.polygons), 1)
        self.assertEqual(
            res.polygons[0],
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        )
        self.assertFalse(any("POLY2" in w for w in res.warnings))

    def test_2d_hotspot2_recognized_without_warning(self):
        res = preview_2d_script(
            "HOTSPOT2 0, 0, 1\nHOTSPOT2 A, 0, 2, A, 2\nRECT2 0, 0, A, B\n",
            parameters={"A": 0.8, "B": 0.3},
        )
        # HOTSPOT2 是交互热点（非渲染几何）：识别为无副作用，不告警、不产生几何。
        self.assertEqual(res.warnings, [])
        self.assertEqual(len(res.polygons), 1)

    def test_set_statement_no_side_effect_no_warning(self):
        r2d = preview_2d_script("SET MATERIAL wood\nRECT2 0, 0, 1, 1\n")
        self.assertFalse(any("SET" in w for w in r2d.warnings))
        self.assertEqual(len(r2d.polygons), 1)

        r3d = preview_3d_script("SET MATERIAL wood\nBLOCK 1, 1, 1\n")
        self.assertFalse(any("SET" in w for w in r3d.warnings))
        self.assertEqual(len(r3d.meshes), 1)

    def test_values_silently_ignored_in_2d_and_3d(self):
        values = 'VALUES "A" RANGE [0.30, 3.00]\n'
        r2d = preview_2d_script(values + "RECT2 0, 0, 1, 1\n")
        self.assertFalse(any("VALUES" in w for w in r2d.warnings))
        self.assertEqual(len(r2d.polygons), 1)

        r3d = preview_3d_script(values + "BLOCK 1, 1, 1\n")
        self.assertFalse(any("VALUES" in w for w in r3d.warnings))
        self.assertEqual(len(r3d.meshes), 1)

    def test_bookshelf_scenario_warnings_single_digit(self):
        """Bookshelf 场景验收：导入 examples/Bookshelf.gdl 后，2D+3D 预览
        总 unsupported 警告数降到个位数（硬指标）。"""
        bookshelf = Path(__file__).resolve().parents[1] / "examples" / "Bookshelf.gdl"
        project, _ = parse_gdl_source_with_warnings(
            bookshelf.read_text(encoding="utf-8"), "Bookshelf"
        )
        params = parameter_values(project)
        scripts = {k.name: v for k, v in project.scripts.items()}

        r2d = preview_2d_script(
            scripts["SCRIPT_2D"], parameters=params, setup_script=scripts["MASTER"]
        )
        r3d = preview_3d_script(
            scripts["SCRIPT_3D"], parameters=params, setup_script=scripts["MASTER"]
        )

        total = len(r2d.warnings) + len(r3d.warnings)
        self.assertLess(total, 10)
        # 几何不丢：2D 侧板线 + 外轮廓矩形，3D 框架/层板/背板网格。
        self.assertEqual(len(r2d.lines), 3)
        self.assertEqual(len(r2d.polygons), 1)
        self.assertGreaterEqual(len(r3d.meshes), 8)


if __name__ == "__main__":
    unittest.main()
