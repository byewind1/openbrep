from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import platform
import re
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import ALL_MODELS, GDLAgentConfig, model_to_provider
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
from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType, VALID_PARAM_TYPES
from openbrep.learning import ErrorLearningStore
from openbrep.llm import LLMAdapter
from openbrep.paramlist_builder import validate_paramlist
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.workbench_tapir import WorkbenchTapirAdapter, default_tapir_bridge_loader
from ui.three_preview import preview_3d_to_three_payload
from ui.view_models import classify_code_blocks, classify_vision_error


_DEMO_PROJECT: HSFProject | None = None
_DEFAULT_SESSION: WorkbenchSession | None = None


SCRIPT_FILE_ORDER = [
    "3d.gdl",
    "2d.gdl",
    "1d.gdl",
    "vl.gdl",
    "pr.gdl",
    "ui.gdl",
    "paramlist.xml",
    "libpartdata.xml",
]

SCRIPT_NAME_TO_TYPE = {
    ScriptType.SCRIPT_3D.value: ScriptType.SCRIPT_3D,
    ScriptType.SCRIPT_2D.value: ScriptType.SCRIPT_2D,
    ScriptType.MASTER.value: ScriptType.MASTER,
    ScriptType.PARAM.value: ScriptType.PARAM,
    ScriptType.PROPERTIES.value: ScriptType.PROPERTIES,
    ScriptType.UI.value: ScriptType.UI,
}
SCRIPT_ROUTE_RE = re.compile(r"^/api/project/script/([^/]+)$")
MEMORY_LESSON_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)$")
MEMORY_LESSON_IGNORE_ROUTE_RE = re.compile(r"^/api/memory/lessons/([^/]+)/ignore$")
GDL_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AUTHORABLE_PARAM_TYPES = {"Length", "RealNum", "Integer", "Boolean", "String"}
MAX_WORKBENCH_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_WORKBENCH_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}


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


