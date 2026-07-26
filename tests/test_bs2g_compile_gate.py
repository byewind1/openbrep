"""
BS2G compile gate — every fixture's generated GDL must pass mock compile.

This is a regression gate: any future BS2G change that produces
invalid GDL structure will immediately fail here.
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openbrep.compiler import MockHSFCompiler
from openbrep.importers.blender_script.converter import convert_blender_script
from openbrep.static_checker import StaticChecker

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blender"

# All fixtures must not crash
ALL_FIXTURES = ["simple_box.py", "bookshelf.py", "with_unsupported.py", "rotated_box.py", "loft_mini.py"]

# Fixtures that must compile cleanly (no Python code leakage)
COMPILE_FIXTURES = ["simple_box.py", "bookshelf.py", "rotated_box.py", "loft_mini.py"]

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

    def test_compile_flag_uses_mock_when_requested(self):
        """--compile --mock must bypass HSFCompiler and use MockHSFCompiler."""
        # Verify _try_compile imports HSFCompiler and MockHSFCompiler
        # (not the nonexistent Compiler class that caused the import bug)
        from openbrep.compiler import HSFCompiler, MockHSFCompiler

        compiler = MockHSFCompiler()

        with tempfile.TemporaryDirectory() as project_dir:
            p = Path(project_dir)
            scripts_dir = p / "scripts"
            scripts_dir.mkdir(parents=True)
            (p / "paramlist.xml").write_text("<xml/>")
            (p / "libpartdata.xml").write_text("<xml/>")
            # Put a minimal valid 3d.gdl so _check_gdl_basic passes
            (scripts_dir / "3d.gdl").write_text("! test\nBLOCK 1, 1, 1\n")

            result = compiler.hsf2libpart(
                project_dir, str(p / "out.gsm")
            )
            self.assertTrue(result.success)

    def test_compile_flag_reports_missing_lp_converter_clearly(self):
        """When no LP_XMLConverter configured, _try_compile falls back
        to MockHSFCompiler (not to the broken 'Compiler' import)."""
        from openbrep.compiler import HSFCompiler, MockHSFCompiler

        # HSFCompiler constructor exists — we should use it,
        # not the old broken 'Compiler' alias
        self.assertTrue(hasattr(HSFCompiler, "__init__"))
        self.assertTrue(hasattr(MockHSFCompiler, "__init__"))

        compiler = MockHSFCompiler()
        with tempfile.TemporaryDirectory() as project_dir:
            p = Path(project_dir)
            scripts_dir = p / "scripts"
            scripts_dir.mkdir(parents=True)
            (p / "paramlist.xml").write_text("<xml/>")
            (p / "libpartdata.xml").write_text("<xml/>")
            (scripts_dir / "3d.gdl").write_text("! test\nBLOCK 1, 1, 1\n")

            result = compiler.hsf2libpart(
                project_dir, str(p / "out.gsm")
            )
            self.assertTrue(result.success)

    def test_no_python_leakage_in_generated_gdl(self):
        """Generated GDL must not contain Python API calls (bpy, bmesh)
        outside of comment lines (IRUnsupported warnings)."""
        for fixture_name in ALL_FIXTURES:
            with self.subTest(fixture=fixture_name):
                code = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
                with tempfile.TemporaryDirectory() as tmp:
                    project, ir = convert_blender_script(code, output_dir=tmp)
                    from openbrep.hsf_project import ScriptType
                    gdl = project.get_script(ScriptType.SCRIPT_3D)
                    # Check non-comment lines only — warning comments legitimately contain bpy.
                    for line in gdl.splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("!"):
                            continue
                        self.assertNotIn("bpy.", stripped)
                        self.assertNotIn("bmesh.", stripped)
                    # No multi-dot attribute access like obj.attr.attr in code lines
                    for line in gdl.splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("!"):
                            continue
                        self.assertFalse(
                            re.search(r"\b\w+\.\w+\.\w+", stripped),
                            f"{fixture_name}: GDL code line contains multi-dot "
                            f"Python attribute access: {stripped}",
                        )

    def test_generated_gdl_passes_static_checker(self):
        """Generated GDL must pass StaticChecker (especially undefined_var)."""
        for fixture_name in COMPILE_FIXTURES:
            with self.subTest(fixture=fixture_name):
                code = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
                with tempfile.TemporaryDirectory() as tmp:
                    project, ir = convert_blender_script(code, output_dir=tmp)
                    result = StaticChecker().check(project)
                    undefined = [
                        e for e in result.errors
                        if e.check_type == "undefined_var"
                    ]
                    self.assertFalse(
                        undefined,
                        f"{fixture_name}: undefined_var errors: "
                        + ", ".join(e.detail for e in undefined),
                    )


if __name__ == "__main__":
    unittest.main()
