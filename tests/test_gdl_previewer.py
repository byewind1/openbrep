import unittest
from pathlib import Path

from openbrep import gdl_previewer
from openbrep.gdl_parser import parse_gdl_source_with_warnings
from openbrep.gdl_previewer import preview_2d_script, preview_3d_script, preview_scripts
from openbrep.workbench.project_parameter_service import parameter_values


class TestGDLPreviewerPhase1(unittest.TestCase):
    def test_unknown_command_policy_warn_ignore_error(self):
        script = "BLOCK 1,1,1\nFOO_CMD 1\n"

        res_warn = preview_3d_script(script, unknown_command_policy="warn")
        self.assertTrue(any("未支持命令 FOO_CMD" in w for w in res_warn.warnings))

        res_ignore = preview_3d_script(script, unknown_command_policy="ignore")
        self.assertFalse(any("未支持命令 FOO_CMD" in w for w in res_ignore.warnings))

        with self.assertRaises(ValueError):
            preview_3d_script(script, unknown_command_policy="error")

    def test_warning_includes_line_and_command_structured(self):
        script = "BLOCK 1,1,1\nFOO_CMD 1\n"
        res = preview_3d_script(script, unknown_command_policy="warn")

        self.assertTrue(any(w.startswith("line 2:") for w in res.warnings))
        self.assertTrue(res.warnings_structured)
        item = res.warnings_structured[-1]
        self.assertEqual(item.line, 2)
        self.assertEqual(item.command, "FOO_CMD")
        self.assertEqual(item.code, "UNKNOWN_COMMAND")

    def test_quality_fast_vs_accurate_density(self):
        script = "SPHERE 1\n"
        fast = preview_3d_script(script, quality="fast")
        accurate = preview_3d_script(script, quality="accurate")

        self.assertEqual(len(fast.meshes), 1)
        self.assertEqual(len(accurate.meshes), 1)
        self.assertGreater(len(accurate.meshes[0].x), len(fast.meshes[0].x))
        self.assertGreater(len(accurate.meshes[0].i), len(fast.meshes[0].i))

    def test_transform_rot_mul_commands(self):
        script = """\
MULX 2
ROTZ 90
BLOCK 1, 1, 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(min(mesh.x), -1.0, places=6)
        self.assertAlmostEqual(max(mesh.x), 0.0, places=6)
        self.assertAlmostEqual(min(mesh.y), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 2.0, places=6)
        self.assertFalse(any("未支持命令 ROT" in w for w in res.warnings))

    def test_quality_profile_baseline_values(self):
        fast = gdl_previewer._quality_profile("fast")
        accurate = gdl_previewer._quality_profile("accurate")

        self.assertEqual(fast["frustum_seg"], 24)
        self.assertEqual(fast["sphere_steps"], (10, 20))
        self.assertEqual(accurate["frustum_seg"], 48)
        self.assertEqual(accurate["sphere_steps"], (20, 40))

    def test_unknown_quality_falls_back_to_fast_profile(self):
        script = "CYLIND 2, 1\n"
        fast = preview_3d_script(script, quality="fast")
        bad = preview_3d_script(script, quality="unexpected")

        self.assertEqual(len(fast.meshes[0].x), len(bad.meshes[0].x))
        self.assertEqual(len(fast.meshes[0].i), len(bad.meshes[0].i))

    def test_mesh_source_ref_tracks_command_and_line(self):
        script = """\
! comment
ADDZ 1
BLOCK 1, 2, 3
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        ref = res.meshes[0].source_ref
        self.assertIsNotNone(ref)
        self.assertEqual(ref.script_type, "3d")
        self.assertEqual(ref.line, 3)
        self.assertEqual(ref.command, "BLOCK")
        self.assertEqual(ref.label, "3D line 3 BLOCK")

    def test_basic_3d_mesh_commands_include_source_ref(self):
        script = """\
CYLIND 1, 0.5
SPHERE 0.25
PRISM 3, 1, 0,0, 1,0, 0,1
PRISM_ 3, 1, 0,0, 1,0, 0,1
"""
        res = preview_3d_script(script)

        self.assertEqual([m.source_ref.command for m in res.meshes], ["CYLIND", "SPHERE", "PRISM", "PRISM_"])
        self.assertEqual([m.source_ref.line for m in res.meshes], [1, 2, 3, 4])

    def test_if_block_executes_true_branch_and_skips_false_branch(self):
        script = """\
IF has_back_panel = 1 THEN
    BLOCK 1, 1, 1
ENDIF
IF has_back_panel = 0 THEN
    BLOCK 9, 9, 9
ENDIF
"""
        res = preview_3d_script(script, {"has_back_panel": 1})

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].source_ref.line, 2)
        self.assertFalse(any("IF 条件解析失败" in w for w in res.warnings))

    def test_setup_script_supports_bookshelf_derived_variables(self):
        script = """\
TOLER 0.001
MATERIAL mat_frame
BLOCK frame_thk, B, ZZYZX
ADDX A - frame_thk
BLOCK frame_thk, B, ZZYZX
DEL 1
MATERIAL mat_shelf
ADDX frame_thk
BLOCK _inner_w, B, shelf_thickness
DEL 1
ADDX frame_thk
ADDZ ZZYZX - shelf_thickness
BLOCK _inner_w, B, shelf_thickness
DEL 2
FOR i = 1 TO shelf_count - 2
    _z = shelf_thickness + i * _shelf_gap
    ADDX frame_thk
    ADDZ _z
    BLOCK _inner_w, B, shelf_thickness
    DEL 2
NEXT i
IF has_back_panel = 1 THEN
    MATERIAL mat_frame
    ADDY B - back_thk
    BLOCK A, back_thk, ZZYZX
    DEL 1
ENDIF
END
"""
        setup = """\
_inner_w = A - 2 * frame_thk
_shelf_gap = (ZZYZX - shelf_thickness * shelf_count) / (shelf_count - 1)
"""
        params = {
            "A": 2,
            "B": 0.4,
            "ZZYZX": 2,
            "frame_thk": 0.05,
            "shelf_thickness": 0.04,
            "shelf_count": 5,
            "has_back_panel": 1,
            "back_thk": 0.02,
        }

        res = preview_3d_script(script, params, setup_script=setup)

        self.assertEqual(len(res.meshes), 8)
        self.assertEqual([m.source_ref.line for m in res.meshes], [3, 5, 9, 13, 19, 19, 19, 25])
        self.assertFalse(any("未定义变量 _inner_w" in w for w in res.warnings))
        self.assertFalse(any("未支持命令 TOLER" in w for w in res.warnings))
        self.assertFalse(any("mat_frame" in w or "mat_shelf" in w for w in res.warnings))

    def test_prism_status_triplets_use_gdl_x_y_status_order(self):
        script = """\
PRISM_ 3, 0.04,
    1 * COS(0), 1 * SIN(0), 15,
    1 * COS(30), 1 * SIN(30), 15,
    0.1 * COS(0), 0.1 * SIN(0), 15
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(max(mesh.x), 1.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 0.5, places=6)
        self.assertLess(max(abs(y) for y in mesh.y), 1.0)

    def test_spiral_stair_fan_steps_preview(self):
        script = """\
TOLER 0.001
RESOL 36

IF r_outer <= 0 THEN r_outer = 0.8
IF r_inner <= 0 THEN r_inner = 0.06
IF r_outer <= r_inner THEN r_outer = r_inner + 0.5
IF h_total <= 0 THEN h_total = 2.90
IF n_step < 2 THEN n_step = 16
IF step_thk <= 0 THEN step_thk = 0.04
IF ang_total <= 0 THEN ang_total = 180
IF col_h <= 0 THEN col_h = h_total

_stepH = h_total / n_step
_stepAng = ang_total / n_step

IF mat_col > 0 THEN
    MATERIAL mat_col
ELSE
    MATERIAL SYMB_MAT
ENDIF

CYLIND col_h, r_inner

IF mat_step > 0 THEN
    MATERIAL mat_step
ELSE
    MATERIAL SYMB_MAT
ENDIF

FOR i = 0 TO n_step - 1
    _z = i * _stepH
    _baseAng = i * _stepAng

    a0 = 0
    a1 = _stepAng * 1 / 6
    a2 = _stepAng * 2 / 6
    a3 = _stepAng * 3 / 6
    a4 = _stepAng * 4 / 6
    a5 = _stepAng * 5 / 6
    a6 = _stepAng

    ADDZ _z
    ROTZ _baseAng

    PRISM_ 14, step_thk,
        r_outer * COS(a0), r_outer * SIN(a0), 15,
        r_outer * COS(a1), r_outer * SIN(a1), 15,
        r_outer * COS(a2), r_outer * SIN(a2), 15,
        r_outer * COS(a3), r_outer * SIN(a3), 15,
        r_outer * COS(a4), r_outer * SIN(a4), 15,
        r_outer * COS(a5), r_outer * SIN(a5), 15,
        r_outer * COS(a6), r_outer * SIN(a6), 15,
        r_inner * COS(a6), r_inner * SIN(a6), 15,
        r_inner * COS(a5), r_inner * SIN(a5), 15,
        r_inner * COS(a4), r_inner * SIN(a4), 15,
        r_inner * COS(a3), r_inner * SIN(a3), 15,
        r_inner * COS(a2), r_inner * SIN(a2), 15,
        r_inner * COS(a1), r_inner * SIN(a1), 15,
        r_inner * COS(a0), r_inner * SIN(a0), 15

    DEL 2
