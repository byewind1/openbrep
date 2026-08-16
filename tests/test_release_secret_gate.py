"""D7: BYOA 与发布秘密泄漏门禁 —— 黑盒安全测试（全部离线、无真实账号/网络）。

覆盖：

1. ``scripts/secret_scan.py`` 发布秘密门禁扫描器（fail-closed）：
   - canary 注入的坏 artifact 在 tree/zip/二进制/大文件尾部/豁免源码/忽略目录/
     symlink/opaque installer 中全部必须失败；正常 artifact 通过且零秘密回显；
   - 凭据文件（auth.json/.env/…）在 open 之前完成分类，内容绝不读取
     （instrumented-open 证明）；unreadable 文件必须失败；
   - 报告对正文、文件名、目录名、target、archive entry 名均零秘密回显；
   - 全文件/全 zip entry 流式扫描（chunk 重叠），2 MiB 之后不藏秘密；
   - 二进制原始字节扫描（NUL 不跳过）；symlink 不跟随、可疑者 fail closed；
   - opaque installer 无 paired staging 不得 exit 0。

2. 黑盒回归：外部环境即使存在 canary token / OPENAI_API_KEY /
   开发机 ~/.codex/auth.json，初装 openai-codex（隔离 CODEX_HOME）仍 signed out；
   两个隔离 CODEX_HOME 的状态互不可见。子进程 fake app-server 模拟真实 codex
   CLI 的 auth.json 行为，transport/子进程环境注入是真实路径。

3. workflow 静态契约：release-tauri / build-installers 的 secret gate 步骤
   排在所有 upload-artifact 与 release publish 之前；tauri-action 纯构建
   （不内联创建 Release）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

# ── 扫描器加载（与 test_package_smoke.py 同模式） ──────────────────────────

SCANNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("_secret_scan_test", SCANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["_secret_scan_test"] = module  # dataclass 需要模块在 sys.modules 中
    spec.loader.exec_module(module)
    return module


# ── 夹具 ───────────────────────────────────────────────────────────────────

SECRET_MARKERS = (
    "sk-proj-REALKEY1234567890abcdef",
    "sk-REAL-SECRET-TOKEN-123456",
    "obr-canary-abc123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456",
)

CANARY = "obr-canary-abc123"


@pytest.fixture()
def bad_tree(tmp_path: Path) -> Path:
    """含各类秘密的坏 artifact 目录（canary 已注入）。"""
    root = tmp_path / "bad"
    (root / ".codex").mkdir(parents=True)
    (root / ".codex" / "auth.json").write_text(
        json.dumps(
            {
                "type": "chatgpt",
                "email": "dev@example.com",
                "accessToken": "sk-REAL-SECRET-TOKEN-123456",
            }
        ),
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        'credential = "sk-proj-REALKEY1234567890abcdef"\n'
        "token = obr-canary-abc123\n"
        "auth = Bearer plain-secret-value\n"
        "jwt = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456\n",
        encoding="utf-8",
    )
    (root / "env.sh").write_text(
        "export OPENAI_API_KEY=" + "sk-proj-REALKEY1234567890abcdef\n"
        "export CODEX_ACCESS_TOKEN=opaque-codex-token-1234567890\n",
        encoding="utf-8",
    )
    (root / "config.toml").write_text('[llm]\nmodel = "gpt-5.6"\n', encoding="utf-8")
    return root


@pytest.fixture()
def clean_tree(tmp_path: Path) -> Path:
    root = tmp_path / "clean"
    root.mkdir()
    (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "config.example.toml").write_text('[llm]\nmodel = "gpt-5.6"\n', encoding="utf-8")
    return root


# ── 扫描器：名称级（凭据文件只按名报，绝不 open） ─────────────────────────


def test_classify_name_and_content_gate():
    scanner = _load_scanner()
    assert scanner.classify_name("a/b/auth.json") == "auth_file"
    assert scanner.classify_name("a/.codex/x") == "codex_dir"
    assert scanner.classify_name("a/.env.local") == "env_file"
    assert scanner.classify_name("a/.env.example") is None
    assert scanner.classify_name("a/config.toml") == "config_toml"
    assert scanner.classify_name("a/config.example.toml") is None
    assert scanner.classify_name("a/id_rsa") == "private_key"
    assert scanner.classify_name("a/cert.pfx") == "private_key"
    assert scanner.classify_name("a/README.md") is None
    # 凭据文件绝不进入内容扫描
    assert scanner.content_scan_applies("a/auth.json") is False
    assert scanner.content_scan_applies("a/.env") is False
    assert scanner.content_scan_applies("a/.codex/anything") is False
    assert scanner.content_scan_applies("a/main.py") is True


def test_credential_file_never_opened(tmp_path, monkeypatch):
    """P0-1：auth.json 在 open 之前按名分类；instrumented-open 证明从未打开。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    (root / ".codex").mkdir(parents=True)
    auth = root / ".codex" / "auth.json"
    auth.write_text("TOP-SECRET-AUTH-BYTES", encoding="utf-8")

    real_open = open
    opened: list[str] = []

    def spy_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy_open)
    result = scanner.scan_tree(root)
    assert any(f.type == "auth_file" for f in result.findings)
    assert not result.ok
    assert str(auth) not in opened  # 凭据文件从未被打开
    report = scanner.report_text(result)
    assert "TOP-SECRET-AUTH-BYTES" not in report


