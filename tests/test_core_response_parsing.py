import unittest
from unittest.mock import MagicMock

from openbrep.core import GDLAgent


class TestCoreResponseParsing(unittest.TestCase):
    def test_file_block_parser_drops_markdown_explanation_after_separator(self):
        response = """\
[FILE: scripts/3d.gdl]
! v4 2026-04-29 3D Script
TOLER 0.001
BLOCK A, B, ZZYZX
END

---

## 修改说明
不要写入脚本
"""

        changes = GDLAgent(llm=MagicMock())._parse_response(response)

        self.assertEqual(set(changes), {"scripts/3d.gdl"})
        self.assertTrue(changes["scripts/3d.gdl"].endswith("END"))
        self.assertNotIn("修改说明", changes["scripts/3d.gdl"])

    def test_parser_infers_gdl_fenced_block_without_file_header(self):
        response = """\
这里是 3D 脚本：

```gdl
TOLER 0.001
BLOCK A, B, ZZYZX
END
```
"""

        changes = GDLAgent(llm=MagicMock())._parse_response(response)

        self.assertEqual(set(changes), {"scripts/3d.gdl"})
        self.assertIn("BLOCK A, B, ZZYZX", changes["scripts/3d.gdl"])

    def test_parser_infers_paramlist_fenced_block_without_file_header(self):
        response = """\
```text
Length A = 1.2 ! Width
Length B = 0.4 ! Depth
Integer shelf_count = 5 ! Shelves
```
"""

        changes = GDLAgent(llm=MagicMock())._parse_response(response)

        self.assertEqual(set(changes), {"paramlist.xml"})
        self.assertIn("Length A", changes["paramlist.xml"])

    def test_parser_merges_split_file_header(self):
        # LLM 偶发把 [FILE: path 与 ] 拆到两行（2026-08-06 任务 H 归因）：
        # 2D 块不应被吞进上一个文件。
        response = """\
[FILE: scripts/3d.gdl]
BLOCK A, B, ZZYZX
[FILE: scripts/2d.gdl
]
PROJECT2 3, 270, 2
"""

        changes = GDLAgent(llm=MagicMock())._parse_response(response)

        self.assertEqual(set(changes), {"scripts/3d.gdl", "scripts/2d.gdl"})
        self.assertNotIn("PROJECT2", changes["scripts/3d.gdl"])
        self.assertIn("PROJECT2 3, 270, 2", changes["scripts/2d.gdl"])

    def test_parser_merges_split_file_header_with_trailing_content(self):
        # 变体：] 与内容同行（] HOTSPOT2 ...）
        response = """\
[FILE: scripts/3d.gdl]
BLOCK A, B, ZZYZX
[FILE: scripts/2d.gdl
] HOTSPOT2 0, 0
PROJECT2 3, 270, 2
"""

        changes = GDLAgent(llm=MagicMock())._parse_response(response)

        self.assertEqual(set(changes), {"scripts/3d.gdl", "scripts/2d.gdl"})
        self.assertIn("HOTSPOT2 0, 0", changes["scripts/2d.gdl"])


if __name__ == "__main__":
    unittest.main()
