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
