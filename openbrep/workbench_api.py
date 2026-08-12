from __future__ import annotations

import platform
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlparse

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.hsf_project import HSFProject
from openbrep.llm import LLMAdapter
from openbrep.runtime.pipeline import TaskPipeline
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from openbrep.workbench.blender_import_service import WorkbenchBlenderImportService
from openbrep.workbench.compiler_service import WorkbenchCompilerService
from openbrep.workbench.git_service import WorkbenchGitService
from openbrep.workbench.memory_service import WorkbenchMemoryService
from openbrep.workbench.preview_service import preview_2d_payload, preview_payload
from openbrep.workbench.project_parameter_service import apply_parameter_values
from openbrep.workbench.project_service import (
    WorkbenchProjectService,
    build_demo_project,
    build_demo_snapshot,
    project_to_snapshot,
)
from openbrep.workbench.request_gate import is_lock_free_route
from openbrep.workbench.settings_service import (
    WorkbenchSettingsService,
    load_workbench_config,
    resolve_workbench_config_path,
)
from openbrep.workbench.tapir_service import WorkbenchTapirService
from openbrep.workbench.workspace_service import (
    init_workspace as ws_init_workspace,
    resolve_workspace as ws_resolve_workspace,
    scan_workspace as ws_scan_workspace,
    search_workspace as ws_search_workspace,
    trash_project as ws_trash_project,
    workspace_root_for_project as ws_root_for_project,
)
from openbrep.workbench_tapir import WorkbenchTapirAdapter, default_tapir_bridge_loader


_WORKSPACE_TOML_REL = Path(".openbrep") / "workspace.toml"


_DEFAULT_SESSION: WorkbenchSession | None = None
_DEFAULT_SESSION_LOCK = threading.Lock()


SCRIPT_ROUTE_RE = re.compile(r"^/api/project/script/([^/]+)$")
MEMORY_LESSON_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)$")
MEMORY_LESSON_IGNORE_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)/ignore$")


