from __future__ import annotations

import hashlib
from typing import Callable

from openbrep.hsf_project import HSFProject
from openbrep.revisions import get_latest_revision_id, list_revisions


def render_revision_panel(
    st,
    proj: HSFProject,
    *,
    is_generation_locked_fn: Callable[[], bool],
    save_revision_fn: Callable[[HSFProject, str, str | None], tuple[bool, str]],
    restore_revision_fn: Callable[[HSFProject, str], tuple[bool, str]],
) -> None:
    with st.expander("🕘 版本管理", expanded=True):
        project_root_text = str(getattr(proj, "root", "") or "")
        project_key = hashlib.sha1(project_root_text.encode("utf-8")).hexdigest()[:10]
        notice_key = f"revision_project_{project_key}_notice"
        st.caption(f"当前项目目录：`{project_root_text}`")
        st.checkbox(
            "编译成功后自动保存版本",
            key="revision_auto_snapshot",
            help="保存 HSF 源文件快照，不保存 .gsm 编译产物",
        )
        revision_message = st.text_input(
            "版本说明",
            value="",
            placeholder="例如：调整层板厚度 / 编译前稳定版本",
            key=f"revision_project_{project_key}_message_input",
            disabled=is_generation_locked_fn(),
        )

        save_col, refresh_col = st.columns([1.2, 1.0])
        with save_col:
            if st.button(
                "保存版本",
                key="revision_save_button",
                width="stretch",
                disabled=is_generation_locked_fn(),
            ):
                ok, msg = save_revision_fn(
                    proj,
                    revision_message,
                    st.session_state.get("pending_gsm_name") or proj.name,
                )
                st.session_state[notice_key] = msg
                if ok:
                    st.toast("已保存版本", icon="✅")
                st.rerun()
        with refresh_col:
            if st.button("刷新历史", key="revision_refresh_button", width="stretch"):
                st.rerun()

        notice = st.session_state.get(notice_key, "") or st.session_state.pop("revision_notice", "")
        if notice:
            if notice.startswith("✅"):
                st.success(notice)
            else:
                st.error(notice)

        try:
            revisions = list_revisions(proj.root)
            latest_revision = get_latest_revision_id(proj.root)
        except Exception as exc:
            revisions = []
            latest_revision = None
            st.caption(f"暂无可读取版本：{exc}")

        if not revisions:
            st.caption("还没有版本。点击“保存版本”后，这里会显示历史。")
            return

        revision_by_label = {
            _format_revision_option(revision): revision
            for revision in reversed(revisions)
        }
        selected_revision = st.selectbox(
            "版本历史",
            options=list(revision_by_label.keys()),
            key=f"revision_project_{project_key}_restore_select",
            help="选择一个版本查看说明或恢复",
        )
        selected_meta = revision_by_label.get(selected_revision)
        if selected_meta:
            latest_mark = " · 最新" if selected_meta.revision_id == latest_revision else ""
            st.caption(
                f"{selected_meta.project_name} / {selected_meta.gsm_name} · "
                f"{selected_meta.created_at}{latest_mark} · "
                f"{len(selected_meta.files)} 个源文件"
            )
            if selected_meta.message:
                st.caption(f"说明：{selected_meta.message}")

        if st.button(
            "恢复此版本",
            key="revision_restore_button",
            width="stretch",
            disabled=is_generation_locked_fn(),
        ):
            revision_id = selected_meta.revision_id if selected_meta else ""
            ok, msg = restore_revision_fn(proj, revision_id)
            st.session_state[notice_key] = msg
            if ok:
                st.toast("已恢复版本", icon="✅")
            st.rerun()


def _format_revision_option(revision) -> str:
    return f"{revision.revision_id} · {revision.gsm_name}"
