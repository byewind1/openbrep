import unittest

from ui.local_file_dialog import _choose_path_macos_script


class TestLocalFileDialog(unittest.TestCase):
    def test_choose_path_macos_script_routes_to_file_or_folder_dialogs(self):
        script = _choose_path_macos_script(title='打开 "对象"', initial_dir="/tmp")

        self.assertIn('buttons {"取消", "HSF 文件夹", "文件"}', script)
        self.assertIn("choose file with prompt", script)
        self.assertIn("choose folder with prompt", script)
        self.assertIn('default location POSIX file "/tmp"', script)
        self.assertIn('打开 \\"对象\\"', script)
        self.assertNotIn("NSOpenPanel", script)
        self.assertNotIn("choose file or folder", script)


if __name__ == "__main__":
    unittest.main()
