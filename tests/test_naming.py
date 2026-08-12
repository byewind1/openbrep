"""P7a 项目命名管线统一：safe_project_name（保中文 sanitize）+ unique_project_name（_vN 后缀）。

规则终表（设计文档 §3.1/§3.3）：
- 只剥文件系统危险字符：/ \\ : * ? " < > | 与控制字符（\x00-\x1f），
  替换为空格并与原有空白一起压成单空格；
- 首尾空格与点剥离；其余字符（含中日韩等 Unicode）逐字符保留；
- 全部剥光 → "未命名构件"（废止 "Imported_GDL" / "Generated_Object"）；
- unique：首个项目保持原名，冲突依次 _v2/_v3……。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from openbrep.naming import DEFAULT_PROJECT_NAME, safe_project_name, unique_project_name


def test_safe_project_name_preserves_chinese():
    assert safe_project_name("书架") == "书架"
    assert safe_project_name("新中式椅子") == "新中式椅子"
    assert safe_project_name("钢结构旋转楼梯_v1") == "钢结构旋转楼梯_v1"


def test_safe_project_name_strips_fs_dangerous_chars():
    # / \ : * ? " < > | 全部剥净（→ 空格并压缩）
    assert safe_project_name("a/b\\c:d*e?f\"g<h>i|j") == "a b c d e f g h i j"
    # 控制字符剥离
    assert safe_project_name("书架\x00版") == "书架 版"
    # 首尾空格与点剥离，内部空白压缩
    assert safe_project_name("  书架.  ") == "书架"
    assert safe_project_name(".新中式椅子..") == "新中式椅子"
    assert safe_project_name("书  架  桌子") == "书 架 桌子"
    # 内部点保留（只剥首尾点）
    assert safe_project_name("Bookshelf.v2") == "Bookshelf.v2"


def test_safe_project_name_fallback_when_everything_stripped():
    assert safe_project_name("///") == DEFAULT_PROJECT_NAME
    assert safe_project_name("  ") == DEFAULT_PROJECT_NAME
    assert safe_project_name("") == DEFAULT_PROJECT_NAME
    assert safe_project_name(None) == DEFAULT_PROJECT_NAME
    assert DEFAULT_PROJECT_NAME == "未命名构件"


def test_safe_project_name_ascii_backward_compatible():
    # 现有合法 ASCII 名逐字符不变
    for name in ("Bookshelf_v1", "Spiral Stair", "PlantPot", "ShelfOrigin", "a-b_c", "OpenBrep_Project"):
        assert safe_project_name(name) == name


def test_unique_project_name_first_keeps_base_name():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        assert unique_project_name("书架", work_dir) == "书架"


def test_unique_project_name_conflicts_get_vN_suffix():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        (work_dir / "书架").mkdir()
        assert unique_project_name("书架", work_dir) == "书架_v2"
        (work_dir / "书架_v2").mkdir()
        assert unique_project_name("书架", work_dir) == "书架_v3"
        (work_dir / "书架_v3").mkdir()
        assert unique_project_name("书架", work_dir) == "书架_v4"


def test_unique_project_name_ascii_uses_vN_too():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        (work_dir / "Bookshelf").mkdir()
        assert unique_project_name("Bookshelf", work_dir) == "Bookshelf_v2"
