"""Session-level serialization for the ThreadingHTTPServer transport.

The workbench API serves each request on its own thread while WorkbenchSession
is a global singleton. Mutating requests must be serialized through
``WorkbenchSession._op_lock`` so a slow mutation (assistant generation) cannot
interleave with a fast one (compile/save) on the same project. Read-only and
native-dialog routes stay lock-free.
"""

import threading
import time

from openbrep.workbench.request_gate import is_lock_free_route
from openbrep.workbench_api import WorkbenchSession


def _make_session() -> WorkbenchSession:
    return WorkbenchSession(tapir_import_ok=False)


def test_mutating_requests_are_serialized():
    session = _make_session()
    entered = threading.Event()
    release = threading.Event()
    second_ran = threading.Event()

    def slow_save(body):
        entered.set()
        release.wait(timeout=5)
        return {"ok": True}

    def quick_compile(body):
        second_ran.set()
        return {"ok": True}

    session.save_project = slow_save
    session.compile_project = quick_compile

    t1 = threading.Thread(target=session.route, args=("POST", "/api/project/save", {}))
    t1.start()
    assert entered.wait(timeout=2), "save request did not start"

    t2 = threading.Thread(target=session.route, args=("POST", "/api/compile", {}))
    t2.start()
    time.sleep(0.3)
    assert not second_ran.is_set(), "compile ran while save still held the session lock"

    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert second_ran.is_set(), "compile did not run after save released the lock"


def test_get_requests_bypass_the_lock():
    session = _make_session()
    entered = threading.Event()
    release = threading.Event()

    def slow_save(body):
        entered.set()
        release.wait(timeout=5)
        return {"ok": True}

    session.save_project = slow_save
    t1 = threading.Thread(target=session.route, args=("POST", "/api/project/save", {}))
    t1.start()
    assert entered.wait(timeout=2), "save request did not start"
    try:
        response = session.route("GET", "/api/snapshot")
        assert response["ok"] is True
    finally:
        release.set()
        t1.join(timeout=5)


def test_route_lock_classification():
    is_free = is_lock_free_route

    assert is_free("GET", "/api/snapshot")
    assert is_free("GET", "/api/project/scripts")
    assert is_free("POST", "/api/preview")
    assert is_free("POST", "/api/preview/2d")
    assert is_free("POST", "/api/settings/llm/test")
    assert is_free("POST", "/api/dialog/open-directory")

    # compile/mock 会 save_to_disk 并改写 last_compile_output_path，必须走锁
    assert not is_free("POST", "/api/compile/mock")
    assert not is_free("POST", "/api/compile")
    assert not is_free("POST", "/api/project/save")
    assert not is_free("POST", "/api/assistant/generate")
    assert not is_free("DELETE", "/api/memory")