NEXT i

ADDZ h_total
ROTZ ang_total
ADD r_inner, -0.04, 0
BLOCK r_outer * 0.95, 0.08, step_thk
DEL 3
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 18)
        self.assertEqual([mesh.source_ref.command for mesh in res.meshes[:2]], ["CYLIND", "PRISM_"])
        self.assertFalse(any("IF 缺少匹配 ENDIF" in w for w in res.warnings))
        self.assertFalse(any("未定义变量" in w for w in res.warnings))
        self.assertLess(max(max(abs(x) for x in mesh.x) for mesh in res.meshes), 1.0)
        self.assertLess(max(max(abs(y) for y in mesh.y) for mesh in res.meshes), 1.0)


class TestP9ConditionEvaluation(unittest.TestCase):
    """P9：预览器条件求值三缺口（字符串比较 / NOT / 单行 IF 深度计数）。

    事故背景：漏窗真机项目（output/window_decorative_lattice_v2/）的合法 GDL
    在 Archicad 可运行，但预览器窗芯全缺（纹样 mesh 恒 8 = 只外框+内框）：
    1. `IF pattern_type = "直棂" THEN GOSUB ...` 字符串比较不支持 → 分发全跳过；
    2. `IF NOT show_in_3d THEN END` 前级 NOT 不支持 → 解析失败；
    3. 单行 IF 被 _find_matching_if_bounds/_find_matching_endif 计入深度，
       块 IF 误报 "IF 缺少匹配 ENDIF"，且匹配失败后块体无条件穿透执行。
    """

    def test_string_pattern_dispatch_gosub(self):
        """事故形状：字符串分发（单行 IF + GOSUB）。pattern_type 命中时
        窗芯 mesh 出现，不命中时不出现。"""
        script = """GOSUB "DrawFrame"
IF pattern_type = "直棂" THEN GOSUB "CoreZhileng"
IF pattern_type = "井字" THEN GOSUB "CoreJingzi"
END
"DrawFrame":
    BLOCK 1, 1, 1
RETURN
"CoreZhileng":
    BLOCK 0.5, 0.5, 0.5
RETURN
"CoreJingzi":
    BLOCK 0.25, 0.25, 0.25
RETURN
"""
        res_zhileng = preview_3d_script(script, {"pattern_type": "直棂"})
        self.assertEqual(len(res_zhileng.meshes), 2)
        res_jingzi = preview_3d_script(script, {"pattern_type": "井字"})
        self.assertEqual(len(res_jingzi.meshes), 2)
        res_other = preview_3d_script(script, {"pattern_type": "菱花"})
        self.assertEqual(len(res_other.meshes), 1)  # 只外框
        for res in (res_zhileng, res_jingzi, res_other):
            self.assertFalse(any("条件解析失败" in w for w in res.warnings))

    def test_string_inequality_operators(self):
        """字符串不等号：`<>` 与 `#` 都按字符串不相等处理。"""
        script = """IF pattern_type <> "井字" THEN
    BLOCK 1, 1, 1
ENDIF
IF pattern_type # "直棂" THEN
    BLOCK 0.5, 0.5, 0.5
ENDIF
"""
        res = preview_3d_script(script, {"pattern_type": "直棂"})
        self.assertEqual(len(res.meshes), 1)  # <> 为真；# 为假
        res2 = preview_3d_script(script, {"pattern_type": "井字"})
        self.assertEqual(len(res2.meshes), 1)  # <> 为假；# 为真
        res3 = preview_3d_script(script, {"pattern_type": "菱花"})
        self.assertEqual(len(res3.meshes), 2)  # 两者都为真

    def test_not_prefix_condition(self):
        """前级 NOT：flag=1 时 IF NOT flag 不执行；flag=0 时执行。"""
        script = """IF NOT show_flag THEN
    BLOCK 9, 9, 9
ENDIF
IF show_flag THEN
    BLOCK 1, 1, 1
ENDIF
"""
        res_on = preview_3d_script(script, {"show_flag": 1})
        self.assertEqual(len(res_on.meshes), 1)  # 只走 show_flag 分支
        self.assertEqual(res_on.meshes[0].source_ref.line, 5)
        res_off = preview_3d_script(script, {"show_flag": 0})
        self.assertEqual(len(res_off.meshes), 1)  # 只走 NOT 分支
        self.assertEqual(res_off.meshes[0].source_ref.line, 2)

    def test_not_incident_guard_parses(self):
        """事故形状 `IF NOT show_in_3d THEN END`：show=1 时守卫不触发，
        不再报条件解析失败。"""
        script = "IF NOT show_in_3d THEN END\nBLOCK 1, 1, 1\n"
        res = preview_3d_script(script, {"show_in_3d": 1})
        self.assertEqual(len(res.meshes), 1)
        self.assertFalse(any("条件解析失败" in w for w in res.warnings))

    def test_inline_if_inside_block_if(self):
        """漏窗 master 形状：块 IF 内含两条单行 IF + ENDIF。零"缺少匹配
        ENDIF"警告；条件为假时块体不执行（盯死无条件穿透不再误触）。"""
        script = """IF _inner_opening_w > bar_width THEN
    _n = INT((_inner_opening_w + gap) / (bar_width + gap))
    IF _n < 1 THEN _n = 1
    IF _n = 1 THEN _gap = 0
    BLOCK _n, 1, 1
ENDIF
"""
        res = preview_3d_script(script, {"_inner_opening_w": 0.66, "bar_width": 0.03, "gap": 0.1})
        self.assertEqual(len(res.meshes), 1)
        self.assertFalse(any("缺少匹配 ENDIF" in w for w in res.warnings))
        self.assertFalse(any("游离 ENDIF" in w for w in res.warnings))

        res_false = preview_3d_script(script, {"_inner_opening_w": 0.01, "bar_width": 0.03, "gap": 0.1})
        self.assertEqual(len(res_false.meshes), 0)  # 条件为假：块体不执行
        self.assertFalse(any("缺少匹配 ENDIF" in w for w in res_false.warnings))

    def test_numeric_condition_shapes_regression(self):
        """数值条件全形态回归：比较 / AND / OR 行为不变。"""
        script = """IF A >= 0.9 AND B <= 1.0 THEN
    BLOCK A, B, ZZYZX
ENDIF
IF A < 0.1 OR has_back = 1 THEN
    BLOCK 0.1, 0.1, 0.1
ENDIF
"""
        res = preview_3d_script(script, {"A": 0.9, "B": 0.9, "ZZYZX": 0.1, "has_back": 1})
        self.assertEqual(len(res.meshes), 2)
        res2 = preview_3d_script(script, {"A": 0.9, "B": 0.9, "ZZYZX": 0.1, "has_back": 0})
        self.assertEqual(len(res2.meshes), 1)


