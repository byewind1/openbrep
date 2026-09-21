import json
import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import GDLParameter
from openbrep.materials import infer_material_slots, load_materials, normalize_slots, save_materials


class TestMaterials(unittest.TestCase):
    def test_normalize_slots_applies_wood_preset_and_preserves_valid_overrides(self):
        slots, warnings = normalize_slots({
            "mat_top": {"family": "wood", "label": "Oak", "color": "#a87848", "roughness": 0.7},
        })

        self.assertEqual(warnings, [])
        self.assertEqual(slots["mat_top"]["color"], "#A87848")
        self.assertEqual(slots["mat_top"]["roughness"], 0.7)
        self.assertEqual(slots["mat_top"]["metalness"], 0.0)
        self.assertEqual(slots["mat_top"]["opacity"], 1.0)

    def test_save_and_load_round_trip_without_overwriting_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_materials(root, {"version": 1, "slots": {"mat_glass": {"family": "glass"}}})
            document, warnings = load_materials(root)

            self.assertEqual(warnings, [])
            self.assertEqual(document["slots"]["mat_glass"]["transmission"], 0.9)

            path = root / ".openbrep" / "materials.json"
            path.write_text("not json", encoding="utf-8")
            invalid, invalid_warnings = load_materials(root)
            self.assertEqual(invalid["slots"], {})
            self.assertTrue(invalid_warnings)
            self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_infer_material_slots_uses_part_names_and_user_semantics(self):
        slots = infer_material_slots(
            [
                GDLParameter("mat_top", "Material", "桌面"),
                GDLParameter("mat_leg", "Material", "桌腿"),
            ],
            "浅色橡木桌面，黑色金属桌腿",
        )

        self.assertEqual(slots["mat_top"]["family"], "wood")
        self.assertEqual(slots["mat_leg"]["family"], "metal")


if __name__ == "__main__":
    unittest.main()
