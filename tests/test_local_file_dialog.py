import unittest

from ui.local_file_dialog import _choose_file_macos_script


class TestLocalFileDialog(unittest.TestCase):
    def test_choose_file_macos_script_opens_file_dialog_directly(self):
        script = _choose_file_macos_script(title='打开 "对象"', initial_dir="/tmp")

        self.assertIn("choose file with prompt", script)
        self.assertIn('default location POSIX file "/tmp"', script)
        self.assertIn('打开 \\"对象\\"', script)
        self.assertNotIn('buttons {"取消", "HSF 文件夹", "文件"}', script)
        self.assertNotIn("choose folder with prompt", script)
        self.assertNotIn("NSOpenPanel", script)
        self.assertNotIn("choose file or folder", script)


if __name__ == "__main__":
    unittest.main()
