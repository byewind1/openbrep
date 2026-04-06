import unittest

from openbrep.explainer.chat_adapter import (
    build_chat_explanation_reply,
    detect_explanation_detail_level,
)
from openbrep.explainer.schema import ExplanationSection, ParameterExplanation, ProjectExplanation, ScriptExplanation


class TestExplainerChatAdapter(unittest.TestCase):
    def test_detect_explanation_detail_level_defaults_to_brief(self):
        self.assertEqual(detect_explanation_detail_level("这是什么对象？"), "brief")
        self.assertEqual(detect_explanation_detail_level("这个对象为什么这样"), "brief")

    def test_detect_explanation_detail_level_detects_detailed(self):
        self.assertEqual(detect_explanation_detail_level("详细讲讲这个对象"), "detailed")
        self.assertEqual(detect_explanation_detail_level("你展开说说"), "detailed")

    def test_detect_explanation_detail_level_detects_code_and_has_priority(self):
        self.assertEqual(detect_explanation_detail_level("分析这段3D代码逻辑"), "code")
        self.assertEqual(detect_explanation_detail_level("详细做代码分析"), "code")

    def test_build_chat_explanation_reply_returns_brief_decomposition_by_default(self):
        explanation = ProjectExplanation(
            overall_goal="chair",
            parameters_summary=["A: 宽度", "B: 深度"],
            script_roles=[
                ExplanationSection(title="3D", summary="主体几何"),
                ExplanationSection(title="2D", summary="平面符号"),
            ],
            dependencies=["MASTER -> 3D"],
            baggage=["历史参数较多"],
        )

        reply = build_chat_explanation_reply(explanation)

        self.assertIn("构件类型/用途", reply)
        self.assertIn("chair", reply)
        self.assertIn("关键部分", reply)
        self.assertIn("核心逻辑", reply)
        self.assertNotIn("参数说明：", reply)
        self.assertNotIn("历史包袱：", reply)
        self.assertNotIn("[FILE:", reply)

    def test_build_chat_explanation_reply_supports_detailed_mode(self):
        explanation = ProjectExplanation(
            overall_goal="chair",
            parameters_summary=["A: 宽度", "B: 深度"],
            script_roles=[ExplanationSection(title="3D", summary="主体几何")],
            dependencies=["MASTER -> 3D"],
            baggage=["历史参数较多"],
        )

        reply = build_chat_explanation_reply(explanation, detail_level="detailed")

        self.assertIn("参数说明：", reply)
        self.assertIn("脚本职责：", reply)
        self.assertIn("历史包袱：", reply)

    def test_build_chat_explanation_reply_supports_code_mode_for_script(self):
        explanation = ScriptExplanation(
            script_type="3D",
            goal="生成主体几何",
            key_commands=["BLOCK", "ADD"],
            parameters=["A", "B", "ZZYZX"],
            risks=["依赖未声明参数"],
        )

        reply = build_chat_explanation_reply(explanation, detail_level="code")

        self.assertIn("关键命令", reply)
        self.assertIn("BLOCK", reply)
        self.assertIn("逻辑", reply)

    def test_build_chat_explanation_reply_supports_parameter_brief_mode(self):
        explanation = ParameterExplanation(
            name="A",
            type_tag="Length",
            default_value="1.00",
            description="Width",
            used_in_scripts=["3D", "PARAM"],
            usage_summaries=["3D: BLOCK A, B, ZZYZX"],
        )

        reply = build_chat_explanation_reply(explanation)

        self.assertIn("参数：A", reply)
        self.assertIn("主要影响：3D, PARAM", reply)
        self.assertIn("核心逻辑", reply)

    def test_build_chat_explanation_reply_supports_parameter_detailed_mode(self):
        explanation = ParameterExplanation(
            name="ZZYZX",
            type_tag="Length",
            default_value="1.00",
            description="Height",
            is_fixed=True,
            used_in_scripts=["3D"],
            usage_summaries=["3D: BLOCK A, B, ZZYZX"],
            risks=["该参数为固定参数，修改时要注意与构件整体尺寸联动"],
        )

        reply = build_chat_explanation_reply(explanation, detail_level="detailed")

        self.assertIn("含义：Height", reply)
        self.assertIn("主要影响脚本：3D", reply)
        self.assertIn("风险点", reply)

    def test_build_chat_explanation_reply_can_auto_detect_detail_level(self):
        explanation = ScriptExplanation(
            script_type="3D",
            goal="生成主体几何",
            key_commands=["BLOCK", "ADD"],
            parameters=["A", "B", "ZZYZX"],
            risks=["依赖未声明参数"],
        )

        reply = build_chat_explanation_reply(explanation, user_input="分析这段3D代码逻辑")

        self.assertIn("关键命令", reply)
        self.assertIn("逻辑", reply)
        self.assertIn("注意点", reply)
