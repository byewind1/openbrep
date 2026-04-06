import unittest

from openbrep.explainer.context_builder import build_project_context, build_script_context
from openbrep.hsf_project import HSFProject, ScriptType


class TestExplainerContextBuilder(unittest.TestCase):
    def test_build_script_context(self):
        ctx = build_script_context(
            script_type="3D",
            script_text="BLOCK A, B, ZZYZX\nEND\n",
            parameters=["A", "B", "ZZYZX"],
        )

        self.assertEqual(ctx["script_type"], "3D")
        self.assertIn("BLOCK A, B, ZZYZX", ctx["script_text"])
        self.assertEqual(ctx["parameters"], ["A", "B", "ZZYZX"])

    def test_build_project_context(self):
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        project.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"

        ctx = build_project_context(project)

        self.assertEqual(ctx["gsm_name"], "chair")
        self.assertIn("scripts", ctx)
        self.assertIn("3D", ctx["scripts"])
        self.assertIn("2D", ctx["scripts"])
