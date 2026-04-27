import unittest
from unittest.mock import patch

from ui import app_shell


class _StreamlitStub:
    def __init__(self):
        self.page_config = None
        self.markdown_calls = []

    def set_page_config(self, **kwargs):
        self.page_config = kwargs

    def markdown(self, body, unsafe_allow_html=False):
        self.markdown_calls.append((body, unsafe_allow_html))


class TestAppShell(unittest.TestCase):
    def test_configure_page_sets_config_and_css(self):
        st = _StreamlitStub()

        app_shell.configure_page(st)

        self.assertEqual(st.page_config["page_title"], "openbrep")
        self.assertEqual(st.page_config["layout"], "wide")
        self.assertEqual(len(st.markdown_calls), 1)
        self.assertIn("<style>", st.markdown_calls[0][0])
        self.assertTrue(st.markdown_calls[0][1])

    def test_is_archicad_running_returns_false_on_error(self):
        with patch("ui.app_shell.subprocess.run", side_effect=OSError("no pgrep")):
            self.assertFalse(app_shell.is_archicad_running())

    def test_is_archicad_running_uses_pgrep_return_code(self):
        with patch("ui.app_shell.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(app_shell.is_archicad_running())

            run.return_value.returncode = 1
            self.assertFalse(app_shell.is_archicad_running())

    def test_missing_tapir_bridge_returns_disabled_capability(self):
        with patch("builtins.__import__", side_effect=ImportError("missing")):
            _get_bridge, errors_to_chat_message, ok = app_shell.load_tapir_bridge()

        self.assertFalse(ok)
        self.assertEqual(errors_to_chat_message(["err"]), "['err']")
        with self.assertRaises(ImportError):
            _get_bridge()


if __name__ == "__main__":
    unittest.main()