class TestP10ForGosubCrossScopeAndColonStatements(unittest.TestCase):
    """P10：FOR 体内 GOSUB 跨作用域 + 单行 IF 内 `:` 多语句。

    事故背景：漏窗菱花（window_decorative_lattice_v2，pattern_type="菱花"）
    P9 修复后仍 8 mesh + 798 警告（399×"DEL 1 超过栈深" + 399×"游离 NEXT"）。
    根因：
    1. FOR 体内 GOSUB 跳到 body 作用域外的标签 → 子程序静默不执行 + GOSUB
       返回点每轮迭代泄漏 → 级联穿透产生 798 条次生警告；
    2. 单行 IF 语句内 `:` 多语句（GDL 语句分隔符）被当成单个语句 → 求值失败。
    """

    def test_for_body_gosub_to_tail_label_executes_subroutine(self):
        """事故形状最小复现：FOR 体内 GOSUB 到脚本尾部标签（body 作用域之外）。
        子程序体实际执行（几何 + 赋值生效）、零"游离 NEXT"、零"DEL 超过栈深"、
        变换栈平衡。"""
        script = """\
FOR i = 1 TO 2
    GOSUB "bar"
    IF _ok THEN
        BLOCK 1, 1, 1
    ENDIF
NEXT i
END
"bar":
    _ok = 1
    ADDX 1
    BLOCK 0.5, 0.5, 0.5
    DEL 1
    RETURN
"""
        res = preview_3d_script(script)
        # 每轮迭代：子程序 1 个 BLOCK（x∈[1,1.5]，ADDX 生效）+ 主流程 1 个
        # BLOCK（x∈[0,1]，_ok 由子程序赋值 → IF 块真实执行）
        self.assertEqual(len(res.meshes), 4)
        sub_meshes = [m for m in res.meshes if min(m.x) >= 1.0 - 1e-9]
        main_meshes = [m for m in res.meshes if max(m.x) <= 1.0 + 1e-9]
        self.assertEqual(len(sub_meshes), 2)
        self.assertEqual(len(main_meshes), 2)
        self.assertEqual(res.warnings, [])

    def test_for_body_gosub_in_scope_keeps_inplace_jump(self):
        """FOR 体内 GOSUB 在块区间内的既有行为回归：目标在 [start, end) 之内时
        压 idx+1 返回点原地跳转，行为与修复前逐字节一致（不回落到 None 哨兵
        分支）。子程序体执行 + 返回后继续 body + 既有回落（P1e 已定性）。"""
        script = """\
FOR i = 1 TO 2
    GOSUB "inner"
    BLOCK 1, 1, 1
    "inner":
        ADDX 2
        BLOCK 0.5, 0.5, 0.5
        DEL 1
        RETURN
NEXT i
"""
        res = preview_3d_script(script)
        # 每轮迭代：GOSUB 原地跳转执行子程序 1 次（x∈[2,2.5]）→ 回主流程
        # BLOCK（x∈[0,1]）→ 回落到标签处再执行一次子程序体（既有回落行为）。
        # 共 6 mesh；若被错误改成跨作用域 None 哨兵路径，行为会变。
        self.assertEqual(len(res.meshes), 6)
        sub_meshes = [m for m in res.meshes if min(m.x) >= 2.0 - 1e-9]
        main_meshes = [m for m in res.meshes if max(m.x) <= 1.0 + 1e-9]
        self.assertEqual(len(sub_meshes), 4)
        self.assertEqual(len(main_meshes), 2)
        # 既有回落行为：子程序体再执行一次时 RETURN 无对应 GOSUB（P1e 已定性，
        # 非本单引入）；变换栈平衡（无 DEL 下溢 / 栈未平衡）。
        self.assertEqual(len([w for w in res.warnings if "RETURN 没有对应 GOSUB" in w]), 2)
        self.assertFalse(any("游离 NEXT" in w for w in res.warnings))
        self.assertFalse(any("DEL 1 超过栈深" in w for w in res.warnings))
        self.assertFalse(any("栈未平衡" in w for w in res.warnings))

    def test_inline_if_colon_multi_statement(self):
        """单行 IF 内 `:` 多语句（条件为真）：IF f = 1 THEN _a = 1 : _b = 2
        首句条件执行，`:` 后语句无条件执行——条件为真时两个赋值都生效。"""
        script = """f = 1
IF f = 1 THEN _a = 1 : _b = 2
BLOCK _a, _b, 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(min(mesh.x), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.x), 1.0, places=6)
        self.assertAlmostEqual(min(mesh.y), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 2.0, places=6)
        self.assertEqual(res.warnings, [])

    def test_inline_if_colon_archicad_semantics_condition_false(self):
        """P13 Archicad 语义：单行 IF 同行只允许一条条件语句（GDL Reference
        Guide AC23 p323）；`:` 是行内语句分隔符，其后的语句**无条件执行**。
        条件为假时：首句不执行、`:` 后语句照跑。

        `IF f = 0 THEN _a = 1 : _b = 2`，f=1（条件为假）→ _a 保持初始值 5，
        _b=2 仍生效。P10 旧语义（整条当作条件执行）在此用例下 _b 不会赋值
        ——本用例盯死 Archicad 语义。"""
        script = """f = 1
_a = 5
IF f = 0 THEN _a = 1 : _b = 2
BLOCK _a, _b, 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        # _a 保持 5（条件为假，首句不执行）；_b = 2 无条件执行
        self.assertAlmostEqual(min(mesh.x), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.x), 5.0, places=6)
        self.assertAlmostEqual(min(mesh.y), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 2.0, places=6)
        self.assertEqual(res.warnings, [])

    def test_inline_if_colon_condition_true_both_execute(self):
        """P13 Archicad 语义补充：条件为真时首句与 `:` 后语句都执行。

        `IF f = 0 THEN _a = 1 : _b = 2`，f=0（条件为真）→ _a=1 与 _b=2
        都生效。"""
        script = """f = 0
_a = 5
IF f = 0 THEN _a = 1 : _b = 2
BLOCK _a, _b, 1
"""
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        self.assertAlmostEqual(max(mesh.x), 1.0, places=6)
        self.assertAlmostEqual(max(mesh.y), 2.0, places=6)
        self.assertEqual(res.warnings, [])

    def test_inline_if_colon_clip_subroutine_equals_block_if_form(self):
        """P13 漏窗事故形状回归：裁剪子程序的
        `IF _pts_count = 1 THEN _px1 = 0 : _py1 = _ty`
        在预览器里与"块 IF 展开版"产出完全一致的几何（两种写法等价呈现），
        `:` 语义对齐 Archicad 后不再是"条件为假也覆写交点"的污染源。"""
        def run(pts_count: int, block_form: bool):
            if block_form:
                s = """_ty = 0.8
_pts_count = %d
_px1 = 9
_py1 = 9
IF _pts_count = 1 THEN
    _px1 = 0
ENDIF
_py1 = _ty
BLOCK _px1, _py1, 1
""" % pts_count
            else:
                s = """_ty = 0.8
_pts_count = %d
_px1 = 9
_py1 = 9
IF _pts_count = 1 THEN _px1 = 0 : _py1 = _ty
BLOCK _px1, _py1, 1
""" % pts_count
            return preview_3d_script(s)

        for pts_count in (1, 2):
            colon_res = run(pts_count, False)
            block_res = run(pts_count, True)
            self.assertEqual(len(colon_res.meshes), 1)
            self.assertEqual(len(block_res.meshes), 1)
            c, b = colon_res.meshes[0], block_res.meshes[0]
            self.assertAlmostEqual(min(c.x), min(b.x), places=6)
            self.assertAlmostEqual(max(c.x), max(b.x), places=6)
            self.assertAlmostEqual(min(c.y), min(b.y), places=6)
            self.assertAlmostEqual(max(c.y), max(b.y), places=6)
            self.assertEqual(colon_res.warnings, [])
            self.assertEqual(block_res.warnings, [])

    def test_inline_if_colon_string_literal_not_split(self):
        """字符串字面量含 `:` 不误拆：条件里的 `"a:b"` 正常字符串比较；
        语句里的 `GOSUB "sub:x"` 标签名含 `:` 不被当作语句分隔符。"""
        script = '''IF s = "a:b" THEN GOSUB "sub:x"
END
"sub:x":
    BLOCK 1, 1, 1
    RETURN
'''
        res = preview_3d_script(script, {"s": "a:b"})
        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.warnings, [])
        res_no = preview_3d_script(script, {"s": "other"})
        self.assertEqual(len(res_no.meshes), 0)

    def test_multi_iteration_gosub_no_stack_leak_no_cascade(self):
        """多轮迭代 GOSUB 后栈不泄漏：FOR 3 轮 + 子程序 RETURN 后，外层
        继续执行的语句只执行一次（盯死级联穿透）。修复前 GOSUB 返回点每轮
        泄漏，外层 RETURN 弹到垃圾区间 → 游离 NEXT + BLOCK 被重复执行多次。"""
        script = """\
GOSUB "outer"
END
"outer":
    _count = 0
    FOR i = 1 TO 3
        GOSUB "inner"
    NEXT i
    _count = _count + 1
    BLOCK _count, 1, 1
    RETURN
"inner":
    _count = _count + 10
    RETURN
"""
        res = preview_3d_script(script)
        # 3 轮 × 10 + 外层 1 = 31；BLOCK 只执行一次（修复前被级联穿透
        # 重复执行 4 次：_count = 1..4，外加 3×"游离 NEXT"）。
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertAlmostEqual(min(mesh.x), 0.0, places=6)
        self.assertAlmostEqual(max(mesh.x), 31.0, places=6)
        self.assertEqual(res.warnings, [])


class TestRuledPreview(unittest.TestCase):

    def test_ruled_basic_square_loft(self):
        """RULED{2} between two squares → one mesh, closed quad strip."""
        script = """\
RULED{2} 4, 52,
    0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
    0, 0, 2, 1, 0, 2, 1, 1, 2, 0, 1, 2
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        self.assertEqual(mesh.name, "RULED")
        self.assertEqual(len(mesh.x), 8)
        # Closed strip: 4 quads → 8 triangles, no caps (mask 52 = j3+j5+j6)
        self.assertEqual(len(mesh.i), 8)
        self.assertEqual(res.warnings, [])

    def test_ruled_caps_add_centroid_fans(self):
        """Mask j1+j2 adds base/top cap fans with centroid vertices."""
        script = """\
RULED{2} 4, 55,
    0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
    0, 0, 2, 1, 0, 2, 1, 1, 2, 0, 1, 2
