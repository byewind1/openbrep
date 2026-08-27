"""Fake Codex app-server for unit tests（D1 + D2）。

Speaks the same wire protocol as `codex app-server` (newline-delimited JSON-RPC
over stdio). The client under test cannot tell it apart from the real binary,
which is exactly the point: framing, id association, notifications, EOF-close,
crash/restart, device-code, cancellation, rate-limits and CODEX_HOME env
wiring are exercised for real.

D2 failure/edge modes are driven by environment variables:

- FAKE_CODEX_VERSION          initialize.userAgent 版本（默认 0.147.0；
                              "old" → 0.50.0；"garbage" → 无版本号）
- FAKE_CODEX_LOGIN_TYPE       account/login/start 返回类型：
                              chatgpt（默认）/ chatgptDeviceCode / apiKey /
                              chatgptAuthTokens / unexpected
- FAKE_CODEX_LOGIN_CANCEL     account/login/cancel 返回 status：canceled/notFound
- FAKE_CODEX_RATE_LIMITS      account/rateLimits/read：
                              ok（默认未登录错误）/ reached / normal / unauth
- FAKE_CODEX_GARBAGE_LINE     启动时先输出一行非 JSON（协议污染测试）
- FAKE_CODEX_STDERR_SECRET    启动时向 stderr 输出一行秘密（脱敏测试）
- FAKE_CODEX_DELAY_RESPONSE_SECONDS  响应前 sleep（超时/迟到响应测试）
- FAKE_CODEX_CRASH_IMMEDIATELY      启动即退出非零（崩溃检测）
- FAKE_CODEX_CRASH_AFTER_REQUESTS   处理 N 个请求后崩溃（含 stderr 诊断）
- FAKE_CODEX_NOTIFY           每个请求响应前发送一条通知
                              （loginCompleted / rateLimitsUpdated / accountUpdated）
- FAKE_CODEX_SPAWN_CHILD_PID_FILE   启动时 spawn 一个 sleep 子进程并把 PID
                              写入该文件（进程组清理测试）

D3 turn 协议（thread/start + turn/start + 通知流，对齐 0.147.0
`codex app-server generate-ts` 绑定；`FAKE_CODEX_TURN=1` 才启用）：
- FAKE_CODEX_TURN                  启用 turn 方法（thread/start、turn/start、
                              turn/interrupt、thread/delete）
- FAKE_CODEX_TURN_FINAL_TEXT       最终 agent message 文本（默认测试文案）
- FAKE_CODEX_TURN_NO_FINAL         turn 完成但没有 agentMessage item（无 final）
- FAKE_CODEX_TURN_COMMENTARY_ONLY  只发 phase=commentary 的中间消息
- FAKE_CODEX_TURN_EMPTY_FINAL      最终 agent message 文本为空（截断语义）
- FAKE_CODEX_TURN_ERROR_CANARY     turn 级 error 通知（message 含该 canary）
- FAKE_CODEX_TURN_QUOTA_CANARY     error 通知 codexErrorInfo=usageLimitExceeded
                              且 message 含该 canary（quota 语义）
- FAKE_CODEX_TURN_HANG             turn/start 后永不完成（等 turn/interrupt）
- FAKE_CODEX_TURN_MALFORMED        正常通知前先发畸形帧（半帧 JSON/坏形状）
- FAKE_CODEX_TURN_TOOL_NOISE       在 agentMessage 前后插入 commandExecution /
                              fileChange item（工具面探测：客户端必须忽略）
- FAKE_CODEX_TURN_FORBIDDEN_CHECK  turn/start 参数含工具/危险键时返回错误
                              （sandbox 反证：D3 参数面无工具）

D4 CREATE 隔离反证探针：
- FAKE_CODEX_CWD_LOG=<path>      每次 thread/start 与 turn/start 收到的 cwd
                              追加写入该文件（一行一个 JSON：method + cwd +
                              写 canary 结果）——测试断言 app-server 只见过
                              自己的临时 cwd，从未收到真实 HSF/工作区路径。
- FAKE_CODEX_WRITE_PROBE=<path>  对每个收到的 cwd 与该路径尝试写 canary；
                              测试把真实 HSF 目录设为只读，probe 写失败即证明
                              app-server 对该路径无写权限（OS 级隔离反证）。

D5 图片输入探针（授权边界反证）：
- FAKE_CODEX_TURN_INPUT_LOG=<path>  每次 turn/start 收到的 input 全文 + 每个
                              localImage 条目的独立校验（path 是否在该 thread
                              cwd 内 / 文件是否存在 / sha256）追加写入该文件。
                              测试据此断言：app-server 只收到物化进临时 cwd 的
                              授权图片，任何用户提供的路径/canary 零到达。
- FAKE_CODEX_REJECT_ESCAPING_IMAGE  越界 localImage（path 不在 thread cwd 内）
                              直接以 JSON-RPC 错误拒绝启动 turn（app-server 侧
                              fail closed 反证；正常客户端永不触发）。
- FAKE_CODEX_TURN_FINAL_TEXTS=<path>  每行一个 JSON 编码字符串（json.dumps）：
                              每个 turn/start 的 final 文本先进先出消费，耗尽后回退
                              FAKE_CODEX_TURN_FINAL_TEXT。用于跑完整 CREATE
                              多 turn 序列（提取→规划→生成，可含多行 [FILE:] 文本）。

Run as: python fake_codex_app_server.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _notify_payload(name: str) -> dict:
    if name == "loginCompleted":
        return {
            "method": "account/login/completed",
            "params": {"loginId": "fake-login-id", "success": True, "error": None},
        }
    if name == "rateLimitsUpdated":
        return {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "limitId": "codex",
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                    "planType": "pro",
                    "primary": {"usedPercent": 12, "windowDurationMins": 360, "resetsAt": 0},
                }
            },
        }
    if name == "accountUpdated":
        return {
            "method": "account/updated",
            "params": {"authMode": "chatgpt", "planType": "pro"},
        }
    return {"method": "test/notification", "params": {"value": 1}}


# ── D3 turn 协议状态 ─────────────────────────────────────────────
# thread/start + turn/start + 通知流（对齐 `codex app-server generate-ts` 绑定）。
_threads: dict[str, dict] = {}
_turns: dict[str, dict] = {}
_turn_seq = 0
_msg_seq = 0
# FAKE_CODEX_TURN_HANG 只对第一个 turn/start 生效（消费后后续 turn 正常完成）。
# 用于「并发交错 + 超时后可继续下一请求」的确定性测试。
_hang_active = True


def _next_turn_id() -> str:
    global _turn_seq
    _turn_seq += 1
    return f"fake-turn-{_turn_seq}"


def _next_msg_id() -> str:
    global _msg_seq
    _msg_seq += 1
    return f"fake-msg-{_msg_seq}"


# ── D6：model/list 的 supportedReasoningEfforts（Fixed 模式 effort 目录）─────
# 默认：gpt-5.6-luna 支持 low/medium/high（默认 medium）；
# gpt-5.6-terra 只支持 medium/high（默认 high）——测试用 terra 验证
# 「旧 effort 残留/luna 独有 effort」的拒绝路径。
# FAKE_CODEX_MODEL_EFFORTS_JSON 可整体覆盖模型 effort 目录（见 _model_efforts_config）。
_DEFAULT_MODEL_EFFORTS = {
    "gpt-5.6-luna": {
        "efforts": [("low", "Fastest"), ("medium", "Balanced"), ("high", "Deep")],
        "default": "medium",
    },
    "gpt-5.6-terra": {
        "efforts": [("medium", "Balanced"), ("high", "Deep")],
        "default": "high",
    },
}


def _model_efforts_config() -> dict:
    raw = os.environ.get("FAKE_CODEX_MODEL_EFFORTS_JSON", "")
    if not raw:
        return _DEFAULT_MODEL_EFFORTS
    try:
        parsed = json.loads(raw)
    except ValueError:
        return _DEFAULT_MODEL_EFFORTS
    if not isinstance(parsed, dict):
        return _DEFAULT_MODEL_EFFORTS
    return parsed


def _model_list_response() -> dict:
    efforts_cfg = _model_efforts_config()
    entries = []
    for model_id in ("gpt-5.6-luna", "gpt-5.6-terra"):
        cfg = efforts_cfg.get(model_id) or {}
        efforts_raw = cfg.get("efforts", [])
        supported = []
        for item in efforts_raw:
            if isinstance(item, (list, tuple)) and len(item) >= 1:
                effort = str(item[0])
                desc = str(item[1]) if len(item) > 1 else ""
            elif isinstance(item, dict):
                effort = str(item.get("effort") or "")
                desc = str(item.get("description") or "")
            else:
                effort = str(item)
                desc = ""
            supported.append({"reasoningEffort": effort, "description": desc})
        default = str(cfg.get("default") or "")
        entries.append(
            {
                "id": model_id,
                "model": model_id,
                "displayName": "GPT-5.6 Luna" if model_id == "gpt-5.6-luna" else "GPT-5.6 Terra",
                "hidden": False,
                "modelSpecialty": None,
                "supportedReasoningEfforts": supported,
                "defaultReasoningEffort": default or None,
            }
        )
    return {"data": entries, "nextCursor": None}


# ── D6：turn 参数全量记录（fallback 反证 / effective 对账）────────────────
# FAKE_CODEX_TURN_PARAMS_LOG=<path>：每次 thread/start 与 turn/start 的完整
# 参数追加写入该文件（一行一个 JSON）。测试据此断言：Fixed 失败后没有指向
# 其他模型/provider 的请求；任务结果元数据里的 effective model/effort 与
# app-server 实收完全一致。
_TURN_PARAMS_LOG_PATH = os.environ.get("FAKE_CODEX_TURN_PARAMS_LOG", "")


def _record_turn_params(method: str, params: dict) -> None:
    if not _TURN_PARAMS_LOG_PATH:
        return
    try:
        with open(_TURN_PARAMS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event": "params", "method": method, "params": params},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


def _effort_supported_for_thread(thread_id: str, effort: str) -> bool:
    """turn/start 的 effort 是否属于该 thread 所用模型的支持集合。"""
    if not effort:
        return True
    thread = _threads.get(thread_id)
    if not isinstance(thread, dict):
        return False
    model_id = str(thread.get("model") or "")
    if not model_id:
        return False
    if "/" in model_id:
        model_id = model_id.rsplit("/", 1)[-1]
    cfg = _model_efforts_config().get(model_id) or {}
    efforts = [str(e[0]) for e in cfg.get("efforts", [])]
    return effort in efforts


def _fake_turn(turn_id: str, status: str = "inProgress", error=None) -> dict:
    return {
        "id": turn_id,
        "items": [],
        "itemsView": "notLoaded",
        "status": status,
        "error": error,
        "startedAt": 1786800000,
        "completedAt": None,
        "durationMs": None,
    }


def _agent_message_item(msg_id: str, text: str, phase) -> dict:
    return {
        "type": "agentMessage",
        "id": msg_id,
        "text": text,
        "phase": phase,
        "memoryCitation": None,
    }


# FAKE_CODEX_TURN_FINAL_TEXTS 的按 turn 消费（每行一个 JSON 编码字符串）
_final_texts_cache: list[str] | None = None
_final_texts_idx = 0


def _next_final_text() -> str:
    """按 turn 顺序消费 FAKE_CODEX_TURN_FINAL_TEXTS；耗尽/未配置回退默认值。

    文件格式：每行一个 JSON 编码字符串（json.dumps(text)），可承载多行 final
    文本（FULL_GDL 含换行）；解析失败/未配置回退默认值。
    """
    global _final_texts_cache, _final_texts_idx
    default = os.environ.get("FAKE_CODEX_TURN_FINAL_TEXT", "你好，我是 Codex 测试助手。")
    texts_file = os.environ.get("FAKE_CODEX_TURN_FINAL_TEXTS", "")
    if not texts_file:
        return default
    if _final_texts_cache is None:
        _final_texts_cache = []
        try:
            with open(texts_file, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        _final_texts_cache.append(json.loads(ln))
                    except ValueError:
                        # 容错：裸行按原样（旧用法）
                        _final_texts_cache.append(ln)
        except OSError:
            _final_texts_cache = []
    if _final_texts_idx < len(_final_texts_cache):
        text = _final_texts_cache[_final_texts_idx]
        _final_texts_idx += 1
        return text
    return default


def _emit_turn_sequence(thread_id: str, turn_id: str, hang: bool = False) -> None:
    """turn/start 后发送标准通知流：started → item → delta → completed。"""
    final_text = _next_final_text()
    no_final = os.environ.get("FAKE_CODEX_TURN_NO_FINAL")
    commentary_only = os.environ.get("FAKE_CODEX_TURN_COMMENTARY_ONLY")
    reasoning_no_final = os.environ.get("FAKE_CODEX_TURN_REASONING_NO_FINAL")
    empty_final = os.environ.get("FAKE_CODEX_TURN_EMPTY_FINAL")
    error_canary = os.environ.get("FAKE_CODEX_TURN_ERROR_CANARY", "")
    quota_canary = os.environ.get("FAKE_CODEX_TURN_QUOTA_CANARY", "")
    malformed = os.environ.get("FAKE_CODEX_TURN_MALFORMED")
    tool_noise = os.environ.get("FAKE_CODEX_TURN_TOOL_NOISE")
    _send(
        {
            "method": "turn/started",
            "params": {"threadId": thread_id, "turn": _fake_turn(turn_id)},
        }
    )
    if malformed:
        # 流式边界：半帧 JSON + 畸形形状帧（客户端必须忽略并继续）
        sys.stdout.write('{"method": "item/agentMessage/delta", "params": {"threadId": "')
        sys.stdout.flush()
        _send({"jsonrpc": "2.0", "id": [99, 100], "result": {}})
        _send(["not", "a", "dict"])
    if tool_noise:
        # 恶意服务器尝试 shell/patch 工具面：客户端必须忽略非 agentMessage item
        shell_item = {
            "type": "commandExecution",
            "id": "fake-shell-1",
            "pluginId": None,
            "scriptPath": None,
            "command": "rm -rf /tmp/CANARY-EVIL",
            "cwd": thread_id,
            "processId": None,
            "source": "codex_cli",
            "status": "running",
            "commandActions": [],
            "aggregatedOutput": None,
            "exitCode": None,
            "durationMs": None,
        }
        patch_item = {
            "type": "fileChange",
            "id": "fake-patch-1",
            "changes": [
                {"changeType": "update", "path": "outside.gdl", "line": 1, "value": "EVIL"}
            ],
            "status": "applied",
        }
        for item in (shell_item, patch_item):
            _send(
                {
                    "method": "item/started",
                    "params": {
                        "item": item,
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "startedAtMs": 1786800000000,
                    },
                }
            )
            _send(
                {
                    "method": "item/completed",
                    "params": {
                        "item": item,
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "completedAtMs": 1786800000100,
                    },
                }
            )
    if error_canary or quota_canary:
        if quota_canary:
            err = {
                "message": f"usage limit exceeded {quota_canary}",
                "codexErrorInfo": "usageLimitExceeded",
                "additionalDetails": None,
            }
        else:
            err = {
                "message": f"upstream exploded {error_canary}",
                "codexErrorInfo": "internalServerError",
                "additionalDetails": None,
            }
        _send(
            {
                "method": "error",
                "params": {
                    "error": err,
                    "willRetry": False,
                    "threadId": thread_id,
                    "turnId": turn_id,
                },
            }
        )
        return
    if reasoning_no_final:
        # D6：reasoning 很长但 final 为空/截断——先发大量 commentary 中间
        # 消息（长思考文本），随后 turn 正常完成但没有 final agent message。
        # 客户端必须进入统一完整性错误（no_final_message），绝不交付空结果，
        # 且 commentary 文本绝不透出 UI 流（on_event 不得收到）。
        import random as _random

        _rng = _random.Random(20260817)
        for i in range(240):
            rid_item = _next_msg_id()
            _send(
                {
                    "method": "item/started",
                    "params": {
                        "item": _agent_message_item(rid_item, "", "commentary"),
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "startedAtMs": 1786800000000 + i,
                    },
                }
            )
            chunk = "思考过程 " + str(i) + " " + "x" * (50 + _rng.randint(0, 100))
            _send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": rid_item,
                        "delta": chunk,
                    },
                }
            )
            _send(
                {
                    "method": "item/completed",
                    "params": {
                        "item": _agent_message_item(rid_item, chunk, "commentary"),
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "completedAtMs": 1786800000100 + i,
                    },
                }
            )
        _send(
            {
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turn": _fake_turn(turn_id, status="completed")},
            }
        )
        return
    if no_final:
        _send(
            {
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turn": _fake_turn(turn_id, status="completed")},
            }
        )
        return
    msg_id = _next_msg_id()
    if commentary_only:
        phase = "commentary"
        final_phase = "commentary"
        final_text = "（中间思考，不是最终答复）"
    else:
        phase = None
        final_phase = "final_answer"
        final_text = final_text if not empty_final else ""
    _send(
        {
            "method": "item/started",
            "params": {
                "item": _agent_message_item(msg_id, "", phase),
                "threadId": thread_id,
                "turnId": turn_id,
                "startedAtMs": 1786800000000,
            },
        }
    )
    if not empty_final and not commentary_only:
        for delta in ("你好，", "我是 ", "Codex 测试助手。"):
            _send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": msg_id,
                        "delta": delta,
                    },
                }
            )
    _send(
        {
            "method": "item/completed",
            "params": {
                "item": _agent_message_item(msg_id, final_text, final_phase),
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": 1786800000200,
            },
        }
    )
    if hang:
        return  # 不发送 turn/completed；等 turn/interrupt
    _send(
        {
            "method": "turn/completed",
            "params": {"threadId": thread_id, "turn": _fake_turn(turn_id, status="completed")},
        }
    )


# D3：参数里的工具/危险面（sandbox 反证：D3 路径绝不能携带）。
# sandboxPolicy={"type":"readOnly"} 是 D3 的合法只读沙箱；只有危险值/
# 工具键/非 never 审批才算违规。违规时 fake 返回错误，测试据此证明
# D3 客户端请求参数面干净。
_TOOL_SURFACE_KEYS = (
    "tools",
    "toolChoice",
    "shell",
    "mcp",
    "mcpServers",
    "plugins",
    "permissionMode",
    "permission_mode",
)


def _params_forbidden(params: dict) -> str | None:
    """恶意参数探测：返回命中描述或 None（只用于 fake 内部判定，不回显给客户端）。"""
    for key in _TOOL_SURFACE_KEYS:
        if key in params:
            return key
    approval = params.get("approvalPolicy")
    if approval is not None and approval != "never":
        return "approvalPolicy"
    sandbox = params.get("sandboxPolicy")
    if isinstance(sandbox, dict):
        if sandbox.get("type") != "readOnly":
            return "sandboxPolicy"
    elif isinstance(sandbox, str) and sandbox != "read-only":
        return "sandboxPolicy"
    return None


# ── D4：隔离反证探针 ───────────────────────────────────────────
# app-server 只被允许看到自己的临时 cwd；绝不能看到/写入真实 HSF cwd。
# FAKE_CODEX_CWD_LOG=<path>      把每次 thread/turn 收到的 cwd 追加写入该文件
#                               （一行一个 JSON：method + cwd），测试据此断言
#                               app-server 从未收到项目/工作区路径。
# FAKE_CODEX_WRITE_PROBE=<path>  对每个收到的 cwd 与 probe 路径尝试写入 canary，
#                               把结果追加进 cwd log：
#                                 {"probe": <dir>, "cwd_write_ok": bool, ...}
#                               测试把真实 HSF 目录设为只读，证明即使 app-server
#                               拿到该路径也没有写权限（OS 级隔离反证）。
_CWD_LOG_PATH = os.environ.get("FAKE_CODEX_CWD_LOG", "")


def _log_line(entry: dict) -> None:
    if not _CWD_LOG_PATH:
        return
    try:
        with open(_CWD_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _log_input_line(entry: dict) -> None:
    """FAKE_CODEX_TURN_INPUT_LOG 专用写入（独立于 cwd log 路径）。"""
    path = os.environ.get("FAKE_CODEX_TURN_INPUT_LOG", "")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _try_write_canary(directory: str) -> dict:
    """尝试向 directory 写入 canary 文件；返回 {ok, error_class}（不抛异常）。"""
    try:
        probe_dir = os.path.join(str(directory), ".fake-codex-canary")
        os.makedirs(probe_dir, exist_ok=True)
        marker = os.path.join(probe_dir, "canary.txt")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("fake-codex-write-probe")
        return {"ok": True, "error": None}
    except OSError as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def _record_turn_input(params: dict, thread_id: str, turn_id: str) -> str | None:
    """D5 授权边界反证：记录 turn/start 收到的 input 全文 + 逐图校验。

    Returns:
        越界 localImage 路径的判定：命中 FAKE_CODEX_REJECT_ESCAPING_IMAGE 时
        返回错误类别名（调用方以 JSON-RPC 错误拒绝）；否则返回 None。
        同时把校验结果写入 FAKE_CODEX_TURN_INPUT_LOG（一行一 JSON）。
    """
    log_path = os.environ.get("FAKE_CODEX_TURN_INPUT_LOG", "")
    record_images = bool(log_path) or os.environ.get("FAKE_CODEX_REJECT_ESCAPING_IMAGE")
    if not record_images:
        return None
    cwd_str = ""
    thread = _threads.get(thread_id)
    if isinstance(thread, dict):
        cwd_str = str(thread.get("cwd") or "")
    cwd_resolved = os.path.realpath(cwd_str) if cwd_str else ""
    input_items = params.get("input") or []
    entries: list[dict] = []
    escaping = None
    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "localImage":
            path = str(item.get("path") or "")
            entry = {"type": "localImage", "path": path}
            if path:
                real = os.path.realpath(path)
                entry["inside_cwd"] = (
                    bool(cwd_resolved)
                    and os.path.commonpath([cwd_resolved, real]) == cwd_resolved
                )
                entry["exists"] = os.path.exists(path)
                entry["is_file"] = os.path.isfile(path)
                entry["symlink"] = os.path.islink(path)
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    import hashlib
                    entry["sha256"] = hashlib.sha256(raw).hexdigest()
                    entry["size"] = len(raw)
                except OSError as exc:
                    entry["sha256"] = None
                    entry["size"] = None
                    entry["error"] = exc.__class__.__name__
                if not entry["inside_cwd"]:
                    escaping = "FORBIDDEN-IMAGE-PATH"
            entries.append(entry)
        elif item_type == "image":
            entries.append({"type": "image", "url": str(item.get("url") or "")[:120]})
        elif item_type == "text":
            text = str(item.get("text") or "")
            entries.append({"type": "text", "len": len(text), "text": text})
        else:
            entries.append({"type": item_type, "keys": sorted(item.keys())})
    if log_path:
        _log_input_line({
            "event": "input",
            "method": "turn/start",
            "threadId": thread_id,
            "turnId": turn_id,
            "cwd": cwd_str,
            "input": entries,
        })
    if escaping and os.environ.get("FAKE_CODEX_REJECT_ESCAPING_IMAGE"):
        return escaping
    return None


def _record_cwd(method: str, cwd) -> None:
    """thread/start 与 turn/start 收到 cwd 时调用：记录 + 写权限探测。

    探测语义（隔离反证）：
    - 收到的 cwd 必须是 app-server 自己的临时目录（写 canary 成功是正常的——
      证明它操作的是自己的 scratch）；
    - FAKE_CODEX_WRITE_PROBE 指向真实 HSF/工作区路径（测试会设为只读）——
      写 canary 失败证明 app-server 对该路径无写权限。
    """
    cwd_str = str(cwd) if cwd is not None else ""
    entry: dict = {"event": "cwd", "method": method, "cwd": cwd_str}
    if cwd_str:
        entry["cwd_write"] = _try_write_canary(cwd_str)
    probe = os.environ.get("FAKE_CODEX_WRITE_PROBE", "")
    if probe:
        entry["probe"] = str(probe)
        entry["probe_write"] = _try_write_canary(probe)
    _log_line(entry)


# ── D10：动态工具桥接协议（experimental dynamicTools + item/tool/call）──────
# 协议（0.147.0 app-server README）：
#   thread/start.dynamicTools（camelCase）声明工具；
#   turn 内模型调用工具时，server 先发 item/started（dynamicToolCall）再发
#   JSON-RPC 服务器请求 item/tool/call（{threadId, turnId, callId, namespace,
#   tool, arguments}），客户端以 {contentItems:[{type:"inputText",text}],
#   success} 回应，最后发 item/completed。
# FAKE_CODEX_TURN_SCRIPT=<path>：每行一个 JSON 数组 = 该 turn 的事件脚本
# （按 turn 顺序消费）。步骤 op：
#   tool_call / exploit_tool : 发一次动态工具调用并等待客户端回应
#     （tool/arguments/call_id/namespace/times/same_request_id/thread_id/
#      turn_id 可覆盖）
#   commentary / final      : 发一条 agentMessage（phase 分别 commentary/final_answer）
#   no_final                : turn 正常结束但无 agent message
#   malformed / tool_noise  : 畸形帧 / shell+patch item 噪音（客户端必须忽略）
#   error / quota           : error 通知（message 可带 canary；quota 带
#                             codexErrorInfo=usageLimitExceeded）
#   hang                    : 不发送 turn/completed（等 turn/interrupt）
# FAKE_CODEX_DYN_LOG=<path>：追加记录 initialize 参数、thread/start 参数、
# 每个 item/tool/call 请求与客户端回应（测试断言关联纪律/审计用）。
_INITIALIZE_PARAMS: dict = {}
_DYN_LOG_PATH = os.environ.get("FAKE_CODEX_DYN_LOG", "")
_tool_req_seq = 0
_script_turn_idx = 0


def _dyn_log(**data) -> None:
    if not _DYN_LOG_PATH:
        return
    try:
        with open(_DYN_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _next_tool_req_id() -> int:
    global _tool_req_seq
    _tool_req_seq += 1
    return _tool_req_seq


def _script_steps_for_turn() -> list | None:
    """按 turn 顺序消费 FAKE_CODEX_TURN_SCRIPT 的每一行（JSON 数组 = 该 turn 步骤）。"""
    global _script_turn_idx
    path = os.environ.get("FAKE_CODEX_TURN_SCRIPT", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return []
    if _script_turn_idx >= len(lines):
        return []
    try:
        steps = json.loads(lines[_script_turn_idx])
    except ValueError:
        steps = []
    _script_turn_idx += 1
    return steps if isinstance(steps, list) else []


def _dynamic_tool_item(call_id: str, namespace, tool, arguments, status: str,
                       content_items=None, success=None) -> dict:
    return {
        "type": "dynamicToolCall",
        "id": call_id,
        "namespace": namespace,
        "tool": tool,
        "arguments": arguments,
        "status": status,
        "contentItems": content_items,
        "success": success,
        "error": None,
        "durationMs": None,
    }


def _wait_for_tool_response(lines, req_id: int) -> dict:
    """等待客户端对 item/tool/call 的回应；期间处理 turn/interrupt 等请求。"""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("id") == req_id:
            if "result" in msg and isinstance(msg["result"], dict):
                return msg["result"]
            return {"success": False}
        method = msg.get("method")
        if isinstance(method, str) and method == "turn/interrupt":
            params = msg.get("params") or {}
            t_id = str(params.get("threadId") or "")
            tu_id = str(params.get("turnId") or "")
            if tu_id in _turns:
                _turns[tu_id]["status"] = "interrupted"
                _send({
                    "method": "turn/completed",
                    "params": {"threadId": t_id, "turn": _fake_turn(tu_id, status="interrupted")},
                })
            # 回应 interrupt 请求本身（客户端在 call() 等待）
            _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
            continue
        # 其他客户端请求（thread/delete 等）：忽略，继续等待
    return {"success": False}


def _emit_dynamic_tool_call(thread_id: str, turn_id: str, step: dict, lines) -> None:
    """发一次 item/started → item/tool/call → 等待回应 → item/completed。"""
    tool = str(step.get("tool") or "unknown")
    arguments = step.get("arguments") or {}
    namespace = step.get("namespace")
    call_id = str(step.get("call_id") or f"call-{_next_msg_id()}")
    times = int(step.get("times") or 1)
    same_request_id = bool(step.get("same_request_id"))
    req_id = None
    for i in range(times):
        req_id = req_id if (same_request_id and i > 0) else _next_tool_req_id()
        params = {
            "threadId": str(step.get("thread_id") or thread_id),
            "turnId": str(step.get("turn_id") or turn_id),
            "callId": call_id,
            "namespace": namespace,
            "tool": tool,
            "arguments": arguments,
        }
        _send({
            "method": "item/started",
            "params": {
                "item": _dynamic_tool_item(call_id, namespace, tool, arguments, "inProgress"),
                "threadId": thread_id,
                "turnId": turn_id,
                "startedAtMs": 1786800000000,
            },
        })
        _send({"jsonrpc": "2.0", "id": req_id, "method": "item/tool/call", "params": params})
        _dyn_log(event="tool_request", request_id=req_id, params=params)
        result = _wait_for_tool_response(lines, req_id)
        _dyn_log(event="tool_response", request_id=req_id, result=result)
        ok = bool((result or {}).get("success"))
        status = "completed" if ok else "failed"
        _send({
            "method": "item/completed",
            "params": {
                "item": _dynamic_tool_item(
                    call_id, namespace, tool, arguments, status,
                    content_items=(result or {}).get("contentItems"),
                    success=ok,
                ),
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": 1786800000200,
            },
        })


def _emit_agent_message(thread_id: str, turn_id: str, text: str, phase: str | None) -> None:
    msg_id = _next_msg_id()
    _send({
        "method": "item/started",
        "params": {
            "item": _agent_message_item(msg_id, "", phase),
            "threadId": thread_id,
            "turnId": turn_id,
            "startedAtMs": 1786800000000,
        },
    })
    _send({
        "method": "item/agentMessage/delta",
        "params": {"threadId": thread_id, "turnId": turn_id, "itemId": msg_id, "delta": text},
    })
    _send({
        "method": "item/completed",
        "params": {
            "item": _agent_message_item(msg_id, text, phase),
            "threadId": thread_id,
            "turnId": turn_id,
            "completedAtMs": 1786800000200,
        },
    })


def _emit_tool_noise(thread_id: str, turn_id: str) -> None:
    """恶意服务器尝试 shell/patch 工具面：客户端必须忽略非 agentMessage item。"""
    shell_item = {
        "type": "commandExecution", "id": "fake-shell-1", "pluginId": None,
        "scriptPath": None, "command": "rm -rf /tmp/CANARY-EVIL", "cwd": thread_id,
        "processId": None, "source": "codex_cli", "status": "running",
        "commandActions": [], "aggregatedOutput": None, "exitCode": None, "durationMs": None,
    }
    patch_item = {
        "type": "fileChange", "id": "fake-patch-1",
        "changes": [{"changeType": "update", "path": "outside.gdl", "line": 1, "value": "EVIL"}],
        "status": "applied",
    }
    for item in (shell_item, patch_item):
        _send({
            "method": "item/started",
            "params": {
                "item": item, "threadId": thread_id, "turnId": turn_id,
                "startedAtMs": 1786800000000,
            },
        })
        _send({
            "method": "item/completed",
            "params": {
                "item": item, "threadId": thread_id, "turnId": turn_id,
                "completedAtMs": 1786800000100,
            },
        })


def _run_turn_script(thread_id: str, turn_id: str, steps: list, lines) -> None:
    """按脚本执行一次 turn 的事件序列（D10 动态工具桥接测试）。"""
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op") or "")
        if op in ("tool_call", "exploit_tool"):
            _emit_dynamic_tool_call(thread_id, turn_id, step, lines)
        elif op == "commentary":
            text_commentary = str(step.get("text") or "（中间思考）")
            _emit_agent_message(thread_id, turn_id, text_commentary, "commentary")
        elif op == "final":
            text_final = str(step.get("text") or "你好，我是 Codex 测试助手。")
            _emit_agent_message(thread_id, turn_id, text_final, "final_answer")
        elif op == "no_final":
            pass
        elif op == "malformed":
            sys.stdout.write('{"method": "item/agentMessage/delta", "params": {"threadId": "')
            sys.stdout.flush()
            _send({"jsonrpc": "2.0", "id": [99, 100], "result": {}})
            _send(["not", "a", "dict"])
            _send("not json at all {{{{")
        elif op == "tool_noise":
            _emit_tool_noise(thread_id, turn_id)
        elif op == "error":
            canary = str(step.get("canary") or "")
            _send({
                "method": "error",
                "params": {
                    "message": f"upstream error {canary}",
                    "codexErrorInfo": None, "additionalDetails": None,
                },
            })
            return
        elif op == "quota":
            canary = str(step.get("canary") or "")
            _send({
                "method": "error",
                "params": {
                    "message": f"usage limit exceeded {canary}",
                    "codexErrorInfo": "usageLimitExceeded",
                    "additionalDetails": None,
                },
            })
            return
        elif op == "approval_request":
            # 未预期的服务器请求（如审批请求）：客户端必须拒绝，绝不批准
            req_id = _next_tool_req_id()
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": str(step.get("method") or "item/commandExecution/requestApproval"),
                "params": step.get("params") or {},
            })
            _wait_for_tool_response(lines, req_id)
        elif op == "post_completed_tool":
            # turn 已完成后迟到的工具调用：先发 turn/completed，再发工具请求；
            # 客户端必须拒绝且不执行（绝不污染已完成轮次）。
            _send({
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turn": _fake_turn(turn_id, status="completed")},
            })
            req_id = _next_tool_req_id()
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "item/tool/call",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "callId": str(step.get("call_id") or "late-call"),
                    "namespace": None,
                    "tool": str(step.get("tool") or "update_script"),
                    "arguments": step.get("arguments") or {},
                },
            })
            _dyn_log(event="tool_request", request_id=req_id, params={})
            _wait_for_tool_response(lines, req_id)
            return
        elif op == "hang":
            return
    _send({
        "method": "turn/completed",
        "params": {"threadId": thread_id, "turn": _fake_turn(turn_id, status="completed")},
    })


def main() -> None:
    version = os.environ.get("FAKE_CODEX_VERSION", "0.147.0")
    login_type = os.environ.get("FAKE_CODEX_LOGIN_TYPE", "chatgpt")
    cancel_status = os.environ.get("FAKE_CODEX_LOGIN_CANCEL", "canceled")
    rate_mode = os.environ.get("FAKE_CODEX_RATE_LIMITS", "unauth")
    notify = os.environ.get("FAKE_CODEX_NOTIFY", "")
    delay = float(os.environ.get("FAKE_CODEX_DELAY_RESPONSE_SECONDS", "0"))
    delay_once_marker = os.environ.get("FAKE_CODEX_DELAY_ONCE_MARKER", "")
    crash_after = int(os.environ.get("FAKE_CODEX_CRASH_AFTER_REQUESTS", "0"))
    crash_marker = os.environ.get("FAKE_CODEX_CRASH_MARKER", "")
    if crash_after and crash_marker and os.path.exists(crash_marker):
        crash_after = 0  # 已崩溃过一次（restart 测试），本次不崩
    child_pid_file = os.environ.get("FAKE_CODEX_SPAWN_CHILD_PID_FILE", "")

    garbage = os.environ.get("FAKE_CODEX_GARBAGE_LINE")
    if garbage:
        sys.stdout.write(garbage + "\n")
        sys.stdout.flush()
    stderr_secret = os.environ.get("FAKE_CODEX_STDERR_SECRET")
    if stderr_secret:
        sys.stderr.write(stderr_secret + "\n")
        sys.stderr.flush()
    if child_pid_file:
        # 不设 start_new_session：子进程加入 fake app-server 的进程组
        # （transport 用 start_new_session=True 启动 fake），进程组回收时一并清理。
        if os.environ.get("FAKE_CODEX_CHILD_IGNORE_TERM"):
            child_code = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"
            )
        else:
            child_code = "import time; time.sleep(300)"
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(child_pid_file, "w", encoding="utf-8") as f:
            f.write(str(child.pid))
        # 保持子进程引用，避免 GC 干扰（Popen 析构不杀进程）
        _CHILD = child

    if os.environ.get("FAKE_CODEX_CRASH_IMMEDIATELY"):
        sys.stderr.write("fake app-server crash at startup\n")
        sys.stderr.flush()
        sys.exit(42)

    handled = 0
    delay_first = float(os.environ.get("FAKE_CODEX_DELAY_FIRST_RESPONSE_SECONDS", "0"))
    malformed_sent = False
    poison_sent = False
    _stdin_lines = iter(sys.stdin)
    for raw in _stdin_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("id")
        method = msg.get("method") or ""
        delay_method = os.environ.get("FAKE_CODEX_DELAY_METHOD", "")
        if delay and (not delay_method or method == delay_method):
            # 一次性延迟：marker 已存在则本次不延迟（用于「超时→关闭→重试」
            # 测试：新进程看到 marker 后正常响应）
            if delay_once_marker and os.path.exists(delay_once_marker):
                pass
            else:
                if delay_once_marker:
                    try:
                        with open(delay_once_marker, "w", encoding="utf-8") as f:
                            f.write("1")
                    except OSError:
                        pass
                time.sleep(delay)
        elif delay_first and handled == 0:
            time.sleep(delay_first)
        if notify:
            _send(_notify_payload(notify))
        # P0-3：unsolicited 响应污染测试——首个请求前先发一个未来 id 的伪响应
        if os.environ.get("FAKE_CODEX_UNSOLICITED_POISON") and not poison_sent:
            poison_sent = True
            _send({"jsonrpc": "2.0", "id": rid + 2, "result": {"value": "POISON"}})
        # P0-3：同 id 重复响应测试——每条响应后再发一条同 id 的 DUPLICATE-POISON
        # （与正确响应在同一次 flush 中连续写出，验证 first-response-wins）
        handled += 1

        if method == "initialize":
            _INITIALIZE_PARAMS.clear()
            _INITIALIZE_PARAMS.update(msg.get("params") or {})
            _dyn_log(event="initialize", params=msg.get("params") or {})
            if version == "garbage":
                user_agent = "openbrep-garbage no-version-token"
            elif version == "old":
                user_agent = "openbrep/0.50.0 (Mac OS 15.7.5; arm64)"
            else:
                user_agent = f"openbrep/{version} (Mac OS 15.7.5; arm64)"
            result = {
                "userAgent": user_agent,
                "codexHome": os.environ.get("CODEX_HOME", ""),
                "platformFamily": "unix",
                "platformOs": "macos",
            }
            if os.environ.get("FAKE_CODEX_MALFORMED_FRAMES") and not malformed_sent:
                malformed_sent = True
                # P0-5：畸形帧批次——list/dict id、无 method 通知、非对象帧、非 JSON
                _send({"jsonrpc": "2.0", "id": [1, 2], "result": {}})
                _send({"jsonrpc": "2.0", "id": {"a": 1}, "result": {}})
                _send({"jsonrpc": "2.0", "params": {}})  # 无 method 的通知
                _send(["not", "a", "dict"])
                _send("not json at all {{{{")  # 非 JSON 行
        elif method == "account/read":
            if os.environ.get("FAKE_CODEX_SIGNED_IN"):
                result = {
                    "account": {"type": "chatgpt", "email": "jo@example.com", "planType": "pro"},
                    "requiresOpenaiAuth": False,
                }
            else:
                result = {"account": None, "requiresOpenaiAuth": True}
        elif method == "account/login/start":
            if login_type == "chatgpt":
                result = {
                    "type": "chatgpt",
                    "loginId": "fake-login-id",
                    "authUrl": "https://example.test/auth/callback?state=fake",
                }
            elif login_type == "chatgptDeviceCode":
                result = {
                    "type": "chatgptDeviceCode",
                    "loginId": "fake-login-id",
                    "verificationUrl": "https://example.test/device",
                    "userCode": "ABCD-EFGH",
                }
            else:
                result = {"type": login_type, "loginId": "fake-login-id"}
        elif method == "account/login/cancel":
            result = {"status": cancel_status}
        elif method == "account/logout":
            result = {}
        elif method == "account/rateLimits/read":
            if rate_mode == "unauth":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {
                            "code": -32600,
                            "message": "codex account authentication required to read rate limits",
                        },
                    }
                )
                continue
            if rate_mode == "reached":
                rl = {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {
                        "usedPercent": 100,
                        "windowDurationMins": 360,
                        "resetsAt": 1786800000,
                    },
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    "spendControlReached": False,
                    "planType": "pro",
                    "rateLimitReachedType": "rate_limit_reached",
                }
            else:  # normal
                rl = {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {
                        "usedPercent": 12,
                        "windowDurationMins": 360,
                        "resetsAt": 1786800000,
                    },
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "123.45"},
                    "spendControlReached": False,
                    "planType": "pro",
                    "rateLimitReachedType": None,
                }
            result = {
                "rateLimits": rl,
                "rateLimitsByLimitId": {"codex": rl},
                "rateLimitResetCredits": None,
            }
        elif method == "model/list":
            result = _model_list_response()
        elif method == "thread/start" and os.environ.get("FAKE_CODEX_TURN"):
            params = msg.get("params") or {}
            _record_cwd("thread/start", params.get("cwd"))
            _record_turn_params("thread/start", params)
            if params.get("dynamicTools") is not None:
                caps = _INITIALIZE_PARAMS.get("capabilities")
                if not isinstance(caps, dict) or not caps.get("experimentalApi"):
                    _send({
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32602, "message": "DYNAMIC-TOOLS-NEED-EXPERIMENTAL"},
                    })
                    continue
                _dyn_log(event="thread/start", params=params)
            forbidden = _params_forbidden(params)
            if forbidden:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32602, "message": "FORBIDDEN-THREAD-KEY"},
                    }
                )
                continue
            tid = f"fake-thread-{len(_threads) + 1}"
            _threads[tid] = dict(params)
            thread = {
                "id": tid,
                "sessionId": f"fake-session-{tid}",
                "forkedFromId": None,
                "parentThreadId": None,
                "preview": "",
                "ephemeral": True,
                "section": None,
                "sectionEnteredAt": None,
                "modelProvider": "openai",
                "createdAt": 1786800000,
                "updatedAt": 1786800000,
                "recencyAt": None,
                "status": {"type": "idle"},
                "path": None,
                "cwd": params.get("cwd", "/tmp"),
                "cliVersion": "0.147.0",
                "source": "codex app-server",
                "threadSource": None,
                "agentNickname": None,
                "agentRole": None,
                "gitInfo": None,
                "name": None,
                "turns": [],
            }
            result = {
                "thread": thread,
                "model": params.get("model", "gpt-5.6-luna"),
                "modelProvider": "openai",
                "serviceTier": None,
                "cwd": params.get("cwd", "/tmp"),
                "instructionSources": [],
                "approvalPolicy": params.get("approvalPolicy", "never"),
                "approvalsReviewer": None,
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "reasoningEffort": None,
            }
        elif method == "turn/start" and os.environ.get("FAKE_CODEX_TURN"):
            params = msg.get("params") or {}
            _record_cwd("turn/start", params.get("cwd"))
            _record_turn_params("turn/start", params)
            forbidden = _params_forbidden(params)
            if forbidden:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32602, "message": "FORBIDDEN-TURN-KEY"},
                    }
                )
                continue
            tid = str(params.get("threadId") or "")
            # D6：FAKE_CODEX_REJECT_UNSUPPORTED_EFFORT —— app-server 侧模拟
            # 拒绝该模型不支持的 effort（真实上游行为；正常客户端永不触发，
            # 因为 provider.chat 在 turn 启动前已本地校验）。
            effort_param = str(params.get("effort") or "")
            if (
                os.environ.get("FAKE_CODEX_REJECT_UNSUPPORTED_EFFORT")
                and effort_param
                and not _effort_supported_for_thread(tid, effort_param)
            ):
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {
                            "code": -32602,
                            "message": "UNSUPPORTED-EFFORT-FOR-MODEL",
                        },
                    }
                )
                continue
            turn_id = _next_turn_id()
            image_err = _record_turn_input(params, tid, turn_id)
            if image_err:
                _turns[turn_id] = {
                    "threadId": tid,
                    "status": "interrupted",
                    "interrupted": True,
                }
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32602, "message": image_err},
                    }
                )
                continue
            _turns[turn_id] = {
                "threadId": tid,
                "status": "inProgress",
                "interrupted": False,
            }
            result = {"turn": _fake_turn(turn_id)}
            # 先响应 turn/start，再流式发通知（客户端按 id 关联响应、
            # 按 threadId/turnId 过滤通知）
            _send({"jsonrpc": "2.0", "id": rid, "result": result})
            global _hang_active
            hang_pending = os.environ.get("FAKE_CODEX_TURN_HANG") and _hang_active
            if hang_pending:
                _hang_active = False  # 只让第一个 turn 挂起
            script_steps = _script_steps_for_turn()
            if script_steps is not None:
                _dyn_log(event="turn/start", turn_id=turn_id, params=params)
                _run_turn_script(tid, turn_id, script_steps, _stdin_lines)
            else:
                _emit_turn_sequence(tid, turn_id, hang=bool(hang_pending))
            continue
        elif method == "turn/interrupt" and os.environ.get("FAKE_CODEX_TURN"):
            params = msg.get("params") or {}
            turn_id = str(params.get("turnId") or "")
            tid = str(params.get("threadId") or "")
            if turn_id in _turns:
                _turns[turn_id]["status"] = "interrupted"
                _send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": tid,
                            "turn": _fake_turn(turn_id, status="interrupted"),
                        },
                    }
                )
            result = {}
        elif method == "thread/delete" and os.environ.get("FAKE_CODEX_TURN"):
            params = msg.get("params") or {}
            tid = str(params.get("threadId") or "")
            _threads.pop(tid, None)
            _dyn_log(event="thread/delete", params=params)
            result = {}
        elif method == "never/respond":
            continue  # 专门用于超时测试：不发响应
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )
            continue
        _send({"jsonrpc": "2.0", "id": rid, "result": result})
        if os.environ.get("FAKE_CODEX_DUP_RESPONSE"):
            _send({"jsonrpc": "2.0", "id": rid, "result": {"value": "DUPLICATE-POISON"}})

        if crash_after and handled >= crash_after:
            if crash_marker:
                try:
                    with open(crash_marker, "w", encoding="utf-8") as f:
                        f.write("1")
                except OSError:
                    pass
            sys.stderr.write("fake app-server crash: simulated failure\n")
            sys.stderr.flush()
            sys.exit(43)

    if os.environ.get("FAKE_CODEX_IGNORE_EOF"):
        # P0-3：stdin EOF 后不退出（父进程对 SIGTERM 响应退出），用于验证
        # close() 进程组兜底回收。
        import signal as _signal

        def _on_term(signum, frame):
            sys.exit(0)

        _signal.signal(_signal.SIGTERM, _on_term)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
