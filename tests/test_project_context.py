import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.hsf_project import HSFProject
from openbrep.project_context import (
    append_project_decision,
    build_project_context_prompt,
    load_project_memory,
    load_project_knowledge,
    load_project_skills,
    resolve_project_context,
)
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


class TestProjectContext(unittest.TestCase):
    def test_project_context_reads_openbrep_project_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("chair", work_dir=tmpdir)
            project.save_to_disk()
            meta_dir = Path(project.root) / ".openbrep"
            meta_dir.mkdir(parents=True)
            (meta_dir / "project.toml").write_text(
                "[project]\n"
                'name = "工程椅"\n'
                'archicad_version = "27"\n'
                "\n[constraints]\n"
                'units = "meters"\n',
                encoding="utf-8",
            )

            context = resolve_project_context(project)
            prompt = build_project_context_prompt(context)

        self.assertIn("Project Context", prompt)
        self.assertIn("project.name: 工程椅", prompt)
        self.assertIn("constraints.units: meters", prompt)

    def test_project_context_merges_knowledge_project_toml_over_root_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("door", work_dir=tmpdir)
            project.save_to_disk()
            meta_dir = Path(project.root) / ".openbrep"
            knowledge_dir = meta_dir / "knowledge"
            knowledge_dir.mkdir(parents=True)
            (meta_dir / "project.toml").write_text(
                "[project]\n"
                'object_type = "generic door"\n'
                "\n[constraints]\n"
                'handing = "left"\n',
                encoding="utf-8",
            )
            (knowledge_dir / "project.toml").write_text(
                "[project]\n"
                'object_type = "residential interior door"\n'
                "\n[constraints]\n"
                'opening = "inward"\n',
                encoding="utf-8",
            )

            context = resolve_project_context(project)
            prompt = build_project_context_prompt(context)

        self.assertIn("project.object_type: residential interior door", prompt)
        self.assertIn("constraints.handing: left", prompt)
        self.assertIn("constraints.opening: inward", prompt)

    def test_project_context_loads_project_knowledge_and_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.save_to_disk()
            meta_dir = Path(project.root) / ".openbrep"
            knowledge_dir = meta_dir / "knowledge"
            skills_dir = meta_dir / "skills"
            knowledge_dir.mkdir(parents=True)
            skills_dir.mkdir(parents=True)
            (knowledge_dir / "bookshelf_rules.md").write_text(
                "书架项目必须包含侧板、顶板、底板和可参数化层板。",
                encoding="utf-8",
            )
            (skills_dir / "create_object.md").write_text(
                "项目默认使用模块化参数，不生成一次性固定尺寸几何。",
                encoding="utf-8",
            )

            context = resolve_project_context(project)
            knowledge = load_project_knowledge(context, task_type="create")
            skills = load_project_skills(context, "生成一个书架")

        self.assertIn("可参数化层板", knowledge)
        self.assertIn("模块化参数", skills)

    def test_project_decision_memory_appends_and_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.save_to_disk()
            context = resolve_project_context(project)

            path = append_project_decision(
                context,
                summary="**变更摘要：**\n- 新增参数：shelf_count",
                intent="MODIFY",
                instruction="增加层板数量参数",
                changed_files=["paramlist.xml", "scripts/3d.gdl"],
                revision_id="r0002",
            )
            memory = load_project_memory(context)
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())

        self.assertIn("Project Memory: decisions", memory)
        self.assertIn("增加层板数量参数", memory)
        self.assertIn("shelf_count", memory)

    def test_project_knowledge_manifest_filters_and_sorts_docs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.save_to_disk()
            knowledge_dir = Path(project.root) / ".openbrep" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            (knowledge_dir / "create_rules.md").write_text("create priority 10", encoding="utf-8")
            (knowledge_dir / "project_rules.md").write_text("all priority 100", encoding="utf-8")
            (knowledge_dir / "debug_rules.md").write_text("debug only", encoding="utf-8")
            (knowledge_dir / "manifest.toml").write_text(
                '[[docs]]\n'
                'id = "project.create"\n'
                'path = "create_rules.md"\n'
                'task_types = ["create"]\n'
                "priority = 10\n"
                "\n[[docs]]\n"
                'id = "project.all"\n'
                'path = "project_rules.md"\n'
                'task_types = ["all"]\n'
                "priority = 100\n"
                "\n[[docs]]\n"
                'id = "project.debug"\n'
                'path = "debug_rules.md"\n'
                'task_types = ["debug"]\n'
                "priority = 50\n",
                encoding="utf-8",
            )

            context = resolve_project_context(project)
            knowledge = load_project_knowledge(context, task_type="create")

        self.assertIn("Project Knowledge: project.all", knowledge)
        self.assertIn("Project Knowledge: project.create", knowledge)
        self.assertNotIn("debug only", knowledge)
        self.assertLess(knowledge.index("all priority 100"), knowledge.index("create priority 10"))

    def test_project_knowledge_manifest_blocks_paths_outside_knowledge_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.save_to_disk()
            root = Path(project.root)
            knowledge_dir = root / ".openbrep" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            (root / "secret.md").write_text("must not load", encoding="utf-8")
            (knowledge_dir / "safe.md").write_text("safe project rule", encoding="utf-8")
            (knowledge_dir / "manifest.toml").write_text(
                '[[docs]]\n'
                'id = "unsafe"\n'
                'path = "../../secret.md"\n'
                'task_types = ["create"]\n'
                "priority = 100\n"
                "\n[[docs]]\n"
                'id = "safe"\n'
                'path = "safe.md"\n'
                'task_types = ["create"]\n'
                "priority = 1\n",
                encoding="utf-8",
            )

            context = resolve_project_context(project)
            knowledge = load_project_knowledge(context, task_type="create")

        self.assertIn("safe project rule", knowledge)
        self.assertNotIn("must not load", knowledge)

    def test_pipeline_injects_project_context_knowledge_and_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.save_to_disk()
            meta_dir = Path(project.root) / ".openbrep"
            (meta_dir / "knowledge").mkdir(parents=True)
            (meta_dir / "skills").mkdir(parents=True)
            (meta_dir / "project.toml").write_text(
                "[project]\n"
                'summary = "用于住宅收纳系统的参数化书架"\n',
                encoding="utf-8",
            )
            (meta_dir / "knowledge" / "bookshelf_rules.md").write_text(
                "层板数量必须由参数驱动。",
                encoding="utf-8",
            )
            append_project_decision(
                resolve_project_context(project),
                summary="**变更摘要：**\n- 已确定层板默认 5 层",
                intent="MODIFY",
                instruction="固定默认层板策略",
                changed_files=["paramlist.xml"],
                revision_id="r0003",
            )
            (meta_dir / "skills" / "create_object.md").write_text(
                "先规划参数表，再生成 3D 脚本。",
                encoding="utf-8",
            )

            pipeline = TaskPipeline(trace_dir="./traces")
            pipeline._load_knowledge = lambda: "GLOBAL_KNOWLEDGE"
            pipeline._resolve_skills_dir = lambda: Path(tmpdir) / "empty-skills"

            captured = {}
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = ({}, "ok")
                mock_agent_cls.return_value = mock_agent

                result = pipeline.execute(
                    TaskRequest(
                        user_input="生成一个书架",
                        intent="CREATE",
                        project=project,
                        work_dir=tmpdir,
                    )
                )
                captured["knowledge"] = mock_agent.generate_only.call_args.kwargs["knowledge"]
                captured["skills"] = mock_agent.generate_only.call_args.kwargs["skills"]

        self.assertTrue(result.success)
        self.assertIn("GLOBAL_KNOWLEDGE", captured["knowledge"])
        self.assertIn("用于住宅收纳系统", captured["knowledge"])
        self.assertIn("已确定层板默认 5 层", captured["knowledge"])
        self.assertIn("层板数量必须由参数驱动", captured["knowledge"])
        self.assertIn("先规划参数表", captured["skills"])


if __name__ == "__main__":
    unittest.main()
