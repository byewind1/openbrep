"""D9: D8-backed Auto routing policy, integration, and red-team guards."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openbrep.codex.routing import (
    LUNA_MODEL,
    TERRA_MODEL,
    choose_escalation,
    choose_initial_route,
    classify_create_complexity,
    run_auto_route,
)
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest, TaskResult


def _model(model: str, *efforts: str) -> dict:
    return {
        "id": model,
        "supported_reasoning_efforts": [{"effort": effort} for effort in efforts],
    }


CATALOG = [_model(LUNA_MODEL, "low", "medium", "high"), _model(TERRA_MODEL, "medium")]
SIGNED_IN = {"state": "signed_in", "connected": True}


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("做一个简单方块", "simple"),
        ("增加循环几何", "medium"),
        ("create a curtain wall system from scratch", "complex"),
    ],
)
def test_create_complexity_reuses_preflight_without_erasing_simple_bucket(instruction, expected):
    assert classify_create_complexity(instruction) == expected


@pytest.mark.parametrize(
    ("complexity", "expected_reason"),
    [
        ("simple", "simple/medium CREATE primary"),
        ("medium", "simple/medium CREATE primary"),
        ("complex", "complex CREATE has no primary"),
    ],
)
def test_d8_route_table_luna_low_only(complexity, expected_reason):
    decision = choose_initial_route(complexity, CATALOG, SIGNED_IN)
    assert decision.ok
    assert (decision.model, decision.reasoning_effort) == (LUNA_MODEL, "low")
    assert expected_reason in decision.reason


def test_simple_falls_back_to_terra_medium_when_luna_absent():
    decision = choose_initial_route("simple", [_model(TERRA_MODEL, "medium")], SIGNED_IN)
    assert decision.ok
    assert (decision.model, decision.reasoning_effort) == (TERRA_MODEL, "medium")


@pytest.mark.parametrize("complexity", ["medium", "complex"])
def test_missing_luna_stops_non_simple_without_unmeasured_fallback(complexity):
    catalog = [
        _model(TERRA_MODEL, "medium"),
        _model("openai-codex/gpt-5.6-sol", "medium", "high"),
        _model("openai-codex/gpt-5.5", "high"),
        _model("openai-codex/gpt-5.4", "medium"),
    ]
    decision = choose_initial_route(complexity, catalog, SIGNED_IN)
    assert not decision.ok
    assert decision.code == "auto_route_unavailable"
    assert not decision.model


def test_missing_required_effort_stops_even_when_model_id_exists():
    decision = choose_initial_route(
        "simple",
        [_model(LUNA_MODEL, "medium"), _model(TERRA_MODEL, "high")],
        SIGNED_IN,
    )
    assert not decision.ok
    assert decision.code == "auto_route_unavailable"


def test_quota_exhausted_stops_before_catalog_selection():
    decision = choose_initial_route(
        "simple",
        CATALOG,
        {"state": "quota_exhausted", "connected": True},
    )
    assert not decision.ok
    assert decision.code == "quota_exhausted"


def _verification(name="bbox_mismatch", check_type="semantic"):
    return {
        "checks": [
            {"name": name, "check_type": check_type, "status": "fail"},
        ]
    }


def test_sole_escalation_is_luna_low_to_high_once_and_marked_untested():
    initial = choose_initial_route("complex", CATALOG, SIGNED_IN)
    escalation = choose_escalation(initial, _verification(), CATALOG, attempts=0)
    assert escalation.ok
    assert (escalation.model, escalation.reasoning_effort) == (LUNA_MODEL, "high")
    assert escalation.escalation is True
    assert escalation.untested_escalation is True

    exhausted = choose_escalation(escalation, _verification(), CATALOG, attempts=1)
    assert not exhausted.ok
    assert exhausted.code == "auto_escalation_exhausted"


@pytest.mark.parametrize(
    "verification",
    [
        None,
        {},
        {"checks": []},
        {"checks": [{"name": "", "check_type": "semantic", "status": "fail"}]},
        {"checks": [{"name": "compile", "check_type": "compile", "status": "fail"}]},
    ],
)
def test_escalation_requires_localized_semantic_static_or_contract_failure(verification):
    initial = choose_initial_route("complex", CATALOG, SIGNED_IN)
    decision = choose_escalation(initial, verification, CATALOG, attempts=0)
    assert not decision.ok
    assert decision.code == "auto_failure_not_localized"


def test_localized_check_name_is_redacted_in_reason_and_metadata():
    initial = choose_initial_route("complex", CATALOG, SIGNED_IN)
    canary = "Authorization: Bearer DEV-AUTO-SECRET"
    decision = choose_escalation(initial, _verification(canary), CATALOG, attempts=0)
    text = json.dumps(decision.to_metadata(), ensure_ascii=False)
    assert decision.ok
    assert "DEV-AUTO-SECRET" not in text
    assert "Bearer" not in text


@dataclass
class _Result:
    success: bool
    verification: dict | None = None
    metadata: dict = field(default_factory=dict)
    plain_text: str = ""


def test_runner_records_effective_route_reason_and_untested_escalation():
    seen = []
    events = []

    def run(decision):
        seen.append((decision.model, decision.reasoning_effort))
        if len(seen) == 1:
            return _Result(False, _verification())
        return _Result(True)

    result = run_auto_route(
        complexity="complex",
        catalog=CATALOG,
        status=SIGNED_IN,
        run=run,
        make_stop_result=lambda _decision: _Result(False),
        on_event=lambda kind, data: events.append((kind, data)),
    )
    assert seen == [(LUNA_MODEL, "low"), (LUNA_MODEL, "high")]
    decisions = result.metadata["codex_auto_route"]["decisions"]
    assert decisions[0]["reason"]
    assert decisions[1]["untested_escalation"] is True
    assert any(kind == "status" and data.get("stage") == "retry" for kind, data in events)


def test_runner_cancel_before_escalation_starts_no_second_call():
    calls = []
    result = run_auto_route(
        complexity="complex",
        catalog=CATALOG,
        status=SIGNED_IN,
        run=lambda decision: calls.append(decision) or _Result(False, _verification()),
        make_stop_result=lambda _decision: _Result(False),
        on_event=lambda *_: None,
        should_cancel=lambda: True,
    )
    assert len(calls) == 1
    assert result.metadata["codex_auto_route"]["cancelled_before_escalation"] is True


class _Provider:
    def __init__(self, status=SIGNED_IN, catalog=CATALOG):
        self.status_value = status
        self.catalog = catalog
        self.status_calls = 0
        self.model_calls = 0

    def status(self, *, refresh=False):
        self.status_calls += 1
        return dict(self.status_value)

    def models(self, *, refresh=False):
        self.model_calls += 1
        return [dict(item) for item in self.catalog]


class _RecordingPipeline(TaskPipeline):
    def __init__(self, *args, outcomes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen = []
        self.outcomes = list(outcomes or [TaskResult(success=True, intent="CREATE")])

    def _handle_gdl(self, request):
        self.seen.append((self.config.llm.model, self.config.llm.reasoning_effort, request))
        return self.outcomes.pop(0)


def test_fixed_pipeline_request_fingerprint_is_unchanged_and_never_probes_provider(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = LUNA_MODEL
    config.llm.reasoning_effort = "medium"
    config.llm.codex_routing_mode = "fixed"
    provider = _Provider()
    pipeline = _RecordingPipeline(
        config=config,
        codex_provider=provider,
        trace_dir=str(tmp_path / "traces"),
    )
    request = TaskRequest(user_input="创建一个简单方块", intent="CREATE")

    result = pipeline.execute(request)

    assert pipeline.seen == [(LUNA_MODEL, "medium", request)]
    assert provider.status_calls == provider.model_calls == 0
    assert "codex_auto_route" not in result.metadata


def test_auto_pipeline_uses_policy_and_restores_saved_fixed_pair(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-sol"
    config.llm.reasoning_effort = "medium"
    config.llm.codex_routing_mode = "auto"
    provider = _Provider()
    pipeline = _RecordingPipeline(
        config=config,
        codex_provider=provider,
        trace_dir=str(tmp_path / "traces"),
    )

    result = pipeline.execute(TaskRequest(user_input="创建简单构件", intent="CREATE"))

    assert pipeline.seen[0][:2] == (LUNA_MODEL, "low")
    assert (config.llm.model, config.llm.reasoning_effort) == (
        "openai-codex/gpt-5.6-sol",
        "medium",
    )
    assert result.metadata["codex_auto_route"]["decisions"][0]["reason"]


def test_auto_pipeline_simple_task_uses_terra_fallback_when_luna_missing(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = LUNA_MODEL
    config.llm.codex_routing_mode = "auto"
    provider = _Provider(catalog=[_model(TERRA_MODEL, "medium")])
    pipeline = _RecordingPipeline(
        config=config,
        codex_provider=provider,
        trace_dir=str(tmp_path / "traces"),
    )
    result = pipeline.execute(TaskRequest(user_input="做一个简单方块", intent="CREATE"))
    assert result.success
    assert pipeline.seen[0][:2] == (TERRA_MODEL, "medium")


def test_auto_pipeline_quota_stops_without_catalog_or_generation_call(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = LUNA_MODEL
    config.llm.codex_routing_mode = "auto"
    provider = _Provider(status={"state": "quota_exhausted", "connected": True})
    pipeline = _RecordingPipeline(
        config=config,
        codex_provider=provider,
        trace_dir=str(tmp_path / "traces"),
    )
    result = pipeline.execute(TaskRequest(user_input="做一个方块", intent="CREATE"))
    assert not result.success
    assert result.metadata["codex_auto_route"]["stopped"]["code"] == "quota_exhausted"
    assert provider.model_calls == 0
    assert pipeline.seen == []


def test_auto_catalog_exception_is_stable_and_does_not_reflect_secret(tmp_path):
    class LeakyProvider(_Provider):
        def status(self, *, refresh=False):
            raise RuntimeError("Authorization: Bearer DEV-AUTO-CATALOG-SECRET")

    config = GDLAgentConfig()
    config.llm.model = LUNA_MODEL
    config.llm.codex_routing_mode = "auto"
    pipeline = _RecordingPipeline(
        config=config,
        codex_provider=LeakyProvider(),
        trace_dir=str(tmp_path / "traces"),
    )
    result = pipeline.execute(TaskRequest(user_input="创建", intent="CREATE"))
    text = json.dumps({"error": result.error, "metadata": result.metadata}, ensure_ascii=False)
    assert not result.success
    assert "DEV-AUTO-CATALOG-SECRET" not in text
    assert "Bearer" not in text


def _project(tmp_path: Path) -> HSFProject:
    project = HSFProject.create_new("d9_micro", work_dir=str(tmp_path))
    project.parameters = [
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4")
    ]
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1, 1, 1\nEND\n"
    project.save_to_disk()
    return project


def test_auto_micro_modify_uses_zero_codex_rpc_with_running_fake_server(tmp_path, monkeypatch):
    """Red-team proof: even with an initialized fake app-server, Auto micro modify adds zero RPC."""
    from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
    from openbrep.codex.provider import CodexProvider

    rpc_log = tmp_path / "rpc.jsonl"
    fake = Path(__file__).resolve().parent / "fake_codex_app_server.py"
    monkeypatch.setenv("FAKE_CODEX_SIGNED_IN", "1")
    monkeypatch.setenv("FAKE_CODEX_RPC_LOG", str(rpc_log))

    def factory():
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=tmp_path / "codex-home",
            extra_args=(str(fake),),
        )
        return CodexAppServerClient(transport=transport)

    provider = CodexProvider(
        codex_home=tmp_path / "codex-home",
        client_factory=factory,
        cli_available=True,
    )
    try:
        provider.status(refresh=True)
        before = rpc_log.read_text(encoding="utf-8").splitlines()
        config = GDLAgentConfig()
        config.llm.model = LUNA_MODEL
        config.llm.codex_routing_mode = "auto"
        pipeline = TaskPipeline(
            config=config,
            codex_provider=provider,
            trace_dir=str(tmp_path / "traces"),
        )
        result = pipeline.execute(
            TaskRequest(
                user_input="把 shelf_count 改成 5",
                intent="MODIFY",
                project=_project(tmp_path / "workspace"),
                work_dir=str(tmp_path / "workspace"),
            )
        )
        after = rpc_log.read_text(encoding="utf-8").splitlines()
        assert result.success
        assert before == after
        assert "确定性微修改，未调用 LLM" in result.plain_text
    finally:
        provider.close()


def test_escalation_cancel_interrupts_fake_turn_and_leaves_no_third_request(tmp_path, monkeypatch):
    """Red-team proof: cancelling Luna-high emits interrupt and no extra escalation."""
    from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
    from openbrep.codex.provider import CodexProvider

    fake = Path(__file__).resolve().parent / "fake_codex_app_server.py"
    rpc_log = tmp_path / "rpc.jsonl"
    script = tmp_path / "turn-script.jsonl"
    script.write_text(
        json.dumps([{"op": "final", "text": "first"}])
        + "\n"
        + json.dumps([{"op": "hang"}])
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CODEX_SIGNED_IN", "1")
    monkeypatch.setenv("FAKE_CODEX_TURN", "1")
    monkeypatch.setenv("FAKE_CODEX_TURN_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CODEX_RPC_LOG", str(rpc_log))

    def factory():
        return CodexAppServerClient(
            transport=StdioJsonRpcTransport(
                codex_binary=sys.executable,
                codex_home=tmp_path / "codex-home-cancel",
                extra_args=(str(fake),),
            )
        )

    provider = CodexProvider(
        codex_home=tmp_path / "codex-home-cancel",
        client_factory=factory,
        cli_available=True,
    )
    cancelled = threading.Event()

    def run(decision):
        timer = None
        if decision.reasoning_effort == "high":
            timer = threading.Timer(0.1, cancelled.set)
            timer.start()
        try:
            turn = provider.chat(
                [{"role": "user", "content": "create"}],
                model=decision.model,
                reasoning_effort=decision.reasoning_effort,
                should_cancel=cancelled.is_set,
                timeout=5,
            )
        finally:
            if timer is not None:
                timer.cancel()
        if decision.reasoning_effort == "low":
            return _Result(False, _verification())
        assert turn.finish_reason == "interrupted"
        return _Result(False, _verification())

    try:
        result = run_auto_route(
            complexity="complex",
            catalog=CATALOG,
            status=SIGNED_IN,
            run=run,
            make_stop_result=lambda _decision: _Result(False),
            on_event=lambda *_: None,
            should_cancel=cancelled.is_set,
        )
        methods = [json.loads(line)["method"] for line in rpc_log.read_text().splitlines()]
        assert methods.count("turn/start") == 2
        assert methods.count("turn/interrupt") == 1
        assert result.metadata["codex_auto_route"]["cancelled_during_escalation"] is True
        assert "stopped" not in result.metadata["codex_auto_route"]
    finally:
        provider.close()
