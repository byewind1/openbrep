"""
multi_image — 多图摄取通道（Vision Harness S0，P5a）

职责（仅当 TaskRequest.images 非空时生效的新路径）：
1. 路径来源 → 读取字节（webview 无 fs 权限，读取必须发生在 Python 侧）；
   path 读取后置 None（防泄露进 prompt）。
2. 统一预处理：长边 > MAX_IMAGE_LONG_EDGE 的等比缩放到上限（Pillow）。
   预处理后的字节供提取 / critic / 生成三处共用同一份。

零回归门禁：本模块只在 images 非空时被 pipeline 调用；单图旧路径
（image_b64 / image_path）不 import 本模块、不 import PIL、不做任何预处理。
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openbrep.runtime.pipeline import ImageRef

logger = logging.getLogger(__name__)

# 长边上限：vision 模型通用甜点（设计文档 §10-D6）
MAX_IMAGE_LONG_EDGE = 1568

_IMAGE_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def resolve_image_path(path: str) -> Optional[str]:
    """path 存在且可读 → 返回 base64 字节；否则返回 None（校验阶段已拦截，这里兜底）。"""
    p = Path(path).expanduser()
    try:
        raw = p.read_bytes()
    except (OSError, ValueError):
        logger.warning("multi_image: cannot read image path %s", path)
        return None
    return base64.b64encode(raw).decode()


def mime_from_path(path: str) -> str:
    return _IMAGE_SUFFIX_MIME.get(Path(path).suffix.lower(), "image/png")


def sha256_of_b64(image_b64: str) -> str:
    """预处理字节的 sha256（设计 D7：内容哈希寻址键；P5b 只算不存）。"""
    try:
        return hashlib.sha256(base64.b64decode(image_b64)).hexdigest()
    except Exception:
        return ""


def preprocess_image_bytes(image_b64: str, image_mime: str) -> tuple[str, str]:
    """
    预处理单张图片：长边 > MAX_IMAGE_LONG_EDGE 时等比缩放到上限，其余不变。

    Args:
        image_b64:  base64 图像字节
        image_mime: MIME（image/png / image/jpeg / image/webp）

    Returns:
        (预处理后的 b64, mime)。Pillow 不可用或解码/缩放失败时原样返回，
        降级不抛异常（设计文档 §10-D8：预处理失败不得阻塞流程）。
    """
    try:
        from PIL import Image  # 仅多图通道 import PIL（旧路径零新依赖面）
    except ImportError:  # pragma: no cover - 环境无 Pillow 时降级原字节
        logger.warning("multi_image: Pillow not installed, skipping preprocessing")
        return image_b64, image_mime

    try:
        raw = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        logger.warning("multi_image: image decode failed (%s), passing through", exc)
        return image_b64, image_mime

    width, height = img.size
    long_edge = max(width, height)
    if long_edge <= MAX_IMAGE_LONG_EDGE:
        # 长边未超限：原字节直通（不重编码，字节保持不变）
        return image_b64, image_mime

    scale = MAX_IMAGE_LONG_EDGE / long_edge
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    img = img.resize(new_size, Image.LANCZOS)

    fmt = img.format or "JPEG"
    buffer = io.BytesIO()
    try:
        img.save(buffer, format=fmt)
    except Exception as exc:
        logger.warning("multi_image: re-encode failed (%s), passing through", exc)
        return image_b64, image_mime
    return base64.b64encode(buffer.getvalue()).decode(), image_mime


def resolve_and_preprocess(images: list["ImageRef"]) -> list["ImageRef"]:
    """
    多图通道入口：逐张把 path 来源读成字节（path 置 None），再统一预处理。

    任一图读取/预处理失败都降级为原字节继续（D8：该图不阻塞其余图），
    读取失败（路径来源）保留空 b64 —— 生成端会跳过无字节的图。
    """
    resolved: list[ImageRef] = []
    for img in images:
        b64 = img.b64
        mime = img.mime or "image/png"
        if not b64 and img.path:
            b64 = resolve_image_path(img.path) or ""
            mime = img.mime or mime_from_path(img.path)
        if b64:
            b64, mime = preprocess_image_bytes(b64, mime)
        resolved.append(img.__class__(
            token=img.token,
            path=None,  # 后端读取后置 None，防泄露进 prompt
            b64=b64,
            mime=mime,
            # P5b：role 透传（S1 推导前为 auto），sha256 取预处理后字节的哈希
            role=getattr(img, "role", "auto") or "auto",
            sha256=sha256_of_b64(b64) if b64 else "",
        ))
    return resolved
