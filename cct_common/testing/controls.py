"""Negative controls: break one formula or check at a time; the tests meant
to catch it must fail; then restore (cct_common 0.25.5; Bevel Gear Designer's
tools/neg_controls.py, its ADR-031, Planetary's before it). An app keeps its
controls as data and hands them over, after configuring
cct_common.testing.affected:

    BREAKS = {"pitch angle": ("geometry/bevel.py", "math.atan2(a, b)", "math.atan2(b, a)"), ...}
    sys.exit(controls.main(BREAKS, sys.argv[1:]))       # [name filter ...] [--all] [--whole-suite]

- **Which controls.** With no filter, those whose check changed since each
  was last caught (neg_state.json, beside affected's record outside the
  repo, so a clean worktree reuses the main folder's catches): the control
  itself, the function it breaks (or its file's module level, or the file
  when it isn't Python), or the test that caught it last time: its file,
  the test files it imports, the conftest.py files over it and pytest's
  config (catcher_deps; 0.30.0, Bevel's a1). Before, a control ran again
  when any test file in its scope changed: 225 of Bevel's 296 after three
  commits to test files, past the night's limit. A control caught before
  0.30.0 has no catcher on record and keeps the old rule until it next runs.
  A filter runs every control whose name holds it; --all runs them all.
  --rekey-bare also runs, once, every control saved that way (a bare key),
  so each is re-saved with its catcher (0.62.9, the owner's "bare b": the
  old runner counted any exit but 0 and 5 as a catch). The run's header
  counts the bare-key reruns and the bare keys still current, and lists as
  "stale state" the saved entries no control has now (a control removed or
  renamed): they stay in the file, and are never run or counted.
  Every control is checked for STALE (its pattern not once in its file),
  filter or not (0.56.9).
- **Which tests.** A control with a catcher on record runs it first, alone;
  if it no longer catches, the control's scope and then the whole suites, as
  below, and the test that caught it is the new catcher (pytest's FAILED or
  ERROR line). Each control's scope is the *tests* that ran the function
  it breaks (cct_common.testing.record's record, per test, from the last
  fast and slow suites: an app whose checks are @slow has its controls
  caught there, 0.25.1), cheapest first (their seconds in the record), run
  with -x (cct_common.testing.select keeps that order, and the run lifts
  pytest.ini's -m "not slow" for them): the first that catches it ends the
  run. By file instead, Bevel's controls ran 12-80 min each on
  18-24 files, mostly tests that pass before the one that fails; by test,
  the same six took 61 s against 23.6 min (2026-10-21). At the module level,
  the files that import the file; a file that isn't Python, those that name
  it. A control its scope doesn't catch runs again on the whole fast suite,
  then on the whole slow suite, and is reported SCOPE (a whole suite caught
  it, the scope didn't: a test served from a cache another test filled) or
  MISSED. --whole-suite runs every control on the whole suites, fast first.
  A run that ran no test (pytest's exit 5) caught nothing.
- **Not by a test that fails anyway** (0.62.12, the owner's "scope credit
  a"; Pulley's nights of 11-15 to 11-17, where one test failing in every
  lane copy was credited with four controls). Once per run, before any
  control, the whole fast suite runs on an unbroken copy, every test, no
  -x (the slow suite too, the first time a control or a catcher needs it).
  Each test that fails there is listed ("fails unbroken: <test>"), left out
  of every control's runs, scope and whole suites alike, and never saved as
  a catcher; a saved catcher that fails there is dropped and its control
  runs its scope instead, so an old false catch is repaired the first time
  its control runs. A control only such a test would catch reads MISSED.
- **Not by the controls' own check.** An app's test that checks its
  controls (each one's text once in its file: Bevel's
  test_every_control_has_a_scope) fails in every lane, as the break takes
  that text out. Every run has CCT_NEG_CONTROL set to the control's name,
  and such a test skips when it is set; the whole suites leave out the
  tests that use the app's controls module, too, so an app that forgot the
  skip still can't catch a control with it (0.25.3: Worm's four SCOPE
  catches were that test, the side supports' own tests none of them). Since
  0.61.3 that is the tests, not their files, where it can be seen: in a test
  file that imports the module itself, only its test functions (and test
  classes' methods) that name it are left out; the file's other tests still
  run. The whole file is left out when the module is used anywhere else in
  it (at module level, in a helper or fixture) or reached only through
  another import. Before, the whole file always went: Planetary's
  test_affected.py imported neg_controls for two listing tests, and a
  control on test_all.py that only that file's checks caught was MISSED.
  Keeping the listing tests in a file of their own is still the clearest.
  The run says what it leaves out.
- **In lanes.** Each lane breaks its own copy of the tree (git's files and
  .git, in a temporary folder), so CCT_TEST_CPUS controls run at once, each
  on one process; a control on the whole suite runs after them, one at a
  time, on all the turn's workers. The lanes' folder (<APP>-neg-* in Temp)
  goes at the end of the run, tried again for up to about 10 s while
  Windows still holds it; one left anyway keeps its mark (cct-neg-run.json)
  and a later run of the same app sweeps it once a day old (0.61.7;
  Sprocket's gate of 2026-11-11 left its lanes on WinError 32).
- **A sync's changes** (0.61.2; Sprocket's night of 2026-11-06). The catcher
  key also holds every function of the vendored packages (affected's
  `vendored`, cct_common/ by default) that the catcher test ran, by the
  record, hashed from the files themselves (an app may fold its vendored
  tree into one snapshot key). A sync that changes one of them reruns the
  control by day, catcher first, and the run names what changed: a MISSED
  there is often the sync making the app's own code dead (Sprocket's Import
  fallback after 0.58.1). A function the sync adds counts once the record
  shows the catcher running it. A control saved before this keeps its old
  key until it next runs. Measured on the apps' records: a one-release sync
  reruns 0-7 controls, a five-release jump 7-85 (Bevel's 413).
- **The command line** (0.61.1, Bevel's tools/neg_controls.py cli): --help
  or -h prints the usage and exits 0 before anything is looked up or run,
  and an option it doesn't know exits 2 with the usage. Before, both were
  taken as "no filter" and ran every changed control (three apps typed
  --help to read the options on 2026-11-05; Planetary's ran all 291)."""
