"""D10：Codex MODIFY 动态工具桥接合同测试（fake app-server 端到端）。

覆盖 D10 验收标准与红队清单：
- 成功流：工具调用 → final → 完成门禁 → success；写操作全部可审计。
- 多轮：门禁打回 → 证据注入下一 turn → 修复 → 通过。
- 预算耗尽 / 编译失败 / 取消 / app-server 崩溃 / 重复 callback。
- 工具面对抗：shell / apply_patch / MCP / 未注册名 / 命名空间工具全部拒绝。
- callback 关联：重复 call id 只执行一次；错 turn / 错 thread / 迟到 / 未预期
  服务器请求全部拒绝且不污染后续轮次。
- gate 绕过对抗：无工具 final（含 [FILE:]）不交付；编译失败后无视反馈直接
  final 不 success。
- 守卫回归：prose leak 与 String paramlist 引用守卫照样拦截（经工具结果文本）。
- epoch 守卫：会话项目切换后拒绝后续 mutation。
- flag 边界：默认 false 时 pipeline 在桥接入口 fail closed（零 RPC）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.runtime.modify_codex_bridge import CodexModifyTurnDriver
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticIssue, SemanticVerificationResult

FAKE_SERVER = str(Path(__file__).resolve().parent / "fake_codex_app_server.py")


def _sem_pass() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


def _sem_blocking() -> SemanticVerificationResult:
    return SemanticVerificationResult(
        passed=False,
        issues=[SemanticIssue(check_type="mesh_empty", detail="几何为空", blocking=True)],
    )


class _FailingCompiler:
    """编译永远失败（完成门禁与 compile_script 工具共用）。"""

    def hsf2libpart(self, hsf_dir, output_gsm) -> CompileResult:
        return CompileResult(
            success=False, exit_code=1, stdout="", stderr="syntax error: line 3", mode="lp",
        )


class _FakeServerHarness:
    """真实子进程 fake app-server + 参数日志 + 动态工具日志 + 环境管理。"""

    def __init__(self, tmp_path, extra_env=None):
        self.tmp_path = Path(tmp_path)
        self.params_log = self.tmp_path / "params.jsonl"
        self.dyn_log = self.tmp_path / "dyn.jsonl"
        self.cwd_log = self.tmp_path / "cwd.jsonl"
        self.home = self.tmp_path / "codex-home"
        self.saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_TURN_PARAMS_LOG": str(self.params_log),
            "FAKE_CODEX_DYN_LOG": str(self.dyn_log),
            "FAKE_CODEX_CWD_LOG": str(self.cwd_log),
        }
        env.update(extra_env or {})
        os.environ.update(env)

    def provider(self):
        from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
        from openbrep.codex.provider import CodexProvider

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

    def read_params(self, method: str) -> list[dict]:
        if not self.params_log.exists():
            return []
        out = []
        for ln in self.params_log.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("method") == method:
                out.append(rec.get("params") or {})
        return out

    def read_dyn(self) -> list[dict]:
        if not self.dyn_log.exists():
            return []
        return [
            json.loads(ln)
            for ln in self.dyn_log.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]

    def read_cwd_log(self) -> list[dict]:
        if not self.cwd_log.exists():
            return []
        out = []
        for ln in self.cwd_log.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            out.append(rec)
        return out


def _codex_config(
    model: str = "openai-codex/gpt-5.6-luna", modify_enabled: bool = True,
) -> GDLAgentConfig:
    config = GDLAgentConfig()
    config.llm.model = model
    config.llm.codex_modify_enabled = modify_enabled
    config.llm.providers = [
        {"name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": []}
    ]
    return config


def _make_project(tmp_path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return proj


def _pipeline(config, provider, tmp_path, compiler=None) -> TaskPipeline:
    pipeline = TaskPipeline(
        config=config,
        trace_dir=str(tmp_path / "traces"),
        codex_provider=provider,
    )
    if compiler is not None:
        config.compiler.path = "/fake/LP_XMLConverter"
        pipeline._make_compiler = lambda: compiler
    return pipeline


def _request(tmp_path, project, **overrides) -> TaskRequest:
    kwargs = dict(
        user_input="给书架加一层层板",
        intent="MODIFY",
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
        agent_loop=True,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


def test_modify_wire_params_strip_codex_namespace_only():
    driver = CodexModifyTurnDriver(
        client=object(),
        model="openai-codex/gpt-5.6-luna",
        cwd="/tmp/openbrep-hf1",
        system_text="sys",
        dynamic_tools=[],
        executor=lambda *_args: ("", True),
        timeout=1,
        should_cancel=None,
        on_delta=None,
    )
    assert driver._thread_start_params()["model"] == "gpt-5.6-luna"
    assert driver._turn_start_params("th-1", "hi")["model"] == "gpt-5.6-luna"
    driver._model = "other-provider/model"
    assert driver._thread_start_params()["model"] == "other-provider/model"


def _write_script(tmp_path, turns: list[list[dict]]) -> Path:
    """写 turn 脚本并把 FAKE_CODEX_TURN_SCRIPT 指向它（fake 子进程按 turn 消费）。"""
    p = tmp_path / "turn_script.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for steps in turns:
            f.write(json.dumps(steps, ensure_ascii=False) + "\n")
    os.environ["FAKE_CODEX_TURN_SCRIPT"] = str(p)
    return p


# ── 工具脚本片段 ───────────────────────────────────────────

def _tool(tool: str, arguments: dict | None = None, **extra) -> dict:
    step = {"op": "tool_call", "tool": tool, "arguments": arguments or {}}
    step.update(extra)
    return step


def _final(text: str = "已按计划完成修改，编译通过。") -> dict:
    return {"op": "final", "text": text}


def _upd(content: str, **extra) -> dict:
    return _tool("update_script", {"file_path": "scripts/3d.gdl", "content": content}, **extra)


# ── 主流程：成功 / 审计 / 多轮 / 预算 / gate 绕过 ─────────────

def test_success_flow_tools_audited_and_scripts_changed(tmp_path):
    """成功流：update → compile → final → 门禁通过；写操作全审计。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"),
                _tool("compile_script"),
                _final("已加一层层板，编译通过。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        assert "ADDZ ZZYZX" in project.get_script(ScriptType.SCRIPT_3D)
        md = result.metadata["codex_modify"]
        assert md["turns"] == 1
        assert md["tool_calls"] == 2
        assert md["enabled"] is True
        # 审计：2 次执行（executed=True），call_id 与 wire request 关联
        audit = md["tool_audit"]
        assert len(audit) == 2
        assert all(e["executed"] and e["ok"] for e in audit)
        assert all(e.get("request_ids") for e in audit)
        # 写路径唯一入口是 ModifyToolRegistry：changed_files ⊆ tool_log 工具
        assert set(result.scripts.keys()) <= {"scripts/3d.gdl"}
    finally:
        provider.close()
        harness.cleanup()


def test_initialize_negotiates_experimental_api_and_scratch_cwd(tmp_path):
    """initialize 协商 experimentalApi；app-server 只见到一次性临时 cwd。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(tmp_path, [[_final()]])
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        inits = [d for d in harness.read_dyn() if d.get("event") == "initialize"]
        assert inits
        caps = (inits[0].get("params") or {}).get("capabilities") or {}
        assert caps.get("experimentalApi") is True
        threads = [d for d in harness.read_dyn() if d.get("event") == "thread/start"]
        assert threads
        assert threads[0]["params"].get("dynamicTools") is not None
        # cwd 隔离：app-server 只见过 openbrep-codex-modify-* 临时目录
        cwd_entries = harness.read_cwd_log()
        assert cwd_entries, "expected cwd log entries"
        project_roots = {str(project.root), str(tmp_path)}
        for rec in cwd_entries:
            cwd = str(rec.get("cwd") or "")
            assert cwd.startswith(tempfile.gettempdir()), cwd
            assert "openbrep-codex-modify-" in cwd, cwd
            assert cwd not in project_roots, f"app-server saw real project cwd: {cwd}"
    finally:
        provider.close()
        harness.cleanup()


def test_gate_rejection_feeds_back_into_next_turn(tmp_path):
    """多轮：门禁打回（语义证据）→ 下一 turn 输入含反馈 → 修复后通过。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [_final("改完了。")],
            [_tool("compile_script"), _final("这次真的修好了。")],
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem_blocking(), _sem_pass()],
        ):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        assert md["turns"] == 2
        assert md["gate_rejections"] == 1
        assert "打回 1 次" in result.plain_text
        # 第二 turn 的输入必须含门禁反馈（证据注入）
        turns = harness.read_params("turn/start")
        assert len(turns) == 2
        second_input = turns[1].get("input") or []
        second_text = "".join(str(i.get("text") or "") for i in second_input if isinstance(i, dict))
        assert "完成门禁未通过" in second_text, second_text
    finally:
        provider.close()
        harness.cleanup()


