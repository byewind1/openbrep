"""
Tests for openbrep.importers.blender_script (BS2G).

Covers:
  - AST parser: primitives, transforms, loops, conditions, degradation
  - Parameter type inference
  - GDL generator: stack balance, grouping, paramlist
  - End-to-end converter with fixture scripts
  - StaticChecker integration
"""

import os
import tempfile
import unittest
from pathlib import Path

from openbrep.importers.blender_script.ir import (
    IRAssignment,
    IRCondition,
    IRLoop,
    IRPrimitive,
    IRScript,
    IRTransform,
    IRUnsupported,
)
from openbrep.importers.blender_script.parser import parse_blender_script
from openbrep.importers.blender_script.generator import (
    generate_gdl_3d,
    generate_paramlist,
    generate_fallback_2d,
)
from openbrep.importers.blender_script.converter import convert_blender_script

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blender"


# ── Parser: basic primitives ────────────────────────────────


class TestParserPrimitives(unittest.TestCase):

    def test_parse_simple_cube(self):
        code = '''
def make_box(width=1.0, height=2.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.scale = (width, width, height)
'''
        ir = parse_blender_script(code)
        self.assertEqual(ir.function_name, "make_box")
        self.assertEqual(len(ir.parameters), 2)
        self.assertEqual(ir.parameters[0].name, "width")
        self.assertEqual(ir.parameters[0].gdl_type, "Length")
        self.assertEqual(ir.parameters[1].name, "height")
        self.assertEqual(ir.parameters[1].gdl_type, "Length")

        # body: primitive + assignment (bpy.context) + transform
        primitives = [n for n in ir.body if isinstance(n, IRPrimitive)]
        transforms = [n for n in ir.body if isinstance(n, IRTransform)]
        self.assertEqual(len(primitives), 1)
        self.assertEqual(primitives[0].kind, "cube")
        self.assertEqual(len(transforms), 1)
        self.assertEqual(transforms[0].kind, "scale")

    def test_parse_cylinder(self):
        code = '''
def make_col(radius=0.5, depth=3.0):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth)
'''
        ir = parse_blender_script(code)
        primitives = [n for n in ir.body if isinstance(n, IRPrimitive)]
        self.assertEqual(len(primitives), 1)
        self.assertEqual(primitives[0].kind, "cylinder")
        self.assertIn("radius", primitives[0].args)
        self.assertIn("depth", primitives[0].args)

    def test_parse_sphere(self):
        code = '''
def make_ball(radius=1.0):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius)
'''
        ir = parse_blender_script(code)
        primitives = [n for n in ir.body if isinstance(n, IRPrimitive)]
        self.assertEqual(primitives[0].kind, "sphere")

    def test_parse_cone(self):
        code = '''
def make_cone(radius1=1.0, radius2=0.0, depth=2.0):
    bpy.ops.mesh.primitive_cone_add(radius1=radius1, radius2=radius2, depth=depth)
'''
        ir = parse_blender_script(code)
        primitives = [n for n in ir.body if isinstance(n, IRPrimitive)]
        self.assertEqual(primitives[0].kind, "cone")


# ── Parser: transforms ──────────────────────────────────────


class TestParserTransforms(unittest.TestCase):

    def test_location_transform(self):
        code = '''
def f():
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.location = (1, 2, 3)
'''
        ir = parse_blender_script(code)
        transforms = [n for n in ir.body if isinstance(n, IRTransform)]
        self.assertEqual(len(transforms), 1)
        self.assertEqual(transforms[0].kind, "translate")
        self.assertIn("1", transforms[0].value)

    def test_scale_transform(self):
        code = '''
def f():
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.scale = (2, 3, 4)
'''
        ir = parse_blender_script(code)
        transforms = [n for n in ir.body if isinstance(n, IRTransform)]
        self.assertEqual(transforms[0].kind, "scale")

    def test_rotation_transform(self):
        code = '''
def f():
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.rotation_euler = (0, 0, 1.5708)
'''
        ir = parse_blender_script(code)
        transforms = [n for n in ir.body if isinstance(n, IRTransform)]
        self.assertEqual(transforms[0].kind, "rotate")


# ── Parser: control flow ────────────────────────────────────


