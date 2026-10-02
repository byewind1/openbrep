import subprocess
import sys
import unittest
from unittest import mock

import openbrep.local_file_dialog as local_file_dialog
from openbrep.local_file_dialog import (
    DialogUnavailableError,
    _choose_directory_macos_script,
    _choose_directory_windows_script,
    _choose_file_macos_script,
    _choose_file_windows_script,
    _filetypes_for_extensions,
    _powershell_filter_for_extensions,
    _run_powershell_dialog,
    choose_directory,
    choose_file,
)


class TestLocalFileDialog(unittest.TestCase):
    def test_choose_file_macos_script_opens_file_dialog_directly(self):
        script = _choose_file_macos_script(title='打开 "对象"', initial_dir="/tmp")

        self.assertIn("activate", script)
        self.assertIn("choose file with prompt", script)
        self.assertIn('default location POSIX file "/tmp"', script)
        self.assertIn('打开 \\"对象\\"', script)
        self.assertNotIn('buttons {"取消", "HSF 文件夹", "文件"}', script)
        self.assertNotIn("choose folder with prompt", script)
        self.assertNotIn("NSOpenPanel", script)
        self.assertNotIn("choose file or folder", script)

    def test_choose_file_macos_script_filters_extensions(self):
        script = _choose_file_macos_script(title="Import GDL", extensions=[".gdl", "GDL"])

        self.assertIn('choose file with prompt "Import GDL"', script)
        self.assertIn('of type {"gdl"}', script)


    def test_filetypes_for_extensions_filters_supported_files(self):
        filetypes = _filetypes_for_extensions([".gsm", "GSM", "gdl"])

        self.assertEqual(filetypes, [("Supported files", "*.gsm *.gdl"), ("All files", "*.*")])


    def test_choose_directory_macos_script_activates_folder_dialog(self):
        script = _choose_directory_macos_script(title='选择 "项目"', initial_dir="/tmp/file.gdl")

        self.assertIn("activate", script)
        self.assertIn("choose folder with prompt", script)
        self.assertIn('选择 \\"项目\\"', script)
        self.assertNotIn("NSOpenPanel", script)
        self.assertNotIn("choose file or folder", script)


class TestWindowsPowerShellDialog(unittest.TestCase):
    def test_choose_directory_windows_script_uses_folder_browser_dialog(self):
        script = _choose_directory_windows_script(title="选择 '项目'")

        self.assertIn("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8", script)
        self.assertIn("Add-Type -AssemblyName System.Windows.Forms", script)
        self.assertIn("FolderBrowserDialog", script)
        self.assertIn("$dialog.Description = '选择 ''项目'''", script)
        self.assertIn("$owner.TopMost = $true", script)
        self.assertIn("[System.Windows.Forms.DialogResult]::OK", script)
        self.assertIn("$dialog.SelectedPath", script)

    def test_choose_directory_windows_script_sets_existing_initial_dir(self):
        with mock.patch.object(local_file_dialog.Path, "is_dir", return_value=True), mock.patch.object(
            local_file_dialog.Path, "exists", return_value=True
        ):
            script = _choose_directory_windows_script(title="t", initial_dir="C:\\work")

        self.assertIn("$dialog.SelectedPath = 'C:\\work'", script)

    def test_choose_file_windows_script_uses_open_file_dialog_with_filter(self):
        script = _choose_file_windows_script(title="Import 'GDL'", extensions=["gdl"])

        self.assertIn("OpenFileDialog", script)
        self.assertIn("$dialog.Title = 'Import ''GDL'''", script)
        self.assertIn("$dialog.CheckFileExists = $true", script)
        self.assertIn("$dialog.Filter = 'Supported files (*.gdl)|*.gdl|All files (*.*)|*.*'", script)
        self.assertIn("$dialog.FileName", script)

    def test_powershell_filter_for_extensions_uses_semicolon_patterns(self):
        self.assertEqual(
            _powershell_filter_for_extensions([".gsm", "gdl"]),
            "Supported files (*.gsm;*.gdl)|*.gsm;*.gdl|All files (*.*)|*.*",
        )

    def test_run_powershell_dialog_invokes_sta_with_timeout_and_decodes_utf8(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="D:\\项目\\书架\n".encode("utf-8"), stderr=b""
        )
        with mock.patch.object(subprocess, "run", return_value=completed) as run_mock:
            selected = _run_powershell_dialog("script body")

        self.assertEqual(selected, "D:\\项目\\书架")
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0][:4], ["powershell", "-NoProfile", "-STA", "-Command"])
        self.assertEqual(args[0][4], "script body")
        self.assertEqual(kwargs["timeout"], 120)

    def test_run_powershell_dialog_maps_empty_stdout_exit_zero_to_cancel(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(subprocess, "run", return_value=completed):
            self.assertIsNone(_run_powershell_dialog("script"))

    def test_run_powershell_dialog_maps_timeout_to_cancel(self):
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=120)
        ):
            self.assertIsNone(_run_powershell_dialog("script"))

    def test_run_powershell_dialog_raises_unavailable_when_powershell_missing(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("powershell")):
            with self.assertRaises(DialogUnavailableError):
                _run_powershell_dialog("script")

    def test_run_powershell_dialog_raises_unavailable_on_nonzero_exit(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr="boom".encode("utf-8")
        )
        with mock.patch.object(subprocess, "run", return_value=completed):
            with self.assertRaises(DialogUnavailableError) as ctx:
                _run_powershell_dialog("script")
        self.assertIn("boom", str(ctx.exception))