def test_gate_bypass_final_without_tools_rejected(tmp_path):
    """gate 绕过对抗：跳过工具直接"完成"final——完成门禁拒绝，且打回有界。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [_final("改完了，编译通过，无需工具。")],
            [_final("还是通过了。")],
            [_final("再确认一次。")],
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_blocking()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success is False, result.plain_text
        md = result.metadata["codex_modify"]
        assert md["turns"] == 3  # 1 + MAX_GATE_REJECTIONS 次打回
        assert md["gate_rejections"] == 2
        assert "门禁未通过" in result.plain_text
        assert result.scripts == {}
    finally:
        provider.close()
        harness.cleanup()


def test_compile_failure_feedback_then_unresolved(tmp_path):
    """编译失败→门禁打回（反馈含编译错误）→ 无视反馈直接 final → 不 success。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [_tool("compile_script"), _final("改完了。")],
            [_final("我确认完成。")],
            [_final("完成。")],
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path, compiler=_FailingCompiler())
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success is False, result.plain_text
        assert "编译失败" in result.plain_text
        turns = harness.read_params("turn/start")
        assert len(turns) == 3
        second_text = "".join(
            str(i.get("text") or "") for i in (turns[1].get("input") or []) if isinstance(i, dict)
        )
        assert "编译失败" in second_text, second_text
    finally:
        provider.close()
        harness.cleanup()


