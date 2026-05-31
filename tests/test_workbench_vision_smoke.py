import base64
import importlib.util
from pathlib import Path


def _load_smoke_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "workbench_vision_smoke.py"
    spec = importlib.util.spec_from_file_location("workbench_vision_smoke", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_workbench_vision_smoke_builds_png_payload():
    smoke = _load_smoke_module()

    payload = smoke.build_test_image_b64()

    assert base64.b64decode(payload).startswith(b"\x89PNG\r\n\x1a\n")


def test_workbench_vision_smoke_skips_without_config(tmp_path):
    smoke = _load_smoke_module()

    result = smoke.run_smoke(config_path=tmp_path / "missing.toml", require_config=True)

    assert result["ok"] is True
    assert result["status"] == "skip"
    assert "config" in result["reason"]


def test_workbench_vision_smoke_posts_image_to_workbench_session(tmp_path):
    smoke = _load_smoke_module()
    calls = []

    class FakeSession:
        def __init__(self, config_path=None):
            self.config_path = config_path

        def route(self, method, path, body=None):
            calls.append((method, path, body or {}))
            return {
                "ok": True,
                "assistant": {"changed_files": ["scripts/3d.gdl"]},
                "project": {"path": str(tmp_path / "ImageShelf")},
            }

    result = smoke.run_smoke(
        output_dir=tmp_path,
        config_path=tmp_path / "config.toml",
        require_config=False,
        session_factory=FakeSession,
    )

    assert result["ok"] is True
    assert result["status"] == "pass"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/project/create"
    assert calls[0][2]["image_mime"] == "image/png"
    assert calls[0][2]["image_b64"]
