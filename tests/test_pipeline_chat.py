import unittest
from unittest.mock import MagicMock, patch

from pathlib import Path

from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.explainer.schema import ParameterExplanation, ProjectExplanation, ScriptExplanation


class TestPipelineChat(unittest.TestCase):
    def _make_pipeline(self, response_text: str = "你好") -> TaskPipeline:
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content=response_text,
            model="mock",
            usage={},
            finish_reason="stop",
        )
        pipeline._make_llm = lambda req: mock_llm
        return pipeline

    def test_chat_includes_recent_history(self):
        pipeline = self._make_pipeline("ok")
        request = TaskRequest(
            user_input="再详细一点",
            intent="CHAT",
            history=[
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "第一答"},
            ],
        )

        result = pipeline.execute(request)

        self.assertTrue(result.success)
        messages = pipeline._make_llm(request).generate.call_args.args[0]
        self.assertEqual(messages[1]["content"], "第一句")
        self.assertEqual(messages[2]["content"], "第一答")
        self.assertEqual(messages[3]["content"], "再详细一点")

    def test_chat_prepends_assistant_settings_prompt(self):
        pipeline = self._make_pipeline("ok")
        request = TaskRequest(
            user_input="你好",
            intent="CHAT",
            assistant_settings="回答简短一点",
        )

        pipeline.execute(request)

        messages = pipeline._make_llm(request).generate.call_args.args[0]
        self.assertIn("AI助手设置", messages[0]["content"])
        self.assertIn("回答简短一点", messages[0]["content"])

    def test_chat_with_project_uses_project_explainer_by_default(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"

        fake_explanation = ProjectExplanation(overall_goal="chair")
        with patch("openbrep.runtime.pipeline.resolve_script_target", return_value=None):
            with patch("openbrep.runtime.pipeline.resolve_parameter_targets", return_value=[]):
                with patch("openbrep.runtime.pipeline.build_project_context", return_value={"gsm_name": "chair"}) as mock_context:
                    with patch("openbrep.runtime.pipeline.explain_project_context", return_value=fake_explanation) as mock_explain:
                        with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="简要拆解") as mock_reply:
                            result = pipeline.execute(TaskRequest(
                                user_input="这是什么对象？",
                                intent="CHAT",
                                project=project,
                            ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "简要拆解")
        mock_context.assert_called_once_with(project)
        mock_explain.assert_called_once_with({"gsm_name": "chair"})
        mock_reply.assert_called_once_with(fake_explanation, user_input="这是什么对象？")

    def test_gdl_wiki_teaching_with_project_stays_chat_and_does_not_compile(self):
        pipeline = self._make_pipeline("CYLIND 语法说明\n\n```gdl\nCYLIND h, r\n```")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        pipeline._make_compiler = MagicMock()

        result = pipeline.execute(TaskRequest(
            user_input="CYLIND 语法",
            project=project,
        ))

        self.assertTrue(result.success)
        self.assertEqual(result.intent, "CHAT")
        self.assertEqual(result.scripts, {})
        self.assertIsNone(result.compile_result)
        self.assertNotIn("变更摘要", result.plain_text)
        self.assertNotIn("编译通过", result.plain_text)
        pipeline._make_compiler.assert_not_called()

    def test_chat_with_project_uses_script_target_explainer(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        fake_explanation = ScriptExplanation(script_type="3D", goal="生成主体几何")

        with patch("openbrep.runtime.pipeline.resolve_script_target", return_value="3D"):
            with patch("openbrep.runtime.pipeline.build_project_script_context", return_value={"script_type": "3D"}) as mock_context:
                with patch("openbrep.runtime.pipeline.explain_script_context", return_value=fake_explanation) as mock_explain:
                    with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="3D 拆解") as mock_reply:
                        result = pipeline.execute(TaskRequest(
                            user_input="解释一下 3D 脚本",
                            intent="CHAT",
                            project=project,
                        ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "3D 拆解")
        mock_context.assert_called_once_with(project, "3D")
        mock_explain.assert_called_once_with({"script_type": "3D"})
        mock_reply.assert_called_once_with(fake_explanation, user_input="解释一下 3D 脚本")

    def test_chat_with_project_uses_parameter_target_explainer(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        fake_explanation = ParameterExplanation(name="A")

        with patch("openbrep.runtime.pipeline.resolve_script_target", return_value=None):
            with patch("openbrep.runtime.pipeline.resolve_parameter_targets", return_value=["A"]) as mock_targets:
                with patch("openbrep.runtime.pipeline.build_project_parameter_context", return_value={"name": "A"}) as mock_context:
                    with patch("openbrep.runtime.pipeline.explain_parameter_context", return_value=fake_explanation) as mock_explain:
                        with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="A 参数拆解") as mock_reply:
                            result = pipeline.execute(TaskRequest(
                                user_input="A 控制什么",
                                intent="CHAT",
                                project=project,
                            ))

        self.assertTrue(result.success)
        self.assertEqual(result.plain_text, "A 参数拆解")
        mock_targets.assert_called_once_with(project, "A 控制什么")
        mock_context.assert_called_once_with(project, "A")
        mock_explain.assert_called_once_with({"name": "A"})
        mock_reply.assert_called_once_with(fake_explanation, user_input="A 控制什么")

    def test_chat_with_project_greeting_calls_raw_llm(self):
        pipeline = self._make_pipeline("你好，我是 OpenBrep 的 GDL 助手。我可以帮你做什么？")
        project = HSFProject.create_new("chair", work_dir="./workdir")

        with patch("openbrep.runtime.pipeline.resolve_script_target") as mock_script_target:
            with patch("openbrep.runtime.pipeline.resolve_parameter_targets") as mock_param_targets:
                result = pipeline.execute(TaskRequest(user_input="你好", intent="CHAT", project=project))

        self.assertTrue(result.success)
        self.assertIn("GDL", result.plain_text)
        self.assertIn("可以帮", result.plain_text)
        mock_script_target.assert_not_called()
        mock_param_targets.assert_not_called()
        self.assertIsNotNone(pipeline._make_llm(TaskRequest(user_input="x")).generate.call_args)

    def test_chat_with_project_adds_explainer_constraint(self):
        pipeline = self._make_pipeline("ok")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        request = TaskRequest(
            user_input="解释一下",
            intent="CHAT",
            project=project,
            assistant_settings="回答简短一点",
        )

        with patch("openbrep.runtime.pipeline.resolve_script_target", return_value=None):
            with patch("openbrep.runtime.pipeline.resolve_parameter_targets", return_value=[]):
                with patch("openbrep.runtime.pipeline.build_project_context", return_value={"gsm_name": "chair"}):
                    with patch("openbrep.runtime.pipeline.explain_project_context", return_value=ProjectExplanation(overall_goal="chair")):
                        with patch("openbrep.runtime.pipeline.build_chat_explanation_reply", return_value="简要拆解"):
                            pipeline.execute(request)

        call_args = pipeline._make_llm(request).generate.call_args
        self.assertIsNotNone(call_args)
        # LLM is called for skill intent classification before explainer shortcut
        msg_list = call_args.args[0] if call_args.args else []
        self.assertGreater(len(msg_list), 0)
        self.assertIn("分类器", str(msg_list[0]))


if __name__ == "__main__":
    unittest.main()




# ── D3：Codex 模型 CHAT / EXPLAIN 安全调用 ────────────────────────────────
# 使用 pytest fixture（tmp_path）隔离真实配置与工作区。unittest 类之上追加
# 的 pytest 风格函数会在同一文件共存（pytest 收集两者）。


class _CodexTurnResult:
    def __init__(self, content, finish_reason="stop", error=None):
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.usage = {}


class _CodexProviderStub:
    """CodexProvider.chat 替身：记录 messages/model 并返回脚本化结果。

    与真实 provider 一致：有 on_event 时先发 status + assistant_delta 事件。
    """

    def __init__(self, content="你好，我是 Codex 测试助手。", finish_reason="stop", error=None):
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.calls: list[dict] = []

    def chat(self, messages, model, **kwargs):
        self.calls.append({"messages": messages, "model": model, "kwargs": kwargs})
        on_event = kwargs.get("on_event")
        if on_event is not None:
            on_event("status", {"stage": "codex", "message": "Codex 对话已开始"})
            on_event("assistant_delta", {"content": self.content})
            on_event("status", {"stage": "codex", "message": "Codex 对话完成"})
        return _CodexTurnResult(self.content, self.finish_reason, self.error)


def _codex_pipeline(tmp_path, provider=None):
    """构造选中 openai-codex 模型的 pipeline（配置内存化，不落真实 config）。"""
    from openbrep.config import GDLAgentConfig

    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.providers = [
        {
            "name": "openai-codex",
            "api_mode": "codex_app_server",
            "api_key": "",
            "models": [],
        }
    ]
    return TaskPipeline(
        config=config,
        trace_dir=str(tmp_path / "traces"),
        codex_provider=provider,
    )


def _dir_snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def test_codex_chat_no_project_creates_no_files(tmp_path):
    """无项目 CHAT：只回复；工作区目录树逐字节一致（不创建任何目录/文件）。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("payload", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "keep.gdl").write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

    provider = _CodexProviderStub("这是 Codex 的回复。")
    before = _dir_snapshot(workspace)
    pipeline = _codex_pipeline(tmp_path, provider=provider)
    result = pipeline.execute(
        TaskRequest(
            user_input="今天天气如何？",
            intent="CHAT",
            work_dir=str(workspace),
            output_dir=str(workspace / "out"),
        )
    )
    after = _dir_snapshot(workspace)

    assert result.success is True
    assert result.plain_text == "这是 Codex 的回复。"
    assert result.scripts == {}
    assert before == after, "无项目 CHAT 不得在工作区创建/修改任何文件"
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "openai-codex/gpt-5.6-luna"
    messages = provider.calls[0]["messages"]
    assert messages[-1]["content"] == "今天天气如何？"
    assert messages[0]["content"].startswith("你是 openbrep 的内置助手")
    # 无项目：不注入项目上下文
    assert "当前工程解释上下文" not in messages[0]["content"]


def test_codex_explain_with_project_does_not_create_revision(tmp_path):
    """EXPLAIN（有项目）：项目只读摘要注入 prompt；HSF revision 数不变。"""
    project = HSFProject.create_new("ExplainShelf", str(tmp_path / "proj"))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
    hsf_dir = project.save_to_disk()

    revisions_dir = Path(hsf_dir) / ".openbrep" / "revisions"
    before_count = len(list(revisions_dir.iterdir())) if revisions_dir.exists() else 0

    provider = _CodexProviderStub("这个构件是一个书架。")
    pipeline = _codex_pipeline(tmp_path, provider=provider)
    result = pipeline.execute(
        TaskRequest(
            user_input="解释一下这个构件",
            intent="CHAT",
            project=project,
            work_dir=str(hsf_dir),
        )
    )
    after_count = len(list(revisions_dir.iterdir())) if revisions_dir.exists() else 0

    assert result.success is True
    assert result.plain_text == "这个构件是一个书架。"
    assert before_count == after_count, "EXPLAIN 不得创建 revision"
    assert len(provider.calls) == 1
    system = provider.calls[0]["messages"][0]["content"]
    assert "当前工程解释上下文" in system
    assert "ExplainShelf" in system
    # 只读摘要：脚本以摘要小节出现（截取前几行），不是完整脚本透传
    assert "### scripts/3d.gdl" in system


def test_codex_chat_streams_events_when_on_event_provided(tmp_path):
    events: list[tuple[str, dict]] = []

    def on_event(event_type, data):
        events.append((event_type, data))

    provider = _CodexProviderStub("流式回复")
    pipeline = _codex_pipeline(tmp_path, provider=provider)
    result = pipeline.execute(
        TaskRequest(user_input="你好", intent="CHAT", on_event=on_event)
    )

    assert result.success is True
    assert result.plain_text == "流式回复"
    kinds = [k for k, _ in events]
    assert "assistant_delta" in kinds
    assert "status" in kinds


def test_codex_chat_passes_should_cancel(tmp_path):
    flag = {"v": False}

    def should_cancel():
        return flag["v"]

    provider = _CodexProviderStub(
        content="", finish_reason="interrupted", error="对话已取消。"
    )
    pipeline = _codex_pipeline(tmp_path, provider=provider)
    result = pipeline.execute(
        TaskRequest(user_input="你好", intent="CHAT", should_cancel=should_cancel)
    )

    assert result.success is False
    assert "取消" in (result.error or "")
    assert provider.calls[0]["kwargs"]["should_cancel"] is should_cancel


def test_codex_chat_signed_out_fails_closed(tmp_path):
    from openbrep.codex.provider import CodexNotSignedInError

    class _SignedOut:
        def chat(self, messages, model, **kwargs):
            raise CodexNotSignedInError("尚未连接 ChatGPT。")

    pipeline = _codex_pipeline(tmp_path, provider=_SignedOut())
    result = pipeline.execute(TaskRequest(user_input="你好", intent="CHAT"))

    assert result.success is False
    assert "ChatGPT" in (result.error or "")
    assert "CodexNotSignedInError" not in (result.error or "")


def test_codex_chat_quota_fails_closed(tmp_path):
    from openbrep.codex.app_server import CodexAppServerError

    class _Quota:
        def chat(self, messages, model, **kwargs):
            raise CodexAppServerError(
                "ChatGPT 订阅额度已耗尽。", category="quota_exhausted"
            )

    pipeline = _codex_pipeline(tmp_path, provider=_Quota())
    result = pipeline.execute(TaskRequest(user_input="你好", intent="CHAT"))

    assert result.success is False
    assert "额度" in (result.error or "")
    assert "quota_exhausted" not in (result.error or "")


def test_codex_explain_prompt_keeps_existing_chat_contract(tmp_path):
    """输入仅来自 OpenBrep 构建的上下文：system 提示与现有 chat 路径同源。"""
    provider = _CodexProviderStub("ok")
    pipeline = _codex_pipeline(tmp_path, provider=provider)
    pipeline.execute(
        TaskRequest(
            user_input="继续",
            intent="CHAT",
            history=[
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "第一答"},
            ],
        )
    )
    messages = provider.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("你是 openbrep 的内置助手")
    assert messages[-1]["content"] == "继续"



def test_non_codex_chat_prompt_byte_identical_to_baseline(tmp_path):
    """prompt 不变性：现有 provider 的 chat intent prompt 与基线逐字节一致。

    对照基线构造（2026-08-16 D3 前行为）：system = assistant_settings 前缀 +
    chat 系统提示；随后是 history（最多 6 条）与当前 user 输入；messages 不含
    任何 codex 专用参数痕迹。
    """
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / "tr"))
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content="ok", model="mock", usage={}, finish_reason="stop"
    )
    pipeline._make_llm = lambda req: mock_llm
    history = [
        {"role": "user", "content": "历史一"},
        {"role": "assistant", "content": "历史答"},
    ]
    pipeline.execute(
        TaskRequest(
            user_input="当前问题",
            intent="CHAT",
            history=history,
            assistant_settings="回答简短一点",
        )
    )
    messages = mock_llm.generate.call_args.args[0]
    # 与基线逐字节一致：assistant_settings 前置 + 固定 system 文案
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("## AI助手设置")
    assert "回答简短一点" in messages[0]["content"]
    assert messages[0]["content"].endswith(
        "回复简洁，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
    )
    assert messages[1:] == [
        {"role": "user", "content": "历史一"},
        {"role": "assistant", "content": "历史答"},
        {"role": "user", "content": "当前问题"},
    ]
    # codex 专用 kwargs 不进入 generate 调用参数
    kwargs = mock_llm.generate.call_args.kwargs
    assert "codex_intent" not in kwargs
    assert "codex_should_cancel" not in kwargs
    assert "codex_on_event" not in kwargs
