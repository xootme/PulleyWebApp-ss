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
  when it isn't Python), or a test meant to catch it. A filter runs every
  control whose name holds it; --all runs them all.
- **Which tests.** Each control's scope is the *tests* that ran the function
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
- **Not by the controls' own check.** An app's test that checks its
  controls (each one's text once in its file: Bevel's
  test_every_control_has_a_scope) fails in every lane, as the break takes
  that text out. Every run has CCT_NEG_CONTROL set to the control's name,
  and such a test skips when it is set; the whole suites leave out the test
  files that import the app's controls module, too, so an app that forgot
  the skip still can't catch a control with it (0.25.3: Worm's four SCOPE
  catches were that test, the side supports' own tests none of them).
- **In lanes.** Each lane breaks its own copy of the tree (git's files and
  .git, in a temporary folder), so CCT_TEST_CPUS controls run at once, each
  on one process; a control on the whole suite runs after them, one at a
  time, on all the turn's workers."""
import ast
import hashlib
import json
import os
import queue
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


def scope(path: str, old: str, index: dict, graph: dict, lines: dict | None = None):
    """(tests, how they were found, the snapshot key of what's broken): node
    ids, or test files when no test is on record as running it. An empty list
    is the whole fast suite. In a big function with a line record, the tests
    that ran the lines the break changes (broken_lines; 0.25.2)."""
    src = (affected.ROOT / path).read_text(encoding="utf-8")
    if not path.endswith(".py"):
        return affected.tests_naming([path]), "they name it", path
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


def _remove(folder):
    """A lane gone, with its worktree's entry in the repo (that one only)."""
    subprocess.run(["git", "worktree", "remove", "--force", str(folder)], cwd=affected.ROOT, capture_output=True)

    def unlock(fn, p, _):                                  # git's objects are read-only
        os.chmod(p, 0o700)
        fn(p)
    if Path(folder).exists():
        shutil.rmtree(folder, onexc=unlock)


def _run(cmd, cwd, env, limit):
    """The command's exit code, or None when it ran past `limit` seconds (it,
    and every process it started, stopped)."""
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)
    try:
        return proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
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
    """The test files that import the app's controls module (`source`, the
    file holding BREAKS), at any depth: left out of the whole suites."""
    try:
        rel = Path(source).resolve().relative_to(Path(affected.ROOT).resolve()).as_posix()
    except (TypeError, ValueError):                          # none given, or outside the repo
        return []
    return affected.importers(rel, graph)


def run_control(root: Path, path, old, new, files, workers, limit=None, name="", skip=()):
    """Break `path` in the tree at `root`, run `files` (node ids or test
    files; all of tests/ when empty) with -x, in their order, restore. True
    when the tests failed; None when they ran past `limit` seconds. The run
    has CCT_NEG_CONTROL set (`name`, else `path`); the test files in `skip`
    are left out (--ignore)."""
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
    listed = root / ".control_tests.txt"
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
                     *(f"--ignore={s}" for s in skip), *args],
                    root, env, limit)
    finally:
        f.write_bytes(raw)
        listed.unlink(missing_ok=True)
    assert f.read_bytes() == raw
    return None if code is None else code not in (0, 5)    # 5: no test ran, so none caught it