def test_budget_exhaustion_fails_closed(tmp_path):
    """预算耗尽：第 N+1 次工具调用被拒绝，如实报告，不继续执行。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _upd("BLOCK A, B, ZZYZX\nEND\n"),
                _tool("compile_script"),
                _tool("preview_geometry"),
                _final("进度总结。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project, agent_loop_budget=2))
        md = result.metadata["codex_modify"]
        assert md["tool_calls"] == 2
        assert md["budget_exhausted"] is True
        assert "预算耗尽" in result.plain_text
        # 第 3 次调用被拒绝且审计留痕
        audit = md["tool_audit"]
        assert any(
            e["tool"] == "preview_geometry" and e["rejected_reason"] == "budget_exhausted"
            for e in audit
        )
    finally:
        provider.close()
        harness.cleanup()


# ── 工具面对抗：shell / apply_patch / MCP / 未注册 / namespace ──

def test_tool_surface_attack_all_rejected_with_audit(tmp_path):
    """fake server 尝试 shell / apply_patch / MCP / 未注册名 / namespace 工具。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                {"op": "exploit_tool", "tool": "shell",
                 "arguments": {"command": "rm -rf /tmp/CANARY-EVIL"}},
                {"op": "exploit_tool", "tool": "apply_patch", "arguments": {"patch": "evil"}},
                {"op": "exploit_tool", "tool": "mcp__filesystem__write",
                 "arguments": {"path": "/tmp/evil"}},
                {"op": "exploit_tool", "tool": "not_registered_tool", "arguments": {}},
                {"op": "tool_call", "tool": "update_script",
                 "arguments": {
                     "file_path": "scripts/3d.gdl",
                     "content": "BLOCK A, B, ZZYZX\nEND\n",
                 },
                 "namespace": "tickets"},
                _final(),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    original = project.get_script(ScriptType.SCRIPT_3D)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        audit = md["tool_audit"]
        reasons = {e.get("rejected_reason") for e in audit}
        assert "dangerous_tool_name" in reasons, reasons
        assert "tool_not_allowed" in reasons, reasons
        # namespace 工具帧校验失败（driver 层拒绝）
        wire = md["wire_requests"]
        ns_rejected = [
            r for r in wire
            if r.get("call_id") and r.get("tool") == "update_script" and not r.get("success")
        ]
        assert ns_rejected, wire
        # 零写入：脚本与磁盘都未被触碰
        assert project.get_script(ScriptType.SCRIPT_3D) == original
        assert result.scripts == {}
        # 4 个拒绝在 executor 审计（shell/apply_patch/mcp/未注册名）；
        # namespace 帧校验在 driver 层（wire_requests，已断言）
        assert len(result.metadata["codex_modify"]["tool_audit"]) == 4
    finally:
        provider.close()
        harness.cleanup()


