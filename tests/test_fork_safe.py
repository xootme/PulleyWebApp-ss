"""A gunicorn worker forked from the master (preload_app) gets the app's locks unlocked and
its own metrics sampler (cct_common.forksafe, 2026-10-28). Before, a worker forked while
the master's sampler held _request_count_lock hung on every request, and no worker's
requests were sampled: the sampler ran in the master only.

Needs os.fork, so it runs in WSL, not on Windows or the Windows night host:
  QUEUE_DISABLED=1 ~/venvs/pulleywebapp-ss/bin/python -m pytest --noconftest tests/test_fork_safe.py
(in a Linux copy; --noconftest as that venv has no pytest-xdist, which conftest hooks).
tools/fork_probe.py does the same under gunicorn itself."""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, 'fork'), reason='needs os.fork (Linux; run in WSL)')

HANG, NO_SAMPLER = 2, 3


def _child_exit_code(check, *, held=None):
    """Fork (with `held`, a lock, taken by another thread of this process meanwhile, as the
    master's sampler takes its lock); run check() in the child; its exit code."""
    took, done = threading.Event(), threading.Event()
    if held is not None:
        def hold():
            with held:
                took.set()
                done.wait(30)
        threading.Thread(target=hold, daemon=True).start()
        assert took.wait(5)
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            code = check()
        finally:
            os._exit(code)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            got, status = os.waitpid(pid, os.WNOHANG)
            if got:
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.05)
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        return None
    finally:
        done.set()


def _returns_within(fn, seconds=3):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(seconds)
    return not t.is_alive()


@pytest.mark.parametrize('name', ['_request_count_lock', '_download_lock', '_subscribers_lock', '_purchases_lock'])
def test_a_worker_forked_while_a_lock_is_held_can_take_it(name):
    import app

    def take():
        lock = getattr(app, name)
        if not _returns_within(lambda: lock.acquire() and lock.release()):
            return HANG
        return 0 if name != '_request_count_lock' or _returns_within(app._increment_request_count) else HANG
    assert _child_exit_code(take, held=getattr(app, name)) == 0


def test_a_worker_samples_its_own_requests():
    import app

    def sampler_running():
        return 0 if any(t.name == 'pulley-metrics' for t in threading.enumerate()) else NO_SAMPLER
    assert _child_exit_code(sampler_running) == 0
