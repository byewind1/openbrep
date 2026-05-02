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
    reload_libraries_after_compile_fn: Callable[[], tuple[bool, str] | None] | None = None
    choose_directory_fn: Callable[[str | None], str | None] | None = None
    choose_path_fn: Callable[[str | None], str | None] | None = None

    def do_compile(self, proj, gsm_name: str, instruction: str = "") -> tuple[bool, str]:
        sync_visible_editor_buffers_fn = getattr(self, "sync_visible_editor_buffers_fn", None)
        if sync_visible_editor_buffers_fn is not None:
            try:
                sync_visible_editor_buffers_fn(proj)
            except Exception as exc:
                return False, f"❌ **错误**: 同步编辑器内容失败：{exc}"

        ok, msg = project_io.do_compile(
            proj,
            gsm_name,
            instruction,
            session_state=self.session_state,
            safe_compile_revision_fn=view_models.safe_compile_revision,
            versioned_gsm_path_fn=view_models.versioned_gsm_path,
            get_compiler_fn=self.get_compiler_fn,
            compiler_mode=self.compiler_mode,
        )
        if ok and not self.compiler_mode.startswith("Mock") and self.reload_libraries_after_compile_fn:
            reload_result = self.reload_libraries_after_compile_fn()
            if reload_result is not None:
                _reload_ok, reload_msg = reload_result
                if reload_msg:
                    msg += f"\n\n{reload_msg}"
        return ok, msg

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

    def browse_and_load_hsf_directory(self) -> tuple[bool, str]:
        if self.choose_directory_fn is None:
            return False, "❌ 当前运行环境不支持本地目录选择，请使用支持系统目录选择器的桌面环境"

        if hasattr(self.session_state, "get"):
            initial_dir = self.session_state.get("editor_hsf_dir", "")
        else:
            initial_dir = getattr(self.session_state, "editor_hsf_dir", "")
        selected = self.choose_directory_fn(initial_dir or None)
        if not selected:
            return False, "已取消选择 HSF 项目目录"

        self.session_state.editor_hsf_dir = selected
        return self.handle_hsf_directory_load(selected)

    def open_project_source_path(self, source_path: str) -> tuple[bool, str]:
        import_gsm_fn = self.import_gsm_override_fn or self.import_gsm
        return project_io.handle_open_path(
            source_path,
            normalize_pasted_path_fn=view_models.normalize_pasted_path,
            load_project_from_disk_fn=self.load_project_from_disk_fn,
            import_gsm_fn=import_gsm_fn,
            parse_gdl_source_fn=self.parse_gdl_source_fn,
            derive_gsm_name_from_filename_fn=view_models.derive_gsm_name_from_filename,
            finalize_loaded_project_fn=self.finalize_loaded_project,
        )

    def browse_and_open_project_source(self) -> tuple[bool, str]:
        chooser = self.choose_path_fn or self.choose_directory_fn
        if chooser is None:
            return False, "❌ 当前运行环境不支持本地文件/目录选择，请在本机桌面环境运行 OpenBrep"

        initial_dir = ""
        if hasattr(self.session_state, "get"):
            initial_dir = (
                self.session_state.get("editor_open_path", "")
                or self.session_state.get("editor_hsf_dir", "")
                or self.session_state.get("work_dir", "")
            )
        else:
            initial_dir = (
                getattr(self.session_state, "editor_open_path", "")
                or getattr(self.session_state, "editor_hsf_dir", "")
                or getattr(self.session_state, "work_dir", "")
            )
        selected = chooser(initial_dir or None)
        if not selected:
            return False, "已取消打开"

        self.session_state.editor_open_path = selected
        self.session_state.editor_hsf_dir = selected
        return self.open_project_source_path(selected)

    def handle_unified_import(self, uploaded_file) -> tuple[bool, str]:
        import_gsm_fn = self.import_gsm_override_fn or self.import_gsm
        return project_io.handle_unified_import(
            uploaded_file,
            import_gsm_fn=import_gsm_fn,
            parse_gdl_source_fn=self.parse_gdl_source_fn,
            derive_gsm_name_from_filename_fn=view_models.derive_gsm_name_from_filename,
            finalize_loaded_project_fn=self.finalize_loaded_project,
        )

    def finalize_loaded_project(
        self,
        proj,
        msg: str,
        pending_gsm_name: str,
        *,
        preserve_project_root: bool = False,
    ) -> tuple[bool, str]:
        result = ui_actions.finalize_loaded_project(
            proj,
            msg,
            pending_gsm_name,
            self.session_state,
            self.reset_tapir_p0_state_fn,
            self.bump_main_editor_version_fn,
            preserve_project_root=preserve_project_root,
        )
        if self.reset_revision_ui_state_fn is not None:
            self.reset_revision_ui_state_fn(self.session_state)
        return result
