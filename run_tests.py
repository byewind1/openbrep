#!/usr/bin/env python3
"""Standalone test runner for gdl-agent. Works without pytest."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gdl_agent.config import GDLAgentConfig, LLMConfig, AgentConfig, CompilerConfig
from gdl_agent.compiler import MockCompiler
from gdl_agent.core import GDLAgent, Status
from gdl_agent.knowledge import KnowledgeBase
from gdl_agent.llm import MockLLM, Message
from gdl_agent.snippets import SnippetLibrary, BUILTIN_SNIPPETS
from gdl_agent.dependencies import DependencyResolver
from gdl_agent.sandbox import Sandbox
from gdl_agent.context import slice_context, detect_relevant_sections
from gdl_agent.preflight import PreflightAnalyzer
from gdl_agent.gdl_parser import parse_gdl_source, parse_gdl_file, ParsedGDL
from gdl_agent.xml_utils import (
    validate_xml, validate_gdl_structure, compute_diff,
    contents_identical, inject_debug_anchors
)

VALID_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<Symbol>\n  <Parameters>\n    <Parameter><n>bTest</n><Type>Boolean</Type><Value>0</Value></Parameter>\n  </Parameters>\n  <Script_3D><![CDATA[\nPRISM_ 4, 1.0, 0,0, 1,0, 1,1, 0,1\n  ]]></Script_3D>\n</Symbol>'

INVALID_IF = '<?xml version="1.0" encoding="UTF-8"?>\n<Symbol>\n  <Script_3D><![CDATA[\nIF bTest THEN\n  PRISM_ 4, 1.0, 0,0, 1,0, 1,1, 0,1\n  ]]></Script_3D>\n</Symbol>'

MALFORMED = "<Symbol><Unclosed>"

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

def ws():
    tmp = Path(tempfile.mkdtemp())
    for d in ["src", "output", "knowledge"]:
        (tmp / d).mkdir()
    return tmp

def cfg(tmp):
    return GDLAgentConfig(
        llm=LLMConfig(model="mock"),
        agent=AgentConfig(max_iterations=5),
        compiler=CompilerConfig(path="/mock"),
        knowledge_dir=str(tmp / "knowledge"),
        src_dir=str(tmp / "src"),
        output_dir=str(tmp / "output"),
    )

# ── XML Utils ─────────────────────────────────────────────
print("\n📦 XML Utils")

def _test_valid_xml():
    r = validate_xml(VALID_XML)
    assert r.valid, f"Expected valid: {r.error}"
run_test("valid XML passes", _test_valid_xml)

def _test_malformed():
    r = validate_xml(MALFORMED)
    assert not r.valid
run_test("malformed XML fails", _test_malformed)

def _test_empty():
    r = validate_xml("")
    assert not r.valid
run_test("empty string fails", _test_empty)

def _test_gdl_valid():
    issues = validate_gdl_structure(VALID_XML)
    assert len(issues) == 0, f"Unexpected: {issues}"
run_test("GDL structure: valid", _test_gdl_valid)

def _test_gdl_if():
    issues = validate_gdl_structure(INVALID_IF)
    assert any("IF/ENDIF" in i for i in issues), f"Expected IF/ENDIF: {issues}"
run_test("GDL structure: IF/ENDIF mismatch", _test_gdl_if)

def _test_diff_identical():
    assert compute_diff(VALID_XML, VALID_XML) == ""
run_test("diff: identical = empty", _test_diff_identical)

def _test_diff_different():
    d = compute_diff(VALID_XML, VALID_XML.replace("bTest", "bMod"))
    assert "bTest" in d and "bMod" in d
run_test("diff: different = non-empty", _test_diff_different)

def _test_contents_identical():
    assert contents_identical("  <a>  b </a>  ", "<a> b </a>")
    assert not contents_identical("<a>x</a>", "<a>y</a>")
run_test("contents_identical", _test_contents_identical)

# ── Knowledge ─────────────────────────────────────────────
print("\n📚 Knowledge")

def _test_kb_empty():
    tmp = ws()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    kb.load()
    assert kb.doc_count == 0
run_test("empty knowledge dir", _test_kb_empty)

def _test_kb_load():
    tmp = ws()
    (tmp / "knowledge" / "test.md").write_text("# Test\nHello")
    kb = KnowledgeBase(str(tmp / "knowledge"))
    kb.load()
    assert kb.doc_count == 1
    assert "Hello" in kb.get_all()
run_test("load docs", _test_kb_load)

def _test_kb_relevance():
    tmp = ws()
    (tmp / "knowledge" / "GDL_Reference.md").write_text("PRISM_ syntax")
    (tmp / "knowledge" / "Common_Errors.md").write_text("Error handling")
    kb = KnowledgeBase(str(tmp / "knowledge"))
    kb.load()
    r = kb.get_relevant("PRISM_ command syntax")
    assert "PRISM_" in r
run_test("relevance scoring", _test_kb_relevance)

# ── Config ────────────────────────────────────────────────
print("\n⚙️  Config")

def _test_config_defaults():
    c = GDLAgentConfig()
    assert c.llm.model == "glm-4-flash"
    assert c.agent.max_iterations == 5
    assert c.llm.temperature == 0.2
run_test("default values", _test_config_defaults)

def _test_config_toml():
    c = GDLAgentConfig()
    s = c.to_toml_string()
    assert "glm-4-flash" in s
    assert "max_iterations" in s
run_test("to_toml_string", _test_config_toml)

# ── Mock LLM ─────────────────────────────────────────────
print("\n🧠 Mock LLM")

def _test_mock_llm():
    llm = MockLLM(responses=["resp1", "resp2"])
    r1 = llm.generate([Message("user", "hi")])
    assert r1.content == "resp1"
    r2 = llm.generate([Message("user", "hi")])
    assert r2.content == "resp2"
    assert llm.call_count == 2
run_test("mock LLM returns responses in order", _test_mock_llm)

# ── Agent Core ────────────────────────────────────────────
print("\n🤖 Agent Core")

def _test_agent_success_first():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Create test object", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.SUCCESS, f"Expected SUCCESS, got {result.status}"
    assert result.attempts == 1
    assert Path(result.output_path).exists()
run_test("success on first try", _test_agent_success_first)

def _test_agent_success_retry():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[INVALID_IF, VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.SUCCESS, f"Expected SUCCESS, got {result.status}: {result.error_summary}"
    assert result.attempts == 2
    assert len(result.history) >= 2
    assert not result.history[0].success
    assert result.history[-1].success
run_test("success after retry (IF/ENDIF fix)", _test_agent_success_retry)

def _test_agent_malformed_retry():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[MALFORMED, VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.SUCCESS, f"Expected SUCCESS, got {result.status}: {result.error_summary}"
    assert result.attempts == 2
run_test("malformed XML → retry → success", _test_agent_malformed_retry)

def _test_agent_identical_stops():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[INVALID_IF, INVALID_IF])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.FAILED
    assert "identical" in result.error_summary.lower() or "Identical" in result.error_summary
run_test("identical retry → stops (anti-loop)", _test_agent_identical_stops)

def _test_agent_exhausted():
    tmp = ws()
    c = cfg(tmp)
    c.agent.max_iterations = 3
    responses = [INVALID_IF.replace("bTest", f"b{i}") for i in range(5)]
    llm = MockLLM(responses=responses)
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.EXHAUSTED
    assert result.attempts == 3
run_test("exhausted retries", _test_agent_exhausted)

def _test_agent_code_fence():
    tmp = ws()
    c = cfg(tmp)
    fenced = f"Here's the code:\n\n```xml\n{VALID_XML}\n```\n\nDone!"
    llm = MockLLM(responses=[fenced])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.SUCCESS
run_test("extract XML from code fence", _test_agent_code_fence)

def _test_agent_events():
    tmp = ws()
    c = cfg(tmp)
    events = []
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, on_event=lambda e, **kw: events.append(e))
    agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert "start" in events
    assert "llm_call" in events
    assert "compile_success" in events
run_test("event callbacks emitted", _test_agent_events)

# ── v0.2: Snippet Library Tests ───────────────────────────
print("\n📎 Snippets (v0.2)")

def _test_snippets_builtin_count():
    lib = SnippetLibrary()
    assert lib.count >= 10, f"Expected ≥10 builtin snippets, got {lib.count}"
run_test("builtin snippets loaded", _test_snippets_builtin_count)

def _test_snippets_match_loop():
    lib = SnippetLibrary()
    matches = lib.match("I need a loop to repeat geometry")
    assert len(matches) > 0
    ids = [s.id for s in matches]
    assert "for_loop" in ids, f"Expected for_loop in {ids}"
run_test("match: 'loop' → for_loop", _test_snippets_match_loop)

def _test_snippets_match_call():
    lib = SnippetLibrary()
    matches = lib.match("call a macro named Frame")
    ids = [s.id for s in matches]
    assert "call_macro" in ids, f"Expected call_macro in {ids}"
run_test("match: 'call macro' → call_macro", _test_snippets_match_call)

def _test_snippets_match_chinese():
    lib = SnippetLibrary()
    matches = lib.match("给窗户加一个循环阵列")
    assert len(matches) > 0, "Should match Chinese triggers"
run_test("match: Chinese '循环阵列'", _test_snippets_match_chinese)

def _test_snippets_match_hotspot():
    lib = SnippetLibrary()
    matches = lib.match("add a hotspot for editing")
    ids = [s.id for s in matches]
    assert "hotspot2d" in ids
run_test("match: 'hotspot' → hotspot2d", _test_snippets_match_hotspot)

def _test_snippets_match_values():
    lib = SnippetLibrary()
    matches = lib.match("constrain parameter range")
    ids = [s.id for s in matches]
    assert "values_constraint" in ids
run_test("match: 'constrain range' → values_constraint", _test_snippets_match_values)

def _test_snippets_format():
    lib = SnippetLibrary()
    matches = lib.match("FOR loop")
    text = lib.format_for_prompt(matches)
    assert "Verified GDL Patterns" in text
    assert "FOR" in text
    assert "NEXT" in text
run_test("format_for_prompt output", _test_snippets_format)

def _test_snippets_no_match():
    lib = SnippetLibrary()
    matches = lib.match("unrelated question about weather")
    assert len(matches) == 0
run_test("no match for irrelevant query", _test_snippets_no_match)

def _test_snippets_user_file():
    tmp = ws()
    snippets_path = tmp / "snippets.json"
    snippets_path.write_text('[{"id":"custom","name":"Custom","triggers":["myword"],"code":"CUSTOM CODE","context":"test"}]')
    lib = SnippetLibrary(str(snippets_path))
    matches = lib.match("use myword here")
    assert len(matches) == 1
    assert matches[0].id == "custom"
run_test("load user snippets.json", _test_snippets_user_file)

# ── v0.2: Dependency Resolver Tests ───────────────────────
print("\n🔗 Dependencies (v0.2)")

def _test_deps_extract_call():
    resolver = DependencyResolver()
    xml = '''<Symbol><Script_3D><![CDATA[
CALL "Macro_Frame"
CALL "Macro_Panel"
    ]]></Script_3D></Symbol>'''
    names = resolver.extract_call_names(xml)
    assert "Macro_Frame" in names
    assert "Macro_Panel" in names
run_test("extract CALL names from XML", _test_deps_extract_call)

def _test_deps_no_calls():
    resolver = DependencyResolver()
    names = resolver.extract_call_names(VALID_XML)
    assert len(names) == 0
run_test("no CALL → empty list", _test_deps_no_calls)

def _test_deps_resolve_found():
    tmp = ws()
    # Create a macro file in src/
    macro_xml = '<?xml version="1.0"?><Symbol><Parameters><Parameter><n>rWidth</n><Type>Length</Type><Value>1.0</Value></Parameter></Parameters><Script_3D>x</Script_3D></Symbol>'
    (tmp / "src" / "Macro_Frame.xml").write_text(macro_xml)
    resolver = DependencyResolver(str(tmp / "src"), str(tmp / "knowledge"))
    calling_xml = '<Symbol><Script_3D><![CDATA[CALL "Macro_Frame"]]></Script_3D></Symbol>'
    sigs = resolver.resolve(calling_xml)
    assert len(sigs) == 1
    assert sigs[0].name == "Macro_Frame"
    assert len(sigs[0].parameters) == 1
    assert sigs[0].parameters[0]["name"] == "rWidth"
run_test("resolve CALL → find macro params", _test_deps_resolve_found)

def _test_deps_resolve_not_found():
    tmp = ws()
    resolver = DependencyResolver(str(tmp / "src"), str(tmp / "knowledge"))
    calling_xml = '<Symbol><Script_3D><![CDATA[CALL "NonExistent"]]></Script_3D></Symbol>'
    sigs = resolver.resolve(calling_xml)
    assert len(sigs) == 0
run_test("resolve missing macro → empty", _test_deps_resolve_not_found)

def _test_deps_format_prompt():
    from gdl_agent.dependencies import MacroSignature
    sig = MacroSignature("TestMacro", [{"name": "A", "type": "Length", "value": "1.0"}])
    resolver = DependencyResolver()
    text = resolver.format_all_for_prompt([sig])
    assert "TestMacro" in text
    assert "EXACTLY" in text
run_test("format deps for prompt", _test_deps_format_prompt)

# ── v0.2: Enhanced XML Validation Tests ───────────────────
print("\n🔍 Enhanced Validation (v0.2)")

def _test_cdata_mismatch():
    xml = '<?xml version="1.0"?><Symbol><Script_3D><![CDATA[ code here </Script_3D></Symbol>'
    issues = validate_gdl_structure(xml)
    assert any("CDATA" in i for i in issues), f"Expected CDATA issue, got: {issues}"
run_test("CDATA boundary mismatch detected", _test_cdata_mismatch)

def _test_while_endwhile():
    xml = '<?xml version="1.0"?><Symbol><Script_3D><![CDATA[\nWHILE x > 0\n  x = x - 1\n]]></Script_3D></Symbol>'
    issues = validate_gdl_structure(xml)
    assert any("WHILE/ENDWHILE" in i for i in issues), f"Expected WHILE issue: {issues}"
run_test("WHILE/ENDWHILE mismatch detected", _test_while_endwhile)

def _test_gosub_missing_label():
    xml = '<?xml version="1.0"?><Symbol><Script_3D><![CDATA[\nGOSUB 999\nEND\n]]></Script_3D></Symbol>'
    issues = validate_gdl_structure(xml)
    assert any("GOSUB 999" in i for i in issues), f"Expected GOSUB issue: {issues}"
run_test("GOSUB missing target label", _test_gosub_missing_label)

def _test_gosub_valid():
    xml = '<?xml version="1.0"?><Symbol><Script_3D><![CDATA[\nGOSUB 100\nEND\n100:\nRETURN\n]]></Script_3D></Symbol>'
    issues = validate_gdl_structure(xml)
    gosub_issues = [i for i in issues if "GOSUB" in i]
    assert len(gosub_issues) == 0, f"Unexpected GOSUB issue: {gosub_issues}"
run_test("GOSUB with valid label → no issue", _test_gosub_valid)

# ── v0.2: Debug Anchor Tests ─────────────────────────────
print("\n🔴 Debug Anchors (v0.2)")

def _test_debug_inject():
    injected = inject_debug_anchors(VALID_XML)
    assert "DEBUG BOUNDING BOX" in injected
    assert "BLOCK A, B, ZZYZX" in injected
    # Should still be valid XML
    r = validate_xml(injected)
    assert r.valid, f"Injected XML invalid: {r.error}"
run_test("inject debug anchors", _test_debug_inject)

def _test_debug_no_script3d():
    xml = '<?xml version="1.0"?><Symbol><Parameters/></Symbol>'
    injected = inject_debug_anchors(xml)
    assert injected == xml, "Should not modify XML without Script_3D"
run_test("no Script_3D → unchanged", _test_debug_no_script3d)

# ── v0.2: Agent Integration Tests ─────────────────────────
print("\n🤖 Agent v0.2 Integration")

def _test_agent_snippets_injected():
    tmp = ws()
    c = cfg(tmp)
    events = []
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, on_event=lambda e, **kw: events.append((e, kw)))
    agent.run("Add a FOR loop to repeat geometry", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    snippet_events = [e for e, kw in events if e == "snippets_matched"]
    assert len(snippet_events) > 0, "Should emit snippets_matched event"
run_test("agent injects snippets for 'FOR loop'", _test_agent_snippets_injected)

def _test_agent_debug_mode():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, debug_mode=True, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.success
    written = Path(tmp/"src"/"t.xml").read_text()
    assert "DEBUG BOUNDING BOX" in written
run_test("debug_mode injects bounding box", _test_agent_debug_mode)

# ── v0.3: Sandbox Tests ──────────────────────────────────
print("\n🏖️  Sandbox (v0.3)")

def _test_sandbox_prepare():
    tmp = ws()
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    paths = sb.prepare("window.xml", "window.gsm", attempt=2)
    assert "temp" in str(paths.temp_xml)
    assert "_wip_" in str(paths.temp_xml)
    assert paths.attempt == 2
run_test("prepare sandbox paths", _test_sandbox_prepare)

def _test_sandbox_write_temp():
    tmp = ws()
    (tmp/"temp").mkdir()
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    paths = sb.prepare("t.xml", "t.gsm")
    sb.write_temp(paths, "<Symbol>test</Symbol>")
    assert paths.temp_xml.exists()
    assert paths.temp_xml.read_text() == "<Symbol>test</Symbol>"
    assert not paths.source_original.exists(), "Source should NOT be written"
run_test("write_temp: only temp, not source", _test_sandbox_write_temp)

def _test_sandbox_promote():
    tmp = ws()
    (tmp/"temp").mkdir()
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    paths = sb.prepare("t.xml", "t.gsm")
    sb.write_temp(paths, "<Symbol>good</Symbol>")
    # Simulate compile output
    paths.temp_output.write_text("[gsm]")
    sb.promote(paths)
    assert paths.source_original.exists(), "Source should be updated after promote"
    assert paths.source_original.read_text() == "<Symbol>good</Symbol>"
    assert paths.final_output.exists()
run_test("promote: temp → source + output", _test_sandbox_promote)

def _test_sandbox_archive():
    tmp = ws()
    (tmp/"temp").mkdir()
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    paths = sb.prepare("t.xml", "t.gsm", attempt=3)
    sb.write_temp(paths, "<Symbol>bad</Symbol>")
    sb.archive_attempt(paths)
    assert paths.attempt_archive.exists()
    assert "attempt_003" in str(paths.attempt_archive)
    assert not paths.source_original.exists(), "Source should NOT be modified on failure"
run_test("archive: failed attempt preserved, source untouched", _test_sandbox_archive)

def _test_sandbox_source_protected():
    """The critical test: a failed compile should NEVER touch source."""
    tmp = ws()
    (tmp/"temp").mkdir()
    # Write initial golden source
    (tmp/"src"/"t.xml").write_text("<Symbol>GOLDEN</Symbol>")
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    paths = sb.prepare("t.xml", "t.gsm")
    sb.write_temp(paths, "<Symbol>BROKEN</Symbol>")
    # Simulate failure — do NOT promote
    sb.archive_attempt(paths)
    assert (tmp/"src"/"t.xml").read_text() == "<Symbol>GOLDEN</Symbol>", \
        "Source MUST remain unchanged after failed attempt"
run_test("source file protected on failure", _test_sandbox_source_protected)

# ── v0.3: Context Surgery Tests ──────────────────────────
print("\n🔪 Context Surgery (v0.3)")

FULL_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Parameters>
    <Parameter><n>bTest</n><Type>Boolean</Type><Value>0</Value></Parameter>
  </Parameters>
  <Script_2D><![CDATA[
RECT2 0, 0, A, B
  ]]></Script_2D>
  <Script_3D><![CDATA[
PRISM_ 4, 1.0, 0,0, 1,0, 1,1, 0,1
  ]]></Script_3D>
  <Script_UI><![CDATA[
UI_DIALOG "Settings"
UI_INFIELD "A", 10, 1, 30, 1
  ]]></Script_UI>
</Symbol>'''

def _test_context_detect_3d():
    sections = detect_relevant_sections("add 3D geometry using PRISM")
    assert "Script_3D" in sections
run_test("detect: '3D geometry' → Script_3D", _test_context_detect_3d)

def _test_context_detect_ui():
    sections = detect_relevant_sections("修改界面面板的菜单")
    assert "Script_UI" in sections
run_test("detect: '修改界面' → Script_UI", _test_context_detect_ui)

def _test_context_detect_2d():
    sections = detect_relevant_sections("fix the 2D floor plan symbol")
    assert "Script_2D" in sections
run_test("detect: '2D floor plan' → Script_2D", _test_context_detect_2d)

def _test_context_detect_unclear():
    sections = detect_relevant_sections("make it better")
    assert len(sections) == 0, "Unclear intent should return empty (full fallback)"
run_test("detect: unclear → empty (fallback to full)", _test_context_detect_unclear)

def _test_context_slice():
    ctx = slice_context(FULL_XML, "add 3D geometry")
    assert not ctx.is_full, "Should be sliced, not full"
    assert ctx.savings_pct > 0, f"Expected savings > 0, got {ctx.savings_pct}%"
    xml_str = ctx.to_xml_string()
    assert "Script_3D" in xml_str or "PRISM" in xml_str
    assert "OMITTED" in xml_str, "Should note omitted sections"
run_test("slice: 3D task → omit Script_2D/UI", _test_context_slice)

def _test_context_full_fallback():
    ctx = slice_context(FULL_XML, "general improvements")
    assert ctx.is_full
run_test("slice: unclear → full fallback", _test_context_full_fallback)

# ── v0.3: Preflight Analyzer Tests ──────────────────────
print("\n✈️  Preflight (v0.3)")

def _test_preflight_simple():
    analyzer = PreflightAnalyzer()
    result = analyzer.analyze("change default width to 1200", VALID_XML)
    assert result.feasible
    assert result.complexity == "simple"
run_test("simple task → feasible, simple", _test_preflight_simple)

def _test_preflight_complex():
    analyzer = PreflightAnalyzer()
    result = analyzer.analyze("create a new curtain wall object from scratch")
    assert result.feasible
    assert result.complexity == "complex"
run_test("complex task → feasible, complex", _test_preflight_complex)

def _test_preflight_binary_blocker():
    analyzer = PreflightAnalyzer()
    result = analyzer.analyze("modify this file", "\x89PNG\r\n binary content")
    assert not result.feasible
    assert len(result.blockers) > 0
run_test("binary file → blocked", _test_preflight_binary_blocker)

def _test_preflight_unresolved_macro():
    tmp = ws()
    resolver = DependencyResolver(str(tmp/"src"), str(tmp/"knowledge"))
    analyzer = PreflightAnalyzer(resolver)
    xml_with_call = '<Symbol><Script_3D><![CDATA[CALL "Missing_Macro"]]></Script_3D></Symbol>'
    result = analyzer.analyze("modify geometry", xml_with_call)
    assert "Missing_Macro" in result.unresolved_macros
    assert len(result.warnings) > 0
run_test("unresolved macro → warning", _test_preflight_unresolved_macro)

# ── v0.3: Agent Integration Tests ─────────────────────────
print("\n🤖 Agent v0.3 Integration")

def _test_agent_sandbox_integration():
    """Full loop: source should only be written after compile success."""
    tmp = ws()
    (tmp/"temp").mkdir()
    c = cfg(tmp)
    # Write initial source
    (tmp/"src"/"t.xml").write_text("<Symbol>INITIAL</Symbol>")
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    agent = GDLAgent(c, llm, compiler, kb, sandbox=sb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.success
    # Source should now be updated (promoted after success)
    content = (tmp/"src"/"t.xml").read_text()
    assert "PRISM_" in content, "Source should be updated after successful compile"
run_test("sandbox: source updated only on success", _test_agent_sandbox_integration)

def _test_agent_sandbox_failure_protects():
    """On compile failure, source must remain unchanged."""
    tmp = ws()
    (tmp/"temp").mkdir()
    c = cfg(tmp)
    c.agent.max_iterations = 2
    (tmp/"src"/"t.xml").write_text("<Symbol>PRECIOUS</Symbol>")
    # Always produce different but invalid XML
    responses = [INVALID_IF, INVALID_IF.replace("bTest", "b2")]
    llm = MockLLM(responses=responses)
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    sb = Sandbox(str(tmp/"src"), str(tmp/"temp"), str(tmp/"output"))
    agent = GDLAgent(c, llm, compiler, kb, sandbox=sb, self_review=False)
    result = agent.run("Test", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert not result.success
    content = (tmp/"src"/"t.xml").read_text()
    assert content == "<Symbol>PRECIOUS</Symbol>", \
        f"Source MUST be protected! Got: {content[:50]}"
run_test("sandbox: source PROTECTED on failure", _test_agent_sandbox_failure_protects)

def _test_agent_analysis_in_result():
    tmp = ws()
    c = cfg(tmp)
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Add 3D geometry", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.analysis is not None, "Result should contain analysis"
    assert result.analysis.feasible
run_test("analysis attached to result", _test_agent_analysis_in_result)

def _test_agent_blocked_by_preflight():
    tmp = ws()
    c = cfg(tmp)
    # Write binary content as source
    (tmp/"src"/"t.xml").write_bytes(b"\x89PNG\r\n\x1a\n binary")
    llm = MockLLM(responses=[VALID_XML])
    compiler = MockCompiler()
    kb = KnowledgeBase(str(tmp / "knowledge"))
    agent = GDLAgent(c, llm, compiler, kb, self_review=False)
    result = agent.run("Modify", str(tmp/"src"/"t.xml"), str(tmp/"output"/"t.gsm"))
    assert result.status == Status.BLOCKED
    assert llm.call_count == 0, "LLM should NOT be called when preflight blocks"
run_test("preflight blocker → no LLM call", _test_agent_blocked_by_preflight)

# ── v0.3.1: GDL Parser Tests ─────────────────────────────
print("\n📄 GDL Parser (v0.3.1)")

BOOKSHELF_GDL = '''! ============================================================================
! 对象名称：TestObj (测试)
! 描述：A test object
! 版本：2.0.0
! ============================================================================

! ============================================================================
! 参数列表（PARAMETERS）
! ============================================================================
! rWidth      Length    1.00    Width
! iCount      Integer   5       Count
! bToggle     Boolean   1       Toggle
! matSurf     Material  "Wood - Oak"    Surface

! ============================================================================
! MASTER SCRIPT
! ============================================================================

IF rWidth < 0.1 THEN rWidth = 0.1
_half = rWidth / 2

! ============================================================================
! PARAMETER SCRIPT
! ============================================================================

VALUES "rWidth" RANGE [0.1, 5.0]

! ============================================================================
! 3D SCRIPT
! ============================================================================

SET MATERIAL matSurf
FOR i = 1 TO iCount
    ADD 0, 0, i * 0.3
    BLOCK rWidth, 0.5, 0.02
    DEL 1
NEXT i

IF bToggle THEN
    ADD 0, 0, 0
    BLOCK rWidth, 0.5, _half
    DEL 1
ENDIF
'''

def _test_parser_params():
    r = parse_gdl_source(BOOKSHELF_GDL)
    assert len(r.parameters) == 4, f"Expected 4 params, got {len(r.parameters)}"
    names = [p.name for p in r.parameters]
    assert "rWidth" in names
    assert "matSurf" in names
run_test("parse parameters from comments", _test_parser_params)

def _test_parser_metadata():
    r = parse_gdl_source(BOOKSHELF_GDL)
    assert r.description == "A test object"
    assert r.version == "2.0.0"
run_test("extract metadata", _test_parser_metadata)

def _test_parser_scripts():
    r = parse_gdl_source(BOOKSHELF_GDL)
    assert "rWidth" in r.master_script
    assert "VALUES" in r.parameter_script
    assert "BLOCK" in r.script_3d
    assert "FOR" in r.script_3d
run_test("extract all script sections", _test_parser_scripts)

def _test_parser_to_xml():
    r = parse_gdl_source(BOOKSHELF_GDL)
    xml = r.to_xml()
    vr = validate_xml(xml)
    assert vr.valid, f"Generated XML invalid: {vr.error}"
run_test("to_xml produces valid XML", _test_parser_to_xml)

def _test_parser_xml_validates_gdl():
    r = parse_gdl_source(BOOKSHELF_GDL)
    xml = r.to_xml()
    issues = validate_gdl_structure(xml)
    assert len(issues) == 0, f"GDL issues: {issues}"
run_test("generated XML passes GDL validation", _test_parser_xml_validates_gdl)

def _test_parser_single_line_if():
    """Single-line IF THEN should NOT need ENDIF."""
    src = '''! ============================================================================
! 参数列表（PARAMETERS）
! ============================================================================
! A Length 1.0 Width

! ============================================================================
! MASTER SCRIPT
! ============================================================================
IF A < 0 THEN A = 0
IF A > 10 THEN A = 10

! ============================================================================
! 3D SCRIPT
! ============================================================================
BLOCK A, 1, 1
'''
    r = parse_gdl_source(src)
    xml = r.to_xml()
    issues = validate_gdl_structure(xml)
    if_issues = [i for i in issues if "IF/ENDIF" in i]
    assert len(if_issues) == 0, f"False positive IF/ENDIF: {if_issues}"
run_test("single-line IF THEN: no false positive", _test_parser_single_line_if)

def _test_parser_material_quoted():
    r = parse_gdl_source(BOOKSHELF_GDL)
    mat = [p for p in r.parameters if p.name == "matSurf"][0]
    assert '"Wood - Oak"' in mat.value, f"Expected quoted value, got {mat.value}"
run_test("Material with quoted value", _test_parser_material_quoted)

def _test_parser_commented_ui():
    src = '''! ============================================================================
! 3D SCRIPT
! ============================================================================
BLOCK 1, 1, 1

! ============================================================================
! UI SCRIPT（如需要）
! ============================================================================
! UI_DIALOG "Settings"
! UI_PAGE 1
! UI_INFIELD "A", 10, 30, 80, 20
'''
    r = parse_gdl_source(src)
    assert "UI_DIALOG" in r.script_ui, f"Should uncomment UI: {r.script_ui[:50]}"
    assert not r.script_ui.startswith("!"), "Should NOT start with comment"
run_test("uncomment commented-out UI script", _test_parser_commented_ui)

def _test_parser_real_bookshelf():
    """Parse the actual uploaded Bookshelf.gdl file."""
    import os
    path = "/mnt/user-data/uploads/Bookshelf.gdl"
    if not os.path.exists(path):
        return  # Skip if file not available
    r = parse_gdl_file(path)
    assert len(r.parameters) == 10, f"Expected 10 params, got {len(r.parameters)}"
    assert r.name == "Bookshelf"
    xml = r.to_xml()
    vr = validate_xml(xml)
    assert vr.valid
    issues = validate_gdl_structure(xml)
    assert len(issues) == 0, f"Issues: {issues}"
run_test("real Bookshelf.gdl: 10 params, clean XML", _test_parser_real_bookshelf)

# ── Summary ───────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  📊 Results: {passed}/{total} passed, {failed} failed")
if failed == 0:
    print(f"  🎉 All tests passed!")
else:
    print(f"  ⚠️  {failed} test(s) failed")
print(f"{'='*50}\n")

sys.exit(0 if failed == 0 else 1)
