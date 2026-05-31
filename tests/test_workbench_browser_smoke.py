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

    command = smoke.build_obr7_command(tmp_path, api_port=19065, web_port=19074)

    assert command == [
        str(tmp_path / "obr7"),
        "--no-open",
        "--api-port",
        "19065",
        "--web-port",
        "19074",
    ]


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
