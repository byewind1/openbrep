import unittest

from openbrep.explainer.service import explain_project_context, explain_script_context


class TestExplainerService(unittest.TestCase):
    def test_explain_script_context_returns_structured_result(self):
        result = explain_script_context({
            "script_type": "3D",
            "script_text": "BLOCK A, B, ZZYZX\nEND\n",
            "parameters": ["A", "B", "ZZYZX"],
        })

        self.assertEqual(result.script_type, "3D")
        self.assertTrue(result.goal)
        self.assertIn("A", result.parameters)

    def test_explain_project_context_returns_structured_result(self):
        result = explain_project_context({
            "gsm_name": "chair",
            "scripts": {
                "3D": "BLOCK A, B, ZZYZX\nEND\n",
                "2D": "PROJECT2 3, 270, 2\n",
            },
            "parameters": ["A", "B", "ZZYZX"],
        })

        self.assertEqual(result.overall_goal, "chair")
        self.assertTrue(result.script_roles)
        self.assertIn("3D", [item.title for item in result.script_roles])
