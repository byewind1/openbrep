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


if __name__ == "__main__":
    unittest.main()