class TestParserControlFlow(unittest.TestCase):

    def test_parse_for_loop(self):
        code = '''
def f(n=4):
    for i in range(n):
        bpy.ops.mesh.primitive_cube_add(size=1)
'''
        ir = parse_blender_script(code)
        loops = [n for n in ir.body if isinstance(n, IRLoop)]
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0].var_name, "i")
        self.assertEqual(loops[0].start, "1")
        self.assertEqual(loops[0].end, "n")
        # Loop body contains a primitive
        prims = [n for n in loops[0].body if isinstance(n, IRPrimitive)]
        self.assertEqual(len(prims), 1)

    def test_parse_for_loop_range_two_args(self):
        code = '''
def f():
    for i in range(2, 10):
        bpy.ops.mesh.primitive_cube_add(size=1)
'''
        ir = parse_blender_script(code)
        loops = [n for n in ir.body if isinstance(n, IRLoop)]
        self.assertEqual(loops[0].start, "2")
        self.assertEqual(loops[0].end, "10")

    def test_parse_if_condition(self):
        code = '''
def f(flag=True):
    if flag:
        bpy.ops.mesh.primitive_cube_add(size=1)
    else:
        bpy.ops.mesh.primitive_cylinder_add(radius=1)
'''
        ir = parse_blender_script(code)
        conds = [n for n in ir.body if isinstance(n, IRCondition)]
        self.assertEqual(len(conds), 1)
        self.assertEqual(conds[0].condition, "flag")
        self.assertEqual(len(conds[0].then_body), 1)
        self.assertEqual(len(conds[0].else_body), 1)

    def test_parse_assignment(self):
        code = '''
def f(height=2.0, n=4):
    spacing = height / (n + 1)
    bpy.ops.mesh.primitive_cube_add(size=1)
'''
        ir = parse_blender_script(code)
        assigns = [n for n in ir.body if isinstance(n, IRAssignment)]
        self.assertEqual(len(assigns), 1)
        self.assertEqual(assigns[0].name, "spacing")
        self.assertIn("height", assigns[0].value)
        # Also in local_vars
        self.assertIn("spacing", ir.local_vars)


# ── Parser: degradation ─────────────────────────────────────


class TestParserDegradation(unittest.TestCase):

    def test_unsupported_modifier_degrades(self):
        code = '''
def f(size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    bpy.ops.object.modifier_add(type='SUBSURF')
'''
        ir = parse_blender_script(code)
        self.assertEqual(len(ir.warnings), 1)
        self.assertIn("modifier_add", ir.warnings[0].operation)
        # Should not crash, primitive still parsed
        prims = [n for n in ir.body if isinstance(n, IRPrimitive)]
        self.assertEqual(len(prims), 1)

    def test_unsupported_in_body(self):
        code = '''
def f():
    bpy.ops.object.modifier_add(type='BOOLEAN')
'''
        ir = parse_blender_script(code)
        unsupported = [n for n in ir.body if isinstance(n, IRUnsupported)]
        self.assertEqual(len(unsupported), 1)

    def test_syntax_error_degrades(self):
        code = "def f(:\n    pass"
        ir = parse_blender_script(code)
        self.assertEqual(ir.function_name, "<syntax_error>")
        self.assertTrue(len(ir.warnings) > 0)

    def test_no_function_degrades(self):
        code = "x = 1\ny = 2\n"
        ir = parse_blender_script(code)
        self.assertTrue(len(ir.warnings) > 0)
        self.assertIn("find_function", ir.warnings[0].operation)

    def test_target_function_not_found(self):
        code = "def foo():\n    pass\n"
        ir = parse_blender_script(code, target_function="bar")
        self.assertTrue(len(ir.warnings) > 0)

    def test_multiple_unsupported_operations(self):
        code = '''
def f(size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    bpy.ops.object.modifier_add(type='SUBSURF')
    bpy.ops.object.modifier_add(type='BOOLEAN')
'''
        ir = parse_blender_script(code)
        self.assertEqual(len(ir.warnings), 2)


# ── Parameter type inference ────────────────────────────────