def test_duplicate_call_id_executed_once(tmp_path):
    """重复 callback：同一 callId 两次请求——只执行一次，第二次拒绝并审计。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _upd(
                    "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n",
                    call_id="dup-1",
                ),
                _tool("compile_script", call_id="dup-2"),
                {"op": "tool_call", "tool": "update_script",
                 "arguments": {"file_path": "scripts/3d.gdl", "content": "BLOCK A, B, 9\nEND\n"},
                 "call_id": "dup-1"},
                _final(),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        audit = md["tool_audit"]
        dup_entries = [e for e in audit if e["call_id"] == "dup-1"]
        executed = [e for e in dup_entries if e["executed"]]
        rejected = [e for e in dup_entries if e["rejected_reason"] == "duplicate_call"]
        assert len(executed) == 1
        assert len(rejected) == 1
        # 第一次的改动生效（第一次 update 的内容），第二次被拒
        assert "ADDZ ZZYZX" in project.get_script(ScriptType.SCRIPT_3D)
        assert "BLOCK A, B, 9" not in project.get_script(ScriptType.SCRIPT_3D)
    finally:
        provider.close()
        harness.cleanup()


def test_wrong_turn_wrong_thread_and_late_requests_refused(tmp_path):
    """callback 关联：错 turn / 错 thread / turn 后迟到工具调用全部拒绝。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                {"op": "tool_call", "tool": "update_script",
                 "arguments": {"file_path": "scripts/3d.gdl", "content": "EVIL-1\nEND\n"},
                 "turn_id": "other-turn", "call_id": "wrong-turn-1"},
                {"op": "tool_call", "tool": "update_script",
                 "arguments": {"file_path": "scripts/3d.gdl", "content": "EVIL-2\nEND\n"},
                 "thread_id": "other-thread", "call_id": "wrong-thread-1"},
                {"op": "approval_request",
                 "method": "item/commandExecution/requestApproval",
                 "params": {"command": "rm -rf /tmp/CANARY-EVIL"}},
                {"op": "final", "text": "完成了。"},
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    original = project.get_script(ScriptType.SCRIPT_3D)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        wire = md["wire_requests"]
        # 错 turn/错 thread：driver 帧校验拒绝（text 含"帧校验失败"）
        frame_rejected = [
            r for r in wire
            if not r.get("success") and "帧校验失败" in str(r.get("text") or "")
        ]
        assert len(frame_rejected) == 2, wire
        # approval_request：以 -32601 拒绝且不执行
        unhandled = [
            r for r in wire
            if not r.get("success")
            and r.get("tool") == "item/commandExecution/requestApproval"
        ]
        assert unhandled, wire
        # 零写入
        assert project.get_script(ScriptType.SCRIPT_3D) == original
        assert result.scripts == {}
    finally:
        provider.close()
        harness.cleanup()


