"""
Tests for the P8 CREATE zero-output guard.

事故回归：CREATE 零产出（模型只输出规划+提问，零 [FILE:] 块）时旧 pipeline 静默
交付 create_new 占位项目（BLOCK A,B,ZZYZX），验证报告对占位脚本空转全绿。

守卫语义：
- CREATE/IMAGE 首轮解析零 [FILE:] → 重试一次（追加硬指令）。
- 重试仍零产出 → TaskResult(success=False, project=None, ...)，service 走既有
  "只有无产出才算硬失败" 路径：占位项目不落盘、不挂载、不跑空转验证。
- 回放安全：重试只在首轮零产出时触发；黄金语料 CREATE 全部首轮有产出 →
  调用序列不变 → 回放零 miss。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticVerificationResult


# ── helpers ────────────────────────────────────────────────


def _mock_llm_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")


def _sem_pass() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


def _make_pipeline(mock_llm: MagicMock) -> TaskPipeline:
    """无真实编译器 + verify_semantics 恒 pass，聚焦零产出守卫本身。"""
    cfg = GDLAgentConfig()
    pipeline = TaskPipeline(config=cfg, trace_dir="/tmp")
    pipeline._make_llm = lambda req: mock_llm
    compiler_mock = MagicMock()
    pipeline._make_compiler = lambda: compiler_mock
    return pipeline


def _make_request(tmp_path: Path, intent: str = "CREATE") -> TaskRequest:
    project = HSFProject.create_new("test_obj", work_dir=str(tmp_path))
    return TaskRequest(
        user_input="生成一个漏窗",
        intent=intent,
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )


def _generation_call_count(mock_llm: MagicMock) -> int:
    """统计 generate_only 的 LLM 调用（排除 object_planner 的规划调用）。"""
    return sum(
        1 for c in mock_llm.generate.call_args_list
        if "请严格按上述规划生成" in str(c.args[0])
    )


def _all_gen_texts(mock_llm: MagicMock) -> str:
    return "\n".join(
        str(c.args[0]) for c in mock_llm.generate.call_args_list
        if "请严格按上述规划生成" in str(c.args[0])
    )


GDL_REPLY = (
    "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
    "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
)
PLAN_ONLY = "我先规划一下：需要确认材质、尺寸和纹样类型，请告诉我更多需求。"
RETRY_HARD_HINT = "你上一次回复没有包含任何 [FILE:] 代码块。"


def _side_effect(first_round: str, retry_round: str | None = None):
    """planner → first gen → (optional) retry gen。"""
    def _gen(messages, **kwargs):
        text = str(messages)
        if "请严格按上述规划生成" not in text:
            return _mock_llm_response('{"object_type": "test"}')
        if retry_round is not None and RETRY_HARD_HINT in text:
            return _mock_llm_response(retry_round)
        return _mock_llm_response(first_round)
    return _gen


# ── 1. 首轮零产出 → 重试一次 → 正常交付 ────────────────────


class TestRetryOnZeroOutput:
    def test_retry_delivers_code(self, tmp_path: Path):
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(PLAN_ONLY, GDL_REPLY)
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            result = pipeline.execute(_make_request(tmp_path))

        assert result.success is True
        assert result.project is not None
        assert result.scripts, "重试轮产出应落盘"
        assert "BLOCK" in result.project.get_script(ScriptType.SCRIPT_3D)
        # LLM 恰好 2 次生成调用（不含规划调用）
        assert _generation_call_count(mock_llm) == 2
        # 重试指令携带硬指令
        assert RETRY_HARD_HINT in _all_gen_texts(mock_llm)

    def test_retry_instruction_content(self, tmp_path: Path):
        """重试指令必须包含"不要提问、不要只输出计划"硬约束。"""
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(PLAN_ONLY, GDL_REPLY)
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            pipeline.execute(_make_request(tmp_path))

        texts = _all_gen_texts(mock_llm)
        assert "不要提问、不要只输出计划，直接输出完整代码文件。" in texts


# ── 2. 两轮都零产出 → 硬失败 ───────────────────────────────


class TestHardFailOnDoubleZero:
    def test_double_zero_returns_hard_fail(self, tmp_path: Path):
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(PLAN_ONLY, "请确认需求后我再生成。")
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            result = pipeline.execute(_make_request(tmp_path))

        assert result.success is False
        assert result.project is None, "project=None → service 硬失败路径"
        assert result.error, "error 应非空"
        assert "模型未产出代码" in result.plain_text
        assert "助手设置" in result.plain_text
        assert "先规划后生成" in result.plain_text
        # 两轮生成调用都发生了（第一轮原文 + 提示）
        assert _generation_call_count(mock_llm) == 2

    def test_double_zero_no_placeholder_delivery(self, tmp_path: Path):
        """零产出时占位项目不落盘：没有 verification 报告、没有 scripts。"""
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(PLAN_ONLY, "还在思考…")
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            result = pipeline.execute(_make_request(tmp_path))

        assert result.scripts == {}
        assert result.verification is None


# ── 3. 首轮即有产出 → 不多发 LLM 调用（回放安全）────────────


class TestNoRetryWhenFirstRoundHasCode:
    def test_first_round_output_no_extra_call(self, tmp_path: Path):
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(GDL_REPLY)
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            result = pipeline.execute(_make_request(tmp_path))

        assert result.success is True
        # LLM 恰好 1 次生成调用（回放安全：调用序列不变）
        assert _generation_call_count(mock_llm) == 1
        assert RETRY_HARD_HINT not in _all_gen_texts(mock_llm)

    def test_image_intent_also_guarded(self, tmp_path: Path):
        """IMAGE 意图同样受零产出守卫保护。"""
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _side_effect(PLAN_ONLY, GDL_REPLY)
        pipeline = _make_pipeline(mock_llm)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem_pass(),
        ):
            result = pipeline.execute(_make_request(tmp_path, intent="IMAGE"))

        assert result.success is True
        assert _generation_call_count(mock_llm) == 2
