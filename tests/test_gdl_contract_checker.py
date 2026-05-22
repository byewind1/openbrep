import tempfile
import unittest
from pathlib import Path

from openbrep.gdl_contract_checker import GDLContractChecker
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType


class TestGDLContractChecker(unittest.TestCase):
    def _project(self) -> HSFProject:
        project = HSFProject.create_new("ContractObject")
        project.add_parameter(GDLParameter("mode", "String", value="A"))
        project.set_script(ScriptType.MASTER, "_panel_count = 4\n")
        project.set_script(ScriptType.SCRIPT_3D, "ADDZ 1\nBLOCK A, B, ZZYZX\nDEL 1\n")
        project.set_script(ScriptType.SCRIPT_2D, "HOTSPOT2 0, 0\nPROJECT2 3, 270, 2\n")
        project.set_script(ScriptType.PARAM, 'VALUES "mode" "A", "B"\nLOCK "ZZYZX"\n')
        return project

    def test_valid_project_passes_contract(self):
        result = GDLContractChecker().check(self._project())

        self.assertTrue(result.passed, result.issues)

    def test_parameter_script_unknown_param_fails(self):
        project = self._project()
        project.set_script(ScriptType.PARAM, 'VALUES "missing_param" "A", "B"\n')

        result = GDLContractChecker().check(project)

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].check_type, "parameter_script_unknown_param")
        self.assertIn("missing_param", result.issues[0].detail)

    def test_empty_2d_script_fails(self):
        project = self._project()
        project.set_script(ScriptType.SCRIPT_2D, "")

        result = GDLContractChecker().check(project)

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].check_type, "empty_2d_script")

    def test_transform_stack_underflow_and_unclosed_are_reported(self):
        project = self._project()
        project.set_script(ScriptType.SCRIPT_3D, "DEL 1\nADDZ 1\nBLOCK A, B, ZZYZX\n")

        result = GDLContractChecker().check(project)

        self.assertFalse(result.passed)
        issue_types = {issue.check_type for issue in result.issues}
        self.assertIn("transform_stack_underflow", issue_types)
        self.assertIn("transform_stack_unclosed", issue_types)

    def test_duplicate_derived_variable_outside_master_is_warning_only(self):
        project = self._project()
        project.set_script(ScriptType.MASTER, "")
        project.set_script(ScriptType.SCRIPT_2D, "_gap = A / 2\nHOTSPOT2 0, 0\n")
        project.set_script(ScriptType.SCRIPT_3D, "_gap = A / 2\nBLOCK _gap, B, ZZYZX\n")

        result = GDLContractChecker().check(project)

        warning_types = {issue.check_type for issue in result.issues if issue.severity == "warning"}
        self.assertIn("derived_var_not_in_master", warning_types)

    def test_hsf_directory_missing_required_files_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "Partial"
            (root / "scripts").mkdir(parents=True)
            (root / "scripts" / "3d.gdl").write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

            result = GDLContractChecker().check_hsf_directory(root)

            self.assertFalse(result.passed)
            issue_files = {issue.file for issue in result.issues}
            self.assertIn("paramlist.xml", issue_files)
            self.assertIn("libpartdata.xml", issue_files)


if __name__ == "__main__":
    unittest.main()
