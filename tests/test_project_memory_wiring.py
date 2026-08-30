"""HF5 AC-2 项目记忆写入接线测试。

覆盖验收点：
- MODIFY 成功交付 → decisions.md 追加一条紧凑记录（HF5 格式断言）
- 零产出（无 changed_files）不写
- include_learned_skills=False（benchmark 录制/回放口径）不写——硬门禁
- CHAT/EXPLAIN 成功回复沉淀（无 changed_files → 要点行）
- CREATE 成功同样沉淀
- 6000 字符注入护栏不回归（load_project_memory 尾部截断）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.core import GDLAgent
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.project_context import (
    append_project_decision,
    load_project_memory,
    resolve_project_context,
)
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


def _mock_llm_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")


def _make_pipeline(llm_content: str, *, include_learned_skills: bool = True) -> TaskPipeline:
    """旧 MODIFY 路径 pipeline（agent_loop 关闭），默认学习记忆开启（生产口径）。"""
    cfg = GDLAgentConfig()
    pipeline = TaskPipeline(
        config=cfg,
        trace_dir="./traces",
        include_learned_skills=include_learned_skills,
    )
    mock_llm = MagicMock()
    mock_llm.generate.return_value = _mock_llm_response(llm_content)
    pipeline._make_llm = lambda req: mock_llm
    original_execute = pipeline.execute

    def _execute_with_agent_loop_off(request):
        request.agent_loop = False
        return original_execute(request)

    pipeline.execute = _execute_with_agent_loop_off
    return pipeline


def _decisions(project: HSFProject) -> Path:
    return Path(project.root) / ".openbrep" / "memory" / "decisions.md"


def _modify_project(tmpdir: str) -> HSFProject:
    project = HSFProject.create_new("Chair", work_dir=tmpdir)
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return project


class ModifyDeliveryMemoryTest(unittest.TestCase):
    def test_modify_success_delivery_appends_compact_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _modify_project(tmpdir)
            design_changed = {"scripts/3d.gdl": "BLOCK A + 0.1, B, ZZYZX\nEND\n"}

            def fake_generate(self_agent, **kwargs):
                return design_changed, "已改宽"

            with patch.object(GDLAgent, "generate_only", fake_generate):
                pipeline = _make_pipeline("")
                result = pipeline.execute(TaskRequest(
                    user_input="把椅子改宽一点",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "output"),
                ))

            self.assertTrue(result.success)
            decisions = _decisions(project)
            self.assertTrue(decisions.exists())
            text = decisions.read_text(encoding="utf-8")
            self.assertIn("用户意图：把椅子改宽一点", text)
            self.assertIn("交付：scripts/3d.gdl —— 已改宽", text)

    def test_zero_output_modify_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _modify_project(tmpdir)

            def fake_generate(self_agent, **kwargs):
                return {}, "已按顺序检查，无需修改"  # 空转式"检查通过"

            with patch.object(GDLAgent, "generate_only", fake_generate):
                pipeline = _make_pipeline("")
                pipeline.execute(TaskRequest(
                    user_input="继续",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "output"),
                ))

            self.assertFalse(_decisions(project).exists())

    def test_benchmark_include_learned_skills_false_never_writes(self):
        """硬门禁：include_learned_skills=False（benchmark 录制/回放口径）禁止写入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project = _modify_project(tmpdir)

            def fake_generate(self_agent, **kwargs):
                return {"scripts/3d.gdl": "BLOCK A + 0.1, B, ZZYZX\nEND\n"}, "已改宽"

            with patch.object(GDLAgent, "generate_only", fake_generate):
                pipeline = _make_pipeline("", include_learned_skills=False)
                result = pipeline.execute(TaskRequest(
                    user_input="把椅子改宽一点",
                    intent="MODIFY",
                    project=project,
                    work_dir=tmpdir,
                    output_dir=str(Path(tmpdir) / "output"),
                ))

            self.assertTrue(result.success)
            self.assertFalse(_decisions(project).exists())