def test_late_tool_after_completion_refused(tmp_path):
    """迟到工具调用（final/turn/completed 之后到达）→ 拒绝且不执行。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"),
                _final("完成。"),
                {"op": "post_completed_tool", "tool": "update_script",
                 "arguments": {"file_path": "scripts/3d.gdl", "content": "EVIL-LATE\nEND\n"},
                 "call_id": "late-call-1"},
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        wire = md["wire_requests"]
        late = [r for r in wire if r.get("call_id") == "late-call-1"]
        assert late and not late[0]["success"], wire
        # 迟到调用没有执行：EVIL-LATE 未落盘
        assert "EVIL-LATE" not in project.get_script(ScriptType.SCRIPT_3D)
        # 第一次 update 正常执行
        assert "ADDZ ZZYZX" in project.get_script(ScriptType.SCRIPT_3D)
    finally:
        provider.close()
        harness.cleanup()


# ── 守卫回归 / 取消 / 崩溃 / epoch / flag 边界 ──────────────

def test_prose_leak_and_string_param_guards_via_bridge(tmp_path):
    """守卫回归：prose leak 与 String 参数引用改值照常拦截（经工具结果文本）。"""
    from openbrep.hsf_project import GDLParameter

    harness = _FakeServerHarness(tmp_path)
    # prose leak: update_script 内容夹带 markdown 散文（P12 复刻）
    # String param 引用改值: 把仍被脚本引用的 String 参数 pattern_type 改值
    leak_content = (
        "BLOCK A, B, ZZYZX\nEND\n\n"
        "## 修改说明\n这是一个用于说明的 markdown 段落，不该出现在脚本里。"
    )
    _write_script(
        tmp_path,
        [
            [
                _tool("update_script", {"file_path": "scripts/3d.gdl", "content": leak_content}),
                _tool("update_script", {
                    "file_path": "paramlist.xml",
                    "content": 'String pattern_type = "zhileng" ! desc',
                }),
                _final("完成。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    # P12 漏窗事故形状：3d.gdl IF 比较 + vl.gdl VALUES 引用 String 参数
    project.scripts[ScriptType.SCRIPT_3D] = (
        'IF pattern_type = "直棂" THEN\nBLOCK A, B, ZZYZX\nENDIF\nEND\n'
    )
    project.scripts[ScriptType.PARAM] = 'VALUES "pattern_type" "直棂" "冰花"\n'
    project.parameters.append(GDLParameter(name="pattern_type", type_tag="String", value="直棂"))
    project.parameters.append(GDLParameter(name="shelf_thk", type_tag="Length", value="0.018"))
    original_3d = project.get_script(ScriptType.SCRIPT_3D)
    original_params = project.parameters[0].value
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        audit = md["tool_audit"]
        # 两个写工具都被拒绝（ok=False），零写入
        write_entries = [e for e in audit if e["tool"] in ("update_script", "patch_script")]
        assert len(write_entries) == 2, audit
        assert all(e["executed"] for e in write_entries), audit  # 进入 registry 执行
        assert all(not e["ok"] for e in write_entries), audit  # 守卫拦截
        assert result.scripts == {}
        assert project.get_script(ScriptType.SCRIPT_3D) == original_3d
        assert project.parameters[0].value == original_params
        # 工具结果文本里能看到守卫文案（replay-safe：只经 tool result 返回）
        wire = md["wire_requests"]
        summaries = [str(r.get("text") or "") for r in wire]
        assert any("markdown 散文泄漏" in s for s in summaries), summaries
        assert any("字符串参数 pattern_type" in s for s in summaries), summaries
    finally:
        provider.close()
        harness.cleanup()


def test_cancel_mid_turn_no_success_no_residual(tmp_path):
    """取消：turn 中断，任务如实报告取消，不 success，不残留临时目录。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nEND\n"),
                {"op": "hang"},
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)

    cancel_after_first_tool = {"flag": False}
    def should_cancel():
        return cancel_after_first_tool["flag"]

    request = _request(tmp_path, project, should_cancel=should_cancel)

    # 在第一个工具执行后翻牌取消：通过 on_event 监控 tool_call
    def flip(event_type, data):
        if event_type == "tool_call" and data.get("name") == "update_script":
            cancel_after_first_tool["flag"] = True

    request.on_event = flip
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(request)
        assert result.success is False, result.plain_text
        md = result.metadata["codex_modify"]
        assert md["cancelled"] is True
        assert "取消" in result.plain_text
        # 无残留临时 cwd
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("openbrep-codex-modify-*")]
        assert leftovers == [], leftovers
    finally:
        provider.close()
        harness.cleanup()


