"""结构化 skill operations 模板走确定性路径（任务 S3）测试。

覆盖（任务要求）：
- operations 字段解析：坏 JSON / 形态非法（缺 match / 空 ops / 未知 op / 坏关键词）
- match 精度：命中 / 不命中 / 参数未在指令出现 / proposed 不匹配 / 参数不存在 /
  多 skill 命中歧义（参数不存在的候选被 prune 后不构成歧义）
- 确定性填值：{{number}}（Integer）/ 单位换算（Length mm→m）/ {{boolean}}（布尔词）
- LLM 填值回落：确定性抽不出 → 一次小 LLM 调用；LLM 坏 JSON / 无 LLM → None
- 校验不过回落：值类型不符 / 保留名 rename → None
- routing 顺序：micro > skill_ops > DSL > LLM（两个分派点）
- metadata 标记：modify_path=skill_ops / skill=<name>
- 端到端 apply + 守护回滚回落
- benchmark 路径：仓库 skills/ 无模板 skill → 零拦截
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime import skill_ops
from openbrep.runtime.param_modify import ApplyOutcome
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.skills_loader import SkillsLoader


# ── 公共构造 ──────────────────────────────────────────────

def _make_project(tmp_path: Path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
        GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"),
        GDLParameter(name="show_frame", type_tag="Boolean", description="是否显示边框", value="0"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.root = tmp_path / name
    proj.save_to_disk()
    return proj


def _write_skill(skills_dir: Path, name: str, status: str = "verified", operations: str = "") -> Path:
    skills_dir.mkdir(parents=True, exist_ok=True)
    ops_line = f"operations: {operations}\n" if operations else ""
    path = skills_dir / f"{name}.md"
    path.write_text(
        f"---\nstatus: {status}\npattern_type: shelf_loop\n{ops_line}---\n\n"
        "# 书架策略\n\n## 触发关键词\n- 书架\n",
        encoding="utf-8",
    )
    return path


def _template(ops, match=None):
    data = {"match": match if match is not None else {"keywords": ["书架"]}, "ops": ops}
    return json.dumps(data, ensure_ascii=False)


def _make_pipeline(tmp_path: Path, skills_dir: Path, llm_content: str = "unused"):
    cfg = GDLAgentConfig()
    pipeline = TaskPipeline(config=cfg, trace_dir=str(tmp_path / "traces"))
    mock_llm = MagicMock()
    mock_llm.generate.return_value = LLMResponse(
        content=llm_content, model="mock", usage={}, finish_reason="stop"
    )
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    loader = SkillsLoader(str(skills_dir))
    loader.load()
    pipeline._skills_loader = loader
    return pipeline, mock_llm


def _modify_request(project, tmp_path, instruction):
    return TaskRequest(
        user_input=instruction, intent="MODIFY", project=project,
        work_dir=str(tmp_path), output_dir=str(tmp_path / "out"),
        gsm_name=project.name, agent_loop=False,
    )


# ── 1. operations 字段解析 ────────────────────────────────

class TestParseOperations(unittest.TestCase):
    def test_valid_template_parses(self):
        raw = _template([{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}])
        tpl = skill_ops.parse_operations(raw)
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["match"]["keywords"], ["书架"])
        self.assertEqual(tpl["ops"][0]["param"], "shelf_count")

    def test_bad_json_none(self):
        for raw in (None, "", "not json", "{broken", 42, [], [{"op": "x"}]):
            self.assertIsNone(skill_ops.parse_operations(raw), repr(raw))

    def test_missing_match_none(self):
        raw = json.dumps({"ops": [{"op": "set_value", "param": "shelf_count", "value": 5}]})
        self.assertIsNone(skill_ops.parse_operations(raw))

    def test_match_bad_keywords_none(self):
        for match in ({}, {"keywords": []}, {"keywords": "书架"}, {"keywords": [123]}):
            raw = _template([{"op": "set_value", "param": "a", "value": 1}], match=match)
            self.assertIsNone(skill_ops.parse_operations(raw), repr(match))

    def test_empty_or_unknown_ops_none(self):
        self.assertIsNone(skill_ops.parse_operations(_template([])))
        self.assertIsNone(
            skill_ops.parse_operations(_template([{"op": "patch_script"}])),
            "patch 模板本单不做 → 视为无模板",
        )

    def test_dict_input_accepted(self):
        tpl = skill_ops.parse_operations(json.loads(_template([{"op": "del_param", "param": "shelf_thk"}])))
        self.assertIsNotNone(tpl)


# ── 2. match 精度 ─────────────────────────────────────────

class TestMatchPrecision(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"

    def tearDown(self):
        self._td.cleanup()

    def test_hits_when_keyword_present(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        candidates = skill_ops._match_candidates("把书架层数改成 5", self.project, loader)
        self.assertEqual([c for c, _ in candidates], ["bookshelf"])

    def test_no_hit_without_keyword(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        self.assertEqual(skill_ops._match_candidates("把层数改成 5", self.project, loader), [])

    def test_declared_params_must_appear(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template(
                         [{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}],
                         match={"keywords": ["书架"], "params": ["shelf_thk"]},
                     ))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        # 书架命中但 shelf_thk 没出现在指令 → 不匹配
        self.assertEqual(skill_ops._match_candidates("把书架层数改成 5", self.project, loader), [])
        # shelf_thk 出现 → 匹配
        candidates = skill_ops._match_candidates("把书架 shelf_thk 改成 25mm", self.project, loader)
        self.assertEqual(len(candidates), 1)

    def test_proposed_status_not_injectable(self):
        _write_skill(self.skills_dir, "bookshelf", status="proposed",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": 5}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        self.assertEqual(skill_ops._match_candidates("把书架层数改成 5", self.project, loader), [])

    def test_param_not_in_project_excluded(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template([{"op": "set_value", "param": "ghost_param", "value": 5}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        self.assertEqual(skill_ops._match_candidates("把书架层数改成 5", self.project, loader), [])

    def test_missing_param_pruned_before_ambiguity(self):
        """参数不存在的候选在匹配阶段被 prune：只剩一个有效候选 → 不构成多命中歧义。"""
        _write_skill(self.skills_dir, "good_skill",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}]))
        _write_skill(self.skills_dir, "bad_skill",
                     operations=_template([{"op": "set_value", "param": "ghost_param", "value": "{{number}}"}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        candidates = skill_ops._match_candidates("把书架层数改成 5", self.project, loader)
        self.assertEqual([c for c, _ in candidates], ["good_skill"])

    def test_multi_hit_is_ambiguity_and_falls_back(self):
        _write_skill(self.skills_dir, "skill_a",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}]))
        _write_skill(self.skills_dir, "skill_b",
                     operations=_template([{"op": "set_value", "param": "shelf_thk", "value": "{{number}}"}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        result = skill_ops.try_skill_ops("把书架层数改成 5", self.project, loader, make_llm=None)
        self.assertIsNone(result)


# ── 3. 确定性填值 ────────────────────────────────────────

class TestDeterministicFill(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"
        self._llm_called = 0

    def tearDown(self):
        self._td.cleanup()

    def make_llm(self):
        def factory():
            self._llm_called += 1
            return None  # 确定性命中不该建 LLM
        return factory

    def hit(self, instruction, ops):
        _write_skill(self.skills_dir, "bookshelf", operations=_template(ops))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        return skill_ops.try_skill_ops(instruction, self.project, loader, make_llm=self.make_llm())

    def test_number_placeholder_integer(self):
        result = self.hit("把书架层数改成 5",
                          [{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}])
        self.assertIsNotNone(result)
        plan, name = result
        self.assertEqual(name, "bookshelf")
        self.assertEqual(plan.operations[0].op, "set_value")
        self.assertEqual(plan.operations[0].value, "5")
        self.assertEqual(self._llm_called, 0)

    def test_number_placeholder_length_unit_conversion(self):
        result = self.hit("把书架层板厚度改成 25mm",
                          [{"op": "set_value", "param": "shelf_thk", "value": "{{number}}"}])
        self.assertIsNotNone(result)
        self.assertEqual(result[0].operations[0].value, "0.025")
        self.assertEqual(self._llm_called, 0)

    def test_boolean_placeholder(self):
        result = self.hit("把书架 show_frame 改成关闭",
                          [{"op": "set_value", "param": "show_frame", "value": "{{boolean}}"}])
        self.assertIsNotNone(result)
        self.assertEqual(result[0].operations[0].value, "0")
        self.assertEqual(self._llm_called, 0)

    def test_concrete_value_preserved(self):
        result = self.hit("把书架层数改一下",
                          [{"op": "set_value", "param": "shelf_count", "value": 5}])
        self.assertIsNotNone(result)
        self.assertEqual(result[0].operations[0].value, "5")
        self.assertEqual(self._llm_called, 0)


# ── 4. LLM 填值回落 ──────────────────────────────────────

class TestLlmFillFallback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"

    def tearDown(self):
        self._td.cleanup()

    def hit(self, instruction, ops, llm):
        _write_skill(self.skills_dir, "bookshelf", operations=_template(ops))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        return skill_ops.try_skill_ops(instruction, self.project, loader, make_llm=lambda: llm)

    def test_llm_fills_when_deterministic_cannot(self):
        """Length 无单位大数值（micro 纪律：>10 不猜）→ 确定性抽不出 → LLM 填值。"""
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def generate(self, messages, **kwargs):
                self.calls += 1
                return LLMResponse(content='{"shelf_thk": 0.5}', model="mock", usage={}, finish_reason="stop")

        llm = FakeLLM()
        result = self.hit("把书架层板厚度改成 500",
                          [{"op": "set_value", "param": "shelf_thk", "value": "{{number}}"}], llm)
        self.assertIsNotNone(result)
        self.assertEqual(result[0].operations[0].value, "0.5")
        self.assertEqual(llm.calls, 1)  # 只一次小 LLM 调用

    def test_llm_bad_json_falls_back(self):
        class FakeLLM:
            def generate(self, messages, **kwargs):
                return LLMResponse(content="not json", model="mock", usage={}, finish_reason="stop")

        result = self.hit("把书架层板厚度改成 500",
                          [{"op": "set_value", "param": "shelf_thk", "value": "{{number}}"}], FakeLLM())
        self.assertIsNone(result)

    def test_no_llm_available_falls_back(self):
        result = self.hit("把书架层板厚度改成 500",
                          [{"op": "set_value", "param": "shelf_thk", "value": "{{number}}"}], None)
        self.assertIsNone(result)

    def test_unknown_placeholder_falls_back(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template([{"op": "set_value", "param": "shelf_count", "value": "{{foo}}"}]))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        result = skill_ops.try_skill_ops("把书架层数改成 5", self.project, loader, make_llm=None)
        self.assertIsNone(result)


# ── 5. 校验不过回落 ──────────────────────────────────────

class TestValidationFallback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"

    def tearDown(self):
        self._td.cleanup()

    def hit(self, ops):
        _write_skill(self.skills_dir, "bookshelf", operations=_template(ops))
        loader = SkillsLoader(str(self.skills_dir)); loader.load()
        return skill_ops.try_skill_ops("把书架层数改成 5", self.project, loader, make_llm=None)

    def test_type_mismatch_validation_falls_back(self):
        # 具体值 "abc" 不是 Integer → _validate_ops 拒绝
        self.assertIsNone(self.hit([{"op": "set_value", "param": "shelf_count", "value": "abc"}]))

    def test_reserved_rename_falls_back(self):
        self.assertIsNone(self.hit([{"op": "rename_param", "from": "A", "to": "width"}]))

    def test_del_referenced_param_falls_back(self):
        # shelf_count 被 3d 脚本引用 → del 拒绝（会留悬挂引用）
        self.project.scripts[ScriptType.SCRIPT_3D] = (
            "IF shelf_count > 2 THEN\nBLOCK A, B, ZZYZX\nENDIF\nEND\n"
        )
        self.assertIsNone(self.hit([{"op": "del_param", "param": "shelf_count"}]))


# ── 6. pipeline 接入：routing 顺序 / metadata / 端到端 ────

class TestPipelineSkillOps(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.project = _make_project(self.tmp)
        self.skills_dir = self.tmp / "skills"
        self._write_book_shelf()

    def tearDown(self):
        self._td.cleanup()

    def _write_book_shelf(self):
        _write_skill(self.skills_dir, "bookshelf",
                     operations=_template([
                         {"op": "set_value", "param": "shelf_count", "value": "{{number}}"},
                     ]))

    def _pipeline(self):
        return _make_pipeline(self.tmp, self.skills_dir)[0]

    def test_routing_micro_wins_over_skill_ops(self):
        pipeline = self._pipeline()
        with patch.object(pipeline, "_try_skill_ops", wraps=pipeline._try_skill_ops) as sk:
            result = pipeline.execute(_modify_request(self.project, self.tmp, "把 shelf_count 改成 5"))
        sk.assert_not_called()  # micro 已拦截，skill_ops 不再评估
        self.assertEqual(self.project.get_parameter("shelf_count").value, "5")
        self.assertIn("确定性微修改", result.plain_text)

    def test_routing_skill_ops_wins_over_dsl(self):
        pipeline = self._pipeline()
        with patch.object(pipeline, "_try_param_modify", wraps=pipeline._try_param_modify) as dsl:
            result = pipeline.execute(_modify_request(self.project, self.tmp, "把书架层数改成 5"))
        dsl.assert_not_called()
        self.assertEqual(self.project.get_parameter("shelf_count").value, "5")
        self.assertEqual(result.metadata["modify_path"], "skill_ops")
        self.assertEqual(result.metadata["skill"], "bookshelf")
        self.assertIn("skill 模板", result.plain_text)

    def test_routing_skill_ops_miss_falls_to_dsl(self):
        """skill_ops 未命中 → DSL 仍被评估（顺序不破）。"""
        from openbrep.llm import MockLLM

        dsl_json = json.dumps({"operations": [
            {"op": "set_value", "param": "shelf_count", "value": 5},
            {"op": "set_value", "param": "shelf_thk", "value": 0.025},
        ]})
        mock_llm = MockLLM(responses=[dsl_json])
        pipeline, _ = _make_pipeline(self.tmp, self.skills_dir)
        pipeline._make_llm = lambda _req: mock_llm
        with patch.object(pipeline, "_try_skill_ops", return_value=None) as sk:
            pipeline.execute(_modify_request(self.project, self.tmp, "把 shelf_count 改成 5，把 shelf_thk 改成 25mm"))
        sk.assert_called_once()
        self.assertEqual(self.project.get_parameter("shelf_count").value, "5")
        self.assertEqual(self.project.get_parameter("shelf_thk").value, "0.025")

    def test_routing_in_agent_loop_default_path(self):
        """agent_loop 默认（True）路径也插入 skill_ops：micro → skill_ops → DSL。"""
        pipeline = self._pipeline()
        request = _modify_request(self.project, self.tmp, "把书架层数改成 5")
        request.agent_loop = None  # 默认启用 agent loop
        with patch.object(pipeline, "_handle_modify_agent_loop", wraps=pipeline._handle_modify_agent_loop) as loop:
            result = pipeline.execute(request)
        loop.assert_not_called()  # skill_ops 在 agent loop 之前拦截
        self.assertEqual(result.metadata["modify_path"], "skill_ops")
        self.assertEqual(self.project.get_parameter("shelf_count").value, "5")

    def test_end_to_end_apply_persists_paramlist(self):
        pipeline = self._pipeline()
        result = pipeline.execute(_modify_request(self.project, self.tmp, "把书架层数改成 7"))
        self.assertTrue(result.success)
        self.assertIsNotNone(result.compile_result)
        self.assertEqual(self.project.get_parameter("shelf_count").value, "7")
        paramlist = (self.project.root / "paramlist.xml").read_text(encoding="utf-8")
        self.assertIn("<Value>7</Value>", paramlist)

    def test_guard_rollback_falls_back_to_none(self):
        """守护回滚（计划外文件变更）→ _try_skill_ops 返回 None 回落。"""
        pipeline = self._pipeline()
        with patch(
            "openbrep.runtime.param_modify.apply_param_modify",
            return_value=ApplyOutcome(applied=False),
        ):
            result = pipeline._try_skill_ops(
                _modify_request(self.project, self.tmp, "把书架层数改成 5")
            )
        self.assertIsNone(result)

    def test_repo_skills_zero_intercept(self):
        """benchmark 路径：仓库 skills/ 无 operations 模板 → _try_skill_ops 零拦截。"""
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(self.tmp / "traces"))
        result = pipeline._try_skill_ops(_modify_request(self.project, self.tmp, "把书架层数改成 5"))
        self.assertIsNone(result)  # 仓库 skills/ 无模板 → 不拦截，直接回落


if __name__ == "__main__":
    unittest.main()
