import importlib.util
import sys
from pathlib import Path


def _load_gate_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "workbench_readiness_gate.py"
    spec = importlib.util.spec_from_file_location("workbench_readiness_gate", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_workbench_readiness_gate_targeted_plan_contains_core_checks():
    gate = _load_gate_module()

    plan = gate.build_plan(full=False)

    names = [step.name for step in plan]
    assert names == [
        "backend workbench api",
        "backend vision smoke tests",
        "frontend tests",
        "frontend build",
        "vision smoke",
    ]
    assert plan[0].command[:4] == ["python", "-m", "pytest", "tests/test_workbench_api.py"]


def test_workbench_readiness_gate_full_plan_uses_full_python_suite():
    gate = _load_gate_module()

    plan = gate.build_plan(full=True)
    names = [step.name for step in plan]

    assert plan[0].name == "backend full tests"
    assert plan[0].command == ["python", "-m", "pytest", "tests/", "-q"]
    assert "browser smoke" in names


def test_workbench_readiness_gate_aggregates_failures(tmp_path):
    gate = _load_gate_module()
    calls = []

    def fake_runner(step):
        calls.append(step.name)
        return gate.StepResult(
            name=step.name,
            command=step.command,
            returncode=1 if step.name == "frontend build" else 0,
            stdout="",
            stderr="build failed" if step.name == "frontend build" else "",
        )

    result = gate.run_gate(root=tmp_path, full=False, runner=fake_runner)

    assert result["ok"] is False
    assert calls[-1] == "vision smoke"
    assert any(step["name"] == "frontend build" and step["ok"] is False for step in result["steps"])
