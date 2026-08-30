"""MODIFY 交付物升级（V5）：确定性自然语言验收摘要 + 修改前后预览对比。

用户不看代码，系统就必须替他证明改对了（验证层=信任层）。每次 MODIFY 成功后
交付：① 确定性生成的自然语言验收摘要（改了什么参数、什么行为、编译/验证结论）；
② 修改前后 2D/3D 预览的轻量几何摘要对比。

- preview_geometry_summary(project)：取当前项目 3D/2D 预览的轻量几何摘要
  （mesh 数、包围盒、2D 元素计数），不存大图；渲染异常降级 available=False。
- build_modify_acceptance(...)：纯函数，从结构化数据生成验收 dict：
  {summary_lines[], geometry_delta, checks[]}。中文模板句，仅当变化可计算时
  才写，算不出就不写、不硬凑。v1 不调 LLM（确定性、零成本、可测试）。
"""

from __future__ import annotations

from typing import Any, Optional

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.project_parameter_service import to_preview_number


# ── 轻量几何摘要（只读消费预览，不改渲染器/返回结构） ──────

def preview_geometry_summary(project: HSFProject) -> dict[str, Any]:
    """对当前项目取 3D/2D 预览的轻量几何摘要；渲染异常时 available=False。

    摘要只含小字段（计数 + 包围盒），不存大图/顶点数据。
    """
    summary: dict[str, Any] = {
        "available": False,
        "reason": "",
        "mesh_count": None,
        "bbox": None,  # {"min": [x, y, z], "max": [x, y, z]}
        "line_count": None,
        "polygon_count": None,
        "circle_count": None,
        "arc_count": None,
    }
    parameters = {
        p.name.upper(): to_preview_number(p.value)
        for p in project.parameters
        if to_preview_number(p.value) is not None
    }
    errors: list[str] = []

    try:
        result_3d = preview_3d_script(
            project.get_script(ScriptType.SCRIPT_3D) or "",
            parameters=parameters,
            setup_script=project.get_script(ScriptType.MASTER) or "",
            unknown_command_policy="warn",
            quality="fast",
        )
        meshes = result_3d.meshes or []
        summary["mesh_count"] = len(meshes)
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for mesh in meshes:
            xs.extend(mesh.x)
            ys.extend(mesh.y)
            zs.extend(mesh.z)
        if xs:
            summary["bbox"] = {
                "min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
                "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
            }
    except Exception as exc:
        errors.append(f"3D 预览不可用：{exc}")

    try:
        result_2d = preview_2d_script(
            project.get_script(ScriptType.SCRIPT_2D) or "",
            parameters=parameters,
            setup_script=project.get_script(ScriptType.MASTER) or "",
            unknown_command_policy="warn",
            quality="fast",
        )
        summary["line_count"] = len(result_2d.lines)
        summary["polygon_count"] = len(result_2d.polygons)
        summary["circle_count"] = len(result_2d.circles)
        summary["arc_count"] = len(result_2d.arcs)
    except Exception as exc:
        errors.append(f"2D 预览不可用：{exc}")

    if errors:
        summary["reason"] = "；".join(errors)
    else:
        summary["available"] = True
    return summary


# ── 验收摘要生成（纯函数，模板句） ─────────────────────────

def _bbox_size(bbox: Optional[dict]) -> Optional[list[float]]:
    """包围盒尺寸 [宽, 深, 高]；bbox 缺失返回 None。"""
    if not bbox or "min" not in bbox or "max" not in bbox:
        return None
    return [round(max - min, 3) for min, max in zip(bbox["min"], bbox["max"])]


def _fmt_bbox(size: Optional[list[float]]) -> str:
    return "×".join(f"{v:g}" for v in size) if size else "—"


def _counts_2d(summary: Optional[dict]) -> Optional[dict]:
    """2D 元素计数；2D 预览不可用返回 None。"""
    if not summary or summary.get("line_count") is None:
        return None
    return {
        "lines": summary.get("line_count") or 0,
        "polygons": summary.get("polygon_count") or 0,
        "circles": summary.get("circle_count") or 0,
        "arcs": summary.get("arc_count") or 0,
    }


