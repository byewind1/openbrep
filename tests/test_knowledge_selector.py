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
            (root / ".DS_Store").write_text("finder metadata", encoding="utf-8")

            kb = KnowledgeBase(str(root))
            kb.load()

        self.assertIn("GDL_quick_reference", kb.doc_names)
        self.assertNotIn("CLAUDE", kb.doc_names)
        self.assertNotIn("README", kb.doc_names)
        self.assertNotIn("AGENTS", kb.doc_names)
        self.assertNotIn("index", kb.doc_names)
        self.assertNotIn("log", kb.doc_names)
        self.assertNotIn(".DS_Store", kb.doc_names)

    def test_skip_file_policy_is_module_level_and_filename_based(self):
        self.assertIn("CLAUDE.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("README.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("AGENTS.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("index.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn("log.md", KNOWLEDGE_SKIP_FILES)
        self.assertIn(".DS_Store", KNOWLEDGE_SKIP_FILES)


if __name__ == "__main__":
    unittest.main()


# ── examples 注入（Phase 2b）──────────────────────────────


def test_examples_injected_for_matching_object(tmp_path) -> None:
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="做一个宽600深300的书架",
        intent="create",
        knowledge_dir="./knowledge",
    )
    example_ids = [s for s in selection.source_ids if s.startswith("example.")]
    assert "example.bookshelf_shelf_loop" in example_ids
    assert "FOR" in selection.generation_context


# ── HF6：命令选择规则文档链路（AC-1/AC-2/AC-3） ──────────────────────


def test_command_selection_doc_survives_create_planner_and_generation() -> None:
    """AC-3：CREATE 的 planner 与 generation 两路 prompt 都含规则文档。

    同时验证文档整份存活（正文不含会被 _split_markdown_sections
    切碎的 `\n---\n` 分隔线，也不被 core/ 预算截断）。
    """
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="做一个旋转体花瓶",
        intent="CREATE",
        knowledge_dir="./knowledge",
        base_context="## GDL_quick_reference\n\n基础规则",
    )
    assert "复杂度阶梯（能低不高）" in selection.planner_context
    assert "复杂度阶梯（能低不高）" in selection.generation_context
    assert "审查模式（优化 / 审查请求专用）" in selection.generation_context
    assert "core.command_selection" in selection.source_ids
    # 关键小节都在（没有被 4000 字符预算挤掉）
    for marker in (
        "减法与加法取舍",
        "建模前决策清单",
        "BLOCK / BRICK / CPRISM_",
        "先用低路径点数出可编译版本",
    ):
        assert marker in selection.planner_context, marker


def test_command_selection_doc_survives_modify_both_contexts() -> None:
    """AC-3：MODIFY 的 planner 与 generation（agent loop 只消费 generation）
    都含规则文档——MODIFY 路径零覆盖问题被修复。
    """
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="把柜门改成推拉门",
        intent="modify",
        knowledge_dir="./knowledge",
        base_context="## GDL_parameters\n\n参数规则",
    )
    assert "复杂度阶梯（能低不高）" in selection.generation_context
    assert "复杂度阶梯（能低不高）" in selection.planner_context
    assert "core.command_selection" in selection.source_ids


def test_review_trigger_words_matching() -> None:
    """AC-4：优化/审查触发词命中口径（一处常量走的保守子串/正则匹配）。"""
    from openbrep.knowledge_selector import _hit_review_trigger

    hits = [
        "帮我优化这个柜子的建模方式",
        "审查一下当前脚本",
        "请检查 3d.gdl 有没有更好的选型",
        "refactor 这段代码",
        "Review the modeling approach",
        "精简这个构件的几何",
    ]
    misses = [
        "把宽度改成 2 米",
        "增加一个层板",
        "帮我解释一下这个参数",
        "生成一个桌子",
    ]
    for text in hits:
        assert _hit_review_trigger(text), text
    for text in misses:
        assert not _hit_review_trigger(text), text


def test_wiki_truncation_keeps_tail_selection_advice() -> None:
    """AC-2.4：wiki 页截断不再误伤页尾选择建议段
    （Edge Cases & Traps / Optimization）。
    """
    from pathlib import Path

    from openbrep.knowledge_selector import _load_wiki_context

    root = Path(__file__).parent.parent / "knowledge"
    context, sources = _load_wiki_context(
        root,
        instruction="做一个旋转体花瓶",
        task_type="create",
        object_keys=["profile_object"],
        max_pages=5,
        max_chars_per_page=700,  # 远小于 REVOLVE 页长（~3.9k），必触发截断
    )
    assert "wiki.REVOLVE" in sources
    assert "Edge Cases & Traps" in context
    assert "[truncated]" in context
    assert "REVOLVE\n\n" in context or "REVOLVE" in context


def test_wiki_short_page_not_truncated() -> None:
    """预算充足时不截断、不加标记。"""
    from pathlib import Path

    from openbrep.knowledge_selector import _load_wiki_context

    root = Path(__file__).parent.parent / "knowledge"
    context, _ = _load_wiki_context(
        root,
        instruction="BLOCK 怎么用",
        task_type="create",
        object_keys=["bookshelf"],
        max_pages=1,
        max_chars_per_page=100000,
    )
    assert "[truncated]" not in context


def test_examples_matched_by_command_token() -> None:
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="用 REVOLVE 做一个花瓶",
        intent="create",
        knowledge_dir="./knowledge",
    )
    assert "example.revolve_vase" in selection.source_ids


def test_examples_not_injected_when_no_match() -> None:
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="完全无关的问题 qwerty",
        intent="create",
        knowledge_dir="./knowledge",
    )
    assert not [s for s in selection.source_ids if s.startswith("example.")]


def test_examples_capped_at_two() -> None:
    from openbrep.knowledge_selector import select_gdl_knowledge

    selection = select_gdl_knowledge(
        instruction="做一个书架、书柜、桌子、门、材质都要 REVOLVE PRISM_ CUTPLANE",
        intent="create",
        knowledge_dir="./knowledge",
    )
    example_ids = [s for s in selection.source_ids if s.startswith("example.")]
    assert 0 < len(example_ids) <= 2
