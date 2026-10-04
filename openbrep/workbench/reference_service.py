"""P1-A ReferenceAsset 引用链（2026-10-04 漏窗诊断 R3/R6）。

"搜索—整理—选图—执行"闭环的会话侧资产层：

- adopt：把用户显式采用的图片 URL 取回为会话参考资产（字节 hash + 状态 +
  来源），是"看过图"的唯一入口——仅 URL 文本存在不算看过图。
- selected_assets：执行轮注入的允许列表来源（conversation_service 把已采用
  资产按 b64 注入执行请求 + effect_contract.reference_asset_ids），模型不能
  自取任意地址。
- 资产绑定 project_epoch：换项目后旧参考不再进入执行注入，防串图。
- 字节持久化到会话资产区（workspace/项目的 .openbrep/assets/references/，
  best-effort），刷新后可恢复；consult 永不写 HSF 源目录。

外部取图约束（R3 第 5 条）：仅 http/https、Content-Type 必须 image/*、
大小上限、超时、拒绝内网/环回地址（SSRF 约束）——参考图不是通用文件读取。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib import request as _urlrequest
from urllib.error import URLError
from urllib.parse import urlparse

REFERENCE_MAX_BYTES = 10 * 1024 * 1024
REFERENCE_FETCH_TIMEOUT = 15.0
MAX_SESSION_ASSETS = 64
_ALLOWED_SCHEMES = {"http", "https"}


class ReferenceFetchError(Exception):
    """取图失败（协议/网络/内容类型/大小）；文案可直接透出给用户。"""


@dataclass
class ReferenceAsset:
    id: str
    url: str
    alt: str
    source_url: str
    mime: str
    sha256: str
    size: int
    selected: bool
    region: str
    project_epoch: int
    created_at: float
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "alt": self.alt,
            "source_url": self.source_url,
            "mime": self.mime,
            "sha256": self.sha256,
            "size": self.size,
            "selected": self.selected,
            "region": self.region,
            "project_epoch": self.project_epoch,
            "error": self.error,
        }


def assert_public_http_url(url: str, *, allow_private: bool = False) -> str:
    """校验参考图 URL：协议白名单 + 主机解析后拒绝内网/环回地址。返回规范化 URL。"""
    parsed = urlparse(url or "")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ReferenceFetchError(f"不支持的图片地址协议：{parsed.scheme or '空'}（仅允许 http/https）")
    if not parsed.hostname:
        raise ReferenceFetchError("图片地址缺少主机名")
    if allow_private:
        return url
    import socket

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ReferenceFetchError(f"图片主机无法解析：{parsed.hostname}") from exc
    for info in infos:
        ip = info[4][0]
        if ip == "::1" or ip.startswith(("127.", "10.", "192.168.", "169.254.", "fc", "fd", "fe80")):
            raise ReferenceFetchError("图片地址指向内网/本机，已拒绝获取")
        if ip.startswith("172."):
            try:
                second = int(ip.split(".")[1])
            except (ValueError, IndexError):
                continue
            if 16 <= second <= 31:
                raise ReferenceFetchError("图片地址指向内网/本机，已拒绝获取")
    return url


def _download_image(url: str) -> tuple[bytes, str]:
    """下载图片字节；Content-Type 必须 image/*，大小受限。"""
    req = _urlrequest.Request(url, headers={"User-Agent": "OpenBrep-ReferenceFetcher/1.0"}, method="GET")
    try:
        with _urlrequest.urlopen(req, timeout=REFERENCE_FETCH_TIMEOUT) as resp:
            mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                raise ReferenceFetchError(f"地址返回的不是图片（Content-Type: {mime or '未知'}）")
            data = resp.read(REFERENCE_MAX_BYTES + 1)
    except ReferenceFetchError:
        raise
    except (URLError, OSError, TimeoutError) as exc:
        raise ReferenceFetchError(f"图片获取失败：{exc}") from exc
    if len(data) > REFERENCE_MAX_BYTES:
        raise ReferenceFetchError(f"图片超过大小上限（{REFERENCE_MAX_BYTES // (1024 * 1024)}MB）")
    if not data:
        raise ReferenceFetchError("图片内容为空")
    return data, mime or "image/png"


class WorkbenchReferenceService:
    """会话内参考图资产；唯一写入入口是显式 adopt。"""

    def __init__(self, session: Any, *, allow_private: bool = False, clock=time.time) -> None:
        self.session = session
        self._allow_private = allow_private  # 测试注入（本地替身服务器）
        self._clock = clock
        self._assets: OrderedDict[str, ReferenceAsset] = OrderedDict()
        # 会话内字节缓存（磁盘只是持久副本）：无 workspace/项目的纯会话模式
        # 也能完成 adopt → 执行注入闭环；上限与会话资产数一致。
        self._bytes_cache: OrderedDict[str, bytes] = OrderedDict()
        self._restore()

    # ── 存储 ────────────────────────────────────────────────

    def _assets_root(self) -> Path | None:
        workspace = getattr(self.session, "workspace_path", None)
        project = getattr(self.session, "source_path", None)
        root = workspace or project
        if root is None:
            return None
        return Path(root) / ".openbrep" / "assets" / "references"

    def _persist_meta(self) -> None:
        """best-effort 元数据持久化；失败只影响刷新恢复，不影响本轮功能。"""
        root = self._assets_root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            meta = [asset.to_dict() for asset in self._assets.values()]
            fd, tmp_name = tempfile.mkstemp(dir=str(root), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False)
            os.replace(tmp_name, str(root / "meta.json"))
        except OSError:
            pass

    def _restore(self) -> None:
        root = self._assets_root()
        if root is None:
            return
        meta_path = root / "meta.json"
        try:
            entries = json.loads(meta_path.read_text(encoding="utf-8"))
            for entry in entries:
                asset = ReferenceAsset(
                    id=entry["id"],
                    url=entry["url"],
                    alt=entry.get("alt") or "",
                    source_url=entry.get("source_url") or "",
                    mime=entry["mime"],
                    sha256=entry["sha256"],
                    size=int(entry.get("size") or 0),
                    selected=bool(entry.get("selected")),
                    region=entry.get("region") or "",
                    project_epoch=int(entry.get("project_epoch") or 0),
                    created_at=float(entry.get("created_at") or 0),
                )
                self._assets[asset.id] = asset
        except (OSError, ValueError, KeyError, TypeError):
            pass

    # ── 动作 ────────────────────────────────────────────────

    def adopt(self, body: dict[str, Any]) -> dict[str, Any]:
        """取回并采用一张参考图（幂等：同字节 hash 直接复用资产）。"""
        url = str(body.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "缺少图片地址"}
        alt = str(body.get("alt") or "")[:200]
        source_url = str(body.get("source_url") or "")[:2000]
        region = str(body.get("region") or "")[:200]
        try:
            assert_public_http_url(url, allow_private=self._allow_private)
            data, mime = _download_image(url)
        except ReferenceFetchError as exc:
            return {"ok": False, "error": str(exc)}
        sha256 = hashlib.sha256(data).hexdigest()
        asset_id = f"ref_{sha256[:12]}"
        existing = self._assets.get(asset_id)
        if existing is not None:
            existing.selected = True
            existing.region = region or existing.region
            self._persist_meta()
            return {"ok": True, "asset": existing.to_dict()}
        asset = ReferenceAsset(
            id=asset_id,
            url=url,
            alt=alt,
            source_url=source_url,
            mime=mime,
            sha256=sha256,
            size=len(data),
            selected=True,
            region=region,
            project_epoch=int(getattr(self.session, "project_epoch", 0)),
            created_at=self._clock(),
        )
        self._bytes_cache[asset_id] = data
        while len(self._bytes_cache) > MAX_SESSION_ASSETS:
            self._bytes_cache.popitem(last=False)
        self._write_bytes(asset_id, data)
        self._assets[asset_id] = asset
        while len(self._assets) > MAX_SESSION_ASSETS:
            self._assets.popitem(last=False)
        self._persist_meta()
        return {"ok": True, "asset": asset.to_dict()}

    def _write_bytes(self, asset_id: str, data: bytes) -> None:
        root = self._assets_root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{asset_id}.bin").write_bytes(data)
        except OSError:
            pass

    def set_selection(self, body: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(body.get("id") or "")
        asset = self._assets.get(asset_id)
        if asset is None:
            return {"ok": False, "error": "参考资产不存在"}
        asset.selected = bool(body.get("selected"))
        if body.get("region") is not None:
            asset.region = str(body.get("region"))[:200]
        self._persist_meta()
        return {"ok": True, "asset": asset.to_dict()}

    def list_assets(self, *, selected_only: bool = False) -> list[dict[str, Any]]:
        return [
            asset.to_dict()
            for asset in self._assets.values()
            if (not selected_only or asset.selected)
        ]

    def asset_bytes(self, asset_id: str) -> tuple[bytes, str] | None:
        """资产字节（前端 <img> 代理读取）；内存无字节时回读磁盘缓存。"""
        asset = self._assets.get(asset_id)
        if asset is None:
            return None
        cached = self._bytes_cache.get(asset_id)
        if cached is not None:
            return cached, asset.mime
        root = self._assets_root()
        if root is not None:
            path = root / f"{asset_id}.bin"
            if path.is_file():
                data = path.read_bytes()
                self._bytes_cache[asset_id] = data
                return data, asset.mime
        return None

    def selected_assets(self) -> list[ReferenceAsset]:
        """执行注入的允许列表：显式采用 + 绑定当前项目 epoch。"""
        epoch = int(getattr(self.session, "project_epoch", 0))
        return [asset for asset in self._assets.values() if asset.selected and asset.project_epoch == epoch]

    def clear(self) -> None:
        self._assets.clear()
        self._bytes_cache.clear()
        self._persist_meta()

    # ── 执行注入（conversation_service 消费） ────────────────

    def execution_images(self) -> list[dict[str, str]]:
        """已采用资产 → 执行请求 images（b64）；读取失败静默跳过该资产。"""
        images: list[dict[str, str]] = []
        for asset in self.selected_assets():
            stored = self.asset_bytes(asset.id)
            if stored is None:
                continue
            data, mime = stored
            images.append({"b64": base64.b64encode(data).decode("ascii"), "mime": mime, "name": asset.id})
        return images
