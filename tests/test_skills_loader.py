import tempfile
import unittest
from pathlib import Path

from openbrep.skills_loader import SkillsLoader


class TestSkillsLoader(unittest.TestCase):
    def test_custom_skill_matches_instruction_by_activation_keywords_without_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "project_style.md").write_text(
                "# 门窗项目规范\n\n"
                "## 触发关键词 / Activation Keywords\n"
                "- 门窗\n"
                "- 窗户\n"
                "- window\n\n"
                "## 常用模式\n"
                "铝合金窗框统一使用 frame_width 参数。\n",
                encoding="utf-8",
            )

            result = SkillsLoader(str(skills_dir)).get_for_task("生成一个铝合金窗户")

        self.assertIn("## Skill: project_style", result)
        self.assertIn("铝合金窗框", result)

    def test_custom_skill_matches_instruction_by_body_content_without_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "my_project_skill.md").write_text(
                "# 项目规范\n\n"
                "## 常用模式\n"
                "书架 shelf 层板 shelf_board 使用等距 FOR/NEXT 生成。\n",
                encoding="utf-8",
            )

            result = SkillsLoader(str(skills_dir)).get_for_task("生成一个三层书架")

        self.assertIn("## Skill: my_project_skill", result)
        self.assertIn("层板", result)

    def test_unrelated_custom_skill_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "window_style.md").write_text(
                "# 门窗规范\n\n"
                "## 触发关键词 / Activation Keywords\n"
                "- 门窗\n"
                "- 窗户\n",
                encoding="utf-8",
            )

            result = SkillsLoader(str(skills_dir)).get_for_task("生成一个三层书架")

        self.assertNotIn("window_style", result)

    def test_builtin_task_skill_still_loads_for_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "create_object.md").write_text("# Create Object\n\n内置创建规则", encoding="utf-8")

            result = SkillsLoader(str(skills_dir)).get_for_task("create a chair")

        self.assertIn("## Skill: create_object", result)
        self.assertIn("内置创建规则", result)

    def test_readme_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "README.md").write_text("# docs", encoding="utf-8")
            loader = SkillsLoader(str(skills_dir))
            loader.load()

        self.assertEqual(loader.skill_names, [])


if __name__ == "__main__":
    unittest.main()
