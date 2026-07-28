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


# ── daemon 模式 ───────────────────────────────────────────

import json  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402


def test_parse_args_daemon_flags():
    launcher = load_launcher_module()
    args = launcher.parse_args(["--daemon"])
    assert args.daemon and not args.status and not args.stop and not args.restart
    args = launcher.parse_args(["--restart"])
    assert args.restart and not args.daemon


def test_pid_alive():
    launcher = load_launcher_module()
    assert launcher._pid_alive(os.getpid()) is True
    assert launcher._pid_alive(2**22) is False
    assert launcher._pid_alive(-1) is False


def test_tcp_open():
    launcher = load_launcher_module()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert launcher._tcp_open(port) is True
    assert launcher._tcp_open(port) is False
    assert launcher._tcp_open(0) is False


def test_state_roundtrip(tmp_path, monkeypatch):
    launcher = load_launcher_module()
    state_path = tmp_path / "run" / "obr7.json"
    monkeypatch.setattr(launcher, "DAEMON_STATE_PATH", state_path)

    assert launcher._read_state() is None
    launcher._write_state(pid=123, ready=True, api_port=8765)
    state = launcher._read_state()
    assert state["pid"] == 123 and state["ready"] is True
    assert launcher._daemon_running(state) is False  # pid 123 不存在
    launcher._remove_state()
    assert launcher._read_state() is None


def test_daemon_status_reports_running(tmp_path, monkeypatch, capsys):
    launcher = load_launcher_module()
    state_path = tmp_path / "obr7.json"
    log_path = tmp_path / "logs" / "obr7.log"
    monkeypatch.setattr(launcher, "DAEMON_STATE_PATH", state_path)
    monkeypatch.setattr(launcher, "DAEMON_LOG_PATH", log_path)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "pid": os.getpid(), "ready": True,
            "api_port": port, "web_port": None,
            "api_pid": os.getpid(), "config": "/tmp/cfg.toml",
        }), encoding="utf-8")
        rc = launcher.daemon_status()

    out = capsys.readouterr().out
    assert rc == 0
    assert "运行中" in out
    assert f"{port}" in out
    assert "/tmp/cfg.toml" in out


def test_daemon_status_not_running_cleans_stale_state(tmp_path, monkeypatch, capsys):
    launcher = load_launcher_module()
    state_path = tmp_path / "obr7.json"
    monkeypatch.setattr(launcher, "DAEMON_STATE_PATH", state_path)
    state_path.write_text(json.dumps({"pid": 2**22, "ready": True}), encoding="utf-8")

    rc = launcher.daemon_status()
    out = capsys.readouterr().out
    assert rc == 1
    assert "未运行" in out
    assert not state_path.exists()


def test_daemon_spawn_refuses_duplicate(tmp_path, monkeypatch, capsys):
    launcher = load_launcher_module()
    state_path = tmp_path / "obr7.json"
    monkeypatch.setattr(launcher, "DAEMON_STATE_PATH", state_path)
    state_path.write_text(json.dumps({
        "pid": os.getpid(), "ready": True, "api_port": 8765, "web_port": 5174,
    }), encoding="utf-8")

    popen_calls = []

    class DummyPopen:
        def __init__(self, *a, **k):
            popen_calls.append((a, k))

    monkeypatch.setattr(launcher.subprocess, "Popen", DummyPopen)
    args = launcher.parse_args(["--daemon"])
    rc = launcher.daemon_spawn(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "已在运行" in out
    assert popen_calls == []  # 重复 --daemon 没有再拉起进程


def test_daemon_stop_when_not_running(tmp_path, monkeypatch, capsys):
    launcher = load_launcher_module()
    state_path = tmp_path / "obr7.json"
    monkeypatch.setattr(launcher, "DAEMON_STATE_PATH", state_path)

    rc = launcher.daemon_stop()
    out = capsys.readouterr().out
    assert rc == 0
    assert "未在运行" in out
