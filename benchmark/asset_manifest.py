"""评测素材 manifest（U00-B）：asset_id/hash/license/split/scenario/expected 来源。

数据 manifest 的 schema（版本 1）::

    {
      "schema_version": 1,
      "assets": [
        {
          "asset_id": "cabinet-w1-front",      # 唯一
          "object_id": "cabinet-w1",           # 独立对象身份：图像变体共享同一 object_id
          "kind": "hsf" | "image" | "drawing",
          "path": "cabinets/cabinet-w1",        # 相对 manifest 所在目录（可选拆分）
          "sha256": "…64 位…",                  # 对 path 内容（目录=清单文件并集）
          "license": "owner" | "cc0" | "mit" | "cc-by-4.0" | …（非空）
          "split": "dev" | "heldout",           # 开发 / 留出，同一 object_id 不得跨 split
          "scenario": "cabinet" | "lattice_window" | "furniture" | "generic",
          "variants": ["front", "angle"],       # kind=image 时的变体标签（可空）
          "expectations": {                     # 构件预期（须有建筑师确认记录）
            "source": "architect-2026-10-07",
            "confirmed": true,
            "required_params": ["A", "B", "ZZYZX"],
            "tolerances_mm": {"A": 5.0}
          },
          "checks": ["compile", "params", "behavior", "host"]
        }
      ]
    }

校验器是纯函数：重复 ID、缺许可、split 非法、对象跨 split（开发/留出交叉）、
变体泄漏（同 object_id 的图变体被当成多个对象）、hash 不匹配（给 root 时实算）、
预期缺确认记录。只校验不修——素材入库与否由维护者授权（有明确授权才纳入
仓库 fixture；原始私有素材留本地）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 1
VALID_KINDS = {"hsf", "image", "drawing"}
VALID_SPLITS = {"dev", "heldout"}
VALID_SCENARIOS = {"cabinet", "lattice_window", "furniture", "generic"}
VALID_CHECKS = {"compile", "params", "behavior", "host", "visual"}


def load_asset_manifest(path: str | Path) -> dict[str, Any]:
    """读 manifest JSON；坏 JSON / 顶层形状不对抛 ValueError（指名文件）。"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: manifest 不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise ValueError(f"{path.name}: manifest 顶层必须是 {{\"assets\": [...]}}")
    return data


def validate_manifest(
    data: dict[str, Any], *, root: str | Path | None = None
) -> list[str]:
    """校验 manifest，返回人类可读问题列表（空 = 通过）。纯函数，不改数据。

    root 给定时对每个资产实算 hash 比对（目录 = 递归文件并集的 sha256），
    文件缺失也算问题；不给 root 跳过实算（schema 校验照常）。
    """
    issues: list[str] = []
    if int(data.get("schema_version") or 0) != MANIFEST_SCHEMA_VERSION:
        issues.append(
            f"schema_version 必须是 {MANIFEST_SCHEMA_VERSION}，实际 {data.get('schema_version')!r}"
        )
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        issues.append("assets 必须是非空数组")
        return issues

    seen_ids: set[str] = set()
    object_splits: dict[str, set[str]] = {}
    for idx, asset in enumerate(assets):
        where = f"assets[{idx}]"
        if not isinstance(asset, dict):
            issues.append(f"{where}: 必须是对象")
            continue
        asset_id = str(asset.get("asset_id") or "").strip()
        object_id = str(asset.get("object_id") or asset_id).strip()
        if not asset_id:
            issues.append(f"{where}: 缺 asset_id")
        elif asset_id in seen_ids:
            issues.append(f"{where}: asset_id 重复：{asset_id!r}")
        seen_ids.add(asset_id)

        kind = str(asset.get("kind") or "")
        if kind not in VALID_KINDS:
            issues.append(f"{where}({asset_id}): kind 非法：{kind!r}（允许 {sorted(VALID_KINDS)}）")
        split = str(asset.get("split") or "")
        if split not in VALID_SPLITS:
            issues.append(f"{where}({asset_id}): split 非法：{split!r}（允许 {sorted(VALID_SPLITS)}）")
        scenario = str(asset.get("scenario") or "")
        if scenario not in VALID_SCENARIOS:
            issues.append(
                f"{where}({asset_id}): scenario 非法：{scenario!r}（允许 {sorted(VALID_SCENARIOS)}）"
            )
        if not str(asset.get("license") or "").strip():
            issues.append(f"{where}({asset_id}): 缺 license（无许可的素材不得入库）")

        if object_id:
            object_splits.setdefault(object_id, set()).add(split)
        # 变体泄漏：变体标签存在但 object_id 缺失/等于 asset_id 自身且 kind=image
        variants = asset.get("variants")
        if isinstance(variants, list) and variants and kind == "image":
            if not object_id or object_id == asset_id:
                issues.append(
                    f"{where}({asset_id}): 图像变体必须共享 object_id，"
                    "否则变体会被重复计成独立对象（泄漏）"
                )

        checks = asset.get("checks")
        if not isinstance(checks, list) or not checks:
            issues.append(f"{where}({asset_id}): checks 为空（至少声明一项验收来源）")
        else:
            for c in checks:
                if str(c) not in VALID_CHECKS:
                    issues.append(
                        f"{where}({asset_id}): check 非法：{c!r}（允许 {sorted(VALID_CHECKS)}）"
                    )

        expectations = asset.get("expectations")
        if not isinstance(expectations, dict) or not expectations:
            issues.append(f"{where}({asset_id}): 缺 expectations（构件预期）")
        else:
            if not str(expectations.get("source") or "").strip():
                issues.append(f"{where}({asset_id}): expectations.source 缺失（预期来源）")
            if expectations.get("confirmed") is not True:
                issues.append(
                    f"{where}({asset_id}): 建筑师预期未经确认记录（confirmed!=true）——"
                    "不能把预期当实测"
                )

        path_value = str(asset.get("path") or "").strip()
        sha = str(asset.get("sha256") or "").strip().lower()
        if not path_value:
            issues.append(f"{where}({asset_id}): 缺 path")
        elif not sha or len(sha) != 64:
            issues.append(f"{where}({asset_id}): sha256 缺失或不是 64 位")

    # 开发/留出对象不交叉（同一 object_id 出现两个 split = 交叉）
    for object_id, splits in sorted(object_splits.items()):
        bad = splits - {"dev", "heldout"}  # 非法 split 已在上面报过
        if len(splits & VALID_SPLITS) > 1:
            issues.append(f"object_id={object_id!r}: 同时出现在 dev 与 heldout（对象交叉）")
        if bad:
            issues.append(f"object_id={object_id!r}: 含非法 split {sorted(bad)}")

    if root is not None:
        issues.extend(_verify_hashes(assets, Path(root)))
    return issues


def _verify_hashes(assets: list[Any], root: Path) -> list[str]:
    """实算 path 内容 hash 比对（缺失/不匹配都算问题）。"""
    issues: list[str] = []
    for idx, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("asset_id")
        path_value = str(asset.get("path") or "").strip()
        sha = str(asset.get("sha256") or "").strip().lower()
        target = root / path_value if path_value else None
        if target is None or not target.exists():
            issues.append(f"assets[{idx}]({asset_id}): 素材文件缺失：{path_value!r}")
            continue
        actual = _hash_path(target)
        if actual and actual != sha:
            issues.append(
                f"assets[{idx}]({asset_id}): hash 不匹配（manifest={sha[:12]}… 实算={actual[:12]}…）"
            )
    return issues


def _hash_path(target: Path) -> str | None:
    """文件 = 内容 sha256；目录 = 递归文件并集（相对路径+字节）流式 sha256。"""
    digest = hashlib.sha256()
    if target.is_file():
        digest.update(target.read_bytes())
        return digest.hexdigest()
    if not target.is_dir():
        return None
    for file in sorted(p for p in target.rglob("*") if p.is_file()):
        rel = file.relative_to(target).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
