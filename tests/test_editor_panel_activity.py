import unittest

from ui.project_activity import project_activity_entries


class TestEditorPanelActivity(unittest.TestCase):
    def test_project_activity_entries_include_legacy_import_messages(self):
        session_state = {
            "chat_history": [
                {"role": "assistant", "content": "普通回复"},
                {"role": "assistant", "content": "✅ 已加载 HSF 项目\n源目录: /tmp/object"},
            ],
            "project_activity_log": [
                {"timestamp": "10:00:00", "message": "✅ 已导入 ok"},
            ],
        }

        entries = project_activity_entries(session_state)

        self.assertEqual(len(entries), 2)
        self.assertIn("已加载 HSF 项目", entries[0]["message"])
        self.assertIn("已导入 ok", entries[1]["message"])

    def test_project_activity_entries_keep_latest_twenty(self):
        session_state = {
            "chat_history": [],
            "project_activity_log": [
                {"timestamp": f"10:00:{idx:02d}", "message": f"entry {idx}"}
                for idx in range(25)
            ],
        }

        entries = project_activity_entries(session_state)

        self.assertEqual(len(entries), 20)
        self.assertEqual(entries[0]["message"], "entry 5")
        self.assertEqual(entries[-1]["message"], "entry 24")


if __name__ == "__main__":
    unittest.main()