import argparse
import ast
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import affected

# the native thread pools a lane holds to one thread, as its one CPU
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS", "VECLIB_MAXIMUM_THREADS")
SLOW_SUITE = ["<the whole slow suite>"]                # run_control's `files` for pytest -m slow on tests/
LANE_MAX = 30          # a scope of more tests than this runs on the turn's workers, not in a lane (0.25.2)
LANE_MAX_FILES = 4     # ... or of more test files, when no test is on record
BUDGET = 300           # s a scope may run before the whole suite takes over (CCT_CONTROL_BUDGET; 0.25.2)
# pytest's short summary: the first test that failed, or the file that didn't collect (0.30.0)
CAUGHT_BY = re.compile(r"^(?:FAILED|ERROR) (tests/[^\s:]+\.py(?:::\S+)?)", re.M)
# pytest's config, whichever an app has: a catcher's catching depends on it too
CONFIG = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
_print = threading.Lock()


def say(*a):
    with _print:
        print(*a, flush=True)


def test_cpus(env=None) -> int:
    """CCT_TEST_CPUS when it is a whole number >= 1 (the test turn's), else os.cpu_count() // 2."""
    env = os.environ if env is None else env
    try:
        n = int(env.get("CCT_TEST_CPUS", ""))
        if n >= 1:
            return n
    except ValueError:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def state_path() -> Path:
    return affected.record_dir() / "neg_state.json"


# -- each control's tests ----------------------------------------------------
def scope_index(state: dict, snap: dict) -> dict:
    """function key -> the tests (node ids) that ran it, fast and slow (the
    record of the last suite runs). A test is one or the other, so a node id
    is never in both records."""
    index = {}
    for kind in affected.KINDS:
        for node, e in affected.test_record(state, kind).items():
            if node.startswith("tests/"):
                for path, q in e["ran"]:
                    index.setdefault(affected.fn_key(path, q, (snap,)), set()).add(node)
    return index


def broken_lines(src: str, offset: int, length: int) -> list:
    """The lines a break at `offset` can change what runs on, as sets tried in
    turn: the smallest statement holding it; or, when it's in a compound
    statement's header (an `if`, `for`, `while`, `with` or `try` line), that
    statement's body, then its else and handlers. `if pocket:` -> `if False:`
    matters to the tests that ran the body, not to every test that ran the
    `if`: of Planetary's M4b's 204, 17 ran the pocket branch, 189 the else
    (2026-10-21)."""
    first = src.count("\n", 0, offset) + 1
    last = src.count("\n", 0, offset + max(length, 1) - 1) + 1
    best = None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.stmt) and node.lineno <= first and last <= node.end_lineno:
            if best is None or node.end_lineno - node.lineno < best.end_lineno - best.lineno:
                best = node
    if best is None:
        return [set(range(first, last + 1))]

    def span(nodes):
        return {ln for n in nodes for ln in range(n.lineno, n.end_lineno + 1)}
    body = getattr(best, "body", None) or []
    rest = [n for part in ("orelse", "handlers", "finalbody") for n in getattr(best, part, []) or []]
    if body and isinstance(body, list) and not isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
            and last < min(n.lineno for n in body + rest):
        return [s for s in (span(body), span(rest)) if s]
    return [set(range(best.lineno, best.end_lineno + 1))]


# A non-Python file no test names (a harness helper no test names or lists,
# affected.naming): the whole suites are its scope, and a catch there reads
# SCOPE (0.62.2).
NO_SCOPE = "no test names it or lists its folder: the whole suite"


def scope(path: str, old: str, index: dict, graph: dict, lines: dict | None = None):
    """(tests, how they were found, the snapshot key of what's broken): node
    ids, or test files when no test is on record as running it. An empty list
    is the whole fast suite. In a big function with a line record, the tests
    that ran the lines the break changes (broken_lines; 0.25.2)."""
    src = (affected.ROOT / path).read_text(encoding="utf-8")
    if not path.endswith(".py"):                 # a harness helper too (run_temp.js): those that name or list it
        named = affected.tests_naming([path])
        return (named, "they name it", path) if named else ([], NO_SCOPE, path)
    at = src.index(old)
    q = affected.enclosing(path, at)
    key = path + (affected.FN + q if q else affected.TOP)
    by_test = (lines or {}).get((path, q)) if q else None
    if by_test:
        for want in broken_lines(src, at, len(old)):                # the body, then the else
            hit = sorted(node for node, ran in by_test.items() if ran & want)
            if hit:
                return hit, f"they ran line {min(want)} of {q}", key
    if q and index.get(key):
        return sorted(index[key]), f"they ran {q}", key
    if path.startswith(("tests/", "tools/")):                  # the suite's own helpers and hooks
        stem = Path(path).stem
        named = [t for t in affected.all_tests()
                 if t != path and stem in (affected.ROOT / t).read_text(encoding="utf-8")]
        if named:
            return named, f"they name {stem}", key
    return affected.importers(path, graph), "they import it", key


def cheapest_first(tests, tstate: dict, direct=()) -> list:
    """A scope's tests (or test files) by their recorded seconds, so -x stops
    at the first that catches the control; one never recorded goes last.
    Those in `direct` test files (ones that import the broken file themselves,
    so likely check what it returns) go first: Planetary's M4b's 204 tests all
    run the broken branch, its catchers are in test_carrier.py (0.25.2)."""
    direct = set(direct)

    def secs(t):
        tests = tstate.get("tests", {})
        e = (tests.get(f"fast:{t}") or tests.get(f"slow:{t}")) if "::" in t else None
        return e["s"] if e else tstate.get("seconds", {}).get(f"fast:{t}", 1e9)
    return sorted(tests, key=lambda t: (t.split("::")[0] not in direct, secs(t), t))


