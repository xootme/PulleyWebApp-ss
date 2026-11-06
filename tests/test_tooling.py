"""The test tooling's own settings: pytest.ini loads the shared recorder and names
caches that exist, nightly.json runs what it should, and the release script's
version rules hold (tools/release.py; its deploys are checked by its --dry-run and
--smoke, never here). Kept out of test_affected.py, which names every file and so
is skipped when a non-Python file's tests are looked up (cct_common.testing.affected
naming_skip): a control on pytest.ini needs a test here to catch it."""
import configparser
import importlib
import json
import os
import re
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


def test_each_worker_logs_in_a_folder_of_its_own():
    """conftest's logs folder is this process's, not one inherited from xdist's controller
    (shared, the workers' download-failure stores locked each other, 2026-10-31)."""
    import os
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    assert os.environ.get("PULLEY_LOG_DIR_FOR") == worker
    assert Path(os.environ["PULLEY_LOG_DIR"]).name.startswith(f"pulley-tests-{worker}-")


def test_the_linux_binary_check_is_exact():
    """tools/linux_binary_check.py: since small_step 0.18.4 (ss-core's dmath) the Linux build's
    STEP is the Windows build's byte for byte; a signed zero or a last digit is a difference."""
    import linux_binary_check as L
    a = b"#7=CARTESIAN_POINT('',(1.5,0.,-2.));\n"
    p = b"#8609=CARTESIAN_POINT('',(3.463133033888,-2.002253107701,16.812407850868));\n"
    assert L.compare(a, a) == ("exact", 0.0)
    assert L.compare(a, b"#7=CARTESIAN_POINT('',(1.5,-0.,-2.));\n")[0] is None
    assert L.compare(p, p.replace(b"850868", b"850869"))[0] is None


def test_the_linux_binary_check_tolerant_allows_only_the_last_bit():
    """--tolerant (builds before 0.18.4): but for the two maths libraries rounding the last bit,
    a signed zero ("-0." against "0.") and a number's last printed digit (at most 1e-12 mm);
    anything else is a difference."""
    import linux_binary_check as L
    cmp = lambda x, y: L.compare(x, y, tolerant=True)   # noqa: E731
    a = b"#7=CARTESIAN_POINT('',(1.5,0.,-2.));\n"
    assert cmp(a, a) == ("exact", 0.0)
    assert cmp(a, b"#7=CARTESIAN_POINT('',(1.5,-0.,-2.));\n")[0] == "signed zero"
    assert cmp(b"(-0.,1.)", b"(0.,1.)")[0] == "signed zero"
    p = b"#8609=CARTESIAN_POINT('',(3.463133033888,-2.002253107701,16.812407850868));\n"
    rule, d = cmp(p, p.replace(b"850868", b"850869"))           # the 0.18.2 samples' kind
    assert rule == "last digit" and 0.9e-12 < d < 1.1e-12
    assert cmp(p, p.replace(b"850868", b"850870"))[0] is None   # two units
    assert cmp(p, p.replace(b"16.812407850868", b"16.81240785087"))[0] is None   # 2e-12
    t = b"#18545=CARTESIAN_POINT('',(7.143232341201,-0.166260908246,3.94658513077));\n"
    assert cmp(t, t.replace(b"7.143232341201", b"7.1432323412"))[0] == "last digit"   # ...200
    assert cmp(t, t.replace(b"7.143232341201", b"7.14323234"))[0] is None   # 1.2e-9
    assert cmp(b"(1.5,-0.5,-2.)", b"(1.5,0.5,-2.)")[0] is None   # a real number's sign
    assert cmp(a, b"#7=CARTESIAN_POINT('',(1.5,0.,-2.1));\n")[0] is None
    assert cmp(b"#7=LINE('',#5,#6);\n", b"#7=LINE('',#5,#8);\n")[0] is None   # a reference
    assert cmp(a, a + b"#8=VECTOR('',#9,1.);\n")[0] is None                   # a line more


def test_the_linux_binary_check_without_wsl_compared_nothing(capsys):
    """No WSL distro by that name: exit 2, "nothing compared", not a pass or a difference."""
    import linux_binary_check as L
    assert L.main(["--distro", "no-such-distro-cct"]) == 2
    assert "nothing compared" in capsys.readouterr().out


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


def test_every_version_file_says_the_apps_version():
    """One number (baseline finding 5, the owner 2026-11-04): APP_VERSION, which the release
    bumps and the dashboard reads, and every other file that names it, version.txt (the desktop
    build's) included. version.txt said 1.2.1 while the app was 2.0.33."""
    import release
    cur = release.current_version()
    assert "version.txt" in [p for p, _ in release.VERSION_FILES]      # the release bumps it too
    for path, pat in release.VERSION_FILES:
        m = re.search(pat, (ROOT / path).read_text(encoding="utf-8"), re.M)
        assert m and m.group(2) == cur, (path, m and m.group(2), cur)


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


