import multiprocessing
import time

from openbrep.project_write_lock import project_write_lock


def _contend(root: str, active, maximum) -> None:
    with project_write_lock(root):
        with active.get_lock():
            active.value += 1
            maximum.value = max(maximum.value, active.value)
        time.sleep(0.04)
        with active.get_lock():
            active.value -= 1


def test_project_write_lock_serializes_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBREP_PROJECT_LOCK_DIR", str(tmp_path / "locks"))
    context = multiprocessing.get_context("spawn")
    active = context.Value("i", 0)
    maximum = context.Value("i", 0)
    project = tmp_path / "Object"
    project.mkdir()
    processes = [context.Process(target=_contend, args=(str(project), active, maximum)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert maximum.value == 1