def control_key(entry, files, key, snap) -> str:
    """What a control's being caught depends on: the control, what it breaks,
    and the tests meant to catch it."""
    tests = [(t, snap.get(t.split("::")[0])) for t in files]
    return hashlib.sha1(json.dumps([list(entry), snap.get(key), tests]).encode("utf-8")).hexdigest()


def catcher_deps(catcher: str, graph: dict) -> list:
    """What a catcher's catching depends on, on the tests' side (0.30.0): its
    test file, the test files that imports at any depth (helpers, and
    tests/conftest.py's), every conftest.py from the repo root down to it,
    and pytest's config. Not the app's own code: the function the control
    breaks is in its key already, and the rest changes every day."""
    test = catcher.split("::")[0]
    deps = {f for f in affected.reached(test, graph) if f.startswith("tests/")} | {test}
    folders = Path(test).parts[:-1]
    deps |= {"/".join([*folders[:i], "conftest.py"]) for i in range(len(folders) + 1)}
    return sorted(deps | set(CONFIG) | set(affected.EVERYTHING))


def catcher_key(entry, key, catcher: str, snap: dict, graph: dict, vendored=None) -> str:
    """A control caught by `catcher` stays caught while this is the same: the
    control, what it breaks, its catcher's side (catcher_deps) and, since
    0.61.2, the vendored functions its catcher ran (vendored_deps; None: a
    key saved before, without them)."""
    deps = [(f, snap.get(f)) for f in catcher_deps(catcher, graph)]
    parts = [list(entry), snap.get(key), catcher, deps]
    if vendored is not None:
        parts.append([list(v) for v in vendored])
    return hashlib.sha1(json.dumps(parts).encode("utf-8")).hexdigest()


def vendored_ran(state: dict) -> dict:
    """node id -> the vendored functions (path, outermost qualname) its test
    ran, by the record of the last suite runs."""
    out = {}
    for kind in affected.KINDS:
        for node, e in affected.test_record(state, kind).items():
            fns = {(p, q.split(".<locals>")[0]) for p, q in e["ran"] if p.startswith(affected.VENDORED)}
            if fns:
                out.setdefault(node, set()).update(fns)
    return out


# A test that checks a vendored copy's hash (a health test's cct_common_hash,
# test_vendored_cct_common's released copy) fails on every edit inside the
# vendored tree, whatever the edit does, so it is never a control's catcher
# there (Controller's ruling, 2026-11-12; Planetary: 'import: a renamed file
# read by what's in it' was saved as caught by test_health_names_the_versions,
# and the import tests that catch it by hand never ran). By the record: the
# tests that ran a vendored release.py's hash_files (content_hash, describe
# and health_fields all go through it). 0.62.4.
HASH_CHECK = ("release.py", "hash_files")


def hash_checks(ran: dict) -> set:
    """The node ids that ran a vendored release.py's hash_files (`ran`: vendored_ran)."""
    return {node for node, fns in ran.items()
            if any(p.endswith("/" + HASH_CHECK[0]) and q == HASH_CHECK[1] for p, q in fns)}


def left_out(path: str, checks, catcher=None) -> list:
    """The hash checks a control breaking `path` leaves out of its runs:
    every one when `path` is inside a vendored tree, none when it is the
    hash's own module (release.py: what they are there to catch) or outside
    the vendored trees, and never the control's declared `catcher` (a test
    file or a node id: `(path, old, new, {"catcher": ...})`, for a control
    meant to be caught by one; Planetary's 'a copy edited in place')."""
    if not path.startswith(affected.VENDORED) or path.endswith("/" + HASH_CHECK[0]):
        return []
    return sorted(n for n in checks if not (catcher and (n == catcher or n.split("::")[0] == catcher)))


class _VendoredHashes:
    """path -> {qualname: hash} of the vendored files as they are now, read once."""

    def __init__(self):
        self._files = {}

    def __call__(self, path: str, qualname: str):
        if path not in self._files:
            try:
                self._files[path] = affected.fn_hashes((affected.ROOT / path).read_bytes())
            except OSError:
                self._files[path] = {}
        return self._files[path].get(qualname)          # None: no such function now


def vendored_deps(catcher: str, ran: dict, hashes) -> list:
    """[(path::qualname, hash now)] of the vendored functions `catcher` ran."""
    return [(f"{p}{affected.FN}{q}", hashes(p, q)) for p, q in sorted(ran.get(catcher, ()))]


def is_current(saved, entry, key, files, snap: dict, graph: dict, vendored=None) -> bool:
    """Whether the control's last catch still holds: by its catcher's key
    (0.30.0, with the vendored functions it ran since 0.61.2: `vendored`,
    catcher -> vendored_deps), or, saved before that (a bare key), by the old
    rule's, every test in its scope (control_key)."""
    if isinstance(saved, dict):
        catcher = saved.get("catcher")
        if not catcher:
            return False
        vend = vendored(catcher) if (vendored is not None and "vendored" in saved) else None
        return saved.get("key") == catcher_key(entry, key, catcher, snap, graph, vend)
    return saved is not None and saved == control_key(entry, files, key, snap)


def sync_changes(saved, vendored) -> list:
    """The vendored functions a saved catch ran that are different now."""
    if not isinstance(saved, dict) or "vendored" not in saved or not saved.get("catcher") or vendored is None:
        return []
    now = dict(vendored(saved["catcher"]))
    return sorted(f for f, h in saved["vendored"] if now.get(f) != h)