class WorkbenchSession:
    """Current-project state for the React workbench local API."""

    def __init__(
        self,
        *,
        pipeline_class: type = TaskPipeline,
        directory_chooser: Callable[[], str] | None = None,
        file_chooser: Callable[..., str] | None = None,
        path_revealer: Callable[[Path], None] | None = None,
        config_path: str | Path | None = None,
        tapir_import_ok: bool | None = None,
        get_tapir_bridge_fn: Callable[[], object] | None = None,
        now_text_fn: Callable[[], str] | None = None,
    ) -> None:
        # session_id 标识一次 backend 进程生命周期；project_epoch 在每次更换项目时 +1，
        # 前端用它丢弃跨项目的过期异步结果（防止 AI/编译结果写进切换后的项目）。
        self.session_id = uuid.uuid4().hex
        self.project_epoch = 0
        self._project: HSFProject | None = None
        self.source = "empty"
        self.source_path: Path | None = None
        # 工作区附着：None = 独立项目模式（现状行为逐字节保持）；否则为工作区根目录。
        self.workspace_path: Path | None = None
        self.pipeline_class = pipeline_class
        self.directory_chooser = directory_chooser or _choose_directory
        self.file_chooser = file_chooser or _choose_file
        self.path_revealer = path_revealer or _reveal_path
        self.config_path = resolve_workbench_config_path(config_path)
        self.config = load_workbench_config(self.config_path)
        self.compiler_mode = self.config.compiler.mode if self.config.compiler.mode in {"mock", "lp"} else "mock"
        self.converter_path = self.config.compiler.path or ""
        self.output_dir = "" if self.config.output_dir in {"", "./output"} else self.config.output_dir
        self.llm_model = self.config.llm.model
        self.llm_api_key = self.config.llm.resolve_api_key() or ""
        self.llm_api_base = self.config.llm.resolve_api_base() or ""
        self.max_retries = self.config.agent.max_iterations
        self.assistant_settings = self.config.llm.assistant_settings or ""
        self.recent_project_paths: list[str] = list(self.config.recent_projects or [])
        self.last_compile_output_path = ""
        # 串行化变更类请求：ThreadingHTTPServer 每请求一个线程，而 session 是全局
        # 单例；慢操作（AI 生成）与快操作（编译/保存）不能在同一 project 上交错。
        self._op_lock = threading.RLock()
        # 计划确认门（V3）：MODIFY 先出计划，用户确认后才执行；None = 无待确认计划
        self.pending_plan: dict[str, Any] | None = None
        # 模式级 skill 提案（P2-d）：成功 CREATE/MODIFY 后提炼，用户确认后才落盘晋升
        self.pending_skill_proposal: dict[str, Any] | None = None
        self.skill_harvest_enabled: bool = True
        self.settings_service = WorkbenchSettingsService(
            self,
            llm_adapter_factory=lambda config: LLMAdapter(config),
        )
        self.compiler_service = WorkbenchCompilerService(
            self,
            real_compiler_factory=lambda converter_path: HSFCompiler(converter_path),
            mock_compiler_factory=lambda: MockHSFCompiler(),
        )
        self.project_service = WorkbenchProjectService(
            self,
            real_compiler_factory=lambda converter_path: HSFCompiler(converter_path),
        )
        self.git_service = WorkbenchGitService(self)
        self.blender_import_service = WorkbenchBlenderImportService(self)
        self.assistant_service = WorkbenchAssistantService(self)
        self.memory_service = WorkbenchMemoryService(self)
        default_bridge_fn, default_import_ok = default_tapir_bridge_loader()
        self.tapir = WorkbenchTapirAdapter(
            tapir_import_ok=default_import_ok if tapir_import_ok is None else tapir_import_ok,
            get_bridge_fn=get_tapir_bridge_fn or default_bridge_fn,
            now_text_fn=now_text_fn or _now_text,
        )
        self.tapir_service = WorkbenchTapirService(self.tapir)

    @property
    def project(self) -> HSFProject | None:
        return self._project

    @project.setter
    def project(self, value: HSFProject | None) -> None:
        self._project = value
        self.project_epoch += 1

    def restore_last_project(self) -> dict[str, Any]:
        """Backend 启动时恢复上次打开的项目；路径不存在或加载失败则保持空会话。"""
        last_path = self.recent_project_paths[0] if self.recent_project_paths else ""
        if not last_path or not Path(last_path).is_dir():
            return {"ok": False, "restored": False}
        result = self.project_service.load_hsf_directory(last_path)
        result["restored"] = bool(result.get("ok"))
        if result.get("ok"):
            self._attach_workspace_for_project(self.source_path)
        # last_workspace 静默附着：失败降级独立模式，不报错
        self._restore_last_workspace()
        return result

    def _attach_workspace_for_project(self, source_path: Path | None) -> None:
        """隐式附着：项目父目录的父目录若是工作区 → 附着；否则独立项目模式。"""
        self.workspace_path = (
            ws_root_for_project(source_path) if source_path is not None else None
        )

    def _restore_last_workspace(self) -> None:
        """config.last_workspace 静默附着；路径失效则降级独立模式（不报错）。"""
        last_ws = (self.config.last_workspace or "").strip()
        if not last_ws:
            return
        self.workspace_path = ws_resolve_workspace(last_ws)

    def _persist_last_workspace(self) -> None:
        """open/close 时更新 config.last_workspace 并落盘（settings 同款保存）。"""
        from openbrep.workbench.settings_service import save_workbench_config

        self.config.last_workspace = str(self.workspace_path) if self.workspace_path else ""
        save_workbench_config(self.config, self.config_path)

    def snapshot(self) -> dict[str, Any]:
        snapshot = project_to_snapshot(
            self.project,
            source=self.source,
            source_path=str(self.source_path) if self.source_path else None,
        )
        snapshot["ok"] = True
        snapshot["session_id"] = self.session_id
        snapshot["project_epoch"] = self.project_epoch
        snapshot["compiler"] = self.compiler_settings()
        snapshot["llm"] = self.llm_settings()
        snapshot["workspace"] = self.workspace_snapshot()
        return snapshot

    def _workspace_scan_result(self, workspace_root: Path) -> dict[str, Any]:
        """scan_workspace 结果 + active 标记（path == 当前 source_path）。"""
        scan = ws_scan_workspace(str(workspace_root))
        if scan.get("ok"):
            current = str(self.source_path.expanduser().resolve()) if self.source_path else ""
            for item in scan.get("projects", []):
                item["active"] = str(Path(item["path"]).expanduser().resolve()) == current
        return scan

    def workspace_snapshot(self) -> dict[str, Any] | None:
        """snapshot 的 workspace 块：None（独立模式）或 {path, project_count, projects}，
        其中 projects 为 scan 结果并带 active 标记。"""
        if self.workspace_path is None:
            return None
        scan = self._workspace_scan_result(self.workspace_path)
        projects = scan.get("projects", []) if scan.get("ok") else []
        return {
            "path": str(self.workspace_path),
            "project_count": len(projects),
            "projects": projects,
        }

    def compiler_settings(self) -> dict[str, str]:
        return self.settings_service.compiler_settings()

    def update_compiler_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.settings_service.update_compiler_settings(body)

    def llm_settings(self) -> dict[str, Any]:
        return self.settings_service.llm_settings()

    def update_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.settings_service.update_llm_settings(body)

    def test_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.settings_service.test_llm_settings(body)

    def open_config(self) -> dict[str, Any]:
        config_path = Path(self.config_path)
        if not config_path.exists():
            return {"ok": False, "error": f"Config file not found: {config_path}"}
        _open_file(config_path)
        return {"ok": True, "path": str(config_path)}

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        result = self.project_service.load_hsf_directory(path)
        if result.get("ok"):
            self._attach_workspace_for_project(self.source_path)
        return result

    # ── Workspace（工作区附着，P3-d1）────────────────────────

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
        self.workspace_path = root
        self._persist_last_workspace()
        scan = ws_scan_workspace(str(root))
        scan.setdefault("workspace", str(root))
        return scan

    def workspace_close(self) -> dict[str, Any]:
        """POST /api/workspace/close：解除附着（项目保持打开，独立项目模式）。"""
        self.workspace_path = None
        self._persist_last_workspace()
        return {"ok": True, "workspace": None}

    def workspace_scan(self) -> dict[str, Any]:
        """GET /api/workspace/scan：无附着 → {ok, workspace: null}；有附着 → scan + active 标记。"""
        if self.workspace_path is None:
            return {"ok": True, "workspace": None}
        scan = self._workspace_scan_result(self.workspace_path)
        scan["workspace"] = str(self.workspace_path)
        return scan

    def workspace_search(self, body: dict[str, Any]) -> dict[str, Any]:
        """GET /api/workspace/search?q=xxx：无附着 → 统一错误 no_workspace。"""
        if self.workspace_path is None:
            return {
                "ok": False,
                "code": "no_workspace",
                "error": "尚未附着工作区。请先 /api/workspace/open 打开一个工作区。",
            }
        query = str(body.get("q") or body.get("query") or "")
        result = ws_search_workspace(str(self.workspace_path), query)
        result["workspace"] = str(self.workspace_path)
        return result

    def workspace_trash_project(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/workspace/trash-project：把工作区 hsf/ 下项目移入
        .openbrep/trash/（可恢复，非删除）。路径安全闸在 workspace_service。"""
        if self.workspace_path is None:
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
        if self.source_path is not None and resolved == Path(self.source_path).expanduser().resolve():
            return {
                "ok": False,
                "code": "project_active",
                "error": "请先切换到其他项目再删除",
            }
        return ws_trash_project(str(self.workspace_path), raw_path)

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.import_gdl_file(body)

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.import_gsm_file(body)

    def import_blender_script(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.blender_import_service.import_blender_script(body)

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.create_project_from_prompt(body)

    def new_project(self) -> dict[str, Any]:
        return self.project_service.new_project()

    def close_project(self) -> dict[str, Any]:
        return self.project_service.close_project()

    def save_project(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.project_service.save_project(body)

    def export_hsf_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.export_hsf_project(body)

    def recent_projects(self) -> dict[str, Any]:
        return self.project_service.recent_projects()

    def list_project_revisions(self) -> dict[str, Any]:
        return self.project_service.list_project_revisions()

    def save_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.save_project_revision(body)

    def restore_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.restore_project_revision(body)

    def project_git_status(self) -> dict[str, Any]:
        return self.git_service.status()

    def initialize_project_git(self) -> dict[str, Any]:
        return self.git_service.initialize()

    def update_project_git_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.git_service.set_enabled(body)

    def commit_project_git(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.git_service.commit(body)

    def _remember_project_path(self, path: Path) -> None:
        self.project_service.remember_project_path(path)

    def choose_and_load_hsf_directory(self) -> dict[str, Any]:
        return self.project_service.choose_and_load_hsf_directory()

    def choose_file(self, body: dict[str, Any]) -> dict[str, Any]:
        purpose = str(body.get("purpose") or "").strip().lower()
        if purpose != "compiler":
            return {"ok": False, "error": f"Unsupported file chooser purpose: {purpose}"}
        try:
            selected = self._choose_file_for_purpose(purpose)
        except Exception as exc:
            return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "File selection cancelled."}
        converter_path = str(Path(selected).expanduser())
        return {
            "ok": True,
            "path": converter_path,
            "compiler": {
                **self.compiler_settings(),
                "converter_path": converter_path,
            },
        }

    def _choose_file_for_purpose(self, purpose: str) -> str:
        try:
            return str(self.file_chooser(purpose) or "")
        except TypeError:
            return str(self.file_chooser() or "")

    def choose_output_directory(self) -> dict[str, Any]:
        try:
            selected = self.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        output_dir = str(Path(selected).expanduser().resolve())
        return {
            "ok": True,
            "path": output_dir,
            "compiler": {
                **self.compiler_settings(),
                "output_dir": output_dir,
            },
        }

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.project_service.preview(overrides)

    def preview_2d(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.project_service.preview_2d(overrides)

    def list_project_scripts(self) -> dict[str, Any]:
        return self.project_service.list_project_scripts()

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        return self.project_service.get_project_script(script_name)

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.save_project_script(script_name, body)

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.apply(changes)

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.add_project_parameter(body)

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.update_project_parameter(body)

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.delete_project_parameter(body)

    def validate_project_parameters(self) -> dict[str, Any]:
        return self.project_service.validate_project_parameters()

    def compile_mock(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.compiler_service.compile_mock(body)

    def compile_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.compiler_service.compile_project(body)

    def reveal_artifact(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or self.last_compile_output_path or "").strip()
        if not raw_path:
            return {"ok": False, "error": "No compiled artifact path is available."}
        target = Path(raw_path).expanduser().resolve()
        if not target.exists():
            return {"ok": False, "error": f"Artifact not found: {target}"}
        try:
            self.path_revealer(target)
        except Exception as exc:
            return {"ok": False, "error": f"Reveal failed: {exc}"}
        return {"ok": True, "path": str(target)}

    def assistant_reply(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.assistant_reply(body)

    def list_assistant_history(self) -> dict[str, Any]:
        return self.assistant_service.list_assistant_history()

    def save_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.save_assistant_history(body)

    def clear_assistant_history(self) -> dict[str, Any]:
        return self.assistant_service.clear_assistant_history()

    def import_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.import_assistant_history(body)

    def distill_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.distill_history_intent(body)

    def extract_assistant_code_blocks(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.extract_assistant_code_blocks(body)

    def memory_status(self) -> dict[str, Any]:
        return self.memory_service.memory_status()

    def list_memory_lessons(self) -> dict[str, Any]:
        return self.memory_service.list_memory_lessons()

    def summarize_project_memory(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.summarize_project_memory(body)

    def delete_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        return self.memory_service.delete_memory_lesson(fingerprint)

    def ignore_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        return self.memory_service.ignore_memory_lesson(fingerprint)

    def update_memory_lesson(self, fingerprint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.update_memory_lesson(fingerprint, body)

    def clear_project_memory(self) -> dict[str, Any]:
        return self.memory_service.clear_project_memory()

    def generate_with_assistant(self, body: dict[str, Any]):
        if body.get("stream"):
            import threading
            cancel_event = threading.Event()
            return self.assistant_service.generate_with_assistant_stream(body, cancel_event=cancel_event)
        return self.assistant_service.generate_with_assistant(body)

    def modify_confirm(self, body: dict[str, Any]):
        """计划确认门：approve 后带已确认计划执行（stream 走 SSE）；拒绝/无 pending 各自返回。"""
        return self.assistant_service.confirm_modify(body)

    def skill_confirm(self, body: dict[str, Any]):
        """POST /api/skill/confirm：审批待确认 skill 提案（薄转发，request_gate 锁内）。

        approve → propose_skill 落盘 + 立即 verify_skill 双闸晋升；reject → 丢弃。
        """
        return self.assistant_service.confirm_skill_proposal(body)

    def _knowledge_status(self) -> dict[str, Any]:
        """Return current knowledge base status (Free/Pro doc counts and path info)."""
        try:
            from openbrep.knowledge import KnowledgeBase
            root = Path(__file__).resolve().parent.parent
            kb = KnowledgeBase(str(root / "knowledge"))
            kb.load()
            names = list(kb._docs.keys())
            pro_names = [n for n in names if n.startswith("pro_")]
            free_names = [n for n in names if not n.startswith("pro_")]
            pro_dir = root / "knowledge" / "raw" / "ccgdl_dev_doc" / "docs"
            return {
                "ok": True,
                "has_pro": kb.has_pro,
                "free_doc_count": len(free_names),
                "pro_doc_count": len(pro_names),
                "pro_doc_names": pro_names,
                "pro_dir": str(pro_dir),
                "pro_dir_exists": pro_dir.exists(),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _knowledge_reload(self) -> dict[str, Any]:
        """Hot-reload the knowledge base from disk and return new status."""
        try:
            from openbrep.knowledge import KnowledgeBase
            root = Path(__file__).resolve().parent.parent
            kb = KnowledgeBase(str(root / "knowledge"))
            kb.load()  # fresh load (no singleton — always reads from disk)
            names = list(kb._docs.keys())
            pro_names = [n for n in names if n.startswith("pro_")]
            free_names = [n for n in names if not n.startswith("pro_")]
            pro_dir = root / "knowledge" / "raw" / "ccgdl_dev_doc" / "docs"
            return {
                "ok": True,
                "has_pro": kb.has_pro,
                "free_doc_count": len(free_names),
                "pro_doc_count": len(pro_names),
                "pro_doc_names": pro_names,
                "pro_dir": str(pro_dir),
                "pro_dir_exists": pro_dir.exists(),
                "message": f"Knowledge base reloaded: {len(free_names)} free + {len(pro_names)} pro docs.",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def route(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_method = method.upper()
        parsed = urlparse(path)
        route = parsed.path
        # URL query 参数并入 body（如 /api/workspace/search?q=xxx → body["q"]）
        if parsed.query:
            body = {**(body or {}), **dict(parse_qsl(parsed.query))}
        if is_lock_free_route(normalized_method, route):
            return self._dispatch(normalized_method, route, body)
        with self._op_lock:
            return self._dispatch(normalized_method, route, body)

    def _dispatch(
        self,
        normalized_method: str,
        route: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = body or {}

        if normalized_method == "GET" and route == "/api/snapshot":
            return {"ok": True, **self.snapshot()}

        if normalized_method == "POST" and route == "/api/project/load":
            return self.load_hsf_directory(str(body.get("path") or ""))

        if normalized_method == "POST" and route == "/api/workspace/init":
            return self.workspace_init(body)

        if normalized_method == "POST" and route == "/api/workspace/open":
            return self.workspace_open(body)

        if normalized_method == "POST" and route == "/api/workspace/close":
            return self.workspace_close()

        if normalized_method == "GET" and route == "/api/workspace/scan":
            return self.workspace_scan()

        if normalized_method == "GET" and route == "/api/workspace/search":
            return self.workspace_search(body)

        if normalized_method == "POST" and route == "/api/workspace/trash-project":
            return self.workspace_trash_project(body)

        if normalized_method == "POST" and route == "/api/project/import-gdl":
            return self.import_gdl_file(body)

        if normalized_method == "POST" and route == "/api/project/import-gsm":
            return self.import_gsm_file(body)

        if normalized_method == "POST" and route == "/api/project/import-blender":
            return self.import_blender_script(body)

        if normalized_method == "POST" and route == "/api/project/create":
            return self.create_project_from_prompt(body)

        if normalized_method == "POST" and route == "/api/project/new":
            return self.new_project()

        if normalized_method == "POST" and route == "/api/project/close":
            return self.close_project()

        if normalized_method == "POST" and route == "/api/project/save":
            return self.save_project(body)

        if normalized_method == "POST" and route == "/api/project/export-hsf":
            return self.export_hsf_project(body)

        if normalized_method == "GET" and route == "/api/project/recent":
            return self.recent_projects()

        if normalized_method == "GET" and route == "/api/project/revisions":
            return self.list_project_revisions()

        if normalized_method == "POST" and route == "/api/project/revision/save":
            return self.save_project_revision(body)

        if normalized_method == "POST" and route == "/api/project/revision/restore":
            return self.restore_project_revision(body)

        if normalized_method == "GET" and route == "/api/project/git":
            return self.project_git_status()

        if normalized_method == "POST" and route == "/api/project/git/init":
            return self.initialize_project_git()

        if normalized_method == "POST" and route == "/api/project/git/settings":
            return self.update_project_git_settings(body)

        if normalized_method == "POST" and route == "/api/project/git/commit":
            return self.commit_project_git(body)

        if normalized_method == "POST" and route == "/api/dialog/open-directory":
            return self.choose_and_load_hsf_directory()

        if normalized_method == "POST" and route == "/api/dialog/open-file":
            return self.choose_file(body)

        if normalized_method == "POST" and route == "/api/dialog/output-directory":
            return self.choose_output_directory()

        if normalized_method == "POST" and route == "/api/settings/compiler":
            return self.update_compiler_settings(body)

        if normalized_method == "GET" and route == "/api/settings/runtime":
            return self.settings_service.reload_runtime_settings()

        if normalized_method == "GET" and route == "/api/settings/config-revision":
            return self.settings_service.config_revision()

        if normalized_method == "POST" and route == "/api/settings/open-config":
            return self.open_config()

        if normalized_method == "POST" and route == "/api/settings/llm/test":
            return self.test_llm_settings(body)

        if normalized_method in ("PATCH", "PUT") and route == "/api/settings/llm/model":
            return self.settings_service.update_llm_model_only(body)

        if normalized_method in ("PATCH", "PUT", "POST") and route == "/api/settings/llm/api-key":
            return self.settings_service.update_llm_api_key(body)

        if normalized_method == "POST" and route == "/api/settings/llm":
            return self.update_llm_settings(body)

        if normalized_method == "GET" and route == "/api/tapir/status":
            return self.tapir_service.status_response()

        if normalized_method == "POST" and route == "/api/tapir/reload-libraries":
            return self.tapir_service.reload_libraries()

        if normalized_method == "POST" and route == "/api/tapir/selection/sync":
            return self.tapir_service.sync_selection()

        if normalized_method == "POST" and route == "/api/tapir/selection/highlight":
            return self.tapir_service.highlight_selection()

        if normalized_method == "POST" and route == "/api/tapir/parameters/load":
            return self.tapir_service.load_selected_params()

        if normalized_method == "POST" and route == "/api/tapir/parameters/apply":
            return self.tapir_service.apply_param_edits(body)

        if normalized_method == "POST" and route == "/api/preview":
            return self.preview(body)

        if normalized_method == "POST" and route == "/api/preview/2d":
            return self.preview_2d(body)

        if normalized_method == "GET" and route == "/api/project/scripts":
            return self.list_project_scripts()

        script_match = SCRIPT_ROUTE_RE.match(route)
        if script_match and normalized_method == "GET":
            return self.get_project_script(unquote(script_match.group(1)))

        if script_match and normalized_method == "POST":
            return self.save_project_script(unquote(script_match.group(1)), body)

        if normalized_method == "POST" and route == "/api/apply":
            return self.apply(body.get("parameters") or {})

        if normalized_method == "POST" and route == "/api/project/parameters":
            return self.add_project_parameter(body)

        if normalized_method == "POST" and route == "/api/project/parameters/update":
            return self.update_project_parameter(body)

        if normalized_method == "POST" and route == "/api/project/parameters/delete":
            return self.delete_project_parameter(body)

        if normalized_method == "POST" and route == "/api/project/parameters/validate":
            return self.validate_project_parameters()

        if normalized_method == "POST" and route == "/api/compile":
            return self.compile_project(body)

        if normalized_method == "POST" and route == "/api/compile/mock":
            return self.compile_mock(body)

        if normalized_method == "POST" and route == "/api/artifact/reveal":
            return self.reveal_artifact(body)

        if normalized_method == "GET" and route == "/api/assistant/history":
            return self.list_assistant_history()

        if normalized_method == "POST" and route == "/api/assistant/history":
            return self.save_assistant_history(body)

        if normalized_method == "DELETE" and route == "/api/assistant/history":
            return self.clear_assistant_history()

        if normalized_method == "POST" and route == "/api/assistant/history/import":
            return self.import_assistant_history(body)

        if normalized_method == "POST" and route == "/api/assistant/history/distill":
            return self.distill_assistant_history(body)

        if normalized_method == "POST" and route == "/api/assistant/code-blocks":
            return self.extract_assistant_code_blocks(body)

        if normalized_method == "GET" and route == "/api/memory/status":
            return self.memory_status()

        if normalized_method == "GET" and route == "/api/memory/lessons":
            return self.list_memory_lessons()

        if normalized_method == "POST" and route == "/api/memory/summarize":
            return self.summarize_project_memory(body)

        lesson_ignore_match = MEMORY_LESSON_IGNORE_ROUTE_RE.match(route)
        if lesson_ignore_match and normalized_method == "POST":
            return self.ignore_memory_lesson(unquote(lesson_ignore_match.group(1)))

        lesson_match = MEMORY_LESSON_ROUTE_RE.match(route)
        if lesson_match and normalized_method == "PATCH":
            return self.update_memory_lesson(unquote(lesson_match.group(1)), body)
        if lesson_match and normalized_method == "DELETE":
            return self.delete_memory_lesson(unquote(lesson_match.group(1)))

        if normalized_method == "DELETE" and route == "/api/memory":
            return self.clear_project_memory()

        if normalized_method == "POST" and route == "/api/assistant":
            return self.assistant_reply(body)

        if normalized_method == "POST" and route == "/api/assistant/generate":
            return self.generate_with_assistant(body)

        if normalized_method == "POST" and route == "/api/modify/confirm":
            return self.modify_confirm(body)

        if normalized_method == "POST" and route == "/api/skill/confirm":
            return self.skill_confirm(body)

        if normalized_method == "GET" and route == "/api/knowledge/status":
            return self._knowledge_status()

        if normalized_method == "POST" and route == "/api/knowledge/reload":
            return self._knowledge_reload()

        return {"ok": False, "error": f"Unknown route: {normalized_method} {route}"}


def _default_session() -> WorkbenchSession:
    global _DEFAULT_SESSION
    if _DEFAULT_SESSION is None:
        with _DEFAULT_SESSION_LOCK:
            if _DEFAULT_SESSION is None:
                _DEFAULT_SESSION = WorkbenchSession()
    return _DEFAULT_SESSION


def route_rpc(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _default_session().route(method, path, body)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _choose_directory() -> str:
    from openbrep.local_file_dialog import choose_directory

    return str(choose_directory(title="Open HSF project directory") or "")


def _choose_file(purpose: str = "openbrep") -> str:
    from openbrep.local_file_dialog import choose_file

    titles = {
        "compiler": "Choose LP_XMLConverter",
        "gdl": "Import GDL script",
        "gsm": "Import GSM object",
        "blender": "Import Blender script",
    }
    extensions = {
        "compiler": [],
        "gdl": ["gdl"],
        "gsm": ["gsm"],
        "blender": ["py"],
    }
    return str(choose_file(title=titles.get(purpose, "Choose OpenBrep file"), extensions=extensions.get(purpose)) or "")


def _reveal_path(path: Path) -> None:
    target = path.resolve()
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
        return
    if system == "Windows":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return
    subprocess.run(["xdg-open", str(target.parent if target.is_file() else target)], check=False)


def _open_file(path: Path) -> None:
    target = path.resolve()
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(target)])
        return
    if system == "Windows":
        subprocess.Popen(["start", str(target)], shell=True)
        return
    subprocess.Popen(["xdg-open", str(target)])


def _run_server_entrypoint() -> None:
    # HTTP transport (ThreadingHTTPServer + static file serving) lives in
    # openbrep.workbench.http_server; imported lazily to avoid a circular
    # import (that module imports route_rpc/_default_session from here).
    from openbrep.workbench.http_server import main

    main()


if __name__ == "__main__":
    _run_server_entrypoint()
