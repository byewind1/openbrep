"""D6：Fixed 模式 model + reasoning effort 的端到端红队测试（真实 fake app-server）。

契约（D6 派单 + 交付协议 v2 红队自查，全部实测）：
1. effort 对抗三条路径：
   - model/list 不支持的 effort 注入保存请求 → settings 层拒绝（见
     test_workbench_services / test_workbench_api）；此处验证运行时拒绝；
   - 模型切换后旧 effort 残留 → 保存拒绝（settings 层测试覆盖）；
   - 直接改配置文件绕过 UI（config 里手写不支持的 effort）→ 运行时 fail
     closed：稳定报错，零 turn/start 请求（fake server 参数日志反证）。
2. fallback 反证：Fixed 请求失败后，fake server 记录的后续请求逐一断言
   没有指向其他模型/provider 的 turn/start。
3. 完整性：reasoning 很长但 final 为空/截断 → 统一完整性错误，不交付空结果。
4. 可追溯：连续多次任务（含中途改设置）的结果元数据 effective model/effort
   与 fake server 实收完全一致。
5. micro modify 不调 LLM（既有契约，D6 不得破坏）——codex 配置下零 RPC 反证。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
from openbrep.codex.provider import CodexProvider
from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticVerificationResult

FAKE_SERVER = str(Path(__file__).resolve().parent / "fake_codex_app_server.py")

PLANNER_JSON = json.dumps(
    {
        "object_type": "参数化构件",
        "validation_checks": ["检查 2D 脚本是否可见", "检查 3D 脚本是否以 END 结束"],
    },
    ensure_ascii=False,
)

FULL_GDL = (
    "[FILE: paramlist.xml]\n"
    "Length A = 0.60 ! Shelf width\n"
    "Length B = 0.40 ! Shelf depth\n"
    "Length ZZYZX = 0.80 ! Total height\n"
    "\n"
    "[FILE: scripts/1d.gdl]\n"
    "! Master script placeholder\n"
    "\n"
    "[FILE: scripts/2d.gdl]\n"
    "PROJECT2 3, 270, 2\n"
    "HOTSPOT2 0, 0\n"
    "HOTSPOT2 A, 0\n"
    "HOTSPOT2 0, B\n"
    "HOTSPOT2 A, B\n"
    "\n"
    "[FILE: scripts/3d.gdl]\n"
    "BLOCK A, B, ZZYZX\n"
    "END\n"
)


def _sem_pass() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


def _ok_compiler() -> MagicMock:
    compiler = MagicMock()
    compiler.hsf2libpart.return_value = CompileResult(
        success=True, stdout="", stderr="", mode="lp",
        output_path="/tmp/x.gsm", exit_code=0,
    )
    return compiler


class _FakeServerHarness:
    """真实子进程 fake app-server + 参数日志 + 环境管理。"""

    def __init__(self, tmp_path, extra_env=None):
        self.tmp_path = Path(tmp_path)
        self.params_log = self.tmp_path / "params.jsonl"
        self.home = self.tmp_path / "codex-home"
        self.saved = {
            k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")
        }
        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_TURN_PARAMS_LOG": str(self.params_log),
        }
        env.update(extra_env or {})
        os.environ.update(env)

    def provider(self) -> CodexProvider:
        def factory():
            transport = StdioJsonRpcTransport(
                codex_binary=sys.executable,
                codex_home=self.home,
                extra_args=(FAKE_SERVER,),
                rpc_timeout=15.0,
            )
            return CodexAppServerClient(transport=transport)

        return CodexProvider(
            codex_home=self.home,
            client_factory=factory,
            cli_available=True,
            browser_opener=lambda url: None,
        )

    def cleanup(self):
        for k in list(os.environ):
            if k.startswith("FAKE_CODEX_"):
                os.environ.pop(k, None)
        os.environ.update(self.saved)

    def read_turn_params(self) -> list[dict]:
        if not self.params_log.exists():
            return []
        return [
            json.loads(ln)
            for ln in self.params_log.read_text(encoding="utf-8").splitlines()
            if ln.strip() and '"turn/start"' in ln
        ]

    def read_thread_params(self) -> list[dict]:
        if not self.params_log.exists():
            return []
        return [
            json.loads(ln)
            for ln in self.params_log.read_text(encoding="utf-8").splitlines()
            if ln.strip() and '"thread/start"' in ln
        ]


def _codex_pipeline(config, provider, tmp_path, compiler=None) -> TaskPipeline:
    pipeline = TaskPipeline(
        config=config,
        trace_dir=str(tmp_path / "traces"),
        codex_provider=provider,
    )
    if compiler is not None:
        config.compiler.path = "/fake/LP_XMLConverter"
        pipeline._make_compiler = lambda: compiler
    return pipeline


def _codex_config(model: str = "openai-codex/gpt-5.6-luna", effort: str = "") -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.model = model
    config.llm.reasoning_effort = effort
    config.llm.providers = [
        {"name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": []}
    ]
    return config


def _create_request(tmp_path, work_dir) -> TaskRequest:
    return TaskRequest(
        user_input="生成一个可参数化的构件",
        intent="CREATE",
        work_dir=str(work_dir),
        output_dir=str(tmp_path / "out"),
    )


def _chat_request() -> TaskRequest:
    return TaskRequest(user_input="你好，介绍一下你自己", intent="CHAT")


def _snapshot_log_lines(harness: _FakeServerHarness) -> int:
    if not harness.params_log.exists():
        return 0
    return len(harness.params_log.read_text(encoding="utf-8").splitlines())


def _sliced_turns(harness: _FakeServerHarness, start_line: int) -> list[dict]:
    if not harness.params_log.exists():
        return []
    lines = harness.params_log.read_text(encoding="utf-8").splitlines()
    return [
        json.loads(ln) for ln in lines[start_line:] if ln.strip() and '"turn/start"' in ln
    ]


# ── 1. Fixed 组合：CREATE 全程只发配置的 model+effort，元数据 == 实收 ────────


def test_create_effective_metadata_matches_server_received(tmp_path):
    harness = _FakeServerHarness(tmp_path, {"FAKE_CODEX_TURN_FINAL_TEXT": FULL_GDL})
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="high")
    pipeline = _codex_pipeline(config, provider, tmp_path, compiler=_ok_compiler())
    work_dir = tmp_path / "hsf-work"
    work_dir.mkdir()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_create_request(tmp_path, work_dir))
        assert result.success is True, result.verification
        # 元数据记录 effective 组合
        eff = result.metadata.get("codex_effective")
        assert eff == {"model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "high"}, eff
        # fake server 实收：所有 turn/start 都是同一 model + effort（无升级/降级/切换）
        turns = harness.read_turn_params()
        assert turns, "fake server 必须收到 turn/start"
        for entry in turns:
            params = entry["params"]
            assert params.get("model") == "openai-codex/gpt-5.6-luna"
            assert params.get("effort") == "high"
        # 元数据与实收逐字段一致
        assert turns[0]["params"]["model"] == "openai-codex/gpt-5.6-luna"
        assert turns[0]["params"]["effort"] == eff["reasoning_effort"]
    finally:
        provider.close()
        harness.cleanup()


def test_chat_effective_metadata_matches_server_received(tmp_path):
    harness = _FakeServerHarness(tmp_path)
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="medium")
    pipeline = _codex_pipeline(config, provider, tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_chat_request())
        assert result.success is True
        eff = result.metadata.get("codex_effective")
        assert eff == {"model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "medium"}, eff
        turns = harness.read_turn_params()
        assert turns
        assert turns[0]["params"]["effort"] == "medium"
        assert turns[0]["params"]["model"] == "openai-codex/gpt-5.6-luna"
    finally:
        provider.close()
        harness.cleanup()


def test_effort_empty_omits_effort_key_and_records_empty(tmp_path):
    """未保存 effort（模型默认模式）：turn/start 无 effort 键，元数据记空。"""
    harness = _FakeServerHarness(tmp_path, {"FAKE_CODEX_TURN_FINAL_TEXT": FULL_GDL})
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="")
    pipeline = _codex_pipeline(config, provider, tmp_path, compiler=_ok_compiler())
    work_dir = tmp_path / "hsf-work"
    work_dir.mkdir()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_create_request(tmp_path, work_dir))
        assert result.success is True
        eff = result.metadata.get("codex_effective")
        assert eff == {"model": "openai-codex/gpt-5.6-luna", "reasoning_effort": ""}, eff
        turns = harness.read_turn_params()
        assert turns
        for entry in turns:
            assert "effort" not in entry["params"]
    finally:
        provider.close()
        harness.cleanup()


# ── 2. 可追溯：多次任务（含中途改设置）元数据 == fake server 实收 ──────────


def test_multi_task_settings_changed_metadata_reconciled(tmp_path):
    harness = _FakeServerHarness(tmp_path, {"FAKE_CODEX_TURN_FINAL_TEXT": FULL_GDL})
    provider = harness.provider()
    work_dir = tmp_path / "hsf-work"
    work_dir.mkdir()
    try:
        # 任务 1：luna + high
        config = _codex_config("openai-codex/gpt-5.6-luna", effort="high")
        pipeline = _codex_pipeline(config, provider, tmp_path, compiler=_ok_compiler())
        start1 = _snapshot_log_lines(harness)
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            r1 = pipeline.execute(_chat_request())
        assert r1.success is True
        turns1 = _sliced_turns(harness, start1)
        assert turns1 and turns1[0]["params"]["effort"] == "high"
        assert r1.metadata["codex_effective"] == {
            "model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "high",
        }

        # 中途改设置：effort medium
        config.llm.reasoning_effort = "medium"
        start2 = _snapshot_log_lines(harness)
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            r2 = pipeline.execute(_chat_request())
        assert r2.success is True
        turns2 = _sliced_turns(harness, start2)
        assert turns2 and turns2[0]["params"]["effort"] == "medium"
        assert r2.metadata["codex_effective"]["reasoning_effort"] == "medium"

        # 中途改设置：切模型 terra + effort high
        config.llm.model = "openai-codex/gpt-5.6-terra"
        config.llm.reasoning_effort = "high"
        start3 = _snapshot_log_lines(harness)
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            r3 = pipeline.execute(_chat_request())
        assert r3.success is True
        turns3 = _sliced_turns(harness, start3)
        assert turns3
        assert turns3[0]["params"]["model"] == "openai-codex/gpt-5.6-terra"
        assert turns3[0]["params"]["effort"] == "high"
        assert r3.metadata["codex_effective"] == {
            "model": "openai-codex/gpt-5.6-terra", "reasoning_effort": "high",
        }

        # 每段实收与对应元数据逐字节一致
        assert turns1[0]["params"]["model"] == r1.metadata["codex_effective"]["model"]
        assert turns1[0]["params"]["effort"] == r1.metadata["codex_effective"]["reasoning_effort"]
        assert turns2[0]["params"]["effort"] == r2.metadata["codex_effective"]["reasoning_effort"]
        assert turns3[0]["params"]["model"] == r3.metadata["codex_effective"]["model"]
        assert turns3[0]["params"]["effort"] == r3.metadata["codex_effective"]["reasoning_effort"]
    finally:
        provider.close()
        harness.cleanup()


# ── 3. fallback 反证：Fixed 失败后零指向其他模型/provider 的请求 ──────────


def test_fixed_failure_no_fallback_to_other_model(tmp_path):
    """turn 级错误（quota 类信号）→ 任务失败；后续请求逐一断言只指向配置模型。"""
    harness = _FakeServerHarness(
        tmp_path,
        {"FAKE_CODEX_TURN_QUOTA_CANARY": "QUOTA-FIXED-D6"},
    )
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="high")
    pipeline = _codex_pipeline(config, provider, tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_chat_request())
        # 请求失败直接报告（稳定 quota 文案，绝不切模型/provider）
        assert result.success is False
        assert "额度" in (result.error or "")
        assert "QUOTA-FIXED-D6" not in (result.error or "")
        # fallback 反证：所有 thread/start 与 turn/start 只引用配置模型
        threads = harness.read_thread_params()
        turns = harness.read_turn_params()
        all_params = [e["params"] for e in threads + turns]
        assert all_params, "fake server 必须记录至少一次请求"
        for params in all_params:
            assert params.get("model") == "openai-codex/gpt-5.6-luna", (
                f"发现指向其他模型的请求: {params}"
            )
        # 失败后没有后续轮次（单次 CHAT 调用；无重试升级）
        assert len(turns) == 1
    finally:
        provider.close()
        harness.cleanup()


def test_fixed_failure_server_rejects_unsupported_effort_no_turn(tmp_path):
    """app-server 拒绝不支持的 effort（模拟真实上游）：零 turn/start，稳定报错。"""
    harness = _FakeServerHarness(
        tmp_path,
        {"FAKE_CODEX_REJECT_UNSUPPORTED_EFFORT": "1"},
    )
    provider = harness.provider()
    # terra 只支持 medium/high；config 直改 low（绕过 UI）→ 客户端本地校验先拒
    config = _codex_config("openai-codex/gpt-5.6-terra", effort="low")
    pipeline = _codex_pipeline(config, provider, tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_chat_request())
        assert result.success is False
        # 稳定文案（绝不回显上游原文 / 不支持的 effort 值）
        assert "low" not in (result.error or "")
        # 零 turn/thread 请求（本地门禁在 turn 启动前拦截）
        assert harness.read_turn_params() == []
        assert harness.read_thread_params() == []
    finally:
        provider.close()
        harness.cleanup()


# ── 4. 完整性：reasoning 很长但 final 为空/截断 → 统一完整性错误 ──────────


def test_long_reasoning_no_final_create_fails_closed(tmp_path):
    harness = _FakeServerHarness(
        tmp_path,
        {"FAKE_CODEX_TURN_REASONING_NO_FINAL": "1"},
    )
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="high")
    pipeline = _codex_pipeline(config, provider, tmp_path, compiler=_ok_compiler())
    work_dir = tmp_path / "hsf-work"
    work_dir.mkdir()
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_create_request(tmp_path, work_dir))
        # 统一完整性错误：不交付空结果 / 无占位项目
        assert result.success is False
        assert "未返回最终回复" in (result.error or "") or "未产出代码" in (result.error or "")
        assert result.project is None
        assert not result.scripts
        # fake server 收到的 effort 仍是配置值（Fixed 不变性）
        turns = harness.read_turn_params()
        assert turns
        for entry in turns:
            assert entry["params"]["effort"] == "high"
            assert entry["params"]["model"] == "openai-codex/gpt-5.6-luna"
    finally:
        provider.close()
        harness.cleanup()


# ── 5. micro modify 零 LLM（codex 配置下也不破坏既有契约）─────────────────


def _param_project(tmp_path) -> HSFProject:
    proj = HSFProject.create_new("test_shelf", work_dir=str(tmp_path / "micro-work"))
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    return proj


def test_micro_modify_zero_llm_with_codex_config(tmp_path):
    harness = _FakeServerHarness(tmp_path)
    provider = harness.provider()
    config = _codex_config("openai-codex/gpt-5.6-luna", effort="high")
    pipeline = _codex_pipeline(config, provider, tmp_path)
    project = _param_project(tmp_path)
    request = TaskRequest(
        user_input="把 shelf_count 改成 5",
        intent="MODIFY",
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
    )
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(request)
        assert result.success is True, result.error
        # micro modify 不调 LLM：fake server 零 turn/thread 请求
        assert harness.read_turn_params() == []
        assert harness.read_thread_params() == []
        # paramlist 值已确定性应用
        assert project.parameters[3].value == "5"
    finally:
        provider.close()
        harness.cleanup()
