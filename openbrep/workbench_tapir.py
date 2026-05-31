from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ui import tapir_controller


class TapirSessionState(dict):
    """Dict/attribute state adapter for existing Tapir controller functions."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def default_tapir_bridge_loader() -> tuple[Callable[[], object] | None, bool]:
    try:
        from openbrep.tapir_bridge import get_bridge

        return get_bridge, True
    except Exception:
        return None, False


@dataclass
class WorkbenchTapirAdapter:
    tapir_import_ok: bool
    get_bridge_fn: Callable[[], object] | None
    now_text_fn: Callable[[], str]
    state: TapirSessionState = field(default_factory=TapirSessionState)

    def __post_init__(self) -> None:
        self.state.setdefault("tapir_selected_guids", [])
        self.state.setdefault("tapir_selected_details", [])
        self.state.setdefault("tapir_selected_params", [])
        self.state.setdefault("tapir_param_edits", {})
        self.state.setdefault("tapir_last_error", "")
        self.state.setdefault("tapir_last_sync_at", "")

    def status_response(self) -> dict[str, Any]:
        return {"ok": True, "tapir": self.snapshot()}

    def snapshot(self) -> dict[str, Any]:
        bridge_status = self._bridge_status()
        available = bool(self.tapir_import_ok and bridge_status.get("tapir_available"))
        message = self._status_message(bridge_status, available)
        return {
            "import_ok": self.tapir_import_ok,
            "available": available,
            "archicad_connected": bool(bridge_status.get("archicad_connected")),
            "tapir_available": bool(bridge_status.get("tapir_available")),
            "version": str(bridge_status.get("version") or ""),
            "message": message,
            "selected_guids": list(self.state.get("tapir_selected_guids") or []),
            "selected_details": list(self.state.get("tapir_selected_details") or []),
            "selected_params": list(self.state.get("tapir_selected_params") or []),
            "param_edits": dict(self.state.get("tapir_param_edits") or {}),
            "last_error": str(self.state.get("tapir_last_error") or ""),
            "last_sync_at": str(self.state.get("tapir_last_sync_at") or ""),
        }

    def reload_libraries(self) -> dict[str, Any]:
        if not self.tapir_import_ok or self.get_bridge_fn is None:
            return self._action(False, "Tapir bridge 未导入")
        bridge = self.get_bridge_fn()
        if not bridge.is_available():
            self.state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
            return self._action(False, self.state.tapir_last_error)
        ok = bool(bridge.reload_libraries())
        msg = "已通知 Archicad 重载图库" if ok else "Tapir 重载 Archicad 图库失败"
        self.state.tapir_last_error = "" if ok else msg
        return self._action(ok, msg)

    def sync_selection(self) -> dict[str, Any]:
        ok, message = tapir_controller.tapir_sync_selection(
            tapir_import_ok=self.tapir_import_ok,
            get_bridge_fn=self._require_bridge,
            session_state=self.state,
            now_text_fn=self.now_text_fn,
        )
        return self._action(ok, message)

    def highlight_selection(self) -> dict[str, Any]:
        ok, message = tapir_controller.tapir_highlight_selection(
            tapir_import_ok=self.tapir_import_ok,
            get_bridge_fn=self._require_bridge,
            session_state=self.state,
        )
        return self._action(ok, message)

    def load_selected_params(self) -> dict[str, Any]:
        ok, message = tapir_controller.tapir_load_selected_params(
            tapir_import_ok=self.tapir_import_ok,
            get_bridge_fn=self._require_bridge,
            session_state=self.state,
        )
        return self._action(ok, message)

    def apply_param_edits(self, edits: dict[str, Any] | None = None) -> dict[str, Any]:
        if edits is not None:
            self.state.tapir_param_edits = dict(edits)
        ok, message = tapir_controller.tapir_apply_param_edits(
            tapir_import_ok=self.tapir_import_ok,
            get_bridge_fn=self._require_bridge,
            session_state=self.state,
        )
        return self._action(ok, message)

    def _action(self, ok: bool, message: str) -> dict[str, Any]:
        return {"ok": ok, "message": message, "tapir": self.snapshot()}

    def _require_bridge(self) -> object:
        if self.get_bridge_fn is None:
            raise RuntimeError("Tapir bridge 未导入")
        return self.get_bridge_fn()

    def _bridge_status(self) -> dict[str, Any]:
        if not self.tapir_import_ok or self.get_bridge_fn is None:
            return {"archicad_connected": False, "tapir_available": False, "version": ""}
        try:
            bridge = self.get_bridge_fn()
            status = bridge.get_status() if hasattr(bridge, "get_status") else {}
            if isinstance(status, dict):
                return status
        except Exception as exc:
            self.state.tapir_last_error = str(exc)
        return {"archicad_connected": False, "tapir_available": False, "version": ""}

    def _status_message(self, bridge_status: dict[str, Any], available: bool) -> str:
        if not self.tapir_import_ok:
            return "Tapir bridge 未导入"
        if available:
            return "Archicad + Tapir 已连接"
        if bridge_status.get("archicad_connected"):
            return "Archicad 已连接，但 Tapir 不可用"
        return "Archicad 未运行或 Tapir 未安装"
