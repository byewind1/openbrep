"""
Tests for openbrep.script_generator.

Covers:
  - detect_affected_scripts: keyword routing
  - merge_results: applies to project, cross-script warning
  - generate_script: mock llm_caller success/failure
  - GDLAgent._build_script_context: correct file set per script type
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from openbrep.script_generator import ScriptGenerator, ScriptResult, ScriptType


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeParam:
    def __init__(self, name: str, type_tag: str = "Length",
                 value: str = "1.0", description: str = "", is_fixed: bool = False):
        self.name = name
        self.type_tag = type_tag
        self.value = value
        self.description = description
        self.is_fixed = is_fixed


class FakeProject:
    """Minimal stand-in for HSFProject."""
    def __init__(self, scripts: dict[str, str], param_names: list[str]):
        self._scripts = dict(scripts)
        self.parameters = [FakeParam(n) for n in param_names]

    def get_script(self, script_type) -> str:
        return self._scripts.get(script_type.value, "")

    def set_script(self, script_type, content: str) -> None:
        self._scripts[script_type.value] = content


def _make_generator(llm_response: str = "") -> ScriptGenerator:
    return ScriptGenerator(llm_caller=lambda msgs: llm_response)


# ── detect_affected_scripts ───────────────────────────────────────────────────

class TestDetectAffectedScripts(unittest.TestCase):

    def setUp(self):
        self.gen = _make_generator()

    def test_create_keyword_returns_all(self):
        """'创建' triggers full generation: params + master + 3d + 2d."""
        result = self.gen.detect_affected_scripts("创建一个书架对象")
        self.assertIn(ScriptType.PARAMS,    result)
        self.assertIn(ScriptType.MASTER,    result)
        self.assertIn(ScriptType.SCRIPT_3D, result)
        self.assertIn(ScriptType.SCRIPT_2D, result)

    def test_english_create_returns_all(self):
        """'create' triggers full generation."""
        result = self.gen.detect_affected_scripts("create a shelf object")
        self.assertIn(ScriptType.PARAMS,    result)
        self.assertIn(ScriptType.SCRIPT_3D, result)

    def test_3d_keyword_includes_3d(self):
        """'三维' / '3d' / 'geometry' → SCRIPT_3D in result."""
        for phrase in ("修改三维几何", "fix 3d geometry", "update model"):
            with self.subTest(phrase=phrase):
                result = self.gen.detect_affected_scripts(phrase)
                self.assertIn(ScriptType.SCRIPT_3D, result)

    def test_params_keyword_includes_params_and_master(self):
        """'参数' / 'parameter' → PARAMS + MASTER both present."""
        result = self.gen.detect_affected_scripts("添加参数 nShelves")
        self.assertIn(ScriptType.PARAMS, result)
        self.assertIn(ScriptType.MASTER, result)

    def test_2d_keyword_includes_2d(self):
        """'2d' / 'plan' / '平面' → SCRIPT_2D in result."""
        for phrase in ("修改 2d 平面图", "update floor plan", "update 2d"):
            with self.subTest(phrase=phrase):
                result = self.gen.detect_affected_scripts(phrase)
                self.assertIn(ScriptType.SCRIPT_2D, result)

    def test_default_fallback_returns_four_scripts(self):
        """Unrecognised instruction → default four scripts."""
        result = self.gen.detect_affected_scripts("improve the object")
        self.assertEqual(
            set(result),
            {ScriptType.PARAMS, ScriptType.MASTER, ScriptType.SCRIPT_3D, ScriptType.SCRIPT_2D},
        )

    def test_no_duplicates(self):
        """Result list must not contain duplicate ScriptType entries."""
        result = self.gen.detect_affected_scripts("创建 3d geometry with 参数")
        self.assertEqual(len(result), len(set(result)))


# ── generate_script ───────────────────────────────────────────────────────────

class TestGenerateScript(unittest.TestCase):

    def test_generate_success_with_file_block(self):
        """LLM returns a [FILE: scripts/3d.gdl] block → content extracted."""
        response = "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
        gen = _make_generator(response)
        result = gen.generate_script(
            ScriptType.SCRIPT_3D,
            instruction="make a block",
            project_context={"paramlist.xml": "Length A = 1.0"},
        )
        self.assertTrue(result.success)
        self.assertIn("BLOCK", result.content)
        self.assertEqual(result.script_type, ScriptType.SCRIPT_3D)

    def test_generate_fallback_when_no_file_block(self):
        """LLM returns raw content (no [FILE:] header) → whole response used."""
        raw = "BLOCK A, B, ZZYZX\nEND"
        gen = _make_generator(raw)
        result = gen.generate_script(
            ScriptType.SCRIPT_3D,
            instruction="block",
            project_context={},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.content, raw)

    def test_generate_llm_exception_returns_failure(self):
        """LLM caller raises → ScriptResult(success=False)."""
        def bad_caller(msgs):
            raise RuntimeError("network error")
        gen = ScriptGenerator(llm_caller=bad_caller)
        result = gen.generate_script(ScriptType.SCRIPT_3D, "x", {})
        self.assertFalse(result.success)
        self.assertIn("network error", result.error)


# ── merge_results ─────────────────────────────────────────────────────────────

class TestMergeResults(unittest.TestCase):

    def test_merge_applies_script_to_project(self):
        """Successful ScriptResult → project.set_script called with correct content."""
        proj = FakeProject(scripts={}, param_names=["width"])
        gen = _make_generator()
        results = [
            ScriptResult(
                script_type=ScriptType.SCRIPT_3D,
                content="BLOCK A, B, ZZYZX\nEND",
                success=True,
            )
        ]
        merged = gen.merge_results(results, proj)
        self.assertIn("scripts/3d.gdl", merged)
        self.assertEqual(merged["scripts/3d.gdl"], "BLOCK A, B, ZZYZX\nEND")
        # Verify it was written to project
        self.assertEqual(proj._scripts.get("3d.gdl"), "BLOCK A, B, ZZYZX\nEND")

    def test_merge_skips_failed_results(self):
        """ScriptResult(success=False) must not be written to project or merged dict."""
        proj = FakeProject(scripts={}, param_names=[])
        gen = _make_generator()
        results = [
            ScriptResult(script_type=ScriptType.SCRIPT_3D, content="", success=False),
        ]
        merged = gen.merge_results(results, proj)
        self.assertNotIn("scripts/3d.gdl", merged)
        self.assertNotIn("3d.gdl", proj._scripts)

    def test_merge_none_project_returns_empty(self):
        """None project → empty dict, no exception."""
        gen = _make_generator()
        result = gen.merge_results([], None)
        self.assertEqual(result, {})

    def test_merge_cross_script_warning_logged(self):
        """Variable used in 3d but absent from params → warning logged."""
        proj = FakeProject(scripts={}, param_names=["width"])
        gen = _make_generator()
        results = [
            ScriptResult(
                script_type=ScriptType.SCRIPT_3D,
                content="BLOCK mystery_var, B, ZZYZX\nEND",
                success=True,
            )
        ]
        with self.assertLogs("openbrep.script_generator", level="WARNING") as cm:
            gen.merge_results(results, proj)
        # At least one warning mentions the unknown variable
        warnings_text = " ".join(cm.output)
        self.assertIn("mystery_var", warnings_text)

    def test_merge_cross_script_ignores_file_metadata(self):
        """[FILE: ...], comments, and blank lines must not create cross-script warnings."""
        proj = FakeProject(scripts={}, param_names=["Length", "width", "height"])
        gen = _make_generator()
        results = [
            ScriptResult(
                script_type=ScriptType.SCRIPT_3D,
                content="[FILE: scripts/3d.gdl]\n\n! generated\nBLOCK width, height, B\nEND",
                success=True,
            )
        ]
        with self.assertNoLogs("openbrep.script_generator", level="WARNING"):
            gen.merge_results(results, proj)

    def test_merge_cross_script_allows_boolean_params(self):
        """Known params like bTest must not trigger cross-script warnings."""
        proj = FakeProject(scripts={}, param_names=["bTest"])
        gen = _make_generator()
        results = [
            ScriptResult(
                script_type=ScriptType.SCRIPT_3D,
                content="IF bTest THEN\n  BLOCK 1,1,1\nENDIF",
                success=True,
            )
        ]
        with self.assertNoLogs("openbrep.script_generator", level="WARNING"):
            gen.merge_results(results, proj)


# ── GDLAgent._build_script_context ───────────────────────────────────────────

class TestBuildScriptContext(unittest.TestCase):
    """Test GDLAgent._build_script_context via a lightweight GDLAgent instance."""

    def _make_agent(self, scripts: dict[str, str], param_names: list[str]):
        """Create a GDLAgent with a MockLLM and pre-loaded project state."""
        from openbrep.core import GDLAgent
        mock_llm = MagicMock()
        mock_llm.generate.return_value = ""
        agent = GDLAgent(llm=mock_llm)

        # Patch _call_llm so ScriptGenerator works without real LLM
        agent._call_llm = lambda msgs: ""
        return agent

    def _make_project(self, scripts: dict[str, str], param_names: list[str]):
        return FakeProject(scripts=scripts, param_names=param_names)

    def test_3d_context_includes_params_and_1d_and_3d(self):
        """3d script context must include paramlist.xml, 1d.gdl, and 3d.gdl."""
        agent = self._make_agent({}, [])
        proj = self._make_project(
            scripts={"1d.gdl": "! master", "3d.gdl": "BLOCK 1,1,1\nEND"},
            param_names=["width"],
        )
        ctx = agent._build_script_context(ScriptType.SCRIPT_3D, proj)
        self.assertIn("paramlist.xml",   ctx)
        self.assertIn("scripts/1d.gdl",  ctx)
        self.assertIn("scripts/3d.gdl",  ctx)

    def test_3d_context_excludes_2d_vl_ui(self):
        """3d context must NOT include 2d.gdl, vl.gdl, or ui.gdl."""
        agent = self._make_agent({}, [])
        proj = self._make_project(
            scripts={"2d.gdl": "PROJECT2 3,270,2", "vl.gdl": "! vl"},
            param_names=[],
        )
        ctx = agent._build_script_context(ScriptType.SCRIPT_3D, proj)
        self.assertNotIn("scripts/2d.gdl",  ctx)
        self.assertNotIn("scripts/vl.gdl",  ctx)
        self.assertNotIn("scripts/ui.gdl",  ctx)

    def test_params_context_contains_only_paramlist(self):
        """PARAMS context must contain paramlist.xml and nothing else."""
        agent = self._make_agent({}, [])
        proj = self._make_project(scripts={"3d.gdl": "BLOCK 1,1,1\nEND"}, param_names=["width"])
        ctx = agent._build_script_context(ScriptType.PARAMS, proj)
        self.assertIn("paramlist.xml", ctx)
        self.assertNotIn("scripts/3d.gdl", ctx)
        self.assertNotIn("scripts/1d.gdl", ctx)

    def test_2d_context_includes_1d_but_not_3d(self):
        """2d context gets paramlist + 1d.gdl + 2d.gdl — not 3d.gdl."""
        agent = self._make_agent({}, [])
        proj = self._make_project(
            scripts={"1d.gdl": "! master", "2d.gdl": "PROJECT2 3,270,2", "3d.gdl": "BLOCK 1,1,1"},
            param_names=["width"],
        )
        ctx = agent._build_script_context(ScriptType.SCRIPT_2D, proj)
        self.assertIn("scripts/1d.gdl",  ctx)
        self.assertIn("scripts/2d.gdl",  ctx)
        self.assertNotIn("scripts/3d.gdl", ctx)


if __name__ == "__main__":
    unittest.main()
