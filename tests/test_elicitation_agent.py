import unittest
from unittest.mock import MagicMock

from openbrep.elicitation_agent import (
    ElicitationAgent,
    ElicitationState,
    GDLSpec,
    ParamDef,
)


class TestParamDefAndSpec(unittest.TestCase):
    def test_gdlspec_to_instruction_includes_geometry_and_parameters(self):
        spec = GDLSpec(
            object_name="书架",
            geometry_intent="落地式双侧板书架，四层层板",
            parameters=[
                ParamDef("width", "Length", "整体宽度", "900"),
                ParamDef("shelf_count", "Integer", "层板数量", "4"),
            ],
            materials=["柜体", "层板"],
            has_2d=True,
            special_behaviors=["层板数量变化时自动重排"],
            confirmed=True,
        )

        instruction = spec.to_instruction()

        self.assertIn("书架", instruction)
        self.assertIn("落地式双侧板书架", instruction)
        self.assertIn("width", instruction)
        self.assertIn("shelf_count", instruction)
        self.assertIn("2D", instruction)
        self.assertIn("层板数量变化时自动重排", instruction)


class TestElicitationAgent(unittest.TestCase):
    def _make_question_llm(self):
        return MagicMock(return_value="请先描述这个对象的几何形态，比如是柜体、层板还是支架结构？")

    def _make_extract_llm(self):
        payload = (
            '{'
            '"object_name":"书架",'
            '"geometry_intent":"落地式书架，双侧板，四层层板",'
            '"parameters":['
            '{"name":"width","type":"Length","description":"整体宽度","default":"900"},'
            '{"name":"depth","type":"Length","description":"整体进深","default":"300"}'
            '],'
            '"materials":["柜体","层板"],'
            '"has_2d":true,'
            '"special_behaviors":["层板数量变化时自动重排"],'
            '"confirmed":false'
            '}'
        )
        return MagicMock(side_effect=[
            "请先描述这个对象的几何形态，比如是柜体、层板还是支架结构？",
            "这个对象需要哪些可调参数？比如宽度、高度、层板数量。",
            "这个对象的材质要分成哪些区域？比如柜体、门板、把手。",
            "这个对象在平面 2D 里需要怎样表达？是否要简化符号？",
            "这个对象还有什么特殊行为？比如联动、开关、显示控制。",
            payload,
        ])

    def test_start_returns_geometry_question(self):
        llm = self._make_question_llm()
        agent = ElicitationAgent(llm_caller=llm)

        question = agent.start("创建一个书架")

        self.assertEqual(agent.state, ElicitationState.ELICITING)
        self.assertIn("几何", question)

    def test_start_without_llm_caller_raises_clear_error(self):
        agent = ElicitationAgent(llm_caller=None)

        with self.assertRaises(Exception) as cm:
            agent.start("创建一个书架")

        self.assertIn("llm_caller", str(cm.exception))

    def test_ask_dimension_prompt_contains_single_question_and_no_guess_rules(self):
        llm = self._make_question_llm()
        agent = ElicitationAgent(llm_caller=llm)

        agent._ask_dimension("geometry", {"object_name": "书架"})

        prompt_text = str(llm.call_args)
        self.assertIn("每次只问一个问题", prompt_text)
        self.assertIn("不猜测", prompt_text)

    def test_respond_advances_dimension_until_spec_ready(self):
        llm = self._make_extract_llm()
        agent = ElicitationAgent(llm_caller=llm)
        agent.start("创建一个书架")

        reply_1, done_1 = agent.respond("落地式，双侧板，四层")
        reply_2, done_2 = agent.respond("宽高深、层板数量")
        reply_3, done_3 = agent.respond("柜体和层板分开")
        reply_4, done_4 = agent.respond("要有简化平面符号")
        reply_5, done_5 = agent.respond("层板数量变化时自动重排")

        self.assertFalse(done_1)
        self.assertFalse(done_2)
        self.assertFalse(done_3)
        self.assertFalse(done_4)
        self.assertTrue(done_5)
        self.assertEqual(agent.state, ElicitationState.SPEC_READY)
        self.assertIn("书架", reply_5)
        self.assertIn("确认", reply_5)
        self.assertIn("材质", reply_2)
        self.assertIn("2D", reply_3)
        self.assertIn("特殊行为", reply_4)

    def test_confirm_true_returns_spec_and_enters_handoff(self):
        llm = self._make_extract_llm()
        agent = ElicitationAgent(llm_caller=llm)
        agent.start("创建一个书架")
        agent.respond("落地式，双侧板，四层")
        agent.respond("宽高深、层板数量")
        agent.respond("柜体和层板分开")
        agent.respond("要有简化平面符号")
        agent.respond("层板数量变化时自动重排")

        spec = agent.confirm(True)

        self.assertIsInstance(spec, GDLSpec)
        self.assertEqual(agent.state, ElicitationState.HANDOFF)
        self.assertTrue(spec.confirmed)

    def test_confirm_false_returns_to_eliciting(self):
        llm = self._make_extract_llm()
        agent = ElicitationAgent(llm_caller=llm)
        agent.start("创建一个书架")
        agent.respond("落地式，双侧板，四层")
        agent.respond("宽高深、层板数量")
        agent.respond("柜体和层板分开")
        agent.respond("要有简化平面符号")
        agent.respond("层板数量变化时自动重排")

        spec = agent.confirm(False)

        self.assertIsNone(spec)
        self.assertEqual(agent.state, ElicitationState.ELICITING)

    def test_reset_clears_all_state(self):
        agent = ElicitationAgent(llm_caller=self._make_question_llm())
        agent.start("创建一个书架")

        agent.reset()

        self.assertEqual(agent.state, ElicitationState.IDLE)
        self.assertIsNone(agent.spec)
        self.assertEqual(agent.current_dimension, 0)
        self.assertEqual(agent.conversation_history, [])

    def test_extract_spec_returns_structured_gdlspec(self):
        llm = MagicMock(return_value=(
            '{'
            '"object_name":"电视柜",'
            '"geometry_intent":"低矮电视柜，左右抽屉，中间开放格",'
            '"parameters":[{"name":"width","type":"Length","description":"总宽度","default":"1600"}],'
            '"materials":["柜体","台面"],'
            '"has_2d":false,'
            '"special_behaviors":["抽屉开关显示"],'
            '"confirmed":false'
            '}'
        ))
        agent = ElicitationAgent(llm_caller=llm)
        agent.conversation_history = [
            {"role": "user", "content": "我想做一个电视柜"},
            {"role": "assistant", "content": "请描述几何"},
            {"role": "user", "content": "低矮电视柜，左右抽屉，中间开放格"},
        ]

        spec = agent._extract_spec()

        self.assertEqual(spec.object_name, "电视柜")
        self.assertEqual(spec.parameters[0].name, "width")
        self.assertIn("抽屉开关显示", spec.special_behaviors)


if __name__ == "__main__":
    unittest.main()
