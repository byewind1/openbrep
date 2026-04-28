import tempfile
import unittest

from openbrep.learning import (
    ErrorLearningStore,
    classify_error,
    error_fingerprint,
    looks_like_error_report,
)


class TestErrorLearning(unittest.TestCase):
    def test_classify_common_archicad_errors(self):
        self.assertEqual(classify_error("Error in 3D script, line 12: ENDIF expected"), "control_flow_closure")
        self.assertEqual(classify_error("Undefined variable seatH"), "variable_mapping")
        self.assertEqual(classify_error("Wrong number of arguments in PRISM_"), "command_arguments")

    def test_fingerprint_normalizes_line_numbers(self):
        first = error_fingerprint("Error in 3D script, line 12: ENDIF expected", "control_flow_closure")
        second = error_fingerprint("Error in 3D script, line 99: ENDIF expected", "control_flow_closure")
        self.assertEqual(first, second)

    def test_record_error_upserts_recurring_lesson_and_builds_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ErrorLearningStore(tmpdir)

            store.record_error(
                "Error in 3D script, line 12: Undefined variable width",
                source="tapir",
                project_name="Chair",
                instruction="修复宽度变量",
            )
            store.record_error(
                "Error in 3D script, line 44: Undefined variable width",
                source="compile",
                project_name="Chair",
            )

            lessons = store.list_error_lessons()
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0].count, 2)
            self.assertEqual(lessons[0].category, "variable_mapping")

            prompt = store.build_skill_prompt(project_name="Chair")
            self.assertIn("learned_gdl_error_avoidance", prompt)
            self.assertIn("出现 2 次", prompt)
            self.assertIn("变量", prompt)

    def test_looks_like_error_report_detects_tapir_message(self):
        self.assertTrue(looks_like_error_report("## 🔴 Archicad GDL 错误报告\nError in 3D script, line 1"))
        self.assertFalse(looks_like_error_report("把椅子做得宽一点"))


if __name__ == "__main__":
    unittest.main()
