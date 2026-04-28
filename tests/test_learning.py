import tempfile
import unittest

from openbrep.learning import (
    ErrorLearningStore,
    classify_error,
    error_fingerprint,
    looks_like_error_report,
    seed_error_lessons,
)


class TestErrorLearning(unittest.TestCase):
    def test_classify_common_archicad_errors(self):
        self.assertEqual(classify_error("Error in 3D script, line 12: ENDIF expected"), "control_flow_closure")
        self.assertEqual(classify_error("Undefined variable seatH"), "variable_mapping")
        self.assertEqual(classify_error("Wrong number of arguments in PRISM_"), "command_arguments")
        self.assertEqual(
            classify_error("文件《钢结构节点_v4.gsm》存在两类问题:3D脚本第75、80行出现“缺少CALL关键字(不推荐写法)”"),
            "missing_call_keyword",
        )

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

    def test_seed_call_keyword_lesson_is_injected_without_local_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ErrorLearningStore(tmpdir)

            self.assertEqual(store.list_error_lessons(), [])
            prompt = store.build_skill_prompt()

            self.assertIn("learned_gdl_error_avoidance", prompt)
            self.assertIn("内置第一条错题", prompt)
            self.assertIn("缺少 CALL", prompt)
            self.assertIn("显式使用 CALL", prompt)

    def test_seed_lessons_are_available_as_first_self_improvement_constraint(self):
        lessons = seed_error_lessons()

        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].category, "missing_call_keyword")
        self.assertEqual(lessons[0].source, "openbrep_seed_lesson")

    def test_looks_like_error_report_detects_tapir_message(self):
        self.assertTrue(looks_like_error_report("## 🔴 Archicad GDL 错误报告\nError in 3D script, line 1"))
        self.assertTrue(looks_like_error_report("文件《钢结构节点_v4.gsm》存在两类问题:3D脚本第75、80行出现“缺少CALL关键字(不推荐写法)”"))
        self.assertFalse(looks_like_error_report("把椅子做得宽一点"))

    def test_user_summarized_gdl_copilot_report_becomes_strong_prompt_constraint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ErrorLearningStore(tmpdir)
            store.record_error(
                "文件《钢结构节点_v4.gsm》存在两类问题:3D脚本第75、80、85、90行出现“缺少CALL关键字(不推荐写法)”;Master脚本第4、5、6、8、9、10、12、13、14、16、28行出现类似问题",
                source="user_summary",
                project_name="钢结构节点",
            )

            lessons = store.list_error_lessons()
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0].category, "missing_call_keyword")
            self.assertIn("钢结构节点_v4.gsm", lessons[0].summary)
            self.assertIn("CALL", lessons[0].guidance)

            prompt = store.build_skill_prompt(project_name="钢结构节点")
            self.assertIn("缺少 CALL", prompt)
            self.assertIn("显式使用 CALL", prompt)


if __name__ == "__main__":
    unittest.main()
