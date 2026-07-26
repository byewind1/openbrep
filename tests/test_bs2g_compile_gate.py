"""
BS2G compile gate — every fixture's generated GDL must pass mock compile.

This is a regression gate: any future BS2G change that produces
invalid GDL structure will immediately fail here.
"""

import tempfile
import unittest
from pathlib import Path

from openbrep.compiler import MockHSFCompiler
from openbrep.importers.blender_script.converter import convert_blender_script

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blender"

# Fixtures that must compile cleanly
COMPILE_FIXTURES = ["simple_box.py", "bookshelf.py"]

# Fixtures that only need to not crash (they contain unsupported ops)
NOCAHS_FIXTURES = ["with_unsupported.py"]


class TestBS2GCompileGate(unittest.TestCase):

    def test_all_fixtures_compile(self):
        """Every compile-grade fixture must pass MockHSFCompiler."""
        compiler = MockHSFCompiler()

        for fixture_name in COMPILE_FIXTURES:
            with self.subTest(fixture=fixture_name):
                code = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
                with tempfile.TemporaryDirectory() as tmp:
                    project, ir = convert_blender_script(code, output_dir=tmp)
                    project_dir = str(project.root)
                    output_gsm = str(Path(tmp) / "out.gsm")

                    result = compiler.hsf2libpart(project_dir, output_gsm)
                    self.assertTrue(
                        result.success,
                        f"{fixture_name} generated GDL failed compile:\n{result.stderr}",
                    )

    def test_unsupported_fixture_does_not_crash(self):
        """with_unsupported.py must not crash, but need not compile."""
        code = (FIXTURES_DIR / "with_unsupported.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            # Must have warnings
            self.assertTrue(len(ir.warnings) >= 2)
            # Project must still be saved to disk
            self.assertTrue(project.root.is_dir())

    def test_bookshelf_3d_geometry_correctness(self):
        """Bookshelf shelves must be at spacing×1 .. spacing×4."""
        code = (FIXTURES_DIR / "bookshelf.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            gdl = project.get_script(ScriptType.SCRIPT_3D)

            # Must have 0-based loop
            self.assertIn("FOR i = 0 TO shelf_count - 1", gdl)
            # Expression verbatim — no ±1 rewrite
            self.assertIn("spacing * (i + 1)", gdl)
            # Side panels unrolled — no FOR side
            self.assertNotIn("FOR side", gdl)
            # No tuple parentheses in ADD/MUL
            self.assertNotIn("ADD (", gdl)
            self.assertNotIn("MUL (", gdl)

    def test_simple_box_no_parentheses(self):
        """simple_box ADD/MUL must not have tuple parentheses."""
        code = (FIXTURES_DIR / "simple_box.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            gdl = project.get_script(ScriptType.SCRIPT_3D)
            self.assertNotIn("ADD (", gdl)
            self.assertNotIn("MUL (", gdl)


if __name__ == "__main__":
    unittest.main()
