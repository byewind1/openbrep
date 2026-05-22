import unittest
from unittest.mock import patch

from evals.scorecards.run_scorecard import main, run_scorecard


class _UnavailableCompiler:
    converter_path = None

    @property
    def is_available(self):
        return False


class TestScorecardRealMode(unittest.TestCase):
    def test_real_mode_skips_when_converter_is_missing(self):
        with patch("evals.scorecards.run_scorecard.HSFCompiler", return_value=_UnavailableCompiler()):
            result = run_scorecard(mode="real")

        self.assertTrue(result["skipped"])
        self.assertEqual(result["effective_mode"], "real")
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertIn("LP_XMLConverter not found", result["skip_reason"])
        self.assertIsNone(result["environment"]["converter_path"])

    def test_auto_mode_falls_back_to_mock_when_converter_is_missing(self):
        with patch("evals.scorecards.run_scorecard.HSFCompiler", return_value=_UnavailableCompiler()):
            result = run_scorecard(mode="auto")

        self.assertFalse(result["skipped"])
        self.assertEqual(result["mode"], "auto")
        self.assertEqual(result["effective_mode"], "mock")
        self.assertEqual(result["summary"]["failed"], 0)
        first_case = result["suites"]["fixture_compile"]["cases"][0]
        self.assertEqual(first_case["compile_mode"], "mock")
        self.assertIn("compile_exit_code", first_case)

    def test_real_mode_skip_exits_successfully_from_cli(self):
        with patch("evals.scorecards.run_scorecard.HSFCompiler", return_value=_UnavailableCompiler()):
            exit_code = main(["--mode", "real"])

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
