from __future__ import annotations

import importlib.util
from pathlib import Path


def load_launcher_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "obr7.py"
    spec = importlib.util.spec_from_file_location("obr7_launcher", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_available_port_skips_busy_port(monkeypatch):
    launcher = load_launcher_module()

    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": port == 8766)

    assert launcher.find_available_port(8765) == 8766


def test_choose_port_auto_shifts_default_when_busy(monkeypatch):
    launcher = load_launcher_module()
    monkeypatch.delenv("OBR7_API_PORT", raising=False)
    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": port != 8765)

    port, shifted = launcher.choose_port(explicit=None, env_name="OBR7_API_PORT", default=8765)

    assert shifted is True
    assert port == 8766


def test_choose_port_fails_for_busy_explicit_port(monkeypatch):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": False)

    try:
        launcher.choose_port(explicit=8765, env_name="OBR7_API_PORT", default=8765)
    except RuntimeError as exc:
        assert "already in use" in str(exc)
    else:
        raise AssertionError("expected busy explicit port to fail")


def test_launcher_builds_frontend_command_with_strict_port():
    launcher = load_launcher_module()

    assert launcher.build_web_command(5199) == [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5199",
        "--strictPort",
    ]
