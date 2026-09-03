"""D4：Codex 文本 CREATE 接入 HSF 交付链的红队测试。

契约（D4 派单 + 交付协议 v2 红队自查）：
- fake Codex CREATE 产生完整 HSF，并经过现有 compile/verification 与 delivery gate；
- 对抗样本（空 final / 半截 [FILE:] / 缺 2D 脚本 / 参数丢失 / 夹带散文）全部走
  现有拒绝语义，不产生半成品 HSF；
- [FILE:] 路径注入（../../、绝对路径、symlink 段）不越出 HSF 目录；
- app-server 隔离反证：临时只读 cwd——fake server 记录每次收到的 cwd（从未收到
  真实 HSF/工作区路径），且对只读 probe 目录无写权限（OS 级反证）；
- CREATE 失败 project=None 且无残留项目目录（文件系统快照 diff）；
- 零 [FILE:] 重试一次后 hard fail（与现有 CREATE 契约一致，重试只发生在首轮零产出）；
- 非 codex 路径调用形态逐字节不变（benchmark replay 指纹安全，不重录语料）。
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticVerificationResult

# ── helpers ────────────────────────────────────────────────


class _CodexTurnResult:
    def __init__(self, content, finish_reason="stop", error=None):
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.usage = {}


class _SequencedCodexProvider:
    """按调用序返回 planner JSON 与生成文本的 CodexProvider.chat 替身。

    planner 判定：system 提示以 object_planner 的 system prompt 开头；
    其余调用（生成/重试/修复）按序消耗 gen_texts；队列耗尽后返回 no_final_message
    （模拟 turn 层无最终答复）。
    """

    _PLANNER_PREFIX = "You are an expert Archicad GDL object architect"

    def __init__(self, planner_json, gen_texts):
        self.planner_json = planner_json
        self.gen_texts = list(gen_texts)
        self.calls = []

    def chat(self, messages, model, **kwargs):
        self.calls.append({"messages": messages, "model": model, "kwargs": kwargs})
        system = ""
        if messages and isinstance(messages[0], dict):
            system = str(messages[0].get("content") or "")
        if system.startswith(self._PLANNER_PREFIX):
            return _CodexTurnResult(self.planner_json)
        if self.gen_texts:
            return _CodexTurnResult(self.gen_texts.pop(0))
        return _CodexTurnResult("", finish_reason="no_final_message", error="未返回最终回复")

    @property
    def generation_calls(self):
        return [
            c for c in self.calls
            if not str(c["messages"][0].get("content") or "").startswith(self._PLANNER_PREFIX)
        ]


def _sem_pass() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


def _ok_compiler() -> MagicMock:
    compiler = MagicMock()
    compiler.hsf2libpart.return_value = CompileResult(
        success=True, stdout="", stderr="", mode="lp",
        output_path="/tmp/x.gsm", exit_code=0,
    )
    return compiler


def _codex_pipeline(tmp_path, provider, *, compiler=None) -> TaskPipeline:
    """选中 openai-codex 模型的 pipeline（配置内存化，不落真实 config）。"""
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.providers = [
        {"name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": []}
    ]
    pipeline = TaskPipeline(
        config=config,
        trace_dir=str(tmp_path / "traces"),
        codex_provider=provider,
    )
    if compiler is not None:
        config.compiler.path = "/fake/LP_XMLConverter"
        pipeline._make_compiler = lambda: compiler
    return pipeline


def _make_request(
    tmp_path,
    user_input: str = "生成一个可参数化的构件",
    intent: str = "CREATE",
    work_dir=None,
    **kwargs,
) -> TaskRequest:
    return TaskRequest(
        user_input=user_input,
        intent=intent,
        work_dir=str(work_dir or tmp_path),
        output_dir=str(tmp_path / "out"),
        **kwargs,
    )


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

ONLY_3D_WITH_END = "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"

TRUNCATED_3D = "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\n"


def _run(pipeline, request, tmp_path):
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        return pipeline.execute(request)


# ── 1. 正常路径：fake Codex CREATE 产生完整 HSF，过 compile/verification ──


def test_codex_create_full_hsf_delivered(tmp_path):
    compiler = _ok_compiler()
    provider = _SequencedCodexProvider(PLANNER_JSON, [FULL_GDL])
    pipeline = _codex_pipeline(tmp_path, provider, compiler=compiler)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is True, result.verification
    assert result.project is not None
    # 脚本：3d/2d/1d/paramlist 全部落盘
    assert "BLOCK A, B, ZZYZX" in result.project.get_script(ScriptType.SCRIPT_3D)
    assert "PROJECT2" in result.project.get_script(ScriptType.SCRIPT_2D)
    assert result.project.get_script(ScriptType.MASTER).strip()
    names = [p.name for p in result.project.parameters]
    assert {"A", "B", "ZZYZX"} <= set(names)
    # 现有 delivery gate：verification.passed
    assert result.verification["passed"] is True
    compile_check = next(
        c for c in result.verification["checks"] if c["check_type"] == "compile"
    )
    assert compile_check["status"] == "pass"
    # 恰好 1 次生成调用（planner + 1 gen；首轮有产出 → 不重试）
    assert len(provider.generation_calls) == 1
    # HSF 目录真实落盘（compile 路径 save_to_disk）
    hsf_dir = result.project.root
    assert hsf_dir.is_dir()
    assert (hsf_dir / "scripts" / "3d.gdl").exists()
    assert (hsf_dir / "paramlist.xml").exists()


# ── 2. 对抗样本：全部走现有拒绝语义，不产生半成品 HSF ────────────────────


def test_codex_create_empty_final_fails_closed_no_leftover(tmp_path):
    """turn 层无最终答复（空 final）→ 稳定文案失败、project=None、无残留目录。"""
    compiler = _ok_compiler()
    # gen_texts 为空 → 首次生成调用即返回 no_final_message
    provider = _SequencedCodexProvider(PLANNER_JSON, [])
    pipeline = _codex_pipeline(tmp_path, provider, compiler=compiler)

    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        result = pipeline.execute(_make_request(tmp_path))

    assert result.success is False
    assert result.project is None, "turn 失败必须 project=None"
    assert "未返回最终回复" in (result.error or "")
    # 无残留项目目录（create_new 只是内存占位，从未 save_to_disk）
    assert not (tmp_path / "untitled").exists()
    # 项目目录树除 traces 外零新增
    leftovers = [p for p in tmp_path.iterdir() if p.name != "traces" and p.name != "out"]
    assert leftovers == []


def test_codex_create_turn_error_fails_closed(tmp_path):
    """turn 级 error（额度/上游失败）→ 稳定文案、project=None、零残留。"""

    class _ErrorProvider(_SequencedCodexProvider):
        def __init__(self, planner_json):
            super().__init__(planner_json, [])

        def chat(self, messages, model, **kwargs):
            system = str(messages[0].get("content") or "") if messages else ""
            if system.startswith(self._PLANNER_PREFIX):
                return _CodexTurnResult(self.planner_json)
            # 生成调用：turn 级 error（上游失败，文案已由 turn 层归一化为稳定文案）
            return _CodexTurnResult(
                "", finish_reason="error", error="Codex 对话失败，请稍后重试。"
            )

    provider = _ErrorProvider(PLANNER_JSON)
    pipeline = _codex_pipeline(tmp_path, provider)
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        result = pipeline.execute(_make_request(tmp_path))
    assert result.success is False
    assert result.project is None
    assert "请稍后重试" in (result.error or "")
    assert not (tmp_path / "untitled").exists()


def test_codex_create_half_cut_header_retry_once_then_hard_fail(tmp_path):
    """半截 [FILE:]（仅头部无内容）→ 首轮零产出 → 重试一次 → 仍零 → hard fail。

    与现有 CREATE 零产出契约完全一致：project=None、无残留目录、恰好 2 次生成调用。
    """
    compiler = _ok_compiler()
    provider = _SequencedCodexProvider(
        PLANNER_JSON,
        ["[FILE: scripts/3d.gdl]\n", "先规划一下，请确认尺寸需求。"],
    )
    pipeline = _codex_pipeline(tmp_path, provider, compiler=compiler)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is False
    assert result.project is None
    assert "模型未产出代码" in result.plain_text
    assert len(provider.generation_calls) == 2, "零产出必须恰好重试一次"
    assert not (tmp_path / "untitled").exists()
    # 重试指令携带硬约束（与现有契约一致）
    retry_texts = "\n".join(
        str(c["messages"][-1]["content"]) for c in provider.generation_calls
    )
    assert "不要提问、不要只输出计划，直接输出完整代码文件。" in retry_texts


def test_codex_create_truncated_script_blocked(tmp_path):
    """半截脚本（BLOCK 无 END、无 2D）→ 现有多重拒绝语义全部生效。"""
    provider = _SequencedCodexProvider(PLANNER_JSON, [TRUNCATED_3D])
    pipeline = _codex_pipeline(tmp_path, provider)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is False
    assert result.verification["passed"] is False
    checks = {c["name"]: c["status"] for c in result.verification["checks"]}
    # plan 校验：2D 为空、3D 未以 END 结束
    assert checks["检查 2D 脚本是否可见"] == "fail"
    assert checks["检查 3D 脚本是否以 END 结束"] == "fail"
    # delivery-integrity：BLOCK A, B, ZZYZX 仍是占位脚本 → 阻断
    assert any(
        "[placeholder_delivery]" in e for e in result.verification["errors_caught"]
    )


def test_codex_create_missing_2d_script_blocked(tmp_path):
    """缺 2D 脚本 → plan 校验「2D 脚本是否可见」FAIL，不交付半成品。"""
    provider = _SequencedCodexProvider(PLANNER_JSON, [ONLY_3D_WITH_END])
    pipeline = _codex_pipeline(tmp_path, provider)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is False
    assert result.verification["passed"] is False
    # 2D 为空 → plan 校验「检查 2D 脚本是否可见」FAIL（现有拒绝语义）
    checks = {c["name"]: c["status"] for c in result.verification["checks"]}
    assert checks["检查 2D 脚本是否可见"] == "fail"


def test_codex_create_param_loss_blocked(tmp_path):
    """参数丢失：paramlist 缺保留参数 A/B/ZZYZX → delivery-integrity 阻断。"""
    gen_text = (
        "[FILE: paramlist.xml]\n"
        "Length w = 1.0 ! width\n"
        "Length d = 1.0 ! depth\n"
        "Length h = 1.0 ! height\n"
        "\n"
        "[FILE: scripts/3d.gdl]\n"
        "BLOCK w, d, h\n"
        "END\n"
    )
    provider = _SequencedCodexProvider(PLANNER_JSON, [gen_text])
    pipeline = _codex_pipeline(tmp_path, provider)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is False
    assert result.verification["passed"] is False
    errors = "\n".join(result.verification["errors_caught"])
    assert "[reserved_params_missing]" in errors


def test_codex_create_ellipsis_stub_prose_blocked(tmp_path):
    """夹带散文 + 退化标记（...行）→ ellipsis_stub 静态错误阻断交付。"""
    gen_text = (
        "[FILE: scripts/3d.gdl]\n"
        "BLOCK A, B, ZZYZX\n"
        "...\n"
        "END\n"
        "[FILE: scripts/2d.gdl]\n"
        "PROJECT2 3, 270, 2\n"
    )
    provider = _SequencedCodexProvider(PLANNER_JSON, [gen_text])
    pipeline = _codex_pipeline(tmp_path, provider)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    assert result.success is False
    assert result.verification["passed"] is False
    assert any("[ellipsis_stub]" in e for e in result.verification["errors_caught"])


def test_codex_create_prose_parity_with_non_codex(tmp_path):
    """夹带散文：codex 路径与现有（非 codex）路径逐字节同语义、同拒绝结果。

    同一 final 文本分别走 codex turn 与现有 llm.generate——verification 报告
    必须完全一致（prose 行进 unknown_command 警告，不阻断；行为零分叉）。
    """
    prose_text = (
        "[FILE: scripts/3d.gdl]\n"
        "下面是完整的 3D 脚本，请查收：\n"
        "REMOVE_ME_PLZ 123\n"
        "BLOCK A, B, ZZYZX\n"
        "END\n"
        "[FILE: scripts/2d.gdl]\n"
        "PROJECT2 3, 270, 2\n"
    )

    # ── codex 路径 ──
    provider = _SequencedCodexProvider(PLANNER_JSON, [prose_text])
    pipeline = _codex_pipeline(tmp_path, provider)
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        codex_result = pipeline.execute(_make_request(tmp_path))

    # ── 非 codex 路径（同一 planner JSON + 同一生成文本）──
    config = GDLAgentConfig()
    npipeline = TaskPipeline(config=config, trace_dir=str(tmp_path / "tr2"))
    mock_llm = MagicMock()
    mock_llm.generate.side_effect = [
        LLMResponse(content=PLANNER_JSON, model="mock", usage={}, finish_reason="stop"),
        LLMResponse(content=prose_text, model="mock", usage={}, finish_reason="stop"),
    ]
    npipeline._make_llm = lambda req: mock_llm
    compiler = _ok_compiler()
    npipeline._make_compiler = lambda: compiler
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        non_codex_result = npipeline.execute(_make_request(tmp_path))

    assert codex_result.success == non_codex_result.success
    assert codex_result.verification == non_codex_result.verification, (
        "codex 与现有路径对同一 final 的验证结果必须逐字节一致"
    )
    # 散文行作为未知命令进入 warnings（现有语义：警告不阻断）
    assert any("unknown_command" in w for w in codex_result.verification["warnings_caught"])


# ── 3. 路径注入：不越出 HSF 目录 ──────────────────────────


def test_codex_create_path_injection_no_escape(tmp_path):
    """[FILE:] 路径注入（../../、绝对路径、symlink 段）不得写出 HSF 目录。"""
    gen_text = (
        "[FILE: ../../evil.gdl]\n"
        "BLOCK 1, 1, 1\n"
        "END\n"
        "[FILE: /etc/passwd.gdl]\n"
        "EVIL\n"
        "[FILE: scripts/../escape.gdl]\n"
        "EVIL2\n"
        "[FILE: scripts/link/3d.gdl]\n"
        "BLOCK 9, 9, 9\n"
        "END\n"
        "[FILE: scripts/3d.gdl]\n"
        "BLOCK A, B, ZZYZX\n"
        "END\n"
        "[FILE: scripts/2d.gdl]\n"
        "PROJECT2 3, 270, 2\n"
    )
    compiler = _ok_compiler()
    provider = _SequencedCodexProvider(PLANNER_JSON, [gen_text])
    pipeline = _codex_pipeline(tmp_path, provider, compiler=compiler)
    result = _run(pipeline, _make_request(tmp_path), tmp_path)

    # 合法部分照常交付（注入的路径不阻断现有解析）
    assert result.success is True
    # 注入路径绝不进入项目脚本（_apply_changes 只认 ScriptType/paramlist）
    project = result.project
    assert set(project.scripts.keys()) <= set(ScriptType)
    assert "evil.gdl" not in str(project.scripts)
    assert "escape.gdl" not in str(project.scripts)
    # 磁盘树：HSF 目录之外没有任何新文件，目录内也没有 evil/escape/passwd
    hsf_dir = project.root
    disk_files = {str(p.relative_to(hsf_dir)) for p in hsf_dir.rglob("*") if p.is_file()}
    assert not any("evil" in f or "escape" in f or "passwd" in f for f in disk_files)
    for p in tmp_path.rglob("*"):
        if p.is_file() and ("evil" in p.name or "escape" in p.name or "passwd" in p.name):
            raise AssertionError(f"路径注入逃逸：{p}")
    # symlink 段不产生任何符号链接
    assert not any(p.is_symlink() for p in tmp_path.rglob("*"))


# ── 4. app-server 隔离反证：临时只读 cwd ───────────────────


def _provider_with_fake_server(tmp_path):
    """真实子进程 fake app-server 支撑的 CodexProvider（含登录态）。"""
    import sys

    from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
    from openbrep.codex.provider import CodexProvider

    fake_server = str(Path(__file__).parent / "fake_codex_app_server.py")
    home = tmp_path / "codex-home"

    def factory():
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=home,
            extra_args=(fake_server,),
            rpc_timeout=10.0,
        )
        return CodexAppServerClient(transport=transport)

    return CodexProvider(
        codex_home=home,
        client_factory=factory,
        cli_available=True,
        browser_opener=lambda url: None,
    )


def test_codex_create_app_server_isolation_real_subprocess(tmp_path):
    """端到端：真实 fake app-server 子进程跑完整 Codex CREATE。

    隔离反证（红队项）：
    - fake server 记录每次收到的 cwd → 断言全部落在临时只读 cwd（openbrep-codex-turn-*），
      从未收到真实 HSF 目录/工作区路径；
    - fake server 对只读 probe 目录写 canary → 失败（OS 级无写权限反证）；
    - 工作区/HSF 目录树无 canary、无注入文件。
    """
    from openbrep.codex.provider import set_default_codex_provider

    hsf_workdir = tmp_path / "hsf-work"
    hsf_workdir.mkdir()
    probe_dir = tmp_path / "probe-ro"
    probe_dir.mkdir()
    (probe_dir / "keep.txt").write_text("payload", encoding="utf-8")
    # OS 级无写权限：probe 目录只读（fake server 尝试写 canary 必须失败）
    os.chmod(probe_dir, stat.S_IRUSR | stat.S_IXUSR)

    cwd_log = tmp_path / "cwd-log.jsonl"
    env = {
        "FAKE_CODEX_TURN": "1",
        "FAKE_CODEX_SIGNED_IN": "1",
        "FAKE_CODEX_TURN_FINAL_TEXT": FULL_GDL,
        "FAKE_CODEX_CWD_LOG": str(cwd_log),
        "FAKE_CODEX_WRITE_PROBE": str(probe_dir),
    }
    saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
    os.environ.update(env)
    provider = _provider_with_fake_server(tmp_path)
    try:
        compiler = _ok_compiler()
        pipeline = _codex_pipeline(
            tmp_path, provider, compiler=compiler,
        )
        request = _make_request(tmp_path, work_dir=str(hsf_workdir))
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(request)

        # 完整 HSF 经现有 compile/verification 交付
        assert result.success is True, result.verification
        assert result.project is not None
        hsf_dir = result.project.root
        assert hsf_dir.is_dir()

        # 反证 1：fake server 只见过临时 cwd（从未收到 HSF 目录/工作区/probe）
        lines = [ln for ln in cwd_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        entries = [json.loads(ln) for ln in lines]
        assert entries, "cwd log 必须有记录"
        temp_root = Path(tempfile.gettempdir()).resolve()
        for e in entries:
            cwd = Path(e["cwd"])
            assert cwd.name.startswith("openbrep-codex-turn-"), f"cwd 必须是临时目录: {e}"
            assert cwd.parent.resolve() == temp_root, f"cwd 必须落在系统临时根: {e}"
            assert str(cwd) not in (str(hsf_workdir), str(hsf_dir), str(probe_dir))
            # 自己的 scratch 可写（app-server 操作自己的临时目录）
            assert e["cwd_write"]["ok"] is True, e
            # probe（只读目录）不可写 → OS 级无写权限
            assert e["probe_write"]["ok"] is False, e

        # 反证 2：probe 目录无 canary、字节不变
        assert not any("canary" in p.name for p in probe_dir.rglob("*"))
        assert (probe_dir / "keep.txt").read_text(encoding="utf-8") == "payload"
        # HSF 目录内没有 fake server 写的任何 canary
        assert not any("canary" in p.name for p in hsf_dir.rglob("*"))
    finally:
        os.chmod(probe_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        try:
            provider.close()
        finally:
            for k in list(os.environ):
                if k.startswith("FAKE_CODEX_"):
                    os.environ.pop(k, None)
            os.environ.update(saved)
            set_default_codex_provider(None)


# ── 4.5 D4 范围锁：MODIFY/DEBUG 仍 fail closed（不把生成类请求发给订阅模型）──


def test_codex_modify_intent_fails_closed_without_cli(tmp_path):
    """Codex MODIFY 由桥接执行，缺少 CLI 时稳定 fail closed。"""
    project = HSFProject.create_new("Shelf", str(tmp_path / "proj"))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    hsf_dir = project.save_to_disk()
    provider = _SequencedCodexProvider(PLANNER_JSON, [FULL_GDL])
    pipeline = _codex_pipeline(tmp_path, provider)

    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        result = pipeline.execute(
            _make_request(
                tmp_path,
                user_input="把整体造型改成圆弧形",
                intent="MODIFY",
                work_dir=str(Path(hsf_dir).parent),
            )
        )

    assert result.success is False
    assert "未检测到 Codex CLI" in (result.error or "")
    # 未登录 provider 之外：MODIFY 绝不能把请求发给订阅模型
    assert provider.generation_calls == []


# ── 5. 失败语义：project=None + 无残留 + 回放指纹安全 ──────


def test_codex_create_failure_no_residual_directories(tmp_path):
    """文件系统快照 diff：CREATE 失败（turn 失败 / 零产出 hard fail）零残留。"""
    def snapshot(root: Path):
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and "traces" not in str(p)
        }

    work = tmp_path / "ws"
    work.mkdir()
    (work / "keep.txt").write_text("keep", encoding="utf-8")
    before = snapshot(work)

    # turn 级失败
    provider = _SequencedCodexProvider(PLANNER_JSON, [])
    pipeline = _codex_pipeline(tmp_path, provider)
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        r1 = pipeline.execute(_make_request(tmp_path, work_dir=str(work)))
    assert r1.success is False and r1.project is None
    after1 = snapshot(work)
    assert before == after1, "turn 失败不得在工作区创建任何文件"

    # 零产出 hard fail
    provider2 = _SequencedCodexProvider(PLANNER_JSON, ["我在规划", "还在思考"])
    pipeline2 = _codex_pipeline(tmp_path, provider2)
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        r2 = pipeline2.execute(_make_request(tmp_path, work_dir=str(work)))
    assert r2.success is False and r2.project is None
    after2 = snapshot(work)
    assert before == after2, "零产出 hard fail 不得在工作区创建任何文件"


def test_codex_create_non_codex_call_shape_byte_identical(tmp_path):
    """非 codex 路径调用形态不变：llm.generate 不携带任何 codex kwargs
    （benchmark replay 指纹 = sha256(messages, kwargs) 不变，无需重录语料）。"""
    config = GDLAgentConfig()
    pipeline = TaskPipeline(config=config, trace_dir=str(tmp_path / "tr"))
    mock_llm = MagicMock()
    mock_llm.generate.side_effect = [
        LLMResponse(content=PLANNER_JSON, model="mock", usage={}, finish_reason="stop"),
        LLMResponse(content=FULL_GDL, model="mock", usage={}, finish_reason="stop"),
    ]
    pipeline._make_llm = lambda req: mock_llm
    compiler = _ok_compiler()
    pipeline._make_compiler = lambda: compiler
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        result = pipeline.execute(_make_request(tmp_path))

    assert result.success is True
    for call in mock_llm.generate.call_args_list:
        assert call.kwargs == {}, f"非 codex 调用不得携带 kwargs: {call.kwargs}"
