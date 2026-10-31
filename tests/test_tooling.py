"""The test tooling's own settings: pytest.ini loads the shared recorder and names
caches that exist, nightly.json runs what it should, and the release script's
version rules hold (tools/release.py; its deploys are checked by its --dry-run and
--smoke, never here). Kept out of test_affected.py, which names every file and so
is skipped when a non-Python file's tests are looked up (cct_common.testing.affected
naming_skip): a control on pytest.ini needs a test here to catch it."""
import configparser
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _ini():
    cp = configparser.ConfigParser()
    cp.read(ROOT / "pytest.ini", encoding="utf-8")
    return cp["pytest"]


def test_every_suite_run_can_record():
    assert "-p cct_common.testing.record" in _ini()["addopts"]


def test_the_listed_caches_are_dicts():
    specs = _ini()["cct_test_caches"].split()
    assert specs
    for spec in specs:
        module, _, attr = spec.partition(":")
        assert isinstance(getattr(importlib.import_module(module), attr), dict), spec


def test_nightly_runs_the_suite_and_the_release():
    cfg = json.loads((ROOT / "nightly.json").read_text(encoding="utf-8"))
    assert cfg["test"][1:] == ["{worktree}/tools/nightly_test.py", "--keep-logs", "{repo}"]
    assert cfg["release"][1:] == ["{worktree}/tools/release.py"]


def test_the_night_runs_every_browser_harness():
    """The owner, 2026-10-25: every harness, every night (dims_ui's regression sat live
    10-08 to 10-23 with none run); a new harness file joins tools/harnesses.py's list."""
    import harnesses
    files = {p.stem for p in (ROOT / "tests" / "browser").glob("*.js")}
    assert files == set(harnesses.HARNESSES) - {"tokens_off"}
    assert "tokens_off" in harnesses.HARNESSES                  # tokens_ui.js with tokens off too
    assert "'tools' / 'harnesses.py'" in (ROOT / "tools" / "nightly_test.py").read_text(encoding="utf-8")


def test_the_release_version(monkeypatch):
    import release
    monkeypatch.delenv("CCT_RELEASE_VERSION", raising=False)
    assert release.next_version("2.0.28") == "2.0.29"
    monkeypatch.setenv("CCT_RELEASE_VERSION", "2.1.0")
    assert release.next_version("2.0.28") == "2.1.0"
    for bad in ("2.0.28", "2.0.3", "2.1"):
        monkeypatch.setenv("CCT_RELEASE_VERSION", bad)
        with pytest.raises(release.Fail):
            release.next_version("2.0.28")


def test_the_release_bumps_both_places(tmp_path, monkeypatch):
    import release
    for path, _ in release.VERSION_FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes((ROOT / path).read_bytes())
    monkeypatch.setattr(release, "ROOT", tmp_path)
    release.bump("9.9.9")
    assert release.current_version() == "9.9.9"
    for path, _ in release.VERSION_FILES:
        assert b"9.9.9" in (tmp_path / path).read_bytes(), path


def _release_with_ledgers(monkeypatch, after):
    """release() with Cloud Run, git and the smoke test stubbed: each service's ledger reads
    (2 accounts, 50 tokens) before its deploy and `after[name]` after it."""
    import release
    calls, reads = [], {}
    monkeypatch.setattr(release, "preflight", lambda: ("2.0.31", "2.0.32", {"pulley-test": {"t1": 100}, "pulley": {"p1": 100}}))
    monkeypatch.setattr(release, "bump", lambda new: None)
    monkeypatch.setattr(release, "git", lambda *a, **k: "")
    monkeypatch.setattr(release, "deploy", lambda name: name + "-new")
    monkeypatch.setattr(release, "smoke", lambda name, version, rev: [])
    monkeypatch.setattr(release, "database_url", lambda name: name)
    monkeypatch.setattr(release, "http", lambda url, data=None, tries=4: (200, "Ver. 2.0.31"))
    monkeypatch.setattr(release, "roll_back", lambda name, before: calls.append((name, before)))

    def totals(url, upto):
        reads[url] = reads.get(url, 0) + 1
        return (2, 50) if reads[url] == 1 else after[url]
    monkeypatch.setattr(release, "ledger_totals", totals)
    return release, calls