"""
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        # 8 ring verts + 2 cap centroids
        self.assertEqual(len(mesh.x), 10)
        # 8 side tris + 4 base cap + 4 top cap
        self.assertEqual(len(mesh.i), 16)

    def test_ruled_multiline_continuation_and_addz(self):
        """Trailing-comma continuation + ADDZ offset apply."""
        script = """\
ADDZ 100
RULED 4, 52,
    0, 0, 0,
    1, 0, 0,
    1, 1, 0,
    0, 1, 0,
    0, 0, 2,
    1, 0, 2,
    1, 1, 2,
    0, 1, 2
DEL 1
"""
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 1)
        self.assertAlmostEqual(min(res.meshes[0].z), 100.0)
        self.assertAlmostEqual(max(res.meshes[0].z), 102.0)

    def test_ruled_no_more_unsupported_warning(self):
        """Generated loft GDL (BS2G mesh mode) previews without warnings,
        and consecutive segments weld into ONE smooth mesh."""
        from pathlib import Path
        from openbrep.importers.blender_script.converter import convert_blender_script
        import tempfile

        code = (Path(__file__).parent / "fixtures" / "blender" / "loft_mini.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            project, _ir = convert_blender_script(code, output_dir=tmp)
            from openbrep.hsf_project import ScriptType
            res = preview_3d_script(project.get_script(ScriptType.SCRIPT_3D))
        # loft_mini: 3 rings → 2 RULED segments welded into one chain mesh
        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.warnings, [])
        # 3 rings x 8 pts + base/top cap centroids
        self.assertEqual(len(res.meshes[0].x), 26)
        # 2 segments x 8 quads x 2 tris + 2 caps x 8 tris
        self.assertEqual(len(res.meshes[0].i), 48)
        self.assertAlmostEqual(min(res.meshes[0].z), 0.0)
        self.assertAlmostEqual(max(res.meshes[0].z), 1.0)


class TestGDLPreviewerStackAndSubroutines(unittest.TestCase):
    def test_put_get_expands_into_prism_arguments(self):
        script = """\
PUT 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0
PRISM_ 4, 0.5, GET(12)
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].name, "PRISM_")
        self.assertEqual(len(res.meshes[0].x), 8)  # 4 base + 4 top
        self.assertEqual(len(res.meshes[0].i), 12)  # 4 side quads + 2 caps
        self.assertEqual(res.warnings, [])

    def test_use_peeks_stack_without_consuming(self):
        script = """\
PUT 1, 2, 3
BLOCK USE(3)
BLOCK GET(3)
"""
        res = preview_3d_script(script)

        # USE peeks without popping, so GET can still retrieve the same values.
        self.assertEqual(len(res.meshes), 2)
        self.assertEqual(res.warnings, [])

    def test_gosub_numeric_labels_and_return(self):
        script = """\
GOSUB 100
GOSUB 200
END
100:
    BLOCK 1, 1, 1
    RETURN
200:
    ADDX 2
    BLOCK 1, 1, 1
    DEL 1
    RETURN
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 2)
        self.assertAlmostEqual(min(res.meshes[0].x), 0.0, places=6)
        self.assertAlmostEqual(max(res.meshes[0].x), 1.0, places=6)
        self.assertAlmostEqual(min(res.meshes[1].x), 2.0, places=6)
        self.assertAlmostEqual(max(res.meshes[1].x), 3.0, places=6)
        self.assertFalse(any("GOSUB" in w or "RETURN" in w for w in res.warnings))

    def test_gosub_string_labels(self):
        script = '''\
GOSUB "box"
END
"box":
    BLOCK 2, 2, 2
    RETURN
'''
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertAlmostEqual(max(res.meshes[0].x), 2.0, places=6)

    def test_end_terminates_script_no_double_subroutine_execution(self):
        script = """\
GOSUB 100
END
100:
    BLOCK 1, 1, 1
    RETURN
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)

    def test_tube_helix_sweep(self):
        script = """\
PUT 0,0,0, 0,0,1, 0,0,2
PUT 0.1,0, -0.1,0, 0,0.1, 0,-0.1
TUBE_ 3, 4, 127, GET(17)
"""
        res = preview_3d_script(script)

        self.assertEqual(len(res.meshes), 1)
        self.assertEqual(res.meshes[0].name, "TUBE_")
        # 3 path rings x 4 section verts + 2 cap centroids = 14
        self.assertEqual(len(res.meshes[0].x), 14)
        # 2 segments x 4 quads x 2 tris + 2 caps x 4 tris = 24
        self.assertEqual(len(res.meshes[0].i), 24)
        # P3c：截面 (0.1,0),(-0.1,0),(0,0.1),(0,-0.1) 是自交蝴蝶结（零面积）
        # 退化轮廓 → 盖帽回退扇形 + 明确警告（不再静默）。几何不变。
        self.assertTrue(
            any("TUBE 截面轮廓退化" in w for w in res.warnings),
            f"应警告退化截面，实际: {res.warnings}",
        )

    def test_spiral_stair_put_get_gosub_tube(self):
        script = """\
TOLER 0.005
PEN 1
ADD A / 2, B / 2, 0
GOSUB 1000
GOSUB 2000
GOSUB 3000
GOSUB 4000
DEL 1
END

1000:
    CYLIND height, pole_radius
RETURN

2000:
    FOR i = 0 TO num_steps - 1
        ADDZ i * step_riser
        ROTZ i * step_angle
        FOR k = 0 TO _seg
            PUT tread_outer_radius * COS(k * step_angle / _seg), tread_outer_radius * SIN(k * step_angle / _seg), 0
        NEXT k
        FOR k = _seg TO 0 STEP -1
            PUT pole_radius * COS(k * step_angle / _seg), pole_radius * SIN(k * step_angle / _seg), 0
        NEXT k
        PRISM_ 2 * (_seg + 1), tread_thickness, GET(2 * (_seg + 1) * 3)
        DEL 2
    NEXT i
RETURN

3000:
    _path_pts = num_steps + 1
    _sec_pts = 8
    _tube_vals = _path_pts * 3 + _sec_pts * 2
    FOR i = 0 TO num_steps
        PUT tread_outer_radius * COS((i + 0.5) * step_angle), tread_outer_radius * SIN((i + 0.5) * step_angle), (i + 1) * step_riser + handrail_height
    NEXT i
    FOR k = 0 TO 7
        PUT _rail_r * COS(k * 45), _rail_r * SIN(k * 45)
    NEXT k
    TUBE_ _path_pts, _sec_pts, 127, GET(_tube_vals)
RETURN

4000:
    MATERIAL mat_landing
    ADDZ height - _landing_thk
    ROTZ num_steps * step_angle
    ADDY -_landing_wid / 2
    BLOCK _landing_len, _landing_wid, _landing_thk
    DEL 3
RETURN
"""
        params = {
            "A": 2.0,
            "B": 2.0,
            "height": 3.0,
            "num_steps": 12,
            "pole_radius": 0.06,
            "tread_outer_radius": 0.8,
            "step_riser": 0.25,
            "step_angle": 30.0,
            "tread_thickness": 0.04,
            "_seg": 8,
            "handrail_height": 0.9,
            "_rail_r": 0.02,
            "_landing_thk": 0.04,
            "_landing_len": 1.0,
            "_landing_wid": 0.8,
        }
        res = preview_3d_script(script, params)

        commands = [m.source_ref.command for m in res.meshes]
        # 1 pole + 12 treads + 1 handrail tube + 1 landing
        self.assertEqual(commands.count("CYLIND"), 1)
        self.assertEqual(commands.count("PRISM_"), 12)
        self.assertEqual(commands.count("TUBE_"), 1)
        self.assertEqual(commands.count("BLOCK"), 1)
        self.assertEqual(res.warnings, [])


