"""Negative controls: break one formula or check at a time; the tests meant
to catch it must fail; then restore (cct_common 0.25.0; Bevel Gear Designer's
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
  full fast suite), cheapest first (their seconds in the record), run with
  -x (cct_common.testing.select keeps that order): the first that catches it
  ends the run. By file instead, Bevel's controls ran 12-80 min each on
  18-24 files, mostly tests that pass before the one that fails; by test,
  the same six took 61 s against 23.6 min (2026-10-21). At the module level,
  the files that import the file; a file that isn't Python, those that name
  it. A control its scope doesn't catch runs again on the whole fast suite,
  and is reported SCOPE (the whole suite caught it, the scope didn't: a test
  served from a cache another test filled) or MISSED. --whole-suite runs
  every control on the whole fast suite.
- **In lanes.** Each lane breaks its own copy of the tree (git's files and
  .git, in a temporary folder), so CCT_TEST_CPUS controls run at once, each
  on one process; a control on the whole suite runs after them, one at a
  time, on all the turn's workers."""
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
    """function key -> the fast tests (node ids) that ran it (the record of the
    last suite runs)."""
    index = {}
    for node, e in affected.test_record(state, "fast").items():
        if node.startswith("tests/"):
            for path, q in e["ran"]:
                index.setdefault(affected.fn_key(path, q, (snap,)), set()).add(node)
    return index


def scope(path: str, old: str, index: dict, graph: dict):
    """(tests, how they were found, the snapshot key of what's broken): node
    ids, or test files when no test is on record as running it. An empty list
    is the whole fast suite."""
    src = (affected.ROOT / path).read_text(encoding="utf-8")
    if not path.endswith(".py"):
        return affected.tests_naming([path]), "they name it", path
    q = affected.enclosing(path, src.index(old))
    key = path + (affected.FN + q if q else affected.TOP)
    if q and index.get(key):
        return sorted(index[key]), f"they ran {q}", key
    if path.startswith(("tests/", "tools/")):                  # the suite's own helpers and hooks
        stem = Path(path).stem
        named = [t for t in affected.all_tests()
                 if t != path and stem in (affected.ROOT / t).read_text(encoding="utf-8")]
        if named:
            return named, f"they name {stem}", key
    return affected.importers(path, graph), "they import it", key


def cheapest_first(tests, tstate: dict) -> list:
    """A scope's tests (or test files) by their recorded seconds, so -x stops
    at the first that catches the control; one never recorded goes last."""
    def secs(t):
        e = tstate.get("tests", {}).get(f"fast:{t}") if "::" in t else None
        return e["s"] if e else tstate.get("seconds", {}).get(f"fast:{t}", 1e9)
    return sorted(tests, key=lambda t: (secs(t), t))


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
    """git's files (ignored ones left out) and .git, byte for byte, with
    bytecode checked by the source's hash, not its size and time: a break the
    same length as its line, restored within the second, would otherwise load
    a stale .pyc (it did: Bevel, 2026-10-19). The runs write no bytecode."""
    root = affected.ROOT
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True,
                         check=True)
    for f in sorted(set(out.stdout.decode("utf-8").split("\0")) - {""}):
        if (root / f).is_file():
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, dst / f)
    shutil.copytree(root / ".git", dst / ".git")
    subprocess.run([sys.executable, "-m", "compileall", "-q", "--invalidation-mode", "checked-hash",
                    "-x", r"[\\/]\.git[\\/]", str(dst)], check=True, capture_output=True)


def _remove(folder):
    def unlock(fn, p, _):                                  # git's objects are read-only
        os.chmod(p, 0o700)
        fn(p)
    shutil.rmtree(folder, onexc=unlock)


def run_control(root: Path, path, old, new, files, workers):
    """Break `path` in the tree at `root`, run `files` (node ids or test
    files; all of tests/ when empty) with -x, in their order, restore. True
    when the tests failed."""
    f = root / path
    raw = f.read_bytes()
    src = raw.decode("utf-8").replace("\r\n", "\n")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join([str(root), str(root / "tests"), os.environ.get("PYTHONPATH", "")])}
    if workers == 1:                                        # a lane: one CPU, its native pools too
        env.update({v: "1" for v in THREAD_VARS})
    args = files or ["tests"]
    listed = root / ".control_tests.txt"
    if any("::" in t for t in files):                       # these tests, in this order
        listed.write_text("\n".join(files), encoding="utf-8")
        env["CCT_SELECT"] = str(listed)
        args = list(dict.fromkeys(t.split("::")[0] for t in files))
    try:
        f.write_bytes(src.replace(old, new).encode("utf-8"))
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                            "-p", "cct_common.testing.select", "-n", str(workers if workers > 1 else 0), *args],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, env=env)
    finally:
        f.write_bytes(raw)
        listed.unlink(missing_ok=True)
    assert f.read_bytes() == raw
    return r.returncode != 0


# -- the run -----------------------------------------------------------------
def main(breaks: dict, argv) -> int:
    """Run the app's controls (name -> (path, old, new)); 0 when every one ran was caught."""
    sys.stdout.reconfigure(encoding="utf-8")
    workers = test_cpus()
    every, whole = "--all" in argv, "--whole-suite" in argv
    only = [a for a in argv if not a.startswith("--")]
    snap, tstate = affected.snapshot(), affected.load_state()
    index, graph = scope_index(tstate, snap), affected.import_graph(snap)
    if not index:
        print(f"no record of what the tests run ({affected.STATE}): run the fast suite with the record first; "
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
        files, how, key = ([], "the whole suite", path) if whole else scope(path, old, index, graph)
        files = cheapest_first(files, tstate)
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

    small = [c for c in todo if c[2]]
    big = [c for c in todo if not c[2]]
    later = []                                                       # caught by no test in its scope
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
                if run_control(roots[i], path, old, new, files, 1):
                    done(name, ck, True, how, files, time.time() - t)
                else:
                    with lock:
                        later.append(c)
        threads = [threading.Thread(target=lane, args=(i,)) for i in range(lanes)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        for c in big:
            name, (path, old, new), files, how, ck = c
            t = time.time()
            if run_control(roots[0], path, old, new, files, workers):
                done(name, ck, True, how, files, time.time() - t)
            elif files:
                later.append(c)
            else:
                done(name, ck, False, how, files, time.time() - t)
        for name, (path, old, new), files, how, ck in later:         # the whole suite, to tell a scope's gap
            t = time.time()
            ok = run_control(roots[0], path, old, new, [], workers)
            if ok:
                shown = ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
                scope_missed.append(f"{name} ({how}: {shown})")
            done(name, ck, ok, f"its scope didn't catch it ({how})", [], time.time() - t)
        for root in roots:
            _remove(root)
    print(f"\n{len(todo)} of {len(breaks)} controls run ({mode}) in {(time.time() - t0) / 60:.1f} min; "
          "the tree untouched (each lane broke its own copy)")
    for s in scope_missed:
        print(f"SCOPE   {s}: caught by the whole suite only; its tests should catch it")
    bad = stale + missed
    print("missed:", bad or "none")
    return 1 if bad else 0
