from __future__ import annotations

import base64
import binascii
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType, VALID_PARAM_TYPES
from openbrep.paramlist_builder import validate_paramlist
from openbrep.runtime.pipeline import TaskRequest
from openbrep.workbench.settings_service import save_workbench_config
from ui.three_preview import preview_3d_to_three_payload
from ui.view_models import classify_vision_error


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

GDL_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AUTHORABLE_PARAM_TYPES = {"Length", "RealNum", "Integer", "Boolean", "String"}
MAX_WORKBENCH_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_WORKBENCH_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_DEMO_PROJECT: HSFProject | None = None


class WorkbenchProjectService:
    def __init__(
        self,
        session: Any,
        *,
        real_compiler_factory: Callable[[str | None], Any],
    ) -> None:
        self.session = session
        self.real_compiler_factory = real_compiler_factory

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        hsf_path = Path(path).expanduser().resolve()
        if not hsf_path.is_dir():
            return {"ok": False, "error": f"HSF directory not found: {path}"}

        try:
            project = HSFProject.load_from_disk(str(hsf_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load HSF project: {exc}"}

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = hsf_path
        self.remember_project_path(hsf_path)
        return {"ok": True, **self.session.snapshot()}

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.session.file_chooser()
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
        project_name = unique_project_name(safe_project_name(source_file.stem), source_file.parent)
        project = HSFProject.create_new(project_name, str(source_file.parent))
        project.description = f"Imported from {source_file.name}"
        project.set_script(script_type, content)
        hsf_dir = project.save_to_disk()

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = hsf_dir
        self.remember_project_path(hsf_dir)
        return {"ok": True, "imported_from": str(source_file), **self.session.snapshot()}

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.session.file_chooser()
            except Exception as exc:
                return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not raw_path:
            return {"ok": False, "cancelled": True, "error": "GSM file selection cancelled."}

        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.is_file():
            return {"ok": False, "error": f"GSM file not found: {raw_path}"}
        if source_file.suffix.lower() != ".gsm":
            return {"ok": False, "error": f"Unsupported file type: {source_file.suffix or '(none)'}"}
        if self.session.compiler_mode != "lp":
            return {
                "ok": False,
                "error": "GSM import requires LP_XMLConverter mode. Open settings and select Real compiler first.",
            }

        compiler = self.real_compiler_factory(self.session.converter_path)
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

            hsf_root = find_hsf_root(hsf_out)
            if hsf_root is None:
                contents = sorted(path.name for path in hsf_out.iterdir()) if hsf_out.exists() else []
                return {"ok": False, "error": f"Could not locate HSF root in converter output: {contents}"}

            target_name = unique_project_name(safe_project_name(source_file.stem), source_file.parent)
            target_dir = source_file.parent / target_name
            shutil.copytree(hsf_root, target_dir)
            project = HSFProject.load_from_disk(str(target_dir))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to import GSM file: {exc}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = target_dir
        self.remember_project_path(target_dir)
        return {
            "ok": True,
            "imported_from": str(source_file),
            "decompile": {
                "mode": "lp",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            **self.session.snapshot(),
        }

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        prompt = str(body.get("prompt") or body.get("message") or "").strip()
        if not prompt:
            return {"ok": False, "error": "Create prompt is empty."}
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        output_root = Path(str(body.get("output_dir") or "./output")).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        requested_name = str(body.get("project_name") or "").strip()
        project_name = unique_project_name(
            safe_project_name(requested_name or project_name_from_prompt(prompt)),
            output_root,
        )
        target_dir = output_root / project_name
        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline = self.session.pipeline_class(trace_dir="./traces")
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            pipeline.config.agent.max_iterations = self.session.max_retries

        result = pipeline.execute(
            TaskRequest(
                user_input=prompt,
                intent="IMAGE" if image_payload["image_b64"] else "CREATE",
                work_dir=str(output_root),
                output_dir=str(output_root),
                gsm_name=project_name,
                image_b64=image_payload["image_b64"],
                image_mime=image_payload["image_mime"],
                assistant_settings=str(body.get("assistant_settings") or self.session.assistant_settings),
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
        self.session.project = result.project
        self.session.source = "hsf"
        self.session.source_path = hsf_dir
        self.remember_project_path(hsf_dir)
        return {
            "ok": True,
            "assistant": {
                "kind": "create",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
            },
            "events": events,
            **self.session.snapshot(),
        }

    def close_project(self) -> dict[str, Any]:
        self.session.project = build_demo_project()
        self.session.source = "demo"
        self.session.source_path = None
        return {"ok": True, **self.session.snapshot()}

    def export_hsf_project(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_parent = str(body.get("parent_dir") or "").strip()
        if not raw_parent:
            try:
                raw_parent = self.session.directory_chooser()
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

        project_name = safe_project_name(str(body.get("name") or self.session.project.name or "OpenBrep_Project"))
        target = (parent / project_name).resolve()
        previous_source = self.session.source_path.expanduser().resolve() if self.session.source_path else None
        current_root = self.session.project.root.expanduser().resolve() if self.session.project.root else None
        allowed_existing_roots = {root for root in (previous_source, current_root) if root is not None}
        if target.exists() and target not in allowed_existing_roots and any(target.iterdir()):
            return {"ok": False, "error": f"Target HSF directory already exists and is not empty: {target}"}

        self.session.project.name = project_name
        self.session.project.work_dir = parent
        self.session.project.root = target
        try:
            saved_root = self.session.project.save_to_disk().expanduser().resolve()
            if previous_source is not None and previous_source.exists() and previous_source != saved_root:
                try:
                    from openbrep.revisions import copy_project_metadata

                    copy_project_metadata(previous_source, saved_root)
                except Exception:
                    pass
            self.session.source = "hsf"
            self.session.source_path = saved_root
            self.remember_project_path(saved_root)
            self.session.project = HSFProject.load_from_disk(str(saved_root))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to export HSF project: {exc}"}

        return {"ok": True, "saved_to": str(saved_root), **self.session.snapshot()}

    def recent_projects(self) -> dict[str, Any]:
        return {
            "ok": True,
            "projects": [
                recent_project_to_api(path)
                for path in self.session.recent_project_paths
            ],
        }

    def list_project_revisions(self) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before reading revisions.", "revisions": []}
        try:
            from openbrep.revisions import get_latest_revision_id, list_revisions

            latest = get_latest_revision_id(self.session.source_path)
            revisions = [
                revision_to_api_item(revision, latest_revision_id=latest)
                for revision in reversed(list_revisions(self.session.source_path))
            ]
        except Exception as exc:
            return {"ok": False, "error": f"Failed to read revisions: {exc}", "revisions": []}
        return {"ok": True, "revisions": revisions, "latest_revision_id": latest}

    def save_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before saving revisions."}
        try:
            from openbrep.revisions import create_revision, get_latest_revision_id

            self.session.project.save_to_disk()
            message = str(body.get("message") or "").strip()
            revision = create_revision(
                self.session.source_path,
                message=message,
                gsm_name=self.session.project.name,
                trigger="manual",
                parent_revision_id=get_latest_revision_id(self.session.source_path),
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save revision: {exc}"}
        return {
            "ok": True,
            "revision": revision_to_api_item(revision, latest_revision_id=revision.revision_id),
            "latest_revision_id": revision.revision_id,
        }

    def restore_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.source_path is None:
            return {"ok": False, "error": "Load an HSF project before restoring revisions."}
        revision_id = str(body.get("revision_id") or "").strip()
        if not revision_id:
            return {"ok": False, "error": "Revision id is required."}
        try:
            from openbrep.revisions import restore_revision

            restored = restore_revision(
                self.session.source_path,
                revision_id,
                message=f"workbench restore {revision_id}",
            )
            self.session.project = HSFProject.load_from_disk(str(self.session.source_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to restore revision: {exc}"}
        return {
            "ok": True,
            "restored_revision_id": revision_id,
            "revision": revision_to_api_item(restored, latest_revision_id=restored.revision_id),
            "latest_revision_id": restored.revision_id,
            **self.session.snapshot(),
        }

    def remember_project_path(self, path: Path) -> None:
        normalized = str(path.expanduser().resolve())
        self.session.recent_project_paths = [
            normalized,
            *[item for item in self.session.recent_project_paths if item != normalized],
        ][:8]
        self.session.config.recent_projects = self.session.recent_project_paths
        save_workbench_config(self.session.config, self.session.config_path)

    def choose_and_load_hsf_directory(self) -> dict[str, Any]:
        try:
            selected = self.session.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        loaded = self.load_hsf_directory(selected)
        if loaded.get("ok"):
            loaded["path"] = str(Path(selected).expanduser().resolve())
        return loaded

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_payload(self.session.project, overrides or {}),
        }

    def preview_2d(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "preview": preview_2d_payload(self.session.project, overrides or {}),
        }

    def list_project_scripts(self) -> dict[str, Any]:
        return {"ok": True, "scripts": [script_file_info(self.session.project, name) for name in SCRIPT_FILE_ORDER]}

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        path = script_relative_path(resolved)
        content = read_project_file_content(self.session.project, resolved)
        if content is None:
            return {"ok": False, "error": f"Script file not found: {resolved}"}
        return {"ok": True, "name": resolved, "path": path, "content": content}

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_script_name(script_name)
        if resolved is None:
            return {"ok": False, "error": f"Unsupported script file: {script_name}"}
        content = str(body.get("content") or "")
        script_type = SCRIPT_NAME_TO_TYPE.get(resolved)
        if script_type is not None:
            self.session.project.set_script(script_type, content)
            self.session.project.save_to_disk()
        else:
            if self.session.source_path is None:
                return {"ok": False, "error": "Load an HSF project before saving XML files."}
            target = project_file_path(self.session.project, resolved)
            if target is None:
                return {"ok": False, "error": f"Unsupported script file: {script_name}"}
            target.write_text(content, encoding="utf-8-sig")
            self.session.project = HSFProject.load_from_disk(str(self.session.source_path))
        return {
            "ok": True,
            "success": True,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        changed = apply_parameter_values(self.session.project, changes)
        if changed and self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {"ok": True, "changed": changed, **self.session.snapshot()}

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            param = build_parameter_from_authoring_request(self.session.project, body)
            self.session.project.add_parameter(param)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.session.project.save_to_disk()
        return {
            "ok": True,
            "added": parameter_to_dict(param),
            **self.session.snapshot(),
        }

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}

        try:
            new_name = validate_authorable_parameter_name(
                self.session.project,
                str(body.get("new_name") if "new_name" in body else param.name),
                current_name=param.name,
            )
            new_type = validate_authorable_type(
                str(body.get("type_tag") if "type_tag" in body else param.type_tag)
            )
            if param.is_fixed and (new_name != param.name or new_type != param.type_tag):
                return {"ok": False, "error": f"Fixed parameter '{param.name}' cannot be renamed or retagged"}
            if "value" in body:
                param.value = coerce_parameter_value(new_type, body.get("value"))
            if "description" in body:
                param.description = str(body.get("description") or "").strip()
            param.name = new_name
            param.type_tag = new_type
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.session.project.save_to_disk()
        return {
            "ok": True,
            "updated": parameter_to_dict(param),
            **self.session.snapshot(),
        }

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}
        if param.is_fixed:
            return {"ok": False, "error": f"Fixed parameter '{name}' cannot be deleted"}
        self.session.project.remove_parameter(name)
        self.session.project.save_to_disk()
        return {
            "ok": True,
            "deleted": name,
            **self.session.snapshot(),
        }

    def validate_project_parameters(self) -> dict[str, Any]:
        return {
            "ok": True,
            "issues": validate_paramlist(self.session.project.parameters or []),
        }


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


def demo_project() -> HSFProject:
    global _DEMO_PROJECT
    if _DEMO_PROJECT is None:
        _DEMO_PROJECT = build_demo_project()
    return _DEMO_PROJECT


def build_demo_snapshot() -> dict[str, Any]:
    return project_to_snapshot(demo_project())


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
        "parameters": [parameter_to_dict(param) for param in project.parameters],
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
        numeric = to_preview_number(param.value)
        if numeric is not None:
            values[param.name.upper()] = numeric
    for key, value in (overrides or {}).items():
        numeric = to_preview_number(value)
        if numeric is not None:
            values[str(key).upper()] = numeric
    return values


def apply_parameter_values(project: HSFProject, changes: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for name, value in changes.items():
        param = project.get_parameter(name)
        if param is None:
            continue
        param.value = coerce_parameter_value(param.type_tag, value)
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

    value = coerce_parameter_value(type_tag, body.get("value"))
    description = str(body.get("description") or "").strip()
    return GDLParameter(name=name, type_tag=type_tag, description=description, value=value)


def validate_authorable_parameter_name(project: HSFProject, name: str, *, current_name: str = "") -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(cleaned):
        raise ValueError("Invalid parameter name.")
    if cleaned != current_name and project.get_parameter(cleaned) is not None:
        raise ValueError(f"Parameter '{cleaned}' already exists")
    return cleaned


def validate_authorable_type(type_tag: str) -> str:
    cleaned = str(type_tag or "").strip()
    if not cleaned:
        raise ValueError("Parameter type is required.")
    if cleaned not in AUTHORABLE_PARAM_TYPES or cleaned not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {cleaned}")
    return cleaned


def safe_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\- ]+", "_", str(name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "Imported_GDL"


def project_name_from_prompt(prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9_\-]+", prompt)
    if words:
        return "_".join(words[:4])
    return "Generated_Object"


def validate_image_payload(body: dict[str, Any]) -> dict[str, Any]:
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


def unique_project_name(base_name: str, work_dir: Path) -> str:
    candidate = base_name
    suffix = 2
    while (work_dir / candidate).exists():
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def revision_to_api_item(revision, *, latest_revision_id: str | None = None) -> dict[str, Any]:
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


def recent_project_to_api(path: str) -> dict[str, Any]:
    project_path = Path(path)
    return {
        "path": str(project_path),
        "name": project_path.name or str(project_path),
        "parent_dir": str(project_path.parent) if project_path.parent != Path(".") else "",
        "exists": project_path.is_dir(),
    }


def find_hsf_root(base: Path) -> Path | None:
    if not base.exists():
        return None
    if (base / "libpartdata.xml").exists() and (base / "scripts").is_dir():
        return base
    for candidate in base.rglob("libpartdata.xml"):
        root = candidate.parent
        if (root / "scripts").is_dir():
            return root
    return None


def resolve_script_name(script_name: str) -> str | None:
    cleaned = Path(str(script_name)).name.lower()
    for name in SCRIPT_FILE_ORDER:
        if cleaned == name.lower():
            return name
    return None


def script_relative_path(script_name: str) -> str:
    return script_name if script_name.endswith(".xml") else f"scripts/{script_name}"


def project_file_path(project: HSFProject, script_name: str) -> Path | None:
    resolved = resolve_script_name(script_name)
    if resolved is None:
        return None
    return project.root / script_relative_path(resolved)


def script_file_info(project: HSFProject, script_name: str) -> dict[str, Any]:
    path = project_file_path(project, script_name)
    content = read_project_file_content(project, script_name)
    return {
        "name": script_name,
        "path": script_relative_path(script_name),
        "exists": bool(path and path.exists()),
        "size": len(content.encode("utf-8")) if content is not None else 0,
        "empty": not bool((content or "").strip()),
    }


def read_project_file_content(project: HSFProject, script_name: str) -> str | None:
    script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
    if script_type is not None:
        return project.get_script(script_type) or ""
    if project.root is None:
        return None
    file_path = project_file_path(project, script_name)
    if file_path is None or not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8-sig")


def parameter_to_dict(param: GDLParameter) -> dict[str, Any]:
    return {
        "name": param.name,
        "type": param.type_tag,
        "type_tag": param.type_tag,
        "description": param.description,
        "value": param.value,
        "is_fixed": bool(param.is_fixed),
    }


def coerce_parameter_value(type_tag: str, value: Any) -> str:
    if type_tag == "Boolean":
        if isinstance(value, bool):
            return "1" if value else "0"
        return "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"
    if type_tag == "Integer":
        return str(int(float(value or 0)))
    if type_tag in {"Length", "RealNum"}:
        return str(float(value or 0))
    return str(value or "")


def to_preview_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"true", "yes", "on"}:
        return 1.0
    if text.lower() in {"false", "no", "off"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None