class TestGDLPreviewer2DCommands(unittest.TestCase):
    """2D 绘图命令：坐标级断言 + 无 unsupported 警告。"""

    def test_2d_rect2_polygon_corners(self):
        res = preview_2d_script("RECT2 0, 0, 4, 2\n")
        self.assertEqual(len(res.polygons), 1)
        self.assertEqual(
            res.polygons[0],
            [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)],
        )
        self.assertFalse(any("RECT2" in w for w in res.warnings))

    def test_2d_line2_endpoints(self):
        res = preview_2d_script("LINE2 1, 2, 3, 4\n")
        self.assertEqual(len(res.lines), 1)
        self.assertEqual(res.lines[0], ((1.0, 2.0), (3.0, 4.0)))
        self.assertFalse(any("LINE2" in w for w in res.warnings))

    def test_2d_circle2_center_radius(self):
        res = preview_2d_script("CIRCLE2 0.5, 0.5, 0.3\n")
        self.assertEqual(len(res.circles), 1)
        self.assertEqual(res.circles[0], (0.5, 0.5, 0.3))
        self.assertFalse(any("CIRCLE2" in w for w in res.warnings))

    def test_2d_arc2_center_radius_angles(self):
        res = preview_2d_script("ARC2 0.5, 0.5, 0.3, 0, 90\n")
        self.assertEqual(len(res.arcs), 1)
        self.assertEqual(res.arcs[0], (0.5, 0.5, 0.3, 0.0, 90.0))
        self.assertFalse(any("ARC2" in w for w in res.warnings))

    def test_2d_poly2_vertices(self):
        res = preview_2d_script("POLY2 4, 0, 0, 1, 0, 1, 1, 0, 1\n")
        self.assertEqual(len(res.polygons), 1)
        self.assertEqual(
            res.polygons[0],
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        )
        self.assertFalse(any("POLY2" in w for w in res.warnings))

    def test_2d_hotspot2_recognized_without_warning(self):
        res = preview_2d_script(
            "HOTSPOT2 0, 0, 1\nHOTSPOT2 A, 0, 2, A, 2\nRECT2 0, 0, A, B\n",
            parameters={"A": 0.8, "B": 0.3},
        )
        # HOTSPOT2 是交互热点（非渲染几何）：识别为无副作用，不告警、不产生几何。
        self.assertEqual(res.warnings, [])
        self.assertEqual(len(res.polygons), 1)

    def test_set_statement_no_side_effect_no_warning(self):
        r2d = preview_2d_script("SET MATERIAL wood\nRECT2 0, 0, 1, 1\n")
        self.assertFalse(any("SET" in w for w in r2d.warnings))
        self.assertEqual(len(r2d.polygons), 1)

        r3d = preview_3d_script("SET MATERIAL wood\nBLOCK 1, 1, 1\n")
        self.assertFalse(any("SET" in w for w in r3d.warnings))
        self.assertEqual(len(r3d.meshes), 1)

    def test_values_silently_ignored_in_2d_and_3d(self):
        values = 'VALUES "A" RANGE [0.30, 3.00]\n'
        r2d = preview_2d_script(values + "RECT2 0, 0, 1, 1\n")
        self.assertFalse(any("VALUES" in w for w in r2d.warnings))
        self.assertEqual(len(r2d.polygons), 1)

        r3d = preview_3d_script(values + "BLOCK 1, 1, 1\n")
        self.assertFalse(any("VALUES" in w for w in r3d.warnings))
        self.assertEqual(len(r3d.meshes), 1)

    def test_bookshelf_scenario_warnings_single_digit(self):
        """Bookshelf 场景验收：导入 examples/Bookshelf.gdl 后，2D+3D 预览
        总 unsupported 警告数降到个位数（硬指标）。"""
        bookshelf = Path(__file__).resolve().parents[1] / "examples" / "Bookshelf.gdl"
        project, _ = parse_gdl_source_with_warnings(
            bookshelf.read_text(encoding="utf-8"), "Bookshelf"
        )
        params = parameter_values(project)
        scripts = {k.name: v for k, v in project.scripts.items()}

        r2d = preview_2d_script(
            scripts["SCRIPT_2D"], parameters=params, setup_script=scripts["MASTER"]
        )
        r3d = preview_3d_script(
            scripts["SCRIPT_3D"], parameters=params, setup_script=scripts["MASTER"]
        )

        total = len(r2d.warnings) + len(r3d.warnings)
        self.assertLess(total, 10)
        # 几何不丢：2D 侧板线 + 外轮廓矩形，3D 框架/层板/背板网格。
        self.assertEqual(len(r2d.lines), 3)
        self.assertEqual(len(r2d.polygons), 1)
        self.assertGreaterEqual(len(r3d.meshes), 8)


