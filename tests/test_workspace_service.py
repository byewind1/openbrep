"""Workspace Service（四区工作区层）合同测试。

覆盖：init 幂等/冲突报告、scan 索引、search 跨项目搜索、import_to_workspace
（原件进 sources/、项目进 hsf/、origin 指向 sources 副本）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.project_session_service import write_project_origin
from openbrep.workbench.workspace_service import (
    import_to_workspace,
    init_workspace,
    scan_workspace,
    search_workspace,
)


def _make_workspace(tmpdir: str) -> Path:
    ws = Path(tmpdir) / "ws"
    result = init_workspace(str(ws))
    assert result["ok"] is True
    return ws.resolve()


def _make_project(root: Path, name: str, work_dir: Path) -> Path:
    project = HSFProject.create_new(name, str(work_dir))
    project.scripts[ScriptType.SCRIPT_3D] = f"BLOCK A, B, ZZYZX\nCYLIND {name.upper()}_H, 1\n"
    project.scripts[ScriptType.SCRIPT_2D] = "PROJECT2 3, 270, 2\n"
    project.add_parameter(
        __import__("openbrep.paramlist_builder", fromlist=["GDLParameter"]).GDLParameter(
            f"{name}_shelf_count", "Integer", "层板数", "3"
        )
    )
    hsf_dir = project.save_to_disk()
    return hsf_dir.resolve()


class TestInitWorkspace(unittest.TestCase):
    def test_init_creates_zones_and_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir) / "ws"
            result = init_workspace(str(ws))

            self.assertTrue(result["ok"])
            self.assertEqual(result["zones"], ["materials", "sources", "hsf", "artifacts"])
            for zone in ("materials", "sources", "hsf", "artifacts"):
                self.assertTrue((ws / zone).is_dir())
            toml_path = ws / ".openbrep" / "workspace.toml"
            self.assertTrue(toml_path.is_file())
            text = toml_path.read_text(encoding="utf-8")
            self.assertIn("[workspace]", text)
            self.assertIn("created_at", text)
            self.assertIn("[zones]", text)

    def test_init_idempotent_toml_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _make_workspace(tmpdir)
            toml = ws / ".openbrep" / "workspace.toml"
            before = toml.read_bytes()

            result = init_workspace(str(ws))

            self.assertTrue(result["ok"])
            self.assertTrue(result["idempotent"])
            self.assertEqual(result["missing_zones"], [])
            self.assertEqual(toml.read_bytes(), before)

    def test_init_reports_conflicts_without_failing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir) / "ws"
            ws.mkdir()
            (ws / "notes.txt").write_text("user stuff", encoding="utf-8")
            (ws / "random_dir").mkdir()

            result = init_workspace(str(ws))

            self.assertTrue(result["ok"])
            self.assertIn("notes.txt", result["conflicts"])
            self.assertIn("random_dir", result["conflicts"])
            # 四区照常创建
            self.assertTrue((ws / "hsf").is_dir())

    def test_scan_non_workspace_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = scan_workspace(str(Path(tmpdir) / "nows"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "not_a_workspace")


class TestScanWorkspace(unittest.TestCase):
    def test_scan_lists_projects_sources_materials_and_zones(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _make_workspace(tmpdir)
            # 项目一：带 origin
            p1 = _make_project(ws, "Shelf", ws / "hsf")
            write_project_origin(p1, imported_from=str(ws / "sources" / "shelf.gdl"), imported_kind="gdl")
            # 项目二：不带 origin
            _make_project(ws, "Chair", ws / "hsf")
            # 素材 + 资料
            (ws / "sources" / "ref.gsm").write_bytes(b"GSM")
            (ws / "sources" / "notes.txt").write_text("todo", encoding="utf-8")
            (ws / "materials" / "spec.pdf").write_bytes(b"%PDF")
            (ws / "materials" / "sub" / "photo.png").parent.mkdir()
            (ws / "materials" / "sub" / "photo.png").write_bytes(b"PNG")

            result = scan_workspace(str(ws))

            self.assertTrue(result["ok"])
            self.assertEqual(result["project_count"], 2)
            self.assertEqual(result["missing_zones"], [])
            self.assertEqual(result["source_count"], 2)
            self.assertEqual(result["materials_count"], 2)

            by_name = {p["name"]: p for p in result["projects"]}
            self.assertIn("Shelf", by_name)
            self.assertIn("Chair", by_name)
            shelf = by_name["Shelf"]
            self.assertEqual(shelf["parameter_count"], 4)  # A/B/ZZYZX + shelf_count
            self.assertIn("SCRIPT_3D", shelf["scripts_present"])
            self.assertIn("SCRIPT_2D", shelf["scripts_present"])
            self.assertIsNone(shelf["latest_revision_id"])
            self.assertIsNotNone(shelf["origin"])
            self.assertEqual(shelf["origin"]["imported_from"], str((ws / "sources" / "shelf.gdl").resolve()))
            self.assertEqual(shelf["artifact_count"], 0)
            self.assertIsNone(by_name["Chair"]["origin"])

            sources_by_name = {s["name"]: s for s in result["sources"]}
            self.assertEqual(sources_by_name["ref.gsm"]["kind"], "gsm")
            self.assertEqual(sources_by_name["notes.txt"]["kind"], "txt")
            self.assertGreater(sources_by_name["ref.gsm"]["size_bytes"], 0)


class TestSearchWorkspace(unittest.TestCase):
    def _workspace_with_projects(self, tmpdir: str) -> Path:
        ws = _make_workspace(tmpdir)
        _make_project(ws, "Shelf", ws / "hsf")
        _make_project(ws, "Chair", ws / "hsf")
        return ws

    def test_search_matches_parameter_name_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._workspace_with_projects(tmpdir)
            result = search_workspace(str(ws), "SHELF_COUNT")
            self.assertTrue(result["ok"])
            names = {(h["project"], h["location"]) for h in result["hits"]}
            self.assertIn(("Shelf", "paramlist.xml"), names)

    def test_search_matches_script_content_with_line_number(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._workspace_with_projects(tmpdir)
            result = search_workspace(str(ws), "cylind")
            self.assertTrue(result["ok"])
            hit = next(
                (h for h in result["hits"] if h["location"] == "scripts/3d.gdl"),
                None,
            )
            self.assertIsNotNone(hit)
            self.assertEqual(hit["line"], 2)  # CYLIND 在 BLOCK 之后
            self.assertIn("CYLIND", hit["snippet"])

    def test_search_matches_project_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._workspace_with_projects(tmpdir)
            result = search_workspace(str(ws), "chair")
            self.assertTrue(result["ok"])
            self.assertTrue(any(h["location"] == "name" and h["project"] == "Chair" for h in result["hits"]))

    def test_search_no_hit_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._workspace_with_projects(tmpdir)
            result = search_workspace(str(ws), "不存在的词xyz")
            self.assertTrue(result["ok"])
            self.assertEqual(result["hit_count"], 0)
            self.assertEqual(result["hits"], [])

    def test_search_empty_query_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._workspace_with_projects(tmpdir)
            result = search_workspace(str(ws), "  ")
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_query")


class TestImportToWorkspace(unittest.TestCase):
    def test_import_copies_to_sources_and_creates_project_in_hsf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _make_workspace(tmpdir)
            source = Path(tmpdir) / "plant_pot.gdl"
            source.write_text(
                "BLOCK A, B, ZZYZX\nEND\n",
                encoding="utf-8",
            )

            result = import_to_workspace(str(ws), str(source), "gdl")

            self.assertTrue(result["ok"], result)
            # 原件进 sources/（归档副本）
            archived = Path(result["source_path"])
            self.assertTrue(archived.exists())
            self.assertEqual(archived.parent, (ws / "sources").resolve())
            self.assertEqual(archived.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
            # 项目建到 hsf/ 下
            project_path = Path(result["project_path"])
            self.assertTrue(project_path.is_dir())
            self.assertEqual(project_path.parent, (ws / "hsf").resolve())
            self.assertTrue((project_path / "libpartdata.xml").exists())
            # origin 指向 sources/ 内副本
            scan = scan_workspace(str(ws))
            project_entry = next(p for p in scan["projects"] if p["name"] == "plant_pot")
            self.assertIsNotNone(project_entry["origin"])
            self.assertEqual(project_entry["origin"]["imported_from"], str(archived))
            self.assertEqual(project_entry["origin"]["imported_kind"], "gdl")

    def test_import_same_source_twice_does_not_overwrite_original(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _make_workspace(tmpdir)
            source = Path(tmpdir) / "pot.gdl"
            source.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")

            first = import_to_workspace(str(ws), str(source), "gdl")
            second = import_to_workspace(str(ws), str(source), "gdl")

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            # 同名原件不覆盖：第二次复用同一个 sources 副本（字节相同）
            self.assertEqual(first["source_path"], second["source_path"])
            self.assertEqual(len(list((ws / "sources").glob("pot*.gdl"))), 1)

    def test_import_rejects_bad_kind_and_missing_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _make_workspace(tmpdir)
            bad_kind = import_to_workspace(str(ws), str(Path(tmpdir) / "x.gdl"), "glb")
            self.assertFalse(bad_kind["ok"])
            self.assertEqual(bad_kind["error"]["code"], "invalid_mode")

            missing = import_to_workspace(str(ws), str(Path(tmpdir) / "nope.gdl"), "gdl")
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["error"]["code"], "source_not_found")

    def test_import_requires_initialized_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir) / "plain"
            raw_dir.mkdir()
            source = Path(tmpdir) / "x.gdl"
            source.write_text("BLOCK A, B, ZZYZX\n", encoding="utf-8")
            result = import_to_workspace(str(raw_dir), str(source), "gdl")
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "not_a_workspace")


if __name__ == "__main__":
    unittest.main()
