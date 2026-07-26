"""
Workbench service for Blender script import (BS2G).

Handles the conversion of Blender Python scripts to HSF projects
and loads the result into the workbench session.

Source resolution order:
  1. ``script_content`` in the request body (paste / API path)
  2. ``path`` pointing to a .py file on disk
  3. native file chooser (purpose "blender") when neither is given

File-based imports persist the HSF project next to the source script
(durable directory, appears in recent projects).  Content-based
imports use a temporary directory kept alive for the session.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openbrep.importers.blender_script.converter import (
    convert_blender_script,
    probe_object_name,
)
from openbrep.workbench.project_session_service import (
    safe_project_name,
    unique_project_name,
)

if TYPE_CHECKING:
    from openbrep.workbench_api import WorkbenchSession


class WorkbenchBlenderImportService:
    """Converts Blender scripts and loads results into the session."""

    def __init__(self, session: "WorkbenchSession") -> None:
        self.session = session
        # Holds the tempdir of content-based imports open for the
        # session lifetime (Garbage-collecting it would delete the
        # project directory out from under the loaded project).
        self._content_tmp: tempfile.TemporaryDirectory | None = None

    def import_blender_script(self, body: dict[str, Any]) -> dict[str, Any]:
        script_content = str(body.get("script_content") or "")
        function_name = body.get("function_name") or None
        use_llm = body.get("use_llm_completion", False)

        source_file: Path | None = None
        if not script_content.strip():
            raw_path = str(body.get("path") or "").strip()
            if not raw_path:
                try:
                    raw_path = self.session._choose_file_for_purpose("blender")
                except Exception as exc:
                    return {"ok": False, "error": f"File chooser failed: {exc}"}
            if not raw_path:
                return {
                    "ok": False,
                    "cancelled": True,
                    "error": "Blender script selection cancelled.",
                }
            source_file = Path(raw_path).expanduser().resolve()
            if not source_file.is_file():
                return {"ok": False, "error": f"Blender script not found: {raw_path}"}
            if source_file.suffix.lower() != ".py":
                return {
                    "ok": False,
                    "error": f"Unsupported file type: {source_file.suffix or '(none)'}",
                }
            script_content = source_file.read_text(encoding="utf-8-sig")

        try:
            if source_file is not None:
                project, ir = self._convert_file_import(
                    script_content, source_file, function_name
                )
            else:
                self._content_tmp = tempfile.TemporaryDirectory()
                project, ir = convert_blender_script(
                    script_content,
                    output_dir=self._content_tmp.name,
                    function_name=function_name,
                )

            if use_llm and self.session.llm_api_key:
                self._run_llm_completion(project, ir)

            self.session.project = project
            self.session.source = "blender_import"
            self.session.source_path = project.root
            if source_file is not None:
                self.session.project_service.remember_project_path(project.root)

            snapshot = self.session.snapshot()
            snapshot["warnings"] = [
                f"line {w.line}: {w.operation} — {w.reason}" for w in ir.warnings
            ]
            if source_file is not None:
                snapshot["imported_from"] = str(source_file)
            return snapshot

        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _convert_file_import(
        self,
        script_content: str,
        source_file: Path,
        function_name: str | None,
    ) -> tuple[Any, Any]:
        """Convert and persist the project next to *source_file*.

        The project directory takes the script's object name
        (function name for primitive scripts, OBJ_NAME for mesh
        scripts), deduplicated against existing sibling directories
        (same rule as GDL import).
        """
        base_name = probe_object_name(
            script_content, function_name, script_path=str(source_file)
        )
        if base_name.startswith("<"):
            base_name = source_file.stem
        object_name = unique_project_name(
            safe_project_name(base_name), source_file.parent
        )
        return convert_blender_script(
            script_content,
            output_dir=str(source_file.parent),
            function_name=function_name,
            object_name=object_name,
            script_path=str(source_file),
        )

    def _run_llm_completion(self, project: Any, ir: Any) -> None:
        """Attempt LLM completion for 2D script; silently fall back."""
        try:
            from openbrep.importers.blender_script.llm_completion import complete_with_llm
            from openbrep.llm import LLMAdapter
            from openbrep.paramlist_builder import build_paramlist_xml
            from openbrep.hsf_project import ScriptType
            from openbrep.config import LLMConfig

            llm_config = LLMConfig(
                model=self.session.llm_model,
                api_key=self.session.llm_api_key,
                api_base=self.session.llm_api_base,
            )
            llm = LLMAdapter(llm_config)
            paramlist_xml = build_paramlist_xml(project.parameters)
            gdl_3d = project.get_script(ScriptType.SCRIPT_3D)
            scripts = complete_with_llm(ir, gdl_3d, paramlist_xml, llm)

            for filename, content in scripts.items():
                script_name = filename.replace("scripts/", "")
                for st in ScriptType:
                    if st.value == script_name:
                        project.set_script(st, content)
                        break
            project.save_to_disk()
        except Exception:
            pass  # Fallback 2D is already set
