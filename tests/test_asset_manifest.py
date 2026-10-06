"""U00-B 素材 manifest 校验器合同测试（benchmark/asset_manifest.py）。

覆盖派单验收：重复 ID / hash 不匹配 / 缺许可 / 缺确认记录 / 开发与留出
对象不交叉 / 图像变体不重复计独立对象（泄漏）。
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.asset_manifest import load_asset_manifest, validate_manifest


def _asset(**overrides) -> dict:
    base = {
        "asset_id": "cabinet-w1",
        "object_id": "cabinet-w1",
        "kind": "hsf",
        "path": "cabinets/cabinet-w1",
        "sha256": "a" * 64,
        "license": "owner",
        "split": "dev",
        "scenario": "cabinet",
        "variants": [],
        "expectations": {
            "source": "architect-2026-10-07",
            "confirmed": True,
            "required_params": ["A", "B", "ZZYZX"],
        },
        "checks": ["compile", "params"],
    }
    base.update(overrides)
    return base


def _manifest(*assets: dict) -> dict:
    return {"schema_version": 1, "assets": list(assets)}


def test_valid_manifest_passes():
    issues = validate_manifest(_manifest(_asset()))
    assert issues == []


def test_duplicate_asset_id_rejected():
    issues = validate_manifest(_manifest(_asset(), _asset(asset_id="cabinet-w1", split="heldout",
                                                            object_id="cabinet-w1-b")))
    assert any("重复" in i for i in issues)


def test_missing_license_rejected():
    issues = validate_manifest(_manifest(_asset(license="")))
    assert any("license" in i for i in issues)


def test_object_must_not_cross_dev_and_heldout():
    issues = validate_manifest(_manifest(
        _asset(asset_id="a-front"),
        _asset(asset_id="a-angle", split="heldout"),
    ))
    assert any("dev 与 heldout" in i for i in issues)


def test_image_variant_without_shared_object_id_is_leakage():
    issues = validate_manifest(_manifest(
        _asset(asset_id="img-1", kind="image", object_id="img-1",
               variants=["front"], path="images/img-1.png"),
    ))
    assert any("泄漏" in i or "object_id" in i for i in issues)


def test_image_variants_sharing_object_id_pass():
    issues = validate_manifest(_manifest(
        _asset(asset_id="cabinet-w1-front", kind="image", object_id="cabinet-w1",
               variants=["front"], path="images/w1-front.png"),
        _asset(asset_id="cabinet-w1-angle", kind="image", object_id="cabinet-w1",
               variants=["angle"], path="images/w1-angle.png"),
    ))
    assert not any("泄漏" in i for i in issues)
    assert not any("dev 与 heldout" in i for i in issues)


def test_unconfirmed_expectation_rejected():
    issues = validate_manifest(_manifest(
        _asset(expectations={"source": "unknown", "confirmed": False}),
    ))
    assert any("确认" in i for i in issues)


def test_hash_mismatch_detected_when_root_given(tmp_path):
    from benchmark.asset_manifest import _hash_path

    target = tmp_path / "cabinets" / "cabinet-w1"
    target.mkdir(parents=True)
    (target / "paramlist.xml").write_text("<parameters/>", encoding="utf-8")
    actual = _hash_path(target)
    good = validate_manifest(_manifest(_asset(sha256=actual)), root=tmp_path)
    assert good == []
    bad = validate_manifest(_manifest(_asset(sha256="b" * 64)), root=tmp_path)
    assert any("hash 不匹配" in i for i in bad)


def test_missing_file_detected(tmp_path):
    issues = validate_manifest(_manifest(_asset()), root=tmp_path)
    assert any("缺失" in i for i in issues)


def test_load_manifest_rejects_bad_shape(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("not json", encoding="utf-8")
    try:
        load_asset_manifest(p)
        assert False, "should raise"
    except ValueError as exc:
        assert "m.json" in str(exc)
    p.write_text(json.dumps({"schema_version": 1, "assets": [_asset()]}), encoding="utf-8")
    data = load_asset_manifest(p)
    assert validate_manifest(data) == []
