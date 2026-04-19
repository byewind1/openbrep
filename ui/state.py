from __future__ import annotations

import uuid


def new_generation_id() -> str:
    return uuid.uuid4().hex


def begin_generation_state(state) -> str:
    generation_id = new_generation_id()
    state["active_generation_id"] = generation_id
    state["generation_status"] = "running"
    state["generation_cancel_requested"] = False
    state["agent_running"] = True
    return generation_id


def request_generation_cancel(state, generation_id: str) -> bool:
    if not generation_id or state.get("active_generation_id") != generation_id:
        return False
    state["generation_cancel_requested"] = True
    state["generation_status"] = "cancelling"
    state["agent_running"] = True
    return True


def is_generation_locked(state) -> bool:
    return state.get("generation_status") in {"running", "cancelling"}


def is_active_generation(state, generation_id: str) -> bool:
    return bool(generation_id) and state.get("active_generation_id") == generation_id


def should_accept_generation_result(state, generation_id: str) -> bool:
    return is_active_generation(state, generation_id) and not state.get("generation_cancel_requested", False)


def finish_generation_state(state, generation_id: str, status: str) -> bool:
    if not is_active_generation(state, generation_id):
        return False
    state["generation_status"] = status
    state["active_generation_id"] = None
    state["generation_cancel_requested"] = False
    state["agent_running"] = False
    return True
