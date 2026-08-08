"""pipeline 注入名单透出 + 直接调 pipeline 不触发 fail_count 回写（任务 S2）。

覆盖：
- 任务结束时 TaskResult.metadata["injected_skills"] 记录本次实际注入的 skill
  （无注入记 []；与既有 metadata 合并不覆盖）
- 每次 execute 前注入侧通道重置（CHAT 等不注入的任务不会残留上一任务名单）
- benchmark/CI 回放路径（直接调 pipeline.execute）：只记 metadata 到内存，
  绝不对 skill 文件写 fail_count / last_failed（fail_count 回写只在 GUI 侧通道）
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openbrep.compiler import CompileResult, MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.skills_loader import SkillsLoader


def _write_skill(skills_dir: Path, name: str, **fields) -> Path:
    skills_dir.mkdir(parents=True, exist_ok=True)
    meta = {"status": "active", "reuse_count": 0, "fail_count": 0, "last_failed": "null"}
    meta.update(fields)
    lines = ["---"] + [f"{k}: {v}" for k, v in meta.items()] + ["---", "", "# body"]
    path = skills_dir / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_pipeline(skills_dir: Path, tmp_path: Path, llm_content: str = "") -> TaskPipeline:
    cfg = GDLAgentConfig()
    cfg.compiler.path = "/fake/LP_XMLConverter"
    pipeline = TaskPipeline(config=cfg, trace_dir=str(tmp_path / "traces"))
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content=(
            llm_content
            or "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
        ),
        model="mock", usage={}, finish_reason="stop",
    )
    pipeline._make_llm = lambda _req: mock_llm
    compiler = MagicMock()
    compiler.hsf2libpart.return_value = CompileResult(
        success=True, stdout="", stderr="", mode="lp", output_path="/tmp/t.gsm", exit_code=0,
    )
    pipeline._make_compiler = lambda: compiler
    loader = SkillsLoader(str(skills_dir))
    loader.load()
    pipeline._skills_loader = loader
    return pipeline


def _project(tmp_path: Path) -> HSFProject:
    proj = HSFProject.create_new("test_shelf", work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return proj


class TestPipelineInjectedSkills:
    def test_metadata_records_actually_injected_skill(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skill_path = _write_skill(skills_dir, "shelf_strategy", pattern_type="shelf_loop")
        skill_path.write_text(
            "---\nstatus: active\nreuse_count: 0\nfail_count: 0\nlast_failed: null\n---\n\n"
            "# Shelf\n\n## 触发关键词 / Activation Keywords\n- 书架\n- 货架\n",
            encoding="utf-8",
        )
        pipeline = _make_pipeline(skills_dir, tmp_path)
        result = pipeline.execute(TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))

        assert result.metadata["injected_skills"] == ["shelf_strategy"]

    def test_metadata_empty_when_no_skill_injected(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"  # 空目录：无 skill 可注入
        pipeline = _make_pipeline(skills_dir, tmp_path)
        result = pipeline.execute(TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))

        assert result.metadata["injected_skills"] == []

    def test_metadata_merges_with_existing_metadata(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        pipeline = _make_pipeline(skills_dir, tmp_path)
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5", intent="MODIFY",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
            agent_loop=False,
        ))

        assert "injected_skills" in result.metadata
        assert isinstance(result.metadata["injected_skills"], list)

    def test_side_channel_resets_between_tasks(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skill_path = _write_skill(skills_dir, "shelf_strategy")
        skill_path.write_text(
            "---\nstatus: active\nreuse_count: 0\nfail_count: 0\nlast_failed: null\n---\n\n"
            "# Shelf\n\n## 触发关键词 / Activation Keywords\n- 书架\n- 货架\n",
            encoding="utf-8",
        )
        pipeline = _make_pipeline(skills_dir, tmp_path)
        create = pipeline.execute(TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))
        assert create.metadata["injected_skills"] == ["shelf_strategy"]

        # 第二个任务不注入 → 不残留上一任务名单
        chat = pipeline.execute(TaskRequest(
            user_input="你好", intent="CHAT",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))
        assert chat.metadata["injected_skills"] == []

    def test_direct_pipeline_call_never_writes_fail_count(self, tmp_path: Path):
        """benchmark 路径：直接调 pipeline 只记 metadata，绝不写 fail_count 回仓库。"""
        skills_dir = tmp_path / "skills"
        skill_path = _write_skill(skills_dir, "shelf_strategy", pattern_type="shelf_loop")
        skill_path.write_text(
            "---\nstatus: active\nreuse_count: 0\nfail_count: 0\nlast_failed: null\n---\n\n"
            "# Shelf\n\n## 触发关键词 / Activation Keywords\n- 书架\n- 货架\n",
            encoding="utf-8",
        )
        before = skill_path.read_bytes()
        pipeline = _make_pipeline(skills_dir, tmp_path)
        result = pipeline.execute(TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        ))

        assert result.metadata["injected_skills"] == ["shelf_strategy"]
        # 直接调 pipeline：fail_count / last_failed 不得被写回（reuse 计数是本就存在的语义）
        on_disk = skill_path.read_text(encoding="utf-8")
        assert "fail_count: 0" in on_disk
        assert "last_failed: null" in on_disk


if __name__ == "__main__":
    pytest.main([__file__])
