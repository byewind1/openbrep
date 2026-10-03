"""配置原子提交合同（卡00）：expected_revision 冲突检测 + 副本验证 + 原子替换 + 发布。

为 provider 设置写路由（创建/更新/删除/导入）提供共用的提交原语。合同要点
（评审 §2 / §11 合同 1+2）：

- 冲突检测：调用方传入 expected_revision，提交时与磁盘指纹重算比对；
  不一致返回 ``config_modified``，不写盘、不改内存（保护外部编辑器并发修改）。
- 副本验证 → 原子替换 → 再发布：变更只在 deep copy 上执行与序列化，写临时
  文件后 ``os.replace`` 原子替换；成功后才通过 ``on_committed`` 发布运行时状态。
  写盘失败时内存不得先行生效。
- 字段覆盖：mutate 只改明确提交的字段，其余（temperature/extra_body/retry 等）
  随副本原样保留。
- key 三态：请求对象 ``api_key`` 缺省 = 保持现有；显式 "" = 清除；非空 = 替换。
- 端点显式空：``api`` 缺省 = 保持/允许顶层兜底（``_explicit_base`` 语义）；
  显式空串 = 显式无端点（绝不回退顶层 api_base）。

本原语只服务本批新路由；既有路由（update_llm_model_only 等）不在改造范围。
注释级无损保存不在本卡范围（save() 维持现状的规范化保存语义）。
"""

from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from openbrep.config import GDLAgentConfig


class ConfigCommitError(Exception):
    """mutator 校验失败：code 为稳定错误码，message 为可读文案。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def file_revision(config_path: str | Path) -> str:
    """config.toml 的廉价指纹（mtime_ns:size，与 settings_service.config_revision 同源）。

    文件不存在时返回 "missing"（与既有 config_revision 行为一致）。
    """
    path = Path(config_path)
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def commit_config_change(
    config: GDLAgentConfig,
    config_path: str | Path,
    *,
    expected_revision: str,
    mutate: Callable[[GDLAgentConfig], None],
    on_committed: Callable[[GDLAgentConfig], None] | None = None,
) -> dict[str, Any]:
    """在配置副本上完成变更与序列化，原子替换落盘，成功后才发布内存状态。

    - ``expected_revision`` 与磁盘当前指纹不一致 → ``{ok: False, code:
      "config_modified", current_revision}``；零写入、零内存变更。
    - ``mutate(committed_copy)`` 抛 ``ConfigCommitError`` → ``{ok: False, code,
      error}``，零副作用；抛 ``ValueError``（解析层校验，如未知 api_mode）映射为
      ``invalid_request``。
    - 序列化写同目录临时文件后 ``os.replace`` 原子替换；序列化/替换失败时清理
      临时文件并返回错误，原文件与内存都不变。
    - 全部成功后调用 ``on_committed(committed_copy)``（调用方借此把已落盘的
      规范化配置发布到 session）；未提供则跳过。
    """
    path = Path(config_path)
    current = file_revision(path)
    if str(expected_revision) != current:
        return {
            "ok": False,
            "code": "config_modified",
            "error": "配置文件已被外部修改，请刷新设置后重试（当前草稿已保留）。",
            "current_revision": current,
        }

    committed = copy.deepcopy(config)
    try:
        mutate(committed)
    except ConfigCommitError as exc:
        return {"ok": False, "code": exc.code, "error": exc.message}
    except ValueError as exc:
        return {"ok": False, "code": "invalid_request", "error": str(exc)}

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".commit-", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        committed.save(str(tmp_path))
    except Exception as exc:  # noqa: BLE001 —— 序列化失败必须兜住，保持磁盘与内存原样
        tmp_path.unlink(missing_ok=True)
        return {"ok": False, "code": "config_serialize_failed", "error": f"配置序列化失败: {exc}"}
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        return {"ok": False, "code": "config_write_failed", "error": f"配置写盘失败: {exc}"}

    if on_committed is not None:
        on_committed(committed)
    return {"ok": True, "revision": file_revision(path), "config": committed}


def resolve_api_key_update(current: Any, request: Mapping[str, Any]) -> str:
    """key 三态（评审 §11 合同 2）：api_key 缺省 = 保持现有；显式 "" = 清除；非空 = 替换。

    空字符串不再身兼"保持"与"清除"两义：请求对象里没有 ``api_key`` 键时才保持。
    """
    if "api_key" not in request:
        return str(current or "")
    return str(request.get("api_key") or "").strip()


def apply_endpoint_update(entry: dict, request: Mapping[str, Any]) -> None:
    """端点字段覆盖（``_explicit_base`` 语义，config.py:389-425）。

    - 请求缺省 ``api``：不改条目（新建条目则无 api 键 → 允许顶层 api_base 兜底；
      已有条目保持原 api 与显式空语义）。
    - 请求显式提供 ``api``（含空串）：写入条目并标记 ``_explicit_base=True``——
      空串从此是"显式无端点"，绝不回退顶层。
    """
    if "api" not in request:
        return
    api = str(request.get("api") or "").strip()
    entry["api"] = api
    entry["base_url"] = api
    entry["_explicit_base"] = True
