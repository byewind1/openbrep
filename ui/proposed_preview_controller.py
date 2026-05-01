from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script


def clear_pending_preview_state(session_state) -> None:
    session_state.pending_preview_2d_data = None
    session_state.pending_preview_3d_data = None
    session_state.pending_current_preview_2d_data = None
    session_state.pending_current_preview_3d_data = None
    session_state.pending_preview_warnings = []
    session_state.pending_preview_meta = {"kind": "", "timestamp": "", "source": ""}
    session_state.pending_preview_diff_summary = {}


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
    set_pending_current_preview_2d_data_fn: Callable[[object], None] | None = None,
    set_pending_current_preview_3d_data_fn: Callable[[object], None] | None = None,
    set_pending_preview_diff_summary_fn: Callable[[dict], None] | None = None,
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
    current_params = preview_param_values_fn(proj)
    proposed_params = preview_param_values_fn(proposed)
    current_pre_warns = collect_preview_prechecks_fn(proj, target)
    proposed_pre_warns = collect_preview_prechecks_fn(proposed, target)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed_paths = _changed_paths(pending_diffs)

    try:
        if target == "2d":
            current_2d = preview_2d_script(
                proj.get_script(script_type_2d),
                parameters=current_params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            res_2d = preview_2d_script(
                proposed.get_script(script_type_2d),
                parameters=proposed_params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            if set_pending_current_preview_2d_data_fn is not None:
                set_pending_current_preview_2d_data_fn(current_2d)
            set_pending_preview_2d_data_fn(res_2d)
            warnings = dedupe_keep_order_fn([*proposed_pre_warns, *res_2d.warnings])
            set_pending_preview_warnings_fn(warnings)
            set_pending_preview_meta_fn({"kind": "2D", "timestamp": ts, "source": "pending"})
            if set_pending_preview_diff_summary_fn is not None:
                set_pending_preview_diff_summary_fn(_preview_diff_summary(
                    "2D",
                    current_2d,
                    res_2d,
                    current_warnings=[*current_pre_warns, *current_2d.warnings],
                    proposed_warnings=[*proposed_pre_warns, *res_2d.warnings],
                    changed_paths=changed_paths,
                ))
            return True, "✅ AI 提案 2D 预览已更新"

        if target == "3d":
            current_3d = preview_3d_script(
                proj.get_script(script_type_3d),
                parameters=current_params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            res_3d = preview_3d_script(
                proposed.get_script(script_type_3d),
                parameters=proposed_params,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            if set_pending_current_preview_3d_data_fn is not None:
                set_pending_current_preview_3d_data_fn(current_3d)
            set_pending_preview_3d_data_fn(res_3d)
            warnings = dedupe_keep_order_fn([*proposed_pre_warns, *res_3d.warnings])
            set_pending_preview_warnings_fn(warnings)
            set_pending_preview_meta_fn({"kind": "3D", "timestamp": ts, "source": "pending"})
            if set_pending_preview_diff_summary_fn is not None:
                set_pending_preview_diff_summary_fn(_preview_diff_summary(
                    "3D",
                    current_3d,
                    res_3d,
                    current_warnings=[*current_pre_warns, *current_3d.warnings],
                    proposed_warnings=[*proposed_pre_warns, *res_3d.warnings],
                    changed_paths=changed_paths,
                ))
            return True, "✅ AI 提案 3D 预览已更新"

        return False, f"❌ 未知预览类型: {target}"

    except Exception as exc:
        set_pending_preview_warnings_fn(dedupe_keep_order_fn([
            *proposed_pre_warns,
            f"[pending preview] 执行失败: {exc}",
        ]))
        set_pending_preview_meta_fn({"kind": target.upper(), "timestamp": ts, "source": "pending"})
        if set_pending_preview_diff_summary_fn is not None:
            set_pending_preview_diff_summary_fn({
                "target": target.upper(),
                "changed_paths": changed_paths,
                "error": str(exc),
            })
        return False, f"❌ AI 提案预览失败: {exc}"


def _changed_paths(pending_diffs: dict) -> list[str]:
    return sorted([
        str(path)
        for path in pending_diffs.keys()
        if str(path).startswith("scripts/") or str(path) == "paramlist.xml"
    ])


def _preview_diff_summary(
    target: str,
    current_data,
    proposed_data,
    *,
    current_warnings: list[str],
    proposed_warnings: list[str],
    changed_paths: list[str],
) -> dict:
    current_counts = _preview_counts(current_data)
    proposed_counts = _preview_counts(proposed_data)
    current_facts = _preview_facts(current_counts, current_warnings)
    proposed_facts = _preview_facts(proposed_counts, proposed_warnings)
    deltas = {
        key: proposed_counts.get(key, 0) - current_counts.get(key, 0)
        for key in sorted(set(current_counts) | set(proposed_counts))
    }
    return {
        "target": target,
        "current": current_counts,
        "proposed": proposed_counts,
        "delta": deltas,
        "current_facts": current_facts,
        "proposed_facts": proposed_facts,
        "current_status": _preview_status(current_facts),
        "proposed_status": _preview_status(proposed_facts),
        "current_warning_count": len(current_warnings),
        "proposed_warning_count": len(proposed_warnings),
        "warning_delta": len(proposed_warnings) - len(current_warnings),
        "changed_paths": changed_paths,
    }


def _preview_counts(data) -> dict[str, int]:
    if hasattr(data, "meshes") or hasattr(data, "wires"):
        return {
            "mesh": len(getattr(data, "meshes", []) or []),
            "wire": len(getattr(data, "wires", []) or []),
        }
    return {
        "line": len(getattr(data, "lines", []) or []),
        "polygon": len(getattr(data, "polygons", []) or []),
        "circle": len(getattr(data, "circles", []) or []),
        "arc": len(getattr(data, "arcs", []) or []),
    }


def _preview_facts(counts: dict[str, int], warnings: list[str]) -> dict:
    total_primitives = sum(value for value in counts.values() if isinstance(value, int))
    return {
        "counts": counts,
        "total_primitives": total_primitives,
        "is_empty": total_primitives == 0,
        "warning_count": len(warnings),
    }


def _preview_status(facts: dict) -> str:
    if facts.get("is_empty"):
        return "empty"
    if int(facts.get("warning_count") or 0) > 0:
        return "warn"
    return "ok"
