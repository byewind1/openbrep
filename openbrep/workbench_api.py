from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.explainer.chat_adapter import build_chat_explanation_reply
from openbrep.explainer.context_builder import (
    build_project_context,
    build_project_parameter_context,
    build_project_script_context,
    resolve_parameter_targets,
    resolve_script_target,
)
from openbrep.explainer.service import (
    explain_parameter_context,
    explain_project_context,
    explain_script_context,
)
from openbrep.gdl_previewer import preview_3d_script
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from ui.three_preview import preview_3d_to_three_payload


_DEMO_PROJECT: HSFProject | None = None
_DEFAULT_SESSION: WorkbenchSession | None = None


def build_demo_project() -> HSFProject:
    project = HSFProject.create_new("Demo Bookshelf")
    project.parameters = [
        GDLParameter("A", "Length", "总宽", "1.2", is_fixed=True),
        GDLParameter("B", "Length", "总深", "0.36", is_fixed=True),
        GDLParameter("ZZYZX", "Length", "总高", "1.8", is_fixed=True),
        GDLParameter("shelf_count", "Integer", "层板数", "5"),
        GDLParameter("shelf_thickness", "Length", "层板厚度", "0.035"),
        GDLParameter("frame_thickness", "Length", "侧板厚度", "0.04"),
        GDLParameter("has_back_panel", "Boolean", "背板", "1"),
        GDLParameter("object_label", "String", "对象标签", "Bookshelf"),
    ]
    project.set_script(
        ScriptType.MASTER,
        """
inner_w = A - 2 * frame_thickness
gap = (ZZYZX - shelf_count * shelf_thickness) / (shelf_count + 1)
""".strip()
        + "\n",
    )
    project.set_script(
        ScriptType.SCRIPT_3D,
        """
! side panels
BLOCK frame_thickness, B, ZZYZX
ADDX A - frame_thickness
BLOCK frame_thickness, B, ZZYZX
DEL 1

! shelves
FOR i = 1 TO shelf_count
    ADDX frame_thickness
    ADDZ i * gap + (i - 1) * shelf_thickness
    BLOCK inner_w, B, shelf_thickness
    DEL 2
NEXT i

! optional back panel
IF has_back_panel = 1 THEN
    ADDY B - 0.018
    BLOCK A, 0.018, ZZYZX
    DEL 1
ENDIF
""".strip()
        + "\n",
    )
    return project


def _demo_project() -> HSFProject:
    global _DEMO_PROJECT
    if _DEMO_PROJECT is None:
        _DEMO_PROJECT = build_demo_project()
    return _DEMO_PROJECT


def build_demo_snapshot() -> dict[str, Any]:
    return project_to_snapshot(_demo_project())


def project_to_snapshot(
    project: HSFProject,
    *,
    source: str = "demo",
    source_path: str | None = None,
) -> dict[str, Any]:
    preview = preview_payload(project)
    return {
        "project": {
            "name": project.name,
            "source": source,
            **({"path": source_path} if source_path else {}),
        },
        "parameters": [_parameter_to_dict(param) for param in project.parameters],
        "preview": preview,
        "warnings": preview.get("warnings", []),
    }


