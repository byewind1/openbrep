import unittest

from openbrep.gdl_linter import GDLLinter


class TestRule001ATN(unittest.TestCase):

    def test_check_warns_on_single_arg_atn_division(self):
        code = "angle = ATN(dy / dx)"
        result = GDLLinter(script_type="3D").check(code)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].rule, "RULE-001")
        self.assertEqual(result.issues[0].severity, "WARNING")

    def test_fix_rewrites_to_atan2_style_block(self):
        code = "angle = ATN(dy / dx)"
        result = GDLLinter(script_type="3D").fix(code)
        self.assertEqual(result.fix_count, 1)
        self.assertIn("[LINTER-FIXED RULE-001]", result.fixed_code)
        self.assertIn("_lint_dx = dx", result.fixed_code)
        self.assertIn("_lint_dy = dy", result.fixed_code)


class TestRule002Circle2(unittest.TestCase):

    def test_check_reports_circle2_as_error(self):
        code = "CIRCLE2 0, 0, 1"
        result = GDLLinter(script_type="2D").check(code)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.issues[0].rule, "RULE-002")

    def test_fix_replaces_circle2_with_arc2(self):
        code = "CIRCLE2 0, 0, 1"
        result = GDLLinter(script_type="2D").fix(code)
        self.assertEqual(result.fix_count, 1)
        self.assertIn("ARC2 0, 0, 1, 0, 360", result.fixed_code)


class TestRule003Hotspot2(unittest.TestCase):

    def test_check_reports_incomplete_hotspot2(self):
        code = "HOTSPOT2 5"
        result = GDLLinter(script_type="2D").check(code)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.issues[0].rule, "RULE-003")

    def test_fix_fills_default_hotspot2_coordinates(self):
        code = "HOTSPOT2 5"
        result = GDLLinter(script_type="2D").fix(code)
        self.assertEqual(result.fix_count, 1)
        self.assertIn("HOTSPOT2 0, 0", result.fixed_code)


class TestRule004Move(unittest.TestCase):

    def test_check_reports_move_as_error(self):
        code = "MOVE 1, 2, 3"
        result = GDLLinter(script_type="3D").check(code)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.issues[0].rule, "RULE-004")

    def test_fix_replaces_move_with_add(self):
        code = "MOVE 1, 2, 3"
        result = GDLLinter(script_type="3D").fix(code)
        self.assertEqual(result.fix_count, 1)
        self.assertIn("ADD 1, 2, 3", result.fixed_code)


class TestRule005PenIn3D(unittest.TestCase):

    def test_check_warns_pen_in_3d(self):
        code = "PEN 5"
        result = GDLLinter(script_type="3D").check(code)
        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.issues[0].rule, "RULE-005")

    def test_check_ignores_pen_in_2d(self):
        code = "PEN 5"
        result = GDLLinter(script_type="2D").check(code)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.issues, [])


class TestRule006Tube(unittest.TestCase):

    def test_check_warns_when_tube_has_variable_args(self):
        code = "TUBE seg_count, 1, 2, x0, y0, z0"
        result = GDLLinter(script_type="3D").check(code)
        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.issues[0].rule, "RULE-006")

    def test_check_ignores_numeric_only_tube_args(self):
        code = "TUBE 2, 1, 2, 0, 0, 0"
        result = GDLLinter(script_type="3D").check(code)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.issues, [])
