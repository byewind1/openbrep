import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
