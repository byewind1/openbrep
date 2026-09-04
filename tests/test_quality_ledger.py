"""G0/G1 质量账本契约测试（observer-only）。

覆盖派单 AC-G0-3 全路径契约：micro modify / DSL / skill_ops / 普通 agent loop /
codex bridge / CREATE / 异常早退（编译器缺失=infrastructure_error、
用户取消=cancelled），每条路径断言 run_id 存在且 outcome 正确；
外加 best-effort 降级、evaluator 异常降级、benchmark 隔离开关、
隐私纪律、趋势 CLI 的契约。

全部离线：MockLLM / MockHSFCompiler / fake codex app-server，不打真实 API。
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse, MockLLM
from openbrep.quality import evaluator as quality_evaluator
from openbrep.quality.report import build_report, format_report
from openbrep.quality.schema import OUTCOMES, QualityRecord
from openbrep.quality.store import load_records, record_path
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

# ── 公共构造 ──────────────────────────────────────────────

def _make_project(tmp_path: Path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
        GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    proj.save_to_disk()
    return proj


def _make_pipeline(tmp_path: Path, *, mock_llm=None, real_compiler: bool = True,
                   quality_ledger_enabled: bool = True) -> TaskPipeline:
    cfg = GDLAgentConfig()
    if real_compiler:
        cfg.compiler.path = "/fake/LP_XMLConverter"  # 仅让编译分支执行，实际用 Mock compiler
    pipeline = TaskPipeline(
        config=cfg,
        trace_dir=str(tmp_path / "traces"),
        quality_ledger_enabled=quality_ledger_enabled,
    )
    if mock_llm is None:
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content="[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n",
            model="mock", usage={}, finish_reason="stop",
        )
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _request(project: HSFProject, tmp_path: Path, user_input: str, intent: str,
             **overrides) -> TaskRequest:
    kwargs = dict(
        user_input=user_input,
        intent=intent,
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


def _read_records(project: HSFProject) -> list[dict]:
    """逐份 json.load（红队 7：每份档案必须可解析）。"""
    runs_dir = project.root / ".openbrep" / "quality" / "runs"
    if not runs_dir.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs_dir.glob("*.json"))]


def _assert_record_shape(record: dict, *, outcome: str) -> None:
    assert record["schema_version"] == 1
    assert record["run_id"].startswith("r_")
    assert record["outcome"] == outcome
    assert outcome in OUTCOMES
    # 隐私纪律：path_hash + 目录名，无完整路径；指令摘要 ≤120 字符
    assert set(record["project_ref"]) == {"path_hash", "name"}
    assert len(record["project_ref"]["path_hash"]) == 12
    assert len(record["instruction_summary"]) <= 120
    for axis in ("delivery", "artifact_quality", "execution_cost", "provenance"):
        assert axis in record


# ── outcome 纯函数映射（全枚举信号源）──────────────────────

class _StubResult:
    def __init__(self, **kw):
        self.success = kw.get("success", True)
        self.intent = kw.get("intent", "MODIFY")
        self.error = kw.get("error")
        self.metadata = kw.get("metadata", {})
        self.verification = kw.get("verification", {"checks": [], "passed": True})
        self.compile_result = kw.get("compile_result", None)
        self.scripts = kw.get("scripts", {"scripts/3d.gdl": "END"})


class _StubRequest:
    intent = "MODIFY"
    should_cancel = None


class TestOutcomeMapping:
    def test_completed(self):
        assert quality_evaluator.map_outcome(_StubRequest(), _StubResult()) == "completed"

    def test_gate_fail(self):
        result = _StubResult(success=False)
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "gate_fail"

    def test_cancelled_flag_in_metadata(self):
        result = _StubResult(success=False, metadata={"execution": {"cancelled": True}})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "cancelled"

    def test_cancelled_via_should_cancel(self):
        class Req(_StubRequest):
            should_cancel = staticmethod(lambda: True)
        assert quality_evaluator.map_outcome(Req(), _StubResult()) == "cancelled"

    def test_timeout(self):
        result = _StubResult(metadata={"execution": {"timeout": True}})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "timeout"

    def test_budget_exhausted(self):
        result = _StubResult(metadata={"execution": {"budget_exhausted": True}})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "budget_exhausted"

    def test_infrastructure_error_via_error_field(self):
        result = _StubResult(success=False, error="codex provider unavailable",
                             verification=None, compile_result=None, scripts={})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "infrastructure_error"

    def test_infrastructure_error_via_skipped_compiler(self):
        result = _StubResult(verification={
            "passed": True,
            "checks": [{"check_type": "compile", "status": "not_run",
                        "detail": "SKIPPED_NO_COMPILER（未配置 LP_XMLConverter）"}],
        })
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "infrastructure_error"

    def test_not_evaluable_chat(self):
        result = _StubResult(intent="CHAT")
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "not_evaluable"

    def test_not_evaluable_awaiting_confirmation(self):
        result = _StubResult(metadata={"awaiting_confirmation": True})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "not_evaluable"

    def test_not_evaluable_zero_evidence(self):
        result = _StubResult(success=False, verification=None, compile_result=None, scripts={})
        assert quality_evaluator.map_outcome(_StubRequest(), result) == "not_evaluable"


# ── AC-G0-3 全路径契约 ────────────────────────────────────

class TestCreatePath:
    def test_create_completed_record(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))

        assert result.metadata["run_id"].startswith("r_")
        records = _read_records(project)
        assert len(records) == 1
        record = records[0]
        _assert_record_shape(record, outcome="completed")
        assert record["run_id"] == result.metadata["run_id"]
        # mock 编译不冒充真实 GSM（设计稿 §3.2 要点）
        assert record["delivery"]["compile"]["mode"] == "mock"
        assert record["delivery"]["compile"]["status"] == "pass"
        assert record["execution_cost"]["elapsed_sec"] >= 0
        assert record["provenance"]["score_profile"] == "quality-v1"
        # QualityRecord.from_dict 往返可解析（schema 校验）
        QualityRecord.from_dict(record)

    def test_create_run_id_in_trace(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))

        trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
        assert trace["run_id"] == result.metadata["run_id"]

    def test_create_missing_compiler_is_infrastructure_error(self, tmp_path):
        """红队 5：hsf2libpart 缺失 → infrastructure_error 而非误记 gate_fail。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, real_compiler=False)
        result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        assert result.metadata["run_id"].startswith("r_")

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="infrastructure_error")
        assert records[0]["delivery"]["compile"]["mode"] == "not_run"

    def test_feedback_pointer_written(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))

        feedback_file = project.root / ".openbrep" / "feedback.jsonl"
        assert feedback_file.exists()
        events = [
            json.loads(line)
            for line in feedback_file.read_text(encoding="utf-8").splitlines()
        ]
        pointers = [e for e in events if e["kind"] == "quality_recorded"]
        assert len(pointers) == 1
        assert pointers[0]["trace_id"] == result.metadata["run_id"]
        assert pointers[0]["detail"]["run_id"] == result.metadata["run_id"]


