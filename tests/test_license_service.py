import unittest
from pathlib import Path
from unittest.mock import patch

from ui.license_service import LicenseService


class TestLicenseService(unittest.TestCase):
    def test_save_skips_disk_write_outside_runtime_context(self):
        service = LicenseService(
            root=Path("/repo"),
            has_runtime_context_fn=lambda: False,
        )

        with patch("ui.license_service.knowledge_access._save_license") as save:
            service.save("/tmp/work", {"pro_unlocked": True})

        save.assert_not_called()

    def test_import_pro_knowledge_zip_passes_project_root(self):
        root = Path("/repo")
        service = LicenseService(root=root)

        with patch("ui.license_service.knowledge_access._import_pro_knowledge_zip", return_value=(True, "ok")) as importer:
            ok, msg = service.import_pro_knowledge_zip(b"pkg", "demo.obrk", "/tmp/work")

        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
        importer.assert_called_once_with(
            b"pkg",
            "demo.obrk",
            "/tmp/work",
            root=root,
        )


if __name__ == "__main__":
    unittest.main()