def preview_payload(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    result = preview_3d_script(
        project.get_script(ScriptType.SCRIPT_3D),
        parameters=parameter_values(project, overrides),
        setup_script=project.get_script(ScriptType.MASTER),
        unknown_command_policy="warn",
        quality="fast",
    )
    payload = preview_3d_to_three_payload(result)
    payload["warnings"] = result.warnings
    return payload


def parameter_values(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, float]:
    values: dict[str, float] = {}
    for param in project.parameters:
        numeric = _to_preview_number(param.value)
        if numeric is not None:
            values[param.name.upper()] = numeric
    for name, value in (overrides or {}).items():
        numeric = _to_preview_number(value)
        if numeric is not None:
            values[str(name).upper()] = numeric
    return values


def apply_parameter_values(project: HSFProject, changes: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for name, value in changes.items():
        param = project.get_parameter(name)
        if param is None:
            continue
        param.value = _coerce_parameter_value(param.type_tag, value)
        changed[name] = value
    return changed


class WorkbenchSession:
    """Current-project state for the React workbench local API."""

    def __init__(
        self,
        *,
        pipeline_class: type = TaskPipeline,
        directory_chooser: Callable[[], str] | None = None,
        file_chooser: Callable[[], str] | None = None,
    ) -> None:
        self.project: HSFProject = build_demo_project()
        self.source = "demo"
        self.source_path: Path | None = None
        self.pipeline_class = pipeline_class
        self.directory_chooser = directory_chooser or _choose_directory
        self.file_chooser = file_chooser or _choose_file
        self.compiler_mode = "mock"
        self.converter_path = ""

    def snapshot(self) -> dict[str, Any]:
        snapshot = project_to_snapshot(
            self.project,
            source=self.source,
            source_path=str(self.source_path) if self.source_path else None,
        )
        snapshot["compiler"] = self.compiler_settings()
        return snapshot

    def compiler_settings(self) -> dict[str, str]:
        return {
            "mode": self.compiler_mode,
            "converter_path": self.converter_path,
        }

    def update_compiler_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode") or self.compiler_mode).strip().lower()
        if mode not in {"mock", "lp"}:
            return {"ok": False, "error": f"Unsupported compiler mode: {mode}"}
        self.compiler_mode = mode
        self.converter_path = str(body.get("converter_path") or "").strip()
        return {"ok": True, "compiler": self.compiler_settings()}

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        hsf_path = Path(path).expanduser().resolve()
        if not hsf_path.is_dir():
            return {"ok": False, "error": f"HSF directory not found: {path}"}

        try:
            project = HSFProject.load_from_disk(str(hsf_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load HSF project: {exc}"}

        self.project = project
        self.source = "hsf"
        self.source_path = hsf_path
        return {"ok": True, **self.snapshot()}

    def choose_and_load_hsf_directory(self) -> dict[str, Any]:
        try:
            selected = self.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        loaded = self.load_hsf_directory(selected)
        if loaded.get("ok"):
            loaded["path"] = str(Path(selected).expanduser().resolve())
        return loaded

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

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_payload(self.project, overrides or {}),
        }

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        changed = apply_parameter_values(self.project, changes)
        if changed and self.source_path is not None:
            self.project.save_to_disk()
        return {"ok": True, "changed": changed, **self.snapshot()}

    def compile_project(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before compiling."}

        output_dir = Path(str(body.get("output_dir") or self.source_path.parent / "output"))
        output_dir = output_dir.expanduser().resolve()
        output_gsm = output_dir / f"{self.project.name}.gsm"
        compiler_mode = str(body.get("compiler_mode") or self.compiler_mode)
        converter_path = body.get("converter_path")
        if converter_path is None:
            converter_path = self.converter_path
        compiler = (
            HSFCompiler(str(converter_path)) if compiler_mode == "lp" and converter_path else MockHSFCompiler()
        )

        self.project.save_to_disk()
        result = compiler.hsf2libpart(str(self.source_path), str(output_gsm))
        return {
            "ok": bool(result.success),
            "compile": {
                "success": bool(result.success),
                "mode": result.mode or ("lp" if compiler_mode == "lp" else "mock"),
                "output_path": result.output_path or str(output_gsm),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "errors": result.errors,
                "warnings": result.warnings,
            },
            **({} if result.success else {"error": result.stderr or "Compile failed"}),
        }

    def assistant_reply(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Assistant message is empty."}

        parameter_targets = resolve_parameter_targets(self.project, message)
        if parameter_targets:
            context = build_project_parameter_context(self.project, parameter_targets[0])
            if context is not None:
                explanation = explain_parameter_context(context)
                return {
                    "ok": True,
                    "assistant": {
                        "kind": "explain_parameter",
                        "reply": build_chat_explanation_reply(explanation, user_input=message),
                    },
                }

        script_target = resolve_script_target(message)
        if script_target:
            context = build_project_script_context(self.project, script_target)
            if context is not None:
                explanation = explain_script_context(context)
                return {
                    "ok": True,
                    "assistant": {
                        "kind": "explain_script",
                        "reply": build_chat_explanation_reply(explanation, user_input=message),
                    },
                }

        explanation = explain_project_context(build_project_context(self.project))
        return {
            "ok": True,
            "assistant": {
                "kind": "explain_project",
                "reply": build_chat_explanation_reply(explanation, user_input=message),
            },
        }

    def generate_with_assistant(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Generation message is empty."}

        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before generating changes."}

        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline = self.pipeline_class(trace_dir="./traces")
        request = TaskRequest(
            user_input=message,
            intent=str(body.get("intent") or "MODIFY"),
            project=self.project,
            work_dir=str(self.source_path.parent),
            output_dir=str(self.source_path.parent / "output"),
            gsm_name=self.project.name,
            assistant_settings=str(body.get("assistant_settings") or ""),
            history=list(body.get("history") or []),
            on_event=on_event,
        )
        result = pipeline.execute(request)
        if not result.success:
            return {"ok": False, "error": result.error or "Generation failed.", "events": events}

        if result.project is not None:
            self.project = result.project
        self.project.save_to_disk()
        return {
            "ok": True,
            "assistant": {
                "kind": "generate",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
            },
            "preview": preview_payload(self.project),
            "warnings": [],
            "events": events,
        }

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

        if normalized_method == "POST" and route == "/api/dialog/open-directory":
            return self.choose_and_load_hsf_directory()

        if normalized_method == "POST" and route == "/api/dialog/open-file":
            return self.choose_file(body)

        if normalized_method == "POST" and route == "/api/settings/compiler":
            return self.update_compiler_settings(body)

        if normalized_method == "POST" and route == "/api/preview":
            return self.preview(body.get("parameters") or {})

        if normalized_method == "POST" and route == "/api/apply":
            return self.apply(body.get("parameters") or {})

        if normalized_method == "POST" and route == "/api/compile":
            return self.compile_project(body)

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


def _choose_directory() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        return str(filedialog.askdirectory(title="Open HSF project directory") or "")
    finally:
        root.destroy()


def _choose_file() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        return str(filedialog.askopenfilename(title="Choose LP_XMLConverter") or "")
    finally:
        root.destroy()


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


def _parameter_to_dict(param: GDLParameter) -> dict[str, Any]:
    return {
        "name": param.name,
        "type_tag": param.type_tag,
        "description": param.description,
        "value": param.value,
        "is_fixed": param.is_fixed,
    }


def _coerce_parameter_value(type_tag: str, value: Any) -> str:
    if type_tag in {"Length", "Angle", "RealNum"}:
        return str(float(value))
    if type_tag == "Integer":
        return str(int(value))
    if type_tag == "Boolean":
        if isinstance(value, str):
            return "1" if value.strip().lower() in {"1", "true", "yes", "on"} else "0"
        return "1" if value else "0"
    return str(value)


def _to_preview_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    text = str(value).strip()
    if text.lower() in {"true", "yes", "on"}:
        return 1.0
    if text.lower() in {"false", "no", "off"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBrep React workbench local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