def _state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# -- a copy of the tree per lane ---------------------------------------------
def copy_tree(dst: Path):
    """git's files (ignored ones left out), byte for byte, on a worktree of the
    repo's own at `dst` (`git worktree add --no-checkout`, its index read from
    HEAD), so git in the lane sees what it sees in the source: the same files,
    tracked or not, changed or not. 0.25.0 copied .git itself, which in a
    `git worktree add` checkout (every nightly run's) is a file, not a folder
    (NotADirectoryError, found by Pulley), and cost Pulley 551 MB a lane. A
    repo with no commit yet gets a `git init` of the lane's own. Bytecode is
    checked by the source's hash, not its size and time: a break the same
    length as its line, restored within the second, would otherwise load a
    stale .pyc (it did: Bevel, 2026-10-19). The runs write no bytecode."""
    root = affected.ROOT
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True,
                         check=True)
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root, capture_output=True, text=True)
    if head.returncode == 0:
        subprocess.run(["git", "worktree", "add", "-q", "--no-checkout", "--detach", str(dst), head.stdout.strip()],
                       cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "read-tree", "HEAD"], cwd=dst, check=True, capture_output=True)
    else:
        dst.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=dst, check=True, capture_output=True)
    for f in sorted(set(out.stdout.decode("utf-8").split("\0")) - {""}):
        if (root / f).is_file():
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, dst / f)
    subprocess.run([sys.executable, "-m", "compileall", "-q", "--invalidation-mode", "checked-hash",
                    "-x", r"[\\/]\.git[\\/]", str(dst)], check=True, capture_output=True)


# 0.61.7: about 10 s, as cdp.js 0.61.6 gives a Chrome profile (5 tries 1 s
# apart left Sprocket's lanes on 2026-11-11, as empty folders)
REMOVE_TRIES, REMOVE_WAIT = 21, 0.5
_sleep = time.sleep
MARK = "cct-neg-run.json"         # in a run's lanes' folder: what the sweep may take
STALE_S = 24 * 3600


def _remove(folder):
    """A lane gone, with its worktree's entry in the repo (that one only), or
    the lanes' folder. A file Windows holds a moment longer (an indexer, a
    scanner, a process still closing) is tried again, REMOVE_TRIES times
    REMOVE_WAIT s apart; past that, a warning and the folder is left: the
    cleanup never changes the run's result (0.51.1: Sprocket's night of
    2026-10-30 caught all 58, then WinError 32 on a lane failed the night)."""
    def unlock(fn, p, _):                                  # git's objects are read-only
        os.chmod(p, 0o700)
        fn(p)
    for i in range(REMOVE_TRIES):
        subprocess.run(["git", "worktree", "remove", "--force", str(folder)], cwd=affected.ROOT, capture_output=True)
        try:
            if Path(folder).exists():
                shutil.rmtree(folder, onexc=unlock)
            return
        except OSError as e:
            err = e
        if i + 1 < REMOVE_TRIES:
            _sleep(REMOVE_WAIT)
    say(f"warning: couldn't remove {folder} ({err}); left behind, the result stands")


