"""U03-B 绑定合同测试（openbrep/contracts/bindings.py）。

覆盖派单验收：仅 spec 变化 / 材质图像场景变化 / 错 GSM 使对应证据失效；
字段顺序变化不无故失效；楼梯证据兼容；本地预览不伪装宿主证据；
canonical hash 的空值/浮点/顺序规范。
"""

from __future__ import annotations

import pytest

from openbrep.contracts.bindings import (
    EvidenceBinding,
    FreshnessReport,
    GuardBinding,
    canonical_hash,
    evidence_binding_from_host_record,
    evidence_binding_from_stair_contract,
    evaluate_evidence_freshness,
    get_executor_spec,
    register_executor_spec,
    requirement_satisfied_by_evidence,
    reset_executor_specs_for_tests,
)


def _guard(**overrides) -> GuardBinding:
    base = {
        "source_fingerprint": "sha256:abc",
        "project_epoch": 3,
        "draft_id": "",
        "requirement_ids": ["req-compile"],
        "plan_hash": "",
    }
    base.update(overrides)
    return GuardBinding(**base)


def _evidence(kind: str = "compile", **overrides) -> EvidenceBinding:
    base = {
        "guard": _guard(),
        "evidence_kind": kind,
        "executor_id": "compile",
        "gsm_fingerprint": "gsm-1",
        "parameter_values_hash": "params-1",
        "dependencies_hash": "deps-1",
        "scene_refs": ["scene-a"],
        "renderer_context": {"engine": "playwright-chromium", "viewport": [1400, 950]},
    }
    base.update(overrides)
    return EvidenceBinding(**base)


@pytest.fixture(autouse=True)
def _clean():
    reset_executor_specs_for_tests()
    yield
    reset_executor_specs_for_tests()


# ── canonical hash 规范 ──────────────────────────────────────


def test_hash_is_field_order_insensitive():
    """dict 键序不敏感（列表元素序语义敏感，保持保序）。"""
    a = {"x": 1, "y": {"a": [1, 2], "b": "s"}, "z": None}
    b = {"z": None, "y": {"b": "s", "a": [1, 2]}, "x": 1}
    assert canonical_hash(a) == canonical_hash(b)
    # 列表元素序语义敏感：换序是不同内容
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})


def test_hash_normalizes_floats_and_negative_zero():
    assert canonical_hash({"v": 1.0}) == canonical_hash({"v": 1})
    assert canonical_hash({"v": -0.0}) == canonical_hash({"v": 0.0})
    assert canonical_hash({"v": 0.1234567890123456}) == canonical_hash({"v": 0.123456789012})


def test_hash_distinguishes_none_and_empty_string():
    assert canonical_hash({"v": None}) != canonical_hash({"v": ""})


# ── fresh/stale 判定 ─────────────────────────────────────────


def test_source_change_stales_all_evidence_kinds():
    for kind in ("compile", "static", "visual", "host", "semantic"):
        report = evaluate_evidence_freshness(
            _evidence(kind), current_source_fingerprint="sha256:changed"
        )
        assert not report.fresh
        assert "source_changed" in report.stale_reasons


def test_scene_change_stales_visual_and_host_but_not_compile():
    changed = ["scene-B"]
    assert not evaluate_evidence_freshness(_evidence("visual"), current_scene_refs=changed).fresh
    assert not evaluate_evidence_freshness(_evidence("host"), current_scene_refs=changed).fresh
    compile_report = evaluate_evidence_freshness(_evidence("compile"), current_scene_refs=changed)
    assert compile_report.fresh  # compile 对场景不敏感
    static_report = evaluate_evidence_freshness(_evidence("static"), current_scene_refs=changed)
    assert static_report.fresh


def test_wrong_gsm_stales_compile_visual_host_but_not_static():
    assert not evaluate_evidence_freshness(_evidence("compile"), current_gsm_fingerprint="gsm-2").fresh
    assert not evaluate_evidence_freshness(_evidence("visual"), current_gsm_fingerprint="gsm-2").fresh
    assert not evaluate_evidence_freshness(_evidence("host"), current_gsm_fingerprint="gsm-2").fresh
    assert evaluate_evidence_freshness(_evidence("static"), current_gsm_fingerprint="gsm-2").fresh


def test_epoch_and_plan_guard_changes_stale_everything():
    report = evaluate_evidence_freshness(_evidence("static"), current_project_epoch=9)
    assert not report.fresh and "epoch_changed" in report.stale_reasons
    guarded = _evidence("static", guard=_guard(plan_hash="plan-1"))
    report = evaluate_evidence_freshness(guarded, current_plan_hash="plan-2")
    assert not report.fresh and "plan_changed" in report.stale_reasons


