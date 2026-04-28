from __future__ import annotations

from ui import state as ui_state


def generation_stop_label(session_state) -> str:
    return "停止生成中..." if session_state.get("generation_status") == "cancelling" else "停止生成"


def render_generation_controls(st, session_state) -> None:
    if not ui_state.is_generation_locked(session_state):
        return
    generation_id = session_state.get("active_generation_id")
    st.warning("AI 正在生成中。涉及工程状态的操作已临时锁定。")
    if st.button(generation_stop_label(session_state), key="stop_generation", width="stretch"):
        if ui_state.request_generation_cancel(session_state, generation_id):
            st.info("已请求停止当前生成，正在等待本轮调用安全结束。")
            st.rerun()


def guarded_event_update(session_state, status_ph, generation_id: str, method_name: str, message: str) -> None:
    if not ui_state.is_active_generation(session_state, generation_id):
        return
    getattr(status_ph, method_name)(message)


def generation_cancelled_message() -> str:
    return "⏹️ 本轮生成已取消，未写入编辑器。"


def consume_generation_result(session_state, generation_id: str) -> bool:
    return ui_state.should_accept_generation_result(session_state, generation_id)


def finalize_generation(session_state, generation_id: str, status: str) -> bool:
    return ui_state.finish_generation_state(session_state, generation_id, status)
