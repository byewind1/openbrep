"""P1-A ReferenceAsset 引用链（2026-10-04 漏窗诊断 R3/R6；review F2/F3/F4/F5 修订）。

"搜索—整理—选图—执行"闭环的会话侧资产层：

- adopt：把用户显式采用的图片 URL 取回为会话参考资产（字节 hash + 状态 +
  来源），是"看过图"的唯一入口——仅 URL 文本存在不算看过图。
- **单选模型（F2）**：adopt 是原子 replace-selection——新图入选，其余全部
  取消；"本轮参考"只有一个，UI 与后端状态一致，旧参考绝不隐式参与执行。
- **项目身份绑定（F3/F4）**：资产归属以稳定项目根目录（workspace 优先，
  否则项目目录）标识；资产 store 惰性附着当前生命周期（附着根变化 → 清表
  重载该根的 meta），正常重启/打开项目/切换工作区后都能恢复；换项目再采用
  同一张图会更新归属（内容去重不等于采用关系）。
- selected_assets：执行注入的允许列表来源（conversation_service 把已采用
  资产按 b64 注入执行请求 + effect_contract.reference_asset_ids），模型不能
  自取任意地址。
- 字节持久化到会话资产区（workspace/项目的 .openbrep/assets/references/，
  best-effort）；consult 永不写 HSF 源目录。

外部取图约束（R3 第 5 条 + F5）：仅 http/https、Content-Type 必须 image/*、
大小上限、超时、**不自动跟随重定向**（逐跳做同样的协议/地址校验，最多 3 跳）、
用 ipaddress 判定拒绝非公网地址（含 IPv4-mapped IPv6）。已知局限：
getaddrinfo 校验与实际连接的 DNS 解析之间理论上存在 TOCTOU 窗口（urllib
无法 pin 连接 IP），按深度防御处理而非宣称绝对封闭。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

REFERENCE_MAX_BYTES = 10 * 1024 * 1024
REFERENCE_FETCH_TIMEOUT = 15.0
REFERENCE_MAX_REDIRECTS = 3
MAX_SESSION_ASSETS = 64
_ALLOWED_SCHEMES = {"http", "https"}


class ReferenceFetchError(Exception):
    """取图失败（协议/网络/内容类型/大小/内网）；文案可直接透出给用户。"""


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
    project_root: str  # 稳定项目身份（workspace/项目根目录）；"" = 无附着会话
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
            "project_root": self.project_root,
            "error": self.error,
        }


def _assert_public_ip(ip_text: str) -> None:
    """F5：用 ipaddress 分类判定；非公网地址（环回/私网/链路本地/未指定/
    组播/保留，含 IPv4-mapped IPv6）一律拒绝。"""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError as exc:
        raise ReferenceFetchError(f"图片主机解析到非法地址：{ip_text}") from exc
    if not ip.is_global:
        raise ReferenceFetchError("图片地址指向内网/本机，已拒绝获取")


def assert_public_http_url(url: str, *, allow_private: bool = False) -> str:
    """校验参考图 URL：协议白名单 + 主机全部解析地址必须公网。返回规范化 URL。"""
    parsed = urlparse(url or "")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ReferenceFetchError(f"不支持的图片地址协议：{parsed.scheme or '空'}（仅允许 http/https）")
    if not parsed.hostname:
        raise ReferenceFetchError("图片地址缺少主机名")
    if allow_private:
        return url
    try:
        infos = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ReferenceFetchError(f"图片主机无法解析：{parsed.hostname}") from exc
    if not infos:
        raise ReferenceFetchError(f"图片主机无法解析：{parsed.hostname}")
    for info in infos:
        _assert_public_ip(info[4][0])
    return url


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """F5：禁止 urllib 自动跟随重定向——每跳都必须重新过协议/内网校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _download_image(url: str, *, allow_private: bool = False) -> tuple[bytes, str]:
    """下载图片字节；Content-Type 必须 image/*，大小受限，重定向逐跳校验。

    禁自动重定向后，3xx 会以 HTTPError 形态出现（主响应或 URLError.reason），
    统一在此解析 Location 并手动跳转（每跳重新走 assert_public_http_url）。
    """
    current = url
    for hop in range(REFERENCE_MAX_REDIRECTS + 1):
        assert_public_http_url(current, allow_private=allow_private)
        req = urllib.request.Request(
            current, headers={"User-Agent": "OpenBrep-ReferenceFetcher/1.0"}, method="GET",
        )
        try:
            with _OPENER.open(req, timeout=REFERENCE_FETCH_TIMEOUT) as resp:
                mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not mime.startswith("image/"):
                    raise ReferenceFetchError(f"地址返回的不是图片（Content-Type: {mime or '未知'}）")
                data = resp.read(REFERENCE_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            next_url = _redirect_target(current, exc.code, exc.headers)
            if next_url is not None:
                if hop == REFERENCE_MAX_REDIRECTS:
                    raise ReferenceFetchError(
                        f"图片重定向次数超过上限（{REFERENCE_MAX_REDIRECTS}）"
                    ) from exc
                current = next_url
                continue
            raise ReferenceFetchError(f"图片获取失败：HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            # 禁用自动重定向后 _NoRedirectHandler 抛出的 3xx 会有两种封装：
            # HTTPError（URLError 子类，带 headers）或裸 URLError（无 Location
            # 可取）——后者直接按失败处理。
            if isinstance(exc, urllib.error.HTTPError):
                next_url = _redirect_target(current, exc.code, exc.headers)
                if next_url is not None:
                    if hop == REFERENCE_MAX_REDIRECTS:
                        raise ReferenceFetchError(
                            f"图片重定向次数超过上限（{REFERENCE_MAX_REDIRECTS}）"
                        ) from exc
                    current = next_url
                    continue
            raise ReferenceFetchError(f"图片获取失败：{exc}") from exc
        if len(data) > REFERENCE_MAX_BYTES:
            raise ReferenceFetchError(f"图片超过大小上限（{REFERENCE_MAX_BYTES // (1024 * 1024)}MB）")
        if not data:
            raise ReferenceFetchError("图片内容为空")
        return data, mime or "image/png"
    raise ReferenceFetchError(f"图片重定向次数超过上限（{REFERENCE_MAX_REDIRECTS}）")


def _redirect_target(current: str, code: int, headers: Any) -> str | None:
    """3xx 响应的跳转目标；非重定向或缺失 Location 返回 None。"""
    if not (300 <= code < 400) or code in (300, 304):
        return None
    location = headers.get("Location") if headers is not None else None
    if not location:
        return None
    return urljoin(current, location)


class WorkbenchReferenceService:
    """会话内参考图资产；唯一写入入口是显式 adopt（单选 replace 语义）。"""

    def __init__(self, session: Any, *, allow_private: bool = False, clock=time.time) -> None:
        self.session = session
        self._allow_private = allow_private  # 测试注入（本地替身服务器）
        self._clock = clock
        self._assets: OrderedDict[str, ReferenceAsset] = OrderedDict()
        # 会话内字节缓存（磁盘只是持久副本）：无附着根的纯会话模式也能完成
        # adopt → 执行注入闭环；上限与会话资产数一致。
        self._bytes_cache: OrderedDict[str, bytes] = OrderedDict()
        # F3：惰性附着——attached_root 与当前生命周期根不一致时清表重载。
        self._attached_root: Path | None = None
        self._attached = False
        self._ensure_attached()

    # ── 生命周期附着（F3） ───────────────────────────────────

    def _current_root(self) -> Path | None:
        workspace = getattr(self.session, "workspace_path", None)
        if workspace is not None:
            return Path(workspace)
        project = getattr(self.session, "source_path", None)
        return Path(project) if project is not None else None

    def _ensure_attached(self) -> None:
        """把内存资产表与当前项目/工作区根绑定：根变化 → 清表重载该根 meta。

        在每个公开方法入口调用（幂等、廉价：root 未变时为一次比较），
        覆盖启动恢复、打开项目、切换/关闭工作区等所有生命周期变化。
        """
        root = self._current_root()
        if self._attached and self._attached_root == root:
            return
        self._attached = True
        self._attached_root = root
        self._assets.clear()
        self._bytes_cache.clear()
        if root is not None:
            self._restore(root)

    # ── 存储 ────────────────────────────────────────────────

    def _assets_root(self) -> Path | None:
        if self._attached_root is None:
            return None
        return self._attached_root / ".openbrep" / "assets" / "references"

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

    def _restore(self, root: Path) -> None:
        meta_path = root / ".openbrep" / "assets" / "references" / "meta.json"
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
                    # F3：meta 属于该根，恢复即归属当前根（历史 epoch 字段忽略）
                    project_root=str(root),
                    created_at=float(entry.get("created_at") or 0),
                )
                self._assets[asset.id] = asset
        except (OSError, ValueError, KeyError, TypeError):
            pass

    # ── 动作 ────────────────────────────────────────────────

    def adopt(self, body: dict[str, Any]) -> dict[str, Any]:
        """取回并采用一张参考图（原子 replace-selection：旧参考全部取消）。"""
        self._ensure_attached()
        url = str(body.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "缺少图片地址"}
        alt = str(body.get("alt") or "")[:200]
        source_url = str(body.get("source_url") or "")[:2000]
        region = str(body.get("region") or "")[:200]
        try:
            data, mime = _download_image(url, allow_private=self._allow_private)
        except ReferenceFetchError as exc:
            return {"ok": False, "error": str(exc)}
        sha256 = hashlib.sha256(data).hexdigest()
        asset_id = f"ref_{sha256[:12]}"
        current_root = str(self._attached_root) if self._attached_root is not None else ""
        existing = self._assets.get(asset_id)
        if existing is not None:
            # F4：内容去重不等于采用关系——再采用时更新当前项目归属与选中态
            existing.project_root = current_root
            existing.selected = True
            existing.region = region or existing.region
            asset = existing
        else:
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
                project_root=current_root,
                created_at=self._clock(),
            )
            self._bytes_cache[asset_id] = data
            while len(self._bytes_cache) > MAX_SESSION_ASSETS:
                self._bytes_cache.popitem(last=False)
            self._write_bytes(asset_id, data)
            self._assets[asset_id] = asset
            while len(self._assets) > MAX_SESSION_ASSETS:
                self._assets.popitem(last=False)
        # F2：单选语义——本次采用后其余资产全部取消（原子 replace-selection）
        for other in self._assets.values():
            if other.id != asset.id:
                other.selected = False
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
        """显式选择/取消；取消后当前采用集合为空（不会回退到旧参考）。"""
        self._ensure_attached()
        asset_id = str(body.get("id") or "")
        asset = self._assets.get(asset_id)
        if asset is None:
            return {"ok": False, "error": "参考资产不存在"}
        asset.selected = bool(body.get("selected"))
        if body.get("region") is not None:
            asset.region = str(body.get("region"))[:200]
        if asset.selected:
            # F2：保持单选不变式
            for other in self._assets.values():
                if other.id != asset.id:
                    other.selected = False
        self._persist_meta()
        return {"ok": True, "asset": asset.to_dict()}

    def list_assets(self, *, selected_only: bool = False) -> list[dict[str, Any]]:
        self._ensure_attached()
        return [
            asset.to_dict()
            for asset in self._assets.values()
            if (not selected_only or asset.selected)
        ]

    def asset_bytes(self, asset_id: str) -> tuple[bytes, str] | None:
        """资产字节（前端 <img> 代理读取）；内存无字节时回读磁盘缓存。"""
        self._ensure_attached()
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
        """执行注入的允许列表：显式采用 + 归属当前项目根。"""
        self._ensure_attached()
        current_root = str(self._attached_root) if self._attached_root is not None else ""
        return [
            asset for asset in self._assets.values()
            if asset.selected and asset.project_root == current_root
        ]

    def clear(self) -> None:
        self._ensure_attached()
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
