import unittest

from openbrep.hsf_project import HSFProject
from ui.views.parameter_panel import render_parameter_panel


class _Col:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def metric(self, *_args, **_kwargs):
        return None


class _FakeStreamlit:
    def __init__(self):
        self.tabs_called = []
        self.markdowns = []
        self.infos = []

    def markdown(self, value, **_kwargs):
        self.markdowns.append(str(value))

    def tabs(self, labels):
        self.tabs_called.append(list(labels))
        return tuple(_Col() for _ in labels)

    def columns(self, spec):
        count = len(spec) if hasattr(spec, "__len__") else int(spec)
        return tuple(_Col() for _ in range(count))

    def expander(self, *_args, **_kwargs):
        return _Col()

    def dataframe(self, *_args, **_kwargs):
        return None

    def caption(self, *_args, **_kwargs):
        return None

    def selectbox(self, _label, options, **_kwargs):
        return options[0]

    def text_input(self, _label, value="", **_kwargs):
        return value

    def button(self, *_args, **_kwargs):
        return False

    def code(self, *_args, **_kwargs):
        return None

    def info(self, value):
        self.infos.append(str(value))


class TestParameterPanel(unittest.TestCase):
    def test_default_parameter_panel_hides_archicad_writeback_tab(self):
        st = _FakeStreamlit()

        render_parameter_panel(st, HSFProject.create_new("Chair"))

        self.assertEqual(st.tabs_called, [])
        self.assertNotIn("Archicad 写回", "\n".join(st.markdowns))
        self.assertEqual(st.infos, [])


if __name__ == "__main__":
    unittest.main()