class TestGDLPreviewerProject2(unittest.TestCase):
    """P3a：PROJECT2 顶视图投影 MVP。

    语义锁定（写入代码注释的同一套）：
    - 只支持 projection_code=3（顶视图），其他值明确警告"暂不支持"。
    - angle：视角旋转 = 投影点反向旋转 angle 度（即旋转 −angle，
      标准数学逆时针为正；angle=90 时投影点旋转 −90°）。
    - method：MVP 无 hidden-line，忽略 + 一次性警告。
    - PROJECT2{n} 扩展形态 → 明确警告，不静默。
    - 投影算法：每 mesh 面边按顶点索引对 (min,max) 去重后投影（丢 z），
      过 _p2 应用当前 2D 变换（ADD2/ROT2/MUL2）。MVP 不做轮廓并集 /
      hidden-line；三角化网格的面对角线按"面边去重"规则保留。
    - 不传 script_3d 时行为与 P3a 前逐字节一致（占位警告）。
    """

    @staticmethod
    def _seg(line: tuple[tuple[float, float], tuple[float, float]], nd: int = 9):
        p1 = tuple(round(v, nd) for v in line[0])
        p2 = tuple(round(v, nd) for v in line[1])
        return (min(p1, p2), max(p1, p2))

    @staticmethod
    def _seg_set(lines):
        return {TestGDLPreviewerProject2._seg(line) for line in lines}

    def test_project2_block_top_view_edge_count_and_coords(self):
        # BLOCK 1,2,3：12 条真实棱边 + 6 条三角化面对角线（面边去重规则，
        # MVP 明确保留）= 18 条线；投影矩形 (0,0)-(1,2)，4 条竖直棱退化。
        res = preview_2d_script(
            "PROJECT2 3, 0, 2\n", script_3d="BLOCK 1, 2, 3\n"
        )
        self.assertEqual(len(res.lines), 18)
        segs = self._seg_set(res.lines)
        # 底面/顶面矩形四条边（顶面与底面投影重合，均出现）
        for seg in [
            ((0.0, 0.0), (1.0, 0.0)),
            ((1.0, 0.0), (1.0, 2.0)),
            ((1.0, 2.0), (0.0, 2.0)),
            ((0.0, 2.0), (0.0, 0.0)),
        ]:
            self.assertIn(self._seg(seg), segs)
        # 三角化面对角线（MVP 面边去重规则保留）：底面与顶面三角化
        # 都取 (0,0)-(1,2) 这条对角，投影后出现 2 次
        self.assertIn(self._seg(((0.0, 0.0), (1.0, 2.0))), segs)
        # 竖直棱投影退化：4 条零长线段
        degenerate = [line for line in res.lines if line[0] == line[1]]
        self.assertEqual(len(degenerate), 4)
        # 顶视图投影不含 z 信息：无任何 y<0 或 y>2 的坐标
        for line in res.lines:
            for px, py in line:
                self.assertGreaterEqual(py, 0.0)
                self.assertLessEqual(py, 2.0)

    def test_project2_angle_rotates_projection_backwards(self):
        # 选 Archicad 语义：视角旋转 = 投影点反向旋转 angle 度（−angle）。
        # BLOCK 1,1,1 底边 (0,0)-(1,0)：
        #   angle=90  → (0,0)-(0,−1)
        #   angle=270 → (0,0)-(0,1)
        #   angle=180 → (0,0)-(−1,0)
        for angle, expected in [
            (90, ((0.0, 0.0), (0.0, -1.0))),
            (270, ((0.0, 0.0), (0.0, 1.0))),
            (180, ((0.0, 0.0), (-1.0, 0.0))),
        ]:
            res = preview_2d_script(
                f"PROJECT2 3, {angle}, 2\n", script_3d="BLOCK 1, 1, 1\n"
            )
            self.assertIn(
                self._seg(expected),
                self._seg_set(res.lines),
                f"angle={angle} 旋转语义错误",
            )

    def test_project2_respects_prior_2d_transforms(self):
        # PROJECT2 前 ADD2/ROT2/MUL2 生效：投影点过 _p2 应用当前 2D 变换。
        # BLOCK 1,1,1 底边 (0,0)-(1,0)。
        res = preview_2d_script(
            "ADD2 100, 50\nPROJECT2 3, 0, 2\n", script_3d="BLOCK 1, 1, 1\n"
        )
        self.assertIn(self._seg(((100.0, 50.0), (101.0, 50.0))), self._seg_set(res.lines))

        # ROT2 90：逆时针旋转 90°（_rot_z_deg 标准数学方向）
        res = preview_2d_script(
            "ROT2 90\nPROJECT2 3, 0, 2\n", script_3d="BLOCK 1, 1, 1\n"
        )
        self.assertIn(self._seg(((0.0, 0.0), (0.0, 1.0))), self._seg_set(res.lines))

        # ADD2 平移 + ROT2 旋转组合：先旋转后平移
        res = preview_2d_script(
            "ADD2 100, 50\nROT2 90\nPROJECT2 3, 0, 2\n",
            script_3d="BLOCK 1, 1, 1\n",
        )
        self.assertIn(self._seg(((100.0, 50.0), (100.0, 51.0))), self._seg_set(res.lines))

        # MUL2 缩放
        res = preview_2d_script(
            "MUL2 2, 1\nPROJECT2 3, 0, 2\n", script_3d="BLOCK 1, 1, 1\n"
        )
        self.assertIn(self._seg(((0.0, 0.0), (2.0, 0.0))), self._seg_set(res.lines))

        # ADD2/DEL2 配对：栈平衡，无"栈未平衡"警告，投影不被平移
        res = preview_2d_script(
            "ADD2 100, 50\nDEL2\nPROJECT2 3, 0, 2\n",
            script_3d="BLOCK 1, 1, 1\n",
        )
        self.assertIn(self._seg(((0.0, 0.0), (1.0, 0.0))), self._seg_set(res.lines))
        self.assertFalse(any("栈未平衡" in w for w in res.warnings))

    def test_project2_non_top_code_warns_not_silent(self):
        # projection_code 只支持 3（顶视图）；其他值明确警告且不投影。
        for code in (1, 2, 4, 5):
            res = preview_2d_script(
                f"PROJECT2 {code}, 0, 2\n", script_3d="BLOCK 1, 1, 1\n"
            )
            self.assertEqual(res.lines, [])
            self.assertTrue(
                any(f"投影方式 {code} 暂不支持" in w for w in res.warnings),
                f"code={code} 应明确警告",
            )
            self.assertTrue(
                any(
                    f"投影方式 {code} 暂不支持" in w.message
                    for w in res.warnings_structured
                )
            )

    def test_project2_braced_extension_warns_not_silent(self):
        # PROJECT2{2}/{3} 扩展形态：明确警告、不静默、不投影。
        for braced in ("PROJECT2{2} 3, 0, 2", "PROJECT2{3} 3, 0, 2"):
            res = preview_2d_script(
                braced + "\n", script_3d="BLOCK 1, 1, 1\n"
            )
            self.assertEqual(res.lines, [])
            self.assertTrue(
                any("扩展形态暂不支持" in w for w in res.warnings)
            )

    def test_project2_method_warned_once(self):
        # method（hidden-line 等）忽略 + 一次性警告；后续 PROJECT2 仍投影。
        res = preview_2d_script(
            "PROJECT2 3, 0, 2\nPROJECT2 3, 45, 3\n",
            script_3d="BLOCK 1, 1, 1\n",
        )
        method_warns = [w for w in res.warnings if "method" in w]
        self.assertEqual(len(method_warns), 1)
        self.assertEqual(
            len([w for w in res.warnings_structured if "method" in w.message]), 1
        )
        self.assertGreater(len(res.lines), 0)
        # 第二个 PROJECT2 的 angle=45 生效：底边 (0,0)-(1,0) 反向旋转 45°
        self.assertIn(
            ((0.0, 0.0), (0.707106781, -0.707106781)),
            self._seg_set(res.lines),
        )

    def test_project2_without_script_3d_keeps_placeholder_byte_identical(self):
        # 不传 script_3d（semantic_verifier / 验收等旧调用方）：占位警告原样。
        res = preview_2d_script("PROJECT2 3, 0, 2\n")
        self.assertEqual(res.lines, [])
        self.assertEqual(
            res.warnings, ["line 1: PROJECT2 暂为占位预览（未实现真实投影）"]
        )
        self.assertEqual(len(res.warnings_structured), 1)
        self.assertEqual(
            res.warnings_structured[0].message,
            "PROJECT2 暂为占位预览（未实现真实投影）",
        )
        self.assertEqual(res.warnings_structured[0].command, "")
        # 扩展形态无 script_3d 时同样保持占位警告原样
        res = preview_2d_script("PROJECT2{2} 3, 0, 2\n")
        self.assertEqual(
            res.warnings, ["line 1: PROJECT2 暂为占位预览（未实现真实投影）"]
        )

    def test_project2_does_not_merge_inner_3d_warnings(self):
        # 内部 3D 执行的 warnings 不并入 2D 结果（3D 预览路径已展示）。
        res = preview_2d_script(
            "PROJECT2 3, 0, 2\n",
            script_3d="BLOCK 1, 1, 1\nFOOBAR 7\n",
        )
        self.assertGreater(len(res.lines), 0)
        self.assertFalse(any("FOOBAR" in w for w in res.warnings))
        self.assertFalse(any("未支持命令" in w for w in res.warnings))

    def test_project2_parameters_and_setup_flow_into_3d_execution(self):
        # 内部 3D runtime 复用同一组 parameters/setup：3D 脚本可用参数与
        # setup 赋值。BLOCK A,B,ZZYZX = 1,2,3 → 顶视图矩形 (0,0)-(1,2)。
        res = preview_2d_script(
            "PROJECT2 3, 0, 2\n",
            parameters={"A": 1.0, "B": 2.0, "ZZYZX": 3.0},
            script_3d="BLOCK A, B, ZZYZX\n",
        )
        self.assertIn(self._seg(((0.0, 0.0), (1.0, 2.0))), self._seg_set(res.lines))
        # setup 赋值变量在 3D 中可用：d=1.5 → 1.5 立方体
        res = preview_2d_script(
            "PROJECT2 3, 0, 2\n",
            setup_script="d = 1.5\n",
            script_3d="BLOCK d, d, d\n",
        )
        self.assertIn(self._seg(((0.0, 0.0), (1.5, 0.0))), self._seg_set(res.lines))

    def test_preview_scripts_passes_script_3d_to_2d_path(self):
        # combined 入口：preview_scripts 同样传递 script_3d，2D 含投影 lines。
        res = preview_scripts("PROJECT2 3, 0, 2\n", "BLOCK 1, 1, 1\n")
        self.assertEqual(len(res.preview_2d.lines), 18)
        self.assertEqual(len(res.preview_3d.meshes), 1)
        self.assertIn(
            ((0.0, 0.0), (1.0, 0.0)), self._seg_set(res.preview_2d.lines)
        )


