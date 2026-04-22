import unittest
from unittest.mock import MagicMock, patch

from openbrep.compiler import HSFCompiler


class TestCompilerOutputDecoding(unittest.TestCase):
    def test_decode_process_output_handles_utf8_bytes(self):
        text = HSFCompiler._decode_process_output("编译成功".encode("utf-8"))
        self.assertEqual(text, "编译成功")

    def test_decode_process_output_falls_back_to_gbk(self):
        text = HSFCompiler._decode_process_output("编译失败".encode("gbk"))
        self.assertEqual(text, "编译失败")

    def test_run_converter_decodes_stderr_bytes(self):
        compiler = HSFCompiler(converter_path="/tmp/LP_XMLConverter")
        proc = MagicMock(returncode=1, stdout=b"", stderr="编译失败".encode("gbk"))
        with patch("openbrep.compiler.subprocess.run", return_value=proc):
            result = compiler._run_converter("libpart2hsf", "in.gsm", "out")
        self.assertFalse(result.success)
        self.assertIn("编译失败", result.stderr)


class TestCompilerAutoDetect(unittest.TestCase):
    def test_detect_converter_prefers_config_auto_detect(self):
        with patch("openbrep.compiler._auto_detect_converter", return_value="/detected/from-config"):
            detected = HSFCompiler._detect_converter()
        self.assertEqual(detected, "/detected/from-config")

    def test_detect_converter_returns_none_when_config_auto_detect_fails(self):
        with patch("openbrep.compiler._auto_detect_converter", return_value=None):
            detected = HSFCompiler._detect_converter()
        self.assertIsNone(detected)


class TestCompilerWindowsPathValidation(unittest.TestCase):
    def test_run_converter_rejects_directory_path_on_windows(self):
        compiler = HSFCompiler(converter_path=r"C:\Program Files\GRAPHISOFT\ArchiCAD 26")
        with patch("openbrep.compiler.platform.system", return_value="Windows"), \
             patch("openbrep.compiler.Path.is_dir", return_value=True), \
             patch("openbrep.compiler.Path.is_file", return_value=False):
            result = compiler._run_converter("libpart2hsf", "in.gsm", "out")
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not an executable file", result.stderr)

    def test_run_converter_rejects_non_exe_path_on_windows(self):
        compiler = HSFCompiler(converter_path=r"C:\Program Files\GRAPHISOFT\ArchiCAD 26\LP_XMLConverter")
        with patch("openbrep.compiler.platform.system", return_value="Windows"), \
             patch("openbrep.compiler.Path.is_dir", return_value=False), \
             patch("openbrep.compiler.Path.is_file", return_value=True):
            result = compiler._run_converter("libpart2hsf", "in.gsm", "out")
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("must end with .exe", result.stderr)


if __name__ == "__main__":
    unittest.main()
