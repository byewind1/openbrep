"""
Workbench service for Blender script import (BS2G).

Handles the conversion of Blender Python scripts to HSF projects
and loads the result into the workbench session.
"""

from __future__ import annotations

import tempfile
from typing import Any, TYPE_CHECKING

from openbrep.importers.blender_script.converter import convert_blender_script
from openbrep.workbench.project_session_service import project_to_snapshot

if TYPE_CHECKING:
    from openbrep.workbench_api import WorkbenchSession


class WorkbenchBlenderImportService:
    """Converts Blender scripts and loads results into the session."""

    def __init__(self, session: "WorkbenchSession") -> None:
        self.session = session

    def import_blender_script(self, body: dict[str, Any]) -> dict[str, Any]:
        script_content = body.get("script_content", "")
        function_name = body.get("function_name") or None
        use_llm = body.get("use_llm_completion", False)

        if not script_content.strip():
            return {"ok": False, "error": "script_content is empty"}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                project, ir = convert_blender_script(
                    script_content,
                    output_dir=tmp,
                    function_name=function_name,
                )

                if use_llm and self.session.llm_api_key:
                    self._run_llm_completion(project, ir)

                # Load into session
                self.session._project = project
                self.session.source = "blender_import"
                self.session.source_path = project.root
                self.session.project_epoch += 1

                return {
                    "ok": True,
                    "snapshot": project_to_snapshot(project),
                    "warnings": [
                        {"operation": w.operation, "reason": w.reason, "line": w.line}
                        for w in ir.warnings
                    ],
                    "parameters": [
                        {"name": p.name, "type": p.gdl_type, "default": p.default_value}
                        for p in ir.parameters
                    ],
                }

        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
        except Exception:
            pass  # Fallback 2D is already set