class TestPreviewSourceSegment(unittest.TestCase):
    """P1e 相关代码段：source_ref 的 segment_start/segment_end 语义。

    区间均为真实脚本行号、含端点；嵌套取数值上包含命令行的最内层区间。
    """

    def _segments(self, script, **kwargs):
        res = preview_3d_script(script, **kwargs)
        return [(m.source_ref.line, m.source_ref.segment_start, m.source_ref.segment_end) for m in res.meshes]

    def test_top_level_command_segment_is_its_own_line(self):
        script = """\
! comment
ADDZ 1
BLOCK 1, 2, 3
"""
        self.assertEqual(self._segments(script), [(3, 3, 3)])

    def test_for_body_mesh_gets_whole_for_next_range(self):
        script = """\
FOR i = 1 TO 3
    ADDX i
    CYLIND 1, 0.5
NEXT i
"""
        # 3 次迭代共享同一个 FOR…NEXT 段（含头尾）
        self.assertEqual(self._segments(script), [(3, 1, 4), (3, 1, 4), (3, 1, 4)])

    def test_if_branch_mesh_gets_whole_if_endif_range_including_else(self):
        script = """\
A = 2
IF A > 1 THEN
    PRISM_ 4, 2,
        0, 0,
        1, 0,
        1, 1,
        0, 1
ELSE
    BLOCK 1, 1, 1
ENDIF
"""
        # PRISM 在 IF 分支内：段 = IF 行 2 到 ENDIF 行 10（含 ELSE 段）
        self.assertEqual(self._segments(script), [(3, 2, 10)])

        # 同一脚本换条件走 ELSE 分支：BLOCK 行 9，段仍是整个 IF…ENDIF
        script_else = script.replace("A = 2", "A = 0")
        self.assertEqual(self._segments(script_else), [(9, 2, 10)])

    def test_gosub_subroutine_mesh_gets_label_to_return_range(self):
        script = """\
GOSUB 100
END
100:
    BLOCK 1, 1, 1
    RETURN
"""
        # BLOCK 行 4：段 = 标签 100（行 3）到 RETURN（行 5）
        self.assertEqual(self._segments(script), [(4, 3, 5)])

    def test_string_label_subroutine_range(self):
        script = """\
GOSUB "box"
END
"box":
    CYLIND 1, 0.5
    RETURN
"""
        self.assertEqual(self._segments(script), [(4, 3, 5)])

    def test_nested_blocks_take_innermost_range(self):
        script = """\
IF 1 THEN
    FOR i = 1 TO 2
        CYLIND 1, 0.5
    NEXT i
ENDIF
"""
        # CYLIND 行 3 同时位于 IF（1..6）和 FOR（2..4）内 → 取最内层 FOR
        self.assertEqual(self._segments(script), [(3, 2, 4), (3, 2, 4)])

    def test_gosub_inside_if_reports_subroutine_not_if_block(self):
        script = """\
IF 1 THEN
    GOSUB 100
ENDIF
100:
    BLOCK 1, 1, 1
    RETURN
"""
        # 第一次执行经 GOSUB：BLOCK 行 5 在子程序（4..6）内，IF 段（1..3）
        # 不包含该行号 → 段 = 子程序区间
        segments = self._segments(script)
        self.assertEqual(segments[0], (5, 4, 6))
        # 预既有解释器行为：ENDIF 后回落到标签处会再执行一次子程序体，
        # 第二次为顶层单行段 —— 本单只保证第一次溯源正确。
        self.assertIn((5, 5, 5), segments)

    def test_nested_gosub_chain_uses_innermost_subroutine(self):
        script = """\
GOSUB 100
END
100:
    GOSUB 200
    BLOCK 1, 1, 1
    RETURN
200:
    CYLIND 1, 0.5
    RETURN
"""
        # 执行顺序：GOSUB 100 → GOSUB 200 → CYLIND（子程序 200：标签行 7..RETURN 行 9）
        # → RETURN 回 100 体 → BLOCK（子程序 100：标签行 3..RETURN 行 6）
        self.assertEqual(self._segments(script), [(8, 7, 9), (5, 3, 6)])

    def test_for_inside_subroutine_takes_for_range(self):
        script = """\
GOSUB 100
END
100:
    FOR i = 1 TO 2
        CYLIND 1, 0.5
    NEXT i
    RETURN
"""
        # FOR 段（4..6）比子程序段（3..7）更内层
        self.assertEqual(self._segments(script), [(5, 4, 6), (5, 4, 6)])

    def test_source_ref_still_backwards_compatible_without_segment(self):
        # 手工构造的 PreviewSourceRef（如测试 fixture）不填段字段 → None，不报错
        from openbrep.gdl_previewer import PreviewSourceRef

        ref = PreviewSourceRef(script_type="3d", line=1, command="BLOCK", label="x")
        self.assertIsNone(ref.segment_start)
        self.assertIsNone(ref.segment_end)

    def test_geometry_unchanged_by_segment_metadata(self):
        # 几何不变锁定：P1e 只加 source_ref 元数据，mesh 几何逐字节一致。
        # 黄金值已用入库前（8d5bcd1）的解释器逐字节比对确认（见 P1e 报告），
        # 任何几何漂移都会红灯。段区间同时验证：BLOCK 顶层单行 (1,1)，
        # PRISM 在子程序 50 内 → (标签 4, RETURN 9)。
        script = """\
BLOCK 1, 2, 3
GOSUB 50
END
50:
    PRISM_ 3, 1,
        0, 0,
        1, 0,
        0, 1
    RETURN
"""
        res = preview_3d_script(script)
        canonical = [
            {
                "name": m.name,
                "x": [round(v, 9) for v in m.x],
                "y": [round(v, 9) for v in m.y],
                "z": [round(v, 9) for v in m.z],
                "i": m.i,
                "j": m.j,
                "k": m.k,
            }
            for m in res.meshes
        ]
        self.assertEqual(
            canonical,
            [
                {
                    "name": "BLOCK",
                    "x": [0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0],
                    "y": [0.0, 0.0, 2.0, 2.0, 0.0, 0.0, 2.0, 2.0],
                    "z": [0.0, 0.0, 0.0, 0.0, 3.0, 3.0, 3.0, 3.0],
                    "i": [0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3],
                    "j": [1, 2, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7],
                    "k": [2, 3, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4],
                },
                {
                    "name": "PRISM_",
                    "x": [0.0, 1.0, 0.0, 0.0, 1.0, 0.0],
                    "y": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
                    "z": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
                    "i": [0, 0, 1, 1, 2, 2, 0, 3],
                    "j": [1, 4, 2, 5, 0, 3, 2, 4],
                    "k": [4, 3, 5, 4, 3, 5, 1, 5],
                },
            ],
        )
        self.assertEqual(
            [(m.source_ref.line, m.source_ref.segment_start, m.source_ref.segment_end) for m in res.meshes],
            [(1, 1, 1), (5, 4, 9)],
        )



if __name__ == "__main__":
    unittest.main()