def test_unreadable_file_fails_closed(tmp_path, monkeypatch):
    """P0-1：无法读取的普通 artifact 文件必须使 gate 失败，不能静默 continue。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    root.mkdir()
    target = root / "payload.dat"
    target.write_bytes(b"hello")

    real_open = open

    def denying_open(path, *args, **kwargs):
        if str(path) == str(target):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", denying_open)
    result = scanner.scan_tree(root)
    assert not result.ok
    assert any(f.type == "unreadable" and f.severity == "error" for f in result.findings)


# ── 扫描器：内容级 ─────────────────────────────────────────────────────────


def test_scan_text_detects_bearer_jwt_and_sk_keys():
    scanner = _load_scanner()
    text = (
        'h = "Authorization: Bearer plain-secret-value"\n'
        "jwt = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456\n"
        'key = "sk-proj-REALKEY1234567890abcdef"\n'
    )
    types = {f.type for f in scanner.scan_text("x.py", text, ())}
    assert "jwt" in types
    assert "openai_api_key" in types
    assert "bearer" not in types  # plain-secret-value 是已知安全样例


def test_scan_text_detects_env_assignments_but_not_plain_references():
    scanner = _load_scanner()
    # 裸赋值（.env / shell）命中
    assert {
        f.type
        for f in scanner.scan_text("env", "OPENAI_API_KEY=" + "sk-live-key-1234567890abcdef\n", ())
    } == {"openai_api_key"}
    assert {
        f.type
        for f in scanner.scan_text("env", "CODEX_ACCESS_TOKEN=opaque-token-value-1234567890\n", ())
    } == {"codex_access_token"}
    # 源码里只引用变量名（无赋值）不命中 —— 避免 D1/D2 代码误报
    assert scanner.scan_text("config.py", 'env_vars=("OPENAI_API_KEY",)\n', ()) == []
    assert (
        scanner.scan_text("llm.py", 'os.environ.setdefault("OPENAI_API_KEY", api_key)\n', ()) == []
    )
    # 占位符赋值不命中
    assert scanner.scan_text("env", 'OPENAI_API_KEY="your-api-key-here"\n', ()) == []
    assert scanner.scan_text("env", 'OPENAI_API_KEY=""\n', ()) == []
    # 字典 key 标签（如 "codex_access_token": "..."）不误报
    assert (
        scanner.scan_text(
            "labels.py", '"codex_access_token": "Codex access token assignment",\n', ()
        )
        == []
    )
    # SSH 算法名（sk-ecdsa-*/sk-ssh-*）不是 OpenAI key
    assert scanner.scan_text("x", "sk-ecdsa-sha2-nistp256@openssh.com\n", ()) == []
    # 散文（全小写单词）不是 Bearer token
    assert (
        scanner.scan_text(
            "x", "The token to use as HTTP bearer authorization for remote files.\n", ()
        )
        == []
    )


def test_secret_redaction_module_safe_example_does_not_false_positive(tmp_path):
    """redact.py 的安全样例（Bearer plain-secret-value）不误报，但 canary/sk-* 必命中。"""
    scanner = _load_scanner()
    root = tmp_path / "pkg"
    (root / "openbrep" / "codex").mkdir(parents=True)
    (root / "openbrep" / "codex" / "redact.py").write_text(
        "# 裸 Bearer token（无 Authorization 前缀时）\n"
        '# "Authorization: Bearer plain-secret-value" 等冒号写法\n'
        '_BEARER_RE = re.compile(r"(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]{6,}")\n',
        encoding="utf-8",
    )
    assert scanner.scan_tree(root).ok
    # canary 与 sk-* 永不豁免
    (root / "openbrep" / "codex" / "redact.py").write_text(
        "# doc\nCANARY=obr-canary-abc123\nsk-proj-REALKEY1234567890abcdef\n",
        encoding="utf-8",
    )
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    types = {f.type for f in result.findings}
    assert "canary" in types
    assert "openai_api_key" in types


# ── 扫描器：canary 注入 → gate 失败（覆盖评审的每条绕过形态） ─────────────


def test_canary_injected_artifact_fails_gate(clean_tree):
    scanner = _load_scanner()
    (clean_tree / "leak.js").write_text(f"const canary = '{CANARY}';\n", encoding="utf-8")
    result = scanner.scan_tree(clean_tree, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)
    assert CANARY not in scanner.report_text(result)  # canary 也不回显


def test_canary_in_developer_ignore_dirs_fails(tmp_path):
    """P0-2：release 树不得忽略 .git/node_modules/.venv/target/.worktrees。"""
    scanner = _load_scanner()
    root = tmp_path / "artifact"
    for d in (".git", "node_modules", ".venv", "target", ".worktrees"):
        (root / d).mkdir(parents=True)
        (root / d / "leak").write_text(f"x={CANARY}\n", encoding="utf-8")
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    canary_files = {f.file for f in result.findings if f.type == "canary"}
    assert len(canary_files) == 5


def test_canary_after_2mib_in_large_file_fails(tmp_path):
    """P0-2：全文件流式扫描，2 MiB 之后不藏秘密。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    root.mkdir()
    big = root / "bundle.js"
    with open(big, "wb") as fh:
        fh.write(b"// x" + b"a" * (2 * 1024 * 1024 + 1000))
        fh.write(f'var c="{CANARY}";'.encode())
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_canary_and_key_in_binary_with_nul_fails(tmp_path):
    """P0-2：含 NUL 的二进制也要原始字节扫描，不得整体跳过。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    root.mkdir()
    (root / "app.bin").write_bytes(
        b"\x00\x01\x02" + CANARY.encode() + b"\x00sk-proj-REALKEY1234567890abcdef\x00\xff"
    )
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    types = {f.type for f in result.findings}
    assert "canary" in types
    assert "openai_api_key" in types


def test_canary_straddling_chunk_boundary_fails(tmp_path):
    """chunk 重叠：canary 恰在 chunk 边界处也必须命中。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    root.mkdir()
    target = root / "straddle.txt"
    filler = b"x" * (1024 * 1024 - 5)  # 让 canary 跨 1 MiB chunk 边界
    with open(target, "wb") as fh:
        fh.write(filler + CANARY.encode() + b"\n")
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_empty_codex_dir_and_auth_json_symlink_fail(tmp_path):
    """P0-2：空 .codex 目录要枚举；auth.json symlink 按名 fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "a"
    (root / ".codex").mkdir(parents=True)
    real_auth = tmp_path / "outside" / "auth.json"
    real_auth.parent.mkdir()
    real_auth.write_text('{"type":"chatgpt","accessToken":"sk-leaked"}', encoding="utf-8")
    (root / ".codex" / "auth.json").symlink_to(real_auth)
    result = scanner.scan_tree(root)
    assert not result.ok
    types = {f.type for f in result.findings}
    assert "codex_dir" in types  # 空目录也被枚举
    assert "auth_file" in types  # symlink 名按凭据名报
    assert "symlink_escape" in types  # 绝对目标逃逸
    # 内容零回显
    report = scanner.report_text(result)
    assert "sk-leaked" not in report


def test_benign_relative_in_tree_symlink_is_info_only(tmp_path):
    """良性相对 in-tree symlink（如 dylib 版本链接）可见但不失败。"""
    scanner = _load_scanner()
    root = tmp_path / "app"
    root.mkdir()
    (root / "libfoo.1.2.3.dylib").write_bytes(b"\x00data")
    (root / "libfoo.1.dylib").symlink_to("libfoo.1.2.3.dylib")
    result = scanner.scan_tree(root)
    assert result.ok
    assert any(f.type == "symlink" and f.severity == "info" for f in result.findings)


def test_secret_filename_and_target_are_redacted_in_reports(tmp_path):
    """P0-4：报告对文件名/目录名/target 中的秘密同样零回显。"""
    scanner = _load_scanner()
    root = tmp_path / "sk-proj-REVIEWSECRET1234567890abcdef"
    root.mkdir()
    (root / "payload.txt").write_text(f"x\n{CANARY}\n", encoding="utf-8")
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    text_report = scanner.report_text(result)
    json_report = scanner.report_json(result)
    assert "REVIEWSECRET" not in text_report
    assert "REVIEWSECRET" not in json_report
    assert CANARY not in text_report
    assert CANARY not in json_report
    # 保留可定位性：目录被脱敏但仍可见
    assert "<redacted>" in json_report


def test_report_never_echoes_any_secret_value(bad_tree):
    scanner = _load_scanner()
    result = scanner.scan_tree(bad_tree, canaries=(CANARY,))
    text_report = scanner.report_text(result)
    json_report = scanner.report_json(result)
    for marker in SECRET_MARKERS:
        assert marker not in text_report
        assert marker not in json_report
    assert "auth_file" in json_report
    assert ".codex/auth.json" in json_report


def test_clean_artifact_passes_and_prints_no_secrets(clean_tree):
    scanner = _load_scanner()
    result = scanner.scan_tree(clean_tree, canaries=(CANARY,))
    assert result.ok
    report = scanner.report_text(result)
    assert "SECRET GATE PASS" in report
    for marker in SECRET_MARKERS:
        assert marker not in report


# ── 扫描器：zip 归档 ───────────────────────────────────────────────────────


def _zip_tree(root: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w") as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(root))


def test_scan_zip_flags_bad_entries(bad_tree, tmp_path):
    scanner = _load_scanner()
    zpath = tmp_path / "bad.zip"
    _zip_tree(bad_tree, zpath)
    result = scanner.scan_archive(zpath, canaries=(CANARY,))
    assert not result.ok
    types = {f.type for f in result.findings}
    assert "auth_file" in types
    assert "openai_api_key" in types
    assert "canary" in types
    report = scanner.report_text(result)
    for marker in SECRET_MARKERS:
        assert marker not in report


def test_scan_zip_clean_passes(clean_tree, tmp_path):
    scanner = _load_scanner()
    zpath = tmp_path / "clean.zip"
    _zip_tree(clean_tree, zpath)
    result = scanner.scan_archive(zpath, canaries=(CANARY,))
    assert result.ok


def test_scan_zip_streams_full_entry_after_2mib(tmp_path):
    """P0-2：zip entry 全量流式扫描，尾部 canary 必须命中。"""
    scanner = _load_scanner()
    zpath = tmp_path / "big.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("big.dat", b"x" * (2 * 1024 * 1024 + 1000) + CANARY.encode())
    result = scanner.scan_archive(zpath, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_opaque_archive_fails_closed(tmp_path):
    """P0-2/P0-3：dmg/msi/exe 不可解析 → gate 失败（须配 staging 树扫描）。"""
    scanner = _load_scanner()
    for suffix in (".dmg", ".msi", ".exe", ".pkg"):
        fake = tmp_path / f"OpenBrep{suffix}"
        fake.write_bytes(CANARY.encode() + b"\x00\x01")
        result = scanner.scan_archive(fake, canaries=(CANARY,))
        assert not result.ok, f"{suffix} 不应 exit 0"
        assert any(f.type == "opaque_archive" and f.severity == "error" for f in result.findings)


def test_nested_zip_in_tree_is_scanned(tmp_path):
    """tree 里的嵌套 zip（如 base_library.zip）也按归档级扫描。"""
    scanner = _load_scanner()
    root = tmp_path / "pkg"
    root.mkdir()
    inner = root / "data.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("leak", CANARY.encode())
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


# ── 复审 P0-1：symlink 相对逃逸必须 fail closed（tree 与 zip 均不跟随） ────


def _symlink_entry(zf: zipfile.ZipFile, name: str, target: str) -> None:
    zi = zipfile.ZipInfo(name)
    zi.create_system = 3
    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
    zf.writestr(zi, target)


@pytest.mark.parametrize(
    "target",
    ["../secret.txt", "../../developer/auth.json", "../../../x/y", "a/../../out"],
)
def test_tree_symlink_relative_escape_fails_closed(tmp_path, target):
    """P0-1：相对 ../ 与多层 ../../ 逃逸（含从子目录出发）必须 fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "artifact"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "escape").symlink_to(target)
    result = scanner.scan_tree(root)
    assert not result.ok
    assert any(f.type == "symlink_escape" and f.severity == "error" for f in result.findings)


