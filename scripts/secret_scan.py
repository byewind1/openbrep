#!/usr/bin/env python3
"""Machine-executable release secret gate (D7: BYOA 与发布秘密泄漏门禁).

Scans release-relevant inputs for secrets that must never ship in an installer:

- staged source (``--staged``), build output trees (``--tree``), zip artifacts
  (``--archive``). dmg/msi/pkg installers are opaque to the stdlib: scan the
  staging directories they are built from (macOS ``.app`` bundle, NSIS/MSI
  staging, or 7z-extracted installer content) with ``--tree``; passing an
  opaque installer to ``--archive`` fails closed.
- detects: ``auth.json`` / ``.env*`` / ``config.toml`` / private key files by
  name; ``.codex`` path segments; ``.openbrep`` home segments; Bearer tokens,
  JWTs, real OpenAI API keys (``sk-...``); ``CODEX_ACCESS_TOKEN`` /
  ``OPENAI_API_KEY`` assignments with literal values; caller-supplied canary
  strings (``--canary`` / ``OPENBREP_RELEASE_CANARY``).

Contract (D7 acceptance, fail-closed):
- Credential-named files are classified by path/name BEFORE any open/read and
  their contents are never opened; pointing the scanner at a developer HOME by
  mistake cannot read real auth material.
- Canary is a raw-byte scan at the highest priority: it is never exempted by
  source allowlists, NUL bytes, file type, chunk size, or directory ignores.
- Full files and full zip entries are streamed with overlap between chunks;
  nothing after 2 MiB can hide.
- Binary content is scanned as raw bytes for canary / JWT / Bearer / ``sk-*`` /
  env-token patterns (a NUL byte never skips a file).
- Unreadable non-credential files fail the gate (``unreadable`` finding).
- Symlinks are never followed; suspicious ones (credential name, secret
  segments in target, absolute target escaping the tree) fail closed, benign
  relative in-tree symlinks (e.g. dylib version links) are reported as INFO.
- Opaque installers (dmg/msi/pkg/exe) fail closed unless their staging tree is
  scanned in the same run.
- The report never echoes secret values — findings and paths are redacted
  (type + redacted location + line), so secrets in file names, directory names,
  archive entry names and target paths are also masked.
- Exit code: 0 = clean, 1 = findings, 2 = usage / target error.

Usage::

    python scripts/secret_scan.py --tree frontend/dist
    python scripts/secret_scan.py --tree dist/OpenBrep --archive release/OpenBrep-free-macOS.zip
    python scripts/secret_scan.py --staged
    python scripts/secret_scan.py --canary obr-canary-abc --tree dist
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ── 名称级检测（凭据类文件绝不读内容） ────────────────────────────────────

# basename 精确命中 → 凭据文件，只报名字不读内容
_CREDENTIAL_BASENAMES = frozenset({"auth.json", "config.toml"})
# basename 前缀命中 → 凭据文件（.env、.env.local、.env.production …）
_ENV_PREFIX = ".env"
_ENV_EXAMPLE_ALLOW = (".env.example", ".env.example.local")
# 路径段命中 → 开发者/订阅登录态目录，只报路径不读内容
_SECRET_DIR_SEGMENTS = (".codex", ".openbrep")
# 私钥形态（basename / 后缀）
_KEY_NAME_RE = re.compile(r"(?i)^id_rsa($|\.|_)")
_KEY_SUFFIXES = (".p12", ".pfx", ".key")

# ── 内容级检测（二进制/文本一视同仁的原始字节扫描） ──────────────────────

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")
# 合并模式：一次 finditer 覆盖 bearer/jwt/sk-*（性能：流式全量扫描只跑一遍）。
# 与独立模式保持完全一致的锚定/大小写：bearer 大小写不敏感，jwt/sk-* 大小写敏感，
# 均保留 \b 边界（避免 base64 hash 里的 sk- 随机子串误报）。
_CONTENT_RE = re.compile(
    r"(?P<bearer>\b[bB][eE][aA][rR][eE][rR]\s+[A-Za-z0-9._~+/=-]{12,}\b)"
    r"|(?P<jwt>\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)"
    r"|(?P<key>\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b)"
)
# 赋值形态：裸 KEY=… / KEY: "…"（shell/.env 与 JSON/YAML 裸键）命中；
# 引号键（"KEY": …、["KEY"] = …）不命中，避免源码字典 key 误报。
# 值必须是字面量；变量引用（$VAR、裸标识符）不命中，
# 避免源码误报（config.py 的 env_vars=("OPENAI_API_KEY",) 不匹配）。
_ACCESS_TOKEN_ASSIGN_RE = re.compile(
    r'(?i)(?<![\w"\'])\b(CODEX_ACCESS_TOKEN|OPENAI_API_KEY)\s*[=:]\s*'
    r'(?:"([^"]*)"|\'([^\']*)\'|([A-Za-z0-9._~+/=-]{16,}))'
)
# SSH 密钥算法名（libssh2 等二进制内嵌）：sk-ecdsa-sha2-nistp256、sk-ssh-ed25519-cert-v01
# 等以 sk- 开头但不是 OpenAI key；按已知算法标识精确排除（绝不整文件豁免）。
_SSH_KEY_ALGO_RE = re.compile(r"(?i)(ecdsa|ed25519|ssh-)")


def _is_bearer_prose(match_text: str) -> bool:
    """bearer 后的 token 是全小写单词（authorization/authentication 等文档措辞）→ 散文非秘密。"""
    token = re.sub(r"(?i)^bearer\s+", "", match_text, count=1)
    return token.isalpha() and token.islower()


def _is_safe_bearer(match_text: str) -> bool:
    lower = match_text.lower()
    if lower in _SAFE_MATCH_EXAMPLES:
        return True
    # 二进制/marshal 环境里安全样例后可能被吞噬 1-2 个类型字节
    # （如 pyc 字符串常量后的 marshal tag）；真实泄漏以安全样例开头则不会只差 1-2 字节。
    if any(lower.startswith(ex) and len(lower) - len(ex) <= 2 for ex in _SAFE_MATCH_EXAMPLES):
        return True
    return _is_bearer_prose(match_text)


def _is_ssh_key_algo(text: str) -> bool:
    return _SSH_KEY_ALGO_RE.search(text) is not None


_PLACEHOLDER_RE = re.compile(
    r"(?i)^(your[-_ ].*|test[-_ ].*|fake[-_ ].*|example[-_ ].*|old[-_ ].*|new[-_ ].*|"
    r"placeholder|xxxxx+|<[^>]+>|changeme|todo.*|sk-?(your|replace|example|test|fake).*)$"
)

# 已知安全样例（匹配级过滤，绝不整文件豁免）：脱敏模块 docstring/注释里的
# 示例值。canary 永不豁免；sk-*/JWT/其他 Bearer 值在 redact.py 内同样命中。
_SAFE_MATCH_EXAMPLES = frozenset({"bearer plain-secret-value"})

# 流式扫描：chunk 间保留重叠（须大于任何模式/最长 canary 的长度）。
# overlap 按本轮最长 canary 字节动态放大（见 scan_stream），保证任意合法
# canary 跨 chunk 边界都能命中；canary 长度由 _MAX_CANARY_BYTES 封顶
# （超限在 CLI 层 fail closed，见 _load_canaries）。
_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 8192
_MAX_CANARY_BYTES = 256 * 1024

# 嵌套归档（zip entry 里的 zip）递归资源保护：达到上限必须 fail closed。
_MAX_ARCHIVE_DEPTH = 4          # 允许的嵌套层数（顶层=0）
_MAX_ARCHIVE_ENTRIES = 50_000   # 单次扫描累计 entry 数
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024   # 累计解压字节上限
_MAX_NESTED_ZIP_BYTES = 256 * 1024 * 1024     # 单个嵌套 zip 读入内存上限

# finding type → 稳定类型名（报告用，不带值）
FINDING_LABELS = {
    "auth_file": "auth.json credential file",
    "env_file": ".env credential file",
    "config_toml": "config.toml (personal config)",
    "private_key": "private key file",
    "codex_dir": ".codex developer login dir",
    "openbrep_home": ".openbrep user data dir",
    "bearer": "Bearer token",
    "jwt": "JWT token",
    "openai_api_key": "OpenAI API key (sk-...)",
    "codex_access_token": "CODEX_ACCESS_TOKEN assignment",
    "openai_api_key_assignment": "OPENAI_API_KEY assignment",
    "canary": "release canary string",
    "unreadable": "file could not be read (fail closed)",
    "symlink": "symbolic link (not followed)",
    "symlink_escape": "symlink target escapes the artifact tree",
    "symlink_dangling": "symlink target missing / unverifiable (fail closed)",
    "symlink_unverifiable": "symlink target could not be read (fail closed)",
    "symlink_target_secret": "symlink target contains secret segments",
    "opaque_archive": "opaque installer archive (scan its staging tree)",
    "archive_limit": "nested archive resource limit reached (fail closed)",
    "nested_archive_unreadable": "nested archive could not be parsed (fail closed)",
}


@dataclass
class Finding:
    type: str
    target: str
    file: str
    line: int | None = None
    count: int = 1
    # severity: "error" 使 gate 失败；"info" 只提示（良性 symlink）
    severity: str = "error"

    def as_dict(self) -> dict:
        out: dict = {"type": self.type, "target": self.target, "file": self.file}
        if self.line is not None:
            out["line"] = self.line
        if self.count > 1:
            out["count"] = self.count
        if self.severity != "error":
            out["severity"] = self.severity
        return out


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    canaries: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def merge(self, other: "ScanResult") -> None:
        self.findings.extend(other.findings)
        self.targets.extend(other.targets)
        if not self.canaries and other.canaries:
            self.canaries = other.canaries


@dataclass
class _ArchiveBudget:
    """嵌套归档递归扫描的累计资源预算（单次扫描运行内共享）。"""

    entries: int = 0  # 累计处理 entry 数
    bytes: int = 0  # 累计解压字节数


# ── 名称级规则 ─────────────────────────────────────────────────────────────


def classify_name(rel_path: str) -> str | None:
    """凭据形态文件名/路径段 → 类型；非凭据返回 None。只判名字，绝不读内容。"""
    posix = rel_path.replace("\\", "/")
    basename = posix.rsplit("/", 1)[-1]
    if basename in _CREDENTIAL_BASENAMES:
        return {"auth.json": "auth_file", "config.toml": "config_toml"}[basename]
    if basename.startswith(_ENV_PREFIX) and basename not in _ENV_EXAMPLE_ALLOW:
        return "env_file"
    if _KEY_NAME_RE.match(basename) or basename.endswith(_KEY_SUFFIXES):
        return "private_key"
    segments = posix.split("/")
    for seg in _SECRET_DIR_SEGMENTS:
        if seg in segments:
            return {
                _SECRET_DIR_SEGMENTS[0]: "codex_dir",
                _SECRET_DIR_SEGMENTS[1]: "openbrep_home",
            }[seg]
    return None


def _dir_secret_type(rel_path: str) -> str | None:
    """目录路径是否含秘密段（.codex/.openbrep），含空目录。"""
    posix = rel_path.replace("\\", "/")
    segments = posix.split("/")
    for seg in _SECRET_DIR_SEGMENTS:
        if seg in segments:
            return {
                _SECRET_DIR_SEGMENTS[0]: "codex_dir",
                _SECRET_DIR_SEGMENTS[1]: "openbrep_home",
            }[seg]
    return None


def name_finding(rel_path: str, target: str) -> Finding | None:
    kind = classify_name(rel_path)
    if kind is None:
        return None
    return Finding(kind, target, rel_path.replace("\\", "/"))


def content_scan_applies(rel_path: str) -> bool:
    """凭据文件不做内容扫描（避免读取真实 auth 文件 / 重复报告）。"""
    return classify_name(rel_path) is None


def path_secret_findings(rel_path: str, canaries: tuple[str, ...], target: str) -> list[Finding]:
    """路径本身内嵌秘密（目录名/文件名/entry 名）：同样必须命中且报告时脱敏。"""
    findings: list[Finding] = []
    posix = rel_path.replace("\\", "/")
    for kind, rx in (
        ("bearer", _BEARER_RE),
        ("jwt", _JWT_RE),
        ("openai_api_key", _OPENAI_KEY_RE),
    ):
        m = rx.search(posix)
        if not m:
            continue
        if kind == "openai_api_key" and _is_ssh_key_algo(m.group(0)):
            continue
        if kind == "bearer" and _is_safe_bearer(m.group(0)):
            continue
        findings.append(Finding(kind, target, posix))
    m = _ACCESS_TOKEN_ASSIGN_RE.search(posix)
    if m:
        key_name = m.group(1).upper()
        value = next((g for g in m.groups()[1:] if g is not None), "").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        if value and (
            key_name != "OPENAI_API_KEY" or (len(value) >= 16 and not _PLACEHOLDER_RE.match(value))
        ):
            findings.append(
                Finding(
                    "codex_access_token"
                    if key_name == "CODEX_ACCESS_TOKEN"
                    else "openai_api_key_assignment",
                    target,
                    posix,
                )
            )
    for canary in canaries:
        if canary and canary in posix:
            findings.append(Finding("canary", target, posix))
    return findings


# ── 内容级规则（流式 + 单行） ──────────────────────────────────────────────


def _assignment_finding(line: str) -> Finding | None:
    m = _ACCESS_TOKEN_ASSIGN_RE.search(line)
    if not m:
        return None
    key_name = m.group(1).upper()
    value = next((g for g in m.groups()[1:] if g is not None), "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        return None
    if key_name == "CODEX_ACCESS_TOKEN":
        return Finding("codex_access_token", "", "")
    if _PLACEHOLDER_RE.match(value) or len(value) < 16:
        return None
    # sk- 形态已被 openai_api_key 规则覆盖，避免同值重复报告
    if _OPENAI_KEY_RE.search(value):
        return None
    return Finding("openai_api_key_assignment", "", "")


def scan_line(rel_path: str, line: str, canaries: tuple[str, ...]) -> list[Finding]:
    """单行内容级扫描（staged 新增行用）。"""
    findings: list[Finding] = []
    m_bearer = _BEARER_RE.search(line)
    if m_bearer and not _is_safe_bearer(m_bearer.group(0)):
        findings.append(Finding("bearer", "", rel_path))
    if _JWT_RE.search(line):
        findings.append(Finding("jwt", "", rel_path))
    m_key = _OPENAI_KEY_RE.search(line)
    if m_key and not _is_ssh_key_algo(m_key.group(0)):
        findings.append(Finding("openai_api_key", "", rel_path))
    assign = _assignment_finding(line)
    if assign is not None:
        findings.append(assign)
    for canary in canaries:
        if canary and canary in line:
            findings.append(Finding("canary", "", rel_path))
    return _merge_line_findings(findings)


def scan_text(rel_path: str, text: str, canaries: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for f in scan_line(rel_path, line, canaries):
            f.line = line_no
            findings.append(f)
    return _merge_line_findings(findings)


def _merge_line_findings(findings: list[Finding]) -> list[Finding]:
    """同文件同类型多行 → 保留首个位置，count 累计（报告只报类型+位置）。"""
    merged: dict[tuple[str, int], Finding] = {}
    for f in findings:
        key = (f.type, f.line or 0)
        if key in merged:
            merged[key].count += 1
        else:
            merged[key] = f
    return list(merged.values())


def scan_stream(fh, rel_path: str, target: str, canaries: tuple[str, ...]) -> list[Finding]:
    """流式扫描完整输入，chunk 间保留重叠；二进制/文本一视同仁。

    - canary 按原始字节搜索，绝不豁免（NUL/文件类型/位置均不影响）。
    - 秘密模式对 latin-1 保字节解码的全文运行（NUL 不跳过二进制）。
    - 重叠区只上报跨 chunk 边界的匹配，避免重复。
    """
    findings: list[Finding] = []
    prev_tail = b""
    line_cursor = 1
    chunk_index = 0
    # overlap 至少 8192；若 canary 更长，按最长 canary 字节动态放大，
    # 保证任何跨 chunk 边界的 canary 都完整落入 data（长度上限见
    # _MAX_CANARY_BYTES，CLI 层超限 fail closed）。
    max_canary_bytes = max(
        (len(c.encode("utf-8")) for c in canaries if c), default=0
    )
    overlap = max(_OVERLAP_BYTES, max_canary_bytes)
    while True:
        chunk = fh.read(_CHUNK_BYTES)
        if not chunk:
            break
        data = prev_tail + chunk
        boundary = len(prev_tail)  # data[boundary:] 是本 chunk 新内容
        text = data.decode("latin-1", errors="replace")
        first = chunk_index == 0

        def seen(start: int, end: int) -> bool:
            return (not first) and end <= boundary

        # canary：原始字节，最高优先级，无任何豁免
        for canary in canaries:
            if not canary:
                continue
            needle = canary.encode("utf-8")
            pos = data.find(needle)
            while pos != -1:
                if first or pos + len(needle) > boundary:
                    line = line_cursor + data[:pos].count(b"\n")
                    findings.append(Finding("canary", target, rel_path, line))
                pos = data.find(needle, pos + 1)

        for m in _CONTENT_RE.finditer(text):
            if seen(m.start(), m.end()):
                continue
            if m.lastgroup == "bearer" and _is_safe_bearer(m.group(0)):
                continue
            if m.lastgroup == "key" and _is_ssh_key_algo(m.group(0)):
                continue  # SSH 算法名（sk-ecdsa-*/sk-ssh-*），非 OpenAI key
            kind = {"bearer": "bearer", "jwt": "jwt", "key": "openai_api_key"}[m.lastgroup]
            line = line_cursor + text[: m.start()].count("\n")
            findings.append(Finding(kind, target, rel_path, line))
        for m in _ACCESS_TOKEN_ASSIGN_RE.finditer(text):
            if seen(m.start(), m.end()):
                continue
            line = line_cursor + text[: m.start()].count("\n")
            assign = _assignment_finding(m.group(0))
            if assign is not None:
                findings.append(Finding(assign.type, target, rel_path, line))

        line_cursor += text[boundary:].count("\n")
        prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk
        chunk_index += 1
    return _merge_line_findings(findings)


# ── 目标扫描 ───────────────────────────────────────────────────────────────


def _tree_symlink_verdict(link_path: Path, link_target: str) -> str:
    """tree symlink 的目标安全分类（纯路径规范化，不跟随、不读取目标）。

    - 绝对路径（/ 开头、Windows 盘符前缀）→ escape
    - 相对目标用 父目录 + target 纯字符串规范化；解析后越出父目录 → escape
    - 解析后仍在树内但目标不存在（悬空/不可验证）→ dangling
    - 其余（解析后位于树内且存在）→ ok
    """
    if link_target.startswith("/") or re.match(r"^[A-Za-z]:", link_target):
        return "escape"
    parent = os.path.abspath(os.path.dirname(str(link_path)))
    joined = os.path.normpath(os.path.join(parent, link_target))
    try:
        inside = os.path.commonpath([joined, parent]) == parent
    except ValueError:  # Windows 跨盘符
        inside = False
    if not inside:
        return "escape"
    if not os.path.lexists(joined):
        return "dangling"
    return "ok"


def _norm_entry(name: str) -> str:
    """zip entry 名规范化（去掉尾部斜杠、反斜杠转 /、posix normpath）。"""
    return posixpath.normpath(name.replace("\\", "/").rstrip("/"))


def _zip_entry_exists(normalized: str, entry_names: set[str], dir_prefixes: set[str]) -> bool:
    """规范化后的目标是否存在于归档（entry 精确名或祖先目录形态）。"""
    return normalized in entry_names or normalized in dir_prefixes


def _zip_symlink_verdict(
    entry_name: str,
    link_target: str,
    entry_names: set[str],
    dir_prefixes: set[str],
) -> str:
    """zip symlink entry 的目标安全分类（entry parent / link target 规范化）。

    - 绝对路径 → escape；反斜杠按分隔符处理（Windows 创建的 zip）
    - 归一化后越过 archive root → escape
    - 归一化后仍在 archive 内但目标 entry 不存在（悬空/不可验证）→ dangling
    - 其余（确认为 archive 内相对链接）→ ok
    """
    target = link_target.rstrip("\r\n").replace("\\", "/")
    if target.startswith("/") or re.match(r"^[A-Za-z]:", target):
        return "escape"
    parent = entry_name.rsplit("/", 1)[0] if "/" in entry_name else ""
    joined = f"{parent}/{target}" if parent else target
    normalized = posixpath.normpath(joined)
    if (
        normalized == ".."
        or normalized.startswith("../")
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        return "escape"
    if not _zip_entry_exists(normalized, entry_names, dir_prefixes):
        return "dangling"
    return "ok"


def _symlink_findings(
    rel: str, link_path: Path, target: str, canaries: tuple[str, ...]
) -> list[Finding]:
    """symlink：绝不跟随；按 link 名与 link target 的安全分类报告。

    相对目标逃逸、绝对目标、悬空/不可验证一律 error（fail closed）；
    确认为树内/归档内相对链接才 INFO。
    """
    findings: list[Finding] = []
    name_kind = classify_name(rel)
    if name_kind is not None:
        findings.append(Finding(name_kind, target, rel))
    findings.extend(path_secret_findings(rel, canaries, target))
    try:
        link_target = os.readlink(link_path)  # 不跟随
    except OSError:
        findings.append(Finding("symlink_unverifiable", target, rel))
        return findings
    link_target = link_target.rstrip("\r\n")
    if _dir_secret_type(link_target):
        findings.append(Finding("symlink_target_secret", target, rel))
    verdict = _tree_symlink_verdict(link_path, link_target)
    if verdict == "escape":
        findings.append(Finding("symlink_escape", target, rel))
    elif verdict == "dangling":
        findings.append(Finding("symlink_dangling", target, rel))
    if not findings:
        # 良性相对 in-tree symlink（如 dylib 版本链接）：可见但不失败
        findings.append(Finding("symlink", target, rel, severity="info"))
    return findings


def scan_tree(
    root: Path,
    canaries: tuple[str, ...] = (),
    target_label: str | None = None,
    _budget: _ArchiveBudget | None = None,
) -> ScanResult:
    """递归扫描目录树：不跟随符号链接，不忽略任何目录（release 树全扫）。"""
    target = target_label or f"tree:{root}"
    result = ScanResult(targets=[target], canaries=canaries)
    budget = _budget if _budget is not None else _ArchiveBudget()
    root_kind = _dir_secret_type(str(root))
    if root_kind:
        result.findings.append(Finding(root_kind, target, str(root)))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        rel_dir = str(base.relative_to(root)) if str(base) != str(root) else ""
        prefix = f"{rel_dir}/" if rel_dir else ""
        for name in list(dirnames):
            rel = f"{prefix}{name}"
            p = base / name
            if p.is_symlink():
                result.findings.extend(_symlink_findings(rel, p, target, canaries))
                dirnames.remove(name)  # 不递归
                continue
            kind = _dir_secret_type(rel)
            if kind is not None:
                result.findings.append(Finding(kind, target, rel))
            result.findings.extend(path_secret_findings(rel, canaries, target))
        for name in filenames:
            rel = f"{prefix}{name}"
            p = base / name
            if p.is_symlink():
                result.findings.extend(_symlink_findings(rel, p, target, canaries))
                continue
            # 1) 文件名分类 —— 绝不 open
            kind = classify_name(rel)
            if kind is not None:
                result.findings.append(Finding(kind, target, rel))
                continue
            # 2) 路径内嵌秘密模式（目录名/文件名）
            result.findings.extend(path_secret_findings(rel, canaries, target))
            # 3) 嵌套 zip → 归档级扫描（streaming entries，递归共享预算）
            if rel.lower().endswith(".zip"):
                try:
                    result.merge(scan_archive(p, canaries, _depth=0, _budget=budget))
                except ValueError:
                    result.findings.append(Finding("unreadable", target, rel))
                continue
            # 4) 内容级流式扫描（完整文件，二进制同样扫原始字节）
            try:
                with open(p, "rb") as fh:
                    result.findings.extend(scan_stream(fh, rel, target, canaries))
            except OSError:
                result.findings.append(Finding("unreadable", target, rel))
    return result


def _scan_zip_entries(
    zf: zipfile.ZipFile,
    result: ScanResult,
    canaries: tuple[str, ...],
    target: str,
    depth: int,
    budget: _ArchiveBudget,
) -> None:
    """遍历一个 zip 的 entries：名称级 + 内容级 + 嵌套 zip 递归（共享预算）。

    资源上限（entry 数 / 累计解压字节 / 嵌套深度）达到即产生 error finding
    并停止该归档的进一步扫描 —— 绝不静默 PASS。
    """
    entry_names: set[str] = set()
    dir_prefixes: set[str] = set()
    for info in zf.infolist():
        normalized = _norm_entry(info.filename)
        entry_names.add(normalized)
        parts = normalized.split("/")
        for k in range(1, len(parts)):
            dir_prefixes.add("/".join(parts[:k]))

    for info in zf.infolist():
        name = info.filename
        if budget.entries >= _MAX_ARCHIVE_ENTRIES:
            result.findings.append(Finding("archive_limit", target, name))
            return
        budget.entries += 1
        size = info.file_size or 0
        if budget.bytes + size > _MAX_ARCHIVE_BYTES:
            result.findings.append(Finding("archive_limit", target, name))
            return
        budget.bytes += size

        if info.is_dir():
            kind = _dir_secret_type(name)
            if kind is not None:
                result.findings.append(Finding(kind, target, name))
            result.findings.extend(path_secret_findings(name, canaries, target))
            continue
        # zip 内 symlink entry（内容即 link target）：每个 symlink 至少一个稳定 finding
        if stat.S_ISLNK(info.external_attr >> 16):
            had_error = False
            kind = classify_name(name)
            if kind is not None:
                result.findings.append(Finding(kind, target, name))
                had_error = True
            path_findings = path_secret_findings(name, canaries, target)
            if path_findings:
                had_error = True
            result.findings.extend(path_findings)
            try:
                link_target = zf.read(info)[:4096].decode("latin-1", errors="replace")
            except (OSError, zipfile.BadZipFile, RuntimeError):
                result.findings.append(Finding("unreadable", target, name))
                continue
            link_target = link_target.rstrip("\r\n")
            if _dir_secret_type(link_target):
                result.findings.append(Finding("symlink_target_secret", target, name))
                had_error = True
            verdict = _zip_symlink_verdict(name, link_target, entry_names, dir_prefixes)
            if verdict == "escape":
                result.findings.append(Finding("symlink_escape", target, name))
                had_error = True
            elif verdict == "dangling":
                result.findings.append(Finding("symlink_dangling", target, name))
                had_error = True
            elif not had_error:
                # 确认为 archive 内相对链接：INFO（可定位但不失败）
                result.findings.append(Finding("symlink", target, name, severity="info"))
            continue
        kind = classify_name(name)
        if kind is not None:
            result.findings.append(Finding(kind, target, name))
            continue
        result.findings.extend(path_secret_findings(name, canaries, target))
        # 嵌套 zip（entry 是 zip）→ 递归扫描 entries；失败必须 fail closed
        if name.lower().endswith(".zip"):
            if depth + 1 > _MAX_ARCHIVE_DEPTH:
                result.findings.append(Finding("archive_limit", target, name))
                continue
            if size > _MAX_NESTED_ZIP_BYTES:
                result.findings.append(Finding("archive_limit", target, name))
                continue
            try:
                data = zf.read(info)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                result.findings.append(Finding("unreadable", target, name))
                continue
            try:
                inner = zipfile.ZipFile(io.BytesIO(data))
            except (zipfile.BadZipFile, OSError):
                result.findings.append(Finding("nested_archive_unreadable", target, name))
                continue
            with inner:
                _scan_zip_entries(inner, result, canaries, target, depth + 1, budget)
            continue
        try:
            with zf.open(info) as entry:
                result.findings.extend(scan_stream(entry, name, target, canaries))
        except (OSError, zipfile.BadZipFile, RuntimeError):
            result.findings.append(Finding("unreadable", target, name))


def scan_archive(
    path: Path,
    canaries: tuple[str, ...] = (),
    _depth: int = 0,
    _budget: _ArchiveBudget | None = None,
) -> ScanResult:
    """扫描 zip 归档（streaming entries + 嵌套 zip 递归）；dmg/msi/pkg/exe → 失败。"""
    target = f"archive:{path}"
    result = ScanResult(targets=[target], canaries=canaries)
    if path.suffix.lower() != ".zip":
        result.findings.append(Finding("opaque_archive", target, str(path)))
        return result
    budget = _budget if _budget is not None else _ArchiveBudget()
    try:
        with zipfile.ZipFile(path) as zf:
            _scan_zip_entries(zf, result, canaries, target, _depth, budget)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"cannot open archive {path}: {exc}") from exc
    return result


def scan_staged(cwd: Path, canaries: tuple[str, ...] = ()) -> ScanResult:
    """扫描 git staged 变更：文件名级 + 新增行内容级（与 pre-commit 同语义）。"""
    target = "staged"
    result = ScanResult(targets=[target], canaries=canaries)

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    staged_files = [
        p for p in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if p
    ]
    for rel in staged_files:
        name = name_finding(rel, target)
        if name is not None:
            result.findings.append(name)
        result.findings.extend(path_secret_findings(rel, canaries, target))
    # 新增行（unified=0）做内容级检测
    diff = _git("diff", "--cached", "--unified=0")
    current_file = ""
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw.removeprefix("+++ b/")
            line_no = 0
            continue
        if current_file and raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)? @@", raw)
            if m:
                line_no = int(m.group(1))
            continue
        if not current_file or not raw.startswith("+"):
            continue
        if content_scan_applies(current_file):
            for f in scan_line(current_file, raw[1:], canaries):
                f.target = target
                f.line = line_no
                result.findings.append(f)
        line_no += 1
    return result


# ── 报告（绝不回显秘密值，路径/目标同样脱敏） ─────────────────────────────


def _load_canaries(cli_values: list[str]) -> tuple[str, ...]:
    values = list(cli_values)
    env = os.environ.get("OPENBREP_RELEASE_CANARY", "").strip()
    if env:
        values.extend(v.strip() for v in env.replace(",", " ").split() if v.strip())
    seen = tuple(dict.fromkeys(values))  # 去重保序
    for c in seen:
        if len(c.encode("utf-8")) > _MAX_CANARY_BYTES:
            # 不把 canary 值拼进错误消息（错误边界统一脱敏，但这里直接不写值）
            raise ValueError(
                f"canary string exceeds {_MAX_CANARY_BYTES} bytes maximum length"
            )
    return seen


def redact_secret_text(value, canaries: tuple[str, ...]) -> str:
    """把文本中的秘密形态（含路径片段）替换为占位符；报告用，绝不回显。"""
    out = str(value)
    for rx in (_BEARER_RE, _JWT_RE, _OPENAI_KEY_RE, _ACCESS_TOKEN_ASSIGN_RE):
        out = rx.sub("<redacted>", out)
    for canary in sorted((c for c in canaries if c), key=len, reverse=True):
        out = out.replace(canary, "<redacted>")
    return out


def report_text(result: ScanResult) -> str:
    canaries = result.canaries or ()
    info = [f for f in result.findings if f.severity == "info"]
    errors = [f for f in result.findings if f.severity == "error"]
    if result.ok:
        lines = [
            f"SECRET GATE PASS: {len(result.targets)} target(s) scanned, {len(errors)} findings"
        ]
        for f in info:
            lines.append(f"INFO: [{f.type}] {redact_secret_text(f.file, canaries)}")
        lines.append("")
        return "\n".join(lines)
    lines = [f"SECRET GATE FAIL: {len(errors)} finding(s)\n"]
    for f in errors:
        label = FINDING_LABELS.get(f.type, f.type)
        location = f"{f.file}:{f.line}" if f.line is not None else f.file
        lines.append(
            f"- [{f.type}] {label} @ {redact_secret_text(f.target, canaries)} "
            f":: {redact_secret_text(location, canaries)}"
        )
    for f in info:
        lines.append(f"INFO: [{f.type}] {redact_secret_text(f.file, canaries)}")
    lines.append("")
    return "\n".join(lines)


def report_json(result: ScanResult) -> str:
    canaries = result.canaries or ()
    payload = {
        "ok": result.ok,
        "targets": [redact_secret_text(t, canaries) for t in result.targets],
        "findings": [],
    }
    for f in result.findings:
        item = f.as_dict()
        item["target"] = redact_secret_text(item.get("target", ""), canaries)
        item["file"] = redact_secret_text(item.get("file", ""), canaries)
        payload["findings"].append(item)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run_scan(args: argparse.Namespace) -> ScanResult:
    """按 CLI 目标执行扫描，返回合并结果（单次运行共享嵌套归档预算）。"""
    canaries = _load_canaries(args.canary or [])
    result = ScanResult(canaries=canaries)
    missing: list[str] = []
    budget = _ArchiveBudget()

    for tree in args.tree or []:
        if not Path(tree).exists():
            missing.append(tree)
            continue
        if not Path(tree).is_dir():
            raise ValueError(f"target path is not a directory: {tree}")
        result.merge(scan_tree(Path(tree), canaries, _budget=budget))
    for archive in args.archive or []:
        if not Path(archive).exists():
            missing.append(archive)
            continue
        result.merge(scan_archive(Path(archive), canaries, _budget=budget))
    if args.staged:
        result.merge(scan_staged(Path.cwd(), canaries))

    if missing:
        raise ValueError(f"target path(s) not found: {', '.join(missing)}")
    return result


class _RedactingParser(argparse.ArgumentParser):
    """usage/参数错误也走统一脱敏边界：argv 里的秘密值不回显到 stderr。"""

    def __init__(self, *args, canaries: tuple[str, ...] = (), **kwargs):
        super().__init__(*args, **kwargs)
        self._canaries = canaries

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {redact_secret_text(message, self._canaries)}\n")


def _argv_canaries(argv: list[str]) -> list[str]:
    """解析前预扫描 argv 里的 --canary 值（供 argparse 错误脱敏）。"""
    values: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--canary":
            try:
                values.append(next(it))
            except StopIteration:
                pass
        elif arg.startswith("--canary="):
            values.append(arg.split("=", 1)[1])
    return values


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # env canary 先加载（超限 fail closed）；CLI canary 预扫描供 argparse 脱敏
    try:
        env_canaries = _load_canaries([])
    except ValueError as exc:
        print(f"SECRET GATE ERROR: {redact_secret_text(exc, ())}", file=sys.stderr)
        return 2
    redaction_canaries = tuple(dict.fromkeys(env_canaries + tuple(_argv_canaries(argv))))
    parser = _RedactingParser(
        description="Release secret gate: scan staged source, build trees and archives.",
        canaries=redaction_canaries,
    )
    parser.add_argument(
        "--tree",
        action="append",
        default=[],
        metavar="DIR",
        help="recursively scan a directory tree (repeatable)",
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        metavar="PATH",
        help="scan a zip artifact (repeatable); opaque installers fail closed",
    )
    parser.add_argument("--staged", action="store_true", help="scan git staged changes (cwd)")
    parser.add_argument(
        "--canary",
        action="append",
        default=[],
        metavar="STRING",
        help="fail if this canary string appears (repeatable; "
        "also read from OPENBREP_RELEASE_CANARY)",
    )
    parser.add_argument("--report", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    if not (args.tree or args.archive or args.staged):
        parser.error("at least one of --tree / --archive / --staged is required")

    try:
        result = run_scan(args)
    except ValueError as exc:
        # 统一脱敏错误边界：target/path/异常中的秘密一律不回显
        canaries = tuple(dict.fromkeys(env_canaries + tuple(args.canary or [])))
        print(f"SECRET GATE ERROR: {redact_secret_text(exc, canaries)}", file=sys.stderr)
        return 2

    if args.report == "json":
        print(report_json(result))
    else:
        print(report_text(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
