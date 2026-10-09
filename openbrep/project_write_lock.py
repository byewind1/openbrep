"""Cross-process serialization for managed writes scoped to one HSF project."""
from __future__ import annotations

import getpass
import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_DEPTH = threading.local()


@contextmanager
def project_write_lock(project_root: str | Path) -> Iterator[None]:
    """Hold a re-entrant process and cross-process lock for a canonical project path."""
    root = Path(project_root).expanduser().resolve()
    key = str(root)
    with _GUARD:
        local_lock = _LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        held = getattr(_DEPTH, "held", set())
        if key in held:
            yield
            return
        held = set(held)
        held.add(key)
        _DEPTH.held = held
        try:
            with _process_lock(root):
                yield
        finally:
            held.remove(key)
            _DEPTH.held = held


@contextmanager
def _process_lock(root: Path) -> Iterator[None]:
    uid = getattr(os, "getuid", lambda: getpass.getuser())()
    lock_root = Path(os.environ.get("OPENBREP_PROJECT_LOCK_DIR") or (Path(tempfile.gettempdir()) / f"openbrep-project-locks-{uid}"))
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        lock_root.chmod(0o700)
    lock_name = hashlib.sha256(os.fsencode(str(root))).hexdigest() + ".lock"
    with (lock_root / lock_name).open("a+b") as stream:
        if os.name == "nt":  # pragma: no cover
            import msvcrt

            stream.seek(0)
            if stream.read(1) == b"":
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