def test_tree_symlink_absolute_and_drive_escape_fail_closed(tmp_path):
    """P0-1：绝对路径与 Windows 盘符目标必须 fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "abs").symlink_to("/etc/passwd")
    (root / "drive").symlink_to("C:\\Users\\me\\secret.txt")
    result = scanner.scan_tree(root)
    assert not result.ok
    escapes = {f.type for f in result.findings if f.type == "symlink_escape"}
    assert "symlink_escape" in escapes
    assert len([f for f in result.findings if f.type == "symlink_escape"]) == 2


def test_tree_symlink_dangling_fails_closed(tmp_path):
    """P0-1：解析后仍在树内但目标不存在（悬空）→ fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "libfoo.1.dylib").symlink_to("libfoo.1.2.3.dylib")  # 目标缺失
    result = scanner.scan_tree(root)
    assert not result.ok
    assert any(f.type == "symlink_dangling" and f.severity == "error" for f in result.findings)


def test_tree_symlink_never_followed(tmp_path, monkeypatch):
    """P0-1：绝不跟随 symlink 读取目标——link 路径本身永不被 open。"""
    scanner = _load_scanner()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "auth.json").write_text("TOP-SECRET-OUTSIDE", encoding="utf-8")

    root = tmp_path / "artifact"
    root.mkdir()
    target = root / "target.txt"
    target.write_text(f"x={CANARY}\n", encoding="utf-8")
    link = root / "link.txt"
    link.symlink_to("target.txt")  # 树内相对链接

    real_open = open
    opened: list[str] = []

    def spy_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy_open)
    result = scanner.scan_tree(root, canaries=(CANARY,))
    # canary 经由真实文件 target.txt 命中，无需跟随 link
    assert any(f.type == "canary" for f in result.findings)
    assert str(link) not in opened  # link 路径从未被打开
    assert str(outside / "auth.json") not in opened  # 树外目标从未被触碰
    # 树外文件内容不在报告中
    report = scanner.report_text(result)
    assert "TOP-SECRET-OUTSIDE" not in report


