import json
import tempfile
import unittest
from pathlib import Path

from openbrep.hsf_project import HSFProject
from openbrep.project_reports import write_object_plan_report


class TestProjectReports(unittest.TestCase):
    def test_write_object_plan_report_creates_json_markdown_and_latest_pointer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = HSFProject.create_new("bookshelf", work_dir=tmpdir)
            path = write_object_plan_report(
                project,
                {
                    "object_type": "专业书架",
                    "geometry": ["侧板", "层板"],
                    "parameters": ["Integer shelf_count = 层板数"],
                    "script_3d_strategy": ["FOR/NEXT"],
                    "script_2d_strategy": ["PROJECT2"],
                    "material_strategy": ["材质参数"],
                    "risks": ["DEL 平衡"],
                },
                instruction="生成一个书架",
                intent="CREATE",
            )

            reports_dir = Path(project.root) / ".openbrep" / "reports"
            latest = json.loads((reports_dir / "latest_object_plan.json").read_text(encoding="utf-8"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            markdown = (reports_dir / latest["object_plan_markdown"]).read_text(encoding="utf-8")

        self.assertEqual(payload["object_plan"]["object_type"], "专业书架")
        self.assertEqual(payload["instruction"], "生成一个书架")
        self.assertIn("专业书架", markdown)
        self.assertIn("层板", markdown)


if __name__ == "__main__":
    unittest.main()
