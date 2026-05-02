import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ui.project_service import ProjectService


class _SessionState(dict):
    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value


class TestProjectService(unittest.TestCase):
    def test_do_compile_updates_compile_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            compiler_result = SimpleNamespace(success=True, stderr="", stdout="", exit_code=0)
            captured = {}

            def _hsf2libpart(hsf_dir, output_gsm):
                captured["hsf_dir"] = hsf_dir
                captured["output_gsm"] = output_gsm
                return compiler_result

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: SimpleNamespace(hsf2libpart=_hsf2libpart),
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )
            proj = SimpleNamespace(name="Chair", save_to_disk=lambda: Path(tmp) / "Chair")

            ok, msg = service.do_compile(proj, "Chair", "compile instruction")

            self.assertTrue(ok)
            self.assertIn("编译成功", msg)
            self.assertTrue(captured["output_gsm"].endswith("Chair_v1.gsm"))
            self.assertEqual(len(session_state.compile_log), 1)
            self.assertTrue(session_state.compile_log[0]["success"])

    def test_do_compile_reloads_archicad_libraries_after_real_compile_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            compiler_result = SimpleNamespace(success=True, stderr="", stdout="", exit_code=0)
            calls = {"reload": 0}

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP_XMLConverter (真实编译)",
                get_compiler_fn=lambda: SimpleNamespace(
                    hsf2libpart=lambda _hsf, _out: compiler_result
                ),
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                reload_libraries_after_compile_fn=lambda: (
                    calls.__setitem__("reload", calls["reload"] + 1) or True,
                    "🔄 已通知 Archicad 重载图库",
                ),
            )
            proj = SimpleNamespace(name="Chair", save_to_disk=lambda: Path(tmp) / "Chair")

            ok, msg = service.do_compile(proj, "Chair", "compile instruction")

            self.assertTrue(ok)
            self.assertEqual(calls["reload"], 1)
            self.assertIn("已通知 Archicad 重载图库", msg)

    def test_do_compile_syncs_visible_editor_buffers_before_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            compiler_result = SimpleNamespace(success=True, stderr="", stdout="", exit_code=0)
            calls = []

            def _hsf2libpart(_hsf_dir, _output_gsm):
                calls.append("compile")
                return compiler_result

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: SimpleNamespace(hsf2libpart=_hsf2libpart),
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )
            service.sync_visible_editor_buffers_fn = lambda _proj: calls.append("sync") or True
            proj = SimpleNamespace(name="Chair", save_to_disk=lambda: Path(tmp) / "Chair")

            ok, _msg = service.do_compile(proj, "Chair", "compile instruction")

            self.assertTrue(ok)
            self.assertEqual(calls, ["sync", "compile"])

    def test_do_compile_skips_archicad_reload_for_mock_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            compiler_result = SimpleNamespace(success=True, stderr="", stdout="", exit_code=0)
            calls = {"reload": 0}

            service = ProjectService(
                session_state=session_state,
                compiler_mode="Mock (无需 ArchiCAD)",
                get_compiler_fn=lambda: SimpleNamespace(
                    hsf2libpart=lambda _hsf, _out: compiler_result
                ),
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                reload_libraries_after_compile_fn=lambda: calls.__setitem__("reload", 1),
            )
            proj = SimpleNamespace(name="Chair", save_to_disk=lambda: Path(tmp) / "Chair")

            ok, msg = service.do_compile(proj, "Chair", "compile instruction")

            self.assertTrue(ok)
            self.assertEqual(calls["reload"], 0)
            self.assertNotIn("重载图库", msg)

    def test_do_compile_records_failed_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            compiler_result = SimpleNamespace(success=False, stderr="compile failed", stdout="", exit_code=1)
            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: SimpleNamespace(hsf2libpart=lambda _hsf, _out: compiler_result),
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )
            proj = SimpleNamespace(name="Chair", save_to_disk=lambda: Path(tmp) / "Chair")

            ok, msg = service.do_compile(proj, "Chair", "compile instruction")

            self.assertFalse(ok)
            self.assertIn("编译失败", msg)
            self.assertEqual(len(session_state.compile_log), 1)
            self.assertFalse(session_state.compile_log[0]["success"])
            self.assertIn("compile failed", session_state.compile_log[0]["message"])

    def test_handle_hsf_directory_load_finalizes_project_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            hsf_dir = Path(tmp) / "Chair"
            hsf_dir.mkdir()
            (hsf_dir / "libpartdata.xml").write_text("<LibraryPart/>", encoding="utf-8")
            session_state = _SessionState(
                work_dir=tmp,
                chat_history=[],
                pending_diffs={"x": "y"},
                revision_notice="old revision",
                revision_project_old_notice="old project revision",
                preview_2d_data={"old": True},
                preview_3d_data={"old": True},
                preview_warnings=["old"],
                preview_meta={"kind": "old"},
            )
            calls = {"reset": 0, "bump": 0}
            proj = SimpleNamespace(
                name="Chair",
                root=hsf_dir,
                parameters=[1, 2],
                scripts={"3d": "BLOCK 1,1,1"},
            )

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: proj,
                reset_tapir_p0_state_fn=lambda: calls.__setitem__("reset", calls["reset"] + 1),
                bump_main_editor_version_fn=lambda: calls.__setitem__("bump", calls["bump"] + 1),
                reset_revision_ui_state_fn=lambda state: [
                    state.pop(key, None)
                    for key in list(state.keys())
                    if str(key).startswith("revision")
                ],
            )

            ok, msg = service.handle_hsf_directory_load(str(hsf_dir))

            self.assertTrue(ok)
            self.assertIn("已加载 HSF 项目", msg)
            self.assertIn("源目录", msg)
            self.assertIs(session_state.project, proj)
            self.assertEqual(proj.root, hsf_dir.resolve())
            self.assertEqual(proj.work_dir, hsf_dir.resolve().parent)
            self.assertEqual(session_state.pending_gsm_name, "Chair")
            self.assertEqual(session_state.pending_diffs, {})
            self.assertEqual(session_state.chat_history, [])
            self.assertEqual(len(session_state.project_activity_log), 1)
            self.assertIn("已加载 HSF 项目", session_state.project_activity_log[0]["message"])
            self.assertNotIn("revision_notice", session_state)
            self.assertNotIn("revision_project_old_notice", session_state)
            self.assertIsNone(session_state.preview_2d_data)
            self.assertIsNone(session_state.preview_3d_data)
            self.assertEqual(calls, {"reset": 1, "bump": 1})

    def test_handle_hsf_directory_load_accepts_parent_with_single_hsf_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            hsf_dir = work_dir / "Chair"
            hsf_dir.mkdir()
            (hsf_dir / "libpartdata.xml").write_text("<LibraryPart/>", encoding="utf-8")
            captured = {}
            session_state = _SessionState(
                work_dir=str(work_dir / "default-workspace"),
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(name="Chair", root=hsf_dir, parameters=[], scripts={})

            def _load_project(path):
                captured["path"] = path
                return proj

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=_load_project,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )

            ok, _msg = service.handle_hsf_directory_load(str(work_dir))

            self.assertTrue(ok)
            self.assertEqual(Path(captured["path"]), hsf_dir)
            self.assertEqual(proj.root, hsf_dir.resolve())

    def test_browse_and_load_hsf_directory_loads_selected_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            hsf_dir = Path(tmp) / "Chair"
            hsf_dir.mkdir()
            (hsf_dir / "libpartdata.xml").write_text("<LibraryPart/>", encoding="utf-8")
            session_state = _SessionState(
                work_dir=tmp,
                editor_hsf_dir="",
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(name="Chair", root=hsf_dir, parameters=[], scripts={})

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: proj,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                choose_directory_fn=lambda _initial: str(hsf_dir),
            )

            ok, msg = service.browse_and_load_hsf_directory()

            self.assertTrue(ok)
            self.assertIn("已加载 HSF 项目", msg)
            self.assertEqual(session_state.editor_hsf_dir, str(hsf_dir))
            self.assertEqual(proj.root, hsf_dir.resolve())

    def test_browse_and_load_hsf_directory_reports_unsupported_without_chooser(self):
        session_state = _SessionState(
            work_dir="/tmp/workspace",
            editor_hsf_dir="",
            chat_history=[],
            pending_diffs={},
            preview_2d_data=None,
            preview_3d_data=None,
            preview_warnings=[],
            preview_meta={},
        )
        service = ProjectService(
            session_state=session_state,
            compiler_mode="LP",
            get_compiler_fn=lambda: None,
            mock_compiler_class=object,
            parse_gdl_source_fn=lambda *_args: None,
            load_project_from_disk_fn=lambda _path: None,
            reset_tapir_p0_state_fn=lambda: None,
            bump_main_editor_version_fn=lambda: None,
        )

        ok, msg = service.browse_and_load_hsf_directory()

        self.assertFalse(ok)
        self.assertIn("不支持本地目录选择", msg)

    def test_browse_and_load_hsf_directory_returns_empty_message_without_mutating_input(self):
        session_state = _SessionState(
            work_dir="/tmp/workspace",
            editor_hsf_dir="/existing/path",
            chat_history=[],
            pending_diffs={},
            preview_2d_data=None,
            preview_3d_data=None,
            preview_warnings=[],
            preview_meta={},
        )
        captured = {}
        service = ProjectService(
            session_state=session_state,
            compiler_mode="LP",
            get_compiler_fn=lambda: None,
            mock_compiler_class=object,
            parse_gdl_source_fn=lambda *_args: None,
            load_project_from_disk_fn=lambda _path: None,
            reset_tapir_p0_state_fn=lambda: None,
            bump_main_editor_version_fn=lambda: None,
            choose_directory_fn=lambda initial: captured.setdefault("initial", initial) and None,
        )

        ok, msg = service.browse_and_load_hsf_directory()

        self.assertFalse(ok)
        self.assertEqual(msg, "")
        self.assertEqual(captured["initial"], "/existing/path")
        self.assertEqual(session_state.editor_hsf_dir, "/existing/path")

    def test_browse_and_open_project_source_loads_selected_hsf_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            hsf_dir = Path(tmp) / "Chair"
            hsf_dir.mkdir()
            (hsf_dir / "libpartdata.xml").write_text("<LibraryPart/>", encoding="utf-8")
            session_state = _SessionState(
                work_dir=tmp,
                editor_open_path="",
                editor_hsf_dir="",
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(name="Chair", root=hsf_dir, parameters=[], scripts={})

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: proj,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                choose_path_fn=lambda _initial: str(hsf_dir),
            )

            ok, msg = service.browse_and_open_project_source()

            self.assertTrue(ok)
            self.assertIn("已加载 HSF 项目", msg)
            self.assertEqual(session_state.editor_open_path, str(hsf_dir))
            self.assertEqual(session_state.editor_hsf_dir, str(hsf_dir))
            self.assertEqual(proj.root, hsf_dir.resolve())

    def test_browse_and_open_project_file_imports_selected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            gdl_file = Path(tmp) / "chair.gdl"
            gdl_file.write_text("BLOCK 1, 1, 1\n", encoding="utf-8")
            session_state = _SessionState(
                work_dir=tmp,
                editor_open_path="",
                editor_hsf_dir="",
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(name="chair", parameters=[], scripts={"3d": "BLOCK 1,1,1"})

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: proj,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                choose_file_fn=lambda _initial: str(gdl_file),
            )

            ok, msg = service.browse_and_open_project_file()

            self.assertTrue(ok)
            self.assertIn("已导入 GDL", msg)
            self.assertEqual(session_state.editor_open_path, str(gdl_file))
            self.assertEqual(session_state.editor_hsf_dir, str(gdl_file.parent))

    def test_open_project_source_path_imports_supported_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            gdl_file = Path(tmp) / "chair.gdl"
            gdl_file.write_text("BLOCK 1, 1, 1\n", encoding="utf-8")
            session_state = _SessionState(
                work_dir=tmp,
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(name="chair", parameters=[], scripts={"3d": "BLOCK 1,1,1"})
            captured = {}

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda content, name: captured.setdefault("input", (content, name)) and proj,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )

            ok, msg = service.open_project_source_path(str(gdl_file))

            self.assertTrue(ok)
            self.assertIn("已导入 GDL", msg)
            self.assertEqual(captured["input"], ("BLOCK 1, 1, 1\n", "chair"))
            self.assertIs(session_state.project, proj)
            self.assertEqual(session_state.pending_gsm_name, "chair")

    def test_open_project_source_path_rejects_non_hsf_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "not-hsf"
            folder.mkdir()
            session_state = _SessionState(
                work_dir=tmp,
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )

            ok, msg = service.open_project_source_path(str(folder))

            self.assertFalse(ok)
            self.assertIn("不是有效 HSF 项目目录", msg)

    def test_handle_hsf_directory_load_rejects_parent_with_multiple_hsf_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            for name in ("Chair", "Desk"):
                hsf_dir = work_dir / name
                hsf_dir.mkdir()
                (hsf_dir / "libpartdata.xml").write_text("<LibraryPart/>", encoding="utf-8")
            session_state = _SessionState(
                work_dir=str(work_dir / "default-workspace"),
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: None,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
            )

            ok, msg = service.handle_hsf_directory_load(str(work_dir))

            self.assertFalse(ok)
            self.assertIn("多个 HSF 项目", msg)
            self.assertIn("Chair", msg)
            self.assertIn("Desk", msg)

    def test_handle_unified_import_accepts_import_gsm_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                chat_history=[],
                pending_diffs={},
                preview_2d_data=None,
                preview_3d_data=None,
                preview_warnings=[],
                preview_meta={},
            )
            proj = SimpleNamespace(
                name="ImportedChair",
                parameters=[],
                scripts={},
                save_to_disk=lambda: None,
            )

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: proj,
                reset_tapir_p0_state_fn=lambda: None,
                bump_main_editor_version_fn=lambda: None,
                import_gsm_override_fn=lambda _bytes, _name: (proj, "ok"),
            )
            uploaded = SimpleNamespace(name="chair.gsm", read=lambda: b"fake")

            ok, _msg = service.handle_unified_import(uploaded)

            self.assertTrue(ok)
            self.assertIs(session_state.project, proj)
            self.assertEqual(session_state.chat_history, [])
            self.assertEqual(len(session_state.project_activity_log), 1)
            self.assertIn("ok", session_state.project_activity_log[0]["message"])


if __name__ == "__main__":
    unittest.main()
