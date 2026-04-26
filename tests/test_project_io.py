import unittest
from pathlib import Path
from types import SimpleNamespace

from ui import project_io


class TestProjectIOUnifiedImport(unittest.TestCase):
    def test_handle_unified_import_gsm_import_failed_returns_consistent_message(self):
        uploaded = SimpleNamespace(name="chair.gsm", read=lambda: b"fake")

        ok, msg = project_io.handle_unified_import(
            uploaded,
            import_gsm_fn=lambda _bytes, _name: (None, "boom"),
            parse_gdl_source_fn=lambda _content, _stem: None,
            derive_gsm_name_from_filename_fn=lambda _name: "chair",
            finalize_loaded_project_fn=lambda proj, msg, pending: (True, "ok"),
        )

        self.assertFalse(ok)
        self.assertTrue(msg.startswith("❌ [IMPORT_GSM]"))
        self.assertIn("boom", msg)

    def test_handle_unified_import_text_parse_failed_is_wrapped(self):
        uploaded = SimpleNamespace(name="chair.gdl", read=lambda: b"bad")

        ok, msg = project_io.handle_unified_import(
            uploaded,
            import_gsm_fn=lambda _bytes, _name: (None, "ignored"),
            parse_gdl_source_fn=lambda _content, _stem: (_ for _ in ()).throw(ValueError("parse err")),
            derive_gsm_name_from_filename_fn=lambda _name: "chair",
            finalize_loaded_project_fn=lambda proj, msg, pending: (True, "ok"),
        )

        self.assertFalse(ok)
        self.assertIn("导入失败", msg)
        self.assertIn("parse err", msg)

    def test_handle_unified_import_uses_filename_fallback_for_pending_name(self):
        uploaded = SimpleNamespace(name="chair.gdl", read=lambda: "BLOCK 1,1,1".encode("utf-8"))
        proj = SimpleNamespace(name="ParsedName", parameters=[], scripts={})
        captured = {}

        def _finalize(_proj, _msg, pending):
            captured["pending"] = pending
            return True, "done"

        ok, _msg = project_io.handle_unified_import(
            uploaded,
            import_gsm_fn=lambda _bytes, _name: (None, "ignored"),
            parse_gdl_source_fn=lambda _content, _stem: proj,
            derive_gsm_name_from_filename_fn=lambda _name: "",
            finalize_loaded_project_fn=_finalize,
        )

        self.assertTrue(ok)
        self.assertEqual(captured["pending"], "ParsedName")


if __name__ == "__main__":
    unittest.main()