class TestMicroModifyPath:
    def test_micro_modify_completed(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        result = pipeline.execute(
            _request(project, tmp_path, "把 shelf_count 改成 5", "MODIFY", agent_loop=True)
        )
        assert "确定性微修改" in result.plain_text  # 确认走的是 micro 路径
        assert result.metadata["execution"]["llm_calls"] == 0
        assert result.metadata["execution"]["tool_calls"] == 0
        assert result.metadata["before_revision_id"]

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="completed")
        assert records[0]["execution_cost"]["llm_calls"] == 0
        assert records[0]["provenance"]["before_revision"]


class TestParamModifyDslPath:
    def test_dsl_completed(self, tmp_path):
        dsl_json = json.dumps({"operations": [
            {"op": "set_value", "param": "shelf_count", "value": 5},
            {"op": "set_value", "param": "shelf_thk", "value": 0.025},
        ]})
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, mock_llm=MockLLM(responses=[dsl_json]))
        # 复合指令 micro 不拦截；空 skills 目录 → skill_ops 不拦截 → DSL 命中
        result = pipeline.execute(_request(
            project, tmp_path,
            "把 shelf_count 改成 5，把 shelf_thk 改成 25mm", "MODIFY", agent_loop=True,
        ))
        assert "确定性参数修改" in result.plain_text  # 确认走的是 DSL 路径
        assert result.metadata["execution"]["llm_calls"] == 1

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="completed")
        assert records[0]["execution_cost"]["llm_calls"] == 1


