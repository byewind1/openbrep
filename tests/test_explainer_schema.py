import unittest

from openbrep.explainer.schema import (
    ExplanationSection,
    ProjectExplanation,
    ScriptExplanation,
)


class TestExplainerSchema(unittest.TestCase):
    def test_script_explanation_to_dict(self):
        item = ScriptExplanation(
            script_type="3D",
            goal="生成主体几何",
            key_commands=["BLOCK", "ADD"],
            parameters=["A", "B"],
            risks=["依赖未声明参数"],
        )

        data = item.to_dict()

        self.assertEqual(data["script_type"], "3D")
        self.assertEqual(data["goal"], "生成主体几何")
        self.assertEqual(data["key_commands"], ["BLOCK", "ADD"])
        self.assertEqual(data["parameters"], ["A", "B"])
        self.assertEqual(data["risks"], ["依赖未声明参数"])

    def test_project_explanation_to_dict(self):
        item = ProjectExplanation(
            overall_goal="书架对象",
            parameters_summary=["A: 宽度", "B: 深度"],
            script_roles=[
                ExplanationSection(title="3D", summary="主体几何"),
                ExplanationSection(title="2D", summary="平面符号"),
            ],
            dependencies=["Master -> 3D"],
            baggage=["历史兼容参数较多"],
        )

        data = item.to_dict()

        self.assertEqual(data["overall_goal"], "书架对象")
        self.assertEqual(data["parameters_summary"], ["A: 宽度", "B: 深度"])
        self.assertEqual(data["script_roles"][0]["title"], "3D")
        self.assertEqual(data["dependencies"], ["Master -> 3D"])
        self.assertEqual(data["baggage"], ["历史兼容参数较多"])
