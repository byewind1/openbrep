import unittest

from openbrep.explainer.context_builder import (
    build_project_context,
    build_project_parameter_context,
    build_project_script_context,
    build_script_context,
    resolve_parameter_targets,
    resolve_script_target,
)
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

    def test_resolve_script_target(self):
        self.assertEqual(resolve_script_target("解释一下 3D 脚本"), "3D")
        self.assertEqual(resolve_script_target("2D 脚本负责什么"), "2D")

    def test_resolve_parameter_targets(self):
        project = HSFProject.create_new("chair", work_dir="./workdir")

        targets = resolve_parameter_targets(project, "A/B/ZZYZX 分别控制什么")

        self.assertEqual(targets, ["A", "B", "ZZYZX"])

    def test_build_project_script_context(self):
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"

        ctx = build_project_script_context(project, "2D")

        self.assertEqual(ctx["script_type"], "2D")
        self.assertIn("PROJECT2", ctx["script_text"])

    def test_build_project_parameter_context(self):
        project = HSFProject.create_new("chair", work_dir="./workdir")
        project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
        project.scripts[ScriptType.PARAM] = "VALUES \"A\" RANGE [0.1, 2.0]\n"

        ctx = build_project_parameter_context(project, "A")

        self.assertEqual(ctx["name"], "A")
        self.assertEqual(ctx["type_tag"], "Length")
        self.assertTrue(ctx["usage_hits"])
        self.assertIn("3D", [item["script"] for item in ctx["usage_hits"]])
