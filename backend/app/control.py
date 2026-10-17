"""Lets the owner stop a running sync at any time. One process runs the sync (see the single uvicorn worker),
so a plain in-memory flag is enough and needs no database change."""
import threading

cancel_sync = threading.Event()
STOPPED = "stopped by you"  # stored in sync_runs.error so the dashboard can tell a stop from a failure


class SyncCancelled(Exception):
    pass


def cancellable_sleep(seconds: float):
    """Sleep, but wake up immediately (and abort) if the owner pressed Stop."""
    if cancel_sync.wait(seconds):
        raise SyncCancelled()
