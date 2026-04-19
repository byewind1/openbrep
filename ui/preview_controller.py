from __future__ import annotations

from datetime import datetime
from typing import Callable

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script


def collect_preview_prechecks(
    proj,
    target: str,
    *,
    check_gdl_script_fn: Callable[[str, str], list],
    validator_factory: Callable[[], object],
    dedupe_keep_order_fn: Callable[[list[str]], list[str]],
    script_type_2d,
    script_type_3d,
) -> list[str]:
    warns: list[str] = []

    if target in {"2d", "both"}:
        for msg in check_gdl_script_fn(proj.get_script(script_type_2d), "2d"):
            if not msg.startswith("✅"):
                warns.append(f"[check 2D] {msg}")
    if target in {"3d", "both"}:
        for msg in check_gdl_script_fn(proj.get_script(script_type_3d), "3d"):
            if not msg.startswith("✅"):
                warns.append(f"[check 3D] {msg}")

    try:
        v_issues = validator_factory().validate_all(proj)
        for issue in v_issues:
            if target == "2d" and not issue.startswith(("2d.gdl", "paramlist.xml")):
                continue
            if target == "3d" and not issue.startswith(("3d.gdl", "paramlist.xml")):
                continue
            warns.append(f"[validator] {issue}")
    except Exception as e:
        warns.append(f"[validator] 执行失败: {e}")

    return dedupe_keep_order_fn(warns)


def run_preview(
    proj,
    target: str,
    *,
    sync_visible_editor_buffers_fn: Callable[[object, int], bool],
    editor_version: int,
    preview_param_values_fn: Callable[[object], dict[str, float]],
    collect_preview_prechecks_fn: Callable[[object, str], list[str]],
    dedupe_keep_order_fn: Callable[[list[str]], list[str]],
    set_preview_2d_data_fn: Callable[[object], None],
    set_preview_3d_data_fn: Callable[[object], None],
    set_preview_warnings_fn: Callable[[list[str]], None],
    set_preview_meta_fn: Callable[[dict], None],
    script_type_2d,
    script_type_3d,
) -> tuple[bool, str]:
    sync_visible_editor_buffers_fn(proj, editor_version)
    params = preview_param_values_fn(proj)
    pre_warns = collect_preview_prechecks_fn(proj, target)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if target == "2d":
            res_2d = preview_2d_script(proj.get_script(script_type_2d), parameters=params)
            set_preview_2d_data_fn(res_2d)
            set_preview_warnings_fn(dedupe_keep_order_fn([*pre_warns, *res_2d.warnings]))
            set_preview_meta_fn({"kind": "2D", "timestamp": ts})
            return True, "✅ 2D 预览已更新"

        if target == "3d":
            res_3d = preview_3d_script(proj.get_script(script_type_3d), parameters=params)
            set_preview_3d_data_fn(res_3d)
            set_preview_warnings_fn(dedupe_keep_order_fn([*pre_warns, *res_3d.warnings]))
            set_preview_meta_fn({"kind": "3D", "timestamp": ts})
            return True, "✅ 3D 预览已更新"

        return False, f"❌ 未知预览类型: {target}"

    except Exception as e:
        set_preview_warnings_fn(dedupe_keep_order_fn([
            *pre_warns,
            f"[preview] 执行失败: {e}",
        ]))
        set_preview_meta_fn({"kind": target.upper(), "timestamp": ts})
        return False, f"❌ 预览失败: {e}"
