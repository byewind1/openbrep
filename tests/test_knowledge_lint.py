import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_lint_module():
    script = Path(__file__).parent.parent / "knowledge" / "scripts" / "lint-knowledge.py"
    spec = importlib.util.spec_from_file_location("lint_knowledge", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestKnowledgeLint(unittest.TestCase):
    def test_builtin_knowledge_lint_passes(self):
        lint_knowledge = _load_lint_module()
        root = Path(__file__).parent.parent / "knowledge"

        self.assertEqual(lint_knowledge.lint(str(root)), 0)

    def test_lint_fails_for_missing_archetype_field(self):
        lint_knowledge = _load_lint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "wiki").mkdir()
            (root / "wiki" / "BLOCK.md").write_text("---\ntype: reference\n---\n# BLOCK\n", encoding="utf-8")
            (root / "archetypes").mkdir()
            (root / "archetypes" / "bad.md").write_text(
                "---\n"
                "id: archetype.bad\n"
                "title: Bad\n"
                "type: archetype\n"
                "commands: [BLOCK]\n"
                "---\n"
                "# Bad\n",
                encoding="utf-8",
            )

            result = lint_knowledge.lint(str(root))

        self.assertEqual(result, 1)

    def test_officially_verified_wiki_pages_do_not_reintroduce_pseudo_syntax(self):
        root = Path(__file__).parent.parent / "knowledge" / "wiki"

        prism = (root / "PRISM_.md").read_text(encoding="utf-8")
        add_del = (root / "ADD_DEL.md").read_text(encoding="utf-8")
        stack = (root / "Transformation_Stack.md").read_text(encoding="utf-8")
        for_next = (root / "FOR_NEXT.md").read_text(encoding="utf-8")
        if_endif = (root / "IF_ENDIF.md").read_text(encoding="utf-8")
        project2 = (root / "PROJECT2.md").read_text(encoding="utf-8")
        hotspot2 = (root / "HOTSPOT2.md").read_text(encoding="utf-8")
        primitives2d = (root / "2D_Primitives.md").read_text(encoding="utf-8")
        cylind = (root / "CYLIND.md").read_text(encoding="utf-8")
        cutplane = (root / "CUTPLANE.md").read_text(encoding="utf-8")
        body_edge_pgon = (root / "BODY_EDGE_PGON.md").read_text(encoding="utf-8")
        revolve = (root / "REVOLVE.md").read_text(encoding="utf-8")
        sweep = (root / "SWEEP.md").read_text(encoding="utf-8")
        project2 = (root / "PROJECT2.md").read_text(encoding="utf-8")
        hotspot2 = (root / "HOTSPOT2.md").read_text(encoding="utf-8")
        block = (root / "BLOCK.md").read_text(encoding="utf-8")

        self.assertIn("PRISM_ n, h, x1, y1, s1", prism)
        self.assertIn("there must be exactly `n` triplets", prism)
        self.assertIn("ADD dx, dy, dz", add_del)
        self.assertIn("DEL n [, begin_with]", add_del)
        self.assertIn("DEL TOP", add_del)
        self.assertIn("NTR ()", add_del)
        self.assertIn("Scripts can only delete the transformations they define locally", stack)
        self.assertIn("FOR variable_name = initial_value TO end_value", for_next)
        self.assertIn("NEXT variable_name", for_next)
        self.assertIn("STEP` is omitted, the increment is `1", for_next)
        self.assertIn("only one `NEXT` is allowed for each `FOR`", for_next)
        self.assertIn("comparison operators are valid", if_endif.lower())
        self.assertIn("IF A > B THEN", if_endif)
        self.assertIn("IF hasArms AND hasBack THEN", if_endif)
        self.assertIn("PROJECT2 projection_code, angle, method", project2)
        self.assertIn("PROJECT2 3, 270, 2", project2)
        self.assertIn("does not define the cut plane itself", project2)
        self.assertIn("HOTSPOT2 x, y [, unID", hotspot2)
        self.assertIn("graphical editing of length and angle parameters", hotspot2.lower())
        self.assertIn("LINE2 x1, y1, x2, y2", primitives2d)
        self.assertIn("CIRCLE2 x, y, r", primitives2d)
        self.assertIn("POLY2 n, status", primitives2d)
        self.assertNotIn("LINE_TO", primitives2d)
        self.assertIn("CYLIND h, r", cylind)
        self.assertNotIn("```gdl\nCYLIND x, y, r1, r2, h", cylind)
        self.assertNotIn("[, segments]", cylind)
        self.assertIn("CUTPLANE [x [, y [, z [, side [, status]]]]]", cutplane)
        self.assertIn("CUTEND", cutplane)
        self.assertIn("not a floor-plan projection setting", cutplane.lower())
        self.assertNotIn("```gdl\nCUTPLANE x, y, z, nx, ny, nz", cutplane)
        self.assertIn("VERT x, y, z", body_edge_pgon)
        self.assertIn("EDGE vert1, vert2, pgon1, pgon2, status", body_edge_pgon)
        self.assertIn("PGON n, vect, status", body_edge_pgon)
        self.assertIn("BODY status", body_edge_pgon)
        self.assertNotIn("BODY 3,", body_edge_pgon)
        self.assertIn("REVOLVE n, alpha, mask", revolve)
        self.assertNotIn("```gdl\nREVOLVE id, angle", revolve)
        self.assertIn("SWEEP n, m, alpha, scale, mask", sweep)
        self.assertNotIn("```gdl\nSWEEP path_id, sections_id", sweep)
        self.assertIn("PROJECT2 projection_code, angle, method", project2)
        self.assertNotIn("PROJECT2 [options]", project2)
        self.assertIn("HOTSPOT2 x, y [, unID", hotspot2)
        self.assertNotIn('HOTSPOT2 x, y, paramName', hotspot2)
        self.assertIn("a >= 0, b >= 0, c >= 0", block)


if __name__ == "__main__":
    unittest.main()
