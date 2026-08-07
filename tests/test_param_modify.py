"""参数级修改 DSL（V1）测试：openbrep/runtime/param_modify.py + TaskPipeline._try_param_modify。

覆盖（任务 V1 测试要求）：
- parse：合法 JSON 各 op（set_value 多参数/add/del/rename）、坏 JSON、参数不存在、
  类型不符、多义描述、保留名 rename 拒绝、复合请求回落（沿用 micro_modify 的
  疑问词纪律）、空 operations 回落、非参数级请求不浪费 LLM 调用
- apply：版本快照生成（param_modify metadata）、paramlist 变更、rename 脚本整词
  替换、守护回滚（变更超界文件时）
- pipeline：DSL 命中端到端（mock LLM，agent_loop 默认路径 + 显式关闭路径）、
  DSL 未命中回落 agent loop / 旧路径行为不变、DEBUG/带图不走 DSL
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse, MockLLM
from openbrep.runtime.param_modify import (
    ApplyOutcome,
    ParamModifyPlan,
    ParamOp,
    apply_param_modify,
    format_op_summary,
    parse_param_modify,
)
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest


def _make_project() -> HSFProject:
    proj = HSFProject.create_new("test_shelf", work_dir="./workdir")
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
        GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"),
        GDLParameter(name="show_frame", type_tag="Boolean", description="显示边框", value="1"),
        GDLParameter(name="ratio", type_tag="RealNum", description="比例", value="1.0"),
        GDLParameter(name="mat_name", type_tag="String", description="材质名", value="oak"),
        GDLParameter(name="shelf_h", type_tag="Length", description="中层板高度", value="0.9"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "BLOCK A, B, ZZYZX\n"
        "ADDZ shelf_h\n"
        "BLOCK A, B, shelf_thk\n"
        "DEL 1\n"
        "FOR i = 1 TO shelf_count\nNEXT i\n"
        "END\n"
    )
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    return proj


class FakeLLM:
    """记录调用并返回脚本化内容的最小 LLM 替身。"""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.last_messages = None

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        return LLMResponse(content=self.content, model="fake", usage={}, finish_reason="stop")


def _plan_json(*ops: dict) -> str:
    return json.dumps({"operations": list(ops)}, ensure_ascii=False)


# ── parse：合法 JSON 各 op ────────────────────────────────

class TestParseParamModify(unittest.TestCase):
    def parse(self, text: str, content: str, project=None):
        llm = FakeLLM(content)
        plan = parse_param_modify(text, project or _make_project(), llm)
        return plan, llm

    def test_set_value_multi_param_normalized(self):
        plan, _ = self.parse(
            "把 shelf_count 改成 5，把 shelf_thk 改成 25mm，关闭 show_frame",
            _plan_json(
                {"op": "set_value", "param": "shelf_count", "value": 5},
                {"op": "set_value", "param": "shelf_thk", "value": 0.025},
                {"op": "set_value", "param": "show_frame", "value": False},
            ),
        )
        self.assertIsNotNone(plan)
        ops = {op.param: op for op in plan.operations}
        self.assertEqual(ops["shelf_count"].value, "5")
        self.assertEqual(ops["shelf_count"].old_value, "4")
        self.assertEqual(ops["shelf_thk"].value, "0.025")
        self.assertEqual(ops["show_frame"].value, "0")

    def test_add_param(self):
        plan, _ = self.parse(
            "新增一个参数 brand，类型 String，默认 oak",
            _plan_json({"op": "add_param", "name": "brand", "type": "String", "value": "oak", "description": "品牌"}),
        )
        self.assertIsNotNone(plan)
        op = plan.operations[0]
        self.assertEqual((op.op, op.name, op.type, op.value), ("add_param", "brand", "String", "oak"))

    def test_add_param_rejects_reserved_and_dup_and_bad_type(self):
        for ops, label in (
            ({"op": "add_param", "name": "A", "type": "Integer", "value": 1}, "保留名"),
            ({"op": "add_param", "name": "shelf_count", "type": "Integer", "value": 1}, "重名"),
            ({"op": "add_param", "name": "foo", "type": "NotAType", "value": 1}, "非法类型"),
            ({"op": "add_param", "name": "foo", "type": "Title", "value": 1}, "Title 无值"),
        ):
            plan, _ = self.parse("新增参数", _plan_json(ops))
            self.assertIsNone(plan, label)

    def test_del_param_unreferenced_ok(self):
        plan, _ = self.parse("删除 ratio 参数", _plan_json({"op": "del_param", "param": "ratio"}))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.operations[0].op, "del_param")
        self.assertEqual(plan.operations[0].old_value, "1.0")

    def test_del_param_referenced_or_reserved_rejected(self):
        # shelf_thk 在 3d.gdl 中被引用
        plan, _ = self.parse("删除 shelf_thk", _plan_json({"op": "del_param", "param": "shelf_thk"}))
        self.assertIsNone(plan)
        # 保留名不可删
        plan, _ = self.parse("删除 A", _plan_json({"op": "del_param", "param": "A"}))
        self.assertIsNone(plan)

    def test_rename_param_with_script_reference(self):
        plan, _ = self.parse(
            "把 shelf_h 改名为 board_h",
            _plan_json({"op": "rename_param", "from": "shelf_h", "to": "board_h"}),
        )
        self.assertIsNotNone(plan)
        op = plan.operations[0]
        self.assertEqual((op.op, op.from_name, op.name), ("rename_param", "shelf_h", "board_h"))

    def test_rename_param_rejects_reserved_source_or_target(self):
        for ops, label in (
            ({"op": "rename_param", "from": "A", "to": "width"}, "源是保留名"),
            ({"op": "rename_param", "from": "shelf_h", "to": "A"}, "目标是保留名"),
            ({"op": "rename_param", "from": "shelf_h", "to": "shelf_h"}, "无意义改名"),
            ({"op": "rename_param", "from": "shelf_h", "to": "shelf_count"}, "目标重名"),
            ({"op": "rename_param", "from": "nope", "to": "x"}, "源不存在"),
        ):
            plan, _ = self.parse("改名", _plan_json(ops))
            self.assertIsNone(plan, label)

    # ── parse：坏 JSON / 结构错误 / 校验失败 ────────────────

    def test_bad_json_falls_through(self):
        for content in ("not json", "[1,2,3]", '{"foo": 1}', ""):
            plan, _ = self.parse("把 shelf_count 改成 5", content)
            self.assertIsNone(plan)

    def test_json_with_surrounding_text_extracted(self):
        plan, _ = self.parse(
            "把 shelf_count 改成 5",
            "好的，以下是解析结果：```json\n" + _plan_json({"op": "set_value", "param": "shelf_count", "value": 5}) + "\n```",
        )
        self.assertIsNotNone(plan)

    def test_param_not_exists(self):
        plan, _ = self.parse(
            "把 leg_count 改成 4",
            _plan_json({"op": "set_value", "param": "leg_count", "value": 4}),
        )
        self.assertIsNone(plan)

    def test_type_mismatch(self):
        cases = [
            ("shelf_count 改成 5.5", {"op": "set_value", "param": "shelf_count", "value": 5.5}, "Integer 小数"),
            ("shelf_thk 改成 25mm", {"op": "set_value", "param": "shelf_thk", "value": "25mm"}, "Length 带单位字符串"),
            ("mat_name 改成 5", {"op": "set_value", "param": "mat_name", "value": 5}, "String 数值"),
            ("show_frame 改成 2", {"op": "set_value", "param": "show_frame", "value": 2}, "Boolean 非 0/1"),
        ]
        for text, op, label in cases:
            plan, _ = self.parse(text, _plan_json(op))
            self.assertIsNone(plan, label)

    def test_ambiguous_description_as_param_name(self):
        # LLM 用描述文字"层板厚度"当参数名 → 不是精确名称，拒绝
        plan, _ = self.parse(
            "把层板厚度改成 20mm",
            _plan_json({"op": "set_value", "param": "层板厚度", "value": 0.02}),
        )
        self.assertIsNone(plan)

    def test_empty_operations_falls_through(self):
        plan, llm = self.parse("把 shelf_count 改成 5，另外优化一下脚本", '{"operations": []}')
        self.assertIsNone(plan)
        self.assertEqual(llm.calls, 1)

    def test_unknown_op_falls_through(self):
        plan, _ = self.parse("把脚本优化一下", _plan_json({"op": "rewrite_script", "param": "x"}))
        self.assertIsNone(plan)

    def test_compound_mixed_request_with_explain_falls_through(self):
        # 混合"修改 + 解释"意图：LLM 正确输出空数组 → 回落
        plan, _ = self.parse(
            "把 shelf_count 改成 5，另外解释一下这个参数",
            '{"operations": []}',
        )
        self.assertIsNone(plan)

    def test_extra_work_hint_falls_through_without_llm_call(self):
        # "顺便/再帮"带额外非参数工作：沿用 micro_modify 复合词纪律，不调 LLM
        for text in ("把 shelf_count 改成 5，顺便优化一下脚本", "把 shelf_count 改成 5，再帮我看看脚本"):
            llm = FakeLLM('{"operations": []}')
            plan = parse_param_modify(text, _make_project(), llm)
            self.assertIsNone(plan)
            self.assertEqual(llm.calls, 0, text)

    # ── parse：复合/疑问词纪律（沿用 micro_modify）──────────

    def test_question_hint_falls_through_without_llm_call(self):
        for text in ("为什么 shelf_count 是 4？", "把 shelf_count 改成 5 为什么塌了？", "怎么看 shelf_thk 的值？"):
            llm = FakeLLM('{"operations": []}')
            plan = parse_param_modify(text, _make_project(), llm)
            self.assertIsNone(plan)
            self.assertEqual(llm.calls, 0, text)

    def test_non_param_request_skips_llm(self):
        # 非参数级请求（无参数名/描述/参数关键词）：不浪费 LLM 调用
        llm = FakeLLM('{"operations": []}')
        plan = parse_param_modify("给书架加一扇门", _make_project(), llm)
        self.assertIsNone(plan)
        self.assertEqual(llm.calls, 0)

    def test_llm_call_uses_low_temperature_and_small_budget(self):
        captured = {}

        class CaptureLLM(FakeLLM):
            def generate(self, messages, **kwargs):
                captured.update(kwargs)
                return super().generate(messages, **kwargs)

        llm = CaptureLLM(_plan_json({"op": "set_value", "param": "shelf_count", "value": 5}))
        parse_param_modify("把 shelf_count 改成 5", _make_project(), llm)
        self.assertEqual(captured.get("temperature"), 0.0)
        self.assertEqual(captured.get("max_tokens"), 1024)
        self.assertIs(captured.get("stream"), False)


# ── apply：快照 / paramlist 变更 / rename / 守护回滚 ────────

class TestApplyParamModify(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _on_disk_project(self):
        proj = _make_project()
        proj.work_dir = self.tmp
        proj.root = self.tmp / proj.name
        proj.save_to_disk()
        return proj

    def test_set_value_multi_persists_paramlist_and_revision_metadata(self):
        calls = []

        def fake_revision(*args, **kwargs):
            calls.append(kwargs)
            return MagicMock(revision_id="r0001")

        proj = self._on_disk_project()
        plan = ParamModifyPlan(
            operations=[
                ParamOp(op="set_value", param="shelf_count", value="5", old_value="4"),
                ParamOp(op="set_value", param="shelf_thk", value="0.025", old_value="0.018"),
            ],
            raw={},
        )
        outcome = apply_param_modify(
            proj, plan, user_instruction="改两个参数", metadata={"param_modify": {"plan": plan.to_dict()}},
            create_revision=fake_revision,
        )
        self.assertTrue(outcome.applied)
        self.assertEqual(outcome.revision_id, "r0001")
        self.assertEqual(proj.get_parameter("shelf_count").value, "5")
        self.assertEqual(proj.get_parameter("shelf_thk").value, "0.025")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["message"], "auto: before modify")
        self.assertEqual(calls[0]["changed_files"], ["paramlist.xml"])
        self.assertIn("param_modify", calls[0]["metadata"])
        paramlist = (self.tmp / proj.name / "paramlist.xml").read_text(encoding="utf-8")
        self.assertIn("<Value>5</Value>", paramlist)
        self.assertIn("<Value>0.025</Value>", paramlist)

    def test_rename_updates_paramlist_and_scripts_whole_word(self):
        proj = self._on_disk_project()
        plan = ParamModifyPlan(
            operations=[ParamOp(op="rename_param", from_name="shelf_h", name="board_h")],
            raw={},
        )
        outcome = apply_param_modify(proj, plan, create_revision=lambda *a, **k: MagicMock(revision_id="r1"))
        self.assertTrue(outcome.applied)
        self.assertIn("paramlist.xml", outcome.changed_files)
        self.assertIn("scripts/3d.gdl", outcome.changed_files)
        self.assertNotIn("scripts/2d.gdl", outcome.changed_files)
        self.assertIsNone(proj.get_parameter("shelf_h"))
        self.assertEqual(proj.get_parameter("board_h").value, "0.9")
        self.assertIn("ADDZ board_h", proj.get_script(ScriptType.SCRIPT_3D))
        self.assertNotIn("shelf_h", proj.get_script(ScriptType.SCRIPT_3D))
        self.assertEqual(plan.operations[0].occurrences, 1)
        # VALUES "name" 整串替换也生效
        proj2 = self._on_disk_project()
        proj2.set_script(ScriptType.SCRIPT_3D, 'VALUES "shelf_h"\nADDZ shelf_h\n')
        plan2 = ParamModifyPlan(operations=[ParamOp(op="rename_param", from_name="shelf_h", name="board_h")], raw={})
        apply_param_modify(proj2, plan2, create_revision=lambda *a, **k: MagicMock(revision_id="r1"))
        self.assertIn('VALUES "board_h"', proj2.get_script(ScriptType.SCRIPT_3D))
        self.assertNotIn('"shelf_h"', proj2.get_script(ScriptType.SCRIPT_3D))

    def test_add_and_del_params_persist(self):
        proj = self._on_disk_project()
        plan = ParamModifyPlan(
            operations=[
                ParamOp(op="add_param", name="brand", type="String", value="oak", description="品牌"),
                ParamOp(op="del_param", param="ratio", old_value="1.0"),
            ],
            raw={},
        )
        outcome = apply_param_modify(proj, plan, create_revision=lambda *a, **k: MagicMock(revision_id="r1"))
        self.assertTrue(outcome.applied)
        self.assertIsNotNone(proj.get_parameter("brand"))
        self.assertIsNone(proj.get_parameter("ratio"))
        paramlist = (self.tmp / proj.name / "paramlist.xml").read_text(encoding="utf-8")
        self.assertIn('Name="brand"', paramlist)
        self.assertNotIn('Name="ratio"', paramlist)

    def test_guard_rolls_back_when_out_of_scope_file_changes(self):
        # 构造一个 save_to_disk 会改 libpartdata.xml 的项目（解析器不认识的额外属性）
        src = self.tmp / "Shelf"
        proj = _make_project()
        proj.work_dir = self.tmp
        proj.root = src
        proj.save_to_disk()
        lp = src / "libpartdata.xml"
        lp.write_text(lp.read_text(encoding="utf-8").replace("<LibpartData Owner=", '<LibpartData Extra="keepme" Owner='), encoding="utf-8")
        proj = HSFProject.load_from_disk(str(src))

        plan = ParamModifyPlan(operations=[ParamOp(op="set_value", param="shelf_count", value="5", old_value="4")], raw={})
        outcome = apply_param_modify(proj, plan, create_revision=lambda *a, **k: MagicMock(revision_id="r1"))
        self.assertFalse(outcome.applied)
        # 内存与磁盘都回滚
        self.assertEqual(proj.get_parameter("shelf_count").value, "4")
        paramlist = (src / "paramlist.xml").read_text(encoding="utf-8")
        self.assertIn("<Value>4</Value>", paramlist)
        self.assertNotIn("<Value>5</Value>", paramlist)
        self.assertIn('Extra="keepme"', lp.read_text(encoding="utf-8"))
        self.assertTrue(any("守护回滚" in w for w in outcome.warnings))

    def test_apply_skips_revision_when_not_on_disk(self):
        proj = _make_project()  # 未落盘（独立 workdir，保证 root 不存在）
        proj.work_dir = self.tmp / "not_on_disk"
        proj.root = proj.work_dir / proj.name
        plan = ParamModifyPlan(operations=[ParamOp(op="set_value", param="shelf_count", value="5", old_value="4")], raw={})
        outcome = apply_param_modify(proj, plan, create_revision=lambda *a, **k: MagicMock(revision_id="r1"))
        self.assertTrue(outcome.applied)
        self.assertEqual(proj.get_parameter("shelf_count").value, "5")
        self.assertTrue(any("跳过自动版本快照" in w for w in outcome.warnings))


# ── pipeline：DSL 命中端到端 / 回落路径不变 ────────────────

def _make_pipeline(mock_llm) -> TaskPipeline:
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir="./traces")
    pipeline._make_llm = lambda req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


class TestPipelineParamModify(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _project_on_disk(self):
        proj = _make_project()
        proj.work_dir = self.tmp
        proj.root = self.tmp / proj.name
        proj.save_to_disk()
        return proj

    def test_dsl_hit_end_to_end_with_agent_loop_default(self):
        mock_llm = MockLLM(responses=[_plan_json(
            {"op": "set_value", "param": "shelf_count", "value": 5},
            {"op": "set_value", "param": "shelf_thk", "value": 0.025},
        )])
        pipeline = _make_pipeline(mock_llm)
        project = self._project_on_disk()
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5，把 shelf_thk 改成 25mm",
            intent="MODIFY", project=project, work_dir=str(self.tmp),
            output_dir=str(self.tmp / "out"), agent_loop=True,
        ))
        self.assertTrue(result.success)
        self.assertEqual(mock_llm.call_count, 1)  # 只有一次意图解析调用
        self.assertEqual(project.get_parameter("shelf_count").value, "5")
        self.assertEqual(project.get_parameter("shelf_thk").value, "0.025")
        self.assertIsNotNone(result.compile_result)
        self.assertIn("确定性参数修改", result.plain_text)
        self.assertIn("shelf_count", result.plain_text)
        self.assertIn("LLM 仅做意图解析", result.plain_text)
        self.assertEqual(result.metadata["param_modify"]["plan"]["operations"][0]["param"], "shelf_count")
        self.assertIs(result.metadata["param_modify"]["compile_success"], True)

    def test_dsl_hit_when_agent_loop_explicitly_off(self):
        # 单参数设值会被 micro_modify 截获；这里用多参数请求验证 DSL 路径
        mock_llm = MockLLM(responses=[_plan_json(
            {"op": "set_value", "param": "shelf_count", "value": 5},
            {"op": "set_value", "param": "shelf_thk", "value": 0.025},
        )])
        pipeline = _make_pipeline(mock_llm)
        project = self._project_on_disk()
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5，把 shelf_thk 改成 25mm",
            intent="MODIFY", project=project, work_dir=str(self.tmp),
            output_dir=str(self.tmp / "out"), agent_loop=False,
        ))
        self.assertTrue(result.success)
        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(project.get_parameter("shelf_count").value, "5")
        self.assertEqual(project.get_parameter("shelf_thk").value, "0.025")

    def test_dsl_miss_falls_back_to_old_script_update_path(self):
        # DSL 解析失败（返回非 JSON）→ 回落 _handle_script_update（旧路径）
        mock_llm = MockLLM(responses=[
            "这不是 JSON",  # DSL 意图解析
            "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n",  # 旧路径 LLM 应答
        ])
        pipeline = _make_pipeline(mock_llm)
        project = self._project_on_disk()
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5，另外修一下 3d 脚本",
            intent="MODIFY", project=project, work_dir=str(self.tmp),
            output_dir=str(self.tmp / "out"), agent_loop=False,
        ))
        self.assertIsNotNone(result)
        self.assertEqual(mock_llm.call_count, 2)
        self.assertIn("BLOCK A, B, ZZYZX", project.get_script(ScriptType.SCRIPT_3D))

    def test_dsl_miss_falls_back_to_agent_loop(self):
        # agent_loop 默认路径：DSL 未命中（空操作）→ run_modify_agent_loop 照旧执行
        mock_llm = MockLLM(responses=[
            '{"operations": []}',  # DSL：空操作
            "已加一层层板，编译通过。",  # agent loop 纯文本完成
        ])
        pipeline = _make_pipeline(mock_llm)
        project = self._project_on_disk()
        result = pipeline.execute(TaskRequest(
            user_input="把 shelf_count 改成 5，另外优化一下脚本",
            intent="MODIFY", project=project, work_dir=str(self.tmp),
            output_dir=str(self.tmp / "out"), agent_loop=True,
        ))
        self.assertIsNotNone(result)
        self.assertGreaterEqual(mock_llm.call_count, 2)
        self.assertIsNotNone(result.compile_result)

    def test_debug_and_repair_intent_not_intercepted(self):
        pipeline = _make_pipeline(MockLLM(responses=["x"]))
        project = self._project_on_disk()
        for intent in ("DEBUG", "REPAIR"):
            result = pipeline._try_param_modify(TaskRequest(
                user_input="把 shelf_count 改成 5",
                intent=intent, project=project, work_dir=str(self.tmp),
                output_dir=str(self.tmp / "out"),
            ))
            self.assertIsNone(result, intent)

    def test_image_request_not_intercepted(self):
        pipeline = _make_pipeline(MockLLM(responses=["x"]))
        project = self._project_on_disk()
        result = pipeline._try_param_modify(TaskRequest(
            user_input="把 shelf_count 改成 5",
            intent="MODIFY", project=project, work_dir=str(self.tmp),
            output_dir=str(self.tmp / "out"), image_b64="aGVsbG8=",
        ))
        self.assertIsNone(result)

    def test_format_op_summary(self):
        self.assertEqual(format_op_summary(ParamOp(op="set_value", param="shelf_count", value="5", old_value="4")),
                         "set_value：shelf_count：4 → 5")
        self.assertEqual(format_op_summary(ParamOp(op="rename_param", from_name="shelf_h", name="board_h", occurrences=2)),
                         "rename_param：shelf_h → board_h（脚本引用同步 2 处）")


if __name__ == "__main__":
    unittest.main()
