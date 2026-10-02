import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openbrep.compiler import HSFCompiler, MockHSFCompiler


class TestMockCompilerArtifactSafety(unittest.TestCase):
    def test_successful_validation_does_not_write_fake_gsm(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "libpartdata.xml").write_text("<LibpartData/>", encoding="utf-8")
            (root / "paramlist.xml").write_text("<ParamSection/>", encoding="utf-8")
            (root / "scripts" / "3d.gdl").write_text("BLOCK 1, 1, 1\n", encoding="utf-8")
            output = root / "looks-real.gsm"

            result = MockHSFCompiler().hsf2libpart(str(root), str(output))

            self.assertTrue(result.success)
            self.assertEqual(result.mode, "mock")
            self.assertEqual(result.output_path, "")
            self.assertFalse(output.exists())
            self.assertIn("no GSM artifact", result.stdout)

    def test_validation_removes_only_legacy_mock_placeholder(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "libpartdata.xml").write_text("<LibpartData/>", encoding="utf-8")
            (root / "paramlist.xml").write_text("<ParamSection/>", encoding="utf-8")
            output = root / "legacy.gsm"
            output.write_text("[MOCK GSM] Compiled from /old/project", encoding="utf-8")

            result = MockHSFCompiler().hsf2libpart(str(root), str(output))

            self.assertTrue(result.success)
            self.assertFalse(output.exists())

    def test_validation_preserves_existing_real_artifact(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "libpartdata.xml").write_text("<LibpartData/>", encoding="utf-8")
            (root / "paramlist.xml").write_text("<ParamSection/>", encoding="utf-8")
            output = root / "existing.gsm"
            output.write_bytes(b"WW.\x00real-binary")

            result = MockHSFCompiler().hsf2libpart(str(root), str(output))

            self.assertTrue(result.success)
            self.assertEqual(output.read_bytes(), b"WW.\x00real-binary")


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

    def test_hsf_compile_requires_the_declared_gsm_to_exist(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "missing.gsm"
            compiler = HSFCompiler(converter_path="/tmp/LP_XMLConverter")
            proc = MagicMock(returncode=0, stdout=b"Success", stderr=b"")
            with patch("openbrep.compiler.subprocess.run", return_value=proc):
                result = compiler._run_converter("hsf2libpart", "in", str(output))

            self.assertFalse(result.success)
            self.assertEqual(result.output_path, "")
            self.assertIn("did not produce", result.stderr)


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