def sweep_lanes(now=None):
    """At the start of a run: this app's lanes' folders a run left over a
    day ago, their lanes' worktree entries with them. Only marked ones (the
    folders before 0.61.7 stay for the owner's decision), never a fresh one
    (another run of the app may be using it) nor another app's. One try
    each, quietly: what won't go is tried again next time."""
    now = time.time() if now is None else now
    temp = Path(tempfile.gettempdir())
    try:
        names = [p for p in temp.iterdir() if p.name.startswith(f"{affected.APP}-neg-")]
    except OSError:
        return
    for folder in names:
        try:
            started = float(json.loads((folder / MARK).read_text(encoding="utf-8"))["started"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not now - started > STALE_S:
            continue
        for lane in [p for p in folder.iterdir() if p.is_dir()]:
            subprocess.run(["git", "worktree", "remove", "--force", str(lane)], cwd=affected.ROOT,
                           capture_output=True)
        shutil.rmtree(folder, ignore_errors=True)


STOPPED = "stopped"     # _run's answer for a run stopped at its first failure (0.62.1)
POLL = 0.5              # s between looks at the catch file


def read_caught(path):
    """The catch file's {"by", "when"}, or None while there is none (whole)."""
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return got if isinstance(got, dict) and got.get("by") is not None else None


def _stop(proc):
    """The command and every process it started: the whole tree, a server a
    test started included (taskkill /T; on POSIX its session's group)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def _run(cmd, cwd, env, limit, log=None, caught=None):
    """The command's exit code; None when it ran past `limit` seconds; or,
    with `caught` (a catch file's path, 0.62.1), STOPPED once that file
    exists: the first failure is in, so the rest isn't waited for. Stopped
    or past its time, it goes with every process it started. Its output
    goes to `log`, a file, when given."""
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    out = open(log, "wb") if log else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT if log else subprocess.DEVNULL, **kw)
    finally:
        if log:
            out.close()
    end = None if limit is None else time.monotonic() + limit
    while True:
        left = None if end is None else max(0.0, end - time.monotonic())
        step = left if caught is None else (POLL if left is None else min(POLL, left))
        try:
            return proc.wait(timeout=step)
        except subprocess.TimeoutExpired:
            pass
        if caught is not None and read_caught(caught):
            _stop(proc)
            return STOPPED
        if end is not None and time.monotonic() >= end:
            _stop(proc)
            return None


def wide(files) -> bool:
    """A scope for the turn's workers rather than one lane's process (0.25.2)."""
    return len(files) > (LANE_MAX if any("::" in t for t in files) else LANE_MAX_FILES)


def budget() -> float:
    """The seconds a scoped run may take: CCT_CONTROL_BUDGET, else BUDGET."""
    try:
        return float(os.environ.get("CCT_CONTROL_BUDGET", "")) or BUDGET
    except ValueError:
        return BUDGET


def controls_users(source, graph: dict) -> list:
    """What the whole suites leave out for the app's controls module
    (`source`, the file holding BREAKS): for each test file importing it, at
    any depth, the node ids of its tests that use it, or the file itself
    when that can't be told (the module docstring, 0.61.3)."""
    try:
        rel = Path(source).resolve().relative_to(Path(affected.ROOT).resolve()).as_posix()
    except (TypeError, ValueError):                          # none given, or outside the repo
        return []
    out = []
    for test in affected.importers(rel, graph):
        out.extend(_tests_using(test, rel) or [test])
    return out


def _tests_using(test: str, module: str) -> list:
    """The node ids of `test`'s tests that name `module` (a repo path), when
    the file imports it itself and uses it nowhere but inside test functions
    and test classes' methods; [] otherwise (leave the whole file out: none
    found also when it is reached only through another import)."""
    try:
        tree = ast.parse((Path(affected.ROOT) / test).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    dotted = module[:-3].replace("/", ".") if module.endswith(".py") else module.replace("/", ".")
    stem = dotted.rsplit(".", 1)[-1]
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == stem or a.name == dotted or a.name.endswith("." + stem):
                    names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod == stem or mod == dotted or mod.endswith("." + stem):
                names.update(a.asname or a.name for a in node.names)
            elif any(a.name == stem for a in node.names) and (dotted == f"{mod}.{stem}" or dotted.endswith(f"{mod}.{stem}")
                                                              or mod in (".", "")):
                names.update(a.asname or a.name for a in node.names if a.name == stem)
    uses = lambda n: any(isinstance(x, ast.Name) and x.id in names for x in ast.walk(n))   # noqa: E731
    found = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            if uses(node):
                found.append(f"{test}::{node.name}")
            continue
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    if uses(item):
                        found.append(f"{test}::{node.name}::{item.name}")
                elif uses(item):
                    return []
            continue
        if uses(node):
            return []                                        # module level, a helper or a fixture
    return found


def run_control(root: Path, path, old, new, files, workers, limit=None, name="", skip=(), found=None):
    """Break `path` in the tree at `root`, run `files` (node ids or test
    files; all of tests/ when empty) with -x, in their order, restore. True
    when the tests failed; None when they ran past `limit` seconds. The run
    has CCT_NEG_CONTROL set (`name`, else `path`); the test files in `skip`
    are left out (--ignore). `found`, a dict, gets "by": the test that
    caught it (its node id, or its file when it didn't collect). A listed
    test whose file isn't in the tree is left out, and with none left
    nothing runs (False): pytest refuses a missing file outright (its exit
    4), which read as a catch. The record names the main folder's tests,
    day work too, and a nightly worktree or a lane of HEAD may not have them
    (0.30.0, Bevel's a1 trial). pytest's output goes to a file of its own
    outside the tree, removed when nothing holds it: a process the tests
    started can keep it open after pytest ends (Windows: WinError 32), which
    stopped Bevel's trial after 47 catches."""
    if files and files != SLOW_SUITE:
        files = [t for t in files if (root / t.split("::")[0]).is_file()]
        if not files:
            return False
    f = root / path
    raw = f.read_bytes()
    src = raw.decode("utf-8").replace("\r\n", "\n")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CCT_NEG_CONTROL": name or path,
           "PYTHONPATH": os.pathsep.join([str(root), str(root / "tests"), os.environ.get("PYTHONPATH", "")])}
    if workers == 1:                                        # a lane: one CPU, its native pools too
        env.update({v: "1" for v in THREAD_VARS})
    # none: the whole fast suite, pytest.ini's own -m; any scope runs its tests whatever their
    # marker, test files too (a module-level break's importers: 0.25.5, Worm's PARITY)
    args, marker = (files, ["-m", "slow or not slow"]) if files else (["tests"], [])
    listed, log = root / ".control_tests.txt", None
    if found is not None:
        fd, out = tempfile.mkstemp(prefix="cct-control-", suffix=".txt")
        os.close(fd)
        log = Path(out)
    # The first failure, from the select plugin (0.62.1): the catcher whatever
    # the app's -r or addopts, and the run stopped there.
    caught = Path(tempfile.gettempdir()) / f"cct-caught-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}.json"
    env["CCT_CONTROL_CAUGHT"] = str(caught)
    if files == SLOW_SUITE:
        args, marker = ["tests"], ["-m", "slow"]
    elif any("::" in t for t in files):                     # these tests, in this order, fast or slow
        listed.write_text("\n".join(files), encoding="utf-8")
        env["CCT_SELECT"] = str(listed)
        args, marker = list(dict.fromkeys(t.split("::")[0] for t in files)), ["-m", "slow or not slow"]
    try:
        f.write_bytes(src.replace(old, new).encode("utf-8"))
        code = _run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                     "-p", "cct_common.testing.select", "-n", str(workers if workers > 1 else 0), *marker,
                     *(f"--deselect={s}" if "::" in s else f"--ignore={s}" for s in skip), *args],
                    root, env, limit, log=log, caught=caught)
        first = read_caught(caught)
        if code == STOPPED:
            if found is not None:
                found.update(by=first["by"], stopped=True)
        elif found is not None and code not in (None, 0, 5):   # finished on its own: never a killed run's log
            if first:
                found["by"] = first["by"]
            elif log:
                by = CAUGHT_BY.search(log.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n"))
                if by:
                    found["by"] = by.group(1)
    finally:
        f.write_bytes(raw)
        listed.unlink(missing_ok=True)
        try:
            caught.unlink(missing_ok=True)
        except OSError:
            pass
        if log:
            try:
                log.unlink(missing_ok=True)
            except OSError:                                 # still held: left in the temp folder
                pass
    assert f.read_bytes() == raw
    if code == STOPPED:
        return True
    return None if code is None else code not in (0, 5)    # 5: no test ran, so none caught it


# -- 0.62.12: the tests that fail with no break (the owner's "scope credit a") --
# Pulley's nights of 11-15 to 11-17: a test that failed in every lane copy for
# its own reason was the whole suite's first failure whatever the break, so
# four controls read caught (SCOPE) by it, and its saved catch was credited
# again each night (run alone, it failed again). Once per run, the whole
# suites run on an unbroken copy, every test, no -x; each test that fails
# there is left out of every control's runs, and is never a catcher.
UNBROKEN = "(none: the unbroken copy)"      # its runs' CCT_NEG_CONTROL, as a control's runs have one


def unbroken(root: Path, suite: str, workers: int, skip=()) -> dict:
    """The whole `suite` ("fast" or "slow") on the unbroken copy at `root`,
    every test, no -x: {"failed", "seen": node ids (a file: it didn't
    collect), "secs", "code": pytest's exit, None when it couldn't run}."""
    note = Path(tempfile.gettempdir()) / f"cct-unbroken-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    out = {"failed": set(), "seen": set(), "secs": 0.0, "code": None}
    if not (root / "tests").is_dir():
        return out
    env = {k: v for k, v in os.environ.items() if k not in ("CCT_CONTROL_CAUGHT", "CCT_SELECT")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "CCT_NEG_CONTROL": UNBROKEN, "CCT_CONTROL_FAILS": str(note),
                "PYTHONPATH": os.pathsep.join([str(root), str(root / "tests"), os.environ.get("PYTHONPATH", "")])})
    if workers == 1:
        env.update({v: "1" for v in THREAD_VARS})
    t = time.time()
    try:
        out["code"] = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
                            "-p", "cct_common.testing.select", "-n", str(workers if workers > 1 else 0),
                            *(["-m", "slow"] if suite == "slow" else []),
                            *(f"--deselect={s}" if "::" in s else f"--ignore={s}" for s in skip), "tests"],
                           root, env, None)
    finally:
        out["secs"] = time.time() - t
        for part in note.parent.glob(note.name + ".*.txt"):
            try:
                for line in part.read_text(encoding="utf-8").splitlines():
                    kind, _, nodeid = line.partition(" ")
                    if nodeid:
                        out["failed" if kind == "fail" else "seen"].add(nodeid)
                part.unlink()
            except OSError:
                pass
    return out


