#!/usr/bin/env python3
"""
gdl-agent v0.4 Test Suite — HSF-native architecture.

Run: python run_tests.py
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

passed = 0
failed = 0
total = 0

def run_test(name, func):
    global passed, failed, total
    total += 1
    try:
        func()
        passed += 1
        print(f"  ✅ {name}")
    except Exception as e:
        failed += 1
        print(f"  ❌ {name}: {e}")


# ══════════════════════════════════════════════════════════
#  HSF Project
# ══════════════════════════════════════════════════════════
print("\n📦 HSF Project")

from gdl_agent.hsf_project import (
    HSFProject, GDLParameter, ScriptType,
    VALID_PARAM_TYPES, PARAM_TYPE_CORRECTIONS,
)

def _test_create_new():
    proj = HSFProject.create_new("TestObj")
    assert proj.name == "TestObj"
    assert len(proj.parameters) == 3
    assert ScriptType.SCRIPT_3D in proj.scripts
    assert proj.guid
run_test("create new project: defaults", _test_create_new)

def _test_add_parameter():
    proj = HSFProject.create_new("T")
    proj.add_parameter(GDLParameter("bTest", "Boolean", "Test param", "1"))
    assert len(proj.parameters) == 4
    p = proj.get_parameter("bTest")
    assert p is not None
    assert p.type_tag == "Boolean"
run_test("add parameter", _test_add_parameter)

def _test_duplicate_parameter():
    proj = HSFProject.create_new("T")
    try:
        proj.add_parameter(GDLParameter("A", "Length", "", "1"))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
run_test("duplicate parameter → error", _test_duplicate_parameter)

def _test_remove_parameter():
    proj = HSFProject.create_new("T")
    proj.add_parameter(GDLParameter("bTest", "Boolean", "", "0"))
    assert proj.remove_parameter("bTest") == True
    assert proj.get_parameter("bTest") is None
    assert proj.remove_parameter("nonexistent") == False
run_test("remove parameter", _test_remove_parameter)

def _test_param_type_correction():
    p = GDLParameter("x", "Float", "", "1.0")
    assert p.type_tag == "RealNum"
    p2 = GDLParameter("y", "Bool", "", "1")
    assert p2.type_tag == "Boolean"
    p3 = GDLParameter("z", "Text", "", "hello")
    assert p3.type_tag == "String"
run_test("type correction: Float→RealNum, Bool→Boolean, Text→String", _test_param_type_correction)

def _test_param_invalid_type():
    try:
        GDLParameter("x", "FakeType", "", "1")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "FakeType" in str(e)
run_test("invalid type → error with message", _test_param_invalid_type)

def _test_affected_scripts_3d():
    proj = HSFProject.create_new("T")
    affected = proj.get_affected_scripts("修改三维几何体")
    assert ScriptType.SCRIPT_3D in affected
    assert ScriptType.MASTER in affected
run_test("affected scripts: '三维' → 3d+master", _test_affected_scripts_3d)

def _test_affected_scripts_2d():
    proj = HSFProject.create_new("T")
    affected = proj.get_affected_scripts("change plan view symbol")
    assert ScriptType.SCRIPT_2D in affected
run_test("affected scripts: 'plan view' → 2d", _test_affected_scripts_2d)

def _test_affected_scripts_ui():
    proj = HSFProject.create_new("T")
    affected = proj.get_affected_scripts("add UI panel")
    assert ScriptType.UI in affected
run_test("affected scripts: 'UI' → ui", _test_affected_scripts_ui)

def _test_affected_scripts_default():
    proj = HSFProject.create_new("T")
    affected = proj.get_affected_scripts("make it better")
    assert ScriptType.SCRIPT_3D in affected
    assert ScriptType.PARAM in affected
run_test("affected scripts: unclear → 3d+param default", _test_affected_scripts_default)

def _test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("RoundTrip", work_dir=tmpdir)
        proj.add_parameter(GDLParameter("bTest", "Boolean", "Test", "1"))
        proj.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
        proj.set_script(ScriptType.SCRIPT_2D, "LINE2 0, 0, A, 0\n")
        proj.save_to_disk()

        root = Path(tmpdir) / "RoundTrip"
        assert (root / "libpartdata.xml").exists()
        assert (root / "paramlist.xml").exists()
        assert (root / "ancestry.xml").exists()
        assert (root / "scripts" / "3d.gdl").exists()
        assert (root / "scripts" / "2d.gdl").exists()

        # Verify BOM
        raw = (root / "scripts" / "3d.gdl").read_bytes()
        assert raw[:3] == b'\xef\xbb\xbf', "Missing UTF-8 BOM"

        # Load back
        loaded = HSFProject.load_from_disk(str(root))
        assert loaded.name == "RoundTrip"
        assert len(loaded.parameters) == 4
        assert ScriptType.SCRIPT_3D in loaded.scripts
        assert "BLOCK" in loaded.scripts[ScriptType.SCRIPT_3D]
run_test("save → load roundtrip with BOM", _test_save_load_roundtrip)

def _test_libpartdata_content():
    proj = HSFProject.create_new("T")
    xml = proj._build_libpartdata()
    assert "MainGUID" in xml
    assert proj.guid in xml
    assert "LibpartData" in xml
    assert 'Version="46"' in xml
    assert "Identification" in xml
    assert "Script_3D" in xml
run_test("libpartdata.xml matches real HSF format", _test_libpartdata_content)

def _test_ancestry_content():
    proj = HSFProject.create_new("T")
    xml = proj._build_ancestry()
    assert "Ancestry" in xml
    assert "MainGUID" in xml
    assert "RootGUID" not in xml  # real format uses MainGUID, not RootGUID
run_test("ancestry.xml matches real HSF format", _test_ancestry_content)

def _test_calledmacros():
    proj = HSFProject.create_new("T")
    xml = proj._build_calledmacros()
    assert "CalledMacros" in xml
run_test("calledmacros.xml generated", _test_calledmacros)

def _test_libpartdocs():
    proj = HSFProject.create_new("T")
    xml = proj._build_libpartdocs()
    assert "libpartdocs" in xml
    assert "Copyright" in xml
run_test("libpartdocs.xml generated", _test_libpartdocs)

def _test_save_has_all_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("AllFiles", work_dir=tmpdir)
        proj.save_to_disk()
        root = Path(tmpdir) / "AllFiles"
        assert (root / "libpartdata.xml").exists()
        assert (root / "paramlist.xml").exists()
        assert (root / "ancestry.xml").exists()
        assert (root / "calledmacros.xml").exists()
        assert (root / "libpartdocs.xml").exists()
        assert (root / "scripts" / "3d.gdl").exists()
run_test("save generates all 5 XML files + scripts", _test_save_has_all_files)

def _test_summary():
    proj = HSFProject.create_new("T")
    s = proj.summary()
    assert "T" in s
    assert "Length" in s
    assert "3d.gdl" in s
run_test("summary output", _test_summary)

def _test_guid_unique():
    p1 = HSFProject.create_new("A")
    p2 = HSFProject.create_new("B")
    assert p1.guid != p2.guid
run_test("GUID uniqueness", _test_guid_unique)


# ══════════════════════════════════════════════════════════
#  Paramlist Builder
# ══════════════════════════════════════════════════════════
print("\n📋 Paramlist Builder")

from gdl_agent.paramlist_builder import (
    build_paramlist_xml, parse_paramlist_xml, validate_paramlist,
)

def _test_build_xml():
    params = [
        GDLParameter("A", "Length", "Width", "1.00", is_fixed=True),
        GDLParameter("bTest", "Boolean", "Test flag", "1"),
    ]
    xml = build_paramlist_xml(params)
    assert "ParamSection" in xml
    assert "ParamSectHeader" in xml
    assert "Parameters" in xml
    assert 'Name="A"' in xml
    assert "<Fix/>" in xml
    assert 'CDATA["Width"]' in xml  # Description wrapped in quotes inside CDATA
    assert 'Name="bTest"' in xml
run_test("build paramlist.xml (real format)", _test_build_xml)

def _test_build_title_separator():
    params = [
        GDLParameter("_geo", "Title", "Geometry", ""),
        GDLParameter("A", "Length", "Width", "1.0", is_fixed=True),
        GDLParameter("_sep", "Separator", "", ""),
    ]
    xml = build_paramlist_xml(params)
    assert "<Title" in xml
    assert "<Separator/>" in xml
run_test("build: Title and Separator tags", _test_build_title_separator)

def _test_parse_xml_roundtrip():
    params = [
        GDLParameter("A", "Length", "Width", "1.00", is_fixed=True),
        GDLParameter("nCount", "Integer", "Count", "5"),
        GDLParameter("sLabel", "String", "Label", "Hello"),
    ]
    xml = build_paramlist_xml(params)
    parsed = parse_paramlist_xml(xml)
    assert len(parsed) == 3
    assert parsed[0].name == "A"
    assert parsed[0].is_fixed == True
    assert parsed[1].name == "nCount"
    assert parsed[1].type_tag == "Integer"
    assert parsed[2].type_tag == "String"
run_test("parse paramlist.xml roundtrip", _test_parse_xml_roundtrip)

def _test_validate_ok():
    params = [
        GDLParameter("A", "Length", "W", "1.00", is_fixed=True),
        GDLParameter("B", "Length", "D", "0.50", is_fixed=True),
        GDLParameter("ZZYZX", "Length", "H", "2.00", is_fixed=True),
        GDLParameter("bTest", "Boolean", "Flag", "1"),
    ]
    assert len(validate_paramlist(params)) == 0
run_test("validate: valid → no issues", _test_validate_ok)

def _test_validate_duplicate():
    params = [
        GDLParameter("A", "Length", "W", "1", is_fixed=True),
        GDLParameter("A", "Length", "W", "2", is_fixed=True),
    ]
    issues = validate_paramlist(params)
    assert any("Duplicate" in i for i in issues)
run_test("validate: duplicate → error", _test_validate_duplicate)

def _test_validate_bool_value():
    params = [GDLParameter("bX", "Boolean", "", "5")]
    issues = validate_paramlist(params)
    assert any("0" in i and "1" in i for i in issues)
run_test("validate: Boolean value != 0/1", _test_validate_bool_value)

def _test_validate_reserved_type():
    params = [GDLParameter("A", "Integer", "W", "1")]
    issues = validate_paramlist(params)
    assert any("Length" in i for i in issues)
run_test("validate: reserved param wrong type", _test_validate_reserved_type)

def _test_validate_reserved_no_fix():
    params = [GDLParameter("A", "Length", "W", "1", is_fixed=False)]
    issues = validate_paramlist(params)
    assert any("Fix" in i for i in issues)
run_test("validate: reserved param missing Fix", _test_validate_reserved_no_fix)


# ══════════════════════════════════════════════════════════
#  Compiler (Mock)
# ══════════════════════════════════════════════════════════
print("\n🔧 Compiler (Mock)")

from gdl_agent.compiler import MockHSFCompiler, CompileResult

def _test_mock_compile_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("CompTest", work_dir=tmpdir)
        proj.save_to_disk()
        compiler = MockHSFCompiler()
        result = compiler.hsf2libpart(str(proj.root), str(Path(tmpdir) / "out.gsm"))
        assert result.success
        assert Path(result.output_path).exists()
run_test("mock compile: valid HSF → success", _test_mock_compile_success)

def _test_mock_compile_missing_dir():
    compiler = MockHSFCompiler()
    result = compiler.hsf2libpart("/nonexistent", "/tmp/out.gsm")
    assert not result.success
run_test("mock compile: missing dir → fail", _test_mock_compile_missing_dir)

def _test_mock_compile_if_endif_mismatch():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("BadIF", work_dir=tmpdir)
        proj.set_script(ScriptType.SCRIPT_3D, "IF bTest THEN\n  BLOCK 1,1,1\n")
        proj.save_to_disk()
        compiler = MockHSFCompiler()
        result = compiler.hsf2libpart(str(proj.root), str(Path(tmpdir) / "out.gsm"))
        assert not result.success
        assert "IF/ENDIF" in result.stderr
run_test("mock compile: IF/ENDIF mismatch → fail", _test_mock_compile_if_endif_mismatch)

def _test_mock_compile_single_line_if():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("GoodIF", work_dir=tmpdir)
        proj.set_script(ScriptType.SCRIPT_3D, "IF bTest THEN BLOCK 1,1,1\n")
        proj.save_to_disk()
        compiler = MockHSFCompiler()
        result = compiler.hsf2libpart(str(proj.root), str(Path(tmpdir) / "out.gsm"))
        assert result.success, f"Should pass: single-line IF. Error: {result.stderr}"
run_test("mock compile: single-line IF THEN → ok", _test_mock_compile_single_line_if)

def _test_mock_compile_for_next_mismatch():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("BadFOR", work_dir=tmpdir)
        proj.set_script(ScriptType.SCRIPT_3D, "FOR i = 1 TO 5\n  BLOCK 1,1,1\n")
        proj.save_to_disk()
        compiler = MockHSFCompiler()
        result = compiler.hsf2libpart(str(proj.root), str(Path(tmpdir) / "out.gsm"))
        assert not result.success
        assert "FOR/NEXT" in result.stderr
run_test("mock compile: FOR/NEXT mismatch → fail", _test_mock_compile_for_next_mismatch)

def _test_compile_result_error_parsing():
    r = CompileResult(success=False, stderr="Error in 3d.gdl at line 5: unknown command\nWarning: deprecated syntax")
    assert len(r.errors) >= 1
    assert len(r.warnings) >= 1
run_test("CompileResult: error/warning parsing", _test_compile_result_error_parsing)


# ══════════════════════════════════════════════════════════
#  GDL Parser → HSFProject
# ══════════════════════════════════════════════════════════
print("\n📄 GDL Parser")

from gdl_agent.gdl_parser import parse_gdl_source, parse_gdl_file

def _test_parser_basic():
    src = """! A  Length  0.80  宽度