class TestParamInference(unittest.TestCase):

    def test_length_inference(self):
        code = "def f(width=1.0, height=2.0, thickness=0.018):\n    pass\n"
        ir = parse_blender_script(code)
        types = {p.name: p.gdl_type for p in ir.parameters}
        self.assertEqual(types["width"], "Length")
        self.assertEqual(types["height"], "Length")
        self.assertEqual(types["thickness"], "Length")

    def test_angle_inference(self):
        code = "def f(angle=45.0, rotation=0.0):\n    pass\n"
        ir = parse_blender_script(code)
        types = {p.name: p.gdl_type for p in ir.parameters}
        self.assertEqual(types["angle"], "Angle")
        self.assertEqual(types["rotation"], "Angle")

    def test_integer_count_inference(self):
        code = "def f(shelf_count=4, segments=32):\n    pass\n"
        ir = parse_blender_script(code)
        types = {p.name: p.gdl_type for p in ir.parameters}
        self.assertEqual(types["shelf_count"], "Integer")
        self.assertEqual(types["segments"], "Integer")

    def test_boolean_inference(self):
        code = "def f(has_door=True, show_handles=False):\n    pass\n"
        ir = parse_blender_script(code)
        types = {p.name: p.gdl_type for p in ir.parameters}
        self.assertEqual(types["has_door"], "Boolean")
        self.assertEqual(types["show_handles"], "Boolean")

    def test_boolean_by_default_value(self):
        code = "def f(flag=True):\n    pass\n"
        ir = parse_blender_script(code)
        self.assertEqual(ir.parameters[0].gdl_type, "Boolean")

    def test_realnum_fallback(self):
        code = "def f(ratio=0.5):\n    pass\n"
        ir = parse_blender_script(code)
        self.assertEqual(ir.parameters[0].gdl_type, "RealNum")

    def test_integer_fallback(self):
        code = "def f(level=3):\n    pass\n"
        ir = parse_blender_script(code)
        self.assertEqual(ir.parameters[0].gdl_type, "Integer")


# ── Generator: GDL output ───────────────────────────────────


class TestGenerator(unittest.TestCase):

    def test_simple_cube_gdl(self):
        code = '''
def make_box(width=1.0, depth=0.5, height=2.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.scale = (width, depth, height)
    obj.location = (0, 0, height / 2)
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)

        self.assertIn("BLOCK", gdl)
        self.assertIn("MUL", gdl)
        self.assertIn("ADD", gdl)
        self.assertIn("DEL", gdl)
        self.assertIn("END", gdl)

    def test_stack_balance_simple(self):
        """Generated GDL must have balanced ADD/DEL."""
        code = '''
def f(w=1.0, h=2.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.scale = (w, w, h)
    obj.location = (0, 0, h / 2)
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)
        self.assertTrue(_is_stack_balanced(gdl), f"Stack imbalanced:\n{gdl}")

    def test_stack_balance_loop(self):
        """Loop body transforms must be balanced within each iteration."""
        code = '''
def f(n=4, w=1.0):
    for i in range(n):
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = bpy.context.object
        obj.scale = (w, w, w)
        obj.location = (0, 0, i * 0.5)
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)
        self.assertTrue(_is_stack_balanced(gdl), f"Stack imbalanced:\n{gdl}")
        self.assertIn("FOR", gdl)
        self.assertIn("NEXT", gdl)

    def test_unsupported_becomes_comment(self):
        code = '''
def f(size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    bpy.ops.object.modifier_add(type='SUBSURF')
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)
        self.assertIn("[BS2G-UNSUPPORTED]", gdl)
        self.assertIn("modifier_add", gdl)

    def test_condition_emission(self):
        code = '''
def f(flag=True):
    if flag:
        bpy.ops.mesh.primitive_cube_add(size=1)
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)
        self.assertIn("IF", gdl)
        self.assertIn("THEN", gdl)
        self.assertIn("ENDIF", gdl)

    def test_local_vars_in_output(self):
        code = '''
def f(height=2.0, n=4):
    spacing = height / (n + 1)
    bpy.ops.mesh.primitive_cube_add(size=1)
