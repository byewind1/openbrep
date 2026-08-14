import importlib.util
from pathlib import Path
from urllib.error import HTTPError


def _load_package_smoke():
    path = Path(__file__).resolve().parents[1] / "scripts" / "package_smoke.py"
    spec = importlib.util.spec_from_file_location("_package_smoke_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
    """D7：package smoke 默认干净 HOME + 清空 OpenAI/Codex env。"""
    smoke = _load_package_smoke()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dev-secret-1234567890")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "dev-canary-token-123456")
    monkeypatch.setenv("CODEX_HOME", "/home/dev/.codex")
    monkeypatch.setenv("GDL_AGENT_CONFIG", "/home/dev/config.toml")
    monkeypatch.setenv("KEEP_ME", "still-here")

    env, tmp_home = smoke.clean_package_env()
    try:
        assert "OPENAI_API_KEY" not in env
        assert "CODEX_ACCESS_TOKEN" not in env
        assert "CODEX_HOME" not in env
        assert "GDL_AGENT_CONFIG" not in env
        assert env["KEEP_ME"] == "still-here"
        assert env["HOME"] == str(tmp_home)
        if smoke.os.name == "nt":
            assert env["USERPROFILE"] == str(tmp_home)
        assert tmp_home.is_dir()
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
