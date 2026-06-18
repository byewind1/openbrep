from __future__ import annotations

from typing import Any


class WorkbenchTapirService:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def status_response(self) -> dict[str, Any]:
        return self.adapter.status_response()

    def reload_libraries(self) -> dict[str, Any]:
        return self.adapter.reload_libraries()

    def sync_selection(self) -> dict[str, Any]:
        return self.adapter.sync_selection()

    def highlight_selection(self) -> dict[str, Any]:
        return self.adapter.highlight_selection()

    def load_selected_params(self) -> dict[str, Any]:
        return self.adapter.load_selected_params()

    def apply_param_edits(self, body: dict[str, Any]) -> dict[str, Any]:
        edits = body.get("param_edits")
        return self.adapter.apply_param_edits(edits if isinstance(edits, dict) else None)