class TestSkillOpsPath:
    def test_skill_ops_completed(self, tmp_path):
        from openbrep.skills_loader import SkillsLoader

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        operations = json.dumps({
            "match": {"keywords": ["书架"]},
            "ops": [{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}],
        }, ensure_ascii=False)
        (skills_dir / "bookshelf.md").write_text(
            f"---\nstatus: verified\npattern_type: shelf_loop\noperations: {operations}\n---\n\n"
            "# 书架策略\n\n## 触发关键词\n- 书架\n",
            encoding="utf-8",
        )
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        loader = SkillsLoader(str(skills_dir))
        loader.load()
        pipeline._skills_loader = loader

        result = pipeline.execute(
            _request(project, tmp_path, "把书架层数改成 5", "MODIFY", agent_loop=True)
        )
        assert result.metadata["modify_path"] == "skill_ops"  # 确认走的是 skill_ops 路径

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="completed")


class TestAgentLoopPath:
    def test_agent_loop_completed_structured_counters(self, tmp_path):
        """普通 agent loop：llm/tool 计数从真实计数点进 metadata 与档案。"""
        new_3d = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "update_script", "arguments": {
                "file_path": "scripts/3d.gdl", "content": new_3d}}]},
            {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
            "已加一层层板，编译通过。",
        ])
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, mock_llm=mock_llm)
        result = pipeline.execute(
            _request(project, tmp_path, "给书架加一层层板", "MODIFY", agent_loop=True)
        )

        execution = result.metadata["execution"]
        assert execution["llm_calls"] == 3
        assert execution["tool_calls"] == 2
        assert execution["budget_exhausted"] is False
        assert execution["cancelled"] is False

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="completed")
        cost = records[0]["execution_cost"]
        assert cost["llm_calls"] == 3
        assert cost["tool_calls"] == 2
        assert cost["budget_exhausted"] is False

    def test_agent_loop_budget_exhausted(self, tmp_path):
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{"name": "read_script", "arguments": {"file_path": "scripts/3d.gdl"}}]},
        ])
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, mock_llm=mock_llm)
        result = pipeline.execute(_request(
            project, tmp_path, "给书架加一层层板", "MODIFY",
            agent_loop=True, agent_loop_budget=1,
        ))
        assert result.metadata["execution"]["budget_exhausted"] is True

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="budget_exhausted")
        assert records[0]["execution_cost"]["budget_exhausted"] is True

    def test_agent_loop_cancelled(self, tmp_path):
        """红队 4：用户中途取消 → outcome=cancelled 且档案存在。"""
        mock_llm = MockLLM(responses=["好的"])
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, mock_llm=mock_llm)
        result = pipeline.execute(_request(
            project, tmp_path, "给书架加一层层板", "MODIFY",
            agent_loop=True, should_cancel=lambda: True,
        ))
        assert result.metadata["execution"]["cancelled"] is True

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="cancelled")


