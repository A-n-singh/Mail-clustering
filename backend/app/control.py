"""Lets a user stop their running sync at any time, and tells the dashboard what is really running. The server is ONE process, so plain
in-memory flags are enough and need no database change. Every user has their own set of flags (keyed by their data key, "main" for
the owner), so one user's Stop button or "Rebuilding..." can never touch another user's.

The module-level names (control.cancel_sync, control.sync_active, ...) always mean the flags of the CURRENT user: the dashboard's
requests and the background jobs select it with use() / scope(); code that never selects one (tests, single-user tools) gets "main"."""
import threading
from contextlib import contextmanager
from contextvars import ContextVar

STOPPED = "stopped by you"  # stored in sync_runs.error so the dashboard can tell a stop from a failure


class Flags:
    def __init__(self):
        self.cancel_sync = threading.Event()
        # "Is something really running right now?" The database cannot tell: if the server restarts mid-sync, the unfinished
        # row stays behind and would look like a running sync forever. These in-memory flags are the truth for this process.
        self.sync_active = threading.Event()
        self.topics_active = threading.Event()
        self.refill_active = threading.Event()  # older mail is being pulled in after mail left (trash / Gmail-side delete)


_registry: dict[str, Flags] = {}
_lock = threading.Lock()
_current: ContextVar[str] = ContextVar("flags_key", default="main")


def flags(key: str | None = None) -> Flags:
    key = key or _current.get()
    with _lock:
        return _registry.setdefault(key, Flags())


def use(key: str):
    """Make `key` the current user for this request / thread."""
    _current.set(key)


@contextmanager
def scope(key: str):
    token = _current.set(key)
    try:
        yield flags(key)
    finally:
        _current.reset(token)


def reset_all():
    with _lock:
        for f in _registry.values():
            for e in (f.cancel_sync, f.sync_active, f.topics_active, f.refill_active):
                e.clear()


class _Flag:
    """Looks up the current user's Event each time it is used."""
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr):
        return getattr(getattr(flags(), self._name), attr)


cancel_sync = _Flag("cancel_sync")
sync_active = _Flag("sync_active")
topics_active = _Flag("topics_active")
refill_active = _Flag("refill_active")


class SyncCancelled(Exception):
    pass


def cancellable_sleep(seconds: float):
    """Sleep, but wake up immediately (and abort) if the user pressed Stop."""
    if cancel_sync.wait(seconds):
        raise SyncCancelled()
