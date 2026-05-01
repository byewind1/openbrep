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



if __name__ == "__main__":
    unittest.main()
