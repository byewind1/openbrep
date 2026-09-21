import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.materials import save_materials
from openbrep.workbench.preview_service import preview_payload
from openbrep.runtime.modify_acceptance import preview_geometry_summary, build_modify_acceptance


class TestMaterialPreview(unittest.TestCase):
    def test_modify_acceptance_reports_actual_material_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = HSFProject.create_new("Window", tmp)
            script = 'DEFINE MATERIAL "wood" 2, 1, 0, 0\nMATERIAL "wood"\nBLOCK 1,1,1\n'
            project.scripts[ScriptType.SCRIPT_3D] = script
            before = preview_geometry_summary(project)
            project.scripts[ScriptType.SCRIPT_3D] = script.replace('1, 0, 0', '0, 1, 0')
            after = preview_geometry_summary(project)
            report = build_modify_acceptance(before=before, after=after, changed_files=['3d.gdl'])
            check = next(c for c in report['checks'] if c['name'] == '材质预览')
            self.assertEqual(check['status'], 'pass')
            self.assertIn('变化', check['detail'])
            project.scripts[ScriptType.SCRIPT_3D] = 'MATERIAL "missing"\nBLOCK 1,1,1\n'
            report = build_modify_acceptance(before=after, after=preview_geometry_summary(project))
            check = next(c for c in report['checks'] if c['name'] == '材质预览')
            self.assertEqual(check['status'], 'warn')

    def test_unresolved_named_material_cannot_pass_preview_material_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = HSFProject.create_new("Window", tmp)
            project.scripts[ScriptType.SCRIPT_3D] = 'MATERIAL "missing"\nBLOCK 1, 1, 1\n'
            payload = preview_payload(project)
            self.assertEqual(payload['material_check']['status'], 'unresolved')
            self.assertEqual(payload['material_check']['unresolved_meshes'], 1)
            self.assertTrue(payload['warnings'])

    def test_named_materials_follow_script_edits_without_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = HSFProject.create_new("Window", tmp)
            project.scripts[ScriptType.MASTER] = 'DEFINE MATERIAL "frame" 2, 0.12, 0.055, 0.025\n'
            project.scripts[ScriptType.SCRIPT_3D] = (
                'MATERIAL "frame"\nBLOCK 1, 1, 0.1\n'
                'DEFINE MATERIAL "lattice" 2, 0.78, 0.52, 0.24\n'
                'MATERIAL "lattice"\nBLOCK 0.1, 0.1, 1\n'
            )
            before = preview_payload(project)
            colors = [before['materials'][m['material_id']]['color'] for m in before['meshes']]
            self.assertEqual(colors, ['#1F0E06', '#C7853D'])
            self.assertEqual(before['warnings'], [])
            project.scripts[ScriptType.SCRIPT_3D] = project.scripts[ScriptType.SCRIPT_3D].replace('0.78, 0.52, 0.24', '1, 0, 0')
            after = preview_payload(project)
            self.assertEqual(after['materials'][after['meshes'][1]['material_id']]['color'], '#FF0000')
            self.assertEqual(before['meshes'], after['meshes'])

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
