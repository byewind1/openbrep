#!/usr/bin/env python3
"""MCP stdio 级冒烟（Phase 1 / P1-e）。

不起真实 MCP host：子进程拉起 `obr mcp-server`，通过 stdin/stdout 走
JSON-RPC（newline-delimited JSON）做协议级验证。

步骤：
1. 临时构造最小 HSF 项目（复用 HSFProject.create_new 手法）。
2. initialize → notifications/initialized → tools/list → tools/call
   load_project(path=项目) → tools/call compile_hsf(mode="mock")。
3. 断言：
   - initialize 响应含 protocolVersion；
   - tools/list 恰好 7 个工具且名字正确；
   - load_project 返回 ok:True 且 name 正确；
   - compile_hsf 返回 success:True。
4. 全程 30s 超时保护；失败打印收到的原始字节便于排查。

stdout 污染检查：server 进程任何 print/日志进 stdout 都会让这里的
json.loads 挂掉——这正是要抓的问题。日志应全部走 stderr。

退出码：0 = 通过，非 0 = 失败。

用法：python scripts/mcp_smoke.py
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30.0  # 全程超时（秒）

# 冒烟脚本自己需要 import openbrep 来构造临时项目；若当前解释器没有 openbrep
#（例如直接 `python scripts/mcp_smoke.py` 落到系统 python），换用 .venv 解释器
# 重新执行自己。
try:
    import openbrep  # noqa: F401
except Exception:
    _venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    if _venv_py.exists():
        os.execv(str(_venv_py), [str(_venv_py), str(__file__), *sys.argv[1:]])
    raise

EXPECTED_TOOLS = (
    "load_project",
    "compile_hsf",
    "semantic_verify",
    "render_evidence",
    "apply_edit",
    "rollback",
    "import_source",
)


def _fail(message: str, raw: str = "") -> None:
    print(f"\n[FAIL] {message}")
    if raw:
        print("[RAW] server 原始 stdout 字节：")
        print(raw)
    print("（若上述是垃圾字节，说明有 print/日志污染了 stdout 协议通道。）")
    sys.exit(1)


def _resolve_python() -> str:
    """优先 .venv（项目声明运行时，装有 mcp 等依赖），其次本脚本解释器。"""
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _server_command(python: str) -> list[str]:
    """实际入口：优先 .venv/bin/obr，其次 python -m cli.main。"""
    obr = Path(python).parent / "obr"
    if obr.exists() and os.access(obr, os.X_OK):
        return [str(obr), "mcp-server"]
    return [python, "-m", "cli.main", "mcp-server"]


def _make_project() -> Path:
    """构造最小 HSF 项目（与 tests/test_mcp_tools.py 同款手法）。"""
    from openbrep.hsf_project import HSFProject

    tmp = Path(tempfile.mkdtemp(prefix="mcp_smoke_"))
    project = HSFProject.create_new("SmokeShelf", str(tmp))
    return project.save_to_disk()


def _read_json_line(queue_out, deadline: float) -> dict:
    """从 stdout 读一行 JSON；超时或非 JSON 即失败。"""
    remaining = deadline - time.time()
    if remaining <= 0:
        _fail("超时：等待 server 响应超过 30s")
    try:
        line = queue_out.get(timeout=remaining)
    except queue.Empty:
        _fail("超时：等待 server 响应超过 30s")
    raw = line.strip()
    if not raw:
        _fail("server stdout 出现空行（协议污染？）")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"stdout 非 JSON（协议污染或协议格式异常）: {exc}", raw=raw)


def _expect_response(msg: dict, expected_id: int) -> dict:
    if "id" in msg and msg.get("id") != expected_id:
        _fail(f"收到非预期响应 id={msg.get('id')}，期望 id={expected_id}；消息: {msg}")
    if "error" in msg:
        _fail(f"server 返回 JSON-RPC error: {msg['error']}")
    return msg.get("result", {})


def _main() -> int:
    python = _resolve_python()
    cmd = _server_command(python)
    print(f"[1] 拉起 server: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    lines: queue.Queue = queue.Queue()
    stderr_log: list[str] = []

    def _reader():
        for line in proc.stdout:
            lines.put(line)

    def _stderr_reader():
        for line in proc.stderr:
            stderr_log.append(line)

    threading.Thread(target=_reader, daemon=True).start()
    threading.Thread(target=_stderr_reader, daemon=True).start()

    project_path = _make_project()
    print(f"[2] 临时 HSF 项目: {project_path}")

    start = time.time()
    deadline = start + TIMEOUT

    try:
        # ── initialize ────────────────────────────────────────────────
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcp-smoke", "version": "0.0.1"},
            },
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
        init_result = _expect_response(_read_json_line(lines, deadline), 1)
        if "protocolVersion" not in init_result:
            _fail(f"initialize 响应缺少 protocolVersion: {init_result}")
        print(f"[3] initialize OK · protocolVersion={init_result['protocolVersion']}")

        # ── notifications/initialized ─────────────────────────────────
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()

        # ── tools/list ────────────────────────────────────────────────
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        list_result = _expect_response(_read_json_line(lines, deadline), 2)
        tools = list_result.get("tools") or []
        names = [t.get("name") for t in tools]
        if names != list(EXPECTED_TOOLS):
            _fail(
                f"tools/list 工具清单不符。期望 {list(EXPECTED_TOOLS)}，实际 {names}",
                raw=json.dumps(list_result, ensure_ascii=False),
            )
        print(f"[4] tools/list OK · {len(tools)} 个工具: {', '.join(names)}")

        # ── tools/call load_project ───────────────────────────────────
        load_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "load_project", "arguments": {"path": str(project_path)}},
        }
        proc.stdin.write(json.dumps(load_req) + "\n")
        proc.stdin.flush()
        load_result = _expect_response(_read_json_line(lines, deadline), 3)
        load_text = (load_result.get("content") or [{}])[0].get("text", "")
        load_payload = json.loads(load_text)
        if load_payload.get("ok") is not True or load_payload.get("name") != "SmokeShelf":
            _fail(f"load_project 断言失败: {load_payload}")
        print(
            f"[5] load_project OK · name={load_payload['name']} "
            f"parameters={load_payload['parameter_count']} trace_id={load_payload['trace_id']}"
        )

        # ── tools/call compile_hsf(mode="mock") ───────────────────────
        compile_req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "compile_hsf",
                "arguments": {"path": str(project_path), "mode": "mock"},
            },
        }
        proc.stdin.write(json.dumps(compile_req) + "\n")
        proc.stdin.flush()
        compile_result = _expect_response(_read_json_line(lines, deadline), 4)
        compile_text = (compile_result.get("content") or [{}])[0].get("text", "")
        compile_payload = json.loads(compile_text)
        if compile_payload.get("ok") is not True or compile_payload.get("success") is not True:
            _fail(f"compile_hsf 断言失败: {compile_payload}")
        print(
            f"[6] compile_hsf OK · mode={compile_payload['mode']} "
            f"success={compile_payload['success']} exit_code={compile_payload['exit_code']}"
        )

        print("\n[PASS] MCP stdio 冒烟全部通过")
        return 0
    finally:
        # ── 收尾：关 stdin 让 server 正常退出，超时则强杀 ─────────────
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    _main()