# -- the run -----------------------------------------------------------------
def main(breaks: dict, argv, source=None) -> int:
    """Run the app's controls (name -> (path, old, new)); 0 when every one ran
    was caught. `source`: the file holding BREAKS (the caller's, unless given),
    whose importers the whole suites leave out."""
    source = source or sys._getframe(1).f_globals.get("__file__")
    sys.stdout.reconfigure(encoding="utf-8")
    workers = test_cpus()
    every, whole = "--all" in argv, "--whole-suite" in argv
    only = [a for a in argv if not a.startswith("--")]
    snap, tstate = affected.snapshot(), affected.load_state()
    index, graph, lines = scope_index(tstate, snap), affected.import_graph(snap), affected.line_record(tstate)
    users = controls_users(source, graph)
    if not index:
        print(f"no record of what the tests run ({affected.STATE}): run the suites with the record first; "
              "until then each control's scope is every test file importing what it breaks", flush=True)
    state, lock, path_ = _state(), threading.Lock(), state_path()
    todo, stale = [], []
    for name, entry in breaks.items():
        path, old, new = entry
        if only and not any(o in name for o in only):
            continue
        if (affected.ROOT / path).read_text(encoding="utf-8").count(old) != 1:   # the code moved on: refresh it
            print(f"STALE   {name}: pattern not found once in {path}", flush=True)
            stale.append(name)
            continue
        files, how, key = ([], "--whole-suite", path) if whole else scope(path, old, index, graph, lines)
        files = cheapest_first(files, tstate, {t for t in {f.split("::")[0] for f in files}
                                               if path in graph.get(t, ())})
        ck = control_key(entry, files, key, snap)
        if every or only or whole or state.get(name) != ck:
            todo.append((name, entry, files, how, ck))
    mode = "every control" if every else "those named" if only else "those whose check changed"
    print(f"{len(todo)} of {len(breaks)} controls to run ({mode}); {workers} CPUs", flush=True)
    t0 = time.time()
    scope_missed, missed = [], []

    def done(name, ck, ok, how, files, secs):
        with lock:
            if ok:
                state[name] = ck
            else:
                missed.append(name)
                state.pop(name, None)
            path_.parent.mkdir(parents=True, exist_ok=True)
            path_.write_text(json.dumps(state, indent=0, sort_keys=True), encoding="utf-8")
        unit = "test" if any("::" in t for t in files) else "file"
        what = "the whole suite" if not files else f"{len(files)} {unit}{'s' * (len(files) > 1)}"
        say(f"{'caught ' if ok else 'MISSED '} {name}  ({what}, {how}, {secs:.0f} s)")

    small = [c for c in todo if c[2] and not wide(c[2])]            # one lane's process each
    broad = [c for c in todo if c[2] and wide(c[2])]                # the turn's workers each, one at a time
    big = [c for c in todo if not c[2]]
    later, limit = [], budget()                                     # caught by no test in its scope, in time
    with tempfile.TemporaryDirectory(prefix=f"{affected.APP}-neg-") as tmp:
        lanes = min(workers, len(small))
        roots = [Path(tmp) / f"lane{i}" for i in range(max(1, lanes) if todo else 0)]
        for root in roots:
            copy_tree(root)
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
                caught = run_control(roots[i], path, old, new, files, 1, limit, name=name)
                if caught:
                    done(name, ck, True, how, files, time.time() - t)
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
            controls' own check: the one that caught it, or None."""
            for suite, files in (("fast", []), ("slow", SLOW_SUITE)):
                if run_control(roots[0], path, old, new, files, workers, name=name, skip=users):
                    return suite
            return None

        for c in broad:                                               # a wide scope: on the turn's workers
            name, (path, old, new), files, how, ck = c
            t = time.time()
            caught = run_control(roots[0], path, old, new, files, workers, limit, name=name)
            if caught:
                done(name, ck, True, how + f", on {workers} workers", files, time.time() - t)
            else:
                later.append(c if caught is not None else (*c[:3], f"{how}; past its {limit:.0f} s", ck))
        for name, (path, old, new), files, how, ck in big:            # the whole suites are its scope
            t = time.time()
            suite = whole_suites(name, path, old, new)
            done(name, ck, suite is not None, how + (", its slow suite" if suite == "slow" else ""), files,
                 time.time() - t)
        for name, (path, old, new), files, how, ck in later:         # the whole suites, to tell a scope's gap
            t = time.time()
            suite = whole_suites(name, path, old, new)
            if suite:
                shown = ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
                scope_missed.append(f"{name} ({how}: {shown}; the whole {suite} suite caught it)")
            done(name, ck, suite is not None, f"its scope didn't catch it ({how})", [], time.time() - t)
        for root in roots:
            _remove(root)
    print(f"\n{len(todo)} of {len(breaks)} controls run ({mode}) in {(time.time() - t0) / 60:.1f} min; "
          "the tree untouched (each lane broke its own copy)")
    for s in scope_missed:
        print(f"SCOPE   {s}: caught by the whole suite only; its tests should catch it")
    bad = stale + missed
    print("missed:", bad or "none")
    return 1 if bad else 0
