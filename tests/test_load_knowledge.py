import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLoadKnowledgeLayers(unittest.TestCase):
    def test_load_knowledge_degrades_when_user_layer_fails(self):
        from ui import app as ui_app

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "knowledge").mkdir(parents=True, exist_ok=True)

            class FakeKB:
                def __init__(self, knowledge_dir: str):
                    self.knowledge_dir = str(knowledge_dir)
                    self._docs = {}

                def load(self):
                    if "pro_knowledge" in self.knowledge_dir:
                        self._docs = {"doc": "pro"}
                        return
                    if self.knowledge_dir.endswith(str((work_dir / "knowledge")).replace("\\", "/")):
                        raise RuntimeError("user knowledge broken")
                    self._docs = {"doc": "bundled"}

                def get_by_task_type(self, _task_type: str):
                    return self._docs.get("doc", "")

            with patch("ui.knowledge_access.KnowledgeBase", FakeKB):
                with patch.dict("ui.app.st.session_state", {"work_dir": str(work_dir), "pro_unlocked": False}, clear=False):
                    result = ui_app.load_knowledge("all")

        self.assertEqual(result, "bundled")

    def test_load_knowledge_degrades_when_pro_layer_fails(self):
        from ui import app as ui_app

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "knowledge").mkdir(parents=True, exist_ok=True)
            (work_dir / "pro_knowledge").mkdir(parents=True, exist_ok=True)

            class FakeKB:
                def __init__(self, knowledge_dir: str):
                    self.knowledge_dir = str(knowledge_dir)
                    self._docs = {}

                def load(self):
                    if "pro_knowledge" in self.knowledge_dir:
                        raise RuntimeError("pro knowledge broken")
                    if self.knowledge_dir.endswith(str((work_dir / "knowledge")).replace("\\", "/")):
                        self._docs = {"doc": "user"}
                        return
                    self._docs = {"doc": "bundled"}

                def get_by_task_type(self, _task_type: str):
                    return self._docs.get("doc", "")

            with patch("ui.knowledge_access.KnowledgeBase", FakeKB):
                with patch.dict("ui.app.st.session_state", {"work_dir": str(work_dir), "pro_unlocked": True}, clear=False):
                    result = ui_app.load_knowledge("all")

        self.assertEqual(result, "user")


if __name__ == "__main__":
    unittest.main()
