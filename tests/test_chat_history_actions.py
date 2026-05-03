import unittest
from types import SimpleNamespace

from ui.chat_history_actions import (
    build_chat_record_entries,
    close_chat_record_browser,
    delete_chat_record_entry,
    hydrate_chat_history_from_workspace_memory,
    sanitize_hsf_name,
    suggest_hsf_name_from_chat_record,
    transcript_entries_to_chat_messages,
)
from ui.view_models import classify_code_blocks, extract_gsm_name_candidate


class TestChatHistoryActions(unittest.TestCase):
    def test_sanitize_hsf_name_removes_invalid_path_chars(self):
        self.assertEqual(sanitize_hsf_name('书架 / A:B*?"<>|'), "书架_A_B")
        self.assertEqual(sanitize_hsf_name("   "), "chat_hsf")

    def test_suggest_hsf_name_prefers_previous_user_generation_request(self):
        history = [
            {"role": "user", "content": "请生成一个书架，带五层隔板"},
            {
                "role": "assistant",
                "content": """已生成。

```gdl
TOLER 0.001
BLOCK A, B, ZZYZX
END
```
""",
            },
        ]

        name = suggest_hsf_name_from_chat_record(
            history,
            1,
            extract_gsm_name_candidate_fn=extract_gsm_name_candidate,
        )

        self.assertEqual(name, "书架")

    def test_build_chat_record_entries_marks_extractable_code(self):
        history = [
            {"role": "user", "content": "看看这个"},
            {"role": "assistant", "content": "TOLER 0.001\nBLOCK A, B, ZZYZX\nEND\n"},
        ]

        entries = build_chat_record_entries(history, classify_code_blocks_fn=classify_code_blocks)

        self.assertEqual(entries[0]["role_label"], "用户")
        self.assertFalse(entries[0]["has_code"])
        self.assertTrue(entries[1]["has_code"])

    def test_transcript_entries_to_chat_messages_restores_basic_roles(self):
        entries = [
            SimpleNamespace(role="user", content="请生成书架"),
            SimpleNamespace(role="assistant", content="已生成"),
            SimpleNamespace(role="system", content="内部消息"),
        ]

        messages = transcript_entries_to_chat_messages(entries)

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "请生成书架"},
                {"role": "assistant", "content": "已生成"},
                {"role": "assistant", "content": "内部消息"},
            ],
        )

    def test_hydrate_chat_history_from_workspace_memory_loads_records_without_touching_active_chat(self):
        class State(dict):
            def __getattr__(self, key):
                return self[key]

            def __setattr__(self, key, value):
                self[key] = value

        class Store:
            def __init__(self, work_dir):
                self.work_dir = work_dir

            def list_chat_transcript(self):
                return [SimpleNamespace(role="user", content="旧记录")]

        state = State(
            chat_history=[],
            chat_record_history=[],
            chat_record_history_loaded_work_dir="",
        )

        loaded = hydrate_chat_history_from_workspace_memory(
            state,
            "/tmp/openbrep-workspace",
            store_factory=Store,
        )

        self.assertEqual(loaded, 1)
        self.assertEqual(state.chat_history, [])
        self.assertEqual(state.chat_record_history, [{"role": "user", "content": "旧记录"}])
        self.assertEqual(state.chat_record_history_loaded_work_dir, "/tmp/openbrep-workspace")

    def test_delete_chat_record_entry_removes_persisted_record(self):
        import tempfile
        from openbrep.learning import ErrorLearningStore

        class State(dict):
            def __getattr__(self, key):
                return self[key]

            def __setattr__(self, key, value):
                self[key] = value

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ErrorLearningStore(tmpdir)
            store.rewrite_chat_transcript(
                [
                    {"role": "user", "content": "第一条"},
                    {"role": "assistant", "content": "第二条"},
                    {"role": "assistant", "content": "第三条"},
                ],
                project_name="Demo",
            )

            state = State(
                chat_record_history=[
                    {"role": "user", "content": "第一条"},
                    {"role": "assistant", "content": "第二条"},
                    {"role": "assistant", "content": "第三条"},
                ],
                chat_record_open_idx=1,
                chat_record_delete_idx=1,
                work_dir=tmpdir,
            )

            ok, msg = delete_chat_record_entry(state, 1, tmpdir)

            self.assertTrue(ok)
            self.assertIn("已删除", msg)
            self.assertEqual(
                [entry.content for entry in ErrorLearningStore(tmpdir).list_chat_transcript()],
                ["第一条", "第三条"],
            )
            self.assertEqual(state.chat_record_history, [
                {"role": "user", "content": "第一条"},
                {"role": "assistant", "content": "第三条"},
            ])
            self.assertIsNone(state.chat_record_open_idx)
            self.assertIsNone(state.chat_record_delete_idx)

    def test_close_chat_record_browser_clears_dialog_state(self):
        class State(dict):
            def __getattr__(self, key):
                return self[key]

            def __setattr__(self, key, value):
                self[key] = value

        state = State(
            chat_record_browser_open=True,
            chat_record_open_idx=3,
            chat_record_delete_idx=2,
        )

        close_chat_record_browser(state)

        self.assertFalse(state.chat_record_browser_open)
        self.assertIsNone(state.chat_record_open_idx)
        self.assertIsNone(state.chat_record_delete_idx)


if __name__ == "__main__":
    unittest.main()
