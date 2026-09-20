import importlib.util
from pathlib import Path
from urllib.error import HTTPError


def test_backend_spec_includes_tiktoken_encoding_plugins():
    """Frozen sidecar must retain tiktoken's plugin-based encoding registry."""
    spec = Path(__file__).resolve().parents[1] / "openbrep-backend.spec"
    text = spec.read_text(encoding="utf-8")
    for module in ("tiktoken", "tiktoken_ext", "tiktoken_ext.openai_public"):
        assert f'"{module}"' in text


def _load_package_smoke():
    path = Path(__file__).resolve().parents[1] / "scripts" / "package_smoke.py"
    spec = importlib.util.spec_from_file_location("_package_smoke_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_desktop_lifecycle_contract_requires_single_instance_before_setup():
    smoke = _load_package_smoke()
    repo_root = Path(__file__).resolve().parents[1]

    smoke.validate_desktop_lifecycle_contract(repo_root)


def test_desktop_lifecycle_contract_rejects_plugin_after_setup(tmp_path):
    smoke = _load_package_smoke()
    tauri_dir = tmp_path / "src-tauri"
    source_dir = tauri_dir / "src"
    source_dir.mkdir(parents=True)
    (tauri_dir / "Cargo.toml").write_text(
        '[dependencies]\ntauri-plugin-single-instance = "2"\n',
        encoding="utf-8",
    )
    (source_dir / "main.rs").write_text(
        ".setup(|app| {})\n"
        ".plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {}))\n"
        "fn shutdown_backend() {}\n"
        "WindowEvent::Destroyed\n",
        encoding="utf-8",
    )

    try:
        smoke.validate_desktop_lifecycle_contract(tmp_path)
    except RuntimeError as exc:
        assert "before .setup" in str(exc)
    else:
        raise AssertionError("plugin ordering violation was accepted")


class _Response:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_package_smoke_homepage_accepts_real_page(monkeypatch):
    smoke = _load_package_smoke()

    monkeypatch.setattr(
        smoke.urllib.request,
        "urlopen",
        lambda _url, timeout: _Response(200, b"<html><body>OpenBrep</body></html>"),
    )

    assert smoke._wait_for_homepage(8501, 0.01)


def test_package_smoke_homepage_rejects_not_found(monkeypatch):
    smoke = _load_package_smoke()

    def _not_found(url, timeout):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(smoke.urllib.request, "urlopen", _not_found)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    assert not smoke._wait_for_homepage(8501, 0.01)


def test_clean_package_env_strips_openai_codex_and_sets_fresh_home(monkeypatch):
    """D7：package smoke 默认干净 HOME + 清空 OpenAI/Codex env（含未知前缀变量）。"""
    smoke = _load_package_smoke()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dev-secret-1234567890")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "dev-canary-token-123456")
    monkeypatch.setenv("CODEX_HOME", "/home/dev/.codex")
    monkeypatch.setenv("GDL_AGENT_CONFIG", "/home/dev/config.toml")
    monkeypatch.setenv("OPENAI_FUTURE_TOKEN", "/developer/cache/SECRET")
    monkeypatch.setenv("CODEX_FUTURE_TOKEN", "/developer/cache/SECRET")
    monkeypatch.setenv("KEEP_ME", "still-here")

    env, tmp_home = smoke.clean_package_env()
    try:
        assert "OPENAI_API_KEY" not in env
        assert "CODEX_ACCESS_TOKEN" not in env
        assert "CODEX_HOME" not in env
        assert "GDL_AGENT_CONFIG" not in env
        # 前缀剥离：未来新增的 OpenAI_*/CODEX_* 变量也不会继承
        assert "OPENAI_FUTURE_TOKEN" not in env
        assert "CODEX_FUTURE_TOKEN" not in env
        assert env["KEEP_ME"] == "still-here"
        assert env["HOME"] == str(tmp_home)
        if smoke.os.name == "nt":
            assert env["USERPROFILE"] == str(tmp_home)
        assert tmp_home.is_dir()
    finally:
        import shutil as _shutil

        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_clean_package_env_redirects_xdg_and_windows_appdata(monkeypatch):
    """P1：XDG/APPDATA 指向开发机 cache 时，必须重定向到临时 HOME 下。"""
    smoke = _load_package_smoke()
    monkeypatch.setenv("XDG_CONFIG_HOME", "/developer/cache/SECRET")
    monkeypatch.setenv("XDG_DATA_HOME", "/developer/cache/SECRET")
    monkeypatch.setenv("XDG_CACHE_HOME", "/developer/cache/SECRET")
    monkeypatch.setenv("APPDATA", "/developer/cache/SECRET")
    monkeypatch.setenv("LOCALAPPDATA", "/developer/cache/SECRET")

    env, tmp_home = smoke.clean_package_env()
    try:
        assert env["XDG_CONFIG_HOME"] == str(tmp_home / ".config")
        assert env["XDG_DATA_HOME"] == str(tmp_home / ".local" / "share")
        assert env["XDG_CACHE_HOME"] == str(tmp_home / ".cache")
        assert "/developer/cache/SECRET" not in "\n".join(env.values())
        if smoke.os.name == "nt":
            assert env["APPDATA"] == str(tmp_home / "AppData" / "Roaming")
            assert env["LOCALAPPDATA"] == str(tmp_home / "AppData" / "Local")
        else:
            assert "APPDATA" not in env
            assert "LOCALAPPDATA" not in env
    finally:
        import shutil as _shutil

        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_clean_package_env_never_leaks_developer_codex_home(monkeypatch):
    """D7：隔离 HOME 必须是全新目录，绝不指向开发机 ~/.codex 或 ~/.openbrep。"""
    smoke = _load_package_smoke()
    monkeypatch.setenv("HOME", "/home/developer")
    monkeypatch.setenv("CODEX_HOME", "/home/developer/.codex")

    env, tmp_home = smoke.clean_package_env()
    try:
        assert str(tmp_home).startswith(smoke.tempfile.gettempdir())
        assert tmp_home != Path("/home/developer/.codex")
        assert env["HOME"] != "/home/developer"
    finally:
        import shutil as _shutil

        _shutil.rmtree(tmp_home, ignore_errors=True)
