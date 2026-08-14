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
import json
import os
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

# 流式扫描：chunk 间保留重叠（须大于任何模式/最长 canary 的长度）
_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 8192

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
    "symlink_target_secret": "symlink target contains secret segments",
    "opaque_archive": "opaque installer archive (scan its staging tree)",
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
        prev_tail = chunk[-_OVERLAP_BYTES:] if len(chunk) >= _OVERLAP_BYTES else chunk
        chunk_index += 1
    return _merge_line_findings(findings)


# ── 目标扫描 ───────────────────────────────────────────────────────────────


def _symlink_findings(
    rel: str, link_path: Path, target: str, canaries: tuple[str, ...]
) -> list[Finding]:
    """symlink：绝不跟随；按 link 名与 link target 的安全分类报告。"""
    findings: list[Finding] = []
    name_kind = classify_name(rel)
    if name_kind is not None:
        findings.append(Finding(name_kind, target, rel))
    findings.extend(path_secret_findings(rel, canaries, target))
    try:
        link_target = os.readlink(link_path)  # 不跟随
    except OSError:
        link_target = ""
    if _dir_secret_type(link_target):
        findings.append(Finding("symlink_target_secret", target, rel))
    if link_target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", link_target):
        findings.append(Finding("symlink_escape", target, rel))
    if not findings:
        # 良性相对 in-tree symlink（如 dylib 版本链接）：可见但不失败
        findings.append(Finding("symlink", target, rel, severity="info"))
    return findings


def scan_tree(
    root: Path, canaries: tuple[str, ...] = (), target_label: str | None = None
) -> ScanResult:
    """递归扫描目录树：不跟随符号链接，不忽略任何目录（release 树全扫）。"""
    target = target_label or f"tree:{root}"
    result = ScanResult(targets=[target], canaries=canaries)
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
            # 3) 嵌套 zip → 归档级扫描（streaming entries）
            if rel.lower().endswith(".zip"):
                try:
                    result.merge(scan_archive(p, canaries))
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


def scan_archive(path: Path, canaries: tuple[str, ...] = ()) -> ScanResult:
    """扫描 zip 归档（streaming entries）；dmg/msi/pkg/exe 不可解析 → 失败（fail closed）。"""
    target = f"archive:{path}"
    result = ScanResult(targets=[target], canaries=canaries)
    if path.suffix.lower() != ".zip":
        result.findings.append(Finding("opaque_archive", target, str(path)))
        return result
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    kind = _dir_secret_type(name)
                    if kind is not None:
                        result.findings.append(Finding(kind, target, name))
                    result.findings.extend(path_secret_findings(name, canaries, target))
                    continue
                # zip 内 symlink entry（内容即 link target）
                if stat.S_ISLNK(info.external_attr >> 16):
                    kind = classify_name(name)
                    if kind is not None:
                        result.findings.append(Finding(kind, target, name))
                    result.findings.extend(path_secret_findings(name, canaries, target))
                    try:
                        link_target = zf.read(info)[:4096].decode("latin-1", errors="replace")
                    except (OSError, zipfile.BadZipFile, RuntimeError):
                        link_target = ""
                    if _dir_secret_type(link_target):
                        result.findings.append(Finding("symlink_target_secret", target, name))
                    if link_target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", link_target):
                        result.findings.append(Finding("symlink_escape", target, name))
                    continue
                kind = classify_name(name)
                if kind is not None:
                    result.findings.append(Finding(kind, target, name))
                    continue
                result.findings.extend(path_secret_findings(name, canaries, target))
                try:
                    with zf.open(info) as entry:
                        result.findings.extend(scan_stream(entry, name, target, canaries))
                except (OSError, zipfile.BadZipFile, RuntimeError):
                    result.findings.append(Finding("unreadable", target, name))
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
    return tuple(dict.fromkeys(values))  # 去重保序


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
    """按 CLI 目标执行扫描，返回合并结果。"""
    canaries = _load_canaries(args.canary or [])
    result = ScanResult(canaries=canaries)
    missing: list[str] = []

    for tree in args.tree or []:
        if not Path(tree).exists():
            missing.append(tree)
            continue
        result.merge(scan_tree(Path(tree), canaries))
    for archive in args.archive or []:
        if not Path(archive).exists():
            missing.append(archive)
            continue
        result.merge(scan_archive(Path(archive), canaries))
    if args.staged:
        result.merge(scan_staged(Path.cwd(), canaries))

    if missing:
        raise ValueError(f"target path(s) not found: {', '.join(missing)}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release secret gate: scan staged source, build trees and archives.",
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
        print(f"SECRET GATE ERROR: {exc}", file=sys.stderr)
        return 2

    if args.report == "json":
        print(report_json(result))
    else:
        print(report_text(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
