"""
Tests for openbrep.gdl_ast — the tolerant GDL parser used by StaticChecker's
branch-aware stack imbalance check.
"""

import unittest

from openbrep.gdl_ast import (
    ControlBlock,
    GeometryCall,
    TransformFrame,
    parse_gdl_script,
)


class TestFlatParsing(unittest.TestCase):
    def test_transform_frames_collected_at_top_level(self):
        script = parse_gdl_script("ADD 1, 0, 0\nDEL 1\n")
        self.assertEqual(len(script.frames), 1)
        self.assertEqual(script.frames[0].name, "ADD")
        self.assertEqual(script.frames[0].args, ["1", "0", "0"])
        # DEL is not a transform push, it goes to raw_lines
        self.assertEqual(len(script.raw_lines), 1)

    def test_geometry_calls_collected(self):
        script = parse_gdl_script("BLOCK 1, 1, 1\nCYLIND 2, 0.5\n")
        self.assertEqual([g.name for g in script.geometry], ["BLOCK", "CYLIND"])

    def test_blank_lines_and_comments_skipped(self):
        script = parse_gdl_script("\n! a comment\n   \nADD 1,1,1\n")
        self.assertEqual(len(script.frames), 1)
        self.assertEqual(len(script.raw_lines), 0)

    def test_single_line_if_does_not_open_block(self):
        script = parse_gdl_script("IF x = 1 THEN ADD 1,1,1\n")
        self.assertEqual(script.controls, [])
        self.assertEqual(len(script.raw_lines), 1)

    def test_delall_routes_to_raw_lines(self):
        script = parse_gdl_script("ADD 1,1,1\nDELALL\n")
        self.assertEqual(len(script.frames), 1)
        self.assertIn("DELALL", script.raw_lines[0].upper())

    def test_never_raises_on_malformed_input(self):
        malformed = "ENDIF\nNEXT\nELSE\nIF\nFOR\n???"
        script = parse_gdl_script(malformed)
        self.assertIsNotNone(script)


class TestIfElseBranching(unittest.TestCase):
    def test_then_and_else_children_separated(self):
        code = (
            "IF cond THEN\n"
            "ADD 1,0,0\n"
            "ELSE\n"
            "ADD 0,1,0\n"
            "DEL 1\n"
            "ENDIF\n"
        )
        script = parse_gdl_script(code)
        self.assertEqual(len(script.controls), 1)
        cb = script.controls[0]
        self.assertEqual(cb.kind, "IF")
        self.assertTrue(cb.has_else)
        self.assertEqual(len(cb.children), 1)
        self.assertIsInstance(cb.children[0], TransformFrame)
        self.assertEqual(len(cb.else_children), 1)
        self.assertEqual(len(cb.else_raw_lines), 1)

    def test_if_without_else(self):
        code = "IF cond THEN\nADD 1,0,0\nENDIF\n"
        script = parse_gdl_script(code)
        cb = script.controls[0]
        self.assertFalse(cb.has_else)
        self.assertEqual(cb.else_children, [])

    def test_nested_if_inside_then_branch(self):
        """Regression: nested IF must attach to the parent's THEN branch,
        not leak into the parent's ELSE or the top-level script."""
        code = (
            "IF outer THEN\n"
            "  IF inner THEN\n"
            "    ADD 1,0,0\n"
            "  ELSE\n"
            "    DEL 1\n"
            "  ENDIF\n"
            "ELSE\n"
            "  ADD 0,0,1\n"
            "ENDIF\n"
        )
        script = parse_gdl_script(code)
        self.assertEqual(len(script.controls), 1)
        outer = script.controls[0]
        self.assertEqual(len(outer.children), 1)
        inner = outer.children[0]
        self.assertIsInstance(inner, ControlBlock)
        self.assertEqual(inner.kind, "IF")
        self.assertTrue(inner.has_else)
        self.assertEqual(len(inner.children), 1)
        self.assertIsInstance(inner.children[0], TransformFrame)
        self.assertEqual(len(inner.else_raw_lines), 1)
        # outer's ELSE branch is untouched by the nested IF
        self.assertEqual(len(outer.else_children), 1)

    def test_nested_if_inside_else_branch(self):
        code = (
            "IF outer THEN\n"
            "  ADD 1,0,0\n"
            "ELSE\n"
            "  IF inner THEN\n"
            "    ADD 0,1,0\n"
            "  ENDIF\n"
            "ENDIF\n"
        )
        script = parse_gdl_script(code)
        outer = script.controls[0]
        self.assertEqual(len(outer.else_children), 1)
        inner = outer.else_children[0]
        self.assertIsInstance(inner, ControlBlock)
        self.assertFalse(inner.has_else)

    def test_unclosed_if_gets_end_lineno_set(self):
        script = parse_gdl_script("IF cond THEN\nADD 1,0,0\n")
        cb = script.controls[0]
        self.assertEqual(cb.end_lineno, 2)


class TestForBlocks(unittest.TestCase):
    def test_for_block_collects_body(self):
        code = "FOR i = 1 TO 5\nADD 1,0,0\nDEL 1\nNEXT i\n"
        script = parse_gdl_script(code)
        self.assertEqual(len(script.controls), 1)
        cb = script.controls[0]
        self.assertEqual(cb.kind, "FOR")
        self.assertEqual(len(cb.children), 1)
        self.assertEqual(len(cb.raw_lines), 1)

    def test_if_nested_inside_for(self):
        code = (
            "FOR i = 1 TO 3\n"
            "  IF i = 1 THEN\n"
            "    ADD 1,0,0\n"
            "  ENDIF\n"
            "NEXT i\n"
        )
        script = parse_gdl_script(code)
        for_block = script.controls[0]
        self.assertEqual(for_block.kind, "FOR")
        self.assertEqual(len(for_block.children), 1)
        self.assertIsInstance(for_block.children[0], ControlBlock)


if __name__ == "__main__":
    unittest.main()
