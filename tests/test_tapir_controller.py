import unittest

from ui.tapir_controller import reload_libraries_after_compile


class _Bridge:
    def __init__(self, *, available=True, reload_ok=True):
        self.available = available
        self.reload_ok = reload_ok
        self.reload_calls = 0

    def is_available(self):
        return self.available

    def reload_libraries(self):
        self.reload_calls += 1
        return self.reload_ok


class TestTapirControllerCompileReload(unittest.TestCase):
    def test_reload_after_compile_skips_when_tapir_not_imported(self):
        bridge = _Bridge()

        result = reload_libraries_after_compile(
            tapir_import_ok=False,
            get_bridge_fn=lambda: bridge,
        )

        self.assertIsNone(result)
        self.assertEqual(bridge.reload_calls, 0)

    def test_reload_after_compile_calls_bridge_when_available(self):
        bridge = _Bridge()

        ok, msg = reload_libraries_after_compile(
            tapir_import_ok=True,
            get_bridge_fn=lambda: bridge,
        )

        self.assertTrue(ok)
        self.assertEqual(bridge.reload_calls, 1)
        self.assertIn("重载图库", msg)

    def test_reload_after_compile_reports_unavailable_archicad(self):
        bridge = _Bridge(available=False)

        ok, msg = reload_libraries_after_compile(
            tapir_import_ok=True,
            get_bridge_fn=lambda: bridge,
        )

        self.assertFalse(ok)
        self.assertEqual(bridge.reload_calls, 0)
        self.assertIn("未自动重载图库", msg)

    def test_reload_after_compile_reports_reload_failure(self):
        bridge = _Bridge(reload_ok=False)

        ok, msg = reload_libraries_after_compile(
            tapir_import_ok=True,
            get_bridge_fn=lambda: bridge,
        )

        self.assertFalse(ok)
        self.assertEqual(bridge.reload_calls, 1)
        self.assertIn("手动重载图库", msg)


if __name__ == "__main__":
    unittest.main()
