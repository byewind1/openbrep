import json
import tempfile
import unittest
from pathlib import Path

from openbrep.revisions import (
    copy_project_metadata,
    create_revision,
    get_latest_revision_id,
    list_revisions,
    restore_revision,
)


class TestProjectRevisions(unittest.TestCase):
    def _make_project(self, tmpdir: str) -> Path:
        project = Path(tmpdir) / "Chair"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        (project / "libpartdata.xml").write_text("<LibpartData />\n", encoding="utf-8")
        (project / "paramlist.xml").write_text("<ParamList />\n", encoding="utf-8")
        (scripts / "3d.gdl").write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
        return project

    def test_create_revision_copies_hsf_source_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            revision = create_revision(project, "initial")

            self.assertEqual(revision.revision_id, "r0001")
            self.assertEqual(revision.project_name, "Chair")
            self.assertEqual(revision.message, "initial")
            self.assertEqual(get_latest_revision_id(project), "r0001")
            self.assertTrue((project / ".openbrep" / "revisions" / "r0001" / "paramlist.xml").exists())
            self.assertTrue((project / ".openbrep" / "revisions" / "r0001" / "scripts" / "3d.gdl").exists())

            manifest = json.loads(
                (project / ".openbrep" / "revisions" / "r0001" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["source_format"], "hsf-project")
            self.assertIn("paramlist.xml", manifest["files"])
            self.assertIn("scripts/3d.gdl", manifest["files"])

    def test_list_revisions_returns_manifest_data_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial")
            (project / "scripts" / "3d.gdl").write_text("CYLIND 1, 1\n", encoding="utf-8")
            create_revision(project, "make cylinder")

            revisions = list_revisions(project)

            self.assertEqual([r.revision_id for r in revisions], ["r0001", "r0002"])
            self.assertEqual([r.message for r in revisions], ["initial", "make cylinder"])
            self.assertEqual(get_latest_revision_id(project), "r0002")

    def test_restore_revision_updates_working_source_and_creates_new_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial")
            (project / "scripts" / "3d.gdl").write_text("CYLIND 1, 1\n", encoding="utf-8")
            create_revision(project, "make cylinder")

            restored = restore_revision(project, "r0001")

            self.assertEqual(restored.revision_id, "r0003")
            self.assertEqual(get_latest_revision_id(project), "r0003")
            self.assertEqual(
                (project / "scripts" / "3d.gdl").read_text(encoding="utf-8"),
                "BLOCK A, B, ZZYZX\n",
            )
            manifest = json.loads((restored.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["metadata"], {"restored_from": "r0001"})

    def test_restore_revision_removes_source_files_not_in_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial")
            (project / "scripts" / "2d.gdl").write_text("LINE2 0, 0, A, B\n", encoding="utf-8")
            create_revision(project, "add 2d")

            restore_revision(project, "r0001")

            self.assertFalse((project / "scripts" / "2d.gdl").exists())

    def test_create_revision_rejects_non_hsf_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_dir = Path(tmpdir) / "plain"
            plain_dir.mkdir()

            with self.assertRaises(ValueError):
                create_revision(plain_dir, "no source")

    def test_copy_project_metadata_imports_existing_revision_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = self._make_project(tmpdir)
            create_revision(source, "initial")

            target = Path(tmpdir) / "ImportedChair"
            target.mkdir()
            (target / "libpartdata.xml").write_text("<LibpartData />\n", encoding="utf-8")

            copied = copy_project_metadata(source, target)

            self.assertTrue(copied)
            self.assertEqual(get_latest_revision_id(target), "r0001")
            self.assertEqual([r.message for r in list_revisions(target)], ["initial"])


if __name__ == "__main__":
    unittest.main()
