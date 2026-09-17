"""双入口 Codex 链路（2026-09-17）：本机配置入口 + OpenBrep 托管入口。

背景：OpenBrep 的 Codex 接入原先把 ``CODEX_HOME`` 隔离到
``~/.openbrep/codex``——好处是登录态归 OpenBrep 所有，代价是打包版（dmg）
读不到用户终端里已经配好的 Codex（模型/provider 全在标准 ``~/.codex``），
表现为「界面里连不上 / 没有模型」。

双入口把取舍交还给用户，两条路并存、互不替代：

- ``local``（默认，推荐）：跟随 ``CODEX_HOME``（未设置时用标准 ``~/.codex``），
  只读解析其中的模型/provider 配置，**不管理认证**。用户的 ``codex login``、
  provider 切换（cc-switch 等）、key 管理全留在 Codex CLI 那一侧。
- ``managed``：OpenBrep 自己托管 ChatGPT 登录（私有 ``~/.openbrep/codex``），
  保持既有行为不变，作为兼容路径继续存在。

本模块只做「入口 → home / 标签 / 锁位置」的纯解析：不读配置内容（那是
``local_config.py`` 的事），不写任何文件，也不接触凭据。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# 入口标识（写进 OpenBrep 的 config.toml，必须稳定）
ENTRY_LOCAL = "local"
ENTRY_MANAGED = "managed"
CODEX_ENTRIES: tuple[str, str] = (ENTRY_LOCAL, ENTRY_MANAGED)
# 默认入口 = 托管入口（保持既有配置行为不变）。UI 把本机配置入口标成「推荐」
# 并支持一键切换，但不悄悄改既有用户的链路：全新用户本机没有 Codex 配置，
# 只有托管入口能用。
DEFAULT_CODEX_ENTRY = ENTRY_MANAGED

# 本机配置入口跟随 Codex CLI 自己的环境变量；托管入口只认 OpenBrep 私有覆盖。
LOCAL_HOME_ENV = "CODEX_HOME"
MANAGED_HOME_ENV = "OPENBREP_CODEX_HOME"
MANAGED_HOME_DIRNAME = ".openbrep"
MANAGED_HOME_LEAF = "codex"

# 认证来源标签（前端状态卡展示「这条链路用的是哪个 home、哪个认证来源」）
AUTH_SOURCE_CODEX_CONFIG = "codex_config"
AUTH_SOURCE_OPENBREP_MANAGED = "openbrep_managed"

# home 来源的**符号化**表示。状态卡要写清「用的是哪个 home」，但 D1 契约禁止
# 任何 auth 路径（含 `.codex` 字面量）离开 openbrep/codex —— 所以后端只回枚举，
# 由前端渲染成 ``~/.codex`` / ``CODEX_HOME`` / ``~/.openbrep/codex`` 文案。
HOME_KIND_USER_DEFAULT = "user_default"
HOME_KIND_ENV_OVERRIDE = "env_override"
HOME_KIND_MANAGED = "managed"
HOME_KIND_CUSTOM = "custom"

# app-server 互斥锁目录：绝不再往 Codex home 里写（``local`` 入口必须只读）
LOCK_RUN_DIRNAME = (".openbrep", "run")
LOCK_DIR_ENV = "OPENBREP_CODEX_LOCK_DIR"


def normalize_codex_entry(value: object) -> str:
    """任意输入 → 合法入口标识；未知值一律按默认入口（fail safe，不新增入口）。"""
    text = str(value or "").strip().lower()
    return text if text in CODEX_ENTRIES else DEFAULT_CODEX_ENTRY


def is_codex_entry(value: object) -> bool:
    """严格判定（保存路径用）：只有两个枚举值算合法。"""
    return str(value or "").strip().lower() in CODEX_ENTRIES


def local_codex_home() -> Path:
    """本机配置入口的 home：``CODEX_HOME`` 优先，否则标准 ``~/.codex``。"""
    configured = os.environ.get(LOCAL_HOME_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def managed_codex_home() -> Path:
    """托管入口的 home：OpenBrep 私有目录，**不**跟随 ``CODEX_HOME``。

    跟随 ``CODEX_HOME`` 会让两个入口指向同一份 home（互抢 app-server 锁、
    认证归属含混），因此托管入口只认 ``OPENBREP_CODEX_HOME`` 这一个显式覆盖。
    """
    configured = os.environ.get(MANAGED_HOME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / MANAGED_HOME_DIRNAME / MANAGED_HOME_LEAF


def codex_home_for_entry(entry: object) -> Path:
    return local_codex_home() if normalize_codex_entry(entry) == ENTRY_LOCAL else managed_codex_home()


def entry_auth_source(entry: object) -> str:
    return (
        AUTH_SOURCE_CODEX_CONFIG
        if normalize_codex_entry(entry) == ENTRY_LOCAL
        else AUTH_SOURCE_OPENBREP_MANAGED
    )


def entry_label(entry: object) -> str:
    """稳定中文标签（后端只回枚举 + 标签，前端负责本地化展示）。"""
    if normalize_codex_entry(entry) == ENTRY_LOCAL:
        return "Codex 本机配置"
    return "ChatGPT 账户登录（OpenBrep 托管）"


def codex_home_kind(home: str | Path, entry: object) -> str:
    """home 的来源枚举：用户默认位置 / CODEX_HOME 覆盖 / 托管 / 自定义。

    只回符号，不回路径——状态卡需要的「这条链路读的是哪里」由前端按枚举渲染，
    auth 目录字面量永不出模块（D1）。
    """
    if normalize_codex_entry(entry) == ENTRY_MANAGED:
        return HOME_KIND_MANAGED
    if os.environ.get(LOCAL_HOME_ENV, "").strip():
        return HOME_KIND_ENV_OVERRIDE
    try:
        target = Path(home).expanduser()
    except (TypeError, ValueError):  # pragma: no cover —— 异常输入
        return HOME_KIND_CUSTOM
    return HOME_KIND_USER_DEFAULT if target == local_codex_home() else HOME_KIND_CUSTOM


def lock_path_for_home(home: str | Path, *, run_dir: str | Path | None = None) -> Path:
    """app-server 互斥锁位置：OpenBrep 自己的 run 目录，按 home 取稳定摘要。

    锁文件必须落在 Codex home 之外——``local`` 入口承诺对用户的 ``~/.codex``
    零写入，哪怕是一把空锁文件也不行。``run_dir`` 参数 > ``OPENBREP_CODEX_LOCK_DIR``
    环境变量 > ``~/.openbrep/run``（测试用环境变量指向临时目录，绝不污染开发机）。
    """
    if run_dir is not None:
        base = Path(run_dir).expanduser()
    else:
        configured = os.environ.get(LOCK_DIR_ENV, "").strip()
        base = (
            Path(configured).expanduser()
            if configured
            else Path.home().joinpath(*LOCK_RUN_DIRNAME)
        )
    digest = hashlib.sha256(str(Path(home).expanduser()).encode("utf-8")).hexdigest()[:12]
    return base / f"codex-app-server-{digest}.lock"
