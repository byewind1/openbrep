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


def test_find_available_port_uses_fallback_range(monkeypatch):
    launcher = load_launcher_module()

    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": port == 19065)

    assert launcher.find_available_port(8766, max_attempts=50, fallback_start=19065) == 19065


def test_choose_port_auto_shifts_default_when_busy(monkeypatch):
    launcher = load_launcher_module()
    monkeypatch.delenv("OBR7_API_PORT", raising=False)
    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": port != 8765)

    port, shifted = launcher.choose_port(explicit=None, env_name="OBR7_API_PORT", default=8765)

    assert shifted is True
    assert port == 8766


def test_choose_port_falls_back_to_high_port(monkeypatch):
    launcher = load_launcher_module()
    monkeypatch.delenv("OBR7_API_PORT", raising=False)
    monkeypatch.setattr(launcher, "is_port_available", lambda port, host="127.0.0.1": port == 19065)

    port, shifted = launcher.choose_port(
        explicit=None,
        env_name="OBR7_API_PORT",
        default=8765,
        fallback_start=19065,
    )

    assert shifted is True
    assert port == 19065


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


def test_python_launcher_exports_vite_api_url():
    launcher_path = Path(__file__).resolve().parents[1] / "scripts" / "obr7.py"

    contents = launcher_path.read_text(encoding="utf-8")

    assert 'env["VITE_OPENBREP_API"] = api_url' in contents


def test_python_launcher_resolves_main_worktree_config(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    main_root = tmp_path / "repo"
    worktree_root = main_root / ".worktrees" / "react-workbench"
    git_dir = main_root / ".git"
    worktree_root.mkdir(parents=True)
    git_dir.mkdir()
    main_config = main_root / "config.toml"
    main_config.write_text("[llm]\nmodel = \"mimo-v2.5-pro\"\n", encoding="utf-8")
    (worktree_root / "config.toml").write_text("[llm]\nmodel = \"deepseek-chat\"\n", encoding="utf-8")

    monkeypatch.delenv("GDL_AGENT_CONFIG", raising=False)
    monkeypatch.setattr(
        launcher.subprocess,
        "check_output",
        lambda *args, **kwargs: str(git_dir),
    )

    assert launcher.resolve_shared_config_path(worktree_root) == main_config


def test_python_launcher_respects_explicit_config_env(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    explicit_config = tmp_path / "custom.toml"

    monkeypatch.setenv("GDL_AGENT_CONFIG", str(explicit_config))

    assert launcher.resolve_shared_config_path(tmp_path) == explicit_config


def test_obr7_entrypoint_delegates_to_python_launcher():
    entrypoint = Path(__file__).resolve().parents[1] / "obr7"

    contents = entrypoint.read_text(encoding="utf-8")

    assert 'exec python "$APP_DIR/scripts/obr7.py" "$@"' in contents
