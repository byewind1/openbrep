import tempfile
import unittest
from pathlib import Path

from openbrep.knowledge import KNOWLEDGE_SKIP_FILES, KnowledgeBase
from openbrep.knowledge_selector import select_gdl_knowledge


class TestKnowledgeSelector(unittest.TestCase):
    def test_select_bookshelf_knowledge_includes_archetype_and_command_wiki(self):
        root = Path(__file__).parent.parent / "knowledge"

        selection = select_gdl_knowledge(
            instruction="做一个专业一点的参数化书架",
            intent="CREATE",
            knowledge_dir=root,
            base_context="## GDL_quick_reference\n\n基础规则",
        )

        self.assertIn("Archetype: bookshelf", selection.planner_context)
        self.assertIn("Core: core.planning_contract", selection.planner_context)
        self.assertIn("Core: core.parameter_rules", selection.planner_context)
        self.assertIn("GDL 构件规划约定", selection.planner_context)
        self.assertIn("参数化书架", selection.planner_context)
        self.assertIn("Wiki: BLOCK", selection.planner_context)
        self.assertIn("Wiki: ADD_DEL", selection.planner_context)
        self.assertIn("Wiki: FOR_NEXT", selection.planner_context)
        self.assertIn("archetype.bookshelf", selection.source_ids)
        self.assertIn("core.planning_contract", selection.source_ids)
        self.assertIn("core.parameter_rules", selection.source_ids)
        self.assertIn("wiki.BLOCK", selection.source_ids)
        self.assertLessEqual(
            len([sid for sid in selection.source_ids if sid.startswith("wiki.")]),
            5,
        )

    def test_select_profile_object_knowledge_recalls_revolve_wiki(self):
        root = Path(__file__).parent.parent / "knowledge"

        selection = select_gdl_knowledge(
            instruction="做一个旋转体花瓶",
            intent="CREATE",
            knowledge_dir=root,
            base_context="## GDL_quick_reference\n\n基础规则",
        )

        self.assertIn("Wiki: REVOLVE", selection.planner_context)
        self.assertIn("wiki.REVOLVE", selection.source_ids)

    def test_wiki_failure_degrades_without_blocking_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "archetypes").mkdir()
            (root / "archetypes" / "bookshelf.md").write_text(
                "# 参数化书架\n\n使用板式结构。",
                encoding="utf-8",
            )
            (root / "wiki").write_text("not a directory", encoding="utf-8")

            selection = select_gdl_knowledge(
                instruction="做一个书架",
                intent="CREATE",
                knowledge_dir=root,
                base_context="## GDL_quick_reference\n\n基础规则",
            )

        self.assertIn("Archetype: bookshelf", selection.planner_context)
        self.assertNotIn("Wiki:", selection.planner_context)

    def test_select_cabinet_knowledge_includes_cabinet_archetype(self):
        root = Path(__file__).parent.parent / "knowledge"

        selection = select_gdl_knowledge(
            instruction="生成一个带门板和层板的收纳柜",
            intent="CREATE",
            knowledge_dir=root,
            base_context="## GDL_quick_reference\n\n基础规则",
        )

        self.assertIn("Archetype: cabinet", selection.planner_context)
        self.assertIn("参数化柜体", selection.planner_context)
        self.assertIn("Wiki: BLOCK", selection.planner_context)

    def test_select_table_door_window_and_profile_archetypes(self):
        root = Path(__file__).parent.parent / "knowledge"

        cases = [
            ("做一个会议桌", "Archetype: table", "参数化桌子"),
            ("生成一个带门框的门", "Archetype: door", "参数化门"),
            ("生成一个三分格窗户", "Archetype: window", "参数化窗"),
            ("做一个旋转体花瓶", "Archetype: profile_object", "剖面/旋转/放样构件"),
        ]

        for instruction, marker, title in cases:
            with self.subTest(instruction=instruction):
                selection = select_gdl_knowledge(
                    instruction=instruction,
                    intent="CREATE",
                    knowledge_dir=root,
                    base_context="## GDL_quick_reference\n\n基础规则",
                )
                self.assertIn(marker, selection.planner_context)
                self.assertIn(title, selection.planner_context)

    def test_generation_context_keeps_project_knowledge_priority(self):
        root = Path(__file__).parent.parent / "knowledge"

        selection = select_gdl_knowledge(
            instruction="生成一个书架",
            intent="CREATE",
            knowledge_dir=root,
            base_context="## GDL_quick_reference\n\n基础规则",
            project_context="## Project Context\n\n- project.name: 住宅收纳",
            project_knowledge="层板数量必须由项目参数驱动。",
        )

        self.assertLess(
            selection.generation_context.index("Project Context"),
            selection.generation_context.index("Archetype: bookshelf"),
        )
        self.assertIn("层板数量必须由项目参数驱动", selection.generation_context)


class TestKnowledgeBaseNoiseFiltering(unittest.TestCase):
    def test_load_skips_agent_and_maintenance_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "GDL_quick_reference.md").write_text("real knowledge", encoding="utf-8")
            (root / "CLAUDE.md").write_text("agent notes", encoding="utf-8")
            (root / "README.md").write_text("readme noise", encoding="utf-8")
            (root / "AGENTS.md").write_text("agent rules", encoding="utf-8")
            (root / "index.md").write_text("index noise", encoding="utf-8")
            (root / "log.md").write_text("maintenance log", encoding="utf-8")

            kb = KnowledgeBase(str(root))
            kb.load()

        self.assertIn("GDL_quick_reference", kb.doc_names)
        self.assertNotIn("CLAUDE", kb.doc_names)
        self.assertNotIn("README", kb.doc_names)
        self.assertNotIn("AGENTS", kb.doc_names)
        self.assertNotIn("index", kb.doc_names)
        self.assertNotIn("log", kb.doc_names)

    def test_skip_file_policy_is_module_level_and_filename_based(self):
        self.assertIn("CLAUDE.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("README.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("AGENTS.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("index.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("log.md", KNOWLEDGE_SKIP_FILES)


if __name__ == "__main__":
    unittest.main()
