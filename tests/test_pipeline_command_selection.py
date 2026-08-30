"""HF6：GDL 几何命令选择规则链路（AC-3 / AC-4）端到端测试。

覆盖：
- CREATE：planner prompt（system 阶梯摘要 + user 知识）与 generation
  知识均含规则文档；
- MODIFY：旧路径与 agent-loop 共用 _assemble_context → knowledge 含文档
  （MODIFY 零覆盖修复）；
- AC-4：优化/审查触发词强制注入全文（MODIFY 与 codex EXPLAIN 两路）；
- 规则文档正文无 `\n---\n` 分隔符（P14 分片 bug 守卫）。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

_REPO = Path(__file__).parent.parent
_RULES_BODY = (  # 规则文档正文（去 frontmatter）
    _REPO / "knowledge" / "core" / "gdl_command_selection.md"
)


def _apply_real_changes(project, changes):
    """按 core.GDLAgent._apply_changes 同款语义应用改动（mock GDLAgent 的替身）。"""
    for file_path, content in changes.items():
        for script_type in ScriptType:
            if script_type.value in file_path:
                project.scripts[script_type] = content + "\n"


def _rules_text() -> str:
    raw = _RULES_BODY.read_text(encoding="utf-8")
    body_start = raw.find("---", 3)
    return raw[body_start + 4 :].strip()


class TestCommandSelectionPipeline(unittest.TestCase):
    def _project(self, tmpdir: str) -> HSFProject:
        return HSFProject.create_new("bookshelf", work_dir=tmpdir)

    def test_create_planner_prompt_has_ladder_and_knowledge_has_rules_doc(self):
        """AC-3：CREATE planner 的 system prompt 含阶梯摘要，user 消息含
        规则文档全文；generation 知识同样含文档。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = self._project(tmpdir)
            mock_llm = MagicMock()
            mock_llm.generate.return_value = LLMResponse(
                content='{"object_type":"专业书架","geometry":["侧板"],"parameters":["A"],"command_candidates":["BLOCK"],"script_3d_strategy":["BLOCK"],"script_2d_strategy":["PROJECT2"],"material_strategy":["材质"],"risks":["DEL"]}',
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
                mock_agent.generate_only.return_value = (
                    {"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"},
                    "ok",
                )
                mock_agent._apply_changes.side_effect = _apply_real_changes
                mock_agent_cls.return_value = mock_agent
                result = pipeline.execute(
                    TaskRequest(
                        user_input="做一个书架",
                        intent="CREATE",
                        project=project,
                        work_dir=tmpdir,
                    )
                )
                captured["system"] = mock_llm.generate.call_args.args[0][0]["content"]
                captured["planner_user"] = mock_llm.generate.call_args.args[0][1]["content"]
                captured["gen_knowledge"] = mock_agent.generate_only.call_args.kwargs["knowledge"]

        self.assertTrue(result.success)
        # planner system：命令选择阶梯摘要
        self.assertIn("GDL Command Selection Ladder", captured["system"])
        self.assertIn("pick the LOWEST justifiable level", captured["system"])
        self.assertIn("CUTPLANE", captured["system"].upper())
        # planner user：全量规则文档
        self.assertIn("复杂度阶梯（能低不高）", captured["planner_user"])
        # generation：全量规则文档
        self.assertIn("复杂度阶梯（能低不高）", captured["gen_knowledge"])
        self.assertIn("审查模式（优化 / 审查请求专用）", captured["gen_knowledge"])

    def test_modify_knowledge_includes_rules_doc(self):
        """AC-3：MODIFY 路径（_handle_script_update，与 agent-loop 共用
        _assemble_context）的 knowledge 含规则文档——MODIFY 零覆盖已修复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = self._project(tmpdir)
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            captured = {}
            with patch("openbrep.core.GDLAgent.generate_only") as mock_gen:
                mock_gen.return_value = ({}, "analysis")
                result = pipeline.execute(
                    TaskRequest(
                        user_input="把宽度改大",
                        intent="MODIFY",
                        project=project,
                        work_dir=tmpdir,
                        agent_loop=False,  # 用旧路径验证同一知识通道
                    )
                )
                captured["knowledge"] = mock_gen.call_args.kwargs["knowledge"]

        self.assertTrue(result.success)
        self.assertIn("复杂度阶梯（能低不高）", captured["knowledge"])

    def test_review_trigger_forces_full_rules_in_modify(self):
        """AC-4：MODIFY + 优化触发词 → generation 知识强制注入全文
        （带「审查模式强制注入」标记，区别于普通 core 注入）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = self._project(tmpdir)
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            captured = {}
            with patch("openbrep.core.GDLAgent.generate_only") as mock_gen:
                mock_gen.return_value = ({}, "analysis")
                pipeline.execute(
                    TaskRequest(
                        user_input="帮我优化这个柜子的建模方式",
                        intent="MODIFY",
                        project=project,
                        work_dir=tmpdir,
                        agent_loop=False,
                    )
                )
                captured["forced"] = mock_gen.call_args.kwargs["knowledge"]

                pipeline.execute(
                    TaskRequest(
                        user_input="把宽度改大",
                        intent="MODIFY",
                        project=project,
                        work_dir=tmpdir,
                        agent_loop=False,
                    )
                )
                captured["plain"] = mock_gen.call_args.kwargs["knowledge"]

        self.assertIn("审查模式强制注入", captured["forced"])
        self.assertNotIn("审查模式强制注入", captured["plain"])
        # 强制注入 = 全文：审查模式小节与阶梯都在
        self.assertIn("审查模式（优化 / 审查请求专用）", captured["forced"])

    def test_review_trigger_ignored_when_no_project(self):
        """AC-4：无打开项目时不强制注入（避免无项目场景也被注入）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            pipeline._load_knowledge = lambda: ""
            pipeline._load_skills = lambda inst: ""

            assembled = pipeline._assemble_context(
                TaskRequest(
                    user_input="帮我优化这个柜子的建模方式",
                    intent="MODIFY",
                    project=None,
                    work_dir=tmpdir,
                ),
                None,
                instruction="帮我优化这个柜子的建模方式",
            )

        self.assertFalse(assembled.review_rules_forced)

    def test_codex_explain_review_trigger_injects_rules(self):
        """AC-4：codex EXPLAIN（_handle_codex_chat，有项目）命中优化触发词时
        system 提示含规则全文 + 审查模式指令；不命中时无注入。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
            project = self._project(tmpdir)
            mock_llm = MagicMock()
            mock_llm.generate.return_value = LLMResponse(
                content="评估结论", model="mock", usage={}, finish_reason="stop"
            )
            pipeline._make_llm = lambda req: mock_llm

            captured = {}
            pipeline._handle_codex_chat(
                TaskRequest(
                    user_input="请优化这个构件的建模方式",
                    intent="CHAT",
                    project=project,
                )
            )
            captured["forced"] = mock_llm.generate.call_args.args[0][0]["content"]

            mock_llm.generate.reset_mock()
            mock_llm.generate.return_value = LLMResponse(
                content="回答", model="mock", usage={}, finish_reason="stop"
            )
            pipeline._handle_codex_chat(
                TaskRequest(user_input="这个参数是什么意思", intent="CHAT", project=project)
            )
            captured["plain"] = mock_llm.generate.call_args.args[0][0]["content"]

        self.assertIn("审查模式（选型评估）", captured["forced"])
        self.assertIn("复杂度阶梯（能低不高）", captured["forced"])
        self.assertNotIn("审查模式（选型评估）", captured["plain"])

    def test_rules_doc_has_no_fragmenting_separator(self):
        """AC-1 硬性约束：规则文档正文禁用 `\\n---\\n`（selector 分片 bug 守卫，
        否则整文档会被 _split_markdown_sections 切碎）。"""
        body = _rules_text()
        self.assertNotIn("\n---\n", body)
        # 正文以标题开头，不是 frontmatter 残留
        self.assertTrue(body.lstrip().startswith("# GDL 几何命令选择规则"))


if __name__ == "__main__":
    unittest.main()
