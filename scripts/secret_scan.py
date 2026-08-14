#!/usr/bin/env python3
"""Machine-executable release secret gate (D7: BYOA 与发布秘密泄漏门禁).

Scans release-relevant inputs for secrets that must never ship in an installer:

- staged source (``--staged``), build output trees (``--tree``), zip artifacts
  (``--archive``). dmg/msi/pkg installers are opaque to the stdlib: scan the
  staging directories they are built from (macOS ``.app`` bundle, NSIS/MSI
  staging) with ``--tree``, then the produced zip with ``--archive``.
- detects: ``auth.json`` / ``.env*`` / ``config.toml`` / private key files by
  name; ``.codex`` path segments; ``.openbrep`` home segments; Bearer tokens,
  JWTs, real OpenAI API keys (``sk-...``); ``CODEX_ACCESS_TOKEN`` /
  ``OPENAI_API_KEY`` assignments with literal values; caller-supplied canary
  strings (``--canary`` / ``OPENBREP_RELEASE_CANARY``).

Contract (D7 acceptance):
- The report never echoes secret values — only finding type + target + file
  (+ line for content findings).
- Credential-named files (``auth.json``, ``.env*``, key files, anything under
  ``.codex``/``.openbrep``) are flagged by name; their contents are never
  opened, so pointing the scanner at a developer HOME by mistake cannot read
  real auth material.
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

# ── 内容级检测（只对非凭据文件做） ────────────────────────────────────────

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")
# 赋值形态：裸 KEY=… / KEY: "…"（shell/.env 与 JSON/YAML 裸键）命中；
# 引号键（"KEY": …、["KEY"] = …）不命中，避免源码字典 key 误报。
# 值必须是字面量；变量引用（$VAR、裸标识符）不命中，
# 避免源码误报（config.py 的 env_vars=("OPENAI_API_KEY",) 不匹配）。
_ACCESS_TOKEN_ASSIGN_RE = re.compile(
    r'(?i)(?<![\w"\'])\b(CODEX_ACCESS_TOKEN|OPENAI_API_KEY)\s*[=:]\s*'
    r'(?:"([^"]*)"|\'([^\']*)\'|([A-Za-z0-9._~+/=-]{16,}))'
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(your[-_ ].*|test[-_ ].*|fake[-_ ].*|example[-_ ].*|old[-_ ].*|new[-_ ].*|"
    r"placeholder|xxxxx+|<[^>]+>|changeme|todo.*|sk-?(your|replace|example|test|fake).*)$"
)

# 树扫描默认忽略的目录（不递归、不读取）
_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "target",  # Rust 构建产物（Tauri bundle 在 target/release/bundle，需显式 --tree）
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".worktrees",
    }
)
# 内容级扫描跳过后缀（精确相对路径）：这些模块源码本身就是「秘密形态处理」，
# docstring/注释必然出现 Bearer/JWT/sk- 等示例词；文件名级检查仍生效。
# 目前只有 codex 脱敏模块自身（其源码随 openbrep/ 镜像进 PyInstaller 包）。
_CONTENT_ALLOW_SUFFIXES = ("openbrep/codex/redact.py",)

# 二进制/大文件内容扫描上限（凭据文件按名报不受限）
_MAX_CONTENT_BYTES = 2 * 1024 * 1024
_SNIFF_BYTES = 8 * 1024

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
}


@dataclass
class Finding:
    type: str
    target: str
    file: str
    line: int | None = None
    count: int = 1
    # severity: "error" 使 gate 失败；"info" 只提示（如不可解析的 dmg/msi）
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

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def merge(self, other: "ScanResult") -> None:
        self.findings.extend(other.findings)
        self.targets.extend(other.targets)


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


def name_finding(rel_path: str, target: str) -> Finding | None:
    kind = classify_name(rel_path)
    if kind is None:
        return None
    return Finding(kind, target, rel_path.replace("\\", "/"))


def content_scan_applies(rel_path: str) -> bool:
    """凭据文件不做内容扫描（避免读取真实 auth 文件 / 重复报告）；
    秘密处理模块源码（如 redact.py）也不做内容级扫描，避免 docstring 误报。"""
    if classify_name(rel_path) is not None:
        return False
    return not rel_path.replace("\\", "/").endswith(_CONTENT_ALLOW_SUFFIXES)


# ── 内容级规则 ─────────────────────────────────────────────────────────────


def scan_text(rel_path: str, text: str, canaries: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _BEARER_RE.search(line):
            findings.append(Finding("bearer", "", rel_path, line_no))
        if _JWT_RE.search(line):
            findings.append(Finding("jwt", "", rel_path, line_no))
        if _OPENAI_KEY_RE.search(line):
            findings.append(Finding("openai_api_key", "", rel_path, line_no))
        m = _ACCESS_TOKEN_ASSIGN_RE.search(line)
        if m:
            key_name = m.group(1).upper()
            value = next((g for g in m.groups()[1:] if g is not None), "").strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            if not value:
                continue
            kind = (
                "codex_access_token"
                if key_name == "CODEX_ACCESS_TOKEN"
                else "openai_api_key_assignment"
            )
            if kind == "openai_api_key_assignment":
                if not value or _PLACEHOLDER_RE.match(value) or len(value) < 16:
                    continue
                # sk- 形态已被 openai_api_key 规则覆盖，避免同值重复报告
                if _OPENAI_KEY_RE.search(value):
                    continue
            findings.append(Finding(kind, "", rel_path, line_no))
        for canary in canaries:
            if canary and canary in line:
                findings.append(Finding("canary", "", rel_path, line_no))
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


def _read_text_slice(data: bytes) -> str | None:
    """二进制嗅探：前 8KB 含 NUL → 视为二进制，跳过内容扫描。"""
    if not data:
        return None
    if b"\x00" in data[:_SNIFF_BYTES]:
        return None
    return data.decode("latin-1", errors="replace")


# ── 目标扫描 ───────────────────────────────────────────────────────────────


def scan_bytes(rel_path: str, data: bytes, target: str, canaries: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    name = name_finding(rel_path, target)
    if name is not None:
        return [name]
    if content_scan_applies(rel_path):
        text = _read_text_slice(data)
        if text is not None:
            findings = scan_text(rel_path, text, canaries)
    for f in findings:
        f.target = target
    return findings


def scan_tree(
    root: Path, canaries: tuple[str, ...] = (), target_label: str | None = None
) -> ScanResult:
    """递归扫描目录树：不跟随符号链接，跳过 _IGNORE_DIRS。"""
    target = target_label or f"tree:{root}"
    result = ScanResult(targets=[target])
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            if path.is_symlink():
                continue  # 绝不跟随符号链接（防链到 HOME）
            try:
                with open(path, "rb") as fh:
                    data = fh.read(_MAX_CONTENT_BYTES)
            except OSError:
                continue
            rel = str(path.relative_to(root))
            result.findings.extend(scan_bytes(rel, data, target, canaries))
    return result


def scan_archive(path: Path, canaries: tuple[str, ...] = ()) -> ScanResult:
    """扫描 zip 归档条目；dmg/msi/pkg 不可解析 → info 提示（不静默通过也不误杀）。"""
    target = f"archive:{path}"
    result = ScanResult(targets=[target])
    if path.suffix.lower() != ".zip":
        result.findings.append(
            Finding(
                "opaque_archive",
                target,
                str(path),
                severity="info",
            )
        )
        return result
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                name_f = name_finding(name, target)
                if name_f is not None:
                    result.findings.append(name_f)
                    continue
                if not content_scan_applies(name):
                    continue
                try:
                    data = zf.read(info)[:_MAX_CONTENT_BYTES]
                except (zipfile.BadZipFile, RuntimeError, OSError):
                    continue
                text = _read_text_slice(data)
                if text is not None:
                    for f in scan_text(name, text, canaries):
                        f.target = target
                        result.findings.append(f)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"cannot open archive {path}: {exc}") from exc
    return result


def scan_staged(cwd: Path, canaries: tuple[str, ...] = ()) -> ScanResult:
    """扫描 git staged 变更：文件名级 + 新增行内容级（与 pre-commit 同语义）。"""
    target = "staged"
    result = ScanResult(targets=[target])

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
            for f in scan_text(current_file, raw[1:], canaries):
                f.target = target
                f.line = line_no
                result.findings.append(f)
        line_no += 1
    return result


# ── 报告（绝不回显秘密值） ─────────────────────────────────────────────────


def _load_canaries(cli_values: list[str]) -> tuple[str, ...]:
    values = list(cli_values)
    env = os.environ.get("OPENBREP_RELEASE_CANARY", "").strip()
    if env:
        values.extend(v.strip() for v in env.replace(",", " ").split() if v.strip())
    return tuple(dict.fromkeys(values))  # 去重保序


def report_text(result: ScanResult) -> str:
    info = [f for f in result.findings if f.severity == "info"]
    errors = [f for f in result.findings if f.severity == "error"]
    if result.ok:
        lines = [
            f"SECRET GATE PASS: {len(result.targets)} target(s) scanned, {len(errors)} findings"
        ]
        for f in info:
            lines.append(f"INFO: [{f.type}] {f.file}")
        lines.append("")
        return "\n".join(lines)
    lines = [f"SECRET GATE FAIL: {len(errors)} finding(s)\n"]
    for f in errors:
        label = FINDING_LABELS.get(f.type, f.type)
        location = f"{f.file}:{f.line}" if f.line is not None else f.file
        lines.append(f"- [{f.type}] {label} @ {f.target} :: {location}")
    for f in info:
        lines.append(f"INFO: [{f.type}] {f.file}")
    lines.append("")
    return "\n".join(lines)


def report_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "ok": result.ok,
            "targets": result.targets,
            "findings": [f.as_dict() for f in result.findings],
        },
        ensure_ascii=False,
        indent=2,
    )


def run_scan(args: argparse.Namespace) -> ScanResult:
    """按 CLI 目标执行扫描，返回合并结果。"""
    canaries = _load_canaries(args.canary or [])
    result = ScanResult()
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
        help="scan a zip artifact (repeatable)",
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
