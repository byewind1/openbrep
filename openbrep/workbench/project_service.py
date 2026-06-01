from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openbrep.workbench.preview_service import WorkbenchPreviewService, preview_payload
from openbrep.workbench.project_parameter_service import WorkbenchProjectParameterService, parameter_to_dict
from openbrep.workbench.project_script_service import WorkbenchProjectScriptService
from openbrep.workbench.project_session_service import (
    WorkbenchProjectSessionService,
    build_demo_project,
    build_demo_snapshot,
    project_to_snapshot,
    validate_image_payload,
)
from openbrep.workbench.revision_service import WorkbenchRevisionService


class WorkbenchProjectService:
    def __init__(
        self,
        session: Any,
        *,
        real_compiler_factory: Callable[[str | None], Any],
    ) -> None:
        self.session_service = WorkbenchProjectSessionService(
            session,
            real_compiler_factory=real_compiler_factory,
        )
        self.preview_service = WorkbenchPreviewService(session)
        self.script_service = WorkbenchProjectScriptService(session)
        self.parameter_service = WorkbenchProjectParameterService(session)
        self.revision_service = WorkbenchRevisionService(session)

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        return self.session_service.load_hsf_directory(path)

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.session_service.import_gdl_file(body)

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.session_service.import_gsm_file(body)

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.session_service.create_project_from_prompt(body)

    def close_project(self) -> dict[str, Any]:
        return self.session_service.close_project()

    def export_hsf_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.session_service.export_hsf_project(body)

    def recent_projects(self) -> dict[str, Any]:
        return self.session_service.recent_projects()

    def remember_project_path(self, path: Path) -> None:
        self.session_service.remember_project_path(path)

    def choose_and_load_hsf_directory(self) -> dict[str, Any]:
        return self.session_service.choose_and_load_hsf_directory()

    def list_project_revisions(self) -> dict[str, Any]:
        return self.revision_service.list_project_revisions()

    def save_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.revision_service.save_project_revision(body)

    def restore_project_revision(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.revision_service.restore_project_revision(body)

    def preview(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.preview_service.preview(overrides)

    def preview_2d(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.preview_service.preview_2d(overrides)

    def list_project_scripts(self) -> dict[str, Any]:
        return self.script_service.list_project_scripts()

    def get_project_script(self, script_name: str) -> dict[str, Any]:
        return self.script_service.get_project_script(script_name)

    def save_project_script(self, script_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.script_service.save_project_script(script_name, body)

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self.parameter_service.apply(changes)

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.parameter_service.add_project_parameter(body)

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.parameter_service.update_project_parameter(body)

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.parameter_service.delete_project_parameter(body)

    def validate_project_parameters(self) -> dict[str, Any]:
        return self.parameter_service.validate_project_parameters()
