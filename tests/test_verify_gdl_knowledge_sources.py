import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_module():
    script = Path(__file__).parent.parent / "scripts" / "verify_gdl_knowledge_sources.py"
    spec = importlib.util.spec_from_file_location("verify_gdl_knowledge_sources", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestVerifyGDLKnowledgeSources(unittest.TestCase):
    def test_parse_official_index_normalizes_command_variants(self):
        verifier = _load_module()
        html = """
        <a href="/reference-guide/block/">BLOCK a, b, c</a>
        <a href="/reference-guide/project2-2/">PROJECT2{2} projection_code, angle, method</a>
        <a href="/reference-guide/material/">[SET] MATERIAL name_string</a>
        """

        parsed = verifier.parse_official_index(html, base_url="https://gdl.graphisoft.com/reference-guide/index/")

        self.assertIn("BLOCK", parsed)
        self.assertIn("PROJECT2", parsed)
        self.assertIn("MATERIAL", parsed)
        self.assertEqual(parsed["PROJECT2"][0].command, "PROJECT2")

    def test_verification_reports_ok_missing_and_needs_review(self):
        verifier = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "knowledge"
            wiki = root / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "BLOCK.md").write_text(
                "---\ntype: reference\nsource: official\n---\n"
                "# BLOCK\n\n## Syntax\n\n```gdl\nBLOCK a, b, c\n```\n",
                encoding="utf-8",
            )
            (wiki / "PRISM_.md").write_text(
                "---\ntype: reference\nsource: official\n---\n"
                "# PRISM_\n\nNo syntax section.\n",
                encoding="utf-8",
            )
            official = verifier.parse_official_index(
                '<a href="/block/">BLOCK a, b, c</a><a href="/prism/">PRISM_ n, h, ...</a>',
                base_url="https://gdl.graphisoft.com/reference-guide/index/",
            )

            rows = verifier.verify_commands(
                knowledge_dir=root,
                official_commands=official,
                commands=["BLOCK", "PRISM_", "REVOLVE"],
            )

        by_command = {row.command: row for row in rows}
        self.assertEqual(by_command["BLOCK"].status, "ok")
        self.assertEqual(by_command["PRISM_"].status, "needs_review")
        self.assertEqual(by_command["REVOLVE"].status, "missing_local")

    def test_main_writes_json_report_from_local_official_fixture(self):
        verifier = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "knowledge"
            wiki = root / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "BLOCK.md").write_text(
                "---\ntype: reference\nsource: official\n---\n"
                "# BLOCK\n\n## Syntax\n\n```gdl\nBLOCK a, b, c\n```\n",
                encoding="utf-8",
            )
            official = Path(tmpdir) / "official.html"
            official.write_text('<a href="/block/">BLOCK a, b, c</a>', encoding="utf-8")
            output = Path(tmpdir) / "report.json"

            code = verifier.main(
                [
                    "--knowledge-dir",
                    str(root),
                    "--official-index-file",
                    str(official),
                    "--commands",
                    "BLOCK",
                    "--output",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["rows"][0]["command"], "BLOCK")

    def test_offline_ok_writes_error_report(self):
        verifier = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "report.json"
            code = verifier.main(
                [
                    "--official-index-file",
                    str(Path(tmpdir) / "missing.html"),
                    "--offline-ok",
                    "--output",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertIn("Failed to load official index", payload["error"])


if __name__ == "__main__":
    unittest.main()
