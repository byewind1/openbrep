from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ui import actions as ui_actions
from ui import project_io
from ui import view_models


@dataclass
class ProjectService:
    session_state: object
    compiler_mode: str
    get_compiler_fn: Callable[[], object]
    mock_compiler_class: type
    parse_gdl_source_fn: Callable[[str, str], object]
    load_project_from_disk_fn: Callable[[str], object]
    reset_tapir_p0_state_fn: Callable[[], None]
    bump_main_editor_version_fn: Callable[[], None]
    import_gsm_override_fn: Callable[[bytes, str], tuple] | None = None
    reset_revision_ui_state_fn: Callable[[object], None] | None = None

    def do_compile(self, proj, gsm_name: str, instruction: str = "") -> tuple[bool, str]:
        return project_io.do_compile(
            proj,
            gsm_name,
            instruction,
            session_state=self.session_state,
            safe_compile_revision_fn=view_models.safe_compile_revision,
            versioned_gsm_path_fn=view_models.versioned_gsm_path,
            get_compiler_fn=self.get_compiler_fn,
            compiler_mode=self.compiler_mode,
        )

    def import_gsm(self, gsm_bytes: bytes, filename: str) -> tuple:
        return project_io.import_gsm(
            gsm_bytes,
            filename,
            get_compiler_fn=self.get_compiler_fn,
            mock_compiler_class=self.mock_compiler_class,
            work_dir=self.session_state.work_dir,
        )

    def handle_hsf_directory_load(self, project_dir: str) -> tuple[bool, str]:
        return project_io.handle_hsf_directory_load(
            project_dir,
            normalize_pasted_path_fn=view_models.normalize_pasted_path,
            load_project_from_disk_fn=self.load_project_from_disk_fn,
            finalize_loaded_project_fn=self.finalize_loaded_project,
        )

    def handle_unified_import(self, uploaded_file) -> tuple[bool, str]:
        import_gsm_fn = self.import_gsm_override_fn or self.import_gsm
        return project_io.handle_unified_import(
            uploaded_file,
            import_gsm_fn=import_gsm_fn,
            parse_gdl_source_fn=self.parse_gdl_source_fn,
            derive_gsm_name_from_filename_fn=view_models.derive_gsm_name_from_filename,
            finalize_loaded_project_fn=self.finalize_loaded_project,
        )

    def finalize_loaded_project(self, proj, msg: str, pending_gsm_name: str) -> tuple[bool, str]:
        result = ui_actions.finalize_loaded_project(
            proj,
            msg,
            pending_gsm_name,
            self.session_state,
            self.reset_tapir_p0_state_fn,
            self.bump_main_editor_version_fn,
        )
        if self.reset_revision_ui_state_fn is not None:
            self.reset_revision_ui_state_fn(self.session_state)
        return result
