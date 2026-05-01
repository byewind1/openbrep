from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script


def clear_pending_preview_state(session_state) -> None:
    session_state.pending_preview_2d_data = None
    session_state.pending_preview_3d_data = None
    session_state.pending_preview_warnings = []
    session_state.pending_preview_meta = {"kind": "", "timestamp": "", "source": ""}


def build_project_with_pending_diffs(
    proj,
    pending_diffs: dict,
    *,
    script_map: list[tuple[object, str, str]],
    parse_paramlist_text_fn: Callable[[str], list],
    deepcopy_fn=deepcopy,
):
    proposed = deepcopy_fn(proj)
    for stype, fpath, _label in script_map:
        if fpath in pending_diffs:
            proposed.set_script(stype, pending_diffs[fpath] or "")

    if "paramlist.xml" in pending_diffs:
        parsed_params = parse_paramlist_text_fn(pending_diffs.get("paramlist.xml", "") or "")
        if parsed_params:
            proposed.parameters = parsed_params

    return proposed


def run_pending_preview(
    proj,
    pending_diffs: dict,
    target: str,
    *,
    script_map: list[tuple[object, str, str]],
    parse_paramlist_text_fn: Callable[[str], list],
    preview_param_values_fn: Callable[[object], dict[str, float]],
    collect_preview_prechecks_fn: Callable[[object, str], list[str]],
    dedupe_keep_order_fn: Callable[[list[str]], list[str]],
    set_pending_preview_2d_data_fn: Callable[[object], None],
    set_pending_preview_3d_data_fn: Callable[[object], None],
    set_pending_preview_warnings_fn: Callable[[list[str]], None],
    set_pending_preview_meta_fn: Callable[[dict], None],
    deepcopy_fn=deepcopy,
    script_type_2d=None,
    script_type_3d=None,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
) -> tuple[bool, str]:
    if proj is None:
        return False, "❌ 当前没有项目，无法预览 AI 提案"
    if not pending_diffs:
        return False, "❌ 当前没有 AI 提案可预览"

    proposed = build_project_with_pending_diffs(
        proj,
        pending_diffs,
        script_map=script_map,
        parse_paramlist_text_fn=parse_paramlist_text_fn,
        deepcopy_fn=deepcopy_fn,
    )
    params = preview_param_values_fn(proposed)
    pre_warns = collect_preview_prechecks_fn(proposed, target)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if target == "2d":
            res_2d = preview_2d_script(
                proposed.get_script(script_type_2d),
                parameters=params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            set_pending_preview_2d_data_fn(res_2d)
            set_pending_preview_warnings_fn(dedupe_keep_order_fn([*pre_warns, *res_2d.warnings]))
            set_pending_preview_meta_fn({"kind": "2D", "timestamp": ts, "source": "pending"})
            return True, "✅ AI 提案 2D 预览已更新"

        if target == "3d":
            res_3d = preview_3d_script(
                proposed.get_script(script_type_3d),
                parameters=params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            set_pending_preview_3d_data_fn(res_3d)
            set_pending_preview_warnings_fn(dedupe_keep_order_fn([*pre_warns, *res_3d.warnings]))
            set_pending_preview_meta_fn({"kind": "3D", "timestamp": ts, "source": "pending"})
            return True, "✅ AI 提案 3D 预览已更新"

        return False, f"❌ 未知预览类型: {target}"

    except Exception as exc:
        set_pending_preview_warnings_fn(dedupe_keep_order_fn([
            *pre_warns,
            f"[pending preview] 执行失败: {exc}",
        ]))
        set_pending_preview_meta_fn({"kind": target.upper(), "timestamp": ts, "source": "pending"})
        return False, f"❌ AI 提案预览失败: {exc}"