class TestDialogDispatch(unittest.TestCase):
    def test_choose_directory_uses_macos_path_on_darwin(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Darwin"), mock.patch.object(
            local_file_dialog, "_choose_directory_macos", return_value="/picked"
        ) as macos_mock, mock.patch.object(local_file_dialog, "_choose_directory_tk") as tk_mock:
            self.assertEqual(choose_directory(), "/picked")
        macos_mock.assert_called_once()
        tk_mock.assert_not_called()

    def test_choose_directory_prefers_powershell_on_windows(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Windows"), mock.patch.object(
            local_file_dialog, "_choose_directory_windows", return_value="D:\\picked"
        ) as ps_mock, mock.patch.object(local_file_dialog, "_choose_directory_tk") as tk_mock:
            self.assertEqual(choose_directory(), "D:\\picked")
        ps_mock.assert_called_once()
        tk_mock.assert_not_called()

    def test_choose_directory_falls_back_to_tkinter_when_powershell_unavailable(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Windows"), mock.patch.object(
            local_file_dialog,
            "_choose_directory_windows",
            side_effect=DialogUnavailableError("no powershell"),
        ), mock.patch.object(local_file_dialog, "_choose_directory_tk", return_value="D:\\tk") as tk_mock:
            self.assertEqual(choose_directory(), "D:\\tk")
        tk_mock.assert_called_once()

    def test_choose_file_falls_back_to_tkinter_when_powershell_unavailable(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Windows"), mock.patch.object(
            local_file_dialog,
            "_choose_file_windows",
            side_effect=DialogUnavailableError("no powershell"),
        ), mock.patch.object(local_file_dialog, "_choose_file_tk", return_value="D:\\a.gdl") as tk_mock:
            self.assertEqual(choose_file(), "D:\\a.gdl")
        tk_mock.assert_called_once()

    def test_choose_directory_raises_unavailable_when_no_backend_works(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Windows"), mock.patch.object(
            local_file_dialog,
            "_choose_directory_windows",
            side_effect=DialogUnavailableError("no powershell"),
        ), mock.patch.object(
            local_file_dialog,
            "_choose_directory_tk",
            side_effect=DialogUnavailableError("no tkinter"),
        ):
            with self.assertRaises(DialogUnavailableError):
                choose_directory()

    def test_choose_directory_uses_tkinter_on_other_platforms(self):
        with mock.patch.object(local_file_dialog.platform, "system", return_value="Linux"), mock.patch.object(
            local_file_dialog, "_choose_directory_tk", return_value="/tk"
        ) as tk_mock, mock.patch.object(local_file_dialog, "_choose_directory_windows") as ps_mock:
            self.assertEqual(choose_directory(), "/tk")
        tk_mock.assert_called_once()
        ps_mock.assert_not_called()


class TestTkinterUnavailable(unittest.TestCase):
    def test_choose_directory_tk_raises_unavailable_when_tkinter_missing(self):
        with mock.patch.dict(sys.modules, {"tkinter": None, "tkinter.filedialog": None}):
            with self.assertRaises(DialogUnavailableError):
                local_file_dialog._choose_directory_tk(title="t")

    def test_choose_file_tk_raises_unavailable_when_tkinter_missing(self):
        with mock.patch.dict(sys.modules, {"tkinter": None, "tkinter.filedialog": None}):
            with self.assertRaises(DialogUnavailableError):
                local_file_dialog._choose_file_tk(title="t")


if __name__ == "__main__":
    unittest.main()
