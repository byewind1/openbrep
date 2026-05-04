import unittest
import tempfile
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
        self.assertIn("ADD/DEL 平衡", plan.risks)

    def test_plan_gdl_object_falls_back_when_llm_fails(self):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("no api key")

        plan = plan_gdl_object(llm, instruction="做一个书架")

        self.assertIn("书架", plan.object_type)
        self.assertIn("shelf_count", "\n".join(plan.parameters))

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

        self.assertTrue(result.success)
        self.assertIn("GDL Object Plan", captured["instruction"])
        self.assertIn("专业书架", captured["instruction"])
        self.assertIn("shelf_count", captured["instruction"])
        self.assertIn("生成前规划", result.plain_text)

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


if __name__ == "__main__":
    unittest.main()