@pytest.mark.parametrize(
    "target",
    ["../secret.txt", "../../developer/auth.json", "../../../x/y", "a/../../out"],
)
def test_zip_symlink_relative_escape_fails_closed(tmp_path, target):
    """P0-1：zip symlink 相对 ../ 与多层 ../../ 逃逸必须 fail closed。"""
    scanner = _load_scanner()
    zpath = tmp_path / "escape.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        _symlink_entry(zf, "escape", target)
    result = scanner.scan_archive(zpath)
    assert not result.ok
    assert any(f.type == "symlink_escape" and f.severity == "error" for f in result.findings)


def test_zip_symlink_subdir_relative_escape_fails_closed(tmp_path):
    """P0-1：从子目录 entry 出发的 ../../ 逃逸同样 fail closed。"""
    scanner = _load_scanner()
    zpath = tmp_path / "escape.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        _symlink_entry(zf, "sub/escape", "../../developer/auth.json")
    result = scanner.scan_archive(zpath)
    assert not result.ok
    assert any(f.type == "symlink_escape" and f.severity == "error" for f in result.findings)


def test_tree_symlink_subdir_relative_escape_fails_closed(tmp_path):
    """P0-1：tree 从子目录出发的 ../../ 逃逸同样 fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "artifact"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "escape").symlink_to("../../developer/auth.json")
    result = scanner.scan_tree(root)
    assert not result.ok
    assert any(f.type == "symlink_escape" and f.severity == "error" for f in result.findings)


def test_zip_symlink_absolute_and_drive_escape_fail_closed(tmp_path):
    """P0-1：zip symlink 绝对路径与 Windows 盘符目标必须 fail closed。"""
    scanner = _load_scanner()
    zpath = tmp_path / "escape.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        _symlink_entry(zf, "abs", "/etc/passwd")
        _symlink_entry(zf, "drive", "C:/Users/me/secret.txt")
        _symlink_entry(zf, "drive2", "C:\\Users\\me\\secret.txt")
    result = scanner.scan_archive(zpath)
    assert not result.ok
    assert len([f for f in result.findings if f.type == "symlink_escape"]) == 3


def test_zip_symlink_dangling_fails_closed(tmp_path):
    """P0-1：zip symlink 目标 entry 不存在（悬空/不可验证）→ fail closed。"""
    scanner = _load_scanner()
    zpath = tmp_path / "dangling.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        _symlink_entry(zf, "libfoo.1.dylib", "libfoo.1.2.3.dylib")
    result = scanner.scan_archive(zpath)
    assert not result.ok
    assert any(f.type == "symlink_dangling" and f.severity == "error" for f in result.findings)


def test_zip_symlink_benign_in_archive_is_info(tmp_path):
    """P0-1：确认为 archive 内相对链接（目标 entry 存在）→ INFO，不失败。"""
    scanner = _load_scanner()
    zpath = tmp_path / "benign.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("lib/libfoo.1.2.3.dylib", b"\x00data")
        _symlink_entry(zf, "lib/libfoo.1.dylib", "libfoo.1.2.3.dylib")
    result = scanner.scan_archive(zpath)
    assert result.ok
    assert any(f.type == "symlink" and f.severity == "info" for f in result.findings)


def test_zip_symlink_every_entry_has_stable_finding(tmp_path):
    """P0-1：zip 内每个 symlink 至少产生一个稳定 finding（error 或 INFO）。"""
    scanner = _load_scanner()
    zpath = tmp_path / "links.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("lib/libfoo.1.2.3.dylib", b"\x00data")
        _symlink_entry(zf, "lib/libfoo.1.dylib", "libfoo.1.2.3.dylib")
        _symlink_entry(zf, "bad", "../../outside")
    result = scanner.scan_archive(zpath)
    symlink_findings = [f for f in result.findings if f.type in ("symlink", "symlink_escape")]
    assert len(symlink_findings) == 2  # 两个 symlink entry 各有 finding


# ── 复审 P0-2：嵌套 zip 不得隐藏 canary；资源上限 fail closed ─────────────


def _nested_zip_chain(tmp_path, levels: int, canary: str = CANARY) -> Path:
    """构造 levels 层嵌套 zip（最内层含 canary），返回最外层 zip。

    levels=1 → outer.zip 内含 inner.zip（inner 里是 canary）；
    levels=2 → outer.zip 内含 mid.zip，mid 内含 inner.zip。
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload", (f"x={canary} " * 200).encode())
    cur = inner
    for level in range(levels):
        outer = tmp_path / f"level{level}.zip"
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(cur, f"nested/{cur.name}")
        cur = outer
    return cur