def test_app_server_crash_fails_closed(tmp_path):
    """app-server 崩溃：稳定文案失败，零写入、不残留临时目录。"""
    harness = _FakeServerHarness(
        tmp_path,
        extra_env={"FAKE_CODEX_CRASH_AFTER_REQUESTS": "3"},
    )
    _write_script(
        tmp_path,
        [
            [
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nEND\n"),
                _tool("compile_script"),
                _final("完成。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success is False, result.plain_text
        assert "请稍后重试" in result.plain_text  # 稳定文案（TURN_ERROR_TEXT）
        assert "CANARY" not in result.plain_text and "traceback" not in result.plain_text.lower()
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("openbrep-codex-modify-*")]
        assert leftovers == [], leftovers
        # 崩溃发生在写入之后：若已写入则写入保留但任务不 success；
        # 关键断言是零假成功。这里不要求脚本回滚（崩溃不是回滚触发器）。
    finally:
        provider.close()
        harness.cleanup()


def test_epoch_guard_blocks_mutation_after_project_switch(tmp_path):
    """project epoch 中途变化：后续工具 mutation 拒绝并中止任务。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                _tool("compile_script", call_id="epoch-c-1"),
                _upd("EVIL-EPOCH\nEND\n", call_id="epoch-c-2"),
                _final("完成。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)

    epoch_ok = {"flag": True}

    def epoch_guard() -> bool:
        return epoch_ok["flag"]

    request = _request(tmp_path, project, epoch_guard=epoch_guard)

    def flip(event_type, data):
        if event_type == "tool_call" and data.get("name") == "compile_script":
            epoch_ok["flag"] = False  # 编译工具后"项目切换"

    request.on_event = flip
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(request)
        assert result.success is False, result.plain_text
        md = result.metadata["codex_modify"]
        assert md["epoch_violated"] is True
        assert "项目已切换" in result.plain_text
        # 拒绝后的写工具没有执行：EVIL-EPOCH 未落盘
        assert "EVIL-EPOCH" not in project.get_script(ScriptType.SCRIPT_3D)
        audit = md["tool_audit"]
        assert any(e.get("rejected_reason") == "epoch_changed" for e in audit), audit
    finally:
        provider.close()
        harness.cleanup()


def test_flag_off_pipeline_fails_closed_zero_rpc(tmp_path):
    """flag=false：codex MODIFY 在 pipeline 桥接入口 fail closed，零 RPC。"""
    harness = _FakeServerHarness(tmp_path)
    config = _codex_config(modify_enabled=False)
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        # DEBUG 意图直接进 agent loop 路径（micro/skill/dsl 都跳过）
        result = pipeline.execute(_request(tmp_path, project, intent="DEBUG"))
        assert result.success is False
        assert "尚未开放" in (result.plain_text or "")
        # 零 RPC：fake server 未收到任何 thread/turn 请求
        assert harness.read_params("thread/start") == []
        assert harness.read_params("turn/start") == []
        # 零写入
        assert result.scripts == {}
    finally:
        provider.close()
        harness.cleanup()


def test_flag_off_modify_non_micro_fails_closed(tmp_path):
    """flag=false + MODIFY 非微修改：确定性路径全部回落，最终 fail closed。"""
    harness = _FakeServerHarness(tmp_path)
    config = _codex_config(modify_enabled=False)
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        result = pipeline.execute(_request(
            tmp_path, project,
            user_input="把柜门改成推拉门并增加弧形面板",
        ))
        assert result.success is False
        assert "尚未开放" in (result.plain_text or "")
        assert harness.read_params("thread/start") == []
        assert harness.read_params("turn/start") == []
    finally:
        provider.close()
        harness.cleanup()


def test_no_file_delivery_channel(tmp_path):
    """[FILE:] 不作为交付通道：含 [FILE:] 的 final 零工具 → 打回；警告如实透出。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [_final("[FILE: scripts/3d.gdl]\nEVIL-FILE\n[FILE: end]")],
            [_final("已按计划完成。")],
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        assert "EVIL-FILE" not in project.get_script(ScriptType.SCRIPT_3D)
        assert "不应用 [FILE:]" in result.plain_text or "[FILE:]" in result.plain_text
        md = result.metadata["codex_modify"]
        assert md["tool_calls"] == 0
    finally:
        provider.close()
        harness.cleanup()


def test_malformed_frames_during_turn_do_not_kill_reader(tmp_path):
    """畸形帧（半帧 JSON / 坏 id / 非对象帧）出现在 turn 中：客户端忽略并继续。"""
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                {"op": "malformed"},
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"),
                _tool("compile_script"),
                _final("完成。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        assert "ADDZ ZZYZX" in project.get_script(ScriptType.SCRIPT_3D)
        md = result.metadata["codex_modify"]
        assert md["tool_calls"] == 2
    finally:
        provider.close()
        harness.cleanup()


def test_patch_script_success_via_bridge(tmp_path):
    """P1 回归（验收 e394981）：patch_script 是首选工具，经桥接必须可执行。

    allowlist（ModifyToolRegistry.definitions()）内的名字永远可达——
    patch_script 不被危险名规则误杀；审计 executed=True、脚本按 patch 改变。
    """
    harness = _FakeServerHarness(tmp_path)
    _write_script(
        tmp_path,
        [
            [
                {"op": "tool_call", "tool": "patch_script", "arguments": {
                    "file_path": "scripts/3d.gdl",
                    "patches": [
                        {
                            "old": "BLOCK A, B, ZZYZX",
                            "new": "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1",
                        },
                    ],
                }},
                _tool("compile_script"),
                _final("已按 patch 完成最小改动。"),
            ]
        ],
    )
    config = _codex_config()
    provider = harness.provider()
    pipeline = _pipeline(config, provider, tmp_path)
    project = _make_project(tmp_path)
    try:
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(tmp_path, project))
        assert result.success, result.plain_text
        md = result.metadata["codex_modify"]
        audit = md["tool_audit"]
        patch_entries = [e for e in audit if e["tool"] == "patch_script"]
        assert patch_entries, audit
        assert patch_entries[0]["executed"] is True
        assert patch_entries[0]["ok"] is True
        assert patch_entries[0]["rejected_reason"] is None
        # 脚本内容按 patch 改变（最小 diff，非全量重写）
        script = project.get_script(ScriptType.SCRIPT_3D)
        assert "ADDZ ZZYZX" in script
        assert "BLOCK A, B, 0.018" in script
        assert "DEL 1" in script
        assert set(result.scripts.keys()) == {"scripts/3d.gdl"}
        assert "ADDZ ZZYZX" in (result.scripts["scripts/3d.gdl"] or "")
    finally:
        provider.close()
        harness.cleanup()
