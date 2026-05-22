import tempfile
import unittest
from pathlib import Path

from benchmark.assertions import assert_success_criteria
from benchmark.runner import BenchmarkRunner
from benchmark.schema import SuccessCriteria, load_benchmark_task
from openbrep.core import AgentResult, Status
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType


class TestBenchmarkAssertions(unittest.TestCase):
    def _project(self) -> HSFProject:
        project = HSFProject.create_new("BenchObject")
        project.add_parameter(GDLParameter("n_shelves", "Integer", value="4"))
        project.add_parameter(GDLParameter("shelf_thk", "Length", value="0.02"))
        project.set_script(ScriptType.MASTER, "shelf_gap = ZZYZX / n_shelves\n")
        project.set_script(
            ScriptType.SCRIPT_3D,
            "FOR i = 1 TO n_shelves\n"
            "  ADDZ i * shelf_gap\n"
            "  BLOCK A, B, shelf_thk\n"
            "  DEL 1\n"
            "NEXT i\n",
        )
        project.set_script(
            ScriptType.SCRIPT_2D,
            "HOTSPOT2 0, 0\n"
            "HOTSPOT2 A, 0\n"
            "HOTSPOT2 A, B\n"
            "PROJECT2 3, 270, 2\n",
        )
        return project

    def test_required_params_scripts_and_geometry_commands_pass(self):
        criteria = SuccessCriteria(
            required_params=["A", "B", "ZZYZX", "n_shelves", "shelf_thk"],
            required_scripts=["3d.gdl", "1d.gdl", "2d.gdl", "paramlist.xml"],
            geometry_check="层板数量参数驱动，FOR循环正确，ADD/DEL配平，单一 BLOCK 几何",
        )

        result = assert_success_criteria(self._project(), criteria)

        self.assertTrue(result.passed, result.failures)

    def test_missing_required_param_fails(self):
        project = self._project()
        project.remove_parameter("n_shelves")
        criteria = SuccessCriteria(required_params=["n_shelves"])

        result = assert_success_criteria(project, criteria)

        self.assertFalse(result.passed)
        self.assertIn("required_param: missing n_shelves", result.failures)

    def test_missing_required_script_fails(self):
        project = self._project()
        project.scripts.pop(ScriptType.SCRIPT_2D)
        criteria = SuccessCriteria(required_scripts=["2d.gdl"])

        result = assert_success_criteria(project, criteria)

        self.assertFalse(result.passed)
        self.assertIn("required_script: missing or empty scripts/2d.gdl", result.failures)

    def test_2d_script_must_have_hotspot_or_project2_when_required(self):
        project = self._project()
        project.set_script(ScriptType.SCRIPT_2D, "LINE2 0, 0, A, B\n")
        criteria = SuccessCriteria(required_scripts=["2d.gdl"])

        result = assert_success_criteria(project, criteria)

        self.assertFalse(result.passed)
        self.assertIn("2d_representation: scripts/2d.gdl must contain PROJECT2 or HOTSPOT2", result.failures)

    def test_geometry_check_for_loop_requires_for_and_next(self):
        project = self._project()
        project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
        criteria = SuccessCriteria(geometry_check="FOR循环驱动步数，总高度 = steps * riser_h")

        result = assert_success_criteria(project, criteria)

        self.assertFalse(result.passed)
        self.assertIn("geometry_command: missing FOR", result.failures)
        self.assertIn("geometry_command: missing NEXT", result.failures)

    def test_load_benchmark_task_parses_success_criteria(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_path = Path(tmpdir) / "C99.yaml"
            task_path.write_text(
                "---\n"
                "id: C99\n"
                "category: create\n"
                "complexity: low\n"
                "description: test object\n"
                "success_criteria:\n"
                "  compile_pass: true\n"
                "  required_params: [A, B]\n"
                "  required_scripts: [3d.gdl, paramlist.xml]\n"
                "  geometry_check: \"BLOCK geometry\"\n",
                encoding="utf-8",
            )

            task = load_benchmark_task(task_path)

            self.assertEqual(task.id, "C99")
            self.assertEqual(task.success_criteria.required_params, ["A", "B"])
            self.assertEqual(task.success_criteria.required_scripts, ["3d.gdl", "paramlist.xml"])
            self.assertEqual(task.success_criteria.geometry_check, "BLOCK geometry")

    def test_create_benchmark_tasks_all_have_machine_readable_criteria(self):
        task_dir = Path(__file__).resolve().parents[1] / "benchmark" / "tasks" / "create"
        tasks = [load_benchmark_task(path) for path in sorted(task_dir.glob("*.yaml"))]

        self.assertEqual(len(tasks), 10)
        for task in tasks:
            self.assertTrue(task.success_criteria.required_params, task.id)
            self.assertTrue(task.success_criteria.required_scripts, task.id)
            self.assertTrue(task.success_criteria.geometry_check, task.id)


class _FakeBenchmarkAgent:
    def __init__(self, configure_project):
        self.configure_project = configure_project

    def run(self, instruction, project, output_gsm):
        self.configure_project(project)
        return AgentResult(
            status=Status.SUCCESS,
            attempts=1,
            output_path=output_gsm,
            project=project,
            history=[{"attempt": 1, "stage": "compile", "result": "success"}],
        )


class TestBenchmarkRunnerCriteria(unittest.TestCase):
    def _task_file(self, tmpdir: str) -> Path:
        task_path = Path(tmpdir) / "C99.yaml"
        task_path.write_text(
            "---\n"
            "id: C99\n"
            "category: create\n"
            "complexity: low\n"
            "description: create test object\n"
            "success_criteria:\n"
            "  compile_pass: true\n"
            "  required_params: [A, B, ZZYZX, custom_w]\n"
            "  required_scripts: [3d.gdl, 2d.gdl, paramlist.xml]\n"
            "  geometry_check: \"单一 BLOCK 几何\"\n",
            encoding="utf-8",
        )
        return task_path

    def _runner(self, tmpdir: str, configure_project) -> BenchmarkRunner:
        runner = BenchmarkRunner.__new__(BenchmarkRunner)
        runner.agent = _FakeBenchmarkAgent(configure_project)
        runner.results_dir = Path(tmpdir) / "results"
        runner.work_dir = Path(tmpdir) / "workdir"
        runner.mode = "mock"
        runner.effective_mode = "mock"
        runner.compiler_skip_reason = ""
        runner.compiler = None
        return runner

    def test_runner_outputs_structured_criteria_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_path = self._task_file(tmpdir)

            def configure(project):
                project.add_parameter(GDLParameter("custom_w", "Length", value="1.0"))
                project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
                project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")

            result = self._runner(tmpdir, configure).run_task(str(task_path))

            self.assertTrue(result["success"], result)
            self.assertTrue(result["compile_pass"])
            self.assertEqual(result["compile_mode"], "mock")
            self.assertIsNone(result["compile_exit_code"])
            self.assertEqual(result["compile_stderr"], "")
            self.assertTrue(result["static_pass"])
            self.assertTrue(result["criteria_pass"])
            self.assertEqual(result["criteria_failures"], [])

    def test_runner_fails_when_required_param_is_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_path = self._task_file(tmpdir)

            def configure(project):
                project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
                project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 270, 2\n")

            result = self._runner(tmpdir, configure).run_task(str(task_path))

            self.assertFalse(result["success"])
            self.assertTrue(result["compile_pass"])
            self.assertTrue(result["static_pass"])
            self.assertFalse(result["criteria_pass"])
            self.assertIn("required_param: missing custom_w", result["criteria_failures"])


if __name__ == "__main__":
    unittest.main()
