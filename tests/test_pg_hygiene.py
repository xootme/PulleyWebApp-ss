"""The test Postgres in conftest (CCTCommon's msi run, 2026-11-07): its password goes to
PGPASSWORD, so no test URL (printed in a traceback) carries it, and a test that leaves a
connection pool open on its test schema fails. No Postgres needed."""
import os
import subprocess
import sys
from pathlib import Path

import conftest

ROOT = Path(__file__).resolve().parent.parent


def test_the_password_goes_to_pgpassword(monkeypatch):
    monkeypatch.delenv('PGPASSWORD', raising=False)
    bare = conftest._password_to_env('postgresql://postgres:p%40ss:w@127.0.0.1:5432/cct?sslmode=disable')
    assert bare == 'postgresql://postgres@127.0.0.1:5432/cct?sslmode=disable'
    assert os.environ['PGPASSWORD'] == 'p@ss:w'


def test_a_url_without_a_password_is_kept(monkeypatch):
    monkeypatch.delenv('PGPASSWORD', raising=False)
    assert conftest._password_to_env('postgresql://postgres@127.0.0.1/cct') == 'postgresql://postgres@127.0.0.1/cct'
    assert 'PGPASSWORD' not in os.environ


PROBE = '''
import os
from cct_common import sqlite_db

class Held:                      # stands in for a psycopg_pool pool
    def close(self):
        pass

def test_leaves_a_pool_open():
    sqlite_db._POOLS[(os.getpid(), "postgresql://u@h/cct?options=-csearch_path%3Dt_left")] = Held()

def test_closes_its_pool():
    url = "postgresql://u@h/cct?options=-csearch_path%3Dt_closed"
    sqlite_db._POOLS[(os.getpid(), url)] = Held()
    sqlite_db.close_pool(url)

def test_the_url_has_no_password():
    assert os.environ["CCT_TEST_POSTGRES"] == "postgresql://u@h/cct" and os.environ["PGPASSWORD"] == "pw"
'''


def test_a_pool_left_open_fails_its_test(tmp_path):
    # this repo's conftest by its path, as test_night_skips does
    (tmp_path / 'conftest.py').write_text(
        f'import importlib.util, sys\nsys.path[:0] = [{str(ROOT)!r}, {str(ROOT / "tests")!r}]\n'
        f'spec = importlib.util.spec_from_file_location("pulley_conftest", {str(ROOT / "tests" / "conftest.py")!r})\n'
        'repo = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(repo)\n'
        '_no_test_pool_left_open = repo._no_test_pool_left_open\n', encoding='utf-8')
    (tmp_path / 'test_probe.py').write_text(PROBE, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in ('PULLEY_NIGHTLY', 'PGPASSWORD')}
    env['CCT_TEST_POSTGRES'] = 'postgresql://u:pw@h/cct'     # never connected to
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:xdist',
                        '-rfE', str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert '3 passed, 1 error' in r.stdout, r.stdout
    assert 'left connection pools open (closed now): t_left' in r.stdout, r.stdout
    assert 'pw@' not in r.stdout + r.stderr
