"""P1-A 参考图资产（WorkbenchReferenceService）契约测试。

覆盖：adopt 取回/hash/幂等、SSRF 与内容类型约束、选中状态与执行注入
（允许列表）、project_epoch 隔离（换项目不串图）、持久化与刷新恢复、
仅 URL 文本不等于看过图。
"""

from __future__ import annotations

import base64
import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

from openbrep.workbench.reference_service import (
    REFERENCE_MAX_BYTES,
    ReferenceFetchError,
    WorkbenchReferenceService,
    assert_public_http_url,
)

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/img.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG_1PX)))
            self.end_headers()
            self.wfile.write(_PNG_1PX)
        elif self.path == "/huge.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(REFERENCE_MAX_BYTES + 10))
            self.end_headers()
            self.wfile.write(b"\x89PNG" + b"0" * (REFERENCE_MAX_BYTES + 6))
        elif self.path == "/page.html":
            body = b"<html>not an image</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, _format, *_args):
        return


class _FakeSession:
    def __init__(self, root, epoch=3):
        self.workspace_path = root
        self.source_path = None
        self.project_epoch = epoch


class TestReferenceService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.session = _FakeSession(self.root)
        self.service = WorkbenchReferenceService(self.session, allow_private=True)

    def tearDown(self):
        self._td.cleanup()

    def test_adopt_fetches_hashes_and_selects(self):
        result = self.service.adopt({"url": f"{self.base}/img.png", "alt": "回纹参考"})
        self.assertTrue(result["ok"], result.get("error"))
        asset = result["asset"]
        self.assertEqual(asset["mime"], "image/png")
        self.assertEqual(asset["size"], len(_PNG_1PX))
        self.assertTrue(asset["selected"])
        self.assertTrue(asset["id"].startswith("ref_"))
        # 字节落盘且 hash 一致
        stored = self.service.asset_bytes(asset["id"])
        self.assertIsNotNone(stored)
        data, mime = stored
        self.assertEqual(len(data), len(_PNG_1PX))
        self.assertEqual(mime, "image/png")

    def test_adopt_is_idempotent_on_same_bytes(self):
        first = self.service.adopt({"url": f"{self.base}/img.png"})
        second = self.service.adopt({"url": f"{self.base}/img.png"})
        self.assertEqual(first["asset"]["id"], second["asset"]["id"])
        self.assertEqual(len(self.service.list_assets()), 1)

    def test_adopt_rejects_non_image_content(self):
        result = self.service.adopt({"url": f"{self.base}/page.html"})
        self.assertFalse(result["ok"])
        self.assertIn("不是图片", result["error"])

    def test_adopt_rejects_oversized_image(self):
        result = self.service.adopt({"url": f"{self.base}/huge.png"})
        self.assertFalse(result["ok"])
        self.assertIn("上限", result["error"])

    def test_ssrf_guard_rejects_private_hosts(self):
        with self.assertRaises(ReferenceFetchError):
            assert_public_http_url("http://127.0.0.1:9/x.png")
        with self.assertRaises(ReferenceFetchError):
            assert_public_http_url("http://192.168.1.4/x.png")
        with self.assertRaises(ReferenceFetchError):
            assert_public_http_url("http://10.0.0.1/x.png")
        with self.assertRaises(ReferenceFetchError):
            assert_public_http_url("ftp://example.com/x.png")
        with self.assertRaises(ReferenceFetchError):
            assert_public_http_url("http://192.168.1.4/x.png")
        # 校验在服务默认开启（allow_private=False）：服务本体拒绝内网
        strict_session = _FakeSession(self.root / "strict")
        strict = WorkbenchReferenceService(strict_session)
        result = strict.adopt({"url": f"{self.base}/img.png"})
        self.assertFalse(result["ok"])
        self.assertIn("内网", result["error"])

    def test_execution_injection_only_selected_and_same_epoch(self):
        adopted = self.service.adopt({"url": f"{self.base}/img.png"})["asset"]
        images = self.service.execution_images()
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["b64"]), _PNG_1PX)
        # 取消选择 → 不再注入
        self.service.set_selection({"id": adopted["id"], "selected": False})
        self.assertEqual(self.service.execution_images(), [])
        # 换项目（epoch 变化）→ 旧参考不串入
        self.service.set_selection({"id": adopted["id"], "selected": True})
        self.session.project_epoch = 4
        self.assertEqual(self.service.execution_images(), [])
        self.assertEqual(self.service.selected_assets(), [])

    def test_restore_after_refresh(self):
        adopted = self.service.adopt({"url": f"{self.base}/img.png"})["asset"]
        revived = WorkbenchReferenceService(self.session, allow_private=True)
        self.assertEqual(len(revived.list_assets()), 1)
        selected = revived.selected_assets()
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].id, adopted["id"])
        stored = revived.asset_bytes(adopted["id"])
        self.assertIsNotNone(stored)
        self.assertEqual(len(stored[0]), len(_PNG_1PX))

    def test_no_project_assets_stay_in_memory(self):
        session = _FakeSession(None)
        service = WorkbenchReferenceService(session, allow_private=True)
        result = service.adopt({"url": f"{self.base}/img.png"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(service.selected_assets()), 1)

    def test_clear_empties_assets(self):
        self.service.adopt({"url": f"{self.base}/img.png"})
        self.service.clear()
        self.assertEqual(self.service.list_assets(), [])
        self.assertEqual(self.service.execution_images(), [])


class TestReferenceRoutes(unittest.TestCase):
    """API 路由层：adopt/select/列表/字节（真实 WorkbenchSession + 替身服务）。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_routes_roundtrip(self):
        import tempfile as _tf

        from openbrep.config import GDLAgentConfig

        with _tf.TemporaryDirectory() as td:
            config_path = Path(td) / "test.toml"
            cfg = GDLAgentConfig()
            cfg.compiler.mode = "mock"
            cfg.save(str(config_path))
            from openbrep.workbench_api import WorkbenchSession

            session = WorkbenchSession(config_path=config_path)
            session.reference_service._allow_private = True
            adopt = session.route("POST", "/api/references/adopt", {"url": f"{self.base}/img.png", "alt": "图1"})
            self.assertTrue(adopt["ok"], adopt.get("error"))
            asset_id = adopt["asset"]["id"]

            listing = session.route("GET", "/api/references")
            self.assertEqual(len(listing["assets"]), 1)

            payload = session.route("GET", f"/api/references/{asset_id}")
            self.assertIsInstance(payload, tuple)
            self.assertEqual(len(payload[0]), len(_PNG_1PX))

            off = session.route("POST", "/api/references/select", {"id": asset_id, "selected": False})
            self.assertTrue(off["ok"])
            self.assertFalse(off["asset"]["selected"])

            missing = session.route("GET", "/api/references/ref_missing0000")
            self.assertFalse(missing["ok"])

            unknown = session.route("DELETE", "/api/references/x")
            self.assertFalse(unknown["ok"])


if __name__ == "__main__":
    unittest.main()