class TestCodexBridgePath:
    def test_codex_bridge_completed_structured_counters(self, tmp_path):
        """codex bridge：execution 块与非 codex loop 同形状（真实计数点）。"""
        from test_codex_modify_bridge import (
            _codex_config,
            _FakeServerHarness,
            _final,
            _sem_pass,
            _tool,
            _upd,
            _write_script,
        )
        from test_codex_modify_bridge import (
            _make_project as _bridge_project,
        )
        from test_codex_modify_bridge import (
            _pipeline as _bridge_pipeline,
        )
        from test_codex_modify_bridge import (
            _request as _bridge_request,
        )

        harness = _FakeServerHarness(tmp_path)
        _write_script(tmp_path, [[
            _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"),
            _tool("compile_script"),
            _final("已加一层层板，编译通过。"),
        ]])
        config = _codex_config()
        provider = harness.provider()
        pipeline = _bridge_pipeline(config, provider, tmp_path, compiler=MockHSFCompiler())
        project = _bridge_project(tmp_path)
        project.save_to_disk()
        try:
            with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
                result = pipeline.execute(_bridge_request(tmp_path, project))
            assert result.success, result.plain_text
            execution = result.metadata["execution"]
            assert execution["llm_calls"] == 1   # turn 次数 = 模型调用次数
            assert execution["tool_calls"] == 2
            assert execution["timeout"] is False

            records = _read_records(project)
            assert len(records) == 1
            _assert_record_shape(records[0], outcome="completed")
            assert records[0]["execution_cost"]["llm_calls"] == 1
            assert records[0]["execution_cost"]["tool_calls"] == 2
        finally:
            harness.cleanup()