def _hit(test: str, failed) -> bool:
    """`test` (a node id, or a file) failed: it, or its file (didn't collect)."""
    return test in failed or test.split("::")[0] in failed


def _ran(test: str, seen) -> bool:
    """`test` ran: it, or (a file) any test of it."""
    return test in seen or ("::" not in test and any(i.startswith(test + "::") for i in seen))


class Unbroken:
    """The whole suites' runs on an unbroken copy, each once, on first need."""

    def __init__(self, root: Path, workers: int, skip=()):
        self.root, self.workers, self.skip = root, workers, list(skip)
        self.runs, self._lock = {}, threading.Lock()

    def run(self, suite: str) -> dict:
        with self._lock:
            if suite not in self.runs:
                r = self.runs[suite] = unbroken(self.root, suite, self.workers, self.skip)
                if r["code"] not in (0, 1, 5):
                    say(f"unbroken: the whole {suite} suite didn't run on the unbroken copy (pytest's exit "
                        f"{r['code']}): no test is left out for failing there")
                else:
                    n = len(r["failed"])
                    say(f"unbroken: the whole {suite} suite on an unbroken copy, {r['secs']:.0f} s: "
                        f"{n} test{'s' * (n != 1)} fail{'s' * (n == 1)} with no break"
                        + (" (left out of every control's runs, never a catcher)" if n else ""))
                    for t in sorted(r["failed"]):
                        say(f"fails unbroken: {t}")
            return self.runs[suite]

    def failed(self) -> set:
        with self._lock:
            return set().union(*(r["failed"] for r in self.runs.values()))

    def fails(self, test: str) -> bool:
        """Whether `test` fails with no break: the fast suite's run tells,
        else the slow suite's, run on first need; one neither ran passes."""
        for suite in ("fast", "slow"):
            r = self.run(suite)
            if _hit(test, r["failed"]):
                return True
            if _ran(test, r["seen"]):
                return False
        return False


def run_clean(base, root, path, old, new, files, workers, limit=None, name="", skip=(), found=None):
    """run_control without the tests that fail unbroken (`base`, an Unbroken;
    None: as run_control). A scope left with none of its tests catches
    nothing. A catch by a test found failing only now (a slow test, its suite
    run on first need) is no catch: the run goes again without it."""
    if base is None:
        return run_control(root, path, old, new, files, workers, limit, name=name, skip=skip, found=found)
    found = {} if found is None else found
    while True:
        out = base.failed()
        run = files if not files or files == SLOW_SUITE else [t for t in files if not _hit(t, out)]
        if files and not run:
            return False
        found.clear()
        caught = run_control(root, path, old, new, run, workers, limit, name=name,
                             skip=[*skip, *sorted(out)], found=found)
        by = found.get("by")
        if not caught or not by or not base.fails(by):
            return caught
        if _hit(by, out):                                   # left out, and still the first failure
            found.clear()
            return False


# -- the run -----------------------------------------------------------------
def cli(argv) -> list:
    """The runner's arguments from the command line's: the name filters, then
    --all and --whole-suite. --help prints the usage and raises SystemExit(0),
    an unknown option SystemExit(2), with nothing looked up or run."""
    ap = argparse.ArgumentParser(
        allow_abbrev=False,                                          # --al is no --all
        description="Break one formula or check at a time; the tests meant to catch it must fail; then restore.")
    ap.add_argument("names", nargs="*", help="run every control whose name holds one of these "
                    "(none: those whose check changed since each was last caught)")
    ap.add_argument("--all", action="store_true", help="run every control")
    ap.add_argument("--whole-suite", action="store_true", help="each on the whole suites, not its scope")
    ap.add_argument("--rekey-bare", action="store_true",
                    help="also run, once, every control whose catch was saved bare (before 0.62.1, no catcher)")
    a = ap.parse_args(list(argv))
    return [*a.names, *(["--all"] if a.all else []), *(["--whole-suite"] if a.whole_suite else []),
            *(["--rekey-bare"] if a.rekey_bare else [])]


def state_counts(state: dict, breaks) -> tuple:
    """(bare, stale) of the saved state (0.62.9): bare, the live controls
    whose catch was saved bare (a key, no catcher: before 0.62.1, or the old
    rule's); stale, the entries that name no control now (one removed or
    renamed). Stale entries are listed and left in the file, never run or
    counted as bare (Worm's 3, Bevel's 19)."""
    bare = sorted(n for n in breaks if n in state and not isinstance(state[n], dict))
    stale = sorted(n for n in state if n not in breaks)
    return bare, stale