def test_nested_zip_canary_detected(tmp_path):
    """P0-2：外层 zip 的 entry 是压缩后的 inner.zip，canary 仍必须命中（评审复现）。"""
    scanner = _load_scanner()
    outer = _nested_zip_chain(tmp_path, levels=1)
    assert b"obr-canary" not in outer.read_bytes()  # 压缩后原始字节不含 canary
    result = scanner.scan_archive(outer, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_two_level_nested_zip_canary_detected(tmp_path):
    """P0-2：两层嵌套（outer→mid→inner）canary 仍必须命中。"""
    scanner = _load_scanner()
    outer = _nested_zip_chain(tmp_path, levels=2)
    result = scanner.scan_archive(outer, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_nested_zip_depth_limit_fails_closed(tmp_path, monkeypatch):
    """P0-2：嵌套深度达到上限必须 error finding，不能 PASS。"""
    scanner = _load_scanner()
    outer = _nested_zip_chain(tmp_path, levels=2)
    monkeypatch.setattr(scanner, "_MAX_ARCHIVE_DEPTH", 0)  # 不允许任何嵌套
    result = scanner.scan_archive(outer, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "archive_limit" and f.severity == "error" for f in result.findings)


def test_nested_zip_entries_limit_fails_closed(tmp_path, monkeypatch):
    """P0-2：累计 entry 数达到上限必须 error finding，不能 PASS。"""
    scanner = _load_scanner()
    outer = _nested_zip_chain(tmp_path, levels=1)
    monkeypatch.setattr(scanner, "_MAX_ARCHIVE_ENTRIES", 1)
    result = scanner.scan_archive(outer, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "archive_limit" and f.severity == "error" for f in result.findings)


def test_nested_zip_bytes_limit_fails_closed(tmp_path, monkeypatch):
    """P0-2：累计解压字节达到上限必须 error finding，不能 PASS。"""
    scanner = _load_scanner()
    outer = _nested_zip_chain(tmp_path, levels=1)
    monkeypatch.setattr(scanner, "_MAX_ARCHIVE_BYTES", 10)
    result = scanner.scan_archive(outer, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "archive_limit" and f.severity == "error" for f in result.findings)


def test_corrupt_nested_zip_fails_closed(tmp_path):
    """P0-2：.zip entry 无法解析为 zip → error finding，不能静默当普通文件。"""
    scanner = _load_scanner()
    zpath = tmp_path / "outer.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("fake/inner.zip", b"this is not a zip archive")
    result = scanner.scan_archive(zpath)
    assert not result.ok
    assert any(
        f.type == "nested_archive_unreadable" and f.severity == "error" for f in result.findings
    )


def test_nested_zip_in_tree_shared_budget(tmp_path, monkeypatch):
    """P0-2：tree 里多个 zip 共享同一预算，超限 fail closed。"""
    scanner = _load_scanner()
    root = tmp_path / "pkg"
    root.mkdir()
    for i in range(2):
        z = _nested_zip_chain(tmp_path / f"z{i}", levels=1)
        (root / z.name).write_bytes(z.read_bytes())
    monkeypatch.setattr(scanner, "_MAX_ARCHIVE_ENTRIES", 1)
    result = scanner.scan_tree(root, canaries=(CANARY,))
    assert not result.ok
    assert any(f.type == "archive_limit" for f in result.findings)


# ── 第三轮复审：ZIP metadata 与改名 ZIP 不得 fail open ───────────

def test_top_level_zip_comment_canary_fails_closed(tmp_path):
    """ZIP 容器 comment 中的 canary 必须 fail closed。"""
    scanner = _load_scanner()
    archive = tmp_path / "comment.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("clean.txt", b"clean")
        zf.comment = CANARY.encode()

    result = scanner.scan_archive(archive, canaries=(CANARY,))

    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_zip_entry_metadata_canary_fails_closed(tmp_path):
    """ZIP entry comment/extra 中的 canary 都必须 fail closed。"""
    scanner = _load_scanner()
    archive = tmp_path / "metadata.zip"
    comment_canary = f"{CANARY}-comment"
    extra_canary = f"{CANARY}-extra"
    info = zipfile.ZipInfo("clean.txt")
    info.comment = comment_canary.encode()
    payload = extra_canary.encode()
    info.extra = b"\xfe\xca" + len(payload).to_bytes(2, "little") + payload
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, b"clean")

    result = scanner.scan_archive(archive, canaries=(comment_canary, extra_canary))

    assert not result.ok
    metadata_files = {f.file for f in result.findings if f.type == "canary"}
    assert "clean.txt:comment" in metadata_files
    assert "clean.txt:extra" in metadata_files


def test_outer_renamed_inner_zip_canary_fails_closed(tmp_path):
    """outer.zip/payload.bin 实为 ZIP 时必须递归扫描。"""
    scanner = _load_scanner()
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("secret.txt", CANARY.encode())
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.bin", inner.getvalue())

    result = scanner.scan_archive(outer, canaries=(CANARY,))

    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_tree_renamed_zip_canary_fails_closed(tmp_path):
    """tree/payload.bin 实为 ZIP 时必须按归档扫描。"""
    scanner = _load_scanner()
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("secret.txt", CANARY.encode())
    root = tmp_path / "release-tree"
    root.mkdir()
    (root / "payload.bin").write_bytes(inner.getvalue())

    result = scanner.scan_tree(root, canaries=(CANARY,))

    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_zip_magic_corrupt_fails_closed(tmp_path):
    """ZIP magic 命中但无法解析时必须产生稳定 error finding。"""
    scanner = _load_scanner()
    root = tmp_path / "release-tree"
    root.mkdir()
    (root / "payload.dat").write_bytes(b"PK\x03\x04corrupt")

    result = scanner.scan_tree(root)

    assert not result.ok
    assert any(
        f.type == "nested_archive_unreadable" and f.severity == "error"
        for f in result.findings
    )


# ── 复审 P0-3：CLI 全部错误输出走统一脱敏边界 ─────────────────────

SECRET_DIR_NAME = "sk-proj-REVIEWSECRET1234567890abcdef"


def _cli(scanner, monkeypatch, capsys, *argv, chdir=None):
    if chdir is not None:
        monkeypatch.chdir(chdir)
    rc = scanner.main(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_cli_missing_tree_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：缺失 tree 的 stderr 不得回显路径中的秘密（评审复现）。"""
    scanner = _load_scanner()
    rc, out, err = _cli(
        scanner, monkeypatch, capsys,
        "--tree", str(tmp_path / SECRET_DIR_NAME / "missing"),
    )
    assert rc == 2
    assert SECRET_DIR_NAME not in err
    assert "<redacted>" in err


def test_cli_missing_tree_json_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：json 模式下缺失 tree 同样脱敏。"""
    scanner = _load_scanner()
    rc, out, err = _cli(scanner, monkeypatch, capsys,
                        "--tree", str(tmp_path / SECRET_DIR_NAME / "missing"), "--report", "json")
    assert rc == 2
    assert SECRET_DIR_NAME not in err


def test_cli_missing_archive_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：缺失 archive 的 stderr 不得回显路径中的秘密。"""
    scanner = _load_scanner()
    rc, out, err = _cli(scanner, monkeypatch, capsys,
                        "--archive", str(tmp_path / SECRET_DIR_NAME / "gone.zip"))
    assert rc == 2
    assert SECRET_DIR_NAME not in err


def test_cli_bad_zip_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：损坏 zip 的错误消息不得回显路径中的秘密（评审复现）。"""
    scanner = _load_scanner()
    bad = tmp_path / SECRET_DIR_NAME
    bad.mkdir()
    (bad / "bad.zip").write_bytes(b"not a zip")
    rc, out, err = _cli(scanner, monkeypatch, capsys, "--archive", str(bad / "bad.zip"))
    assert rc == 2
    assert SECRET_DIR_NAME not in err
    assert "<redacted>" in err


def test_cli_tree_is_file_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：--tree 指向文件属于 usage 错误，路径脱敏。"""
    scanner = _load_scanner()
    secret = tmp_path / SECRET_DIR_NAME
    secret.mkdir()
    f = secret / "file.txt"
    f.write_text("x")
    rc, out, err = _cli(scanner, monkeypatch, capsys, "--tree", str(f))
    assert rc == 2
    assert SECRET_DIR_NAME not in err


def test_cli_permission_error_redacts_secret_path(tmp_path, monkeypatch, capsys):
    """P0-3：树内权限错误走 finding 报告（exit 1），target/路径同样脱敏。"""
    scanner = _load_scanner()
    secret = tmp_path / SECRET_DIR_NAME
    secret.mkdir()
    payload = secret / "payload.dat"
    payload.write_bytes(b"hello")

    real_open = open

    def denying_open(path, *args, **kwargs):
        if str(path) == str(payload):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", denying_open)
    rc, out, err = _cli(scanner, monkeypatch, capsys, "--tree", str(secret))
    assert rc == 1
    assert SECRET_DIR_NAME not in out
    assert SECRET_DIR_NAME not in err
    assert "unreadable" in out


def test_cli_argparse_error_redacts_secret_value(tmp_path):
    """P0-3：usage 错误回显 argv 时秘密值同样脱敏（黑盒 subprocess）。"""
    (tmp_path / "ok").mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCANNER_PATH), "--tree", str(tmp_path / "ok"),
         "--bogus", "sk-proj-ARGLEAK1234567890abcdef"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "ARGLEAK1234567890abcdef" not in proc.stderr


def test_cli_error_redacts_canary_in_path(tmp_path, monkeypatch, capsys):
    """P0-3：缺失路径内含 canary 时错误输出必须脱敏 canary。"""
    scanner = _load_scanner()
    rc, out, err = _cli(scanner, monkeypatch, capsys,
                        "--tree", str(tmp_path / CANARY / "missing"), "--canary", CANARY)
    assert rc == 2
    assert CANARY not in err
    assert "<redacted>" in err


# ── 复审 P1：超过 8192 字节 canary 跨 chunk 边界也必须命中 ────────────────


def test_long_canary_crossing_chunk_boundary_detected(tmp_path):
    """P1：9000 字节 canary 在首 chunk 留 8900 字节、次 chunk 留 100 字节必须命中。"""
    scanner = _load_scanner()
    root = tmp_path / "big"
    root.mkdir()
    big = root / "big.txt"
    canary = "C" * 9000
    with open(big, "wb") as fh:
        fh.write(b"x" * (1024 * 1024 - 8900) + canary.encode())
    result = scanner.scan_tree(root, canaries=(canary,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_long_canary_fully_inside_chunk_detected(tmp_path):
    """P1：>8192 字节 canary 完整落在单 chunk 内同样命中。"""
    scanner = _load_scanner()
    root = tmp_path / "big"
    root.mkdir()
    big = root / "big.txt"
    canary = "Q" * 9000
    big.write_bytes(canary.encode() + b"\n")
    result = scanner.scan_tree(root, canaries=(canary,))
    assert not result.ok
    assert any(f.type == "canary" for f in result.findings)


def test_oversized_canary_fails_closed_as_usage_error(tmp_path, monkeypatch, capsys):
    """P1：超过 _MAX_CANARY_BYTES 的 canary 必须 usage error（exit 2），不能返回 clean。"""
    scanner = _load_scanner()
    rc, out, err = _cli(
        scanner, monkeypatch, capsys,
        "--tree", str(tmp_path / "ok"),
        "--canary", "K" * (scanner._MAX_CANARY_BYTES + 1),
    )
    assert rc == 2
    assert "canary string exceeds" in err
    # 不把 canary 值回显
    assert "K" * 64 not in err

# ── 扫描器：staged source ──────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "clean.py").write_text("print('ok')\n", encoding="utf-8")
    _git("add", "clean.py", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


def test_scan_staged_flags_new_secret_lines(git_repo):
    scanner = _load_scanner()
    (git_repo / "secret.py").write_text(
        'os.environ["OPENAI_API_KEY"] = "sk-proj-REALKEY1234567890abcdef"\n',
        encoding="utf-8",
    )
    _git("add", "secret.py", cwd=git_repo)
    result = scanner.scan_staged(git_repo, canaries=())
    assert not result.ok
    assert any(f.type == "openai_api_key" for f in result.findings)
    report = scanner.report_text(result)
    assert "sk-proj-REALKEY1234567890abcdef" not in report


def test_scan_staged_clean_passes(git_repo):
    scanner = _load_scanner()
    (git_repo / "feature.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git("add", "feature.py", cwd=git_repo)
    result = scanner.scan_staged(git_repo, canaries=())
    assert result.ok


def test_scan_staged_flags_staged_auth_json_by_name(git_repo):
    scanner = _load_scanner()
    (git_repo / "auth.json").write_text('{"type":"chatgpt"}', encoding="utf-8")
    _git("add", "auth.json", cwd=git_repo)
    result = scanner.scan_staged(git_repo, canaries=())
    assert not result.ok
    assert any(f.type == "auth_file" for f in result.findings)


def test_scan_staged_flags_secret_filename(git_repo):
    scanner = _load_scanner()
    (git_repo / "sk-proj-REVIEWSECRET1234567890abcdef.txt").write_text("ok\n", encoding="utf-8")
    _git("add", ".", cwd=git_repo)
    result = scanner.scan_staged(git_repo, canaries=())
    assert not result.ok
    assert any(f.type == "openai_api_key" for f in result.findings)
    report = scanner.report_text(result)
    assert "REVIEWSECRET" not in report


# ── 扫描器：CLI 入口 ───────────────────────────────────────────────────────


def test_cli_exit_codes(clean_tree, bad_tree, tmp_path, monkeypatch, capsys):
    scanner = _load_scanner()
    monkeypatch.chdir(tmp_path)
    assert scanner.main(["--tree", str(clean_tree)]) == 0
    assert scanner.main(["--tree", str(bad_tree), "--canary", CANARY]) == 1
    assert scanner.main(["--tree", str(tmp_path / "missing")]) == 2
    opaque = tmp_path / "x.msi"
    opaque.write_bytes(CANARY.encode() + b"\x00\x01")
    assert scanner.main(["--archive", str(opaque)]) == 1  # opaque → fail
    out = capsys.readouterr().out
    for marker in SECRET_MARKERS:
        assert marker not in out


# ── workflow 静态契约（P0-3）：gate 必须先于一切上传/发布 ──────────────────


def test_release_workflow_secret_gate_before_uploads():
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1]

    with open(root / ".github" / "workflows" / "release-tauri.yml") as fh:
        wf = yaml.safe_load(fh)
    build = wf["jobs"]["build-tauri"]
    steps = build["steps"]
    names = [str(s.get("name", "")) for s in steps]
    gate_idx = next(i for i, n in enumerate(names) if "Secret gate" in n)
    # 上传/发布不得出现在 gate 之前
    for step in steps[:gate_idx]:
        uses = str(step.get("uses", "") or "")
        run = str(step.get("run", "") or "")
        assert "upload-artifact" not in uses, f"gate 前存在上传: {uses}"
        assert "gh release" not in run, f"gate 前存在 release 操作: {run}"
    # gate 之后确实存在上传（顺序成立）
    assert any("upload-artifact" in str(s.get("uses", "")) for s in steps[gate_idx:])
    # tauri-action 纯构建：不得内联创建/上传 Release
    tauri_step = next(s for s in steps if "tauri-action" in str(s.get("uses", "")))
    with_ = tauri_step.get("with", {}) or {}
    assert "tagName" not in with_
    assert "releaseName" not in with_
    assert "GITHUB_TOKEN" not in (tauri_step.get("env", {}) or {})
    # 发布在独立 job，且必须等待 gate 所在 build job
    assert "publish-release" in wf["jobs"]
    assert wf["jobs"]["publish-release"].get("needs") == "build-tauri"

    with open(root / ".github" / "workflows" / "build-installers.yml") as fh:
        wf2 = yaml.safe_load(fh)
    build2 = wf2["jobs"]["build"]
    names2 = [str(s.get("name", "")) for s in build2["steps"]]
    first_gate = next(i for i, n in enumerate(names2) if "Secret gate" in n)
    for step in build2["steps"][:first_gate]:
        uses = str(step.get("uses", "") or "")
        assert "upload-artifact" not in uses, f"gate 前存在上传: {uses}"
    assert any("upload-artifact" in str(s.get("uses", "")) for s in build2["steps"][first_gate:])
    # publish-release job 独立且在 build 之后
    assert "publish-release" in wf2["jobs"]
    assert wf2["jobs"]["publish-release"].get("needs") == "build"


# ── 构建脚本不复制用户 home/config/cache（静态守卫） ────────────────────


def test_build_scripts_do_not_copy_user_home_or_config():
    """构建脚本/打包 spec 不得递归复制用户 home/config/cache（D7 验收）。"""
    root = Path(__file__).resolve().parents[1]
    for rel in ("scripts/build_macos.sh", "scripts/build_windows.ps1", "openbrep.spec"):
        text = (root / rel).read_text(encoding="utf-8")
        lowered = text.lower()
        for op in ("cp -r", "copy-item", "robocopy", "xcopy", "copytree"):
            assert op not in lowered, f"{rel} 含 home 复制操作: {op}"
        for home_ref in (
            "$home",
            "${home}",
            "~/",
            "userprofile",
            "%userprofile%",
            "/users/",
            "/home/",
        ):
            assert home_ref not in lowered, f"{rel} 引用用户 home: {home_ref}"
        if rel == "openbrep.spec":
            assert 'root / "ui"' in text or '"ui"' in text


# ── 黑盒回归：初装 openai-codex 仍 signed out ──────────────────────────────

# 模拟真实 codex CLI 的 auth 行为：登录态存 CODEX_HOME/auth.json，环境只吃
# CODEX_HOME；绝不看 OPENAI_API_KEY / CODEX_ACCESS_TOKEN / 开发机 ~/.codex。
FAKE_APP_SERVER = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

_home = os.environ.get("CODEX_HOME", "")
_auth = Path(_home) / "auth.json" if _home else None


def _account():
    if _auth is None:
        return None
    try:
        data = json.loads(_auth.read_text())
    except Exception:
        return None
    if data.get("type") == "chatgpt":
        return {
            "type": "chatgpt",
            "email": str(data.get("email", "")),
            "planType": str(data.get("planType", "free")),
        }
    return None


for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    rid = msg.get("id")
    method = msg.get("method") or ""
    if method == "initialize":
        result = {
            "codexHome": _home,
            "envSeen": {
                "CODEX_ACCESS_TOKEN": os.environ.get("CODEX_ACCESS_TOKEN"),
                "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            },
        }
    elif method == "account/read":
        acct = _account()
        result = {"account": acct, "requiresOpenaiAuth": acct is None}
    elif method == "account/login/start":
        result = {"type": "chatgpt", "loginId": "bb-fake", "authUrl": "https://example.test/auth?state=fake"}
    elif method == "account/logout":
        if _auth is not None:
            try:
                _auth.unlink()
            except OSError:
                pass
        result = {}
    elif method == "model/list":
        acct = _account()
        result = (
            {
                "data": [
                    {"id": "gpt-5.6-luna", "model": "gpt-5.6-luna", "displayName": "GPT-5.6 Luna"}
                ]
            }
            if acct
            else {"data": [], "nextCursor": None}
        )
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()
"""


@pytest.fixture()
def fake_codex_server(tmp_path: Path) -> Path:
    server = tmp_path / "app-server"
    server.write_text(FAKE_APP_SERVER, encoding="utf-8")
    server.chmod(server.stat().st_mode | stat.S_IXUSR)
    return server


def _plant_developer_codex(dev_home: Path, token: str = "sk-dev-secret-token-1234567890") -> Path:
    auth = dev_home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(
        json.dumps({"type": "chatgpt", "email": "dev@example.com", "accessToken": token}),
        encoding="utf-8",
    )
    return auth


def test_fresh_install_signed_out_despite_canary_env_and_dev_codex_home(
    tmp_path, fake_codex_server, monkeypatch
):
    """黑盒：外部环境有 canary token / OPENAI_API_KEY / 开发机 ~/.codex 登录态，
    初装（隔离 CODEX_HOME）仍 signed_out，且开发机 auth 文件分毫未动。"""
    from openbrep.codex.provider import CodexProvider

    dev_home = tmp_path / "dev-home"
    dev_auth = _plant_developer_codex(dev_home)
    dev_auth_before = dev_auth.read_bytes()

    monkeypatch.setenv("HOME", str(dev_home))
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "sk-canary-abcdef1234567890")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-canary-abcdef1234567890")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com")

    isolated = tmp_path / "fresh-install" / "codex"
    provider = CodexProvider(
        codex_home=isolated, codex_binary=str(fake_codex_server), cli_available=True
    )
    try:
        status = provider.status(refresh=True)
    finally:
        provider.close()

    assert status["state"] == "signed_out"
    assert status["connected"] is False
    assert status.get("codex_available") is True
    # 隔离 HOME 无残留登录态
    assert not (isolated / "auth.json").exists()
    # 开发机 auth 文件未被读取/修改（字节级一致）
    assert dev_auth.read_bytes() == dev_auth_before


