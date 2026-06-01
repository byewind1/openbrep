from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from datetime import datetime
from urllib.parse import unquote, urlparse

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.hsf_project import HSFProject
from openbrep.llm import LLMAdapter
from openbrep.runtime.pipeline import TaskPipeline
from openbrep.workbench.assistant_service import WorkbenchAssistantService
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
from openbrep.workbench.settings_service import (
    WorkbenchSettingsService,
    load_workbench_config,
    resolve_workbench_config_path,
    save_workbench_config,
)
from openbrep.workbench.tapir_service import WorkbenchTapirService
from openbrep.workbench_tapir import WorkbenchTapirAdapter, default_tapir_bridge_loader


_DEFAULT_SESSION: WorkbenchSession | None = None


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
        file_chooser: Callable[[], str] | None = None,
        path_revealer: Callable[[Path], None] | None = None,
        config_path: str | Path | None = None,
        tapir_import_ok: bool | None = None,
        get_tapir_bridge_fn: Callable[[], object] | None = None,
        now_text_fn: Callable[[], str] | None = None,
    ) -> None:
        self.project: HSFProject = build_demo_project()
        self.source = "demo"
        self.source_path: Path | None = None
        self.pipeline_class = pipeline_class
        self.directory_chooser = directory_chooser or _choose_directory
        self.file_chooser = file_chooser or _choose_file
        self.path_revealer = path_revealer or _reveal_path
        self.config_path = resolve_workbench_config_path(config_path)
        self.config = load_workbench_config(self.config_path)
        self.compiler_mode = "mock"
        self.converter_path = self.config.compiler.path or ""
        self.output_dir = "" if self.config.output_dir in {"", "./output"} else self.config.output_dir
        self.llm_model = self.config.llm.model
        self.llm_api_key = self.config.llm.resolve_api_key() or ""
        self.llm_api_base = self.config.llm.resolve_api_base() or ""
        self.max_retries = self.config.agent.max_iterations
        self.assistant_settings = self.config.llm.assistant_settings or ""
        self.recent_project_paths: list[str] = list(self.config.recent_projects or [])
        self.last_compile_output_path = ""
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
        self.assistant_service = WorkbenchAssistantService(self)
        self.memory_service = WorkbenchMemoryService(self)
        default_bridge_fn, default_import_ok = default_tapir_bridge_loader()
        self.tapir = WorkbenchTapirAdapter(
            tapir_import_ok=default_import_ok if tapir_import_ok is None else tapir_import_ok,
            get_bridge_fn=get_tapir_bridge_fn or default_bridge_fn,
            now_text_fn=now_text_fn or _now_text,
        )
        self.tapir_service = WorkbenchTapirService(self.tapir)

    def snapshot(self) -> dict[str, Any]:
        snapshot = project_to_snapshot(
            self.project,
            source=self.source,
            source_path=str(self.source_path) if self.source_path else None,
        )
        snapshot["compiler"] = self.compiler_settings()
        snapshot["llm"] = self.llm_settings()
        return snapshot

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

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        return self.project_service.load_hsf_directory(path)

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.import_gdl_file(body)

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.import_gsm_file(body)

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.project_service.create_project_from_prompt(body)

    def close_project(self) -> dict[str, Any]:
        return self.project_service.close_project()

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
            selected = self.file_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "File selection cancelled."}
        self.compiler_mode = "lp"
        self.converter_path = str(Path(selected).expanduser())
        return {"ok": True, "path": self.converter_path, "compiler": self.compiler_settings()}

    def choose_output_directory(self) -> dict[str, Any]:
        try:
            selected = self.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        self.output_dir = str(Path(selected).expanduser().resolve())
        self.config.output_dir = self.output_dir
        save_workbench_config(self.config, self.config_path)
        return {"ok": True, "path": self.output_dir, "compiler": self.compiler_settings()}

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

    def generate_with_assistant(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.assistant_service.generate_with_assistant(body)

    def route(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_method = method.upper()
        route = urlparse(path).path
        body = body or {}

        if normalized_method == "GET" and route == "/api/snapshot":
            return {"ok": True, **self.snapshot()}

        if normalized_method == "POST" and route == "/api/project/load":
            return self.load_hsf_directory(str(body.get("path") or ""))

        if normalized_method == "POST" and route == "/api/project/import-gdl":
            return self.import_gdl_file(body)

        if normalized_method == "POST" and route == "/api/project/import-gsm":
            return self.import_gsm_file(body)

        if normalized_method == "POST" and route == "/api/project/create":
            return self.create_project_from_prompt(body)

        if normalized_method == "POST" and route == "/api/project/close":
            return self.close_project()

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

        if normalized_method == "POST" and route == "/api/settings/llm/test":
            return self.test_llm_settings(body)

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

        return {"ok": False, "error": f"Unknown route: {normalized_method} {route}"}


def _default_session() -> WorkbenchSession:
    global _DEFAULT_SESSION
    if _DEFAULT_SESSION is None:
        _DEFAULT_SESSION = WorkbenchSession()
    return _DEFAULT_SESSION


def route_rpc(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _default_session().route(method, path, body)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _choose_directory() -> str:
    from ui.local_file_dialog import choose_directory

    return str(choose_directory(title="Open HSF project directory") or "")


def _choose_file() -> str:
    from ui.local_file_dialog import choose_file

    return str(choose_file(title="Choose OpenBrep file") or "")


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


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), _WorkbenchRequestHandler)
    print(f"OpenBrep workbench API listening on http://{host}:{port}")
    server.serve_forever()


class _WorkbenchRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._send(route_rpc("GET", self.path))

    def do_POST(self) -> None:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._send({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        self._send(route_rpc("POST", self.path, body))

    def do_OPTIONS(self) -> None:
        self._send({}, status=204)

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, payload: dict[str, Any], status: int | None = None) -> None:
        ok = payload.get("ok", True)
        response_status = status or (200 if ok else 404)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(response_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if response_status != 204:
            self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBrep React workbench local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