def test_the_release_keeps_every_balance(monkeypatch, capsys):
    release, calls = _release_with_ledgers(monkeypatch, {"pulley-test": (2, 50), "pulley": (2, 50)})
    release.release()
    out = capsys.readouterr().out
    assert "RELEASED 2.0.32" in out and not calls
    assert "pulley: the ledger before: 2 accounts, 50 tokens" in out


@pytest.mark.parametrize("after", [(1, 50), (2, 30)])
def test_a_ledger_changed_by_the_deploy_rolls_prod_back(monkeypatch, capsys, after):
    release, calls = _release_with_ledgers(monkeypatch, {"pulley-test": (2, 50), "pulley": after})
    with pytest.raises(release.Fail, match="prod rolled back"):
        release.release()
    assert calls == [("pulley", {"p1": 100})]
    assert f"{after[0]} accounts, {after[1]} tokens after" in capsys.readouterr().out


def test_a_test_ledger_changed_leaves_prod_alone(monkeypatch):
    release, calls = _release_with_ledgers(monkeypatch, {"pulley-test": (2, 0), "pulley": (2, 50)})
    with pytest.raises(release.Fail, match="prod was not touched"):
        release.release()
    assert not calls


@pytest.fixture
def pg_url():
    """A Postgres URL on a schema made for this test alone (skipped unless CCT_TEST_POSTGRES
    points at a server, e.g. the local cct-pg container)."""
    import os
    import uuid
    pg = os.environ.get("CCT_TEST_POSTGRES", "").strip()
    if not pg:
        pytest.skip("CCT_TEST_POSTGRES not set")
    import psycopg
    from cct_common.sqlite_db import close_pool
    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{pg}{'&' if '?' in pg else '?'}options=-csearch_path%3D{schema}"
    try:
        yield url
    finally:
        close_pool(url)
        with psycopg.connect(pg, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


def test_the_ledger_totals_as_of_a_time(pg_url):
    """The accounts and the sum of every balance written before the time; later work is left out,
    and a lost entry or account shows."""
    import psycopg
    import release
    from cct_common.tokens import TokenStore
    now = [1_000_000.0]
    store = TokenStore(pg_url, clock=lambda: now[0])
    a = store.get_or_create_account("a@example.com", signup_grant=20)
    store.credit(a, 30)
    b = store.get_or_create_account("b@example.com", signup_grant=20)
    assert release.ledger_totals(pg_url, 1_000_001.0) == (2, 70)
    now[0] = 1_000_100.0                                  # during the deploy
    store.get_or_create_account("c@example.com", signup_grant=20)
    store.credit(b, 5)
    assert release.ledger_totals(pg_url, 1_000_001.0) == (2, 70)
    assert release.ledger_kept("pulley", pg_url, 1_000_001.0, (2, 70)) == []
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("DELETE FROM ledger WHERE account_id = %s AND kind = 'purchase'", (a,))
    assert release.ledger_totals(pg_url, 1_000_001.0) == (2, 40)
    assert release.ledger_kept("pulley", pg_url, 1_000_001.0, (2, 70))


def test_an_account_deleted_during_the_deploy_keeps_the_totals(pg_url):
    """Deleting an account (the person's own, or housekeeping's) keeps its accounts row and its
    ledger rows (financial records; only the email goes), so the ledger as of before the deploy
    is the same after it: a deletion can't roll a good release back."""
    import release
    from cct_common.accounts import AccountStore
    from cct_common.tokens import TokenStore
    now = [1_000_000.0]
    tokens = TokenStore(pg_url, clock=lambda: now[0])
    accounts = AccountStore(tokens, signup_grant=20, clock=lambda: now[0])
    a = accounts.sign_in("email", "a@example.com", "a@example.com", email_verified=True)
    tokens.credit(a, 40, ref="order:1")
    accounts.sign_in("email", "b@example.com", "b@example.com", email_verified=True)
    held = release.ledger_totals(pg_url, 1_000_001.0)
    assert held == (2, 80)
    now[0] = 1_000_100.0                                  # during the deploy
    accounts.delete_account(a)
    assert release.ledger_kept("pulley", pg_url, 1_000_001.0, held) == []