def test_subprocess_codex_home_never_points_at_dev_codex(tmp_path, fake_codex_server, monkeypatch):
    """黑盒：transport 子进程实际收到的 CODEX_HOME 是隔离目录，绝不继承
    开发机 ~/.codex；同时外部 canary env 确实存在（证明被忽略而非被抹除）。"""
    from openbrep.codex.app_server import CodexAppServerClient

    dev_home = tmp_path / "dev-home"
    _plant_developer_codex(dev_home)
    monkeypatch.setenv("HOME", str(dev_home))
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "sk-canary-abcdef1234567890")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-canary-abcdef1234567890")

    isolated = tmp_path / "iso"
    client = CodexAppServerClient(
        codex_binary=str(fake_codex_server),
        codex_home=isolated,
    )
    try:
        init = client.start()
    finally:
        client.close()

    assert init["codexHome"] == str(isolated)
    assert init["codexHome"] != str(dev_home / ".codex")
    assert not str(init["codexHome"]).endswith("/.codex")
    # canary env 确实存在于子进程环境（黑盒观察），但没有导致登录
    assert init["envSeen"]["CODEX_ACCESS_TOKEN"] == "sk-canary-abcdef1234567890"
    assert init["envSeen"]["OPENAI_API_KEY"] == "sk-proj-canary-abcdef1234567890"
    # 开发机 auth 未被动过
    assert (dev_home / ".codex" / "auth.json").exists()


