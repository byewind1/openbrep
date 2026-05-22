import tempfile
import unittest
from pathlib import Path

from evals.prepare_fixtures import prepare_broken_fixtures
from evals.scorecards.run_scorecard import run_scorecard


class TestEvalScorecard(unittest.TestCase):
    def test_prepare_broken_fixtures_creates_expected_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            valid_dir = root / "valid"
            broken_dir = root / "broken"
            valid_dir.mkdir()
            (valid_dir / "box.gdl").write_text(
                "ADDX 1\nBLOCK A, B, ZZYZX\nDEL 1\nFOR i = 1 TO 2\nBLOCK A, B, ZZYZX\nNEXT i\nEND\n",
                encoding="utf-8",
            )

            written = prepare_broken_fixtures(valid_dir, broken_dir)

            self.assertEqual(len(written), 4)
            self.assertTrue((broken_dir / "box__bad_command.gdl").exists())
            self.assertTrue((broken_dir / "box__stack_imbalance.gdl").exists())
            self.assertTrue((broken_dir / "box__misspelled_variable.gdl").exists())
            self.assertTrue((broken_dir / "box__missing_next.gdl").exists())

    def test_mock_scorecard_reports_valid_and_broken_fixture_quality(self):
        result = run_scorecard(mode="mock")

        self.assertEqual(result["summary"]["failed"], 0)
        self.assertEqual(result["suites"]["fixture_compile"]["total"], 5)
        self.assertEqual(result["suites"]["fixture_compile"]["pass_rate"], 1.0)
        self.assertEqual(result["suites"]["broken_detection"]["total"], 20)
        self.assertEqual(result["suites"]["broken_detection"]["pass_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
