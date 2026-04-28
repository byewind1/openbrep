import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from openbrep.hsf_project import HSFProject
from ui import project_io


class _SessionState(dict):
    def __getattr__(self, item):
        return self[item]


class TestProjectIOCompile(unittest.TestCase):
    def test_do_compile_uses_compile_result_formatter(self):
        session_state = _SessionState(
            work_dir="/tmp/work",
            compile_log=[],
            script_revision=2,
        )
        compiler_result = SimpleNamespace(success=False, stderr="bad", stdout="", exit_code=1)
        captured = {}

        def _format(**kwargs):
            captured.update(kwargs)
            return False, "ERR"

        ok, msg = project_io.do_compile(
            SimpleNamespace(name="Chair", save_to_disk=lambda: "/tmp/work/Chair"),
            "Chair",
            "ins",
            session_state=session_state,
            safe_compile_revision_fn=lambda name, work, req: req,
            versioned_gsm_path_fn=lambda name, work, revision: "/tmp/work/Chair_v2.gsm",
            get_compiler_fn=lambda: SimpleNamespace(hsf2libpart=lambda hsf, out: compiler_result),
            compiler_mode="LP",
            format_compile_result_fn=_format,
        )

        self.assertFalse(ok)
        self.assertEqual(msg, "ERR")
        self.assertEqual(captured["result"], compiler_result)
        self.assertEqual(captured["output_gsm"], "/tmp/work/Chair_v2.gsm")
        self.assertEqual(captured["hsf_dir"], "/tmp/work/Chair")

    def test_do_compile_promotes_untitled_project_to_gsm_name_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_state = _SessionState(
                work_dir=tmp,
                compile_log=[],
                script_revision=1,
            )
            proj = HSFProject.create_new("untitled", work_dir=tmp)
            compiler_result = SimpleNamespace(success=True, stderr="", stdout="", exit_code=0)
            captured = {}

            def _hsf2libpart(hsf_dir, output_gsm):
                captured["hsf_dir"] = hsf_dir
                captured["output_gsm"] = output_gsm
                return compiler_result

            ok, msg = project_io.do_compile(
                proj,
                "Chair",
                "manual compile",
                session_state=session_state,
                safe_compile_revision_fn=lambda name, work, req: req,
                versioned_gsm_path_fn=lambda name, work, revision: str(Path(work) / f"{name}_v{revision}.gsm"),
                get_compiler_fn=lambda: SimpleNamespace(hsf2libpart=_hsf2libpart),
                compiler_mode="LP",
            )

            self.assertTrue(ok)
            self.assertEqual(proj.name, "Chair")
            self.assertEqual(proj.root, Path(tmp) / "Chair")
            self.assertEqual(captured["hsf_dir"], str(Path(tmp) / "Chair"))
            self.assertTrue((Path(tmp) / "Chair" / "libpartdata.xml").exists())
            self.assertIn("HSF 源目录", msg)


if __name__ == "__main__":
    unittest.main()
