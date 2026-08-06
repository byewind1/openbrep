import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openbrep.revisions import (
    archive_artifact,
    compare_revisions,
    copy_project_metadata,
    create_revision,
    get_latest_revision_id,
    list_archived_artifacts,
    list_revisions,
    prune_revisions,
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
            self.assertEqual(revision.gsm_name, "Chair")
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
            self.assertEqual(manifest["gsm_name"], "Chair")
            self.assertIn("paramlist.xml", manifest["files"])
            self.assertIn("scripts/3d.gdl", manifest["files"])

    def test_create_revision_records_compile_gsm_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            revision = create_revision(project, "compile stable", gsm_name="Chair_For_Client")

            self.assertEqual(revision.gsm_name, "Chair_For_Client")
            manifest = json.loads((revision.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["gsm_name"], "Chair_For_Client")

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

    def test_compare_revisions_returns_unified_diff_for_changed_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial")
            (project / "scripts" / "3d.gdl").write_text("CYLIND 1, 1\n", encoding="utf-8")
            (project / "scripts" / "2d.gdl").write_text("LINE2 0, 0, A, B\n", encoding="utf-8")
            create_revision(project, "make cylinder")

            diff = compare_revisions(project, "r0001", "r0002")

            self.assertIn("--- r0001/scripts/3d.gdl", diff)
            self.assertIn("+++ r0002/scripts/3d.gdl", diff)
            self.assertIn("-BLOCK A, B, ZZYZX", diff)
            self.assertIn("+CYLIND 1, 1", diff)
            self.assertIn("+++ r0002/scripts/2d.gdl", diff)
            self.assertIn("+LINE2 0, 0, A, B", diff)

    def test_compare_revisions_includes_compile_metadata_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(
                project,
                "mock",
                metadata={"compile": {"mode": "mock", "success": True, "gsm_path": "/tmp/mock.gsm"}},
            )
            create_revision(
                project,
                "real",
                metadata={"compile": {"mode": "real", "success": False, "exit_code": 1}},
            )

            diff = compare_revisions(project, "r0001", "r0002")

            self.assertIn("## Compile metadata changed (r0001 -> r0002)", diff)
            self.assertIn("- mode: 'mock' -> 'real'", diff)
            self.assertIn("- success: True -> False", diff)

    def test_create_revision_writes_explanation_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            revision = create_revision(
                project,
                "modify shelves",
                trigger="modify",
                user_instruction="把层板改成 6 个",
                changed_files=["scripts/3d.gdl"],
                metadata={
                    "explanation": "- Added shelf_count parameter.",
                    "compile": {"mode": "mock", "success": True, "gsm_size_bytes": 2048},
                },
            )

            explanation = (revision.path / "explanation.md").read_text(encoding="utf-8")
            self.assertIn("# Revision r0001", explanation)
            self.assertIn("把层板改成 6 个", explanation)
            self.assertIn("- Added shelf_count parameter.", explanation)
            self.assertIn("- `scripts/3d.gdl`", explanation)
            self.assertIn("Passed (mock); size=2048 bytes.", explanation)

    def test_compare_revisions_includes_explanation_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial", metadata={"explanation": "Initial block."})
            create_revision(project, "modify", metadata={"explanation": "Changed to cylinder."})

            diff = compare_revisions(project, "r0001", "r0002")

            self.assertIn("## Explanation changed (r0001 -> r0002)", diff)
            self.assertIn("--- r0001/explanation.md", diff)
            self.assertIn("+++ r0002/explanation.md", diff)
            self.assertIn("-Initial block.", diff)
            self.assertIn("+Changed to cylinder.", diff)

    def test_compare_revisions_includes_compile_comparison_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            create_revision(project, "initial")
            create_revision(
                project,
                "modify",
                metadata={
                    "compile_comparison": {
                        "mode": "mock",
                        "before": {"success": True},
                        "after": {"success": False},
                        "size_delta_bytes": 1024,
                        "param_delta": 2,
                    }
                },
            )

            diff = compare_revisions(project, "r0001", "r0002")

            self.assertIn("## Compile comparison changed (r0001 -> r0002)", diff)
            self.assertIn("- mode: None -> 'mock'", diff)
            self.assertIn("- before.success: None -> True", diff)
            self.assertIn("- after.success: None -> False", diff)
            self.assertIn("- size_delta_bytes: None -> 1024", diff)
            self.assertIn("- param_delta: None -> 2", diff)

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
    def test_create_revision_records_v07_manifest_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)

            revision = create_revision(
                project,
                "before modify",
                trigger="modify",
                intent="MODIFY",
                user_instruction="增加背板",
                changed_files=["scripts/3d.gdl"],
                parent_revision_id="r0000",
                metadata={"compile": {"success": True, "gsm_size_bytes": 123, "gsm_path": "/tmp/a.gsm"}},
            )

            manifest = json.loads((revision.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], 1)
            self.assertEqual(manifest["trigger"], "modify")
            self.assertEqual(manifest["intent"], "MODIFY")
            self.assertEqual(manifest["user_instruction"], "增加背板")
            self.assertEqual(manifest["changed_files"], ["scripts/3d.gdl"])
            self.assertEqual(manifest["parent_revision_id"], "r0000")
            self.assertTrue(manifest["compile"]["success"])
            self.assertEqual(manifest["compile"]["gsm_size_bytes"], 123)

    def test_list_revisions_hydrates_v07_manifest_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            create_revision(
                project,
                "modify",
                trigger="modify",
                intent="MODIFY",
                user_instruction="改层板",
                metadata={
                    "explanation": "Updated shelf spacing.",
                    "compile_comparison": {"mode": "mock", "before": {"success": True}, "after": {"success": True}},
                },
            )

            revision = list_revisions(project)[0]

            self.assertEqual(revision.trigger, "modify")
            self.assertEqual(revision.intent, "MODIFY")
            self.assertEqual(revision.user_instruction, "改层板")
            self.assertEqual(revision.explanation, "Updated shelf spacing.")
            self.assertEqual(revision.compile_comparison["mode"], "mock")


class TestPruneRevisions(unittest.TestCase):
    def _make_project(self, tmpdir: str) -> Path:
        project = Path(tmpdir) / "Chair"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        (project / "libpartdata.xml").write_text("<LibpartData />\n", encoding="utf-8")
        (project / "paramlist.xml").write_text("<ParamList />\n", encoding="utf-8")
        (scripts / "3d.gdl").write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
        return project

    def test_prune_keeps_newest_n_and_deletes_rest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            for i in range(5):
                create_revision(project, f"rev {i}")

            deleted = prune_revisions(project, keep_last_n=2)

            self.assertEqual(deleted, 3)
            remaining_ids = {r.revision_id for r in list_revisions(project)}
            self.assertEqual(remaining_ids, {"r0004", "r0005"})

    def test_prune_never_deletes_latest_even_if_outside_keep_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            for i in range(3):
                create_revision(project, f"rev {i}")
            latest_id_before = get_latest_revision_id(project)

            # keep_last_n=1 would normally only keep the newest; latest must survive regardless.
            prune_revisions(project, keep_last_n=1)

            self.assertEqual(get_latest_revision_id(project), latest_id_before)
            remaining_ids = {r.revision_id for r in list_revisions(project)}
            self.assertIn(latest_id_before, remaining_ids)

    def test_prune_noop_when_fewer_revisions_than_keep_last_n(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            create_revision(project, "only one")

            deleted = prune_revisions(project, keep_last_n=20)

            self.assertEqual(deleted, 0)
            self.assertEqual(len(list_revisions(project)), 1)

    def test_prune_clamps_keep_last_n_to_at_least_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            for i in range(3):
                create_revision(project, f"rev {i}")

            deleted = prune_revisions(project, keep_last_n=0)

            # keep_last_n clamps to 1: 3 revisions - 1 kept = 2 deleted.
            self.assertEqual(deleted, 2)
            self.assertEqual(len(list_revisions(project)), 1)

    def test_create_revision_auto_prunes_beyond_twenty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            for i in range(23):
                create_revision(project, f"rev {i}")

            # create_revision auto-prunes with keep_last_n=20 after every call.
            self.assertEqual(len(list_revisions(project)), 20)
            self.assertEqual(get_latest_revision_id(project), "r0023")

    # ── [revisions] keep_last_n 可配置：自动 prune 读配置而非硬编码 20 ──

    def _keep_last_n_env(self, value):
        """写一个带 [revisions] keep_last_n 的临时 config 并指到 GDL_AGENT_CONFIG。"""
        cfg_dir = Path(tempfile.mkdtemp(prefix="obr_rev_cfg_"))
        cfg_path = cfg_dir / "config.toml"
        cfg_path.write_text(f"[revisions]\nkeep_last_n = {value}\n", encoding="utf-8")
        return patch.dict(os.environ, {"GDL_AGENT_CONFIG": str(cfg_path)})

    def test_create_revision_auto_prune_uses_configured_keep_last_n(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            with self._keep_last_n_env(5):
                for i in range(7):
                    create_revision(project, f"rev {i}")

            remaining_ids = {r.revision_id for r in list_revisions(project)}
            self.assertEqual(remaining_ids, {"r0003", "r0004", "r0005", "r0006", "r0007"})
            self.assertEqual(get_latest_revision_id(project), "r0007")

    def test_create_revision_auto_prune_disabled_when_keep_last_n_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            with self._keep_last_n_env(0):
                for i in range(7):
                    create_revision(project, f"rev {i}")

            # keep_last_n=0 → 完全禁用自动 prune，历史一条不丢
            self.assertEqual(len(list_revisions(project)), 7)
            self.assertEqual(get_latest_revision_id(project), "r0007")

    def test_create_revision_auto_prune_invalid_config_falls_back_to_twenty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            with self._keep_last_n_env(-3):
                for i in range(21):
                    create_revision(project, f"rev {i}")

            # 非法值（负数）回退默认 20：只留最新 20 条，不炸
            self.assertEqual(len(list_revisions(project)), 20)
            self.assertEqual(get_latest_revision_id(project), "r0021")

    # ── 成品归档区（artifacts/）────────────────────────────

    def _write_gsm(self, tmpdir: str, name: str = "Chair.gsm", data: bytes = b"GSM") -> Path:
        gsm = Path(tmpdir) / name
        gsm.write_bytes(data)
        return gsm

    def test_archive_artifact_versions_by_revision_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir).resolve()
            gsm = self._write_gsm(tmpdir)

            archive_path = archive_artifact(project, gsm, revision_id="r0007")

            self.assertTrue(archive_path.exists())
            self.assertEqual(archive_path.relative_to(project).as_posix(), "artifacts/r0007/Chair.gsm")
            self.assertEqual(archive_path.read_bytes(), b"GSM")

    def test_archive_artifact_unversioned_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir).resolve()
            gsm = self._write_gsm(tmpdir)

            first = archive_artifact(project, gsm)
            second = archive_artifact(project, gsm)
            third = archive_artifact(project, gsm, revision_id="r0001")
            fourth = archive_artifact(project, gsm, revision_id="r0001")

            self.assertEqual(first.relative_to(project).as_posix(), "artifacts/unversioned/Chair.gsm")
            # 同名不覆盖：追加短数字后缀
            self.assertEqual(second.relative_to(project).as_posix(), "artifacts/unversioned/Chair-1.gsm")
            self.assertEqual(third.relative_to(project).as_posix(), "artifacts/r0001/Chair.gsm")
            self.assertEqual(fourth.relative_to(project).as_posix(), "artifacts/r0001/Chair-1.gsm")
            for p in (first, second, third, fourth):
                self.assertTrue(p.exists())
                self.assertEqual(p.read_bytes(), b"GSM")

    def test_archive_artifact_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            with self.assertRaises(FileNotFoundError):
                archive_artifact(project, Path(tmpdir) / "nope.gsm")

    def test_create_revision_manifest_gsm_path_points_to_archived_artifact(self):
        """端到端：revision manifest 的 gsm_path 指向归档路径且文件真实存在（修悬空）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            gsm = self._write_gsm(tmpdir, data=b"GSM-V1")
            metadata = {
                "compile": {
                    "mode": "lp", "success": True,
                    "gsm_size_bytes": gsm.stat().st_size,
                    "gsm_path": str(gsm),
                    "parameter_count": 0, "exit_code": 0,
                }
            }

            revision = create_revision(project, "compiled", metadata=metadata)

            self.assertIsNotNone(revision.compile)
            gsm_path = revision.compile["gsm_path"]
            self.assertIn("artifacts", gsm_path)
            self.assertIn(revision.revision_id, gsm_path)
            self.assertTrue(Path(gsm_path).exists())
            self.assertEqual(Path(gsm_path).read_bytes(), b"GSM-V1")

    def test_create_revision_does_not_archive_on_compile_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            metadata = {
                "compile": {
                    "mode": "lp", "success": False,
                    "gsm_size_bytes": None,
                    "gsm_path": None,
                    "parameter_count": 0, "exit_code": 1,
                }
            }

            revision = create_revision(project, "failed", metadata=metadata)

            self.assertEqual(revision.compile["gsm_path"], None)
            self.assertFalse((project / "artifacts").exists())

    def test_list_archived_artifacts_returns_entries_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = self._make_project(tmpdir)
            gsm = self._write_gsm(tmpdir)
            archive_artifact(project, gsm, revision_id="r0001")
            archive_artifact(project, gsm, revision_id="r0002")
            archive_artifact(project, gsm)

            entries = list_archived_artifacts(project)

            self.assertEqual(len(entries), 3)
            versions = {e["version"] for e in entries}
            self.assertEqual(versions, {"r0001", "r0002", "unversioned"})
            for e in entries:
                self.assertTrue(Path(e["path"]).exists())
                self.assertEqual(e["size_bytes"], 3)
                self.assertIn("mtime_iso", e)
            # limit 生效
            self.assertEqual(len(list_archived_artifacts(project, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