def _fmt_counts(counts: Optional[dict]) -> str:
    if not counts:
        return "—"
    return (
        f"{counts['lines']}/{counts['polygons']}/"
        f"{counts['circles']}/{counts['arcs']}"
    )


def _geometry_delta(
    before: Optional[dict], after: Optional[dict], changed_files: Optional[list[str]] = None
) -> tuple[list[str], dict[str, Any]]:
    """前后几何摘要对比：返回 (摘要行, geometry_delta 结构化对比)。

    HF3：几何未变时文案不得呈现失败读法——新增可选样式类 MODIFY
    （vl.gdl VALUES 新枚举 / 新分支）当前参数未命中新分支时几何不变是正确
    行为。改为中性"当前参数下几何未变化"；若参数定义面（vl.gdl / paramlist.xml）
    确实有落盘变更，追加参数面板提示行，提醒用户切换新选项查看效果。
    """
    lines: list[str] = []
    delta: dict[str, Any] = {"status": "ok", "reason": ""}

    if before is None or not before.get("available"):
        delta["status"] = "before_unavailable"
        delta["reason"] = (before or {}).get("reason") or "修改前预览不可用"
        lines.append("修改前预览不可用")
        return lines, delta
    if after is None or not after.get("available"):
        delta["status"] = "after_unavailable"
        delta["reason"] = (after or {}).get("reason") or "修改后预览不可用"
        lines.append("修改后预览不可用")
        return lines, delta

    mesh_from = before.get("mesh_count")
    mesh_to = after.get("mesh_count")
    bbox_from = _bbox_size(before.get("bbox"))
    bbox_to = _bbox_size(after.get("bbox"))
    counts_from = _counts_2d(before)
    counts_to = _counts_2d(after)

    changed = False
    if mesh_from is not None and mesh_to is not None and mesh_from != mesh_to:
        lines.append(f"几何体数量从 {mesh_from} 变为 {mesh_to}")
        delta["mesh_count"] = {"from": mesh_from, "to": mesh_to}
        changed = True
    if bbox_from is not None and bbox_to is not None and bbox_from != bbox_to:
        lines.append(
            f"包围盒尺寸（宽×深×高）从 {_fmt_bbox(bbox_from)} 变为 {_fmt_bbox(bbox_to)}"
        )
        delta["bbox_size"] = {"from": bbox_from, "to": bbox_to}
        changed = True
    if counts_from is not None and counts_to is not None and counts_from != counts_to:
        lines.append(
            f"平面元素数量（线/多边形/圆/弧）从 {_fmt_counts(counts_from)} 变为 {_fmt_counts(counts_to)}"
        )
        delta["counts_2d"] = {"from": counts_from, "to": counts_to}
        changed = True

    if not changed:
        delta["status"] = "unchanged"
        lines.append("当前参数下几何未变化")
        # HF3：参数定义面确实落盘（枚举/参数定义更新）→ 给出中性操作提示，
        # 避免"几何未变化"被读成修改失败。文件名单取自真实 changed_files。
        param_files = sorted(
            {
                f.split('/')[-1] if '/' in f else f
                for f in (changed_files or [])
                if (f.split('/')[-1] if '/' in f else f) in ("vl.gdl", "paramlist.xml")
            }
        )
        if param_files:
            lines.append(
                f"参数/枚举定义已更新（{'、'.join(param_files)}）："
                f"在参数面板切换新选项可查看效果，无需重开项目"
            )
    return lines, delta


