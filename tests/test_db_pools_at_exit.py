"""A server process closes its own database pools at exit, before Python's finalization: an open
pool's __del__ joins its threads there, which Python 3.14 refuses, and the traceback it logged at
every instance stop failed 2.0.38's release smoke test (pulley-test, 2026-11-17). A pool another
process opened (gunicorn's preloaded master's) is not closed by a worker. Since cct_common 0.62.10
the close is cct_common.sqlite_db's own (close_pools, registered at its import), and the app's
hook is gone; importing the app registers it. No Postgres needed."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Held:                      # stands in for a psycopg_pool pool
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_its_own_pools_are_closed_and_anothers_left():
    import app  # noqa: F401  (the app imports the pools' module)
    from cct_common import sqlite_db
    own, other = Held(), Held()
    own_key = (os.getpid(), 'postgresql://u@h/own')
    other_key = (os.getpid() + 1, 'postgresql://u@h/master')
    sqlite_db._POOLS[own_key] = own
    sqlite_db._POOLS[other_key] = other
    try:
        assert sqlite_db.close_pools() >= 1          # this worker may hold a real one too
        assert own.closed and own_key not in sqlite_db._POOLS
        assert not other.closed and sqlite_db._POOLS[other_key] is other
    finally:
        sqlite_db._POOLS.pop(own_key, None)
        sqlite_db._POOLS.pop(other_key, None)


PROBE = '''
import os, sys
sys.path.insert(0, {root!r})
import app
from cct_common import sqlite_db

class Held:
    def close(self):
        print("closed at exit", flush=True)

sqlite_db._POOLS[(os.getpid(), "postgresql://u@h/cct")] = Held()
print("imported", flush=True)
'''


def test_the_close_runs_when_the_process_exits(tmp_path):
    probe = tmp_path / 'probe.py'
    probe.write_text(PROBE.format(root=str(ROOT)), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'DATABASE_URL'}
    r = subprocess.run([sys.executable, str(probe)], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace', timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    assert r.stdout.splitlines()[-2:] == ['imported', 'closed at exit'], r.stdout[-2000:]
