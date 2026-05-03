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
