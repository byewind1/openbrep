import unittest

from ui.generation_controls import (
    consume_generation_result,
    finalize_generation,
    generation_cancelled_message,
    generation_stop_label,
    guarded_event_update,
    render_generation_controls,
)
from ui.state import begin_generation_state


class _State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _StatusPlaceholder:
    def __init__(self):
        self.calls = []

    def info(self, message):
        self.calls.append(("info", message))


class _FakeStreamlit:
    def __init__(self, click=False):
        self.click = click
        self.warnings = []
        self.infos = []
        self.rerun_called = False
        self.button_labels = []

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        self.infos.append(message)

    def button(self, label, **_kwargs):
        self.button_labels.append(label)
        return self.click

    def rerun(self):
        self.rerun_called = True


class TestGenerationControls(unittest.TestCase):
    def test_generation_stop_label_reflects_cancelling_state(self):
        self.assertEqual(generation_stop_label({"generation_status": "running"}), "停止生成")
        self.assertEqual(generation_stop_label({"generation_status": "cancelling"}), "停止生成中...")

    def test_render_generation_controls_requests_cancel(self):
        state = _State()
        generation_id = begin_generation_state(state)
        fake_st = _FakeStreamlit(click=True)

        render_generation_controls(fake_st, state)

        self.assertEqual(fake_st.button_labels, ["停止生成"])
        self.assertTrue(state.generation_cancel_requested)
        self.assertEqual(state.active_generation_id, generation_id)
        self.assertTrue(fake_st.rerun_called)

    def test_render_generation_controls_skips_idle_state(self):
        fake_st = _FakeStreamlit(click=True)

        render_generation_controls(fake_st, {"generation_status": "idle"})

        self.assertEqual(fake_st.button_labels, [])
        self.assertFalse(fake_st.rerun_called)

    def test_guarded_event_update_only_updates_active_generation(self):
        state = _State()
        generation_id = begin_generation_state(state)
        status = _StatusPlaceholder()

        guarded_event_update(state, status, "stale", "info", "old")
        guarded_event_update(state, status, generation_id, "info", "current")

        self.assertEqual(status.calls, [("info", "current")])

    def test_consume_and_finalize_generation(self):
        state = _State()
        generation_id = begin_generation_state(state)

        self.assertTrue(consume_generation_result(state, generation_id))
        self.assertTrue(finalize_generation(state, generation_id, "completed"))
        self.assertFalse(state.agent_running)
        self.assertEqual(state.generation_status, "completed")

    def test_generation_cancelled_message_is_stable(self):
        self.assertIn("未写入编辑器", generation_cancelled_message())


if __name__ == "__main__":
    unittest.main()
