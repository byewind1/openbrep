"""P0-A 执行效果契约（2026-10-04 漏窗诊断 R1）：把"用户本轮要看到什么变化"
变成可判定的证据门。

背景：MODIFY 的执行成功此前只绑定"文件变化 + 通用编译/语义检查"。模型改注释、
写等价算式（`_hui_step * 2` → `2 * _hui_step`），几何逐点不变，也会被记为
completed / verified_change / confidence=high——"编译成功"被提升成了"用户目标完成"。

本模块是纯函数层，不调 LLM、不写文件：

- normalize_effect_contract(raw)：校验/规范化调用方显式传入的契约。契约必须
  由 GUI/调用方显式给出（TaskRequest.effect_contract）；benchmark/CLI 不传
  → None，完成门行为与历史完全一致（不偷改无上下文语义）。
- compute_geometry_signature(meshes)：规范化几何签名——顶点按 1e-6 容差取整、
  三角面内顶点排序、三角面集合排序、mesh 顺序无关。等价源码改写签名稳定；
  真实顶点变化敏感；同数量不同形态能区分（这是 mesh_count/bbox 做不到的）。
- evaluate_effect_contract(...)：对 before/after 预览摘要判定目标效果是否
  达成。返回 {required, change_kind, satisfied, status, reason}；
  status ∈ satisfied / no_effect / unverifiable。无法可靠观察时如实报
  unverifiable（unknown），绝不把"看不清"升级成"没效果"或"有效果"。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

VALID_CHANGE_KINDS = ("geometry", "material", "parameter", "behavior", "new-option")

# 签名容差：GDL 单位为米，1e-6（微米级）吸收等价算式的浮点 rounding 差异，
# 对真实几何变化（毫米级以上）不敏感丢失。
_SIGNATURE_TOLERANCE = 6


def count_mesh_components(meshes: Any) -> Optional[int]:
    """P1-C：网格连通分量数（返回分量数；可靠性语义见 mesh_components_detail）。"""
    components, _reliable = mesh_components_detail(meshes)
    return components


def mesh_components_detail(meshes: Any) -> tuple[Optional[int], bool]:
    """F6（review 2026-10-04）：连通分量数 + 可靠性标记。

    连接判定 = 顶点共享聚类 **∪** 轴对齐包围盒接触/交叠（eps=1e-6）。
    顶点共享单独使用会漏掉"实体交叠但无重合角点"的合法连接（两个交叠
    BLOCK 被误判为断开——review F6 探针场景）；AABB 接触对轴对齐盒
    （BLOCK）是精确等价，对一般非凸网格则偏保守（可能高估连通性）。

    可靠性（第二返回值）：所有 mesh 都是轴对齐盒（去重顶点 8 个且全部
    落在 (min,max) 角点组合上）→ True，连通判定精确；否则 False——此时
    分量数只是保守近似，require_connected 契约必须按 unverifiable 处理，
    不得用顶点共享近似阻断交付。
    """
    parent: dict[tuple[int, int, int], tuple[int, int, int]] = {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    all_boxed = True
    for mesh in meshes or []:
        try:
            keys = [
                (round(float(x), _SIGNATURE_TOLERANCE), round(float(y), _SIGNATURE_TOLERANCE), round(float(z), _SIGNATURE_TOLERANCE))
                for x, y, z in zip(mesh.x, mesh.y, mesh.z)
            ]
        except (TypeError, ValueError):
            return None, False
        for key in keys:
            if key not in parent:
                parent[key] = key
        for i, j, k in zip(mesh.i, mesh.j, mesh.k):
            union(keys[i], keys[j])
            union(keys[j], keys[k])
        xs, ys, zs = zip(*keys)
        min_key = (min(xs), min(ys), min(zs))
        max_key = (max(xs), max(ys), max(zs))
        boxes.append((min_key, max_key))
        distinct = set(keys)
        corners = {
            (x, y, z)
            for x in (min_key[0], max_key[0])
            for y in (min_key[1], max_key[1])
            for z in (min_key[2], max_key[2])
        }
        if len(distinct) != 8 or not distinct <= corners:
            all_boxed = False
    if not parent:
        return None, False
    if all_boxed and len(boxes) > 1:
        # F6：轴对齐盒接触/交叠合并（含 eps 容差；接触也算连接——棂条拼花
        # 通常共面拼接）。全部 mesh 都是盒时该判定精确。
        eps = 1e-6
        box_comp = list(range(len(boxes)))

        def bfind(a: int) -> int:
            while box_comp[a] != a:
                box_comp[a] = box_comp[box_comp[a]]
                a = box_comp[a]
            return a

        for i in range(len(boxes)):
            min_i, max_i = boxes[i]
            for j in range(i + 1, len(boxes)):
                min_j, max_j = boxes[j]
                overlap = all(
                    min_i[axis] <= max_j[axis] + eps and min_j[axis] <= max_i[axis] + eps
                    for axis in range(3)
                )
                if overlap:
                    ri, rj = bfind(i), bfind(j)
                    if ri != rj:
                        box_comp[ri] = rj
        return len({bfind(i) for i in range(len(boxes))}), True
    ordered_roots = list({find(key) for key in parent})
    return len(ordered_roots), all_boxed


def normalize_effect_contract(raw: Any) -> Optional[dict]:
    """校验并规范化效果契约；无契约/非法契约返回 None（不猜测、不抛异常）。

    字段：
    - change_kind（必填）：geometry / material / parameter / behavior / new-option
    - target_branch（可选）：目标分支描述（如 "pattern_type=回纹"），只进审计
    - reference_asset_ids（可选）：本轮采用的参考资产 id 列表，只进审计
    - acceptance_items（可选）：关键验收项文本，只进审计
    - forbidden_scopes（可选）：禁止改动范围，只进审计
    - option_param / option_value（可选，new-option）：内存 override 验证新分支用
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("change_kind")
    if kind not in VALID_CHANGE_KINDS:
        return None
    contract: dict[str, Any] = {"change_kind": kind}
    if bool(raw.get("require_connected")):
        # P1-C：声明"结果必须是单一连通形态"（如一条连续回纹）——确定性
        # 连通性检查，非硬编码约束；仅显式声明才生效。
        contract["require_connected"] = True
    for key in ("target_branch", "option_param"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            contract[key] = value.strip()
    value = raw.get("option_value")
    if isinstance(value, str) and value.strip():
        contract["option_value"] = value.strip()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        contract["option_value"] = value
    for key in ("reference_asset_ids", "acceptance_items", "forbidden_scopes"):
        value = raw.get(key)
        if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
            contract[key] = [v.strip() for v in value][:20]
    return contract


def compute_geometry_signature(meshes: Any) -> Optional[str]:
    """对预览 mesh 列表计算规范化几何签名；空几何返回 None。

    规范化：顶点 1e-6 取整 → 三角面内三点排序 → 三角面集合排序 →
    各 mesh 内容 hash 排序后整体 hash。mesh/三角面顺序重排不算几何变化。
    """
    per_mesh: list[str] = []
    for mesh in meshes or []:
        try:
            verts = [
                (
                    round(float(x), _SIGNATURE_TOLERANCE),
                    round(float(y), _SIGNATURE_TOLERANCE),
                    round(float(z), _SIGNATURE_TOLERANCE),
                )
                for x, y, z in zip(mesh.x, mesh.y, mesh.z)
            ]
            tris = []
            for i, j, k in zip(mesh.i, mesh.j, mesh.k):
                tri = tuple(sorted((verts[i], verts[j], verts[k])))
                tris.append(json.dumps(tri, separators=(",", ":")))
            tris.sort()
            digest = hashlib.sha256("\n".join(tris).encode()).hexdigest()
            per_mesh.append(digest)
        except (TypeError, ValueError, IndexError):
            return None
    if not per_mesh:
        return None
    per_mesh.sort()
    return hashlib.sha256("|".join(per_mesh).encode()).hexdigest()[:16]


def _bbox_size(bbox: Optional[dict]) -> Optional[list[float]]:
    if not bbox or "min" not in bbox or "max" not in bbox:
        return None
    return [round(max - min, 3) for min, max in zip(bbox["min"], bbox["max"])]


def _counts_2d(summary: dict) -> Optional[dict]:
    if summary.get("line_count") is None:
        return None
    return {
        "lines": summary.get("line_count") or 0,
        "polygons": summary.get("polygon_count") or 0,
        "circles": summary.get("circle_count") or 0,
        "arcs": summary.get("arc_count") or 0,
    }


def evaluate_effect_contract(
    contract: Optional[dict],
    *,
    before: Optional[dict],
    after: Optional[dict],
    parameter_changes: Optional[list[dict]] = None,
    changed_files: Optional[list[str]] = None,
) -> Optional[dict]:
    """判定显式契约的目标效果是否达成；无契约返回 None（完成门不变）。

    change_kind 判定面：
    - geometry：当前参数下几何签名必须变化（签名不可用时如实 unverifiable）
    - material：材质映射变化（不要求几何 hash 变化，材质任务不被几何门误拦）
    - parameter：参数值发生变化
    - behavior：任一可观察行为面变化（几何/2D 计数）
    - new-option：参数定义面（vl.gdl/paramlist.xml）落盘变化；
      新增选项不强制改变当前参数下的几何（在内存 override 下另行验证）
    """
    if contract is None:
        return None
    kind = contract["change_kind"]
    result: dict[str, Any] = {
        "required": True,
        "change_kind": kind,
        "satisfied": False,
        "status": "unverifiable",
        "reason": "",
    }
    before_ok = bool(before and before.get("available"))
    after_ok = bool(after and after.get("available"))

    if kind == "parameter":
        if parameter_changes:
            result["satisfied"] = True
            result["status"] = "satisfied"
            result["reason"] = f"参数已变化（{len(parameter_changes)} 项）"
        else:
            result["status"] = "no_effect"
            result["reason"] = "本轮要求参数变化，但没有观察到任何参数值变化"
        return result

    if kind == "material":
        if not before_ok or not after_ok:
            result["reason"] = "预览不可用，无法验证材质效果"
            return result
        materials_before = before.get("materials")
        materials_after = after.get("materials")
        if materials_before is None or materials_after is None:
            result["reason"] = "材质预览数据缺失，无法验证材质效果"
            return result
        if materials_before != materials_after:
            result["satisfied"] = True
            result["status"] = "satisfied"
            result["reason"] = "材质预览数据发生变化"
        else:
            result["status"] = "no_effect"
            result["reason"] = "当前参数下材质预览数据未变化，未观察到材质效果"
        return result

    if kind == "new-option":
        param_files = sorted(
            {
                (f.split("/")[-1] if "/" in f else f)
                for f in (changed_files or [])
                if (f.split("/")[-1] if "/" in f else f) in ("vl.gdl", "paramlist.xml")
            }
        )
        if param_files:
            result["satisfied"] = True
            result["status"] = "satisfied"
            result["reason"] = f"参数定义面已更新（{'、'.join(param_files)}）；当前参数不命中新选项属预期行为"
        else:
            result["status"] = "no_effect"
            result["reason"] = "本轮要求新增可选项，但参数定义面（vl.gdl/paramlist.xml）没有任何落盘变化"
        return result

    # geometry / behavior：观察 3D 签名（主）与 2D 计数（辅）
    if not before_ok or not after_ok:
        result["reason"] = "预览不可用，无法验证几何效果"
        return result
    sig_before = before.get("geometry_signature")
    sig_after = after.get("geometry_signature")
    counts_before = _counts_2d(before)
    counts_after = _counts_2d(after)
    mesh_changed = before.get("mesh_count") != after.get("mesh_count")
    bbox_changed = _bbox_size(before.get("bbox")) != _bbox_size(after.get("bbox"))
    counts_changed = counts_before is not None and counts_after is not None and counts_before != counts_after
    if sig_before and sig_after:
        geometry_changed = sig_before != sig_after
    else:
        # 签名不可用（旧摘要/空几何）：不能可靠区分同数量不同形态，
        # 不把计数当签名用——如实报 unverifiable。
        geometry_changed = None
    if kind == "geometry":
        if geometry_changed is None:
            # 签名不可用（双侧无 3D mesh / 旧摘要）：计数不能证明形态，
            # 也不判定 no_effect——如实 unverifiable，由人工/截图复核。
            result["reason"] = "几何签名不可用，无法确认形态变化；不得宣称按图完成"
            return result
        if geometry_changed:
            # F6：require_connected 只消费可靠（全轴对齐盒）的连通证据；
            # 不可靠/不可求值时如实 unverifiable，绝不拿顶点共享近似阻断交付。
            components_after = after.get("mesh_components") if isinstance(after, dict) else None
            reliable = after.get("mesh_components_reliable", False) if isinstance(after, dict) else False
            if contract.get("require_connected") and (components_after is None or not reliable):
                result["status"] = "unverifiable"
                result["reason"] = (
                    "几何发生了变化，但连通性无法可靠求值（非轴对齐盒网格）——"
                    "连续目标是否达成需人工/截图复核，不自动判定"
                )
            elif contract.get("require_connected") and components_after > 1:
                result["status"] = "shape_check_failed"
                result["reason"] = (
                    f"几何发生了变化，但结果形成 {components_after} 个互不连通的组件"
                    f"（契约要求单一连通形态）；不能宣称连续目标已达成"
                )
            else:
                result["satisfied"] = True
                result["status"] = "satisfied"
                result["reason"] = "当前参数下几何发生实质变化（几何签名不同）"
        else:
            result["status"] = "no_effect"
            result["reason"] = (
                "本轮要求形态变化，但当前参数下几何与修改前完全一致（几何签名相同）；"
                "已写源码不等于用户目标完成"
            )
        return result

    # behavior：任一观察面变化即可
    observed = [c for c in (geometry_changed, mesh_changed, bbox_changed, counts_changed) if c]
    if observed:
        result["satisfied"] = True
        result["status"] = "satisfied"
        result["reason"] = "预览可观察行为面发生变化"
    elif geometry_changed is None and not mesh_changed and not bbox_changed and not counts_changed:
        result["status"] = "unverifiable"
        result["reason"] = "几何签名不可用且计数面无变化，无法确认行为效果"
    else:
        result["status"] = "no_effect"
        result["reason"] = "本轮要求行为变化，但预览的几何与 2D 观察面均未变化"
    return result
