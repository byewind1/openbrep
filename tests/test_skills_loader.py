import tempfile
import unittest
from datetime import date
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

    def test_pro_skill_layer_matches_instruction_without_public_skill_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            pro_dir = skills_dir / "pro"
            pro_dir.mkdir()
            (pro_dir / "gdl_stair.md").write_text(
                "# GDL Stair Pro Skill\n\n"
                "## Activation Keywords\n"
                "- 楼梯\n"
                "- spiral stair\n\n"
                "## Method\n"
                "Use PUT/GET with bPRISM_ for spiral stairs.\n",
                encoding="utf-8",
            )

            result = SkillsLoader(str(skills_dir)).get_for_task("生成一个螺旋楼梯")

        self.assertIn("## Skill: gdl_stair", result)
        self.assertIn("bPRISM_", result)

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

    # ── frontmatter / 状态过滤 / 复用计数 ───────────────────

    def test_legacy_skill_without_frontmatter_injects_byte_identical_and_default_meta(self):
        """无 frontmatter 旧 skill：注入正文无 frontmatter 残留；skill_meta 返回默认 active。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            content = (
                "# 门窗项目规范\n\n"
                "## 触发关键词 / Activation Keywords\n"
                "- 门窗\n"
                "- 窗户\n\n"
                "## 常用模式\n"
                "铝合金窗框统一使用 frame_width 参数。\n"
            )
            (skills_dir / "project_style.md").write_text(content, encoding="utf-8")
            loader = SkillsLoader(str(skills_dir))

            result = loader.get_for_task("生成一个铝合金门窗")

        self.assertIn("## Skill: project_style", result)
        self.assertIn("铝合金窗框", result)
        self.assertNotIn("---", result)  # 正文无 frontmatter 残留
        meta = loader.skill_meta("project_style")
        self.assertEqual(meta["status"], "active")
        self.assertEqual(meta["skill_version"], 0)
        self.assertEqual(meta["pattern_type"], None)
        self.assertEqual(meta["source_project"], None)
        self.assertEqual(meta["source_trace_id"], None)
        self.assertEqual(meta["verified_evidence"], None)
        self.assertEqual(meta["reuse_count"], 0)
        self.assertIsNone(meta["last_used"])

    def test_status_filter_skips_proposed_deprecated_but_lists_them_and_injects_verified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "proposed_skill.md").write_text(
                "---\nstatus: proposed\nskill_version: 1\n---\n\n"
                "# Proposed\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            (skills_dir / "deprecated_skill.md").write_text(
                "---\nstatus: deprecated\n---\n\n"
                "# Deprecated\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            (skills_dir / "verified_skill.md").write_text(
                "---\nstatus: verified\n---\n\n"
                "# Verified\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            (skills_dir / "active_skill.md").write_text(
                "---\nstatus: active\n---\n\n"
                "# Active\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            loader = SkillsLoader(str(skills_dir))

            result = loader.get_for_task("生成一个铝合金门窗")
            get_by_name = loader.get_by_name

        self.assertIn("## Skill: verified_skill", result)
        self.assertIn("## Skill: active_skill", result)
        self.assertNotIn("proposed_skill", result)
        self.assertNotIn("deprecated_skill", result)
        # 管理面仍列出全部（含 proposed / deprecated）
        self.assertEqual(
            sorted(loader.skill_names),
            ["active_skill", "deprecated_skill", "proposed_skill", "verified_skill"],
        )
        self.assertEqual(loader.skill_names_by_status("proposed"), ["proposed_skill"])
        self.assertEqual(loader.skill_names_by_status("deprecated"), ["deprecated_skill"])
        # get_by_name 同样做注入级过滤
        self.assertIsNone(get_by_name("proposed_skill"))
        self.assertIsNone(get_by_name("deprecated_skill"))
        self.assertIsNotNone(get_by_name("verified_skill"))
        self.assertIsNotNone(get_by_name("active_skill"))

    def test_bad_yaml_frontmatter_is_treated_as_no_frontmatter(self):
        """frontmatter 坏 YAML：不炸，按无 frontmatter 处理（status=active 且注入）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "broken_skill.md").write_text(
                "---\n- item1\n- item2\n---\n\n"
                "# Broken\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            loader = SkillsLoader(str(skills_dir))

            result = loader.get_for_task("生成一个铝合金门窗")

        self.assertIn("## Skill: broken_skill", result)
        self.assertEqual(loader.skill_meta("broken_skill")["status"], "active")
        self.assertEqual(loader.skill_meta("broken_skill")["reuse_count"], 0)

    def test_reuse_counting_increments_dedupes_and_skips_legacy_files(self):
        """复用计数：命中写回 reuse_count/last_used；同 loader 生命周期不重复计；
        无 frontmatter 旧文件不被改写。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "counted_skill.md"
            skill_file.write_text(
                "---\nstatus: active\nreuse_count: 0\n---\n\n"
                "# Counted\n\n## 触发关键词\n- 门窗\n",
                encoding="utf-8",
            )
            legacy_file = skills_dir / "legacy_skill.md"
            legacy_content = "# Legacy\n\n## 触发关键词\n- 门窗\n"
            legacy_file.write_text(legacy_content, encoding="utf-8")
            legacy_before = legacy_file.read_bytes()

            loader = SkillsLoader(str(skills_dir))
            loader.get_for_task("生成一个铝合金门窗")

            # 命中后 reuse_count 0→1、last_used 写入，其余 frontmatter 行不动
            meta = loader.skill_meta("counted_skill")
            self.assertEqual(meta["reuse_count"], 1)
            self.assertEqual(meta["last_used"], date.today().isoformat())
            self.assertEqual(meta["status"], "active")
            on_disk = skill_file.read_text(encoding="utf-8")
            self.assertIn("reuse_count: 1", on_disk)
            self.assertIn(f"last_used: {date.today().isoformat()}", on_disk)
            self.assertIn("status: active\n", on_disk)

            # 同一 loader 实例重复调用不重复计数
            loader.get_for_task("生成一个铝合金门窗")
            self.assertEqual(loader.skill_meta("counted_skill")["reuse_count"], 1)
            self.assertIn("reuse_count: 1", skill_file.read_text(encoding="utf-8"))

            # 无 frontmatter 旧文件不被改写
            self.assertEqual(legacy_file.read_bytes(), legacy_before)

    def test_frontmatter_file_without_reuse_fields_is_not_rewritten(self):
        """有 frontmatter 但没接入复用字段（如存量 skill_dougong.md）→ 注入但不改写。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "legacy_meta_skill.md"
            original = (
                "---\n"
                "title: Legacy Skill\n"
                "version: 1.0.0\n"
                "author: someone\n"
                "---\n\n"
                "# Legacy Meta\n\n## 触发关键词\n- 门窗\n"
            )
            skill_file.write_text(original, encoding="utf-8")
            loader = SkillsLoader(str(skills_dir))

            result = loader.get_for_task("生成一个铝合金门窗")
            self.assertIn("## Skill: legacy_meta_skill", result)  # 正常注入
            self.assertEqual(skill_file.read_text(encoding="utf-8"), original)  # 字节级不变
            self.assertEqual(loader.skill_meta("legacy_meta_skill")["reuse_count"], 0)


if __name__ == "__main__":
    unittest.main()
