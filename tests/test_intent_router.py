"""
Tests for openbrep.runtime.router.IntentRouter and helpers.

Key scenarios:
- "检查" alone must NOT route to DEBUG (was causing MODIFY → DEBUG misrouting)
- Explicit error log in text DOES route to DEBUG even with weak keywords
- Strong debug keywords (bug/crash/报错) always route to DEBUG
- Modify-flavoured "检查" routes to MODIFY
"""

import unittest

from openbrep.runtime.router import IntentRouter, _is_debug_intent


class TestIsDebugIntent(unittest.TestCase):
    """Unit tests for the _is_debug_intent() classifier."""

    # ── Strong keywords (always DEBUG) ──────────────────────────

    def test_strong_keyword_bug(self):
        self.assertTrue(_is_debug_intent("这里有个bug"))

    def test_strong_keyword_crash(self):
        self.assertTrue(_is_debug_intent("程序crash了"))

    def test_strong_keyword_出错(self):
        self.assertTrue(_is_debug_intent("运行出错了"))

    def test_strong_keyword_报错(self):
        self.assertTrue(_is_debug_intent("编译报错"))

    def test_strong_keyword_debug(self):
        self.assertTrue(_is_debug_intent("debug this script"))

    # ── Weak keywords without error log → NOT DEBUG ─────────────

    def test_weak_fix_without_error_log_not_debug(self):
        """'fix the width' — no error log signature → should NOT route to DEBUG."""
        self.assertFalse(_is_debug_intent("帮我fix一下宽度"))

    def test_weak_wrong_without_error_log_not_debug(self):
        self.assertFalse(_is_debug_intent("something is wrong with the style"))

    def test_weak_问题_without_error_log_not_debug(self):
        self.assertFalse(_is_debug_intent("这段脚本有点问题"))

    def test_weak_错误_without_error_log_not_debug(self):
        self.assertFalse(_is_debug_intent("好像有点错误"))

    # ── Weak keywords WITH error log → DEBUG ────────────────────

    def test_weak_fix_with_error_colon_is_debug(self):
        self.assertTrue(_is_debug_intent("fix this error:\nundefined variable 'W'"))

    def test_weak_error_with_line_number_is_debug(self):
        self.assertTrue(_is_debug_intent("error on line 42: ENDIF expected"))

    def test_weak_issue_with_lp_format_is_debug(self):
        self.assertTrue(_is_debug_intent("(15) : Missing ENDIF\nfix it"))

    def test_weak_fail_with_编译失败_is_debug(self):
        self.assertTrue(_is_debug_intent("编译失败 fail"))

    # ── Archicad error pattern ──────────────────────────────────

    def test_archicad_error_pattern_is_debug(self):
        self.assertTrue(_is_debug_intent(
            "Error in 3D Script, line 12: undefined variable"
        ))

    # ── Editor prefix ───────────────────────────────────────────

    def test_debug_editor_prefix_is_debug(self):
        self.assertTrue(_is_debug_intent("[DEBUG:editor]帮我看一下"))

    # ── "检查" alone is NOT debug ────────────────────────────────

    def test_检查_alone_not_debug(self):
        """Core regression: '检查' must NOT trigger DEBUG routing."""
        self.assertFalse(_is_debug_intent("帮我检查一下这段脚本"))

    def test_看看_not_debug(self):
        self.assertFalse(_is_debug_intent("帮我看看这段代码"))

    def test_分析_not_debug(self):
        self.assertFalse(_is_debug_intent("帮我分析一下逻辑"))

    def test_explain_alone_not_debug(self):
        self.assertFalse(_is_debug_intent("explain this code"))

    def test_why_alone_not_debug(self):
        self.assertFalse(_is_debug_intent("why is this wrong?"))


class TestIntentRouterModifyPriority(unittest.TestCase):
    """'检查' requests must route to MODIFY, not DEBUG."""

    def setUp(self):
        self.router = IntentRouter()

    def test_检查_routes_to_modify(self):
        """'帮我检查' with an open project must become MODIFY, not DEBUG."""
        intent = self.router.classify("帮我检查这段脚本", has_project=True)
        self.assertEqual(intent, "MODIFY", f"Expected MODIFY, got {intent!r}")

    def test_看看_with_project_routes_to_modify(self):
        intent = self.router.classify("帮我看看这段代码", has_project=True)
        self.assertNotEqual(intent, "DEBUG", "看看 alone should not trigger DEBUG")

    def test_error_log_text_routes_to_debug(self):
        """Message with an explicit error log must route to DEBUG."""
        text = "编译报错了，麻烦帮我修一下：\nerror: line 5: ENDIF expected"
        intent = self.router.classify(text, has_project=True)
        self.assertEqual(intent, "DEBUG", f"Expected DEBUG for error log text, got {intent!r}")

    def test_create_intent_with_gdl_keyword_routes_to_create(self):
        intent = self.router.classify("生成一个书架构件", has_project=False)
        self.assertEqual(intent, "CREATE")

    def test_pure_chat_routes_to_chat(self):
        intent = self.router.classify("你好！", has_project=False)
        self.assertEqual(intent, "CHAT")


if __name__ == "__main__":
    unittest.main()
