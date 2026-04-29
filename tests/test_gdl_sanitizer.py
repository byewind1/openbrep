import unittest

from openbrep.gdl_sanitizer import sanitize_llm_script_output


class TestGDLSanitizer(unittest.TestCase):
    def test_script_output_truncates_markdown_explanation_after_separator(self):
        raw = """\
! v4 2026-04-29 3D Script
TOLER 0.001
BLOCK A, B, ZZYZX
END

---

## 修改说明
这部分不应进入脚本编辑栏。
"""

        cleaned = sanitize_llm_script_output(raw, "scripts/3d.gdl")

        self.assertIn("BLOCK A, B, ZZYZX", cleaned)
        self.assertTrue(cleaned.endswith("END"))
        self.assertNotIn("修改说明", cleaned)

    def test_script_comments_with_long_dash_text_are_preserved(self):
        raw = """\
! ---- 派生变量 ----
BLOCK A, B, ZZYZX
END
"""

        cleaned = sanitize_llm_script_output(raw, "scripts/3d.gdl")

        self.assertIn("! ---- 派生变量 ----", cleaned)


if __name__ == "__main__":
    unittest.main()
