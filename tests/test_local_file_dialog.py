import unittest

from ui.local_file_dialog import _choose_path_macos_script


class TestLocalFileDialog(unittest.TestCase):
    def test_choose_path_macos_script_uses_nsopenpanel_for_files_and_directories(self):
        script = _choose_path_macos_script(title='打开 "对象"', initial_dir="/tmp")

        self.assertIn('use framework "AppKit"', script)
        self.assertIn("NSOpenPanel", script)
        self.assertIn("setCanChooseFiles:true", script)
        self.assertIn("setCanChooseDirectories:true", script)
        self.assertIn('setMessage:"打开 \\"对象\\""', script)
        self.assertNotIn("choose file or folder", script)


if __name__ == "__main__":
    unittest.main()
