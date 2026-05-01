import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from openbrep.compiler import CompileResult
from openbrep.hsf_project import HSFProject, ScriptType
from ui.pending_compile_controller import run_pending_compile_preflight


class TestPendingCompileController(unittest.TestCase):
    def setUp(self):
        self.script_map = [
            (ScriptType.SCRIPT_3D, "scripts/3d.gdl", "3D"),
            (ScriptType.SCRIPT_2D, "scripts/2d.gdl", "2D"),
        ]

    def test_preflight_compiles_temporary_project_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = HSFProject.create_new("Demo", work_dir=tmp)
            proj.save_to_disk()
            original_root = proj.root
            original_work_dir = proj.work_dir
            captured = {}
            state = {}

            def hsf2libpart(hsf_dir, output_gsm):
                captured["hsf_dir"] = Path(hsf_dir)
                captured["output_gsm"] = Path(output_gsm)
                self.assertTrue((Path(hsf_dir) / "libpartdata.xml").exists())
                self.assertIn("openbrep-pending-compile-", str(hsf_dir))
                return CompileResult(success=True, stdout="ok", output_path=output_gsm)

            ok, msg = run_pending_compile_preflight(
                proj,
                {"scripts/3d.gdl": "BLOCK 2, 3, 4"},
                gsm_name="Demo",
                script_map=self.script_map,
                parse_paramlist_text_fn=lambda _text: [],
                get_compiler_fn=lambda: type("Compiler", (), {"hsf2libpart": staticmethod(hsf2libpart)})(),
                compiler_mode="Mock",
                set_pending_compile_result_fn=lambda result: state.__setitem__("result", result),
                set_pending_compile_meta_fn=lambda meta: state.__setitem__("meta", meta),
                deepcopy_fn=deepcopy,
            )

            self.assertTrue(ok)
            self.assertIn("通过", msg)
            self.assertEqual(proj.root, original_root)
            self.assertEqual(proj.work_dir, original_work_dir)
            self.assertNotEqual(captured["hsf_dir"], original_root)
            self.assertEqual(state["result"][0], True)
            self.assertEqual(state["meta"]["compiler_mode"], "Mock")
            self.assertEqual(state["meta"]["changed_paths"], ["scripts/3d.gdl"])

    def test_preflight_records_failure_status(self):
        proj = HSFProject.create_new("Demo")
        state = {}

        def hsf2libpart(_hsf_dir, _output_gsm):
            return CompileResult(success=False, stderr="compile failed", exit_code=2)

        ok, msg = run_pending_compile_preflight(
            proj,
            {"scripts/3d.gdl": "IF 1 THEN\nBLOCK 1,1,1"},
            gsm_name="Demo",
            script_map=self.script_map,
            parse_paramlist_text_fn=lambda _text: [],
            get_compiler_fn=lambda: type("Compiler", (), {"hsf2libpart": staticmethod(hsf2libpart)})(),
            compiler_mode="LP",
            set_pending_compile_result_fn=lambda result: state.__setitem__("result", result),
            set_pending_compile_meta_fn=lambda meta: state.__setitem__("meta", meta),
            deepcopy_fn=deepcopy,
        )

        self.assertFalse(ok)
        self.assertIn("失败", msg)
        self.assertEqual(state["result"][0], False)
        self.assertEqual(state["meta"]["exit_code"], 2)
        self.assertEqual(state["meta"]["stderr"], "compile failed")


if __name__ == "__main__":
    unittest.main()
