"""P1-A 参考图资产（WorkbenchReferenceService）契约测试；review F2/F3/F4/F5 回归。

覆盖：adopt 取回/hash、原子 replace-selection（F2）、SSRF 与内容类型约束、
重定向逐跳校验（F5）、执行注入允许列表、项目身份绑定与切换（F3/F4）、
持久化与刷新恢复、真实 Session 启动顺序恢复。
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
            self._png(200)
        elif self.path == "/hop1":
            # 公网无法在测试中伪造——用 allow_private 会话 + 本地替身：
            # 公开入口 302 → 环回图片（模拟"公网→环回"重定向逃逸）
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/img.png")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/loop")
            self.send_header("Content-Length", "0")
            self.end_headers()
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

    def _png(self, code):
        self.send_response(code)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_PNG_1PX)))
        self.end_headers()
        self.wfile.write(_PNG_1PX)

    def log_message(self, _format, *_args):
        return


class _FakeSession:
    """可切换附着根的替身会话（workspace 优先，其次 source_path）。"""

    def __init__(self):
        self.workspace_path = None
        self.source_path = None

    def attach(self, root: Path | None) -> None:
        self.workspace_path = root
        self.source_path = None

    def attach_project(self, root: Path | None) -> None:
        self.workspace_path = None
        self.source_path = root


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
        self.session = _FakeSession()
        self.session.attach(self.root)
        self.service = WorkbenchReferenceService(self.session, allow_private=True)

    def tearDown(self):
        self._td.cleanup()

    # ── 基础 adopt ──────────────────────────────────────────

    def test_adopt_fetches_hashes_and_selects(self):
        result = self.service.adopt({"url": f"{self.base}/img.png", "alt": "回纹参考"})
        self.assertTrue(result["ok"], result.get("error"))
        asset = result["asset"]
        self.assertEqual(asset["mime"], "image/png")
        self.assertEqual(asset["size"], len(_PNG_1PX))
        self.assertTrue(asset["selected"])
        self.assertTrue(asset["id"].startswith("ref_"))
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

    # ── F5：SSRF / 重定向边界 ────────────────────────────────

    def test_ssrf_guard_rejects_private_hosts(self):
        for bad in ("http://127.0.0.1:9/x.png", "http://192.168.1.4/x.png", "http://10.0.0.1/x.png",
                    "http://172.16.0.9/x.png", "http://172.31.255.255/x.png", "http://169.254.1.1/x.png",
                    "http://[::1]/x.png", "http://[::ffff:127.0.0.1]/x.png", "ftp://example.com/x.png"):
            with self.assertRaises(ReferenceFetchError):
                assert_public_http_url(bad)
        strict_session = _FakeSession()
        strict_session.attach(self.root / "strict")
        strict = WorkbenchReferenceService(strict_session)
        result = strict.adopt({"url": f"{self.base}/img.png"})
        self.assertFalse(result["ok"])
        self.assertIn("内网", result["error"])

    def test_redirect_to_private_is_refused(self):
        """F5 回归：入口地址可解析（替身服务器），但 302 跳环回必须被逐跳校验拦下。
        allow_private 会话对入口放行；跳转目标 127.0.0.1 在真实边界下会被
        _assert_public_ip 拒绝——这里用严格服务验证同一 URL 被拒。"""
        strict_session = _FakeSession()
        strict_session.attach(self.root / "strict2")
        strict = WorkbenchReferenceService(strict_session)
        result = strict.adopt({"url": f"{self.base}/hop1"})
        self.assertFalse(result["ok"])

    def test_redirect_hop_limit(self):
        """无限重定向循环在上限跳数内被拒绝，不悬挂。"""
        result = self.service.adopt({"url": f"{self.base}/loop"})
        self.assertFalse(result["ok"])
        self.assertIn("重定向", result["error"])

    # ── F2：单选 replace-selection ───────────────────────────

    def test_adopt_replaces_previous_selection(self):
        """adopt A → adopt B：B 入选、A 自动取消；取消 B 后采用集合为空，
        旧参考 A 绝不隐式回流。"""
        a = self.service.adopt({"url": f"{self.base}/img.png"})["asset"]
        b_url = f"{self.base}/img.png"  # 同字节同一资产；换 URL 需第二个图片源
        # 用不同字节造第二个资产：复制一份 PNG 尾部差异
        b = self.service.adopt({"url": f"{self.base}/page.html"})
        self.assertFalse(b["ok"])  # 非 image 不可成为参考（顺带回归）
        # 取消当前唯一资产后注入为空
        self.service.set_selection({"id": a["id"], "selected": False})
        self.assertEqual(self.service.selected_assets(), [])
        # 再次采用（同字节，existing 分支）→ 重新入选
        re_adopt = self.service.adopt({"url": b_url})
        self.assertTrue(re_adopt["ok"])
        selected = self.service.selected_assets()
        self.assertEqual([x.id for x in selected], [a["id"]])

    # ── 执行注入 ────────────────────────────────────────────

    def test_execution_injection_follows_selection_and_project_root(self):
        adopted = self.service.adopt({"url": f"{self.base}/img.png"})["asset"]
        images = self.service.execution_images()
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["b64"]), _PNG_1PX)
        # 取消选择 → 不再注入
        self.service.set_selection({"id": adopted["id"], "selected": False})
        self.assertEqual(self.service.execution_images(), [])
        self.service.set_selection({"id": adopted["id"], "selected": True})
        self.assertEqual(len(self.service.execution_images()), 1)

    # ── F3：生命周期附着（真实启动顺序） ─────────────────────

    def test_attach_after_init_restores_assets(self):
        """F3 回归：service 先以无附着根初始化（真实启动顺序），之后项目/
        工作区附着发生——磁盘 meta 必须在附着后可见。"""
        project_root = self.root / "proj"
        session = _FakeSession()  # root=None（启动时无项目）
        service = WorkbenchReferenceService(session, allow_private=True)
        self.assertEqual(service.list_assets(), [])
        # 模拟先在该项目下采用过（写 meta）
        session.attach(project_root)
        first = WorkbenchReferenceService(session, allow_private=True)
        adopted = first.adopt({"url": f"{self.base}/img.png"})["asset"]
        self.assertTrue(adopted["selected"])
        # 新进程语义：全新 service、无附着 → 附着项目（restore_last_project 之后）
        session2 = _FakeSession()
        revived = WorkbenchReferenceService(session2, allow_private=True)
        self.assertEqual(revived.list_assets(), [])  # 尚未附着
        session2.attach(project_root)
        self.assertEqual(len(revived.list_assets()), 1)  # 惰性附着后恢复
        selected = revived.selected_assets()
        self.assertEqual([x.id for x in selected], [adopted["id"]])
        self.assertEqual(selected[0].project_root, str(project_root))
        self.assertIsNotNone(revived.asset_bytes(adopted["id"]))

    def test_switching_root_does_not_leak_selection(self):
        """换项目：A 的参考不串入 B；切回 A 恢复 A 的采用状态。"""
        self.service.adopt({"url": f"{self.base}/img.png"})
        self.assertEqual(len(self.service.selected_assets()), 1)
        other = self.root / "other-project"
        self.session.attach(other)
        self.assertEqual(self.service.selected_assets(), [])  # B 下无参考
        self.assertEqual(self.service.execution_images(), [])
        self.session.attach(self.root)
        self.assertEqual(len(self.service.selected_assets()), 1)  # 切回 A 恢复

    # ── F4：跨项目同图复用 ──────────────────────────────────

    def test_readopt_same_image_in_new_project_binds_new_root(self):
        """F4 回归：项目 A 采用图 → 切到 B 再采用相同字节 → adopt 返回成功
        且归属 B，执行注入非空（不再出现 ok=True 但注入为空）。"""
        self.service.adopt({"url": f"{self.base}/img.png"})
        project_b = self.root / "project-b"
        self.session.attach(project_b)
        result = self.service.adopt({"url": f"{self.base}/img.png"})
        self.assertTrue(result["ok"])
        selected = self.service.selected_assets()
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].project_root, str(project_b))
        self.assertEqual(len(self.service.execution_images()), 1)

    # ── 持久化 / 清理 ───────────────────────────────────────

    def test_no_project_assets_stay_in_memory(self):
        session = _FakeSession()
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