class TestChatAndEarlyExit:
    def test_chat_not_evaluable(self, tmp_path):
        project = _make_project(tmp_path)
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content="这是解释。", model="mock", usage={}, finish_reason="stop")
        pipeline = _make_pipeline(tmp_path, mock_llm=mock_llm)
        result = pipeline.execute(_request(project, tmp_path, "这个脚本是干什么的？", "CHAT"))

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="not_evaluable")
        assert result.metadata["run_id"]

    def test_exception_early_exit_infrastructure_error(self, tmp_path):
        """异常早退：handler 抛异常 → infrastructure_error，档案照落。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        with patch.object(pipeline, "_handle_modify", side_effect=RuntimeError("boom")):
            result = pipeline.execute(
                _request(project, tmp_path, "改一下", "MODIFY", agent_loop=False)
            )
        assert result.success is False
        assert result.error == "boom"

        records = _read_records(project)
        assert len(records) == 1
        _assert_record_shape(records[0], outcome="infrastructure_error")


# ── G1 健壮性 / 隔离 ──────────────────────────────────────

class TestRobustness:
    def test_run_ids_unique_rapid_fire(self, tmp_path):
        """红队 1：同一项目快速连发两任务 → run_id 不撞、两份档案都在。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        r1 = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        r2 = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        assert r1.metadata["run_id"] != r2.metadata["run_id"]

        records = _read_records(project)
        assert len(records) == 2
        assert {rec["run_id"] for rec in records} == {
            r1.metadata["run_id"], r2.metadata["run_id"]}

    def test_readonly_project_dir_delivery_unaffected(self, tmp_path):
        """红队 2：项目目录只读 → 交付结果不受影响，仅 warning。"""
        project = _make_project(tmp_path)
        openbrep_dir = project.root / ".openbrep"
        openbrep_dir.mkdir(exist_ok=True)
        openbrep_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # 只读：无法建 quality/runs
        pipeline = _make_pipeline(tmp_path)
        try:
            result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
            assert result.success is True  # 交付不受影响
            assert _read_records(project) == []  # 档案未写入但不炸
        finally:
            openbrep_dir.chmod(stat.S_IRWXU)

    def test_evaluator_section_exception_degrades_unavailable(self, tmp_path):
        """红队 6：evaluator 内注入异常 → 字段 unavailable，任务照常交付。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        with patch.object(
            quality_evaluator, "_delivery", side_effect=RuntimeError("injected")
        ):
            result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        assert result.success is True

        records = _read_records(project)
        assert len(records) == 1
        assert records[0]["delivery"]["status"] == "unavailable"
        assert records[0]["delivery"]["reason"] == "evaluator_error"

    def test_evaluator_total_failure_still_delivers(self, tmp_path):
        """build_quality_record 整体异常 → 无档案、仅 warning、交付不变。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        with patch.object(
            quality_evaluator, "build_quality_record", side_effect=RuntimeError("injected")
        ):
            result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        assert result.success is True
        assert _read_records(project) == []

    def test_quality_ledger_disabled_writes_nothing(self, tmp_path):
        """benchmark 隔离开关（AC-G1-4）：显式 False → 零档案、零 feedback 指针。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, quality_ledger_enabled=False)
        result = pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        assert result.success is True
        assert _read_records(project) == []
        feedback_file = project.root / ".openbrep" / "feedback.jsonl"
        if feedback_file.exists():
            events = [
                json.loads(line)
                for line in feedback_file.read_text(encoding="utf-8").splitlines()
            ]
            assert not [e for e in events if e["kind"] == "quality_recorded"]

    def test_benchmark_runner_disables_ledger(self):
        """benchmark runner 显式传 False（回放零污染的代码契约）。"""
        import inspect

        import benchmark.runner as runner

        source = inspect.getsource(runner.BenchmarkRunner._make_pipeline)
        assert "quality_ledger_enabled=False" in source

    def test_privacy_no_full_path_no_long_instruction(self, tmp_path):
        """AC-G1-6：档案不存完整路径，指令截断 ≤120。"""
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        long_input = "做一个" + "很长" * 100 + "的书架"
        pipeline.execute(_request(project, tmp_path, long_input, "CREATE"))

        records = _read_records(project)
        assert len(records) == 1
        raw = json.dumps(records[0], ensure_ascii=False)
        assert str(project.root.resolve()) not in raw
        assert records[0]["project_ref"]["name"] == project.root.name
        assert len(records[0]["instruction_summary"]) == 120
        assert len(long_input) > 120


# ── 趋势 CLI / 报告 ───────────────────────────────────────

class TestReport:
    def test_empty_dir_friendly_message(self, tmp_path):
        report = build_report(tmp_path)
        assert report["total"] == 0
        text = format_report(report, scan_root=str(tmp_path))
        assert "暂无质量档案" in text

    def test_buckets_counts_and_percent(self, tmp_path):
        project = _make_project(tmp_path / "p1")
        pipeline = _make_pipeline(tmp_path / "p1")
        pipeline.execute(_request(project, tmp_path / "p1", "做一个书架", "CREATE"))
        pipeline.execute(_request(
            project, tmp_path / "p1", "做另一个书架", "CREATE"))

        report = build_report(tmp_path)
        assert report["total"] == 2
        assert report["buckets"]["outcome"] == {"completed": 2}
        assert report["buckets"]["intent"] == {"CREATE": 2}
        text = format_report(report)
        assert "completed: 2（100.0%）" in text
        assert "CREATE: 2（100.0%）" in text

    def test_cli_quality_report(self, tmp_path):
        from typer.testing import CliRunner

        from cli.main import app

        runner = CliRunner()
        empty = runner.invoke(app, ["quality-report", "--workdir", str(tmp_path)])
        assert empty.exit_code == 0
        assert "暂无质量档案" in empty.output

        project = _make_project(tmp_path / "p1")
        pipeline = _make_pipeline(tmp_path / "p1")
        pipeline.execute(_request(project, tmp_path / "p1", "做一个书架", "CREATE"))
        filled = runner.invoke(app, ["quality-report", "--workdir", str(tmp_path)])
        assert filled.exit_code == 0
        assert "共 1 份记录" in filled.output
        assert "completed" in filled.output


# ── store 层契约 ──────────────────────────────────────────

class TestStore:
    def test_load_records_skips_broken_json(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        pipeline.execute(_request(project, tmp_path, "做一个书架", "CREATE"))
        broken = project.root / ".openbrep" / "quality" / "runs" / "r_broken.json"
        broken.write_text("{not json", encoding="utf-8")

        records = load_records(tmp_path)
        assert len(records) == 1  # 坏文件跳过，好文件在

    def test_record_path_uses_run_id(self, tmp_path):
        path = record_path(tmp_path, "r_20260905_120000_abc123")
        assert path.name == "r_20260905_120000_abc123.json"

    def test_schema_validation_rejects_bad_outcome(self):
        record = QualityRecord(
            run_id="r_x", intent="CREATE", outcome="bogus",
            project_ref={"path_hash": "a" * 12, "name": "p"},
        )
        with pytest.raises(ValueError):
            record.validate()