class TestP3cConcaveCapTriangulation(unittest.TestCase):
    """P3c：凹多边形盖帽耳切三角化 + 凸轮廓逐字节不变 + 退化回退警告。"""

    # 凹 L 形轮廓（CCW），面积 3.0：2x2 方块切掉右上 1x1
    L_SHAPE = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]

    @staticmethod
    def _tri_area(p0, p1, p2):
        return abs(
            (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
        ) / 2.0

    @staticmethod
    def _polygon_area(pts):
        return abs(gdl_previewer._polygon_signed_area2(pts))

    @staticmethod
    def _point_in_polygon(px, py, pts):
        """Ray-casting point-in-polygon."""
        inside = False
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > py) != (yj > py):
                x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
                if px < x_cross:
                    inside = not inside
            j = i
        return inside

    def _cap_faces(self, mesh, n_side):
        """Split mesh faces into (side, bottom, top) by 0-based index ranges."""
        return mesh.i, mesh.j, mesh.k

    def test_concave_prism_l_shape_triangulation_correct(self):
        script = "PRISM_ 6, 1, " + ", ".join(
            f"{x},{y}" for x, y in self.L_SHAPE
        ) + "\n"
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        self.assertEqual(len(mesh.x), 12)  # 6 base + 6 top
        # 6 side quads x2 + 4 bottom + 4 top = 20 faces
        self.assertEqual(len(mesh.i), 20)
        self.assertEqual(res.warnings, [])

        pts = self.L_SHAPE
        n = len(pts)
        area = self._polygon_area(pts)
        self.assertAlmostEqual(area, 3.0, places=6)

        # 底盖与顶盖逐三角形校验（face 顺序：每个耳切三角形先底后顶交错）：
        # 面积和 = 多边形面积，且每个三角形质心在多边形内（无越界三角形）。
        side = 2 * n
        bottom = mesh.i[side::2]
        top = mesh.i[side + 1 :: 2]
        self.assertEqual(len(bottom), n - 2)
        self.assertEqual(len(top), n - 2)

        def check_cap(face_idx_triples, z):
            total = 0.0
            for i, j, k in face_idx_triples:
                p0 = (mesh.x[i], mesh.y[i])
                p1 = (mesh.x[j], mesh.y[j])
                p2 = (mesh.x[k], mesh.y[k])
                total += self._tri_area(p0, p1, p2)
                cx = (p0[0] + p1[0] + p2[0]) / 3
                cy = (p0[1] + p1[1] + p2[1]) / 3
                self.assertTrue(
                    self._point_in_polygon(cx, cy, pts),
                    f"三角形质心 ({cx},{cy}) 越出多边形",
                )
                self.assertAlmostEqual(mesh.z[i], z, places=6)
                self.assertAlmostEqual(mesh.z[j], z, places=6)
                self.assertAlmostEqual(mesh.z[k], z, places=6)
            self.assertAlmostEqual(total, area, places=6)

        bottom_tris = [(bottom[t], mesh.j[side + 2 * t], mesh.k[side + 2 * t]) for t in range(len(bottom))]
        top_tris = [(top[t], mesh.j[side + 2 * t + 1], mesh.k[side + 2 * t + 1]) for t in range(len(top))]
        check_cap(bottom_tris, 0.0)
        check_cap(top_tris, 1.0)

    def test_convex_prism_byte_identical_to_old_fan(self):
        # 凸矩形：顶点 0 扇形（旧行为）逐字节不变 —— 与旧代码路径公式逐字段比对
        pts = [(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0)]
        n = len(pts)
        res = preview_3d_script("PRISM 4, 2, 0,0, 3,0, 3,2, 0,2\n")
        mesh = res.meshes[0]

        expected_i = []
        expected_j = []
        expected_k = []
        for i in range(n):
            j = (i + 1) % n
            expected_i.extend((i, i))
            expected_j.extend((j, n + j))
            expected_k.extend((n + j, n + i))
        # Bottom fan (0, i+1, i)
        for i in range(1, n - 1):
            expected_i.append(0)
            expected_j.append(i + 1)
            expected_k.append(i)
        # Top fan (n, n+i, n+i+1)
        for i in range(1, n - 1):
            expected_i.append(n)
            expected_j.append(n + i)
            expected_k.append(n + i + 1)

        self.assertEqual(mesh.i, expected_i)
        self.assertEqual(mesh.j, expected_j)
        self.assertEqual(mesh.k, expected_k)
        self.assertEqual(res.warnings, [])

    def test_degenerate_prism_contours_warn_and_fallback_to_fan(self):
        # 重复顶点 / 共线 / 自交轮廓 → 警告 + 回退旧扇形（几何与旧扇形逐字节一致）
        cases = {
            "dup": ([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1.0, 1.0), (0.0, 1.0)], "重复"),
            "collinear": ([(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], "共线"),
            "self_intersect": ([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)], "自交"),
        }
        for key, (pts, _tag) in cases.items():
            n = len(pts)
            script = "PRISM " + str(n) + ", 1, " + ", ".join(
                f"{x},{y}" for x, y in pts
            ) + "\n"
            res = preview_3d_script(script)
            mesh = res.meshes[0]
            self.assertTrue(
                any("轮廓退化" in w for w in res.warnings),
                f"{key}: 应警告退化轮廓，实际 {res.warnings}",
            )
            # 回退旧扇形：底盖 (0, i+1, i)，顶盖 (n, n+i, n+i+1)
            side = 2 * n
            self.assertEqual(len(mesh.i), side + 2 * (n - 2))
            for t in range(n - 2):
                self.assertEqual((mesh.i[side + t], mesh.j[side + t], mesh.k[side + t]),
                                 (0, t + 2, t + 1))
            for t in range(n - 2):
                self.assertEqual((mesh.i[side + n - 2 + t], mesh.j[side + n - 2 + t], mesh.k[side + n - 2 + t]),
                                 (n, n + t + 1, n + t + 2))

    def test_concave_ruled_caps_earclipped(self):
        # RULED：L 形底盖 + L 形顶盖（mask 55 = j1+j2）→ 耳切，面积和 = 3.0
        script = "RULED 6, 55, " + ", ".join(
            f"{x},{y},0" for x, y in self.L_SHAPE
        ) + ", " + ", ".join(
            f"{x},{y},2" for x, y in self.L_SHAPE
        ) + "\n"
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        # 12 ring verts（耳切盖帽不加质心顶点）
        self.assertEqual(len(mesh.x), 12)
        # 6 quads x2 side + 4 + 4 cap tris = 20
        self.assertEqual(len(mesh.i), 20)
        self.assertEqual(res.warnings, [])
        # 两端盖（底 + 顶）共 8 个三角形：面积和 = 2 × 多边形面积，
        # 每个三角形质心在多边形内（无越界三角形）。
        n = 6
        side = 2 * n
        cap_faces = [
            (mesh.i[t], mesh.j[t], mesh.k[t])
            for t in range(side, len(mesh.i))
        ]
        total = 0.0
        for i, j, k in cap_faces:
            p0 = (mesh.x[i], mesh.y[i])
            p1 = (mesh.x[j], mesh.y[j])
            p2 = (mesh.x[k], mesh.y[k])
            total += self._tri_area(p0, p1, p2)
            cx = (p0[0] + p1[0] + p2[0]) / 3
            cy = (p0[1] + p1[1] + p2[1]) / 3
            self.assertTrue(self._point_in_polygon(cx, cy, self.L_SHAPE))
        self.assertAlmostEqual(total, 2 * self._polygon_area(self.L_SHAPE), places=6)

    def test_concave_tube_caps_earclipped(self):
        # TUBE：L 形截面沿竖直路径扫掠（mask 127 = 两端盖）→ 耳切
        path = "0,0,0, 0,0,3"
        sec = ", ".join(f"{x},{y}" for x, y in self.L_SHAPE)
        script = f"TUBE_ 2, 6, 127, {path}, {sec}\n"
        res = preview_3d_script(script)
        mesh = res.meshes[0]
        # 2 rings x 6 verts（耳切盖帽不加质心顶点）
        self.assertEqual(len(mesh.x), 12)
        # 1 segment x 6 quads x2 side + 4 + 4 cap tris = 20
        self.assertEqual(len(mesh.i), 20)
        self.assertEqual(res.warnings, [])
        # 两端盖共 8 个三角形：面积和 = 2 × 多边形面积，无越界三角形。
        # 盖帽落在截面局部平面（竖直线路径的 frame 会旋转截面），所以用实际
        # 截面环的 world 顶点构造多边形来做质心包含判断。
        n = 6
        side = 2 * n
        ring0 = [(mesh.x[i], mesh.y[i]) for i in range(n)]
        cap_faces = [
            (mesh.i[t], mesh.j[t], mesh.k[t])
            for t in range(side, len(mesh.i))
        ]
        total = 0.0
        for i, j, k in cap_faces:
            p0 = (mesh.x[i], mesh.y[i])
            p1 = (mesh.x[j], mesh.y[j])
            p2 = (mesh.x[k], mesh.y[k])
            total += self._tri_area(p0, p1, p2)
            cx = (p0[0] + p1[0] + p2[0]) / 3
            cy = (p0[1] + p1[1] + p2[1]) / 3
            self.assertTrue(
                self._point_in_polygon(cx, cy, ring0),
                f"盖帽三角形质心 ({cx:.3f},{cy:.3f}) 越出截面",
            )
        self.assertAlmostEqual(total, 2 * self._polygon_area(ring0), places=6)
    def test_concave_ruled_welded_chain_top_cap_earclipped(self):
        # RULED 链式焊接（ADDZ 抬升后续段）+ 每段凹 L 形轮廓：焊接后新增的
        # 顶盖也走耳切（_try_weld_ruled 的 mask&2 路径）。
        script = """\
RULED 6, 55,
    0,0,0, 2,0,0, 2,1,0, 1,1,0, 1,2,0, 0,2,0,
    0,0,1, 2,0,1, 2,1,1, 1,1,1, 1,2,1, 0,2,1
ADDZ 1
RULED 6, 2,
    0,0,0, 2,0,0, 2,1,0, 1,1,0, 1,2,0, 0,2,0,
    0,0,1, 2,0,1, 2,1,1, 1,1,1, 1,2,1, 0,2,1
DEL 1
"""
        res = preview_3d_script(script)
        self.assertEqual(res.warnings, [])
        self.assertEqual(len(res.meshes), 1)
        mesh = res.meshes[0]
        # 12 + 6 环顶点（耳切盖帽不加质心），face 36 = 24 侧面 + 12 盖帽
        self.assertEqual(len(mesh.x), 18)
        self.assertEqual(len(mesh.i), 36)
        # 盖帽三角形（seg1 底/顶盖 + seg2 顶盖）质心均在 L 形内，面积和 = 3×3
        pts = self.L_SHAPE
        total = 0.0
        for t in [*range(12, 20), *range(32, 36)]:
            i, j, k = mesh.i[t], mesh.j[t], mesh.k[t]
            p0 = (mesh.x[i], mesh.y[i])
            p1 = (mesh.x[j], mesh.y[j])
            p2 = (mesh.x[k], mesh.y[k])
            total += self._tri_area(p0, p1, p2)
            cx = (p0[0] + p1[0] + p2[0]) / 3
            cy = (p0[1] + p1[1] + p2[1]) / 3
            self.assertTrue(self._point_in_polygon(cx, cy, pts))
        self.assertAlmostEqual(total, 3 * self._polygon_area(pts), places=6)


class TestP3cForDoubleGate(unittest.TestCase):
    """P3c：FOR 双闸门——迭代上限（默认 5000）+ wall-clock 上限（默认 10s）。"""

    def test_default_for_limit_raised_to_5000(self):
        from openbrep.gdl_previewer import DEFAULT_FOR_LIMIT
        self.assertEqual(DEFAULT_FOR_LIMIT, 5000)
        # 600 次迭代的循环在旧上限 500 下会被截断；新默认下完整执行
        script = "FOR i = 1 TO 600\nBLOCK 0.01, 0.01, 0.01\nNEXT i\n"
        res = preview_3d_script(script)
        self.assertEqual(len(res.meshes), 600)
        self.assertFalse(any("提前终止" in w for w in res.warnings))

    def test_iteration_gate_warns_and_stops(self):
        script = "FOR i = 1 TO 100\nBLOCK 0.01, 0.01, 0.01\nNEXT i\n"
        res = preview_3d_script(script, for_limit=10)
        self.assertTrue(
            any("FOR 迭代超过上限 10" in w for w in res.warnings),
            f"应触发迭代闸门，实际 {res.warnings}",
        )
        self.assertFalse(any("耗时上限" in w for w in res.warnings))
        # 只执行了 10 次迭代
        self.assertEqual(len(res.meshes), 10)
        self.assertTrue(
            any("FOR 迭代超过上限 10" in w.message for w in res.warnings_structured)
        )

    def test_wall_clock_gate_warns_and_stops(self):
        # 迭代上限设很大，耗时闸门极小 → 触发的是耗时闸门而不是迭代闸门
        script = "FOR i = 1 TO 100000\nBLOCK 0.01, 0.01, 0.01\nNEXT i\n"
        res = preview_3d_script(
            script, for_limit=100000, wall_clock_limit=1e-9
        )
        self.assertTrue(
            any("FOR 执行超过耗时上限" in w for w in res.warnings),
            f"应触发耗时闸门，实际 {res.warnings}",
        )
        self.assertFalse(any("迭代超过上限" in w for w in res.warnings))
        self.assertLess(len(res.meshes), 100000)
        self.assertTrue(
            any("FOR 执行超过耗时上限" in w.message for w in res.warnings_structured)
        )

    def test_wall_clock_limit_zero_disables_time_gate(self):
        # wall_clock_limit=0 关闭耗时闸门：仅迭代闸门生效
        script = "FOR i = 1 TO 100\nBLOCK 0.01, 0.01, 0.01\nNEXT i\n"
        res = preview_3d_script(script, for_limit=10, wall_clock_limit=0.0)
        self.assertTrue(any("FOR 迭代超过上限 10" in w for w in res.warnings))
        self.assertFalse(any("耗时上限" in w for w in res.warnings))