def test_two_isolated_codex_homes_are_mutually_invisible(tmp_path, fake_codex_server, monkeypatch):
    """两个隔离 CODEX_HOME 的状态互不可见：H1 有登录态，H2 初装仍是 signed_out。"""
    from openbrep.codex.provider import CodexProvider

    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    home_a = tmp_path / "install-a" / "codex"
    home_b = tmp_path / "install-b" / "codex"
    home_a.mkdir(parents=True)
    # H1 已有登录态（模拟 A 机器/实例登录过）
    (home_a / "auth.json").write_text(
        json.dumps({"type": "chatgpt", "email": "alice@example.com", "planType": "pro"}),
        encoding="utf-8",
    )

    provider_a = CodexProvider(
        codex_home=home_a, codex_binary=str(fake_codex_server), cli_available=True
    )
    try:
        status_a = provider_a.status(refresh=True)
    finally:
        provider_a.close()
    assert status_a["state"] == "signed_in"
    assert status_a["account"]["email_masked"] == "al***@example.com"

    provider_b = CodexProvider(
        codex_home=home_b, codex_binary=str(fake_codex_server), cli_available=True
    )
    try:
        status_b = provider_b.status(refresh=True)
    finally:
        provider_b.close()
    # B 实例看不到 A 的登录态
    assert status_b["state"] == "signed_out"
    assert status_b["connected"] is False
    # B 的初装行为没有把 A 的 auth 复制进自己的 HOME
    assert not (home_b / "auth.json").exists()
    # A 的 auth 文件保持原样
    assert json.loads((home_a / "auth.json").read_text())["email"] == "alice@example.com"