class ChatAndCreateMemoryTest(unittest.TestCase):
    def test_chat_with_project_appends_points_line_without_changed_files(self):
        from openbrep.runtime import pipeline as pipeline_module

        with tempfile.TemporaryDirectory() as tmpdir:
            project = _modify_project(tmpdir)
            pipeline = _make_pipeline("ok")
            fake_reply = MagicMock()
            fake_reply.overall_goal = "chair"
            with patch.object(
                pipeline_module, "resolve_script_target", return_value=None
            ), patch.object(
                pipeline_module, "resolve_parameter_targets", return_value=[]
            ), patch.object(
                pipeline_module, "build_project_context",
                return_value={"gsm_name": "chair"},
            ), patch.object(
                pipeline_module, "explain_project_context", return_value=fake_reply
            ), patch.object(
                pipeline_module, "build_chat_explanation_reply",
                return_value="这是一把参数化椅子",
            ) as mock_reply:
                result = pipeline.execute(TaskRequest(
                    user_input="这是什么对象？",
                    intent="CHAT",
                    project=project,
                ))

            self.assertTrue(result.success)
            mock_reply.assert_called_once()
            decisions = _decisions(project)
            self.assertTrue(decisions.exists())
            text = decisions.read_text(encoding="utf-8")
            self.assertIn("用户意图：这是什么对象？", text)
            self.assertIn("要点：这是一把参数化椅子", text)
            self.assertNotIn("交付：", text)  # 无 changed_files 行
            memory = load_project_memory(resolve_project_context(project))
            self.assertIn("Project Memory: decisions", memory)

    def test_create_success_appends_delivery_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("Shelf", work_dir=tmpdir)
            project.scripts[ScriptType.SCRIPT_3D] = ""
            project.save_to_disk()
            fake_llm = MagicMock()
            fake_llm.generate.return_value = MagicMock(content="{}")

            pipeline = TaskPipeline(trace_dir="./traces")
            pipeline.config.compiler.path = ""
            pipeline._load_knowledge = lambda: ""
            pipeline._resolve_skills_dir = lambda: Path(tmpdir) / "empty-skills"
            pipeline._make_llm = lambda req: fake_llm

            with patch.object(GDLAgent, "generate_only") as mock_gen, \
                 patch.object(GDLAgent, "_apply_changes") as mock_apply:
                mock_gen.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"},
                    "ok",
                )

                def _apply_real(project_obj, changes):
                    for file_path, content in changes.items():
                        for st in ScriptType:
                            if st.value in file_path:
                                project_obj.scripts[st] = content + "\n"

                mock_apply.side_effect = _apply_real
                result = pipeline.execute(TaskRequest(
                    user_input="生成一个书架",
                    intent="CREATE",
                    project=project,
                    work_dir=tmpdir,
                ))

            self.assertTrue(result.success, result.error)
            decisions = _decisions(project)
            self.assertTrue(decisions.exists())
            text = decisions.read_text(encoding="utf-8")
            self.assertIn("用户意图：生成一个书架", text)
            self.assertIn("交付：scripts/3d.gdl", text)


class MemoryInjectionGuardTest(unittest.TestCase):
    def test_load_project_memory_keeps_6000_char_tail_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("Memory", work_dir=tmpdir)
            context = resolve_project_context(project)
            # 累积 70 条记录强制总长 > 6000（单条约 120 字符）
            for i in range(70):
                append_project_decision(
                    context,
                    summary="交付摘要 " + f"r{i:03d}" + "x" * 200,
                    intent="MODIFY",
                    instruction="用户指令 " + f"r{i:03d}",
                    changed_files=["scripts/3d.gdl"],
                )
            memory = load_project_memory(context)
            self.assertTrue(memory.startswith("## Project Memory: decisions"))
            body = memory[len("## Project Memory: decisions"):].strip()
            # 护栏：正文 ≤ 6000 字符，只保留最近记录
            self.assertLessEqual(len(body), 6000)
            self.assertNotIn("用户指令 r000", body)
            self.assertNotIn("用户指令 r005", body)
            self.assertIn("用户指令 r069", body)

    def test_record_contains_only_instruction_and_summary_lines(self):
        """隐私/卫生：单条记录恰好 2 行——用户意图 + 交付/要点，无多余元数据。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("Privacy", work_dir=tmpdir)
            context = resolve_project_context(project)
            append_project_decision(
                context,
                summary="回纹样式已落盘",
                intent="MODIFY",
                instruction="继续，按顺序加下一个样式",
                changed_files=["scripts/vl.gdl"],
            )
            lines = context.decisions_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith("- ["))
            self.assertIn("用户意图：继续，按顺序加下一个样式", lines[0])
            self.assertIn("交付：scripts/vl.gdl —— 回纹样式已落盘", lines[1])


class BenchmarkRunnerGateTest(unittest.TestCase):
    def test_runner_pipeline_disables_learning_memory(self):
        """runner._make_pipeline（录制/回放口径）必须 include_learned_skills=False：
        decisions.md 是累积态，任何写入都会污染黄金语料。"""
        from benchmark.runner import BenchmarkRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            conf = Path(tmpdir) / "config.toml"
            conf.write_text("", encoding="utf-8")
            runner = BenchmarkRunner(
                config_path=str(conf),
                mode="mock",
                llm_replay="benchmark/fixtures/llm_corpus/create.jsonl",
            )
            pipeline = runner._make_pipeline()
            self.assertFalse(pipeline.include_learned_skills)


if __name__ == "__main__":
    unittest.main()
