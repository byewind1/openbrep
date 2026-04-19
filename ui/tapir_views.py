from __future__ import annotations

from typing import Callable


def render_tapir_inspector_panel(*, session_state, caption_fn: Callable[[str], None], warning_fn: Callable[[str], None], info_fn: Callable[[str], None], markdown_fn: Callable[[str], None], code_fn: Callable[[str, str], None], json_fn: Callable[[object], None]) -> None:
    guids = session_state.get("tapir_selected_guids") or []
    details = session_state.get("tapir_selected_details") or []
    last_sync = session_state.get("tapir_last_sync_at", "")
    last_error = session_state.get("tapir_last_error", "")

    if last_sync:
        caption_fn(f"最近同步：{last_sync}")
    if last_error:
        warning_fn(last_error)

    if not guids:
        info_fn("未选中对象。")
        return

    markdown_fn(f"**选中 GUID（{len(guids)}）**")
    code_fn("\n".join(guids), "text")

    markdown_fn("**元素详情**")
    if details:
        json_fn(details)
    else:
        caption_fn("暂无元素详情。")


def render_tapir_param_workbench_panel(*, session_state, info_fn: Callable[[str], None], expander_fn: Callable[..., object], text_input_fn: Callable[..., str]) -> None:
    rows = session_state.get("tapir_selected_params") or []
    if not rows:
        info_fn("暂无参数数据，请先点击「读取参数」。")
        return

    edits = session_state.get("tapir_param_edits") or {}
    for row in rows:
        guid = (row.get("guid") or "").strip()
        params = row.get("gdlParameters")
        if not guid or not isinstance(params, list):
            continue

        with expander_fn(f"对象 {guid}", expanded=False):
            for p in params:
                if not isinstance(p, dict):
                    continue
                name = p.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                key = f"{guid}::{name.strip()}"
                current_value = edits.get(key, "")
                p_type = p.get("type", "")
                label = name.strip()
                if p_type:
                    label = f"{label} ({p_type})"
                new_val = text_input_fn(label, value=str(current_value), key=f"tapir_edit::{key}")
                edits[key] = new_val

    session_state.tapir_param_edits = edits
