"""Request serialization policy for the ThreadingHTTPServer transport.

The workbench API serves each request on its own thread while WorkbenchSession
is a global singleton. Mutating requests are serialized through a session-level
RLock so a slow mutation (assistant generation) cannot interleave with a fast
one (compile/save) on the same project. Read-only and native-dialog routes
stay lock-free.
"""

from __future__ import annotations

# POST routes that never touch session/project state. Everything else
# (POST/PATCH/PUT/DELETE) is serialized by WorkbenchSession. New routes default
# to locked (safe direction).
# NOTE: /api/compile/mock calls project.save_to_disk() and rewrites
# session.last_compile_output_path — it is a mutation and must stay locked.
LOCK_FREE_POST_ROUTES = frozenset({
    "/api/preview",
    "/api/preview/2d",
    "/api/project/parameters/validate",
    "/api/settings/llm/test",
    "/api/assistant/code-blocks",
    "/api/artifact/reveal",
    # Copilot 端点：不触碰 session/project 状态（chat 只读 LLM 配置并回写
    # copilot 自身 buffer；clipboard-buffer/clear、summarize-errors 也只操作
    # copilot 的剪贴板 buffer，该 buffer 有 service 内部锁保护），无需持有
    # session 级 RLock。GET 两条（status / clipboard-buffer）天然 lock-free。
    "/api/copilot/chat",
    "/api/copilot/clipboard-buffer/clear",
    "/api/copilot/summarize-errors",
    # E1：手动错误沉淀。只读写全局错题本 ~/.openbrep/error_lessons.jsonl，
    # 不触碰 session/project 状态；文件写入有 copilot service 自有锁保护。
    "/api/copilot/ingest-error",
    # Codex BYOA（D1+D2）：login/start 拉起 app-server 子进程并打开终端用户浏览器
    # （可能耗时数秒）；logout/cancel/restart 只改 codex 登录态/进程。都不触碰
    # session/project 状态。并发安全由两层保证：StdioJsonRpcTransport.call() 的
    # 内部锁串行化 JSON-RPC 帧（写帧+等响应）；provider._op_lock 串行化完整
    # lifecycle transition（start/cancel/logout/restart/client replacement），
    # close() 置 closing 唤醒 in-flight waiter。因此无需持有 session 级锁。
    # GET 两条（status / models）天然 lock-free。
    "/api/settings/llm/codex/login/start",
    "/api/settings/llm/codex/login/device-code",
    "/api/settings/llm/codex/login/cancel",
    "/api/settings/llm/codex/logout",
    # D2：restart 只重建 codex app-server 子进程（不触碰 session/project），
    # JSON-RPC 帧由 transport 内部锁串行化，无需 session 级锁。
    "/api/settings/llm/codex/restart",
})


def is_lock_free_route(normalized_method: str, route: str) -> bool:
    """True when a request cannot mutate session/project/source state.

    GET routes are all read-only. Native dialog routes may wait on the user for
    a long time and must never hold the lock (accepted residual risk: the load
    that follows a successful open-directory dialog happens unlocked).
    """
    if normalized_method == "GET":
        return True
    if route.startswith("/api/dialog/"):
        return True
    return route in LOCK_FREE_POST_ROUTES
