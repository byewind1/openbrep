"""D7: BYOA 与发布秘密泄漏门禁 —— 黑盒安全测试（全部离线、无真实账号/网络）。

覆盖两个层面：

1. ``scripts/secret_scan.py`` 发布秘密门禁扫描器：
   - 注入 canary 的坏 artifact 必须使 gate 失败，正常 artifact 通过；
   - 报告绝不回显完整秘密，只报类型和文件位置；
   - 凭据文件（auth.json/.env/…）只按名报，内容绝不读取；
   - zip 归档、dmg 占位、staged source 均按目标规则处理。

2. 黑盒回归：外部环境即使存在 canary token / OPENAI_API_KEY /
   开发机 ~/.codex/auth.json，初装 openai-codex（隔离 CODEX_HOME）仍 signed out；
   两个隔离 CODEX_HOME 的状态互不可见。
   子进程 fake app-server 模拟真实 codex CLI 的 auth.json 行为（auth 状态存在
   CODEX_HOME 下），transport/子进程环境注入是真实路径。
"""

from __future__ import annotations

import importlib.util
import json
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
    "plain-secret-value",
    "obr-canary-abc123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456",
)


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


# ── 扫描器：名称级（凭据文件只按名报，不读内容） ──────────────────────────


def test_scan_tree_flags_auth_json_by_name_without_echoing_value(bad_tree):
    scanner = _load_scanner()
    result = scanner.scan_tree(bad_tree)
    auth = [f for f in result.findings if f.type == "auth_file"]
    assert auth and auth[0].file.endswith(".codex/auth.json")
    assert not result.ok
    report = scanner.report_text(result)
    assert "sk-REAL-SECRET-TOKEN-123456" not in report  # 内容未回显 = 未读取内容


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
    # 秘密脱敏模块源码（随包镜像）豁免内容级，但不豁免其他文件
    assert scanner.content_scan_applies("openbrep/codex/redact.py") is False
    assert scanner.content_scan_applies("some/other/redact.py") is True


def test_secret_redaction_module_source_does_not_false_positive(tmp_path):
    """随包镜像的 openbrep/codex/redact.py 源码含 Bearer 示例词，不得误报；
    同目录/同树的真实秘密仍必须命中。"""
    scanner = _load_scanner()
    root = tmp_path / "pkg"
    (root / "openbrep" / "codex").mkdir(parents=True)
    (root / "openbrep" / "codex" / "redact.py").write_text(
        '# 裸 Bearer token（无 Authorization 前缀时）\n'
        '# "Authorization: Bearer plain-secret-value" 等冒号写法\n'
        '_BEARER_RE = re.compile(r"(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]{6,}")\n',
        encoding="utf-8",
    )
    assert scanner.scan_tree(root).ok
    # 真实秘密（同树其他文件）不受豁免影响
    (root / ".env").write_text(
        "OPENAI_API_KEY=" + "sk-proj-REALKEY1234567890abcdef\n", encoding="utf-8"
    )
    result = scanner.scan_tree(root)
    assert not result.ok
    assert any(f.type == "env_file" for f in result.findings)


# ── 扫描器：内容级 ─────────────────────────────────────────────────────────


def test_scan_text_detects_bearer_jwt_and_sk_keys():
    scanner = _load_scanner()
    text = (
        'h = "Authorization: Bearer plain-secret-value"\n'
        "jwt = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456\n"
        'key = "sk-proj-REALKEY1234567890abcdef"\n'
    )
    findings = scanner.scan_text("x.py", text, ())
    types = {f.type for f in findings}
    assert "bearer" in types
    assert "jwt" in types
    assert "openai_api_key" in types


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


# ── 扫描器：canary 注入 → gate 失败 ────────────────────────────────────────


def test_canary_injected_artifact_fails_gate(clean_tree, tmp_path):
    scanner = _load_scanner()
    (clean_tree / "leak.js").write_text("const canary = 'obr-canary-abc123';\n", encoding="utf-8")
    result = scanner.scan_tree(clean_tree, canaries=("obr-canary-abc123",))
    assert not result.ok
    canary = [f for f in result.findings if f.type == "canary"]
    assert canary and canary[0].file == "leak.js"
    assert "obr-canary-abc123" not in scanner.report_text(result)  # canary 也不回显


def test_clean_artifact_passes_and_prints_no_secrets(clean_tree):
    scanner = _load_scanner()
    result = scanner.scan_tree(clean_tree, canaries=("obr-canary-abc123",))
    assert result.ok
    assert result.findings == []
    report = scanner.report_text(result)
    assert "SECRET GATE PASS" in report
    for marker in SECRET_MARKERS:
        assert marker not in report


def test_report_never_echoes_any_secret_value(bad_tree):
    scanner = _load_scanner()
    result = scanner.scan_tree(bad_tree, canaries=("obr-canary-abc123",))
    text_report = scanner.report_text(result)
    json_report = scanner.report_json(result)
    for marker in SECRET_MARKERS:
        assert marker not in text_report
        assert marker not in json_report
    # 报告只含类型与位置
    assert "auth_file" in json_report
    assert ".codex/auth.json" in json_report


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
    result = scanner.scan_archive(zpath, canaries=("obr-canary-abc123",))
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
    result = scanner.scan_archive(zpath, canaries=("obr-canary-abc123",))
    assert result.ok


def test_opaque_archive_dmg_is_info_not_failure(tmp_path):
    scanner = _load_scanner()
    dmg = tmp_path / "OpenBrep.dmg"
    dmg.write_bytes(b"\x00" * 64)
    result = scanner.scan_archive(dmg)
    assert result.ok  # dmg 不可解析 ≠ gate 失败
    assert any(f.type == "opaque_archive" and f.severity == "info" for f in result.findings)


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


# ── 扫描器：CLI 入口 ───────────────────────────────────────────────────────


def test_cli_exit_codes(clean_tree, bad_tree, tmp_path, monkeypatch, capsys):
    scanner = _load_scanner()
    monkeypatch.chdir(tmp_path)
    assert scanner.main(["--tree", str(clean_tree)]) == 0
    assert scanner.main(["--tree", str(bad_tree), "--canary", "obr-canary-abc123"]) == 1
    assert scanner.main(["--tree", str(tmp_path / "missing")]) == 2
    out = capsys.readouterr().out
    for marker in SECRET_MARKERS:
        assert marker not in out


# ── 构建脚本不复制用户 home/config/cache（静态守卫） ────────────────────


def test_build_scripts_do_not_copy_user_home_or_config(tmp_path):
    """构建脚本/打包 spec 不得递归复制用户 home/config/cache（D7 验收）。

    静态守卫：未来若有人往打包脚本里加 home 复制，这里立刻红灯。
    """
    root = Path(__file__).resolve().parents[1]
    for rel in ("scripts/build_macos.sh", "scripts/build_windows.ps1", "openbrep.spec"):
        text = (root / rel).read_text(encoding="utf-8")
        lowered = text.lower()
        # 复制类操作
        for op in ("cp -r", "cp -r ", "copy-item", "robocopy", "xcopy", "copytree"):
            assert op not in lowered, f"{rel} 含 home 复制操作: {op}"
        # home 展开引用
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
        # openbrep.spec 的 datas 必须只引用仓库相对路径
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
    server.chmod(server.stat().st_mode | 0o755)
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
    # 开发机 auth 文件未被读取/修改（内容、字节级一致）
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
