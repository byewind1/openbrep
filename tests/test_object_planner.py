import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject
from openbrep.llm import LLMResponse
from openbrep.object_planner import infer_minimum_plan, parse_gdl_object_plan, plan_gdl_object
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


class TestObjectPlanner(unittest.TestCase):
    def test_parse_gdl_object_plan_from_json(self):
        plan = parse_gdl_object_plan(
            """
            ```json
            {
              "object_type": "参数化鞋柜",
              "geometry": ["柜体", "隔板"],
              "parameters": ["Length A = 宽度"],
              "command_candidates": ["BLOCK", "PROJECT2"],
              "validation_checks": ["ADD/DEL 平衡"],
              "script_3d_strategy": ["BLOCK 组合"],
              "script_2d_strategy": ["PROJECT2"],
              "material_strategy": ["材质参数"],
              "risks": ["ADD/DEL 平衡"]
            }
            ```
            """
        )

        self.assertEqual(plan.object_type, "参数化鞋柜")
        self.assertIn("隔板", plan.geometry)
        self.assertIn("BLOCK", plan.command_candidates)
        self.assertIn("ADD/DEL 平衡", plan.validation_checks)
        self.assertIn("ADD/DEL 平衡", plan.risks)

    def test_plan_gdl_object_falls_back_when_llm_fails(self):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("no api key")

        plan = plan_gdl_object(llm, instruction="做一个书架")

        self.assertIn("书架", plan.object_type)
        self.assertIn("shelf_count", "\n".join(plan.parameters))

    def test_plan_gdl_object_does_not_hard_truncate_selected_planner_knowledge(self):
        llm = MagicMock()
        llm.generate.return_value = LLMResponse(
            content='{"object_type":"测试对象","geometry":["几何"],"parameters":["A"]}',
            model="mock",
            usage={},
            finish_reason="stop",
        )
        knowledge = "A" * 6100 + "TAIL_MARKER"

        plan_gdl_object(llm, instruction="做一个对象", knowledge=knowledge)

        prompt = llm.generate.call_args.args[0][1]["content"]
        self.assertIn("TAIL_MARKER", prompt)

    def test_pipeline_injects_create_plan_into_generation_instruction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            mock_llm = MagicMock()
            mock_llm.generate.return_value = LLMResponse(
                content='{"object_type":"专业书架","geometry":["侧板和层板"],"parameters":["Integer shelf_count = 层板数"],"script_3d_strategy":["FOR/NEXT 生成层板"],"script_2d_strategy":["PROJECT2"],"material_strategy":["材质参数"],"risks":["DEL 平衡"]}',
                model="mock",
                usage={},
                finish_reason="stop",
            )
            pipeline._make_llm = lambda req: mock_llm
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            captured = {}
            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = ({"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, "ok")
                mock_agent_cls.return_value = mock_agent

                result = pipeline.execute(
                    TaskRequest(
                        user_input="做一个书架",
                        intent="CREATE",
                        project=project,
                        work_dir=tmpdir,
                    )
                )
                captured["instruction"] = mock_agent.generate_only.call_args.kwargs["instruction"]
                captured["planner_prompt"] = mock_llm.generate.call_args.args[0][1]["content"]

        self.assertTrue(result.success)
        self.assertIn("GDL Object Plan", captured["instruction"])
        self.assertIn("专业书架", captured["instruction"])
        self.assertIn("shelf_count", captured["instruction"])
        self.assertIn("Archetype: bookshelf", captured["planner_prompt"])
        self.assertIn("Core: core.planning_contract", captured["planner_prompt"])
        self.assertIn("Core: core.parameter_rules", captured["planner_prompt"])
        self.assertIn("Wiki: BLOCK", captured["planner_prompt"])
        self.assertIn("本次使用知识", result.plain_text)
        self.assertIn("archetype.bookshelf", result.plain_text)
        self.assertIn("生成前规划", result.plain_text)
        self.assertEqual(result.object_plan["object_type"], "专业书架")
        self.assertIn("侧板和层板", result.object_plan["geometry"])
        self.assertIn("archetype.bookshelf", result.object_plan["knowledge_sources"])
        self.assertIn("core.planning_contract", result.object_plan["knowledge_sources"])
        self.assertIn("core.parameter_rules", result.object_plan["knowledge_sources"])
        self.assertIn("wiki.BLOCK", result.object_plan["knowledge_sources"])

    def test_modify_path_does_not_run_object_planner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            with patch("openbrep.runtime.pipeline.plan_gdl_object") as mock_planner:
                with patch("openbrep.core.GDLAgent.generate_only", return_value=({}, "analysis")):
                    result = pipeline.execute(
                        TaskRequest(
                            user_input="把宽度改大",
                            intent="MODIFY",
                            project=project,
                            work_dir=tmpdir,
                        )
                    )

        self.assertTrue(result.success)
        mock_planner.assert_not_called()

    def test_bookshelf_fallback_is_professional_not_single_block(self):
        plan = infer_minimum_plan("生成一个书架")

        self.assertIn("侧板", "\n".join(plan.geometry))
        self.assertIn("层板", "\n".join(plan.geometry))
        self.assertIn("FOR", "\n".join(plan.script_3d_strategy))

    def test_trace_records_object_plan_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=tmpdir)
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            mock_llm = MagicMock()
            mock_llm.generate.return_value = LLMResponse(
                content='{"object_type":"专业书架","geometry":["侧板"],"parameters":["A"],"script_3d_strategy":["BLOCK"],"script_2d_strategy":["PROJECT2"],"material_strategy":["材质"],"risks":["DEL"]}',
                model="mock",
                usage={},
                finish_reason="stop",
            )
            pipeline._make_llm = lambda req: mock_llm
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            with patch("openbrep.runtime.pipeline.GDLAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.generate_only.return_value = ({"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"}, "")
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(
                    TaskRequest(
                        user_input="做一个书架",
                        intent="CREATE",
                        project=project,
                        work_dir=tmpdir,
                    )
                )

            trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))

        self.assertTrue(trace["has_object_plan"])
        self.assertEqual(trace["object_type"], "专业书架")
        self.assertIn("archetype.bookshelf", trace["knowledge_sources"])


if __name__ == "__main__":
    unittest.main()
