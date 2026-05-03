import importlib.util
import sys
from pathlib import Path


def _load_launcher():
    path = Path(__file__).resolve().parents[1] / "packaging" / "openbrep_launcher.py"
    spec = importlib.util.spec_from_file_location("_openbrep_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_launcher_runs_streamlit_in_process(monkeypatch, tmp_path):
    launcher = _load_launcher()
    app_py = tmp_path / "ui" / "app.py"
    app_py.parent.mkdir()
    app_py.write_text("print('app')\n", encoding="utf-8")

    calls = {}

    monkeypatch.setattr(launcher, "_resource_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(launcher, "_find_free_port", lambda: 18601)
    monkeypatch.setattr(launcher, "_schedule_browser_open", lambda port: calls.setdefault("port", port))
    monkeypatch.setattr(
        launcher,
        "_run_streamlit_cli",
        lambda args: calls.__setitem__("args", args) or 0,
    )

    assert launcher.main() == 0
    assert calls["port"] == 18601
    assert calls["args"][0] == "run"
    assert str(app_py) in calls["args"]
    assert sys.executable not in calls["args"]
    assert "-m" not in calls["args"]
    assert "--server.port" in calls["args"]
    assert "18601" in calls["args"]


def test_launcher_can_use_explicit_smoke_test_port(monkeypatch):
    launcher = _load_launcher()

    monkeypatch.setenv("OPENBREP_PORT", "19001")

    assert launcher._find_free_port() == 19001


def test_launcher_can_disable_browser_for_smoke_tests(monkeypatch):
    launcher = _load_launcher()
    calls = []

    monkeypatch.setenv("OPENBREP_NO_BROWSER", "1")
    monkeypatch.setattr(launcher.threading, "Thread", lambda **_kwargs: calls.append(_kwargs))

    launcher._schedule_browser_open(19001)

    assert calls == []


def test_launcher_returns_error_when_app_is_missing(monkeypatch, tmp_path):
    launcher = _load_launcher()

    monkeypatch.setattr(launcher, "_resource_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(
        launcher,
        "_run_streamlit_cli",
        lambda _args: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    assert launcher.main() == 1


def test_macos_build_package_includes_clickable_command_launcher():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_macos.sh").read_text(
        encoding="utf-8"
    )

    assert "dist/OpenBrep/OpenBrep.command" in script
    assert "chmod +x dist/OpenBrep/OpenBrep.command" in script
    assert "dist/OpenBrep/README-macOS.txt" in script