def build_modify_acceptance(
    *,
    before: Optional[dict] = None,          # preview_geometry_summary 输出
    after: Optional[dict] = None,
    parameter_changes: Optional[list[dict]] = None,  # [{name, from, to}]
    changed_files: Optional[list[str]] = None,
    compile_result: Any = None,             # 有 .success 即可
    semantic_issues: Optional[list[str]] = None,  # 阻塞几何语义问题 detail 列表
    revision_id: Optional[str] = None,
    revision_warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """从结构化数据生成验收报告 dict（纯函数，不调 LLM）。

    返回 {summary_lines[], geometry_delta, checks[]}：
    - summary_lines：中文模板句（参数变更 / 文件 / 几何变化）
    - geometry_delta：结构化前后对比（前端渲染双栏）
    - checks：编译 / 语义 / 版本快照 / 预览状态
    """
    summary_lines: list[str] = []

    for change in parameter_changes or []:
        name = str(change.get("name") or "")
        to = change.get("to")
        if name and to is not None:
            summary_lines.append(f"参数 {name} 从 {change.get('from')} 改为 {to}")

    files = sorted({f for f in (changed_files or []) if f})
    if files:
        summary_lines.append("已修改文件：" + "、".join(files))
    else:
        # AC-2（HF4）：零文件交付绝不呈现为"成功修改"——首行显性中性警示。
        # 真实事件：模型误解指代指令（history 断裂）→ 零 changed_files 零
        # [FILE:]，但 compile/semantic 全绿后被包装成"检查通过"式成功。
        summary_lines.insert(0, "本次未修改任何文件（如预期有修改，请检查指令或重试）")

    geometry_lines, geometry_delta = _geometry_delta(before, after, changed_files)
    if not files and geometry_lines:
        # 零产出去重：HF3"当前参数下几何未变化"与 AC-2 首行都表达"什么都没
        # 发生"，不并列两条易混文案；几何**变化**行（数量/包围盒/平面元素）
        # 及预览不可用诊断仍如实呈现。HF3 的参数面板提示（vl.gdl/paramlist.xml
        # 落盘）只随 changed_files 出现，零产出时必然为空，无需额外去重。
        geometry_lines = [ln for ln in geometry_lines if ln != "当前参数下几何未变化"]
    summary_lines.extend(geometry_lines)

    checks: list[dict[str, str]] = []
    if compile_result is None:
        checks.append({"name": "compile", "status": "not_run", "detail": "未执行编译"})
    elif compile_result.success:
        checks.append({"name": "compile", "status": "pass", "detail": "编译通过"})
    else:
        checks.append({"name": "compile", "status": "fail", "detail": "编译失败"})

    if semantic_issues is None:
        checks.append({"name": "semantic", "status": "not_run", "detail": "未执行几何语义验证"})
    elif not semantic_issues:
        checks.append({"name": "semantic", "status": "pass", "detail": "几何语义验证通过"})
    else:
        checks.append(
            {"name": "semantic", "status": "fail", "detail": f"{len(semantic_issues)} 个阻塞问题"}
        )

    if revision_id:
        checks.append({"name": "revision", "status": "pass", "detail": f"版本快照 {revision_id} 已创建"})
    else:
        checks.append({"name": "revision", "status": "skipped", "detail": "未创建版本快照"})

    checks.extend(_preview_checks(before, after))

    return {
        "summary_lines": summary_lines,
        "geometry_delta": geometry_delta,
        "checks": checks,
    }


def _preview_checks(before: Optional[dict], after: Optional[dict]) -> list[dict[str, str]]:
    """3D/2D 预览状态检查（before/after 各取可用侧；不可用如实标注）。"""
    checks: list[dict[str, str]] = []
    for label, key in (("3D 预览", "mesh_count"), ("2D 预览", "line_count")):
        before_ok = bool(before and before.get("available") and before.get(key) is not None)
        after_ok = bool(after and after.get("available") and after.get(key) is not None)
        if not before_ok and not after_ok:
            reason = ""
            if before and before.get("reason"):
                reason = before["reason"]
            elif after and after.get("reason"):
                reason = after["reason"]
            checks.append(
                {"name": label, "status": "fail", "detail": f"预览不可用{('：' + reason) if reason else ''}"}
            )
            continue
        before_n = before.get(key) if before_ok else "不可用"
        after_n = after.get(key) if after_ok else "不可用"
        status = "pass" if after_ok else "warn"
        checks.append(
            {"name": label, "status": status, "detail": f"{before_n} → {after_n}"}
        )
    return checks
