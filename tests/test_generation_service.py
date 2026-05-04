import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path

from openbrep.hsf_project import HSFProject, ScriptType
from ui.generation_service import GenerationService


class _State(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class _Status:
    def empty(self):
        return self

    def info(self, _msg):
        return None

    def warning(self, _msg):
        return None

    def error(self, _msg):
        return None

    def success(self, _msg):
        return None


class _Pipeline:
    captured_request = None

    def __init__(self, trace_dir):
        self.trace_dir = trace_dir
        self.config = None

    def execute(self, request):
        _Pipeline.captured_request = request
        return SimpleNamespace(success=True, scripts={}, plain_text="ok", project=request.project)


class _FailingPipeline(_Pipeline):
    def execute(self, request):
        _Pipeline.captured_request = request
        return SimpleNamespace(success=False, error="pipeline failed")


class _PlanningPipeline(_Pipeline):
    def execute(self, request):
        _Pipeline.captured_request = request
        return SimpleNamespace(
            success=True,
            scripts={"scripts/3d.gdl": "BLOCK A, B, ZZYZX\nEND\n"},
            plain_text="ok",
            project=request.project,
            object_plan={
                "object_type": "专业书架",
                "geometry": ["侧板", "层板"],
                "parameters": ["Integer shelf_count = 层板数"],
            },
        )


def _service(session_state, *, should_accept=True):
    return GenerationService(
        session_state=session_state,
        pipeline_class=_Pipeline,
        config_loader_fn=lambda: SimpleNamespace(),
        build_generation_result_plan_fn=lambda *_args, **_kwargs: SimpleNamespace(has_changes=False),
        begin_generation_state_fn=lambda _state: "gen-1",
        is_active_generation_fn=lambda _state, _generation_id: True,
        should_accept_generation_result_fn=lambda _state, _generation_id: should_accept,
        finish_generation_state_fn=lambda _state, _generation_id, status: session_state.__setitem__("finished", status) or True,
        generation_cancelled_message_fn=lambda: "cancelled",
        trim_history_fn=lambda history: history,
        is_debug_intent_fn=lambda text: "修复" in text,
        get_debug_mode_fn=lambda _text: "keyword",
        is_explainer_intent_fn=lambda text: "解释" in text,
        is_modify_bridge_prompt_fn=lambda text: "桥接修改" in text,
        is_post_clarification_prompt_fn=lambda text: "本次确认目标" in text,
        apply_generation_plan_fn=lambda *_args, **_kwargs: ("", []),
        build_generation_reply_fn=lambda plain_text, _prefix, _blocks: plain_text,
    )


def _service_with_pipeline(session_state, pipeline_class):
    service = _service(session_state)
    service.pipeline_class = pipeline_class
    return service


class TestGenerationService(unittest.TestCase):
    def test_routes_debug_request_to_repair(self):
        state = _State(chat_history=[], work_dir="./workdir")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1"

        result = _service(state).run_agent_generate("请修复错误", project, _Status(), gsm_name="chair")

        self.assertEqual(result, "ok")
        self.assertEqual(_Pipeline.captured_request.intent, "REPAIR")
        self.assertEqual(state.finished, "completed")

    def test_force_generate_prefix_suppresses_debug_and_is_not_sent_to_pipeline(self):
        state = _State(chat_history=[], work_dir="./workdir")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1"

        _service(state).run_agent_generate("[GENERATE] 请修复错误", project, _Status(), gsm_name="chair")

        self.assertEqual(_Pipeline.captured_request.intent, "MODIFY")
        self.assertEqual(_Pipeline.captured_request.user_input, "请修复错误")

    def test_routes_explainer_request_to_chat(self):
        state = _State(chat_history=[], work_dir="./workdir")
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK 1,1,1"

        _service(state).run_agent_generate("解释一下脚本", project, _Status(), gsm_name="chair")

        self.assertEqual(_Pipeline.captured_request.intent, "CHAT")

    def test_returns_cancel_message_when_generation_was_cancelled(self):
        state = _State(chat_history=[], work_dir="./workdir")
        project = HSFProject.create_new("chair", work_dir="./workdir")

        result = _service(state, should_accept=False).run_agent_generate("生成椅子", project, _Status(), gsm_name="chair")

        self.assertEqual(result, "cancelled")
        self.assertEqual(state.finished, "cancelled")

    def test_returns_error_when_pipeline_fails(self):
        state = _State(chat_history=[], work_dir="./workdir")
        project = HSFProject.create_new("chair", work_dir="./workdir")

        result = _service_with_pipeline(state, _FailingPipeline).run_agent_generate(
            "生成椅子",
            project,
            _Status(),
            gsm_name="chair",
        )

        self.assertIn("错误", result)
        self.assertIn("pipeline failed", result)
        self.assertEqual(state.finished, "failed")

    def test_auto_apply_generation_persists_object_plan_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _State(chat_history=[], work_dir=tmpdir)
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            project.scripts = {}
            service = _service_with_pipeline(state, _PlanningPipeline)
            service.build_generation_result_plan_fn = lambda *_args, **_kwargs: SimpleNamespace(
                has_changes=True,
                mode="auto_apply",
                code_blocks=[],
                reply_prefix="",
            )

            service.run_agent_generate("生成一个书架", project, _Status(), gsm_name="bookshelf")

            reports_dir = Path(project.root) / ".openbrep" / "reports"
            latest = reports_dir / "latest_object_plan.json"
            latest_exists = latest.exists()
            latest_text = latest.read_text(encoding="utf-8") if latest_exists else ""

        self.assertTrue(latest_exists)
        self.assertIn("object_plan", latest_text)


if __name__ == "__main__":
    unittest.main()
