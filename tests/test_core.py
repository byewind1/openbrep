"""
Tests for the GDL Agent core loop.

Uses mock LLM + mock compiler to verify the full agentic workflow
without requiring ArchiCAD or API keys.
"""

import os
import tempfile
from pathlib import Path

import pytest

from openbrep.compiler import MockCompiler
from openbrep.config import AgentConfig, CompilerConfig, GDLAgentConfig, LLMConfig
from openbrep.core import GDLAgent, Status
from openbrep.knowledge import KnowledgeBase
from openbrep.llm import Message, MockLLM
from openbrep.xml_utils import validate_xml, validate_gdl_structure, compute_diff


# ── Fixtures ──────────────────────────────────────────────────────────

VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Parameters>
    <Parameter>
      <n>bTest</n>
      <Type>Boolean</Type>
      <Value>0</Value>
      <Description>Test parameter</Description>
    </Parameter>
  </Parameters>
  <Script_3D><![CDATA[
PRISM_ 4, 1.0,
  0, 0,
  1, 0,
  1, 1,
  0, 1
  ]]></Script_3D>
</Symbol>"""

INVALID_XML_IF_MISMATCH = """\
<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Script_3D><![CDATA[
IF bTest THEN
  PRISM_ 4, 1.0,
    0, 0, 1, 0, 1, 1, 0, 1
  ]]></Script_3D>
</Symbol>"""

MALFORMED_XML = "<Symbol><Unclosed>"


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with required directories."""
    (tmp_path / "src").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "knowledge").mkdir()
    return tmp_path


@pytest.fixture
def config(tmp_workspace):
    return GDLAgentConfig(
        llm=LLMConfig(model="mock"),
        agent=AgentConfig(max_iterations=5),
        compiler=CompilerConfig(path="/mock"),
        knowledge_dir=str(tmp_workspace / "knowledge"),
        src_dir=str(tmp_workspace / "src"),
        output_dir=str(tmp_workspace / "output"),
    )


# ── XML Utils Tests ───────────────────────────────────────────────────


class TestXMLValidation:
    def test_valid_xml(self):
        result = validate_xml(VALID_XML)
        assert result.valid

    def test_malformed_xml(self):
        result = validate_xml(MALFORMED_XML)
        assert not result.valid
        assert "ParseError" in result.error or "unclosed" in result.error.lower()

    def test_empty_string(self):
        result = validate_xml("")
        assert not result.valid


class TestGDLValidation:
    def test_valid_structure(self):
        issues = validate_gdl_structure(VALID_XML)
        assert len(issues) == 0

    def test_if_endif_mismatch(self):
        issues = validate_gdl_structure(INVALID_XML_IF_MISMATCH)
        assert any("IF/ENDIF" in i for i in issues)

    def test_wrong_root(self):
        xml = '<?xml version="1.0"?><Object><Script_3D/></Object>'
        issues = validate_gdl_structure(xml)
        assert any("Symbol" in i for i in issues)


class TestDiff:
    def test_identical(self):
        diff = compute_diff(VALID_XML, VALID_XML)
        assert diff == ""

    def test_different(self):
        modified = VALID_XML.replace("bTest", "bModified")
        diff = compute_diff(VALID_XML, modified)
        assert "bTest" in diff
        assert "bModified" in diff


# ── Agent Core Tests ──────────────────────────────────────────────────


class TestAgentSuccess:
    """Test the happy path: LLM generates valid code on first try."""

    def test_success_first_try(self, config, tmp_workspace):
        llm = MockLLM(responses=[VALID_XML])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Create a simple test object",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.SUCCESS
        assert result.attempts == 1
        assert result.output_path.endswith(".gsm")
        assert Path(result.output_path).exists()

    def test_success_after_retry(self, config, tmp_workspace):
        """LLM fails first (IF mismatch), then succeeds."""
        llm = MockLLM(responses=[INVALID_XML_IF_MISMATCH, VALID_XML])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Create object with IF block",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.SUCCESS
        assert result.attempts == 2
        assert len(result.history) == 2
        assert not result.history[0].success  # First attempt failed
        assert result.history[1].success  # Second succeeded


