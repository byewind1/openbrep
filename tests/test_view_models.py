import unittest

from ui.view_models import classify_code_blocks


class TestViewModelsCodeExtraction(unittest.TestCase):
    def test_fenced_script_block_drops_markdown_explanation_after_separator(self):
        text = """\
```gdl
! v4 2026-04-29 3D Script
TOLER 0.001
BLOCK A, B, ZZYZX
END

---

## 修改说明
不要写入脚本
```
"""

        extracted = classify_code_blocks(text)

        self.assertEqual(set(extracted), {"scripts/3d.gdl"})
        self.assertTrue(extracted["scripts/3d.gdl"].endswith("END"))
        self.assertNotIn("修改说明", extracted["scripts/3d.gdl"])

    def test_raw_script_reply_without_fence_is_extractable_and_truncated(self):
        text = """\
! v4 2026-04-29 3D Script
TOLER 0.001
MATERIAL mat_frame
BLOCK A, B, ZZYZX
END

---

## 修改说明
| 问题 | 修复 |
|------|------|
"""

        extracted = classify_code_blocks(text)

        self.assertEqual(set(extracted), {"scripts/3d.gdl"})
        script = extracted["scripts/3d.gdl"]
        self.assertIn("TOLER 0.001", script)
        self.assertTrue(script.endswith("END"))
        self.assertNotIn("| 问题 | 修复 |", script)


if __name__ == "__main__":
    unittest.main()
