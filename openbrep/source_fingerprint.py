"""纯源指纹：HSF 受管源文件集合的确定性 SHA256。

契约（旋转楼梯 ST02 / 总控冻结）：
- 完整源集合复用 revisions 的受管文件清单（顶层 *.xml + scripts/**）；
- 按相对路径排序，带文件边界与字节长度计算 SHA256；
- 保留 BOM，不包含 ``.openbrep`` / 文章 / GSM；
- 形如 ``sha256:<hex>``。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

FINGERPRINT_PREFIX = "sha256:"


def collect_managed_source_files(project_root: str | Path) -> list[str]:
    """返回与 revisions 快照一致的受管相对路径列表（已排序）。"""
    from openbrep.revisions import _collect_source_files

    root = Path(project_root)
    return sorted(_collect_source_files(root))


def _file_entry_hash(rel_path: str, data: bytes) -> bytes:
    """单文件条目：`path\\nlength\\n` + 原始字节（保留 BOM）。"""
    header = f"{rel_path}\n{len(data)}\n".encode("utf-8")
    return header + data


def compute_source_fingerprint(project_root: str | Path) -> str:
    """计算项目当前受管源的指纹；无受管文件时返回空集合指纹。"""
    root = Path(project_root)
    digest = hashlib.sha256()
    files = collect_managed_source_files(root)
    for rel_path in files:
        path = root / rel_path
        data = path.read_bytes() if path.is_file() else b""
        digest.update(_file_entry_hash(rel_path, data))
    return f"{FINGERPRINT_PREFIX}{digest.hexdigest()}"


def compute_files_fingerprint(
    project_root: str | Path,
    files: Iterable[str],
) -> str:
    """只对给定相对路径集合计算指纹（路径排序后）。"""
    root = Path(project_root)
    digest = hashlib.sha256()
    for rel_path in sorted(files):
        path = root / rel_path
        data = path.read_bytes() if path.is_file() else b""
        digest.update(_file_entry_hash(rel_path, data))
    return f"{FINGERPRINT_PREFIX}{digest.hexdigest()}"


def compute_revision_fingerprint(revision_dir: str | Path) -> str:
    """对 revision 快照目录内受管文件计算指纹（不含 manifest.json）。"""
    rev = Path(revision_dir)
    digest = hashlib.sha256()
    rel_paths: list[str] = []
    for path in sorted(rev.glob("*.xml")):
        if path.is_file():
            rel_paths.append(path.name)
    scripts_dir = rev / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*")):
            if path.is_file():
                rel_paths.append(path.relative_to(rev).as_posix())
    for rel_path in sorted(rel_paths):
        data = (rev / rel_path).read_bytes()
        digest.update(_file_entry_hash(rel_path, data))
    return f"{FINGERPRINT_PREFIX}{digest.hexdigest()}"


def fingerprints_equal(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return str(a).strip() == str(b).strip()


def sanitize_fingerprint(fp: str | None) -> str | None:
    """脱敏展示：保留算法前缀 + 前 12 位十六进制。"""
    if not fp:
        return None
    text = str(fp)
    if not text.startswith(FINGERPRINT_PREFIX):
        return text[:12]
    return f"{FINGERPRINT_PREFIX}{text[len(FINGERPRINT_PREFIX):][:12]}"