class TestAgentFailure:
    """Test failure modes."""

    def test_identical_retry_stops(self, config, tmp_workspace):
        """Agent should stop if it produces the same code twice."""
        llm = MockLLM(responses=[INVALID_XML_IF_MISMATCH, INVALID_XML_IF_MISMATCH])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Create object",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.FAILED
        assert "identical" in result.error_summary.lower() or "Identical" in result.error_summary

    def test_malformed_xml_retries(self, config, tmp_workspace):
        """Agent should retry when LLM produces malformed XML."""
        llm = MockLLM(responses=[MALFORMED_XML, VALID_XML])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Create object",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.SUCCESS
        assert result.attempts == 2

    def test_exhausted_retries(self, config, tmp_workspace):
        """Agent should give up after max_iterations."""
        config.agent.max_iterations = 3
        # Always return different but invalid XML
        responses = [
            INVALID_XML_IF_MISMATCH.replace("bTest", f"b{i}")
            for i in range(5)
        ]
        llm = MockLLM(responses=responses)
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Create object",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.EXHAUSTED
        assert result.attempts == 3


class TestAgentXMLExtraction:
    """Test XML extraction from various LLM response formats."""

    def test_extract_from_code_fence(self, config, tmp_workspace):
        response = f"Here's the XML:\n\n```xml\n{VALID_XML}\n```\n\nDone!"
        llm = MockLLM(responses=[response])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Test",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.SUCCESS

    def test_extract_raw_xml(self, config, tmp_workspace):
        """LLM returns raw XML without code fences."""
        llm = MockLLM(responses=[VALID_XML])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge)
        result = agent.run(
            instruction="Test",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert result.status == Status.SUCCESS


class TestAgentEventCallbacks:
    """Test that the agent emits correct events."""

    def test_events_emitted(self, config, tmp_workspace):
        events = []

        def handler(event, **kwargs):
            events.append(event)

        llm = MockLLM(responses=[VALID_XML])
        compiler = MockCompiler()
        knowledge = KnowledgeBase(str(tmp_workspace / "knowledge"))

        agent = GDLAgent(config, llm, compiler, knowledge, on_event=handler)
        agent.run(
            instruction="Test",
            source_path=str(tmp_workspace / "src" / "test.xml"),
            output_path=str(tmp_workspace / "output" / "test.gsm"),
        )

        assert "start" in events
        assert "attempt_start" in events
        assert "llm_call" in events
        assert "compile_success" in events


# ── Knowledge Tests ───────────────────────────────────────────────────


class TestKnowledge:
    def test_load_empty_dir(self, tmp_workspace):
        kb = KnowledgeBase(str(tmp_workspace / "knowledge"))
        kb.load()
        assert kb.doc_count == 0

    def test_load_docs(self, tmp_workspace):
        kdir = tmp_workspace / "knowledge"
        (kdir / "test.md").write_text("# Test\nHello", encoding="utf-8")
        kb = KnowledgeBase(str(kdir))
        kb.load()
        assert kb.doc_count == 1
        assert "Hello" in kb.get_all()

    def test_relevance(self, tmp_workspace):
        kdir = tmp_workspace / "knowledge"
        (kdir / "GDL_Reference.md").write_text("PRISM_ syntax docs", encoding="utf-8")
        (kdir / "Common_Errors.md").write_text("Error handling docs", encoding="utf-8")

        kb = KnowledgeBase(str(kdir))
        kb.load()

        result = kb.get_relevant("PRISM_ command syntax")
        assert "PRISM_" in result


# ── Config Tests ──────────────────────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        config = GDLAgentConfig()
        assert config.llm.model == "glm-4-flash"
        assert config.agent.max_iterations == 5
        assert config.llm.temperature == 0.2

    def test_load_from_toml(self, tmp_workspace):
        toml = tmp_workspace / "config.toml"
        toml.write_text(
            '[llm]\nmodel = "claude-sonnet"\ntemperature = 0.1\n'
            '[agent]\nmax_iterations = 3\n',
            encoding="utf-8",
        )
        config = GDLAgentConfig.load(str(toml))
        assert config.llm.model == "claude-sonnet"
        assert config.llm.temperature == 0.1
        assert config.agent.max_iterations == 3

    def test_to_toml_roundtrip(self):
        config = GDLAgentConfig()
        toml_str = config.to_toml_string()
        assert "glm-4-flash" in toml_str
        assert "max_iterations" in toml_str
