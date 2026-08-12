"""
tests/test_extraction_store — P5d-1 提取工件存储（设计 D7 内容哈希寻址）

覆盖：
1. save/load 往返：schema 字段/置信度/修正/降级标记/模型名/时间戳齐全
2. sha256 为空 → 跳过落盘（返回 None，不计错误）
3. 同 sha256 覆盖写（幂等：内容一致再写不炸）
4. generic 路径 VisualStructure dataclass 字段 → JSON 可序列化（asdict 化）
5. extraction_hash_from_filename / list_extraction_hashes（manifest 口径）
6. load 不存在的哈希 → None；损坏文件 → None（不抛）
"""

import json
import unittest
from pathlib import Path

from openbrep.vision.extraction_store import (
    EXTRACTION_HASH_LEN,
    extraction_hash_from_filename,
    list_extraction_hashes,
    load_extraction,
    plan_to_dict,
    save_extraction,
)
from openbrep.vision.modeling_plan import ModelingPlan
from openbrep.vision.schema import VisualLayer, VisualStructure


def _schema_plan(sha: str = "ab" * 32) -> ModelingPlan:
    """lattice_window 风格 plan（含低置信 + critic 修正 + 无降级）。"""
    return ModelingPlan(
        schema_name="lattice_window",
        fields={
            "opening_shape": "rect",
            "pattern_family": "冰裂",
            "grid_topology": {"kind": "grid", "rows": 4, "cols": 4, "cell_desc": "方冰裂单元"},
            "bar_width_ratio": 0.08,
        },
        confidence={
            "opening_shape": "high",
            "pattern_family": "high",
            "grid_topology.rows": "low",  # critic unknown → low
            "bar_width_ratio": "low",
        },
        corrections=[
            {
                "field": "grid_topology.rows",
                "old": 4,
                "new": 3,
                "evidence": "图中棂条为 3 行",
            }
        ],
        source_images=[sha],
        raw_description="方洞冰裂纹漏窗",
    )


def _generic_plan(sha: str = "cd" * 32) -> ModelingPlan:
    vs = VisualStructure(
        component_type="斗",
        main_form="tapered_block",
        layers=[VisualLayer("base", "PRISM_", "台座", parametric=True)],
        key_features=["收分"],
        parametrize=["A"],
        raw_description="一个斗",
    )
    return ModelingPlan(
        schema_name="generic",
        fields={"visual_structure": vs},
        confidence={},
        corrections=[],
        source_images=[sha],
    )


class TestSaveLoadRoundtrip(unittest.TestCase):
    def test_schema_plan_roundtrip_preserves_all_fields(self, tmp_path=None):
        tmp_path = Path(tmp_path) if tmp_path else Path(__file__).parent / "_tmp_extraction"
        tmp_path.mkdir(exist_ok=True)
        try:
            plan = _schema_plan()
            path = save_extraction(tmp_path, plan, model="kimi-k2.6")

            self.assertIsNotNone(path)
            self.assertEqual(path.name, f"extraction-{plan.source_images[0][:12]}.json")
            self.assertEqual(path.parent.name, "vision")
            self.assertEqual(path.parent.parent.name, ".openbrep")
            self.assertTrue(path.exists())

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_name"], "lattice_window")
            self.assertEqual(data["fields"]["opening_shape"], "rect")
            self.assertEqual(data["fields"]["grid_topology"]["rows"], 4)
            self.assertEqual(data["confidence"]["grid_topology.rows"], "low")
            self.assertEqual(data["corrections"][0]["field"], "grid_topology.rows")
            self.assertEqual(data["corrections"][0]["old"], 4)
            self.assertEqual(data["corrections"][0]["new"], 3)
            self.assertFalse(data["degraded"])
            self.assertFalse(data["critic_degraded"])
            self.assertEqual(data["model"], "kimi-k2.6")
            self.assertIn("created_at", data)

            # load 往返（完整哈希与前 12 位均可）
            loaded = load_extraction(tmp_path, plan.source_images[0])
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["schema_name"], "lattice_window")
            self.assertEqual(loaded["model"], "kimi-k2.6")
            loaded_short = load_extraction(tmp_path, plan.source_images[0][:12])
            self.assertEqual(loaded_short["schema_name"], "lattice_window")
        finally:
            import shutil

            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_generic_visual_structure_serialized_to_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plan = _generic_plan()
            path = save_extraction(tmp, plan, model="m")
            self.assertIsNotNone(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            vs = data["fields"]["visual_structure"]
            self.assertEqual(vs["component_type"], "斗")
            self.assertEqual(vs["layers"][0]["name"], "base")
            self.assertEqual(vs["layers"][0]["parametric"], True)
            self.assertEqual(vs["raw_description"], "一个斗")

    def test_empty_sha256_skips_save(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plan = _schema_plan(sha="")
            path = save_extraction(tmp, plan, model="m")
            self.assertIsNone(path)
            # 目录都不该被创建（无任何落盘内容）
            self.assertFalse((Path(tmp) / ".openbrep" / "vision").exists())

    def test_same_sha256_overwrites_idempotently(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plan = _schema_plan()
            p1 = save_extraction(tmp, plan, model="m")
            p2 = save_extraction(tmp, plan, model="m")
            self.assertEqual(p1, p2)
            # 同名单文件（覆盖写，不叠加）
            files = list((Path(tmp) / ".openbrep" / "vision").glob("*.json"))
            self.assertEqual(len(files), 1)

    def test_load_missing_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_extraction(tmp, "00" * 32))

    def test_load_corrupt_file_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".openbrep" / "vision" / f"extraction-{'ee' * 12}.json"
            target.parent.mkdir(parents=True)
            target.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(load_extraction(tmp, "ee" * 32))


class TestHashNaming(unittest.TestCase):
    def test_extraction_hash_from_filename(self):
        self.assertEqual(extraction_hash_from_filename("extraction-abcdef123456.json"), "abcdef123456")
        self.assertIsNone(extraction_hash_from_filename("manifest.json"))
        self.assertIsNone(extraction_hash_from_filename("extraction-too-short.json"))
        self.assertIsNone(extraction_hash_from_filename("other-abcdef123456.json"))

    def test_list_extraction_hashes_sorted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_extraction_hashes(tmp), [])
            save_extraction(tmp, _schema_plan(sha="aa" * 32), model="m")
            save_extraction(tmp, _schema_plan(sha="bb" * 32), model="m")
            # 无关文件不进列表
            (Path(tmp) / ".openbrep" / "vision" / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                list_extraction_hashes(tmp),
                ["a" * 12, "b" * 12],
            )

    def test_plan_to_dict_is_pure_json(self):
        plan = _generic_plan()
        data = plan_to_dict(plan)
        # 序列化后必须是纯 JSON 形状（dataclass → dict）
        json.dumps(data, ensure_ascii=False)  # 不抛
        self.assertEqual(data["sha256"], "cd" * 32)
        self.assertFalse(data["degraded"])
        self.assertEqual(data["schema_name"], "generic")


if __name__ == "__main__":
    unittest.main()