'''
        ir = parse_blender_script(code)
        gdl = generate_gdl_3d(ir)
        self.assertIn("spacing =", gdl)

    def test_fallback_2d(self):
        gdl_2d = generate_fallback_2d()
        self.assertIn("HOTSPOT2", gdl_2d)
        self.assertIn("PROJECT2", gdl_2d)


# ── Generator: paramlist ────────────────────────────────────


class TestParamlistGeneration(unittest.TestCase):

    def test_paramlist_includes_reserved(self):
        code = "def f(width=1.0):\n    bpy.ops.mesh.primitive_cube_add(size=1)\n"
        ir = parse_blender_script(code)
        params = generate_paramlist(ir)
        names = [p.name for p in params]
        self.assertIn("A", names)
        self.assertIn("B", names)
        self.assertIn("ZZYZX", names)
        self.assertIn("width", names)

    def test_paramlist_types(self):
        code = "def f(width=1.0, count=4, flag=True):\n    pass\n"
        ir = parse_blender_script(code)
        params = generate_paramlist(ir)
        by_name = {p.name: p for p in params}
        self.assertEqual(by_name["width"].type_tag, "Length")
        self.assertEqual(by_name["count"].type_tag, "Integer")
        self.assertEqual(by_name["flag"].type_tag, "Boolean")

    def test_boolean_value_format(self):
        code = "def f(flag=True):\n    pass\n"
        ir = parse_blender_script(code)
        params = generate_paramlist(ir)
        flag = [p for p in params if p.name == "flag"][0]
        self.assertEqual(flag.value, "1")


# ── End-to-end: fixture scripts ─────────────────────────────


class TestEndToEnd(unittest.TestCase):

    def _load_fixture(self, name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    def test_simple_box_conversion(self):
        code = self._load_fixture("simple_box.py")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)

            self.assertEqual(project.name, "make_box")
            self.assertEqual(len(ir.warnings), 0)

            # Check scripts exist
            gdl_3d = project.get_script(
                __import__("openbrep.hsf_project", fromlist=["ScriptType"]).ScriptType.SCRIPT_3D
            )
            self.assertIn("BLOCK", gdl_3d)
            self.assertIn("END", gdl_3d)

            # Check files on disk
            self.assertTrue((Path(tmp) / "make_box" / "paramlist.xml").exists())
            self.assertTrue((Path(tmp) / "make_box" / "scripts" / "3d.gdl").exists())

    def test_bookshelf_conversion(self):
        code = self._load_fixture("bookshelf.py")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)

            self.assertEqual(project.name, "make_bookshelf")
            self.assertEqual(len(ir.warnings), 0)

            # Parameters should include shelf_count as Integer
            param_names = {p.name for p in project.parameters}
            self.assertIn("shelf_count", param_names)
            self.assertIn("width", param_names)
            self.assertIn("panel_thickness", param_names)

            by_name = {p.name: p for p in project.parameters}
            self.assertEqual(by_name["shelf_count"].type_tag, "Integer")
            self.assertEqual(by_name["width"].type_tag, "Length")

            # 3D should have FOR/NEXT
            from openbrep.hsf_project import ScriptType
            gdl_3d = project.get_script(ScriptType.SCRIPT_3D)
            self.assertIn("FOR", gdl_3d)
            self.assertIn("NEXT", gdl_3d)

    def test_with_unsupported_conversion(self):
        code = self._load_fixture("with_unsupported.py")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)

            # Should not crash, should have warnings
            self.assertTrue(len(ir.warnings) >= 2)

            # 3D should contain unsupported comments
            from openbrep.hsf_project import ScriptType
            gdl_3d = project.get_script(ScriptType.SCRIPT_3D)
            self.assertIn("[BS2G-UNSUPPORTED]", gdl_3d)

    def test_static_checker_integration(self):
        """Generated GDL must pass StaticChecker stack balance."""
        from openbrep.static_checker import StaticChecker
        from openbrep.hsf_project import ScriptType

        code = self._load_fixture("simple_box.py")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            result = StaticChecker().check(project)

            stack_errors = [
                e for e in result.errors
                if e.check_type == "stack_imbalance"
            ]
            self.assertEqual(
                stack_errors, [],
                f"Stack imbalance in generated GDL:\n"
                f"{project.get_script(ScriptType.SCRIPT_3D)}"
            )

    def test_static_checker_bookshelf(self):
        """Bookshelf GDL must also pass stack balance check."""
        from openbrep.static_checker import StaticChecker
        from openbrep.hsf_project import ScriptType

        code = self._load_fixture("bookshelf.py")
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            result = StaticChecker().check(project)

            stack_errors = [
                e for e in result.errors
                if e.check_type == "stack_imbalance"
            ]
            self.assertEqual(
                stack_errors, [],
                f"Stack imbalance in generated GDL:\n"
                f"{project.get_script(ScriptType.SCRIPT_3D)}"
            )


# ── Helpers ─────────────────────────────────────────────────


def _is_stack_balanced(gdl: str) -> bool:
    """Quick check: count ADD/MUL/ROT pushes vs DEL pops."""
    import re
    push_re = re.compile(r"\b(ADD(?:[XYZ])?|ADD2|MUL2?|ROT[XYZ]?|ROT2)\b", re.IGNORECASE)
    pop_re = re.compile(r"\bDEL\s*(\d+)?\b", re.IGNORECASE)

    # Skip comment lines
    code_lines = []
    for line in gdl.splitlines():
        stripped = line.strip()
        if stripped.startswith("!"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)

    pushes = len(push_re.findall(code))
    pops = sum(
        int(m.group(1)) if m.group(1) else 1
        for m in pop_re.finditer(code)
    )
    return pushes == pops


# ── LLM completion layer ────────────────────────────────────


class TestLLMCompletion(unittest.TestCase):

    def test_parse_llm_scripts(self):
        from openbrep.importers.blender_script.llm_completion import _parse_llm_scripts

        content = """--- 2d.gdl ---
HOTSPOT2 0, 0
PROJECT2 3, 270, 2
--- 1d.gdl ---
x = 1
"""
        scripts = _parse_llm_scripts(content)
        self.assertIn("scripts/2d.gdl", scripts)
        self.assertIn("HOTSPOT2", scripts["scripts/2d.gdl"])
        self.assertIn("scripts/1d.gdl", scripts)

    def test_parse_llm_scripts_fallback_2d(self):
        """If LLM response has no 2d marker, fallback is used."""
        from openbrep.importers.blender_script.llm_completion import _parse_llm_scripts

        content = "Some random text without markers"
        scripts = _parse_llm_scripts(content)
        self.assertIn("scripts/2d.gdl", scripts)
        self.assertIn("PROJECT2", scripts["scripts/2d.gdl"])

    def test_format_warnings(self):
        from openbrep.importers.blender_script.llm_completion import _format_warnings

        warnings = [
            IRUnsupported("modifier_add(SUBSURF)", "Cannot map", 5, "bpy.ops..."),
        ]
        text = _format_warnings(warnings)
        self.assertIn("modifier_add", text)
        self.assertIn("Line 5", text)

    def test_format_warnings_empty(self):
        from openbrep.importers.blender_script.llm_completion import _format_warnings
        self.assertEqual(_format_warnings([]), "")

    def test_complete_with_llm_fallback_on_error(self):
        """LLM failure should fall back to minimal 2D."""
        from openbrep.importers.blender_script.llm_completion import complete_with_llm

        ir = IRScript(function_name="test")
        gdl_3d = "BLOCK 1, 1, 1\nEND\n"

        class FailingLLM:
            def generate(self, messages, **kw):
                raise RuntimeError("API error")

        scripts = complete_with_llm(ir, gdl_3d, "<xml/>", FailingLLM())
        self.assertIn("scripts/2d.gdl", scripts)
        self.assertIn("PROJECT2", scripts["scripts/2d.gdl"])


# ── Workbench API integration ───────────────────────────────


class TestWorkbenchBlenderImport(unittest.TestCase):

    def test_import_blender_route_exists(self):
        """The /api/project/import-blender route should be registered."""
        from openbrep.workbench_api import WorkbenchSession

        session = WorkbenchSession(config_path="nonexistent.toml")
        result = session.route(
            "POST",
            "/api/project/import-blender",
            {"script_content": "def f(w=1.0):\n    bpy.ops.mesh.primitive_cube_add(size=1)\n"},
        )
        self.assertTrue(result.get("ok"), f"Import failed: {result}")
        self.assertIn("snapshot", result)
        self.assertIn("warnings", result)
        self.assertIn("parameters", result)

    def test_import_blender_empty_content(self):
        from openbrep.workbench_api import WorkbenchSession

        session = WorkbenchSession(config_path="nonexistent.toml")
        result = session.route(
            "POST",
            "/api/project/import-blender",
            {"script_content": ""},
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_import_blender_with_warnings(self):
        from openbrep.workbench_api import WorkbenchSession

        code = "def f(size=1.0):\n    bpy.ops.mesh.primitive_cube_add(size=size)\n    bpy.ops.object.modifier_add(type='SUBSURF')\n"
        session = WorkbenchSession(config_path="nonexistent.toml")
        result = session.route(
            "POST",
            "/api/project/import-blender",
            {"script_content": code},
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(len(result["warnings"]) >= 1)


# ── Converter edge cases ────────────────────────────────────


class TestConverterEdgeCases(unittest.TestCase):

    def test_convert_with_explicit_name(self):
        code = "def f(w=1.0):\n    bpy.ops.mesh.primitive_cube_add(size=1)\n"
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp, object_name="my_box")
            self.assertEqual(project.name, "my_box")

    def test_convert_syntax_error_script(self):
        code = "def f(:\n    pass"
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            # Should not crash
            self.assertTrue(len(ir.warnings) > 0)

    def test_convert_no_function_script(self):
        code = "x = 1\ny = 2\n"
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            self.assertTrue(len(ir.warnings) > 0)

    def test_master_script_generated_for_locals(self):
        code = '''
def f(height=2.0, n=4):
    spacing = height / (n + 1)
    bpy.ops.mesh.primitive_cube_add(size=1)
'''
        with tempfile.TemporaryDirectory() as tmp:
            project, ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            master = project.get_script(ScriptType.MASTER)
            self.assertIn("spacing", master)


if __name__ == "__main__":
    unittest.main()
