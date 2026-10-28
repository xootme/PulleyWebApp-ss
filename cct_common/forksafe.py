"""forksafe.py — module-level locks a forked child can use (0.26.0).

gunicorn's preload_app imports the app in the master and then forks the
workers. A fork copies every lock as it stands, but only the forking
thread: a lock some other master thread held at that instant (job_queue's
queue processor takes its lock every second) arrives in the worker locked,
with no thread left to release it, and the worker's first request to take
it hangs until gunicorn's timeout kills it (pulley, 2026-10-27: a download
bundle, WORKER TIMEOUT, a 500).

So a module that keeps a lock at module level renews it in the child:

    _LOCK = threading.Lock()
    forksafe.renew_after_fork(globals(), "_LOCK")

and code that must also run in each child (job_queue's threads) registers
it with after_fork_in_child(fn). Both do nothing where there's no fork
(Windows). Code takes the lock by its global name (`with _LOCK:`), so the
renewed one is what it gets.
"""
from __future__ import annotations

import os
import threading


def after_fork_in_child(fn) -> None:
    """Run fn() in a forked child, right after the fork (no-op without fork)."""
    if hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=fn)


def renew_after_fork(namespace: dict, *names: str, factory=threading.Lock) -> None:
    """Replace namespace[name] with a new, unlocked lock in a forked child.
    `namespace` is the module's globals()."""
    def renew():
        for name in names:
            namespace[name] = factory()
    after_fork_in_child(renew)
