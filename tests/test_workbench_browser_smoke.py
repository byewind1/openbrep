import importlib.util
import subprocess
import sys
from pathlib import Path


def _load_smoke_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "workbench_browser_smoke.py"
    spec = importlib.util.spec_from_file_location("workbench_browser_smoke", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_workbench_browser_smoke_builds_obr7_command(tmp_path):
    smoke = _load_smoke_module()

    command, env = smoke.build_obr7_launch(tmp_path, api_port=19065, web_port=19074)

    assert command == [str(tmp_path / "obr7"), "--no-open"]
    assert env["OBR7_API_PORT"] == "19065"
    assert env["OBR7_WEB_PORT"] == "19074"


def test_workbench_browser_smoke_accepts_code_first_page_markers():
    smoke = _load_smoke_module()

    assert smoke.page_has_workbench_markers(
        title="OpenBrep Workbench",
        body="SCRIPTS 3d.gdl Save Mock Compile Settings",
    )


def test_workbench_browser_smoke_rejects_plain_vite_shell():
    smoke = _load_smoke_module()

    assert not smoke.page_has_workbench_markers(
        title="OpenBrep Workbench",
        body="",
    )


def test_workbench_browser_smoke_detects_mock_compile_result():
    smoke = _load_smoke_module()

    assert smoke.body_has_mock_compile_result("Mock compile passed in 12 ms")
    assert smoke.body_has_mock_compile_result("✓ 编译通过")
    assert not smoke.body_has_mock_compile_result("Not compiled")


def test_workbench_browser_smoke_detects_save_result():
    smoke = _load_smoke_module()

    assert smoke.body_has_script_save_result("Saved 3d.gdl at 2026-05-31T10:00:00")
    assert not smoke.body_has_script_save_result("Saved")


def test_workbench_browser_smoke_creates_disk_hsf_project(tmp_path):
    smoke = _load_smoke_module()

    hsf_dir = smoke.create_smoke_hsf_project(tmp_path)

    assert (hsf_dir / "scripts" / "3d.gdl").exists()
    assert (hsf_dir / "paramlist.xml").exists()


def test_workbench_browser_smoke_collects_process_output():
    smoke = _load_smoke_module()
    process = subprocess.Popen(
        [sys.executable, "-c", "print('workbench smoke output')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    process.wait(timeout=5)

    output = smoke.collect_process_output(process)

    assert "workbench smoke output" in output