! B  Length  0.40  深度
! ZZYZX  Length  1.80  高度
! nShelves  Integer  4  层数

! === 3D SCRIPT ===
BLOCK A, B, ZZYZX
"""
    proj = parse_gdl_source(src, "Test")
    assert proj.name == "Test"
    assert len(proj.parameters) >= 4
    assert ScriptType.SCRIPT_3D in proj.scripts
    assert "BLOCK" in proj.scripts[ScriptType.SCRIPT_3D]
run_test("parse basic GDL source", _test_parser_basic)

def _test_parser_chinese_headers():
    src = """! A  Length  1.00  宽
! === 参数列表 ===
! bTest  Boolean  1  测试

! === 三维脚本 ===
BLOCK A, B, ZZYZX

! === 二维脚本 ===
LINE2 0, 0, A, 0
"""
    proj = parse_gdl_source(src, "CNTest")
    assert ScriptType.SCRIPT_3D in proj.scripts
    assert ScriptType.SCRIPT_2D in proj.scripts
run_test("parse Chinese section headers", _test_parser_chinese_headers)

def _test_parser_ensures_reserved():
    src = """! bOnly  Boolean  1  test
! === 3D SCRIPT ===
BLOCK 1, 1, 1
"""
    proj = parse_gdl_source(src, "NoABZ")
    names = {p.name for p in proj.parameters}
    assert "A" in names
    assert "B" in names
    assert "ZZYZX" in names
run_test("parser ensures A, B, ZZYZX exist", _test_parser_ensures_reserved)

def _test_parser_output_is_hsf():
    src = "BLOCK 1, 1, 1\n"
    proj = parse_gdl_source(src, "Simple")
    assert isinstance(proj, HSFProject)
    # Can save to disk
    with tempfile.TemporaryDirectory() as tmpdir:
        proj.work_dir = Path(tmpdir)
        proj.root = proj.work_dir / proj.name
        hsf_dir = proj.save_to_disk()
        assert (hsf_dir / "libpartdata.xml").exists()
        assert (hsf_dir / "scripts" / "3d.gdl").exists()
run_test("parser output is HSFProject, saveable", _test_parser_output_is_hsf)

def _test_parser_bookshelf():
    path = str(Path(__file__).parent / "examples" / "Bookshelf.gdl")
    if not Path(path).exists():
        return  # skip if example not present
    proj = parse_gdl_file(path)
    assert proj.name == "Bookshelf"
    assert len(proj.parameters) >= 8
    assert ScriptType.SCRIPT_3D in proj.scripts
run_test("parse real Bookshelf.gdl", _test_parser_bookshelf)


# ══════════════════════════════════════════════════════════
#  Agent Core
# ══════════════════════════════════════════════════════════
print("\n🤖 Agent Core")

from gdl_agent.core import GDLAgent, Status

class MockLLM:
    """Mock LLM that returns pre-defined responses."""
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
    def generate(self, messages):
        resp = self._responses[min(self._idx, len(self._responses)-1)]
        self._idx += 1
        return resp

def _test_agent_success():
    llm = MockLLM([
        "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! Width\n"
        "Length B = 0.50  ! Depth\n"
        "Length ZZYZX = 2.00  ! Height\n"
    ])
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("AgentTest", work_dir=tmpdir)
        agent = GDLAgent(llm=llm)
        result = agent.run("Create a box", proj, str(Path(tmpdir) / "out.gsm"))
        assert result.status == Status.SUCCESS
        assert result.attempts == 1
run_test("agent: success on first attempt", _test_agent_success)

def _test_agent_retry_on_error():
    llm = MockLLM([
        # Attempt 1: IF without ENDIF
        "[FILE: scripts/3d.gdl]\nIF bTest THEN\n  BLOCK 1,1,1\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! W\nLength B = 1.00  ! D\nLength ZZYZX = 1.00  ! H\n"
        "Boolean bTest = 1  ! Test\n",
        # Attempt 2: Fixed
        "[FILE: scripts/3d.gdl]\nIF bTest THEN\n  BLOCK 1,1,1\nENDIF\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! W\nLength B = 1.00  ! D\nLength ZZYZX = 1.00  ! H\n"
        "Boolean bTest = 1  ! Test\n",
    ])
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("RetryTest", work_dir=tmpdir)
        agent = GDLAgent(llm=llm)
        result = agent.run("Create test", proj, str(Path(tmpdir) / "out.gsm"))
        assert result.status == Status.SUCCESS
        assert result.attempts == 2
run_test("agent: retry on compile error → success", _test_agent_retry_on_error)

def _test_agent_anti_loop():
    same_response = (
        "[FILE: scripts/3d.gdl]\nIF bTest THEN\n  BLOCK 1,1,1\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! W\nLength B = 1.00  ! D\nLength ZZYZX = 1.00  ! H\n"
    )
    llm = MockLLM([same_response, same_response])
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("LoopTest", work_dir=tmpdir)
        agent = GDLAgent(llm=llm)
        result = agent.run("Loop", proj, str(Path(tmpdir) / "out.gsm"))
        assert result.status == Status.FAILED
        assert "Identical" in result.error_summary
run_test("agent: identical output → anti-loop stop", _test_agent_anti_loop)

def _test_agent_exhausted():
    bad = (
        "[FILE: scripts/3d.gdl]\nIF bX THEN\n  BLOCK 1,1,1\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! W\nLength B = 1.00  ! D\nLength ZZYZX = 1.00  ! H\n"
    )
    # Different enough to avoid anti-loop but always broken
    responses = [bad.replace("bX", f"b{i}") for i in range(5)]
    llm = MockLLM(responses)
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("ExhaustTest", work_dir=tmpdir)
        agent = GDLAgent(llm=llm, max_iterations=3)
        result = agent.run("Break", proj, str(Path(tmpdir) / "out.gsm"))
        assert result.status == Status.EXHAUSTED
        assert result.attempts == 3
run_test("agent: exhausted retries", _test_agent_exhausted)

def _test_agent_events():
    events = []
    llm = MockLLM([
        "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\n\n"
        "[FILE: paramlist.xml]\n"
        "Length A = 1.00  ! W\nLength B = 1.00  ! D\nLength ZZYZX = 1.00  ! H\n"
    ])
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("EvtTest", work_dir=tmpdir)
        agent = GDLAgent(llm=llm, on_event=lambda e, d: events.append(e))
        agent.run("Test", proj, str(Path(tmpdir) / "out.gsm"))
    assert "start" in events
    assert "success" in events
run_test("agent: event callbacks", _test_agent_events)

def _test_agent_unparseable():
    llm = MockLLM(["Just some random text with no file markers" for _ in range(5)])
    with tempfile.TemporaryDirectory() as tmpdir:
        proj = HSFProject.create_new("ParseFail", work_dir=tmpdir)
        agent = GDLAgent(llm=llm, max_iterations=3)
        result = agent.run("Do something", proj, str(Path(tmpdir) / "out.gsm"))
        assert result.status == Status.EXHAUSTED
run_test("agent: unparseable LLM output → exhausted", _test_agent_unparseable)


# ══════════════════════════════════════════════════════════
#  Skills Loader
# ══════════════════════════════════════════════════════════
print("\n🎯 Skills Loader")

from gdl_agent.skills_loader import SkillsLoader

def _test_skills_empty():
    sl = SkillsLoader("/nonexistent")
    sl.load()
    assert sl.skill_count == 0
run_test("empty skills dir", _test_skills_empty)

def _test_skills_detect_create():
    sl = SkillsLoader()
    types = sl.detect_task_type("Create a new door")
    assert "create" in types
run_test("detect: 'Create' → create", _test_skills_detect_create)

def _test_skills_detect_chinese():
    sl = SkillsLoader()
    types = sl.detect_task_type("修改材质参数")
    assert "modify" in types
run_test("detect: '修改' → modify", _test_skills_detect_chinese)

def _test_skills_detect_debug():
    sl = SkillsLoader()
    types = sl.detect_task_type("Fix the compile error")
    assert "debug" in types
run_test("detect: 'Fix error' → debug", _test_skills_detect_debug)


# ══════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  📊 Results: {passed}/{total} passed, {failed} failed")
if failed == 0:
    print(f"  🎉 All tests passed!")
else:
    print(f"  ⚠️  {failed} test(s) failed")
print(f"{'='*50}\n")

sys.exit(0 if failed == 0 else 1)
