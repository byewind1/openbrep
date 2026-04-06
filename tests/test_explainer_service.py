import unittest

from openbrep.explainer.service import explain_parameter_context, explain_project_context, explain_script_context


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

    def test_explain_parameter_context_returns_structured_result(self):
        result = explain_parameter_context({
            "name": "A",
            "type_tag": "Length",
            "default_value": "1.00",
            "description": "Width",
            "is_fixed": True,
            "usage_hits": [
                {"script": "3D", "lines": ["BLOCK A, B, ZZYZX"]},
                {"script": "PARAM", "lines": ['VALUES "A" RANGE [0.1, 2.0]']},
            ],
        })

        self.assertEqual(result.name, "A")
        self.assertIn("3D", result.used_in_scripts)
        self.assertTrue(result.usage_summaries)
        self.assertTrue(result.risks)

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
