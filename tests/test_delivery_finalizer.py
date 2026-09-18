"""ST02：交付源绑定（delivery_source / after-revision / 源指纹）契约测试。

覆盖总控验收矩阵 R01–R10 的可离线自动化部分。全部使用 tmp_path +
MockLLM / MockHSFCompiler，不读取开发者真实 config.toml。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.llm import LLMResponse, MockLLM
from openbrep.revisions import (
    create_revision,
    list_revisions,
    load_revision_protections,
    protected_revision_ids,
    prune_revisions,
    register_revision_protection,
    unregister_revision_protection,
)
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.runtime.delivery_finalizer import (
    DeliverySource,
    FinalizeDeliveryInputs,
    apply_delivery_source_to_result,
    finalize_delivery,
)
from openbrep.source_fingerprint import (
    compute_revision_fingerprint,
    compute_source_fingerprint,
    fingerprints_equal,
    sanitize_fingerprint,
)


# ── fixtures ───────────────────────────────────────────────


def _make_project(tmp_path: Path, name: str = "Shelf") -> HSFProject:
    proj = HSFProject.create_new(name, work_dir=str(tmp_path))
    proj.parameters = [
        GDLParameter(name="A", type_tag="Length", description="宽度", value="0.9"),
        GDLParameter(name="B", type_tag="Length", description="深度", value="0.4"),
        GDLParameter(name="ZZYZX", type_tag="Length", description="高度", value="1.8"),
        GDLParameter(name="shelf_count", type_tag="Integer", description="层板数量", value="4"),
        GDLParameter(name="shelf_thk", type_tag="Length", description="层板厚度", value="0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\nEND\n"
    proj.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    proj.save_to_disk()
    return proj


def _make_pipeline(tmp_path: Path, *, mock_llm=None, quality_ledger_enabled: bool = True) -> TaskPipeline:
    cfg = GDLAgentConfig()
    cfg.compiler.path = "/fake/LP_XMLConverter"
    pipeline = TaskPipeline(
        config=cfg,
        trace_dir=str(tmp_path / "traces"),
        quality_ledger_enabled=quality_ledger_enabled,
        include_learned_skills=False,
    )
    if mock_llm is None:
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content="[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nEND\n",
            model="mock", usage={}, finish_reason="stop",
        )
    pipeline._make_llm = lambda _req: mock_llm
    pipeline._make_compiler = lambda: MockHSFCompiler()
    return pipeline


def _request(project: HSFProject, tmp_path: Path, user_input: str, intent: str = "MODIFY", **overrides) -> TaskRequest:
    kwargs = dict(
        user_input=user_input,
        intent=intent,
        project=project,
        work_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        gsm_name=project.name,
        agent_loop=True,
    )
    kwargs.update(overrides)
    return TaskRequest(**kwargs)


def _agent_loop_llm_updates(content: str) -> MockLLM:
    return MockLLM(responses=[
        {"tool_calls": [{"name": "update_script", "arguments": {
            "file_path": "scripts/3d.gdl", "content": content,
        }}]},
        {"tool_calls": [{"name": "compile_script", "arguments": {}}]},
        "已完成修改。",
    ])


def _sem_pass():
    result = MagicMock()
    result.issues = []
    result.passed = True
    return result


def _delivery(result) -> DeliverySource:
    ds = DeliverySource.from_dict((result.metadata or {}).get("delivery_source"))
    assert ds is not None, f"missing delivery_source in metadata: {(result.metadata or {}).keys()}"
    return ds


def _quality_records(project: HSFProject) -> list[dict]:
    runs_dir = project.root / ".openbrep" / "quality" / "runs"
    if not runs_dir.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs_dir.glob("*.json"))]


def _revision_fp(project: HSFProject, revision_id: str) -> str:
    rev_dir = project.root / ".openbrep" / "revisions" / revision_id
    return compute_revision_fingerprint(rev_dir)


# ── R01：两种 agent loop 各成功修改一次 ───────────────────


class TestR01VerifiedChangeBindsAfter:
    def test_normal_agent_loop_before_after_and_quality(self, tmp_path):
        project = _make_project(tmp_path)
        before_fp = compute_source_fingerprint(project.root)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n")
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(
                project, tmp_path, "给书架加一层层板", intent="MODIFY",
            ))
        assert result.success, result.plain_text
        ds = _delivery(result)
        assert ds.state == "verified_change"
        assert ds.before_revision_id
        assert ds.after_revision_id
        assert ds.before_revision_id != ds.after_revision_id
        assert ds.source_fingerprint
        assert ds.snapshot_status == "saved"
        assert fingerprints_equal(ds.source_fingerprint, _revision_fp(project, ds.after_revision_id))
        assert not fingerprints_equal(before_fp, ds.source_fingerprint)
        records = _quality_records(project)
        assert records
        assert records[0]["provenance"]["after_revision"] == ds.after_revision_id
        assert records[0]["provenance"]["before_revision"] == ds.before_revision_id
        assert records[0]["provenance"]["source_fingerprint"] == ds.source_fingerprint

    def test_codex_agent_loop_binds_after(self, tmp_path):
        """Codex 桥接路径：假 app-server 成功流 → delivery_source.after 与验证后源一致。"""
        from tests.test_codex_modify_bridge import (
            _FakeServerHarness,
            _codex_config,
            _final,
            _make_project as _codex_project,
            _pipeline as _codex_pipeline,
            _request as _codex_request,
            _sem_pass as _codex_sem,
            _tool,
            _upd,
            _write_script,
        )

        harness = _FakeServerHarness(tmp_path)
        _write_script(
            tmp_path,
            [[
                _upd("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"),
                _tool("compile_script"),
                _final("已加一层层板，编译通过。"),
            ]],
        )
        config = _codex_config()
        provider = harness.provider()
        pipeline = _codex_pipeline(config, provider, tmp_path)
        project = _codex_project(tmp_path)
        try:
            with patch("openbrep.semantic_verifier.verify_semantics", return_value=_codex_sem()):
                result = pipeline.execute(_codex_request(tmp_path, project))
            assert result.success, result.plain_text
            ds = _delivery(result)
            assert ds.state == "verified_change"
            assert ds.before_revision_id != ds.after_revision_id
            assert fingerprints_equal(ds.source_fingerprint, _revision_fp(project, ds.after_revision_id))
            records = _quality_records(project)
            if records:
                assert records[0]["provenance"]["after_revision"] == ds.after_revision_id
        finally:
            provider.close()
            harness.cleanup()


# ── R02：连续两次修改，after 独立且前次不可变 ─────────────


class TestR02IndependentAfterSnapshots:
    def test_two_modifies_independent_after(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)

        mock_a = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nEND\n")
        pipeline._make_llm = lambda _req: mock_a
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result_a = pipeline.execute(_request(project, tmp_path, "加一层层板"))
        ds_a = _delivery(result_a)
        assert ds_a.state == "verified_change"
        fp_a = ds_a.source_fingerprint
        snap_a_path = project.root / ".openbrep" / "revisions" / ds_a.after_revision_id / "scripts" / "3d.gdl"
        snap_a_content = snap_a_path.read_text(encoding="utf-8")

        mock_b = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 2\nEND\n")
        pipeline._make_llm = lambda _req: mock_b
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result_b = pipeline.execute(_request(project, tmp_path, "再加一层层板"))
        ds_b = _delivery(result_b)
        assert ds_b.state == "verified_change"
        assert ds_b.after_revision_id != ds_a.after_revision_id
        assert ds_b.before_revision_id != ds_a.before_revision_id
        assert not fingerprints_equal(fp_a, ds_b.source_fingerprint)
        # 前次 after 快照不可变
        assert snap_a_path.read_text(encoding="utf-8") == snap_a_content
        assert fingerprints_equal(fp_a, _revision_fp(project, ds_a.after_revision_id))


# ── R03：无工具解释 / 只检查 ──────────────────────────────


class TestR03UnchangedExplainOnly:
    def test_explain_only_unchanged_no_extra_revision(self, tmp_path):
        project = _make_project(tmp_path)
        revs_before = len(list_revisions(project.root))
        pipeline = _make_pipeline(tmp_path)
        mock_llm = MockLLM(responses=["这个书架由 BLOCK 构成，参数 A/B/ZZYZX 控制尺寸。"])
        pipeline._make_llm = lambda _req: mock_llm
        result = pipeline.execute(_request(
            project, tmp_path, "解释一下这个对象", intent="CHAT", agent_loop=False,
        ))
        ds = _delivery(result)
        assert ds.state == "unchanged"
        assert ds.after_revision_id is None
        assert len(list_revisions(project.root)) == revs_before

    def test_agent_loop_text_only_no_tools_unchanged(self, tmp_path):
        project = _make_project(tmp_path)
        revs_before = len(list_revisions(project.root))
        pipeline = _make_pipeline(tmp_path)
        mock_llm = MockLLM(responses=["我检查过了，脚本无需修改。"])
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(project, tmp_path, "检查一下是否需要修改"))
        ds = _delivery(result)
        assert ds.after_revision_id is None
        assert ds.state in ("unchanged", "failed_no_change")
        assert len(list_revisions(project.root)) == revs_before


# ── R04：明确修改但没有 diff ──────────────────────────────


class TestR04ClaimedChangeNoDiff:
    def test_claimed_modify_no_diff_is_unchanged(self, tmp_path):
        project = _make_project(tmp_path)
        original = project.get_script(ScriptType.SCRIPT_3D)
        pipeline = _make_pipeline(tmp_path)
        # 工具写回相同内容 → 声称修改但无 diff
        mock_llm = _agent_loop_llm_updates(original)
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(project, tmp_path, "把层板厚度微调一下"))
        ds = _delivery(result)
        # 编译通过不能当作「修改完成」
        assert ds.state == "unchanged"
        assert ds.after_revision_id is None


# ── R05：写一部分后 timeout/cancel ────────────────────────


class TestR05PartialChangeInterrupted:
    def test_cancel_after_write_partial_change(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        content = "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nEND\n"
        mock_llm = MockLLM(responses=[
            {"tool_calls": [{
                "name": "update_script",
                "arguments": {"file_path": "scripts/3d.gdl", "content": content},
            }]},
            "unused after cancel",
        ])
        pipeline._make_llm = lambda _req: mock_llm
        cancel_flag = {"n": 0}

        def should_cancel():
            cancel_flag["n"] += 1
            # 首次 loop 入口放行，写完后下一轮取消
            return cancel_flag["n"] >= 2

        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(
                project, tmp_path, "加一层层板", should_cancel=should_cancel,
            ))
        ds = _delivery(result)
        assert ds.state == "partial_change"
        assert ds.after_revision_id is None
        assert ds.before_revision_id  # before 仍在，可恢复
        # TaskResult 不宣称完整成功（有变更被中断）
        assert result.success is False
        before_dir = project.root / ".openbrep" / "revisions" / ds.before_revision_id
        assert before_dir.is_dir()
        assert (before_dir / "scripts" / "3d.gdl").exists()


# ── R06：after / ledger 写盘失败 ──────────────────────────


class TestR06SnapshotAndLedgerFailures:
    def test_after_write_failure_snapshot_failed(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates(
            "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        )
        pipeline._make_llm = lambda _req: mock_llm
        from openbrep.revisions import create_revision as real_create

        def _broken_after_only(*args, **kwargs):
            message = str(kwargs.get("message") or "")
            if "after" in message.lower():
                raise OSError("disk full")
            return real_create(*args, **kwargs)

        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()), \
             patch("openbrep.revisions.create_revision", side_effect=_broken_after_only):
            result = pipeline.execute(_request(project, tmp_path, "给书架加一层层板"))
        ds = _delivery(result)
        # after 不可伪造：写盘失败 → snapshot_failed，success 不宣称完整成功
        assert ds.state == "snapshot_failed"
        assert ds.after_revision_id is None
        assert ds.snapshot_status == "failed"
        assert result.success is False
        assert result.error
        # 验证子结果保留
        assert result.verification is not None
        assert (result.verification or {}).get("passed") is True

    def test_ledger_write_failure_does_not_change_delivery(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nEND\n")
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()), \
             patch("openbrep.quality.store.write_record", side_effect=OSError("ledger disk full")):
            result = pipeline.execute(_request(project, tmp_path, "加一层层板"))
        ds = _delivery(result)
        assert ds.state == "verified_change"
        assert ds.after_revision_id
        assert result.success is True


# ── R07：微修改 / 老路径 after / 无重复 ───────────────────


class TestR07MicroAndExistingAfter:
    def test_micro_modify_contract_creates_after(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        result = pipeline.execute(_request(
            project, tmp_path, "把 shelf_count 改成 5", agent_loop=True,
        ))
        assert "确定性微修改" in result.plain_text
        ds = _delivery(result)
        assert ds.state == "verified_change"
        assert ds.after_revision_id
        assert fingerprints_equal(ds.source_fingerprint, _revision_fp(project, ds.after_revision_id))
        assert "paramlist.xml" in ds.changed_files
        records = _quality_records(project)
        assert records[0]["provenance"]["after_revision"] == ds.after_revision_id
        # 无重复 after：仅一条 after role
        after_revs = [
            r for r in list_revisions(project.root)
            if (r.path / "manifest.json").exists()
            and json.loads((r.path / "manifest.json").read_text(encoding="utf-8"))
            .get("metadata", {}).get("delivery", {}).get("role") == "after"
        ]
        assert len(after_revs) == 1

    def test_existing_after_not_duplicated(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path, mock_llm=MockLLM(responses=[
            "[FILE: scripts/3d.gdl]\nBLOCK A, B, ZZYZX\nADDZ ZZYZX\nEND\n",
        ]))
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(
                project, tmp_path, "加一层层板", agent_loop=False,
            ))
        ds = _delivery(result)
        if ds.state == "verified_change":
            ids = [r.revision_id for r in list_revisions(project.root)]
            assert ids.count(ds.after_revision_id) == 1
            assert ds.after_revision_id
            # 老路径已创建 after 时 finalizer 复用，不再新建同内容 after
            from openbrep.source_fingerprint import compute_source_fingerprint as cfp
            assert fingerprints_equal(ds.source_fingerprint, cfp(project.root))


# ── R08：epoch 改变 ──────────────────────────────────────


class TestR08EpochChanged:
    def test_epoch_changed_rejects_snapshot_and_quality(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nEND\n")
        pipeline._make_llm = lambda _req: mock_llm
        state = {"epoch_ok": True}

        def epoch_guard():
            return state["epoch_ok"]

        # before 创建后 epoch 翻转：通过 finalizer 输入模拟
        from openbrep.runtime.delivery_finalizer import finalize_delivery as _fd

        inputs = FinalizeDeliveryInputs(
            run_id="r_test_epoch",
            project=project,
            intent="MODIFY",
            handler_success=True,
            claimed_change=True,
            changed_files=["scripts/3d.gdl"],
            interrupted=False,
            verified=True,
            before_revision_id="r0000",
            epoch_guard=lambda: False,
        )
        ds, _ = _fd(inputs)
        assert ds.state in ("partial_change", "failed_no_change")
        assert ds.error_code == "epoch_changed"
        assert ds.after_revision_id is None
        assert ds.snapshot_status == "rejected"

    def test_pipeline_epoch_guard_skips_quality_on_new_project(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nEND\n")
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(
                project, tmp_path, "加一层层板",
                epoch_guard=lambda: False,
            ))
        ds = _delivery(result)
        assert ds.after_revision_id is None
        assert ds.error_code == "epoch_changed"
        records = _quality_records(project)
        assert records == []  # epoch 失效：不给新项目写质量档案


# ── R09：keep_last_n + 保护 ──────────────────────────────


class TestR09ProtectedPrune:
    def test_keep_last_n_protects_before_after(self, tmp_path):
        project = _make_project(tmp_path)
        root = project.root
        # 创建阶段禁用 auto-prune，避免保护登记前就被 keep_last_n=1 删掉
        cfg_path = tmp_path / "cfg_revisions.toml"
        cfg_path.write_text("[revisions]\nkeep_last_n = 0\n", encoding="utf-8")
        with patch.dict("os.environ", {"GDL_AGENT_CONFIG": str(cfg_path)}):
            r1_before = create_revision(root, "before 1", parent_revision_id=None)
            register_revision_protection(
                root, r1_before.revision_id,
                reason="current_run_before", run_id="r_a",
            )
            (root / "scripts" / "3d.gdl").write_text(
                "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nEND\n", encoding="utf-8"
            )
            r1_after = create_revision(
                root, "after 1",
                parent_revision_id=r1_before.revision_id,
            )
            register_revision_protection(
                root, r1_after.revision_id,
                reason="latest_successful_after", run_id="r_a",
            )
            r2_before = create_revision(
                root, "before 2", parent_revision_id=r1_after.revision_id,
            )
            register_revision_protection(
                root, r2_before.revision_id,
                reason="current_run_before", run_id="r_b",
            )
            (root / "scripts" / "3d.gdl").write_text(
                "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nADDZ ZZYZX\nEND\n", encoding="utf-8",
            )
            r2_after = create_revision(
                root, "after 2", parent_revision_id=r2_before.revision_id,
            )
            register_revision_protection(
                root, r2_after.revision_id,
                reason="latest_successful_after", run_id="r_b",
            )
            # keep_last_n=1：受保护 before/after 不消失
            result = prune_revisions(root, keep_last_n=1)
            remaining = {r.revision_id for r in list_revisions(root)}
            assert r2_before.revision_id in remaining
            assert r2_after.revision_id in remaining
            assert r1_after.revision_id in remaining  # 仍被保护
            # 保护导致超过 keep_last_n 时明确报告
            assert result.overflow_protection or result.warnings

            # 解除保护后按保留策略处理
            for rid in (r1_before.revision_id, r1_after.revision_id,
                        r2_before.revision_id, r2_after.revision_id):
                unregister_revision_protection(root, rid)
            result2 = prune_revisions(root, keep_last_n=1)
            remaining2 = {r.revision_id for r in list_revisions(root)}
            assert result2.deleted >= 1
            from openbrep.revisions import get_latest_revision_id
            assert get_latest_revision_id(root) in remaining2
            # 解除保护后只剩 keep_last_n 窗口 + latest
            assert len(remaining2) <= 2

    def test_external_protection_interface(self, tmp_path):
        project = _make_project(tmp_path)
        rev = create_revision(project.root, "host candidate")
        from openbrep.runtime.delivery_finalizer import register_external_protection
        register_external_protection(
            project.root, rev.revision_id,
            reason="pending_candidate", run_id="r_x",
            ref={"proposal_id": "p1"},
        )
        assert rev.revision_id in protected_revision_ids(project.root)
        assert load_revision_protections(project.root)[rev.revision_id]


# ── R10：验证与快照之间手工改源 ──────────────────────────


class TestR10ExternalChangeBetweenVerifyAndSnapshot:
    def test_refuse_verified_change_on_source_mutation(self, tmp_path):
        project = _make_project(tmp_path)
        before = create_revision(project.root, "before modify", parent_revision_id=None)
        # 验证时捕获指纹
        verified_fp = compute_source_fingerprint(project.root)
        # 验证后、快照前手工改源
        (project.root / "scripts" / "3d.gdl").write_text(
            "BLOCK A, B, ZZYZX\nREM external mutation\nEND\n", encoding="utf-8",
        )
        ds, warnings = finalize_delivery(FinalizeDeliveryInputs(
            run_id="r10",
            project=project,
            intent="MODIFY",
            claimed_change=True,
            changed_files=["scripts/3d.gdl"],
            verified=True,
            before_revision_id=before.revision_id,
            verified_source_fingerprint=verified_fp,
        ))
        assert ds.state == "snapshot_failed"
        assert ds.error_code == "source_changed_after_verification"
        assert ds.after_revision_id is None
        assert any("外部" in w for w in warnings)

    def test_pipeline_rejects_when_fingerprint_diverges(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates(
            "BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.018\nDEL 1\nEND\n"
        )
        pipeline._make_llm = lambda _req: mock_llm
        from openbrep.source_fingerprint import compute_source_fingerprint as real_csf

        def _mutate_then_fp(root):
            # finalizer 路径：先外部改源再算指纹，与 agent loop 验证后捕获的指纹不一致
            path = Path(root) / "scripts" / "3d.gdl"
            path.write_text(
                path.read_text(encoding="utf-8") + "REM external\n", encoding="utf-8",
            )
            return real_csf(root)

        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()), \
             patch(
                 "openbrep.runtime.delivery_finalizer.compute_source_fingerprint",
                 side_effect=_mutate_then_fp,
             ):
            result = pipeline.execute(_request(project, tmp_path, "给书架加一层层板"))
        ds = _delivery(result)
        assert ds.state != "verified_change"
        assert ds.state == "snapshot_failed"
        assert ds.error_code == "source_changed_after_verification"
        assert ds.after_revision_id is None


# ── 纯函数 / 契约形状 ─────────────────────────────────────


class TestDeliverySourceContract:
    def test_schema_shape_and_roundtrip(self):
        ds = DeliverySource(
            run_id="r_20260918_000000_abc123",
            state="verified_change",
            before_revision_id="r0001",
            after_revision_id="r0002",
            source_fingerprint="sha256:" + "a" * 64,
            changed_files=["scripts/3d.gdl"],
            snapshot_status="saved",
            error_code=None,
        )
        data = ds.to_dict()
        assert set(data) == {
            "schema_version", "run_id", "state",
            "before_revision_id", "after_revision_id",
            "source_fingerprint", "changed_files",
            "snapshot_status", "error_code",
        }
        assert data["schema_version"] == 1
        restored = DeliverySource.from_dict(data)
        assert restored is not None
        assert restored.after_revision_id == "r0002"
        # 旧记录缺字段 → from_dict 安全
        assert DeliverySource.from_dict({"state": "verified_change", "run_id": "x"}) is not None
        assert DeliverySource.from_dict({"state": "nope"}) is None
        assert DeliverySource.from_dict(None) is None

    def test_fingerprint_stable_and_sensitive(self, tmp_path):
        project = _make_project(tmp_path)
        fp1 = compute_source_fingerprint(project.root)
        fp2 = compute_source_fingerprint(project.root)
        assert fp1 == fp2
        assert fp1.startswith("sha256:")
        (project.root / "scripts" / "3d.gdl").write_text("CYLIND 1, 1\nEND\n", encoding="utf-8")
        fp3 = compute_source_fingerprint(project.root)
        assert fp1 != fp3
        assert sanitize_fingerprint(fp1).startswith("sha256:")
        assert len(sanitize_fingerprint(fp1)) == len("sha256:") + 12

    def test_old_quality_records_untouched_by_latest_fallback(self, tmp_path):
        """旧质量记录无 after 字段时，provenance.after_revision 必须是 null，不退回 latest。"""
        project = _make_project(tmp_path)
        create_revision(project.root, "r0001")
        create_revision(project.root, "r0002 will be latest")
        from openbrep.quality.evaluator import _provenance

        class _StubResult:
            metadata = {"before_revision_id": "r0001"}
            object_plan = {}

        prov = _provenance(_StubResult(), {"after_revision": None, "delivery_source": None})
        assert prov["before_revision"] == "r0001"
        assert prov["after_revision"] is None
        assert prov["delivery_source"] is None

    def test_finalize_classify_unchanged_vs_partial(self):
        base = dict(run_id="r1", project=None, intent="MODIFY")
        ds, _ = finalize_delivery(FinalizeDeliveryInputs(
            **base, claimed_change=True, changed_files=[], verified=True,
        ))
        assert ds.state == "unchanged"
        ds2, _ = finalize_delivery(FinalizeDeliveryInputs(
            **base, claimed_change=True, changed_files=["scripts/3d.gdl"],
            interrupted=True, verified=False, before_revision_id="r0001",
        ))
        assert ds2.state == "partial_change"
        assert ds2.after_revision_id is None
        assert ds2.before_revision_id == "r0001"


# ── modify_acceptance 集成：revision 检查可引用 after ──────


class TestModifyAcceptanceIntegration:
    def test_acceptance_revision_check_uses_before(self, tmp_path):
        project = _make_project(tmp_path)
        pipeline = _make_pipeline(tmp_path)
        mock_llm = _agent_loop_llm_updates("BLOCK A, B, ZZYZX\nADDZ ZZYZX\nBLOCK A, B, 0.02\nDEL 1\nEND\n")
        pipeline._make_llm = lambda _req: mock_llm
        with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
            result = pipeline.execute(_request(project, tmp_path, "加一层层板"))
        acceptance = (result.metadata or {}).get("acceptance") or {}
        checks = acceptance.get("checks") or []
        rev_checks = [c for c in checks if c.get("name") == "revision"]
        ds = _delivery(result)
        if rev_checks and ds.before_revision_id:
            assert ds.before_revision_id in rev_checks[0].get("detail", "")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
