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

    def test_handle_hsf_directory_load_finalizes_project_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            hsf_dir = Path(tmp) / "Chair"
            hsf_dir.mkdir()
            session_state = _SessionState(
                work_dir=tmp,
                chat_history=[],
                pending_diffs={"x": "y"},
                preview_2d_data={"old": True},
                preview_3d_data={"old": True},
                preview_warnings=["old"],
                preview_meta={"kind": "old"},
            )
            calls = {"reset": 0, "bump": 0}
            proj = SimpleNamespace(name="Chair", parameters=[1, 2], scripts={"3d": "BLOCK 1,1,1"})

            service = ProjectService(
                session_state=session_state,
                compiler_mode="LP",
                get_compiler_fn=lambda: None,
                mock_compiler_class=object,
                parse_gdl_source_fn=lambda *_args: None,
                load_project_from_disk_fn=lambda _path: proj,
                reset_tapir_p0_state_fn=lambda: calls.__setitem__("reset", calls["reset"] + 1),
                bump_main_editor_version_fn=lambda: calls.__setitem__("bump", calls["bump"] + 1),
            )

            ok, msg = service.handle_hsf_directory_load(str(hsf_dir))

            self.assertTrue(ok)
            self.assertIn("已加载 HSF 项目", msg)
            self.assertIs(session_state.project, proj)
            self.assertEqual(session_state.pending_gsm_name, "Chair")
            self.assertEqual(session_state.pending_diffs, {})
            self.assertIsNone(session_state.preview_2d_data)
            self.assertIsNone(session_state.preview_3d_data)
            self.assertEqual(calls, {"reset": 1, "bump": 1})

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


if __name__ == "__main__":
    unittest.main()
