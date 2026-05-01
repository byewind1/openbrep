from __future__ import annotations

from datetime import datetime
from typing import Callable

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from ui.proposed_preview_controller import clear_pending_preview_state


def sync_visible_editor_buffers(
    proj,
    editor_version: int,
    *,
    session_state,
    script_map: list[tuple[object, str, str]],
    main_editor_state_key_fn: Callable[[str, int], str],
    ace_available: bool,
) -> bool:
    changed = False
    pending_keys = session_state.get("_ace_pending_main_editor_keys") or set()
    for stype, fpath, _label in script_map:
        current_code = proj.get_script(stype) or ""
        editor_key = main_editor_state_key_fn(fpath, editor_version)
        if editor_key not in session_state:
            continue
        raw_value = session_state.get(editor_key)
        if raw_value is None:
            continue
        new_code = raw_value or ""
        if ace_available and editor_key in pending_keys and current_code and new_code == "":
            continue
        pending_keys.discard(editor_key)
        if new_code == current_code:
            continue
        proj.set_script(stype, new_code)
        changed = True

    session_state._ace_pending_main_editor_keys = pending_keys

    if changed:
        session_state.preview_2d_data = None
        session_state.preview_3d_data = None
        session_state.preview_warnings = []
        session_state.preview_meta = {"kind": "", "timestamp": ""}
        clear_pending_preview_state(session_state)

    return changed


def clear_script_editor_widget_state(
    session_state,
    *,
    script_map: list[tuple[object, str, str]],
) -> None:
    """Drop stale Streamlit editor widget values so project scripts can repopulate editors."""
    for _stype, fpath, _label in script_map:
        prefixes = (f"ace_{fpath}_v", f"script_{fpath}_v")
        for key in list(session_state.keys()):
            if any(str(key).startswith(prefix) for prefix in prefixes):
                session_state.pop(key, None)


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
    script_type_master=None,
    strict: bool = False,
    unknown_command_policy: str = "warn",
    quality: str = "fast",
) -> tuple[bool, str]:
    sync_visible_editor_buffers_fn(proj, editor_version)
    params = preview_param_values_fn(proj)
    pre_warns = collect_preview_prechecks_fn(proj, target)
    if script_type_master is None:
        script_type_master = getattr(getattr(script_type_3d, "__class__", object), "MASTER", None)
    setup_script = proj.get_script(script_type_master) if script_type_master is not None else ""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if target == "2d":
            res_2d = preview_2d_script(
                proj.get_script(script_type_2d),
                parameters=params,
                setup_script=setup_script,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
            set_preview_2d_data_fn(res_2d)
            set_preview_warnings_fn(dedupe_keep_order_fn([*pre_warns, *res_2d.warnings]))
            set_preview_meta_fn({"kind": "2D", "timestamp": ts})
            return True, "✅ 2D 预览已更新"

        if target == "3d":
            res_3d = preview_3d_script(
                proj.get_script(script_type_3d),
                parameters=params,
                setup_script=setup_script,
                strict=strict,
                unknown_command_policy=unknown_command_policy,
                quality=quality,
            )
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
