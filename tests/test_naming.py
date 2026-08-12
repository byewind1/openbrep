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

from openbrep.naming import (
    DEFAULT_PROJECT_NAME,
    project_name_from_prompt,
    safe_project_name,
    unique_project_name,
)


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


# ── P7b：规则提取（project_name_from_prompt）──────────────────────

def test_project_name_from_prompt_strips_image_tokens():
    # [图N] 括号形式与裸 图N 都剥
    assert project_name_from_prompt("[图1][图2]生成一个书架") == "书架"
    assert project_name_from_prompt("参考图1生成中国古建筑斗拱中坐斗gdl构件") == "参考生成中国古建筑斗拱中"
    assert project_name_from_prompt("[图1]帮我做一把椅子") == "一把椅子"


def test_project_name_from_prompt_strips_verbs_and_politeness():
    assert project_name_from_prompt("生成一个参数化书架") == "参数化书架"
    assert project_name_from_prompt("帮我做一个圆形花盆") == "圆形花盆"
    assert project_name_from_prompt("帮忙生成一个桌子") == "桌子"
    assert project_name_from_prompt("请创建衣柜") == "衣柜"
    assert project_name_from_prompt("新建一个楼梯") == "楼梯"
    assert project_name_from_prompt("重做一个柜子") == "柜子"
    assert project_name_from_prompt("制作一个床头柜") == "床头柜"  # 制作 不被 做 抢先截断
    assert project_name_from_prompt("做一个柜子") == "柜子"


def test_project_name_from_prompt_strips_quantifiers():
    assert project_name_from_prompt("生成一个书架") == "书架"
    assert project_name_from_prompt("生成一只花瓶") == "花瓶"
    assert project_name_from_prompt("生成这款沙发") == "沙发"
    assert project_name_from_prompt("生成这个茶几") == "茶几"
    assert project_name_from_prompt("生成个垃圾桶") == "垃圾桶"
    assert project_name_from_prompt("生成只猫爬架") == "猫爬架"
    assert project_name_from_prompt("生成把雨伞") == "雨伞"


def test_project_name_from_prompt_truncates_to_12_chars():
    # 中英文都按字符计
    assert len(project_name_from_prompt("生成一个非常非常非常长的参数化书架")) == 12
    assert project_name_from_prompt("create a parameterized bookshelf") == "create a par"
    # 不足 12 字不截断
    assert project_name_from_prompt("生成一个中国古建筑斗拱坐斗构件") == "中国古建筑斗拱坐斗构件"


def test_project_name_from_prompt_empty_returns_empty_string():
    # 空/纯前缀 → ""，由上层 safe_project_name 兜底到 未命名构件
    assert project_name_from_prompt("") == ""
    assert project_name_from_prompt("生成一个") == ""
    assert project_name_from_prompt("   ") == ""
    assert project_name_from_prompt(None) == ""
    # 全剥空后的兜底由 safe_project_name 接住
    assert safe_project_name(project_name_from_prompt("生成一个") or "") == DEFAULT_PROJECT_NAME


def test_project_name_from_prompt_keeps_leading_context_words():
    # 句首不是动词不剥，取前 12 字（规则提取只是回退，不必追求完美语义）
    assert project_name_from_prompt("参考生成中国古建筑斗拱中坐斗") == "参考生成中国古建筑斗拱中"
    assert project_name_from_prompt("按图生成书架") == "按图生成书架"