def _harnesses_in(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import harnesses
    monkeypatch.setattr(harnesses, "FAILED_RUNS", tmp_path / "failures")
    monkeypatch.setattr(harnesses, "NODE_REPORTS", tmp_path / "failures" / "node_reports")
    return harnesses


def test_a_harness_crash_leaves_nodes_report(tmp_path, monkeypatch, capsys):
    """tools/harnesses.py runs node with its crash reports on: a harness that dies is named with
    node's report, its event and its top frames, in the output and the kept failure (roundtrip_ui
    died with only "exit 3221226505", 2026-11-02)."""
    import shutil as _shutil
    if not _shutil.which("node"):
        pytest.skip("no node")
    harnesses = _harnesses_in(tmp_path, monkeypatch)
    results = []
    harnesses.run("crash", ["node", "-e", "function deep() { throw new Error('boom') } deep()"], dict(os.environ), results)
    out = capsys.readouterr().out
    assert results[0][1] is False
    assert "node report: " in out and "event: " in out and "boom" in out and "  js: " in out
    kept = list((tmp_path / "failures").glob("*-crash-*.txt"))
    assert kept and "node report: " in kept[0].read_text(encoding="utf-8")


def test_a_passed_harness_leaves_no_report_folder(tmp_path, monkeypatch, capsys):
    import shutil as _shutil
    if not _shutil.which("node"):
        pytest.skip("no node")
    harnesses = _harnesses_in(tmp_path, monkeypatch)
    results = []
    harnesses.run("fine", ["node", "-e", "console.log('1/1 passed')"], dict(os.environ), results)
    assert results[0][1] is True
    assert not list((tmp_path / "failures" / "node_reports").glob("*fine*"))


def test_health_names_the_running_small_step(client):
    """/api/health's small_step is the binary's own --version (the release smoke test reads it)."""
    import os
    from app import _SS_BIN
    from exporters.step_worker_ss import _binary_version
    if not (_SS_BIN and os.path.isfile(_SS_BIN)):
        pytest.skip("no small_step binary")
    got = client.get("/api/health").get_json()["small_step"]
    assert got == ".".join(map(str, _binary_version(_SS_BIN))) and got.count(".") == 2


@pytest.mark.parametrize("health, want", [
    ({"version": "2.0.33", "small_step": "0.18.2"}, []),
    ({"version": "2.0.33", "small_step": "0.19.0"}, []),
    ({"version": "2.0.33", "small_step": "0.13.4"}, ["below the minimum 0.18.2"]),
    ({"version": "2.0.33", "small_step": None}, ["below the minimum 0.18.2"]),
    ({"version": "2.0.32", "small_step": "0.18.2"}, ["version 2.0.32 (want 2.0.33)"]),
], ids=["the minimum", "newer", "0.13.4", "none", "the old revision"])
def test_the_smoke_test_holds_the_new_revisions_small_step(monkeypatch, capsys, health, want):
    """release.smoke fails (so rolls back) unless the new revision's own health names a small_step
    at least SMALL_STEP_MIN_VERSION, and prints it for the morning report."""
    import json as _json
    import release
    asked = []
    monkeypatch.setattr(release, "small_step_min", lambda: (0, 18, 2))
    monkeypatch.setattr(release, "http", lambda url, data=None, tries=4: (asked.append(url), (200, _json.dumps(health)))[1])
    bad = release.small_step_check("pulley", "2.0.33", "https://example.test/tools/pulleys")
    assert len(bad) == len(want) and all(w in b for w, b in zip(want, bad)), bad
    assert asked[0].startswith("https://example.test/tools/pulleys/api/health?fresh=")   # never a cached answer
    assert f"pulley: small_step {health['small_step']} (minimum 0.18.2)" in capsys.readouterr().out


def test_the_smoke_tests_minimum_is_the_released_one():
    import inspect
    import release
    from exporters.step_worker_ss import SMALL_STEP_MIN_VERSION
    assert release.small_step_min() == SMALL_STEP_MIN_VERSION
    assert "bad += small_step_check(name, version, base)" in inspect.getsource(release.smoke)


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


def test_the_ledger_is_read_as_of_the_time(monkeypatch):
    """Without a Postgres (the night has none, so the test below skips there): both totals are
    asked as of the time, the accounts by created_at and the ledger by ts, and nothing else."""
    import types
    import release
    asked = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            asked.append((sql, params))
            return types.SimpleNamespace(fetchone=lambda: (len(asked),))
    monkeypatch.setitem(sys.modules, "psycopg",
                        types.SimpleNamespace(connect=lambda *a, **k: Conn(), Error=Exception))
    assert release.ledger_totals("postgresql://test", 1_000_001.0) == (1, 2)
    assert asked == [("SELECT COUNT(*) FROM accounts WHERE created_at < %s", (1_000_001.0,)),
                     ("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE ts < %s", (1_000_001.0,))]


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
