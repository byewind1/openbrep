"""
BS2G GDL purity gate.

The generated 3d.gdl must be pure GDL:
  - no Python API calls leak through (bpy / bmesh / untranslated math)
  - no multi-level dotted attribute access (Python object access)
  - the result must pass StaticChecker's undefined_var check
"""

import re
import tempfile
from pathlib import Path

from openbrep.hsf_project import ScriptType
from openbrep.importers.blender_script.converter import convert_blender_script
from openbrep.static_checker import StaticChecker

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blender"


def generate_3d_for_fixture(fixture_name: str) -> str:
    """Convert a fixture script and return its generated 3d.gdl text."""
    project = convert_fixture_to_project(fixture_name)
    return project.get_script(ScriptType.SCRIPT_3D)


def convert_fixture_to_project(fixture_name: str):
    """Convert a fixture script into a saved HSF project (temp dir)."""
    code = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
    project, _ir = convert_blender_script(code, output_dir=tempfile.mkdtemp())
    return project


def test_no_python_leakage_in_generated_gdl():
    """生成的 GDL 不能包含 Python API 调用"""
    for fixture in ["simple_box.py", "bookshelf.py", "with_unsupported.py", "rotated_box.py", "loft_mini.py"]:
        gdl = generate_3d_for_fixture(fixture)
        assert "bpy." not in gdl, f"{fixture} 泄漏 bpy"
        assert "bmesh." not in gdl, f"{fixture} 泄漏 bmesh"
        assert "math." not in gdl, f"{fixture} 泄漏未转换的 math"
        # 多级点号属性访问（Python 对象访问，GDL 不支持）
        assert not re.search(r'\b\w+\.\w+\.\w+', gdl), f"{fixture} 有多级属性访问"


def test_generated_gdl_passes_static_checker():
    """生成的 GDL 必须通过 StaticChecker 的 undefined_var 检查"""
    for fixture in ["simple_box.py", "bookshelf.py", "rotated_box.py", "loft_mini.py"]:
        project = convert_fixture_to_project(fixture)
        result = StaticChecker().check(project)
        undefined = [e for e in result.errors if e.check_type == "undefined_var"]
        assert not undefined, f"{fixture} 未定义变量: {[e.detail for e in undefined]}"