def preview_2d_payload(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    result = preview_2d_script(
        project.get_script(ScriptType.SCRIPT_2D),
        parameters=parameter_values(project, overrides),
        setup_script=project.get_script(ScriptType.MASTER),
        unknown_command_policy="warn",
        quality="fast",
    )
    return {
        "lines": [{"from": list(p1), "to": list(p2)} for p1, p2 in result.lines],
        "polygons": [[list(point) for point in polygon] for polygon in result.polygons],
        "circles": [
            {"cx": cx, "cy": cy, "r": r}
            for cx, cy, r in result.circles
        ],
        "arcs": [
            {"cx": cx, "cy": cy, "r": r, "a0": a0, "a1": a1}
            for cx, cy, r, a0, a1 in result.arcs
        ],
        "warnings": result.warnings,
    }


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


def build_parameter_from_authoring_request(project: HSFProject, body: dict[str, Any]) -> GDLParameter:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(name):
        raise ValueError("Invalid parameter name.")
    if project.get_parameter(name) is not None:
        raise ValueError(f"Parameter '{name}' already exists")

    type_tag = str(body.get("type_tag") or "").strip()
    if not type_tag:
        raise ValueError("Parameter type is required.")
    if type_tag not in AUTHORABLE_PARAM_TYPES or type_tag not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {type_tag}")

    value = _coerce_parameter_value(type_tag, body.get("value"))
    description = str(body.get("description") or "").strip()
    return GDLParameter(name=name, type_tag=type_tag, description=description, value=value)


def _validate_authorable_parameter_name(project: HSFProject, name: str, *, current_name: str = "") -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(cleaned):
        raise ValueError("Invalid parameter name.")
    if cleaned != current_name and project.get_parameter(cleaned) is not None:
        raise ValueError(f"Parameter '{cleaned}' already exists")
    return cleaned


def _validate_authorable_type(type_tag: str) -> str:
    cleaned = str(type_tag or "").strip()
    if not cleaned:
        raise ValueError("Parameter type is required.")
    if cleaned not in AUTHORABLE_PARAM_TYPES or cleaned not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {cleaned}")
    return cleaned


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
        self.config_path = Path(config_path or "config.toml")
        self.config = _load_workbench_config(self.config_path)
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
        default_bridge_fn, default_import_ok = default_tapir_bridge_loader()
        self.tapir = WorkbenchTapirAdapter(
            tapir_import_ok=default_import_ok if tapir_import_ok is None else tapir_import_ok,
            get_bridge_fn=get_tapir_bridge_fn or default_bridge_fn,
            now_text_fn=now_text_fn or _now_text,
        )

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
        return {
            "mode": self.compiler_mode,
            "converter_path": self.converter_path,
            "output_dir": self.output_dir,
        }

    def update_compiler_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode") or self.compiler_mode).strip().lower()
        if mode not in {"mock", "lp"}:
            return {"ok": False, "error": f"Unsupported compiler mode: {mode}"}
        self.compiler_mode = mode
        self.converter_path = str(body.get("converter_path") or "").strip()
        self.output_dir = str(body.get("output_dir") or "").strip()
        self.config.compiler.path = self.converter_path
        self.config.output_dir = self.output_dir or "./output"
        _save_workbench_config(self.config, self.config_path)
        return {"ok": True, "compiler": self.compiler_settings()}

    def llm_settings(self) -> dict[str, Any]:
        models = self.config.get_available_models()
        for model in ALL_MODELS:
            if model not in models:
                models.append(model)
        return {
            "model": self.llm_model,
            "models": models,
            "api_key": self.llm_api_key,
            "api_base": self.llm_api_base,
            "max_retries": self.max_retries,
            "assistant_settings": self.assistant_settings,
        }

    def update_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required."}
        self.llm_model = model
        self.llm_api_key = str(body.get("api_key") or "").strip()
        self.llm_api_base = str(body.get("api_base") or "").strip()
        self.assistant_settings = str(body.get("assistant_settings") or "")
        try:
            self.max_retries = max(1, min(10, int(body.get("max_retries") or self.max_retries)))
        except (TypeError, ValueError):
            self.max_retries = 5

        self.config.llm.model = self.llm_model
        self.config.llm.assistant_settings = self.assistant_settings
        self.config.agent.max_iterations = self.max_retries
        _apply_llm_credentials_to_config(
            self.config,
            model=self.llm_model,
            api_key=self.llm_api_key,
            api_base=self.llm_api_base,
        )
        _save_workbench_config(self.config, self.config_path)
        return {"ok": True, "llm": self.llm_settings()}

    def test_llm_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body.get("model") or self.llm_model).strip()
        if not model:
            return {"ok": False, "error": "Model is required.", "category": "llm_configuration"}

        test_config = copy.deepcopy(self.config)
        test_config.llm.model = model
        test_config.llm.assistant_settings = str(body.get("assistant_settings") or self.assistant_settings)
        test_config.llm.max_tokens = min(test_config.llm.max_tokens, 16)
        test_config.llm.timeout = min(test_config.llm.timeout, 20)
        _apply_llm_credentials_to_config(
            test_config,
            model=model,
            api_key=str(body.get("api_key") or "").strip(),
            api_base=str(body.get("api_base") or "").strip(),
        )

        start = time.perf_counter()
        try:
            response = LLMAdapter(test_config.llm).generate(
                [{"role": "user", "content": "Reply with OK."}],
                temperature=0,
                max_tokens=8,
                timeout=20,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc) or exc.__class__.__name__,
                "category": "llm_configuration",
                "model": model,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }

        return {
            "ok": True,
            "message": "LLM connection OK",
            "model": response.model or model,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }

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
        self._remember_project_path(hsf_path)
        return {"ok": True, **self.snapshot()}

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.file_chooser()
            except Exception as exc:
                return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not raw_path:
            return {"ok": False, "cancelled": True, "error": "GDL file selection cancelled."}

        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.is_file():
            return {"ok": False, "error": f"GDL file not found: {raw_path}"}
        if source_file.suffix.lower() != ".gdl":
            return {"ok": False, "error": f"Unsupported file type: {source_file.suffix or '(none)'}"}

        script_name = str(body.get("script_name") or ScriptType.SCRIPT_3D.value)
        script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
        if script_type is None:
            return {"ok": False, "error": f"Unsupported target script: {script_name}"}

        content = source_file.read_text(encoding="utf-8-sig")
        project_name = _unique_project_name(_safe_project_name(source_file.stem), source_file.parent)
        project = HSFProject.create_new(project_name, str(source_file.parent))
        project.description = f"Imported from {source_file.name}"
        project.set_script(script_type, content)
        hsf_dir = project.save_to_disk()

        self.project = project
        self.source = "hsf"
        self.source_path = hsf_dir
        self._remember_project_path(hsf_dir)
        return {"ok": True, "imported_from": str(source_file), **self.snapshot()}

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.file_chooser()
            except Exception as exc:
                return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not raw_path:
            return {"ok": False, "cancelled": True, "error": "GSM file selection cancelled."}

        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.is_file():
            return {"ok": False, "error": f"GSM file not found: {raw_path}"}
        if source_file.suffix.lower() != ".gsm":
            return {"ok": False, "error": f"Unsupported file type: {source_file.suffix or '(none)'}"}
        if self.compiler_mode != "lp":
            return {
                "ok": False,
                "error": "GSM import requires LP_XMLConverter mode. Open settings and select Real compiler first.",
            }

        compiler = HSFCompiler(self.converter_path)
        if not compiler.is_available:
            return {
                "ok": False,
                "error": f"LP_XMLConverter not found: {compiler.converter_path or '(not configured)'}",
            }

        tmp_dir = Path(tempfile.mkdtemp(prefix="openbrep-gsm-import-"))
        try:
            hsf_out = tmp_dir / "hsf_out"
            result = compiler.libpart2hsf(str(source_file), str(hsf_out))
            if not result.success:
                diag = result.stderr or result.stdout or "(no converter output)"
                return {
                    "ok": False,
                    "error": f"GSM decompile failed (exit={result.exit_code}): {diag[:800]}",
                }

            hsf_root = _find_hsf_root(hsf_out)
            if hsf_root is None:
                contents = sorted(path.name for path in hsf_out.iterdir()) if hsf_out.exists() else []
                return {"ok": False, "error": f"Could not locate HSF root in converter output: {contents}"}

            target_name = _unique_project_name(_safe_project_name(source_file.stem), source_file.parent)
            target_dir = source_file.parent / target_name
            shutil.copytree(hsf_root, target_dir)
            project = HSFProject.load_from_disk(str(target_dir))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to import GSM file: {exc}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.project = project
        self.source = "hsf"
        self.source_path = target_dir
        self._remember_project_path(target_dir)
        return {
            "ok": True,
            "imported_from": str(source_file),
            "decompile": {
                "mode": "lp",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            **self.snapshot(),
        }

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        prompt = str(body.get("prompt") or body.get("message") or "").strip()
        if not prompt:
            return {"ok": False, "error": "Create prompt is empty."}
        image_payload = _validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        output_root = Path(str(body.get("output_dir") or "./output")).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        requested_name = str(body.get("project_name") or "").strip()
        project_name = _unique_project_name(
            _safe_project_name(requested_name or _project_name_from_prompt(prompt)),
            output_root,
        )
        target_dir = output_root / project_name
        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline = self.pipeline_class(trace_dir="./traces")
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.llm_model
            if self.llm_api_key:
                pipeline.config.llm.api_key = self.llm_api_key
            if self.llm_api_base:
                pipeline.config.llm.api_base = self.llm_api_base
            pipeline.config.llm.assistant_settings = self.assistant_settings
            pipeline.config.agent.max_iterations = self.max_retries

        result = pipeline.execute(
            TaskRequest(
                user_input=prompt,
                intent="IMAGE" if image_payload["image_b64"] else "CREATE",
                work_dir=str(output_root),
                output_dir=str(output_root),
                gsm_name=project_name,
                image_b64=image_payload["image_b64"],
                image_mime=image_payload["image_mime"],
                assistant_settings=str(body.get("assistant_settings") or self.assistant_settings),
                history=list(body.get("history") or []),
                on_event=on_event,
            )
        )
        if not result.success or result.project is None:
            error = result.error or "Create failed."
            if image_payload["image_b64"]:
                error = classify_vision_error(Exception(error))
            return {"ok": False, "error": error, "events": events}

        result.project.name = project_name
        result.project.work_dir = target_dir.parent
        result.project.root = target_dir
        hsf_dir = result.project.save_to_disk()
        self.project = result.project
        self.source = "hsf"
        self.source_path = hsf_dir
        self._remember_project_path(hsf_dir)
        return {
            "ok": True,
            "assistant": {
                "kind": "create",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
            },
            "events": events,
            **self.snapshot(),
        }

    def close_project(self) -> dict[str, Any]:
        self.project = build_demo_project()
        self.source = "demo"
        self.source_path = None
        return {"ok": True, **self.snapshot()}

    def export_hsf_project(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_parent = str(body.get("parent_dir") or "").strip()
        if not raw_parent:
            try:
                raw_parent = self.directory_chooser()
            except Exception as exc:
                return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not raw_parent:
            return {"ok": False, "cancelled": True, "error": "HSF export directory selection cancelled."}

        parent = Path(raw_parent).expanduser().resolve()
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to create export directory: {exc}"}
        if not parent.is_dir():
            return {"ok": False, "error": f"Export target is not a directory: {parent}"}

        project_name = _safe_project_name(str(body.get("name") or self.project.name or "OpenBrep_Project"))
        target = (parent / project_name).resolve()
        previous_source = self.source_path.expanduser().resolve() if self.source_path else None
        current_root = self.project.root.expanduser().resolve() if self.project.root else None
        allowed_existing_roots = {root for root in (previous_source, current_root) if root is not None}
        if target.exists() and target not in allowed_existing_roots and any(target.iterdir()):
            return {"ok": False, "error": f"Target HSF directory already exists and is not empty: {target}"}

        self.project.name = project_name
        self.project.work_dir = parent
        self.project.root = target
        try:
            saved_root = self.project.save_to_disk().expanduser().resolve()
            if previous_source is not None and previous_source.exists() and previous_source != saved_root:
                try:
                    from openbrep.revisions import copy_project_metadata

                    copy_project_metadata(previous_source, saved_root)
                except Exception:
                    pass
            self.source = "hsf"
            self.source_path = saved_root
            self._remember_project_path(saved_root)
            self.project = HSFProject.load_from_disk(str(saved_root))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to export HSF project: {exc}"}

        return {"ok": True, "saved_to": str(saved_root), **self.snapshot()}

    def recent_projects(self) -> dict[str, Any]:
        return {
            "ok": True,
            "projects": [
                _recent_project_to_api(path)
                for path in self.recent_project_paths
            ],
        }

    def list_project_revisions(self) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before reading revisions.", "revisions": []}
        try:
            from openbrep.revisions import get_latest_revision_id, list_revisions

            latest = get_latest_revision_id(self.source_path)
            revisions = [
                _revision_to_api_item(revision, latest_revision_id=latest)
                for revision in reversed(list_revisions(self.source_path))
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read revisions: {exc}", "revisions": []}
        return {"ok": True, "revisions": revisions, "latest_revision_id": latest}

    def save_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before saving revisions."}
        try:
            from openbrep.revisions import create_revision, get_latest_revision_id

            self.project.save_to_disk()
            message = str(body.get("message") or "").strip()
            revision = create_revision(
                self.source_path,
                message=message,
                gsm_name=self.project.name,
                trigger="manual",
                parent_revision_id=get_latest_revision_id(self.source_path),
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save revision: {exc}"}
        return {
            "ok": True,
            "revision": _revision_to_api_item(revision, latest_revision_id=revision.revision_id),
            "latest_revision_id": revision.revision_id,
        }

    def restore_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before restoring revisions."}
        revision_id = str(body.get("revision_id") or "").strip()
        if not revision_id:
            return {"ok": False, "error": "Revision id is required."}
        try:
            from openbrep.revisions import restore_revision

            restored = restore_revision(
                self.source_path,
                revision_id,
                message=f"workbench restore {revision_id}",
            )
            self.project = HSFProject.load_from_disk(str(self.source_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to restore revision: {exc}"}
        return {
            "ok": True,
            "restored_revision_id": revision_id,
            "revision": _revision_to_api_item(restored, latest_revision_id=restored.revision_id),
            "latest_revision_id": restored.revision_id,
            **self.snapshot(),
        }

    def _remember_project_path(self, path: Path) -> None:
        normalized = str(path.expanduser().resolve())
        self.recent_project_paths = [
            normalized,
            *[item for item in self.recent_project_paths if item != normalized],
        ][:8]
        self.config.recent_projects = self.recent_project_paths
        _save_workbench_config(self.config, self.config_path)

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

    def choose_output_directory(self) -> dict[str, Any]:
        try:
            selected = self.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        self.output_dir = str(Path(selected).expanduser().resolve())
        self.config.output_dir = self.output_dir
        _save_workbench_config(self.config, self.config_path)
        return {"ok": True, "path": self.output_dir, "compiler": self.compiler_settings()}

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_payload(self.project, overrides or {}),
        }

    def preview_2d(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_2d_payload(self.project, overrides or {}),
        }

    def list_project_scripts(self) -> dict[str, Any]:
        return {"ok": True, "scripts": [_script_file_info(self.project, name) for name in SCRIPT_FILE_ORDER]}

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        resolved = _resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        path = _script_relative_path(resolved)
        content = _read_project_file_content(self.project, resolved)
        if content is None:
            return {"ok": False, "error": f"Script file not found: {resolved}"}
        return {"ok": True, "name": resolved, "path": path, "content": content}

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        resolved = _resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        content = str(body.get("content") or "")
        script_type = SCRIPT_NAME_TO_TYPE.get(resolved)
        if script_type is not None:
            self.project.set_script(script_type, content)
            if self.source_path is None:
                self.project.save_to_disk()
            else:
                self.project.save_to_disk()
        else:
            if self.source_path is None:
                return {"ok": False, "error": "Load an HSF project before saving XML files."}
            target = _project_file_path(self.project, resolved)
            if target is None:
                return {"ok": False, "error": f"Unsupported script file: {script_name}"}
            target.write_text(content, encoding="utf-8-sig")
            self.project = HSFProject.load_from_disk(str(self.source_path))
        return {
            "ok": True,
            "success": True,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        changed = apply_parameter_values(self.project, changes)
        if changed and self.source_path is not None:
            self.project.save_to_disk()
        return {"ok": True, "changed": changed, **self.snapshot()}

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            param = build_parameter_from_authoring_request(self.project, body)
            self.project.add_parameter(param)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.project.save_to_disk()
        return {
            "ok": True,
            "added": _parameter_to_dict(param),
            **self.snapshot(),
        }

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}

        try:
            new_name = _validate_authorable_parameter_name(
                self.project,
                str(body.get("new_name") if "new_name" in body else param.name),
                current_name=param.name,
            )
            new_type = _validate_authorable_type(
                str(body.get("type_tag") if "type_tag" in body else param.type_tag)
            )
            if param.is_fixed and (new_name != param.name or new_type != param.type_tag):
                return {"ok": False, "error": f"Fixed parameter '{param.name}' cannot be renamed or retagged"}
            if "value" in body:
                param.value = _coerce_parameter_value(new_type, body.get("value"))
            if "description" in body:
                param.description = str(body.get("description") or "").strip()
            param.name = new_name
            param.type_tag = new_type
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.project.save_to_disk()
        return {
            "ok": True,
            "updated": _parameter_to_dict(param),
            **self.snapshot(),
        }

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}
        if param.is_fixed:
            return {"ok": False, "error": f"Fixed parameter '{name}' cannot be deleted"}
        self.project.remove_parameter(name)
        self.project.save_to_disk()
        return {
            "ok": True,
            "deleted": name,
            **self.snapshot(),
        }

    def validate_project_parameters(self) -> dict[str, Any]:
        return {
            "ok": True,
            "issues": validate_paramlist(self.project.parameters or []),
        }

    def compile_mock(self, body: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter()
        if self.source_path is None:
            self.project.save_to_disk()
            hsf_dir = self.project.root
        else:
            self.project.save_to_disk()
            hsf_dir = self.source_path
        output_dir = _resolve_output_dir(body, self.output_dir, hsf_dir.parent / "output")
        output_dir = output_dir.expanduser().resolve()
        output_gsm = output_dir / f"{self.project.name}.gsm"
        result = MockHSFCompiler().hsf2libpart(str(hsf_dir), str(output_gsm))
        duration_ms = int((time.perf_counter() - start) * 1000)
        output_path = result.output_path or str(output_gsm)
        self.last_compile_output_path = output_path
        return {
            "ok": True,
            "success": bool(result.success),
            "mode": "mock",
            "issues": _compile_issues_from_result(result),
            "duration_ms": duration_ms,
            "output_path": output_path,
            "gsm_size_bytes": _file_size_or_none(output_gsm),
            "parameter_count": len(self.project.parameters or []),
        }

    def compile_project(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before compiling."}

        output_dir = _resolve_output_dir(body, self.output_dir, self.source_path.parent / "output")
        output_dir = output_dir.expanduser().resolve()
        output_gsm = output_dir / f"{self.project.name}.gsm"
        compiler_mode = str(body.get("compiler_mode") or self.compiler_mode)
        converter_path = body.get("converter_path")
        if converter_path is None:
            converter_path = self.converter_path
        compiler = HSFCompiler(str(converter_path) if converter_path else None) if compiler_mode == "lp" else MockHSFCompiler()

        self.project.save_to_disk()
        result = compiler.hsf2libpart(str(self.source_path), str(output_gsm))
        output_path = result.output_path or str(output_gsm)
        self.last_compile_output_path = output_path
        return {
            "ok": bool(result.success),
            "compile": {
                "success": bool(result.success),
                "mode": result.mode or ("lp" if compiler_mode == "lp" else "mock"),
                "output_path": output_path,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "errors": result.errors,
                "warnings": result.warnings,
                "gsm_size_bytes": _file_size_or_none(output_gsm),
                "parameter_count": len(self.project.parameters or []),
            },
            **({} if result.success else {"error": result.stderr or "Compile failed"}),
        }

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

    def list_assistant_history(self) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": True, "messages": []}
        try:
            entries = ErrorLearningStore(self.source_path).list_chat_transcript()
            messages = [
                {"role": entry.role if entry.role in {"user", "assistant"} else "assistant", "content": entry.content}
                for entry in entries
                if entry.content
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load assistant history: {exc}", "messages": []}
        return {"ok": True, "messages": messages}

    def save_assistant_history(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before saving assistant history."}
        messages = body.get("messages") or []
        if not isinstance(messages, list):
            return {"ok": False, "error": "Assistant history messages must be a list."}
        try:
            count = ErrorLearningStore(self.source_path).rewrite_chat_transcript(
                messages,
                project_name=self.project.name,
                source="react_workbench",
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save assistant history: {exc}"}
        return {"ok": True, "count": count}

    def clear_assistant_history(self) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": True, "count": 0}
        try:
            count = ErrorLearningStore(self.source_path).rewrite_chat_transcript(
                [],
                project_name=self.project.name,
                source="react_workbench",
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to clear assistant history: {exc}"}
        return {"ok": True, "count": count}

    def extract_assistant_code_blocks(self, body: dict[str, Any]) -> dict[str, Any]:
        content = str(body.get("content") or "")
        try:
            extracted = classify_code_blocks(content)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to extract assistant code blocks: {exc}", "blocks": []}
        blocks = [
            {
                "path": path,
                "script_name": Path(path).name,
                "content": script,
            }
            for path, script in extracted.items()
        ]
        return {"ok": True, "blocks": blocks}

    def memory_status(self) -> dict[str, Any]:
        if self.source_path is None:
            return {
                "ok": True,
                "memory": {
                    "memory_root": "",
                    "chat_count": 0,
                    "lesson_count": 0,
                    "has_learned_skill": False,
                    "total_bytes": 0,
                },
            }
        try:
            status = ErrorLearningStore(self.source_path).memory_status()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read project memory status: {exc}"}
        return {"ok": True, "memory": _memory_status_to_api(status)}

    def list_memory_lessons(self) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": True, "lessons": []}
        try:
            lessons = ErrorLearningStore(self.source_path).list_error_lessons(include_seed=False)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read project memory lessons: {exc}", "lessons": []}
        return {"ok": True, "lessons": [_error_lesson_to_api(lesson) for lesson in lessons]}

    def summarize_project_memory(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before summarizing project memory."}
        body = body or {}
        try:
            limit = int(body.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        limit = max(1, min(limit, 50))
        try:
            store = ErrorLearningStore(self.source_path)
            summary = store.summarize_to_skill(
                project_name=self.project.name,
                limit=limit,
                scan_chat=True,
                llm_refiner=None,
            )
            skill = store.load_learned_skill()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to summarize project memory: {exc}"}
        return {
            "ok": bool(summary.ok),
            "summary": _learning_summary_to_api(summary),
            "skill": skill,
            **({} if summary.ok else {"error": summary.message}),
        }

    def delete_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before deleting project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        try:
            deleted, remaining_count = ErrorLearningStore(self.source_path).delete_error_lesson(cleaned)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to delete project memory lesson: {exc}"}
        if not deleted:
            return {"ok": False, "error": "Project memory lesson was not found.", "remaining_count": remaining_count}
        return {"ok": True, "deleted": cleaned, "remaining_count": remaining_count}

    def ignore_memory_lesson(self, fingerprint: str) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before ignoring project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        try:
            ignored, remaining_count = ErrorLearningStore(self.source_path).ignore_error_lesson(cleaned)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to ignore project memory lesson: {exc}"}
        if not ignored:
            return {"ok": False, "error": "Project memory lesson was not found.", "remaining_count": remaining_count}
        return {"ok": True, "ignored": cleaned, "remaining_count": remaining_count}

    def update_memory_lesson(self, fingerprint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before editing project memory lessons."}
        cleaned = str(fingerprint or "").strip()
        if not cleaned:
            return {"ok": False, "error": "Lesson fingerprint is required."}
        body = body or {}
        updates = {
            key: body[key]
            for key in ("category", "summary", "guidance", "example")
            if key in body
        }
        if not updates:
            return {"ok": False, "error": "No editable lesson fields were provided."}
        try:
            lesson = ErrorLearningStore(self.source_path).update_error_lesson(cleaned, updates)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to update project memory lesson: {exc}"}
        if lesson is None:
            return {"ok": False, "error": "Project memory lesson was not found."}
        return {"ok": True, "lesson": _error_lesson_to_api(lesson)}

    def clear_project_memory(self) -> dict[str, Any]:
        if self.source_path is None:
            return {
                "ok": True,
                "before": {
                    "memory_root": "",
                    "chat_count": 0,
                    "lesson_count": 0,
                    "has_learned_skill": False,
                    "total_bytes": 0,
                },
            }
        try:
            before = ErrorLearningStore(self.source_path).clear_memory()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to clear project memory: {exc}"}
        return {"ok": True, "before": _memory_status_to_api(before)}

    def generate_with_assistant(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "Generation message is empty."}

        if self.source_path is None:
            return {"ok": False, "error": "Load an HSF project before generating changes."}
        image_payload = _validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

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
            image_b64=image_payload["image_b64"],
            image_mime=image_payload["image_mime"],
            assistant_settings=str(body.get("assistant_settings") or self.assistant_settings),
            history=list(body.get("history") or []),
            on_event=on_event,
        )
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.llm_model
            if self.llm_api_key:
                pipeline.config.llm.api_key = self.llm_api_key
            if self.llm_api_base:
                pipeline.config.llm.api_base = self.llm_api_base
            pipeline.config.llm.assistant_settings = self.assistant_settings
            pipeline.config.agent.max_iterations = self.max_retries
        result = pipeline.execute(request)
        if not result.success:
            error = result.error or "Generation failed."
            if image_payload["image_b64"]:
                error = classify_vision_error(Exception(error))
            return {"ok": False, "error": error, "events": events}

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

        if normalized_method == "POST" and route == "/api/dialog/open-directory":
            return self.choose_and_load_hsf_directory()

        if normalized_method == "POST" and route == "/api/dialog/open-file":
            return self.choose_file(body)

        if normalized_method == "POST" and route == "/api/dialog/output-directory":
            return self.choose_output_directory()

        if normalized_method == "POST" and route == "/api/settings/compiler":
            return self.update_compiler_settings(body)

        if normalized_method == "GET" and route == "/api/settings/runtime":
            return {"ok": True, "compiler": self.compiler_settings(), "llm": self.llm_settings()}

        if normalized_method == "POST" and route == "/api/settings/llm/test":
            return self.test_llm_settings(body)

        if normalized_method == "POST" and route == "/api/settings/llm":
            return self.update_llm_settings(body)

        if normalized_method == "GET" and route == "/api/tapir/status":
            return self.tapir.status_response()

        if normalized_method == "POST" and route == "/api/tapir/reload-libraries":
            return self.tapir.reload_libraries()

        if normalized_method == "POST" and route == "/api/tapir/selection/sync":
            return self.tapir.sync_selection()

        if normalized_method == "POST" and route == "/api/tapir/selection/highlight":
            return self.tapir.highlight_selection()

        if normalized_method == "POST" and route == "/api/tapir/parameters/load":
            return self.tapir.load_selected_params()

        if normalized_method == "POST" and route == "/api/tapir/parameters/apply":
            edits = body.get("param_edits")
            return self.tapir.apply_param_edits(edits if isinstance(edits, dict) else None)

        if normalized_method == "POST" and route == "/api/preview":
            return self.preview(body.get("parameters") or {})

        if normalized_method == "POST" and route == "/api/preview/2d":
            return self.preview_2d(body.get("parameters") or {})

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


def _load_workbench_config(config_path: Path) -> GDLAgentConfig:
    if config_path.exists():
        return GDLAgentConfig.load(str(config_path))
    return GDLAgentConfig()


def _save_workbench_config(config: GDLAgentConfig, config_path: Path) -> None:
    config.save(str(config_path))


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _safe_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\- ]+", "_", str(name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "Imported_GDL"


def _project_name_from_prompt(prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9_\-]+", prompt)
    if words:
        return "_".join(words[:4])
    return "Generated_Object"


def _validate_image_payload(body: dict[str, Any]) -> dict[str, Any]:
    image_b64 = str(body.get("image_b64") or "").strip()
    if not image_b64:
        return {"ok": True, "image_b64": None, "image_mime": "image/png"}

    image_mime = str(body.get("image_mime") or "image/png").strip().lower()
    if image_mime not in SUPPORTED_WORKBENCH_IMAGE_MIMES:
        supported = ", ".join(sorted(SUPPORTED_WORKBENCH_IMAGE_MIMES))
        return {"ok": False, "error": f"Unsupported image type: {image_mime}. Supported: {supported}."}

    try:
        raw = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        return {"ok": False, "error": "Invalid image data: expected base64 payload."}

    if len(raw) > MAX_WORKBENCH_IMAGE_BYTES:
        size_mb = len(raw) / (1024 * 1024)
        return {
            "ok": False,
            "error": f"Image is too large ({size_mb:.1f} MB). Please compress it to 5 MB or less.",
        }
    return {"ok": True, "image_b64": image_b64, "image_mime": image_mime}


def _unique_project_name(base_name: str, work_dir: Path) -> str:
    candidate = base_name
    suffix = 2
    while (work_dir / candidate).exists():
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def _revision_to_api_item(revision, *, latest_revision_id: str | None = None) -> dict[str, Any]:
    return {
        "revision_id": revision.revision_id,
        "project_name": revision.project_name,
        "gsm_name": revision.gsm_name,
        "created_at": revision.created_at,
        "message": revision.message,
        "file_count": len(revision.files or []),
        "trigger": revision.trigger,
        "intent": revision.intent,
        "user_instruction": revision.user_instruction,
        "changed_files": list(revision.changed_files or []),
        "parent_revision_id": revision.parent_revision_id,
        "compile": revision.compile or {},
        "explanation": revision.explanation,
        "is_latest": revision.revision_id == latest_revision_id,
    }


def _memory_status_to_api(status) -> dict[str, Any]:
    return {
        "memory_root": str(status.memory_root),
        "chat_count": status.chat_count,
        "lesson_count": status.lesson_count,
        "has_learned_skill": bool(status.has_learned_skill),
        "total_bytes": status.total_bytes,
    }


def _recent_project_to_api(path: str) -> dict[str, Any]:
    project_path = Path(path)
    return {
        "path": str(project_path),
        "name": project_path.name or str(project_path),
        "parent_dir": str(project_path.parent) if project_path.parent != Path(".") else "",
        "exists": project_path.is_dir(),
    }


def _error_lesson_to_api(lesson) -> dict[str, Any]:
    return {
        "fingerprint": lesson.fingerprint,
        "category": lesson.category,
        "summary": lesson.summary,
        "guidance": lesson.guidance,
        "example": lesson.example,
        "count": lesson.count,
        "first_seen": lesson.first_seen,
        "last_seen": lesson.last_seen,
        "source": lesson.source,
        "project_name": lesson.project_name,
        "raw_excerpt": lesson.raw_excerpt,
        "ignored": bool(getattr(lesson, "ignored", False)),
    }


def _learning_summary_to_api(summary) -> dict[str, Any]:
    return {
        "ok": bool(summary.ok),
        "lesson_count": summary.lesson_count,
        "path": str(summary.path),
        "message": summary.message,
    }


def _file_size_or_none(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


def _apply_llm_credentials_to_config(
    config: GDLAgentConfig,
    *,
    model: str,
    api_key: str,
    api_base: str,
) -> None:
    custom_match = config.llm._find_custom_provider_match(model)
    if custom_match is not None:
        provider = custom_match.get("provider")
        if isinstance(provider, dict):
            provider["api_key"] = api_key
            provider["base_url"] = api_base
        config.llm.api_key = api_key
        config.llm.api_base = api_base
        return

    provider_name = model_to_provider(model)
    if provider_name and provider_name != "custom" and api_key:
        config.llm.provider_keys[provider_name] = api_key
    config.llm.api_key = api_key
    config.llm.api_base = api_base


def _choose_directory() -> str:
    from ui.local_file_dialog import choose_directory

    return str(choose_directory(title="Open HSF project directory") or "")


def _choose_file() -> str:
    from ui.local_file_dialog import choose_file

    return str(choose_file(title="Choose OpenBrep file") or "")


def _find_hsf_root(base: Path) -> Path | None:
    if not base.exists():
        return None
    if (base / "libpartdata.xml").exists() or (base / "scripts").is_dir():
        return base
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "libpartdata.xml").exists():
            return child
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "scripts").is_dir():
            return child
    subdirs = [child for child in sorted(base.iterdir()) if child.is_dir()]
    return subdirs[0] if subdirs else None


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


def _resolve_script_name(script_name: str) -> str | None:
    name = Path(str(script_name or "")).name
    if name != script_name:
        return None
    if name not in SCRIPT_FILE_ORDER:
        return None
    return name


def _script_relative_path(script_name: str) -> str:
    if script_name.endswith(".gdl"):
        return f"scripts/{script_name}"
    return script_name


def _project_file_path(project: HSFProject, script_name: str) -> Path | None:
    resolved = _resolve_script_name(script_name)
    if resolved is None:
        return None
    rel_path = _script_relative_path(resolved)
    return project.root / rel_path


def _script_file_info(project: HSFProject, script_name: str) -> dict[str, Any]:
    path = _script_relative_path(script_name)
    file_path = _project_file_path(project, script_name)
    script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
    memory_content = project.get_script(script_type) if script_type is not None else ""
    exists = bool(memory_content) or (file_path.exists() if file_path is not None else False)
    return {
        "name": script_name,
        "path": path,
        "exists": exists,
        "size": (
            len(memory_content.encode("utf-8"))
            if memory_content
            else file_path.stat().st_size if file_path is not None and exists else 0
        ),
    }


def _read_project_file_content(project: HSFProject, script_name: str) -> str | None:
    script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
    if script_type is not None:
        content = project.get_script(script_type)
        if content:
            return content
        file_path = _project_file_path(project, script_name)
        if file_path is not None and file_path.exists():
            return file_path.read_text(encoding="utf-8-sig")
        return None
    file_path = _project_file_path(project, script_name)
    if file_path is None or not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8-sig")


def _compile_issues_from_result(result) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for severity, lines in (("error", result.errors), ("warning", result.warnings)):
        for raw in lines or []:
            script, line, message = _parse_compile_issue(str(raw))
            issues.append({
                "severity": severity,
                "script": script,
                "line": line,
                "message": message,
            })
    return issues


def _resolve_output_dir(body: dict[str, Any], session_output_dir: str, fallback: Path) -> Path:
    configured = str(body.get("output_dir") or session_output_dir or "").strip()
    if configured:
        return Path(configured)
    return fallback


def _parse_compile_issue(raw: str) -> tuple[str, int | None, str]:
    match = re.search(r"Error in ([^:]+):\s*(.+)", raw)
    if match:
        return f"scripts/{match.group(1)}", None, match.group(2)
    match = re.search(r"([^:\s]+\.gdl).*?line\s+(\d+)[:\s-]*(.+)", raw, re.IGNORECASE)
    if match:
        return f"scripts/{Path(match.group(1)).name}", int(match.group(2)), match.group(3).strip()
    return "", None, raw


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