def test_unprovided_dimensions_do_not_fake_verification():
    """当前值未提供的维度跳过——不假装核对（unknown/not_run 不冒充通过）。"""
    report = evaluate_evidence_freshness(_evidence("compile"))
    assert report.fresh and report.stale_reasons == []


def test_fresh_binding_stays_fresh():
    report = evaluate_evidence_freshness(
        _evidence("host"),
        current_source_fingerprint="sha256:abc",
        current_gsm_fingerprint="gsm-1",
        current_parameter_values_hash="params-1",
        current_dependencies_hash="deps-1",
        current_scene_refs=["scene-a"],
        current_project_epoch=3,
    )
    assert report.fresh


# ── 本地预览不伪装宿主证据 ────────────────────────────────────


def test_local_preview_never_satisfies_host_requirement():
    preview = _evidence("visual")
    report = requirement_satisfied_by_evidence("host", preview)
    assert not report.fresh
    assert report.not_valid_for == "host"
    assert any("mismatch" in r for r in report.stale_reasons)


def test_kind_gate_passes_for_matching_kind():
    assert requirement_satisfied_by_evidence("visual", _evidence("visual")).fresh


# ── 执行器规格注册 ────────────────────────────────────────────


def test_executor_spec_registration_and_lookup():
    spec = get_executor_spec("compile")
    assert spec is not None and spec.result_kind == "check_result"
    assert register_executor_spec("behavior", required_bindings=frozenset({"source", "gsm"}),
                                  result_kind="check_result") is True
    behavior = get_executor_spec("behavior")
    assert behavior is not None and "gsm" in behavior.required_bindings
    assert register_executor_spec("behavior", required_bindings=frozenset({"source", "gsm"}),
                                  result_kind="check_result") is False  # 幂等


def test_u03a_registry_delegates_to_bindings():
    from openbrep.contracts.object_spec import (
        known_check_executor,
        register_check_executor,
        reset_check_executors_for_tests,
    )

    reset_check_executors_for_tests()
    assert known_check_executor("compile")
    assert not known_check_executor("future_check")
    assert register_check_executor("future_check") is True
    assert known_check_executor("future_check")
    spec = get_executor_spec("future_check")
    assert spec is not None and spec.required_bindings == frozenset({"source"})


# ── 读适配 ────────────────────────────────────────────────────


def test_host_record_read_adapter():
    record = {
        "record_id": "r1",
        "status": "passed",
        "identity_status": "verified",
        "evidence_save_status": "saved",
        "gsm_sha256": "gsm-host",
        "source_fingerprint_after_compile": "sha256:abc",
        "finished_at": "2026-10-07T00:00:00Z",
        "unknown_future_field": {"nested": 1},
    }
    binding = evidence_binding_from_host_record(record, guard=_guard())
    assert binding.evidence_kind == "host"
    assert binding.executor_id == "host_run"
    assert binding.gsm_fingerprint == "gsm-host"
    assert binding.result_facts["status"] == "passed"
    assert binding.result_facts["unknown_future_field"] == {"nested": 1}
    # 绑定 hash 稳定（顺序不敏感）
    assert binding.binding_hash() == evidence_binding_from_host_record(
        dict(reversed(list(record.items()))), guard=_guard()
    ).binding_hash()


def test_stair_contract_evidence_compatible():
    binding = evidence_binding_from_stair_contract(
        "stair-contract-hash",
        guard=_guard(),
        report_facts={"applicability": "applicable", "passed": True},
    )
    assert binding.evidence_kind == "contract"
    report = evaluate_evidence_freshness(
        binding, current_source_fingerprint="sha256:abc",
        current_contract_hash="stair-contract-hash",
    )
    assert report.fresh
    changed = evaluate_evidence_freshness(
        binding, current_source_fingerprint="sha256:abc",
        current_contract_hash="stair-contract-changed",
    )
    assert not changed.fresh and "contract_changed" in changed.stale_reasons


def test_binding_hash_stable_under_dict_order():
    """dict 键序与浮点形态变化不影响绑定 hash（列表元素序语义敏感、保序）。"""
    guard = _guard(requirement_ids=["req-a", "req-b"])
    b1 = _evidence("visual", guard=guard, renderer_context={"a": 1, "b": [1.0, 2]})
    b2 = _evidence("visual", guard=guard, renderer_context={"b": [1, 2.0], "a": 1})
    assert b1.binding_hash() == b2.binding_hash()