def test_login_in_one_home_does_not_leak_to_another(tmp_path, fake_codex_server, monkeypatch):
    """通过 provider 登录 H1 后，H2 初装仍是 signed_out（登录态以 CODEX_HOME 为界）。"""
    from openbrep.codex.app_server import CodexAppServerClient
    from openbrep.codex.provider import CodexProvider

    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    # 模拟 H1 完成登录：app-server 把 auth.json 写进自己的 CODEX_HOME
    client_a = CodexAppServerClient(
        codex_binary=str(fake_codex_server),
        codex_home=home_a,
    )
    try:
        client_a.start()
        client_a.account_login_start_chatgpt()
    finally:
        client_a.close()
    # 真实 codex CLI 登录后会写 auth.json；fake 不写，测试手动模拟该产物
    (home_a / "auth.json").write_text(
        json.dumps({"type": "chatgpt", "email": "bob@example.com", "planType": "free"}),
        encoding="utf-8",
    )

    provider_b = CodexProvider(
        codex_home=home_b, codex_binary=str(fake_codex_server), cli_available=True
    )
    try:
        status_b = provider_b.status(refresh=True)
    finally:
        provider_b.close()
    assert status_b["state"] == "signed_out"
    assert not (home_b / "auth.json").exists()


# ── 测试自身不依赖真实账号/网络/本机登录态 ─────────────────────────────────


def test_tests_are_hermetic(fake_codex_server, tmp_path):
    """本文件所有黑盒路径只与 fake app-server 通信：断言子进程只连过 fake。"""
    from openbrep.codex.app_server import CodexAppServerClient

    # 子进程 argv 第一项必须是 fake server 脚本（无网络、无真实 codex）
    home = tmp_path / "h"
    client = CodexAppServerClient(codex_binary=str(fake_codex_server), codex_home=home)
    try:
        client.start()
        assert client.transport._proc is not None
        argv = client.transport._proc.args
    finally:
        client.close()
    assert argv[0] == str(fake_codex_server)
    assert "app-server" in argv
