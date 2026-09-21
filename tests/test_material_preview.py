import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.materials import save_materials
from openbrep.workbench.preview_service import preview_payload


class TestMaterialPreview(unittest.TestCase):
    def test_preview_payload_carries_project_material_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = HSFProject.create_new("Table", work_dir=str(root))
            project.scripts[ScriptType.SCRIPT_3D] = "MATERIAL mat_top\nBLOCK 1, 1, 0.1\n"
            save_materials(project.root, {"version": 1, "slots": {"mat_top": {"family": "wood"}}})

            payload = preview_payload(project)

            self.assertEqual(payload["meshes"][0]["material_id"], "mat_top")
            self.assertEqual(payload["materials"]["mat_top"]["family"], "wood")


if __name__ == "__main__":
    unittest.main()