def main(breaks: dict, argv, source=None) -> int:
    """Run the app's controls (name -> (path, old, new), or (path, old, new,
    {"catcher": test}) for one meant to be caught by a vendored-hash check,
    left_out; 0.62.4); 0 when every one ran
    was caught. `source`: the file holding BREAKS (the caller's, unless given),
    whose importers the whole suites leave out. --help: the usage, 0; an
    unknown option: the usage, 2; nothing run either way (cli)."""
    source = source or sys._getframe(1).f_globals.get("__file__")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        argv = cli(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    workers = test_cpus()
    every, whole, rekey = "--all" in argv, "--whole-suite" in argv, "--rekey-bare" in argv
    only = [a for a in argv if not a.startswith("--")]
    snap, tstate = affected.snapshot(), affected.load_state()
    index, graph, lines = scope_index(tstate, snap), affected.import_graph(snap), affected.line_record(tstate)
    users = controls_users(source, graph)
    if users:
        print(f"the whole suites leave out what uses the controls module: {', '.join(users)}", flush=True)
    if not index:
        print(f"no record of what the tests run ({affected.STATE}): run the suites with the record first; "
              "until then each control's scope is every test file importing what it breaks", flush=True)
    state, lock, path_ = _state(), threading.Lock(), state_path()
    ran, hashes = vendored_ran(tstate), _VendoredHashes()
    vend = lambda catcher: vendored_deps(catcher, ran, hashes)      # noqa: E731
    checks, leave = hash_checks(ran), {}                            # name -> the hash checks it runs without
    todo, stale, meta, synced = [], [], {}, []
    bare, stale_state = state_counts(state, breaks)
    for name, entry in breaks.items():
        path, old, new = entry[:3]                                  # (path, old, new[, {"catcher": test}])
        declared = (entry[3] if len(entry) > 3 else {}).get("catcher")
        entry = (path, old, new)
        # Every control's pattern, named or not (0.56.9): a filtered by-day
        # run skipped the others', and Pulley's night was first to see one stale.
        if (affected.ROOT / path).read_text(encoding="utf-8").count(old) != 1:   # the code moved on: refresh it
            print(f"STALE   {name}: pattern not found once in {path}", flush=True)
            stale.append(name)
            continue
        if only and not any(o in name for o in only):
            continue
        files, how, key = ([], "--whole-suite", path) if whole else scope(path, old, index, graph, lines)
        out = left_out(path, checks, declared)
        if out:                                                     # never its catcher (0.62.4)
            leave[name] = out
            files = [f for f in files if f not in out]
        files = cheapest_first(files, tstate, {t for t in {f.split("::")[0] for f in files}
                                               if path in graph.get(t, ())})
        ck = control_key(entry, files, key, snap)
        saved = state.get(name)
        prior = saved.get("catcher") if isinstance(saved, dict) else None
        hash_caught = prior in out                                  # saved before 0.62.4: run it again
        meta[name] = (entry, key, None if hash_caught else prior)
        if every or only or whole or hash_caught or (rekey and name in bare) \
                or not is_current(saved, entry, key, files, snap, graph, vend):
            todo.append((name, entry, files, how, ck))
            changed = sync_changes(saved, vend)
            if changed:
                synced.append((name, changed))
    mode = "every control" if every else "those named" if only else "those whose check changed"
    if rekey and not every:
        mode += ", and every bare key"
    print(f"{len(todo)} of {len(breaks)} controls to run ({mode}); {workers} CPUs", flush=True)
    chosen = {c[0] for c in todo}
    bare_run = [n for n in bare if n in chosen]
    current = [n for n in bare if n in meta and n not in chosen]      # looked at (filter, not STALE), kept
    print(f"  {len(bare_run)} of them bare-key reruns (saved before 0.62.1, re-saved with their catcher); "
          f"{len(current)} bare key{'s' * (len(current) != 1)} still current", flush=True)
    if rekey:
        print(f"  {len(bare_run)} bare-key controls rerun on --rekey-bare", flush=True)
    if stale_state:                                        # 0.62.9: a removed control's entry
        print(f"  stale state: {len(stale_state)} entr{'ies' if len(stale_state) != 1 else 'y'} name no control "
              "now (left in the file, not counted)", flush=True)
        for n in stale_state:
            print(f"  stale state: {n}", flush=True)
    if synced:                                             # read these before the gate (0.61.2)
        print(f"{len(synced)} of them because a sync changed what their catcher ran "
              "(a MISSED there can be the sync making the app's own code dead):", flush=True)
        for name, changed in synced:
            print(f"  sync: {name}: {', '.join(changed[:4])}{' ...' if len(changed) > 4 else ''}", flush=True)
    for name in [c[0] for c in todo if c[0] in leave]:     # a break in a vendored tree (0.62.4)
        out = leave[name]
        print(f"  hash: {name}: runs without the {len(out)} test{'s' * (len(out) > 1)} checking the "
              f"vendored copy's hash ({', '.join(out[:2])}{' ...' if len(out) > 2 else ''})", flush=True)
    t0 = time.time()
    scope_missed, missed = [], []

    def done(name, ck, ok, how, files, secs, by=None, stopped=False):
        with lock:
            if ok:                                         # its catcher's key; none seen, the old rule's
                entry, key, _ = meta[name]
                v = vend(by) if by else None
                state[name] = ({"key": catcher_key(entry, key, by, snap, graph, v), "catcher": by,
                                "vendored": [list(x) for x in v], **({"stopped": True} if stopped else {})}
                               if by else ck)
            else:
                missed.append(name)
                state.pop(name, None)
            path_.parent.mkdir(parents=True, exist_ok=True)
            path_.write_text(json.dumps(state, indent=0, sort_keys=True), encoding="utf-8")
        unit = "test" if any("::" in t for t in files) else "file"
        what = "the whole suite" if not files else f"{len(files)} {unit}{'s' * (len(files) > 1)}"
        stop = ", stopped at its first failure" if ok and stopped else ""
        say(f"{'caught ' if ok else 'MISSED '} {name}  ({what}, {how}{stop}, {secs:.0f} s)")

    small = [c for c in todo if c[2] and not wide(c[2])]            # one lane's process each
    broad = [c for c in todo if c[2] and wide(c[2])]                # the turn's workers each, one at a time
    big = [c for c in todo if not c[2]]
    later, limit = [], budget()                                     # caught by no test in its scope, in time

    def by_catcher(root, name, path, old, new, files, how):
        """Its last catcher alone, first (0.30.0): (the test, how) when it
        still catches, else None. A catcher outside the scope reads SCOPE."""
        prior = meta[name][2]
        if not prior or whole:
            return None
        if base is not None and base.fails(prior):                   # 0.62.12: never credited again
            say(f"dropped: {name}: its saved catcher, {prior}, fails unbroken; its scope runs instead")
            return None
        found = {}
        if not run_clean(base, root, path, old, new, [prior], 1, limit, name=name, found=found):
            return None
        if files and prior.split("::")[0] not in {t.split("::")[0] for t in files}:
            with lock:
                scope_missed.append(f"{name} ({how}: its last catcher, {prior}, is outside it)")
        return found.get("by") or prior, f"its last catcher; {how}", found.get("stopped", False)
    # The lanes' folder goes after the result is printed, and its failing to
    # go is a warning (_remove), never the run's exit code. One left keeps
    # its mark for a later run's sweep (0.61.7).
    sweep_lanes()
    tmp, roots = tempfile.mkdtemp(prefix=f"{affected.APP}-neg-"), []
    mark = json.dumps({"started": time.time(), "pid": os.getpid()})
    (Path(tmp) / MARK).write_text(mark, encoding="utf-8")

    def cleanup():
        for root in roots:
            _remove(root)
        _remove(tmp)
        try:
            if Path(tmp).exists():
                (Path(tmp) / MARK).write_text(mark, encoding="utf-8")
        except OSError:
            pass
    base = None
    try:
        lanes = min(workers, len(small))
        roots = [Path(tmp) / f"lane{i}" for i in range(max(1, lanes) if todo else 0)]
        for root in roots:
            copy_tree(root)
        if todo:                                                      # 0.62.12: what fails with no break
            roots.append(Path(tmp) / "unbroken")
            copy_tree(roots[-1])
            base = Unbroken(roots[-1], workers, users)
            base.run("fast")
        q = queue.Queue()
        for c in small:
            q.put(c)

        def lane(i):
            while True:
                try:
                    c = q.get_nowait()
                except queue.Empty:
                    return
                name, (path, old, new), files, how, ck = c
                t = time.time()
                first = by_catcher(roots[i], name, path, old, new, files, how)
                if first:
                    done(name, ck, True, first[1], files, time.time() - t, first[0], first[2])
                    continue
                found = {}
                caught = run_clean(base, roots[i], path, old, new, files, 1, limit, name=name,
                                   skip=leave.get(name, ()), found=found)
                if caught:
                    done(name, ck, True, how, files, time.time() - t, found.get("by"), found.get("stopped", False))
                else:
                    with lock:
                        later.append(c if caught is not None else (*c[:3], f"{how}; past its {limit:.0f} s", ck))
        threads = [threading.Thread(target=lane, args=(i,)) for i in range(lanes)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        def whole_suites(name, path, old, new):
            """The whole fast suite, then the whole slow suite, without the
            controls' own check: the one that caught it, or None; the test that did; and
            whether the run stopped at that first failure."""
            for suite, files in (("fast", []), ("slow", SLOW_SUITE)):
                found = {}
                if base is not None:
                    base.run(suite)                                    # all it leaves out, known first
                if run_clean(base, roots[0], path, old, new, files, workers, name=name,
                             skip=[*users, *leave.get(name, ())], found=found):
                    return suite, found.get("by"), found.get("stopped", False)
            return None, None, False

        for c in broad:                                               # a wide scope: on the turn's workers
            name, (path, old, new), files, how, ck = c
            t = time.time()
            first = by_catcher(roots[0], name, path, old, new, files, how)
            if first:
                done(name, ck, True, first[1], files, time.time() - t, first[0], first[2])
                continue
            found = {}
            caught = run_clean(base, roots[0], path, old, new, files, workers, limit, name=name,
                               skip=leave.get(name, ()), found=found)
            if caught:
                done(name, ck, True, how + f", on {workers} workers", files, time.time() - t, found.get("by"),
                     found.get("stopped", False))
            else:
                later.append(c if caught is not None else (*c[:3], f"{how}; past its {limit:.0f} s", ck))
        for name, (path, old, new), files, how, ck in big:            # the whole suites are its scope
            t = time.time()
            first = by_catcher(roots[0], name, path, old, new, files, how)
            if first:
                done(name, ck, True, first[1], files, time.time() - t, first[0], first[2])
                continue
            suite, by, stopped = whole_suites(name, path, old, new)
            if suite and how == NO_SCOPE:                             # stated: no test of its own caught it
                scope_missed.append(f"{name} ({how}: {path}; the whole {suite} suite caught it)")
            done(name, ck, suite is not None, how + (", its slow suite" if suite == "slow" else ""), files,
                 time.time() - t, by, stopped)
        for name, (path, old, new), files, how, ck in later:         # the whole suites, to tell a scope's gap
            t = time.time()
            suite, by, stopped = whole_suites(name, path, old, new)
            if suite:
                shown = ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
                scope_missed.append(f"{name} ({how}: {shown}; the whole {suite} suite caught it)")
            done(name, ck, suite is not None, f"its scope didn't catch it ({how})", [], time.time() - t, by, stopped)
    except BaseException:
        cleanup()
        raise
    print(f"\n{len(todo)} of {len(breaks)} controls run ({mode}) in {(time.time() - t0) / 60:.1f} min; "
          "the tree untouched (each lane broke its own copy)")
    for s in scope_missed:
        print(f"SCOPE   {s}: caught by the whole suite only; its tests should catch it")
    unbroken_fails = sorted(base.failed()) if base is not None else []
    if unbroken_fails:
        print(f"fails unbroken: {len(unbroken_fails)} test{'s' * (len(unbroken_fails) != 1)} left out of every "
              f"control's runs ({', '.join(unbroken_fails[:3])}{' ...' if len(unbroken_fails) > 3 else ''}): "
              "they fail with no break; fix them, or a control only they would catch reads MISSED", flush=True)
    bad = stale + missed
    print("missed:", bad or "none", flush=True)
    cleanup()
    return 1 if bad else 0
