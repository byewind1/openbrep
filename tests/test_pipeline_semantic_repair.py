"""
Tests for the CREATE-path semantic repair loop (S1).

After compile succeeds, verify_semantics runs; blocking semantic issues
(mesh_empty / mesh_degenerate / bbox_mismatch) trigger bounded repair rounds
whose repair instruction carries the deterministic evidence. Each round is
accepted only if compile still passes (when a compiler is configured) AND the
blocking-issue count strictly decreases — otherwise the round is rolled back
so delivery is never worse than no repair.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticIssue, SemanticVerificationResult


# ── helpers ────────────────────────────────────────────────


def _make_project(tmp_path: Path, name: str = "test_shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    return proj


def _mock_llm_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, model="mock", usage={}, finish_reason="stop")


def _make_pipeline(
    llm_content: str,
    compiler_mock: MagicMock,
    has_real_compiler: bool = True,
) -> tuple[TaskPipeline, MagicMock]:
    cfg = GDLAgentConfig()
    if has_real_compiler:
        cfg.compiler.path = "/fake/LP_XMLConverter"
    pipeline = TaskPipeline(config=cfg, trace_dir="/tmp")
    mock_llm = MagicMock()
    mock_llm.generate.return_value = _mock_llm_response(llm_content)
    pipeline._make_llm = lambda req: mock_llm
    pipeline._make_compiler = lambda: compiler_mock
    return pipeline, mock_llm


def _ok_compile() -> CompileResult:
    return CompileResult(
        success=True, stdout="", stderr="", mode="lp",
        output_path="/tmp/test.gsm", exit_code=0,
    )


def _fail_compile() -> CompileResult:
    return CompileResult(
        success=False, stdout="", stderr="error: Undefined variable 'bad'", mode="lp",
        output_path=None, exit_code=1,
    )


def _sem(*issues: SemanticIssue) -> SemanticVerificationResult:
    return SemanticVerificationResult(
        passed=not any(i.blocking for i in issues),
        issues=list(issues),
    )


def _blocking(code: str = "mesh_empty") -> SemanticIssue:
    return SemanticIssue(
        check_type=code,
        detail="3d.gdl 中检测到几何命令，但预览渲染出 0 个 mesh（几何可能未实际生效）",
        blocking=True,
    )


GDL_REPLY = (
    "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
    "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
)
GDL_REPAIR_REPLY = (
    "[FILE: scripts/3d.gdl]\nADDX 0.1\nBLOCK A, B, ZZYZX\nDEL 1\nEND\n"
    "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
)


def _run_create(pipeline: TaskPipeline, tmp_path: Path) -> object:
    project = _make_project(tmp_path)
    req = TaskRequest(
        user_input="生成一个货架", intent="CREATE",
        project=project, work_dir=str(tmp_path), output_dir=str(tmp_path),
    )
    return pipeline.execute(req)


def _semantic_check(result) -> dict | None:
    return next(
        (c for c in result.verification["checks"] if c["check_type"] == "semantic"),
        None,
    )


def _llm_texts(mock_llm: MagicMock) -> str:
    return "\n".join(
        str(call.args[0]) + str(call.kwargs)
        for call in mock_llm.generate.call_args_list
    )


# ── 1. blocking issue triggers repair; accepted when issues clear ──


class TestSemanticRepairAccepted:
    def test_blocking_issue_triggers_repair_round(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPAIR_REPLY, compiler_mock)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ) as sem_mock:
            result = _run_create(pipeline, tmp_path)

        assert sem_mock.call_count == 2
        # 修复指令携带确定性证据
        assert "几何语义验证" in _llm_texts(mock_llm)
        assert "mesh_empty" in _llm_texts(mock_llm)
        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"
        assert "语义修复" in result.plain_text

    def test_repair_works_without_compiler(self, tmp_path: Path):
        """未配置编译器时语义修复仍可用（判决者 previewer 不依赖编译器）。"""
        compiler_mock = MagicMock()
        pipeline, mock_llm = _make_pipeline(GDL_REPAIR_REPLY, compiler_mock, has_real_compiler=False)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"
        assert "几何语义验证" in _llm_texts(mock_llm)


# ── 2. semantic pass → no repair ──


class TestNoRepairWhenClean:
    def test_no_repair_when_semantic_passes(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem(),
        ) as sem_mock:
            result = _run_create(pipeline, tmp_path)

        assert sem_mock.call_count == 1
        assert "几何语义验证" not in _llm_texts(mock_llm)
        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"
        assert result.success is True


# ── 3. no improvement → rollback ──


class TestRollbackOnNoImprovement:
    def test_rolls_back_when_blocking_count_unchanged(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(GDL_REPAIR_REPLY)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem(_blocking())],
        ):
            result = _run_create(pipeline, tmp_path)

        # 脚本回退到修复前内容
        script_3d = result.project.get_script(ScriptType.SCRIPT_3D)
        assert "ADDX" not in script_3d
        assert "已回退" in result.plain_text
        # 未解决的 blocking issue 如实出现在报告里
        check = _semantic_check(result)
        assert check is not None and check["status"] == "fail"
        # 交付门禁：blocking 验证失败 → success=False
        assert result.success is False


# ── 4. repair breaks compile → rollback ──


class TestRollbackOnCompileBreak:
    def test_rolls_back_when_repair_breaks_compile(self, tmp_path: Path):
        compiler_mock = MagicMock()
        # 初次编译 ok → 语义修复轮编译 fail → 回退后重编译 ok
        compiler_mock.hsf2libpart.side_effect = [_ok_compile(), _fail_compile(), _ok_compile()]
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(GDL_REPAIR_REPLY)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        assert compiler_mock.hsf2libpart.call_count == 3
        script_3d = result.project.get_script(ScriptType.SCRIPT_3D)
        assert "ADDX" not in script_3d
        assert "已回退" in result.plain_text
        compile_check = next(
            c for c in result.verification["checks"] if c["check_type"] == "compile"
        )
        assert compile_check["status"] == "pass"


# ── 5. bounded rounds ──


class TestMaxRounds:
    def test_rounds_bounded_and_each_judged(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, _ = _make_pipeline(GDL_REPAIR_REPLY, compiler_mock)

        b1, b2 = _blocking("mesh_empty"), _blocking("bbox_mismatch")
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[
                _sem(b1, b2),   # 初始：2 个阻断
                _sem(b1),       # 第 1 轮：降到 1 → 接受
                _sem(b1),       # 第 2 轮：未下降 → 回退并停止
            ],
        ):
            result = _run_create(pipeline, tmp_path)

        assert "生效" in result.plain_text
        assert "已回退" in result.plain_text
        check = _semantic_check(result)
        assert check is not None and check["status"] == "fail"


# ── 6. compile ultimately failed → semantic repair skipped ──


class TestSkipWhenCompileBroken:
    def test_no_semantic_repair_when_compile_failed(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _fail_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem(_blocking()),
        ) as sem_mock:
            result = _run_create(pipeline, tmp_path)

        # 编译都没过，不在未编译代码上做语义修复
        assert sem_mock.call_count == 1
        assert "几何语义验证" not in _llm_texts(mock_llm)
        check = _semantic_check(result)
        assert check is not None and check["status"] == "fail"
        assert result.success is False


# ── MODIFY 路径：同一实现，对称行为 ──


def _run_modify(pipeline: TaskPipeline, tmp_path: Path) -> object:
    project = _make_project(tmp_path)
    req = TaskRequest(
        user_input="把书架加一层", intent="MODIFY",
        project=project, work_dir=str(tmp_path), output_dir=str(tmp_path),
        agent_loop=False,
    )
    return pipeline.execute(req)


class TestModifySemanticWiring:
    """MODIFY / DEBUG / REPAIR 此前零几何验证：报告里必须出现 semantic check。"""

    def test_modify_verification_report_includes_semantic(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, _ = _make_pipeline(GDL_REPLY, compiler_mock)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            return_value=_sem(),
        ) as sem_mock:
            result = _run_modify(pipeline, tmp_path)

        assert sem_mock.call_count == 1
        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"
        assert result.success is True

    def test_modify_blocking_issue_triggers_repair(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPAIR_REPLY, compiler_mock)

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_modify(pipeline, tmp_path)

        assert "几何语义验证" in _llm_texts(mock_llm)
        assert "语义修复" in result.plain_text
        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"

    def test_modify_rolls_back_when_no_improvement(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(GDL_REPAIR_REPLY)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem(_blocking())],
        ):
            result = _run_modify(pipeline, tmp_path)

        script_3d = result.project.get_script(ScriptType.SCRIPT_3D)
        assert "ADDX" not in script_3d
        assert "已回退" in result.plain_text
        check = _semantic_check(result)
        assert check is not None and check["status"] == "fail"
        assert result.success is False

# ── P8 1. 省略号残桩 → 拒绝 + 回退 ─────────────────────────

# ── P8 防退化守卫测试辅助 ─────────────────────────────────


def _make_project_long_3d(tmp_path: Path, n_lines: int = 20, name: str = "test_long") -> HSFProject:
    """3d.gdl 有 n_lines 行**非注释**代码（≥10 才触发锐减规则），供内容锐减守卫测试。"""
    body_lines = [f"dummy_{i} = {i}" for i in range(1, n_lines)]
    code = "BLOCK A, B, ZZYZX\n" + "\n".join(body_lines) + "\nEND\n"
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.scripts[ScriptType.SCRIPT_3D] = code
    return proj


def _long_3d_code(n_code_lines: int) -> str:
    """BLOCK + (n_code_lines-2) 行赋值 + END 的 3d 脚本体（非注释代码行数 = n_code_lines）。"""
    body = [f"dummy_{i} = {i}" for i in range(1, n_code_lines - 1)]
    return "BLOCK A, B, ZZYZX\n" + "\n".join(body) + "\nEND"


def _repair_reply_3d(script_body: str, paramlist: str | None = None) -> str:
    parts = [f"[FILE: scripts/3d.gdl]\n{script_body}"]
    if paramlist is not None:
        parts.append(f"[FILE: paramlist.xml]\n{paramlist}")
    return "\n".join(parts)


PARAMLIST_3_RESERVED = (
    "Length A = 1.0  ! Width\n"
    "Length B = 1.0  ! Depth\n"
    "Length ZZYZX = 1.0  ! Height\n"
)
PARAMLIST_2_MATERIALS = (
    "Material mat_frame = 0  ! 框架材质\n"
    "Material mat_lattice = 0  ! 棂条材质\n"
)

class TestDegradationGuardEllipsis:
    """修复轮返回独立成行省略号残桩 → 不进接受判定，拒绝并回退。"""

    def _repair_round_script(self) -> str:
        # 事故形状：5 行残桩，第 2 行是字面省略号
        return "IF show_lattice THEN\n       ...\n   ENDIF\n\n   END\n"

    def test_ellipsis_stub_rejected_and_rolled_back(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(self._repair_round_script())
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        # 回退：3d.gdl 与修复前逐字节一致（无 IF/省略号）
        script_3d = result.project.get_script(ScriptType.SCRIPT_3D)
        assert "..." not in script_3d and "IF" not in script_3d
        assert script_3d == "BLOCK A, B, ZZYZX\nEND\n"
        assert "省略号残桩" in result.plain_text
        assert "已回退" in result.plain_text
        # 未接受的 blocking issue 如实报告 → 交付门禁不过
        check = _semantic_check(result)
        assert check is not None and check["status"] == "fail"
        assert result.success is False

    def test_ellipsis_variant_unicode_ellipsis_rejected(self, tmp_path: Path):
        """Unicode 省略号 … 同样拒绝。"""
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response("[FILE: scripts/3d.gdl]\n…\nEND\n")
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        assert "省略号残桩" in result.plain_text
        assert "已回退" in result.plain_text
        assert result.project.get_script(ScriptType.SCRIPT_3D) == "BLOCK A, B, ZZYZX\nEND\n"

    def test_comment_ellipsis_not_rejected(self, tmp_path: Path):
        """行内注释/注释行里的 ... 不是残桩 → 不拒绝（正常接受）。"""
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        good_repair = (
            "[FILE: scripts/3d.gdl]\n"
            "ADDX 0.1\n"
            "BLOCK A, B, ZZYZX ! 尺寸 ... 注释里的省略号\n"
            "! ... 注释行\n"
            "DEL 1\n"
            "END\n"
        )

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(good_repair)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        assert "省略号残桩" not in result.plain_text
        assert "已回退" not in result.plain_text
        assert "生效" in result.plain_text
        assert "ADDX" in result.project.get_script(ScriptType.SCRIPT_3D)


# ── P8 2. 参数丢失守卫 ─────────────────────────────────────


class TestDegradationGuardParamLoss:
    """修复轮丢 A/B/ZZYZX 或参数总数下降 → 拒绝 + 回退。"""

    def test_missing_reserved_param_rejected(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        # 修复回复：3d 正常，但 paramlist 只剩 2 个材质参数（A/B/ZZYZX 全丢）
        bad_reply = _repair_reply_3d(
            "BLOCK A, B, ZZYZX\nEND\n",
            paramlist=PARAMLIST_2_MATERIALS,
        )

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(bad_reply)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = _run_create(pipeline, tmp_path)

        assert "保留参数" in result.plain_text
        assert "已回退" in result.plain_text
        # 参数表回退到修复前（3 个保留参数）
        names = [p.name for p in result.project.parameters]
        assert names == ["A", "B", "ZZYZX"]
        assert result.success is False

    def test_param_count_decrease_rejected(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        proj = _make_project(tmp_path)
        proj.parameters = [
            GDLParameter("A", "Length", "Width", "1.00", is_fixed=True),
            GDLParameter("B", "Length", "Depth", "1.00", is_fixed=True),
            GDLParameter("ZZYZX", "Length", "Height", "1.00", is_fixed=True),
            GDLParameter("shelf_count", "Integer", "层板数", "3", is_fixed=False),
        ]

        # 修复回复把 shelf_count 删了（4 → 3）
        shrink_reply = _repair_reply_3d(
            "BLOCK A, B, ZZYZX\nEND\n",
            paramlist=PARAMLIST_3_RESERVED,
        )

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(shrink_reply)
            return _mock_llm_response(GDL_REPLY)

        mock_llm.generate.side_effect = _gen

        req = TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=proj, work_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = pipeline.execute(req)

        assert "参数总数下降" in result.plain_text
        assert "已回退" in result.plain_text
        assert len(result.project.parameters) == 4  # shelf_count 保留
        assert result.success is False


# ── P8 3. 内容锐减守卫 ─────────────────────────────────────


class TestDegradationGuardContentShrink:
    """修复后脚本内容锐减（20 行 → 3 行）→ 拒绝；最小修复（20 → 19）→ 接受。"""

    def test_shrink_to_stub_rejected(self, tmp_path: Path):
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        # 初生成本身是 21 行非注释代码；修复轮掉到 2 行残桩
        long_reply = (
            "[FILE: scripts/3d.gdl]\n" + _long_3d_code(20) + "\n"
            "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
        )
        stub_reply = (
            "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n"
            "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
        )

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(stub_reply)
            return _mock_llm_response(long_reply)

        mock_llm.generate.side_effect = _gen

        req = TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_make_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = pipeline.execute(req)

        assert "内容锐减" in result.plain_text
        assert "已回退" in result.plain_text
        # 回退后 3d.gdl 与修复前（初生成 21 行版本）逐字节一致
        assert result.project.get_script(ScriptType.SCRIPT_3D) == _long_3d_code(20) + "\n"
        assert result.success is False

    def test_minimal_fix_accepted(self, tmp_path: Path):
        """21 行 → 20 行的正常最小修复不被守卫误伤。"""
        compiler_mock = MagicMock()
        compiler_mock.hsf2libpart.return_value = _ok_compile()
        pipeline, mock_llm = _make_pipeline(GDL_REPLY, compiler_mock)

        long_reply = (
            "[FILE: scripts/3d.gdl]\n" + _long_3d_code(20) + "\n"
            "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
        )
        # 20 行非注释代码：正常最小修复（去掉一行）
        minimal_reply = (
            "[FILE: scripts/3d.gdl]\n" + _long_3d_code(19) + "\n"
            "[FILE: scripts/2d.gdl]\nPROJECT2 3, -1, 2\nEND\n"
        )

        def _gen(messages, **kwargs):
            if "几何语义验证" in str(messages):
                return _mock_llm_response(minimal_reply)
            return _mock_llm_response(long_reply)

        mock_llm.generate.side_effect = _gen

        req = TaskRequest(
            user_input="生成一个货架", intent="CREATE",
            project=_make_project(tmp_path), work_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        with patch(
            "openbrep.semantic_verifier.verify_semantics",
            side_effect=[_sem(_blocking()), _sem()],
        ):
            result = pipeline.execute(req)

        assert "内容锐减" not in result.plain_text
        assert "已回退" not in result.plain_text
        assert "生效" in result.plain_text
        check = _semantic_check(result)
        assert check is not None and check["status"] == "pass"
        assert result.success is True
