import unittest

from ui.chat_controller import process_chat_turn


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _DummyStreamlit:
    def __init__(self):
        self.rerun_called = 0
        self.toasts = []
        self.errors = []

    def rerun(self):
        self.rerun_called += 1

    def toast(self, msg, icon=None):
        self.toasts.append((msg, icon))

    def error(self, msg):
        self.errors.append(msg)


class TestChatFlowDispatch(unittest.TestCase):
    def test_process_chat_turn_routes_text_input(self):
        st = _DummyStreamlit()
        session_state = _State(
            chat_history=[],
            project=object(),
            pending_gsm_name="",
            agent_running=False,
        )
        calls = {"normal": 0, "vision": 0}

        def run_normal_text_path_fn(*args, **kwargs):
            calls["normal"] += 1
            return True, True, None

        def run_vision_path_fn(*args, **kwargs):
            calls["vision"] += 1
            return False, False, None

        process_chat_turn(
            st=st,
            session_state=session_state,
            chat_payload={
                "user_input": "请修复这个对象",
                "live_output": object(),
                "vision_b64": None,
                "vision_mime": None,
                "vision_name": None,
            },
            api_key="k",
            model_name="glm-4-flash",
            resolve_bridge_input_fn=lambda *_args, **_kwargs: None,
            resolve_effective_input_fn=lambda *args, **kwargs: ("请修复这个对象", False, False),
            detect_gsm_name_candidate_fn=lambda text: "demo-gsm" if text else None,
            handle_tapir_test_trigger_fn=lambda *_args: (False, False),
            handle_tapir_selection_trigger_fn=lambda *_args: (False, False),
            handle_tapir_highlight_trigger_fn=lambda *_args: (False, False),
            handle_tapir_load_params_trigger_fn=lambda *_args: (False, False),
            handle_tapir_apply_params_trigger_fn=lambda *_args: (False, False),
            run_vision_path_fn=run_vision_path_fn,
            run_normal_text_path_fn=run_normal_text_path_fn,
            apply_chat_anchor_pending_fn=lambda **_kwargs: False,
        )

        self.assertEqual(calls["normal"], 1)
        self.assertEqual(calls["vision"], 0)
        self.assertEqual(session_state.pending_gsm_name, "demo-gsm")
        self.assertEqual(st.errors, [])

    def test_process_chat_turn_routes_image_input(self):
        st = _DummyStreamlit()
        session_state = _State(
            chat_history=[],
            project=object(),
            pending_gsm_name="",
            agent_running=False,
        )
        calls = {"normal": 0, "vision": 0}

        def run_normal_text_path_fn(*args, **kwargs):
            calls["normal"] += 1
            return False, False, None

        def run_vision_path_fn(*args, **kwargs):
            calls["vision"] += 1
            return True, True, None

        process_chat_turn(
            st=st,
            session_state=session_state,
            chat_payload={
                "user_input": "请生成",
                "live_output": object(),
                "vision_b64": "ZmFrZQ==",
                "vision_mime": "image/png",
                "vision_name": "shot.png",
            },
            api_key="k",
            model_name="glm-4-flash",
            resolve_bridge_input_fn=lambda *_args, **_kwargs: None,
            resolve_effective_input_fn=lambda *args, **kwargs: ("请生成", False, False),
            detect_gsm_name_candidate_fn=lambda text: "demo-gsm" if text else None,
            handle_tapir_test_trigger_fn=lambda *_args: (False, False),
            handle_tapir_selection_trigger_fn=lambda *_args: (False, False),
            handle_tapir_highlight_trigger_fn=lambda *_args: (False, False),
            handle_tapir_load_params_trigger_fn=lambda *_args: (False, False),
            handle_tapir_apply_params_trigger_fn=lambda *_args: (False, False),
            run_vision_path_fn=run_vision_path_fn,
            run_normal_text_path_fn=run_normal_text_path_fn,
            apply_chat_anchor_pending_fn=lambda **_kwargs: False,
        )

        self.assertEqual(calls["normal"], 0)
        self.assertEqual(calls["vision"], 1)
        self.assertEqual(st.errors, [])


if __name__ == "__main__":
    unittest.main()
