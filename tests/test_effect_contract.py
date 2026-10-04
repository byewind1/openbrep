"""P0-A 效果契约测试（2026-10-04 漏窗诊断 R1 修复）。

覆盖：
- compute_geometry_signature：等价源码改写稳定 / 真实顶点变化敏感 /
  mesh 重排不算变化 / 同数量不同形态可区分
- evaluate_effect_contract：geometry no_effect（r0015→r0016 场景）、
  material 不要求几何变化、parameter、new-option、无契约不判定、
  unverifiable 不谎报
- preview_geometry_summary：String 参数进入预览参数表（pattern_type=回纹），
  验收观察面与 preview_geometry 工具一致
- build_modify_acceptance：effect 判定进验收报告，no_effect 覆盖 delta 状态
- build_verification_report：effect no_effect 阻断 passed；unverifiable 不阻断
- working_intent：effect blocked → 任务 incomplete（不 completed、不 failed）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.runtime.effect_contract import (
    compute_geometry_signature,
    derive_change_kind,
    evaluate_effect_contract,
    mesh_components_detail,
    normalize_effect_contract,
)
from openbrep.runtime.modify_acceptance import (
    build_modify_acceptance,
    preview_geometry_summary,
)
from openbrep.verification import build_verification_report
from openbrep.workbench.working_intent import initial_intent, reduce_intent


class _FakeMesh:
    def __init__(self, x, y, z, i, j, k):
        self.x, self.y, self.z = x, y, z
        self.i, self.j, self.k = i, j, k


def _cube_mesh(ox=0.0, oy=0.0, size=1.0):
    """退化立方体：8 顶点 12 三角面，位置可平移、尺寸可调。"""
    s = size
    x = [ox, ox + s, ox, ox + s, ox, ox + s, ox, ox + s]
    y = [oy, oy, oy + s, oy + s, oy, oy, oy + s, oy + s]
    z = [0.0, 0.0, 0.0, 0.0, s, s, s, s]
    quads = [
        (0, 1, 3, 2),  # 底
        (4, 5, 7, 6),  # 顶
        (0, 1, 5, 4),  # 前
        (2, 3, 7, 6),  # 后
        (0, 2, 6, 4),  # 左
        (1, 3, 7, 5),  # 右
    ]
    i, j, k = [], [], []
    for a, b, c, d in quads:
        i += [a, a]
        j += [b, c]
        k += [c, d]
    return _FakeMesh(x, y, z, i, j, k)


class TestGeometrySignature(unittest.TestCase):
    def test_identical_geometry_same_signature(self):
        self.assertEqual(
            compute_geometry_signature([_cube_mesh()]),
            compute_geometry_signature([_cube_mesh()]),
        )

    def test_reordered_meshes_same_signature(self):
        a = compute_geometry_signature([_cube_mesh(0), _cube_mesh(5)])
        b = compute_geometry_signature([_cube_mesh(5), _cube_mesh(0)])
        self.assertEqual(a, b)

    def test_reordered_triangles_same_signature(self):
        m1 = _cube_mesh()
        m2 = _cube_mesh()
        # 反转三角面顶点顺序 + 打乱面顺序：同一几何，不同索引顺序
        m2.i = list(reversed(m1.i))
        m2.j = list(reversed(m1.j))
        m2.k = list(reversed(m1.k))
        self.assertEqual(compute_geometry_signature([m1]), compute_geometry_signature([m2]))

    def test_real_vertex_change_changes_signature(self):
        self.assertNotEqual(
            compute_geometry_signature([_cube_mesh()]),
            compute_geometry_signature([_cube_mesh(size=1.001)]),
        )

    def test_same_count_different_shape_distinguished(self):
        # mesh_count 相同（各 1 个）但形态不同：签名必须可区分——这是
        # mesh_count/bbox 判据做不到的（漏窗 r0015→r0016 事故核心）。
        flat = _FakeMesh([0, 1, 0], [0, 0, 1], [0, 0, 0], [0, 0], [1, 1], [2, 2])
        self.assertNotEqual(
            compute_geometry_signature([_cube_mesh()]),
            compute_geometry_signature([flat]),
        )

    def test_empty_geometry_returns_none(self):
        self.assertIsNone(compute_geometry_signature([]))
        self.assertIsNone(compute_geometry_signature(None))


class TestNormalizeEffectContract(unittest.TestCase):
    def test_none_and_invalid_return_none(self):
        self.assertIsNone(normalize_effect_contract(None))
        self.assertIsNone(normalize_effect_contract({}))
        self.assertIsNone(normalize_effect_contract({"change_kind": "vibes"}))
        self.assertIsNone(normalize_effect_contract("geometry"))
        self.assertIsNone(normalize_effect_contract({"change_kind": 123}))

    def test_valid_contract_normalized(self):
        contract = normalize_effect_contract({
            "change_kind": "geometry",
            "target_branch": "pattern_type=回纹",
            "reference_asset_ids": [" ref_1 "],
            "junk": "dropped",
        })
        self.assertEqual(contract["change_kind"], "geometry")
        self.assertEqual(contract["target_branch"], "pattern_type=回纹")
        self.assertEqual(contract["reference_asset_ids"], ["ref_1"])
        self.assertNotIn("junk", contract)


class TestEvaluateEffectContract(unittest.TestCase):
    def _summary(self, signature="sig_a", mesh_count=16, **overrides):
        data = {
            "available": True,
            "reason": "",
            "mesh_count": mesh_count,
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 0.4, 1.8]},
            "line_count": 0,
            "polygon_count": 0,
            "circle_count": 0,
            "arc_count": 0,
            "geometry_signature": signature,
        }
        data.update(overrides)
        return data

    def test_no_contract_returns_none(self):
        self.assertIsNone(evaluate_effect_contract(None, before={}, after={}))

    def test_geometry_no_effect_on_identical_signature(self):
        """漏窗事故场景：改注释/等价算式 → 源码变了、几何签名不变 → no_effect。"""
        effect = evaluate_effect_contract(
            {"change_kind": "geometry"},
            before=self._summary(signature="4a8bb9372c82"),
            after=self._summary(signature="4a8bb9372c82"),
            changed_files=["scripts/3d.gdl"],
        )
        self.assertEqual(effect["status"], "no_effect")
        self.assertFalse(effect["satisfied"])
        self.assertIn("几何签名相同", effect["reason"])

    def test_geometry_satisfied_on_signature_change(self):
        effect = evaluate_effect_contract(
            {"change_kind": "geometry"},
            before=self._summary(signature="aaa"),
            after=self._summary(signature="bbb"),
        )
        self.assertEqual(effect["status"], "satisfied")
        self.assertTrue(effect["satisfied"])

    def test_geometry_unverifiable_without_signature(self):
        """签名不可用时不得用 mesh_count 冒充判定（同数量不同形态不可见）。"""
        effect = evaluate_effect_contract(
            {"change_kind": "geometry"},
            before=self._summary(signature=None, mesh_count=8),
            after=self._summary(signature=None, mesh_count=8),
        )
        self.assertEqual(effect["status"], "unverifiable")
        self.assertFalse(effect["satisfied"])

    def test_geometry_unverifiable_when_preview_unavailable(self):
        effect = evaluate_effect_contract(
            {"change_kind": "geometry"},
            before=self._summary(available=False),
            after=self._summary(),
        )
        self.assertEqual(effect["status"], "unverifiable")

    def test_material_does_not_require_geometry_change(self):
        """材质任务不被几何门误拦：材质映射变化即满足，几何 hash 不变没关系。"""
        effect = evaluate_effect_contract(
            {"change_kind": "material"},
            before=self._summary(signature="same", materials=["wood"]),
            after=self._summary(signature="same", materials=["metal"]),
        )
        self.assertEqual(effect["status"], "satisfied")

    def test_material_no_effect_when_unchanged(self):
        effect = evaluate_effect_contract(
            {"change_kind": "material"},
            before=self._summary(materials=["wood"]),
            after=self._summary(materials=["wood"]),
        )
        self.assertEqual(effect["status"], "no_effect")

    def test_parameter_satisfied_on_changes(self):
        effect = evaluate_effect_contract(
            {"change_kind": "parameter"},
            before=self._summary(),
            after=self._summary(),
            parameter_changes=[{"name": "A", "from": "1", "to": "2"}],
        )
        self.assertEqual(effect["status"], "satisfied")

    def test_parameter_no_effect_without_changes(self):
        effect = evaluate_effect_contract(
            {"change_kind": "parameter"},
            before=self._summary(),
            after=self._summary(),
            parameter_changes=[],
        )
        self.assertEqual(effect["status"], "no_effect")

    def test_new_option_satisfied_by_param_definition_files(self):
        """新增选项不强制改变当前参数下的几何；定义面落盘即达成。"""
        effect = evaluate_effect_contract(
            {"change_kind": "new-option"},
            before=self._summary(signature="same"),
            after=self._summary(signature="same"),
            changed_files=["scripts/vl.gdl"],
        )
        self.assertEqual(effect["status"], "satisfied")
        self.assertIn("预期行为", effect["reason"])

    def test_new_option_no_effect_without_definition_change(self):
        effect = evaluate_effect_contract(
            {"change_kind": "new-option"},
            before=self._summary(),
            after=self._summary(),
            changed_files=["scripts/3d.gdl"],
        )
        self.assertEqual(effect["status"], "no_effect")


class TestPreviewGeometrySummaryStringParams(unittest.TestCase):
    def test_string_parameter_reaches_preview(self):
        """P0-A R1 核心回归：String 参数（pattern_type="回纹"）必须进入预览
        参数表——此前 to_preview_number 把它丢掉，验收摘要观察不到回纹分支。"""
        proj = HSFProject.create_new("Lattice", work_dir="./workdir")
        proj.parameters.append(GDLParameter(name="pattern_type", type_tag="String", description="纹样", value="回纹"))
        proj.parameters.append(GDLParameter(name="shelf_count", type_tag="Integer", description="层数", value="2"))
        proj.scripts[ScriptType.SCRIPT_3D] = (
            'IF pattern_type = "回纹" THEN\n'
            "  BLOCK 0.1, 0.1, 0.1\n"
            "  ADDX 0.2\n"
            "  BLOCK 0.1, 0.1, 0.1\n"
            "ELSE\n"
            "  BLOCK A, B, ZZYZX\n"
            "ENDIF\n"
        )
        summary = preview_geometry_summary(proj)
        self.assertTrue(summary["available"])
        self.assertGreaterEqual(summary["mesh_count"], 2)
        self.assertIsNotNone(summary["geometry_signature"])

    def test_overrides_change_observed_geometry_without_writes(self):
        proj = HSFProject.create_new("Lattice", work_dir="./workdir")
        proj.parameters.append(GDLParameter(name="pattern_type", type_tag="String", description="纹样", value="直棂"))
        proj.scripts[ScriptType.SCRIPT_3D] = (
            'IF pattern_type = "回纹" THEN\n'
            "  BLOCK 0.1, 0.1, 0.1\n"
            "ELSE\n"
            "  BLOCK A, B, ZZYZX\n"
            "ENDIF\n"
        )
        base = preview_geometry_summary(proj)
        probed = preview_geometry_summary(proj, overrides={"pattern_type": "回纹"})
        self.assertNotEqual(base["geometry_signature"], probed["geometry_signature"])


class TestAcceptanceAndVerificationGate(unittest.TestCase):
    def _summary(self, signature, mesh_count=16):
        return {
            "available": True,
            "reason": "",
            "mesh_count": mesh_count,
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 0.4, 1.8]},
            "line_count": 0,
            "polygon_count": 0,
            "circle_count": 0,
            "arc_count": 0,
            "geometry_signature": signature,
        }

    class _CompileOk:
        success = True

    def test_acceptance_carries_effect_and_marks_no_effect(self):
        acc = build_modify_acceptance(
            before=self._summary("same"),
            after=self._summary("same"),
            changed_files=["scripts/3d.gdl"],
            compile_result=self._CompileOk(),
            semantic_issues=[],
            effect_contract={"change_kind": "geometry"},
        )
        effect = acc["effect"]
        self.assertEqual(effect["status"], "no_effect")
        self.assertEqual(acc["geometry_delta"]["status"], "no_effect")
        self.assertTrue(any("目标效果（geometry）" in ln for ln in acc["summary_lines"]))

    def test_no_contract_keeps_legacy_acceptance_shape(self):
        acc = build_modify_acceptance(
            before=self._summary("same"),
            after=self._summary("same"),
            changed_files=["scripts/3d.gdl"],
            compile_result=self._CompileOk(),
            semantic_issues=[],
        )
        self.assertNotIn("effect", acc)

    def test_verification_blocks_on_no_effect(self):
        report = build_verification_report(
            intent="MODIFY",
            effect_result={"required": True, "change_kind": "geometry", "satisfied": False,
                           "status": "no_effect", "reason": "几何签名相同"},
        )
        self.assertFalse(report.passed)
        self.assertTrue(any(c["check_type"] == "effect_contract" and c["status"] == "fail"
                            for c in report.to_dict()["checks"]))
        self.assertIn("目标效果", report.to_summary_text())

    def test_verification_unverified_effect_does_not_block(self):
        report = build_verification_report(
            intent="MODIFY",
            effect_result={"required": True, "change_kind": "geometry", "satisfied": False,
                           "status": "unverifiable", "reason": "几何签名不可用"},
        )
        self.assertTrue(report.passed)
        self.assertTrue(any("effect_unverified" in w for w in report.warnings_caught))

    def test_verification_satisfied_effect_passes(self):
        report = build_verification_report(
            intent="MODIFY",
            effect_result={"required": True, "change_kind": "geometry", "satisfied": True,
                           "status": "satisfied", "reason": "几何发生实质变化"},
        )
        self.assertTrue(report.passed)

    def test_verification_without_effect_result_unchanged(self):
        report = build_verification_report(intent="MODIFY")
        self.assertTrue(report.passed)
        self.assertFalse(any(c.check_type == "effect_contract" for c in report.checks))


class TestWorkingIntentEffectGate(unittest.TestCase):
    def _state_with_task(self):
        state = initial_intent("s1", 1)
        return reduce_intent(state, {"kind": "start_task", "task_id": "t1", "goal": "按图修改回纹", "task_intent": "MODIFY"})

    def _result(self, *, passed, effect=None, ok=True):
        assistant = {
            "delivery_source": {"run_id": "r1"},
            "run_id": "r1",
            "changed_files": ["scripts/3d.gdl"],
            "verification": {"passed": passed},
        }
        if effect is not None:
            assistant["acceptance"] = {"effect": effect}
        return {"ok": ok, "assistant": assistant}

    def test_no_effect_keeps_task_incomplete(self):
        """r0015→r0016 场景：文件改了、编译绿了，但形态目标未达成 → 任务不得 completed。"""
        state = self._state_with_task()
        state = reduce_intent(state, {"kind": "result", "task_id": "t1", "result": self._result(
            passed=False,
            effect={"required": True, "change_kind": "geometry", "satisfied": False, "status": "no_effect", "reason": "签名相同"},
        )})
        task = state["tasks"][0]
        self.assertEqual(task["state"], "incomplete")

    def test_satisfied_effect_completes_task(self):
        state = self._state_with_task()
        state = reduce_intent(state, {"kind": "result", "task_id": "t1", "result": self._result(
            passed=True,
            effect={"required": True, "change_kind": "geometry", "satisfied": True, "status": "satisfied", "reason": "变化"},
        )})
        self.assertEqual(state["tasks"][0]["state"], "completed")

    def test_no_contract_legacy_semantics_unchanged(self):
        state = self._state_with_task()
        state = reduce_intent(state, {"kind": "result", "task_id": "t1", "result": self._result(passed=True)})
        self.assertEqual(state["tasks"][0]["state"], "completed")
        state2 = self._state_with_task()
        state2 = reduce_intent(state2, {"kind": "result", "task_id": "t1", "result": self._result(passed=False)})
        self.assertEqual(state2["tasks"][0]["state"], "failed")


if __name__ == "__main__":
    unittest.main()


class TestConnectivityReliability(unittest.TestCase):
    """F6（review 2026-10-04）：连通判定不得把交叠实体误判为断开，
    非轴对齐盒网格必须 unverifiable 而非阻断。"""

    class _Mesh:
        def __init__(self, x, y, z, i, j, k):
            self.x, self.y, self.z = x, y, z
            self.i, self.j, self.k = i, j, k

    @staticmethod
    def _box(ox, oy, sx, sy, sz):
        x = [ox, ox + sx, ox, ox + sx, ox, ox + sx, ox, ox + sx]
        y = [oy, oy, oy + sy, oy + sy, oy, oy, oy + sy, oy + sy]
        z = [0.0, 0.0, 0.0, 0.0, sz, sz, sz, sz]
        quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
        i, j, k = [], [], []
        for a, b, c, d in quads:
            i += [a, a]
            j += [b, c]
            k += [c, d]
        return TestConnectivityReliability._Mesh(x, y, z, i, j, k)

    def _summary(self, signature, components=None, reliable=False):
        return {
            "available": True, "reason": "", "mesh_count": 2,
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [2.0, 2.0, 1.0]},
            "line_count": 0, "polygon_count": 0, "circle_count": 0, "arc_count": 0,
            "geometry_signature": signature,
            "mesh_components": components, "mesh_components_reliable": reliable,
        }

    def test_overlapping_blocks_are_one_component(self):
        """review F6 探针：BLOCK(2,1,1) + 平移(0.5,0.5,0) 的 BLOCK(1,2,1)
        正体积交叠、无重合角点 → 必须判 1 个组件（顶点共享会误判 2）。"""
        a = self._box(0.0, 0.0, 2.0, 1.0, 1.0)
        b = self._box(0.5, 0.5, 1.0, 2.0, 1.0)
        components, reliable = mesh_components_detail([a, b])
        self.assertEqual(components, 1)
        self.assertTrue(reliable)

    def test_disconnected_blocks_stay_two_components(self):
        a = self._box(0.0, 0.0, 2.0, 1.0, 1.0)
        c = self._box(5.0, 0.0, 1.0, 1.0, 1.0)
        components, reliable = mesh_components_detail([a, c])
        self.assertEqual(components, 2)
        self.assertTrue(reliable)

    def test_unreliable_mesh_verdicts_unverifiable(self):
        tri = self._Mesh([0, 1, 0], [0, 0, 1], [0, 0, 0], [0, 0], [1, 1], [2, 2])
        components, reliable = mesh_components_detail([tri])
        self.assertFalse(reliable)
        effect = evaluate_effect_contract(
            {"change_kind": "geometry", "require_connected": True},
            before=self._summary("a"),
            after=self._summary("b", components=1, reliable=False),
        )
        self.assertEqual(effect["status"], "unverifiable")
        self.assertFalse(effect["satisfied"])

    def test_connected_overlap_satisfies_require_connected(self):
        a = self._box(0.0, 0.0, 2.0, 1.0, 1.0)
        b = self._box(0.5, 0.5, 1.0, 2.0, 1.0)
        components, reliable = mesh_components_detail([a, b])
        effect = evaluate_effect_contract(
            {"change_kind": "geometry", "require_connected": True},
            before=self._summary("a"),
            after=self._summary("b", components=components, reliable=reliable),
        )
        self.assertEqual(effect["status"], "satisfied")

    def test_disconnected_overlap_fails_require_connected(self):
        a = self._box(0.0, 0.0, 2.0, 1.0, 1.0)
        c = self._box(5.0, 0.0, 1.0, 1.0, 1.0)
        components, reliable = mesh_components_detail([a, c])
        effect = evaluate_effect_contract(
            {"change_kind": "geometry", "require_connected": True},
            before=self._summary("a"),
            after=self._summary("b", components=components, reliable=reliable),
        )
        self.assertEqual(effect["status"], "shape_check_failed")


class TestDeriveChangeKind(unittest.TestCase):
    """R2（二轮 review）：change_kind 必须来自任务意图，不来自"有没有图片"。

    "按图把材质改为金属，形状不变"是 material 任务——GUI 旧逻辑强加 geometry
    门会把合法交付误判为 no_effect。推导不出明确意图 → None（不设门）。
    """

    def test_material_intent_wins_over_shape_words(self):
        self.assertEqual(derive_change_kind("按图把材质改为金属，形状不变"), "material")
        self.assertEqual(derive_change_kind("换成金属材质并保持形状"), "material")

    def test_new_option_intent(self):
        self.assertEqual(derive_change_kind("新增一个回纹样式选项"), "new-option")
        self.assertEqual(derive_change_kind("加个六角形选项"), "new-option")

    def test_geometry_intent(self):
        self.assertEqual(derive_change_kind("把回纹改成连续方折"), "geometry")
        self.assertEqual(derive_change_kind("优化一下脚本结构"), "geometry")

    def test_parameter_intent(self):
        self.assertEqual(derive_change_kind("把 shelf_count 改成 5"), "parameter")
        self.assertEqual(derive_change_kind("把宽度改成2"), "parameter")
        self.assertEqual(derive_change_kind("检查一下参数面板"), "parameter")

    def test_ambiguous_messages_get_no_contract(self):
        for text in ("你好", "继续", "按这张图调整这个构件", "帮我看看这个构件", ""):
            self.assertIsNone(derive_change_kind(text), text)

    def test_negated_objects_are_not_goals(self):
        """S1（三轮 review）：否定/保持约束中的对象不是变化目标。"""
        self.assertEqual(derive_change_kind("保持材质不变，把回纹改成连续方折"), "geometry")
        self.assertEqual(derive_change_kind("不要改材质，把回纹改成方折"), "geometry")

    def test_refactor_intent_gets_no_contract(self):
        """S1：重构类任务正确性不依赖几何签名变化——返回 None 不设门。"""
        self.assertIsNone(derive_change_kind("只重构参数代码，参数值和形状不变"))
        self.assertIsNone(derive_change_kind("重构一下这段 GDL 代码"))

    def test_compound_intents_get_no_contract(self):
        """S1：肯定子句命中多个 kind（复合意图）→ None，不猜主目标。"""
        self.assertIsNone(derive_change_kind("把材质改成金属并把宽度改成2"))

    def test_continuation_inherits_unique_prior_goal(self):
        """S2（三轮 review）：续接语句继承前文唯一明确目标；歧义或无目标不继承。"""
        self.assertEqual(
            derive_change_kind("按你的建议进行修改", history_texts=("你能不能搜个回纹的图片参考一下？",)),
            "geometry",
        )
        self.assertEqual(
            derive_change_kind("按这张图修改", history_texts=("漏窗回字纹按这个样式",)),
            "geometry",
        )
        # 前文只有观察类/无命中 → 不继承
        self.assertIsNone(
            derive_change_kind("按你的建议进行修改", history_texts=("检查一下参数", "看看结构")),
        )
        # 当轮有明确 kind 时不回看前文
        self.assertEqual(derive_change_kind("把宽度改成2", history_texts=("回纹",)), "parameter")
        # 非续接语句不回看前文（防误挂历史目标）
        self.assertIsNone(derive_change_kind("优化一下脚本", history_texts=("回纹",)))

    def test_material_task_with_gui_geometry_gate_would_fail_but_correct_kind_passes(self):
        """R2 探针回归：材质变化、几何签名相同——material 契约 satisfied，
        geometry 契约 no_effect（这正是旧 GUI 强加 geometry 的误拦场景）。"""
        before = {"available": True, "mesh_count": 2, "materials": ["wood"],
                  "geometry_signature": "same", "line_count": 0, "polygon_count": 0,
                  "circle_count": 0, "arc_count": 0}
        after = {**before, "materials": ["metal"]}
        material = evaluate_effect_contract({"change_kind": "material"}, before=before, after=after)
        self.assertEqual(material["status"], "satisfied")
        geometry = evaluate_effect_contract({"change_kind": "geometry"}, before=before, after=after)
        self.assertEqual(geometry["status"], "no_effect")
