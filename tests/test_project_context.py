import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.hsf_project import HSFProject, ScriptType


def _apply_real_changes(project, changes):
    """按 core.GDLAgent._apply_changes 同款语义应用改动（mock GDLAgent 的替身）。"""
    for file_path, content in changes.items():
        for script_type in ScriptType:
            if script_type.value in file_path:
                project.scripts[script_type] = content + "\n"


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
                summary="新增参数：shelf_count（默认 5）",
                intent="MODIFY",
                instruction="增加层板数量参数",
                changed_files=["paramlist.xml", "scripts/3d.gdl"],
                revision_id="r0002",
            )
            memory = load_project_memory(context)
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())

        self.assertIn("Project Memory: decisions", memory)
        self.assertIn("用户意图：增加层板数量参数", memory)
        self.assertIn("交付：paramlist.xml, scripts/3d.gdl", memory)
        self.assertIn("新增参数：shelf_count（默认 5）", memory)
        self.assertIn("（修订 r0002）", memory)
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
                summary="已确定层板默认 5 层",
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
            # 测试关注知识/技能注入，与编译器可用性无关：
            # 显式置空编译路径，编译验证干净跳过（SKIPPED_NO_COMPILER），
            # 避免示例配置的占位 converter 路径或本机 Archicad 影响结果
            pipeline.config.compiler.path = ""
            pipeline._load_knowledge = lambda: "GLOBAL_KNOWLEDGE"
            pipeline._resolve_skills_dir = lambda: Path(tmpdir) / "empty-skills"

            captured = {}
            fake_llm = MagicMock()
            # _handle_gdl 在 GDLAgent 之前先用 _make_llm() 建真实 LLM，并交给对象规划
            # （plan_gdl_object → llm.generate，真实网络调用，曾导致本测试 224s 超时失败、
            # 数小时后同 commit 又秒过：prompt 不变、端点可达性/响应内容决定成败）。
            # 测试原只 mock 了 GDLAgent，漏掉了规划这一层；此处把 pipeline 的 LLM seam
            # 一并替换：generate 返回空 JSON → parse 走确定性 infer_minimum_plan fallback，
            # 断网也能跑，且 fallback 规划不含 2D 检查（不会因空 2D 脚本误报 blocking）。
            fake_llm.generate.return_value = MagicMock(content="{}")
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                # P8：mock 必须真正交付非占位脚本并应用（空交付现在会触发零产出硬失败）
                mock_agent.generate_only.return_value = ({"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, "ok")
                mock_agent._apply_changes.side_effect = _apply_real_changes
                mock_agent_cls.return_value = mock_agent
                with patch.object(pipeline, "_make_llm", return_value=fake_llm):
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
