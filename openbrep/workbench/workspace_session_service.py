"""Workspace attachment, persistence and routes owned by one session service."""
from pathlib import Path
from typing import Any

from openbrep.workbench.workspace_service import init_workspace as ws_init_workspace
from openbrep.workbench.workspace_service import resolve_workspace as ws_resolve_workspace
from openbrep.workbench.workspace_service import scan_workspace as ws_scan_workspace
from openbrep.workbench.workspace_service import search_workspace as ws_search_workspace
from openbrep.workbench.workspace_service import trash_project as ws_trash_project
from openbrep.workbench.workspace_service import workspace_root_for_project as ws_root_for_project

_WORKSPACE_TOML_REL = Path(".openbrep") / "workspace.toml"

class WorkbenchWorkspaceSessionService:
    def __init__(self, session):
        self.session = session

    def _attach_workspace_for_project(self, source_path: Path | None) -> None:
        """隐式附着：项目父目录的父目录若是工作区 → 附着；否则独立项目模式。"""
        self.session.workspace_path = (
            ws_root_for_project(source_path) if source_path is not None else None
        )

    def _restore_last_workspace(self) -> None:
        """config.last_workspace 静默附着；路径失效则降级独立模式（不报错）。"""
        last_ws = (self.session.config.last_workspace or "").strip()
        if not last_ws:
            return
        self.session.workspace_path = ws_resolve_workspace(last_ws)

    def _persist_last_workspace(self) -> None:
        """open/close 时更新 config.last_workspace 并落盘（settings 同款保存）。"""
        from openbrep.workbench.settings_service import save_workbench_config

        self.session.config.last_workspace = str(self.session.workspace_path) if self.session.workspace_path else ""
        save_workbench_config(self.session.config, self.session.config_path)

    def _workspace_scan_result(self, workspace_root: Path) -> dict[str, Any]:
        """scan_workspace 结果 + active 标记（path == 当前 source_path）。"""
        scan = ws_scan_workspace(str(workspace_root))
        if scan.get("ok"):
            current = str(self.session.source_path.expanduser().resolve()) if self.session.source_path else ""
            for item in scan.get("projects", []):
                item["active"] = str(Path(item["path"]).expanduser().resolve()) == current
        return scan

    def workspace_snapshot(self) -> dict[str, Any] | None:
        """snapshot 的 workspace 块：None（独立模式）或 {path, project_count, projects}，
        其中 projects 为 scan 结果并带 active 标记。"""
        if self.session.workspace_path is None:
            return None
        scan = self._workspace_scan_result(self.session.workspace_path)
        projects = scan.get("projects", []) if scan.get("ok") else []
        return {
            "path": str(self.session.workspace_path),
            "project_count": len(projects),
            "projects": projects,
        }

    def workspace_init(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/workspace/init：初始化工作区（四区 + workspace.toml）。"""
        return ws_init_workspace(str(body.get("path") or ""))

    def workspace_open(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/workspace/open：显式附着工作区；未初始化返回统一错误。"""
        path = str(body.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": "workspace path required."}
        root = Path(path).expanduser().resolve()
        if not (root / _WORKSPACE_TOML_REL).is_file():
            return {
                "ok": False,
                "code": "not_a_workspace",
                "error": (
                    f"不是已初始化的工作区（缺 .openbrep/workspace.toml）: {path}；"
                    "请先调用 /api/workspace/init"
                ),
            }
        self.session.workspace_path = root
        self._persist_last_workspace()
        scan = ws_scan_workspace(str(root))
        scan.setdefault("workspace", str(root))
        return scan

    def workspace_close(self) -> dict[str, Any]:
        """POST /api/workspace/close：解除附着（项目保持打开，独立项目模式）。"""
        self.session.workspace_path = None
        self._persist_last_workspace()
        return {"ok": True, "workspace": None}

    def workspace_scan(self) -> dict[str, Any]:
        """GET /api/workspace/scan：无附着 → {ok, workspace: null}；有附着 → scan + active 标记。"""
        if self.session.workspace_path is None:
            return {"ok": True, "workspace": None}
        scan = self._workspace_scan_result(self.session.workspace_path)
        scan["workspace"] = str(self.session.workspace_path)
        return scan

    def workspace_search(self, body: dict[str, Any]) -> dict[str, Any]:
        """GET /api/workspace/search?q=xxx：无附着 → 统一错误 no_workspace。"""
        if self.session.workspace_path is None:
            return {
                "ok": False,
                "code": "no_workspace",
                "error": "尚未附着工作区。请先 /api/workspace/open 打开一个工作区。",
            }
        query = str(body.get("q") or body.get("query") or "")
        result = ws_search_workspace(str(self.session.workspace_path), query)
        result["workspace"] = str(self.session.workspace_path)
        return result

    def workspace_trash_project(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/workspace/trash-project：把工作区 hsf/ 下项目移入
        .openbrep/trash/（可恢复，非删除）。路径安全闸在 workspace_service。"""
        if self.session.workspace_path is None:
            return {
                "ok": False,
                "code": "no_workspace",
                "error": "尚未附着工作区。请先 /api/workspace/open 打开一个工作区。",
            }
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            return {"ok": False, "code": "not_found", "error": "project path required."}
        try:
            resolved = Path(raw_path).expanduser().resolve()
        except Exception as exc:
            return {"ok": False, "code": "not_found", "error": f"无效项目路径: {exc}"}
        # 安全闸 a：当前会话打开中的项目 → 拒绝（先切换项目再删）
        if self.session.source_path is not None and resolved == Path(self.session.source_path).expanduser().resolve():
            return {
                "ok": False,
                "code": "project_active",
                "error": "请先切换到其他项目再删除",
            }
        return ws_trash_project(str(self.session.workspace_path), raw_path)

