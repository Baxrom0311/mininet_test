"""Exclusive real-network test scope, including ordinary util-linux flock wrappers.

Mininet cleanup is host-wide. Only entry points acquire this lock; imported test
functions run inside their caller's scope. Reuse a lock only when the process
actually has an exclusive FLOCK descriptor for the same inode. Never infer lock
ownership from a PID file, an environment flag or the existence of the path.
"""

from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import stat
import sys
import threading

LOCK_PATH = Path("/tmp/netlab-real.lock")
_guard = threading.RLock()
_owned_fds = set()


class RealNetworkBusy(RuntimeError):
    pass


def _after_fork():
    # A child must not retain a helper-owned lock after its parent exits. Exec
    # also closes these descriptors (O_CLOEXEC / Python's non-inheritable dup).
    global _guard
    for fd in _owned_fds:
        try:
            os.close(fd)
        except OSError:
            pass
    _owned_fds.clear()
    _guard = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _inherited_lock(info, exclude):
    """Linux fdinfo describes a FLOCK on this open file description, not a PID guess."""
    try:
        descriptors = list(Path("/proc/self/fd").iterdir())
    except OSError:
        return None
    for entry in descriptors:
        if not entry.name.isdigit() or int(entry.name) == exclude:
            continue
        fd = int(entry.name)
        try:
            other = os.fstat(fd)
            if (other.st_dev, other.st_ino) != (info.st_dev, info.st_ino):
                continue
            lines = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            if any(line.startswith("lock:") and line.split()[2:5] == ["FLOCK", "ADVISORY", "WRITE"]
                   for line in lines):
                return os.dup(fd)
        except OSError:
            continue
    return None


@contextmanager
def real_network_lock(path=None):
    """Fail fast on contention; nest safely on one thread or inside `flock file command`.

    An inherited descriptor is duplicated and closed, never explicitly unlocked:
    the outer flock must remain held after this context finishes. Shared locks
    and flock --close cannot be reused and are reported as contention.
    """
    import fcntl

    path = Path(path) if path is not None else LOCK_PATH
    if not _guard.acquire(blocking=False):
        raise RealNetworkBusy(f"real network busy: another thread holds {path}")
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        _owned_fds.add(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"real network lock must be a regular file: {path}")
        inherited = _inherited_lock(info, fd)
        if inherited is not None:
            _owned_fds.add(inherited)
            _owned_fds.remove(fd)
            os.close(fd)
            fd = inherited
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RealNetworkBusy(f"real network busy: {path} is held by another test/server; retry after it stops") from None
        yield
    finally:
        if fd is not None:
            _owned_fds.discard(fd)
            os.close(fd)
        _guard.release()


def serial_real_test(function):
    """Guard a CLI entry point through its cleanup; preserve prerequisite exit codes."""
    @wraps(function)
    def run(*args, **kwargs):
        # Non-root prerequisite checks and compile-only probes do not start a network.
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return function(*args, **kwargs)
        try:
            with real_network_lock():
                return function(*args, **kwargs)
        except RealNetworkBusy as error:
            print(str(error), file=sys.stderr)
            return 1
    return run
