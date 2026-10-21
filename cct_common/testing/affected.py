r"""What a change reaches: the test files and browser harnesses to run while
working, and the record they're picked by (cct_common 0.25.0; from Bevel Gear
Designer's tools/affected.py, its ADR-031, and Worm Gear Designer's before it).
The whole set still runs once before a release (the 11 PM nightly run).

An app configures it once, in its own tools/affected.py:

    from cct_common.testing import affected
    affected.configure(root=ROOT, app="BevelGearWebApp",
                       test_rules=[("templates/*", r'get\("/"')],
                       harness_rules=[("static/app.js", affected.ALL), ...])

Everything is measured from when each test file (fast and slow apart) and
harness last passed, not from the last commit, so what already passed on
the current files doesn't run again. A test file runs again when, since:

- **it changed**, or it never passed here;
- **a function it ran changed** (cct_common.testing.record's record; every
  function and method is hashed apart, `fn_hashes`; a nested function is its
  outer one's);
- **the module level of a file it imports changed**, at any depth
  (`top_hash`: constants, defaults, decorators, class attributes; not
  function bodies, comments or docstrings): it runs at import, outside any
  test, so the record can't see it;
- **a non-Python file it names changed** (help pages, pictures, JS, CSS,
  templates, …), or quotes the file's folders and a glob that matches it, or
  that the app's test_rules give it; Markdown is left out;
- **one of `everything` changed** (pytest.ini, the requirements: everything),
  or a file in a `fold` glob (a vendored tree, hashed as one key; 0.25.1).

Harnesses: by the app's harness_rules (glob -> harnesses, or ALL). Files are
hashed with their line endings made LF, so a checkout with git's CRLF (the
nightly worktree) and the main folder agree on what's unchanged.

The record (test_state.json) holds the files' hashes when each passed, what
each test file and each test ran, and how long each took. It lives outside
the repo, so every checkout of it shares one: CCT_TEST_RECORD_DIR, else
%LOCALAPPDATA%/cct/test-records/<app>."""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ALL = "*"
TOP = "#top"                    # snapshot key: a Python file's module level
FN = "::"                       # snapshot key: path::qualname, one function or method
KINDS = ("fast", "slow")        # pytest.ini's -m "not slow", and -m slow: recorded apart
LINE_MIN = 50                   # a function more tests than this run has its lines recorded (0.25.2)

# The app's, set by configure()
ROOT = Path.cwd()
APP = ROOT.name
EVERYTHING = ("pytest.ini", "requirements.txt", "requirements-dev.txt")
TEST_RULES: list = []           # (glob, regex): a non-Python file the app reads -> the tests whose source matches
HARNESS_RULES: list = []        # (glob, harnesses or ALL): a changed file -> the harnesses it reaches
HARNESS_GLOB = "tests/browser/*_ui.js"
HARNESS_DIR = "tests/browser/"
NAMING_SKIP: set = set()        # test files that name every file (the tool's own tests)
FOLD: list = []                 # globs whose files hash as one key each (vendored trees): a change there reaches all
FOLDED = "fold:"                # snapshot key: a folded glob's files, together


def record_dir() -> Path:
    """Where the records live: outside the repo, shared by every checkout of it."""
    home = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "cct" / "test-records" / APP
    return Path(os.environ.get("CCT_TEST_RECORD_DIR") or home)


STATE = record_dir() / "test_state.json"


def configure(root, app: str, test_rules=(), harness_rules=(), everything=None,
              harness_glob="tests/browser/*_ui.js", naming_skip=(), fold=()):
    """The app's repo and rules. Call it before anything else here. `fold`:
    globs (fnmatch: `*` crosses folders) for vendored or third-party trees,
    whose files hash as one key per glob instead of thousands (Pulley's
    2,270 .deps/ files: 17 s a snapshot, 3.3 s folded); a change there
    reaches every test and harness, as `everything` does (0.25.1)."""
    global ROOT, APP, TEST_RULES, HARNESS_RULES, EVERYTHING, HARNESS_GLOB, HARNESS_DIR, NAMING_SKIP, STATE, FOLD
    ROOT, APP = Path(root).resolve(), app
    TEST_RULES, HARNESS_RULES = list(test_rules), list(harness_rules)
    if everything is not None:
        EVERYTHING = tuple(everything)
    HARNESS_GLOB, HARNESS_DIR = harness_glob, harness_glob.rpartition("/")[0] + "/"
    NAMING_SKIP = set(naming_skip)
    FOLD = list(fold)
    STATE = record_dir() / "test_state.json"


@dataclass
class Pick:
    snapshot: dict = field(default_factory=dict)      # key -> hash, now
    tests: dict = field(default_factory=lambda: {k: [] for k in KINDS})   # kind -> test files
    harnesses: list = field(default_factory=list)
    why: dict = field(default_factory=dict)           # "fast:tests/x.py" / "view3d_ui" -> reason


# -- the files as they are now -----------------------------------------------
def snapshot() -> dict:
    """Every file git would see (ignored ones left out), hashed; each Python
    file's module level as `<path>#top`, and its every function and method
    as `<path>::<qualname>`; the files of a `fold` glob together, as
    `fold:<glob>`."""
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True)
    snap, folds = {}, {}
    for f in sorted(set(out.stdout.decode("utf-8").split("\0")) - {""}):
        p = ROOT / f
        if p.is_file():
            data = p.read_bytes().replace(b"\r\n", b"\n")
            glob = next((g for g in FOLD if fnmatch.fnmatchcase(f, g)), None)
            if glob is not None:
                folds.setdefault(glob, hashlib.sha1()).update(f.encode("utf-8") + b"\0" + hashlib.sha1(data).digest())
                continue
            snap[f] = hashlib.sha1(data).hexdigest()
            if f.endswith(".py"):
                snap[f + TOP] = top_hash(data)
                snap.update({f + FN + q: h for q, h in fn_hashes(data).items()})
    snap.update({FOLDED + g: h.hexdigest() for g, h in folds.items()})
    return snap


class _Bodiless(ast.NodeTransformer):
    """A module with every function's body and every docstring taken out:
    what runs when it's imported."""
    def _doc(self, body):
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            return body[1:]
        return body

    def visit_FunctionDef(self, node):
        node.body = [ast.Pass()]
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        node.body = self._doc(node.body) or [ast.Pass()]
        return self.generic_visit(node)

    def visit_Module(self, node):
        node.body = self._doc(node.body)
        return self.generic_visit(node)


def top_hash(data: bytes) -> str:
    """A hash of what a Python file runs when imported (its constants, its
    defs' defaults and decorators, its classes' attributes), not its function
    bodies, comments or docstrings."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return hashlib.sha1(data).hexdigest()
    return hashlib.sha1(ast.dump(_Bodiless().visit(tree)).encode("utf-8")).hexdigest()


def functions(tree) -> list:
    """(qualname, node) for every function and method outside a function, as
    code objects name them (`f`, `C.m`, `C.D.m`), through if / try / with
    blocks; a function's nested ones are part of it."""
    out = []

    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + node.name, node))
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")
            else:
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(node, attr, None)
                    if isinstance(inner, list):
                        walk(inner, prefix)
    walk(tree.body, "")
    return out


def fn_hashes(data: bytes) -> dict:
    """qualname -> a hash of its code (not its comments, nor where it sits)."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return {}
    out = {}
    for q, node in functions(tree):           # a property's getter and setter share a name
        out[q] = hashlib.sha1((out.get(q, "") + ast.dump(node)).encode("utf-8")).hexdigest()
    return out


def fn_key(path: str, qualname: str, snaps) -> str:
    """The snapshot key a code object's run is charged to: its outermost
    function's, or the module level's (a lambda, a generator expression or a
    class body outside any function, `<module>` itself)."""
    q = qualname.split(".<locals>")[0]
    key = path + FN + q
    return key if any(key in s for s in snaps) else path + TOP


def enclosing(path: str, offset: int):
    """The qualname of the function or method holding character `offset` of
    a file, None at the module level."""
    src = (ROOT / path).read_text(encoding="utf-8")
    line = src.count("\n", 0, offset) + 1
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for q, node in functions(tree):           # its decorators and signature run at import
        if node.body[0].lineno <= line <= node.end_lineno:
            return q
    return None


# -- imports -----------------------------------------------------------------
def _module_names(snap) -> dict:
    out = {}
    for f in snap:
        if f.endswith(".py"):
            name = f[:-3].replace("/", ".")
            out[name[:-9] if name.endswith(".__init__") else name] = f
    return out


def import_graph(snap) -> dict:
    """repo path -> the repo paths it imports. conftest puts the repo root
    and tests/ on sys.path; the tests put tools/ there."""
    names = _module_names(snap)
    graph = {}
    for name, f in names.items():
        try:
            tree = ast.parse((ROOT / f).read_bytes())
        except (OSError, SyntaxError, ValueError):
            graph[f] = set()
            continue
        package = name if f.endswith("__init__.py") else name.rpartition(".")[0]
        wanted = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                wanted += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = package.split(".")[:len(package.split(".")) - node.level + 1] if package else []
                    base = ".".join([*up, base] if base else up)
                wanted += [base] + [f"{base}.{a.name}" if base else a.name for a in node.names]
        deps = set()
        for w in wanted:
            for cand in (w, f"tests.{w}", f"tools.{w}"):
                parts = cand.split(".")
                for i in range(len(parts), 0, -1):                 # a.b.c imports a and a.b too
                    if ".".join(parts[:i]) in names:
                        deps.add(names[".".join(parts[:i])])
        graph[f] = deps - {f}
    return graph


def reached(test: str, graph: dict) -> set:
    """Every repo file a test file imports, at any depth (conftest.py too)."""
    seen, todo = set(), [test, "tests/conftest.py"]
    while todo:
        f = todo.pop()
        if f in seen or f not in graph:
            continue
        seen.add(f)
        todo += graph[f]
    return seen


def importers(path: str, graph: dict) -> list:
    """The test files that import `path`, at any depth."""
    return [t for t in all_tests() if path in reached(t, graph)]


# -- the record --------------------------------------------------------------
def _sid(snap: dict) -> str:
    return hashlib.sha1(json.dumps(snap, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    for k in ("snaps", "passed", "ran", "seconds", "tests", "lines"):
        state.setdefault(k, {})
    state.setdefault("codes", [])
    return state


def save_state(state: dict):
    used = set(state["passed"].values())
    state["snaps"] = {k: v for k, v in state["snaps"].items() if k in used}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE)


def _worker_files(folder: Path):
    """Each record file as (files, tests, lines); the codes resolved."""
    for f in sorted(Path(folder).glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if "files" not in data:                                    # a record of files alone
            yield {t: {**e, "ran": [tuple(r) for r in e["ran"]]} for t, e in data.items()}, {}, {}
            continue
        codes = [tuple(c) for c in data["codes"]]
        yield ({t: {**e, "ran": [codes[i] for i in e["ran"]]} for t, e in data["files"].items()},
               {t: {**e, "ran": [codes[i] for i in e["ran"]]} for t, e in data["tests"].items()},
               {t: {codes[int(c)]: set(ln) for c, ln in by.items()} for t, by in data.get("lines", {}).items()})


def merge_map(folder: Path) -> dict:
    """tests/run_map.py's per-process files as one: test file -> {"ok", "ran"}.
    Every xdist worker collects every file, each runs some of its tests."""
    out = {}
    for files, _, _ in _worker_files(folder):
        for test, e in files.items():
            m = out.setdefault(test, {"selected": 0, "finished": 0, "ok": True, "seconds": 0.0, "ran": set()})
            m["selected"] = max(m["selected"], e["selected"])
            m["finished"] += e["finished"]
            m["seconds"] += e.get("seconds", 0.0)
            m["ok"] &= e["ok"]
            m["ran"].update(e["ran"])
    for m in out.values():
        m["ok"] &= m["finished"] >= m["selected"]
    return out


def merge_tests(folder: Path) -> dict:
    """node id -> {"ok", "seconds", "ran": set}: each test's own record (one worker ran it)."""
    out = {}
    for _, tests, _ in _worker_files(folder):
        for t, e in tests.items():
            out[t] = {"ok": e["ok"], "seconds": e["seconds"], "ran": set(e["ran"])}
    return out


def merge_lines(folder: Path) -> dict:
    """node id -> {(path, qualname): {line}}: the lines each test ran in the big functions."""
    out = {}
    for _, _, lines in _worker_files(folder):
        for t, by in lines.items():
            for c, ln in by.items():
                out.setdefault(t, {}).setdefault(c, set()).update(ln)
    return out


def big_functions(state: dict, line_min: int = LINE_MIN) -> list:
    """(path, qualname) of each function more than `line_min` tests ran (the
    last record, fast and slow): those a change scopes to by line (0.25.2)."""
    count = {}
    for kind in KINDS:
        for e in test_record(state, kind).values():
            for path, q in {(p, q.split(".<locals>")[0]) for p, q in e["ran"]}:
                count[(path, q)] = count.get((path, q), 0) + 1
    return sorted(k for k, n in count.items() if n > line_min and k[1] != "<module>")


def record_env(folder: Path, state: dict | None = None, line_min: int = LINE_MIN) -> dict:
    """The environment a suite run records with (cct_common.testing.record):
    CCT_TEST_MAP, and CCT_TEST_LINES naming the big functions whose lines each
    test runs are noted too, so a control in one runs only the tests that
    reached its line (0.25.2)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    big = big_functions(load_state() if state is None else state, line_min)
    listed = folder / "big_functions.txt"
    listed.write_text("".join(f"{path}\t{q}\n" for path, q in big), encoding="utf-8")
    return {"CCT_TEST_MAP": str(folder), "CCT_TEST_LINES": str(listed)}


def line_record(state: dict) -> dict:
    """(path, qualname) -> {node id: {line}}: the lines each test ran in the big
    functions (nested functions under their outer one's name), from tests whose
    file last passed."""
    codes, out = state["codes"], {}
    for key, by in state["lines"].items():
        kind, _, node = key.partition(":")
        if f"{kind}:{node.split('::')[0]}" not in state["passed"]:
            continue
        for c, ln in by.items():
            path, q = codes[int(c)]
            out.setdefault((path, q.split(".<locals>")[0]), {}).setdefault(node, set()).update(ln)
    return out


def test_record(state: dict, kind: str) -> dict:
    """node id -> {"seconds", "ran": [(path, qualname)]}, of `kind`'s tests whose file last passed."""
    codes = state["codes"]
    out = {}
    for key, e in state["tests"].items():
        k, _, node = key.partition(":")
        if k == kind and f"{kind}:{node.split('::')[0]}" in state["passed"]:
            out[node] = {"seconds": e["s"], "ran": [tuple(codes[i]) for i in e["r"]]}
    return out


def record_run(kind: str, folder: Path, snap: dict, state: dict | None = None) -> dict:
    """A suite run's record: each test file whose selected tests all passed
    passed on `snap` (a file with none selected, too); one that failed hasn't."""
    state = load_state() if state is None else state
    sid = _sid(snap)
    state["snaps"][sid] = snap
    files, tests, lines = merge_map(folder), merge_tests(folder), merge_lines(folder)
    index = {tuple(c): i for i, c in enumerate(state["codes"])}

    def code(c):
        if c not in index:
            index[c] = len(state["codes"])
            state["codes"].append(list(c))
        return index[c]
    for test, m in files.items():
        key = f"{kind}:{test}"
        for record in (state["tests"], state["lines"]):          # its tests as of this run
            for t in [t for t in record if t.startswith(key + "::")]:
                del record[t]
        if m["ok"]:
            state["passed"][key] = sid
            state["ran"][key] = sorted(m["ran"])
            state["seconds"][key] = m["seconds"]
            for node, e in tests.items():
                if node.split("::")[0] == test:
                    state["tests"][f"{kind}:{node}"] = {"s": round(e["seconds"], 3),
                                                        "r": sorted(code(c) for c in e["ran"])}
                    if lines.get(node):
                        state["lines"][f"{kind}:{node}"] = {str(code(c)): sorted(ln)
                                                            for c, ln in lines[node].items()}
        else:
            state["passed"].pop(key, None)
    save_state(state)
    return state


def record_harnesses(names, snap: dict, state: dict | None = None):
    state = load_state() if state is None else state
    sid = _sid(snap)
    state["snaps"][sid] = snap
    for h in names:
        state["passed"][f"harness:{h}"] = sid
    save_state(state)


def changed_since(key: str, snap: dict, state: dict):
    """The snapshot keys that differ from when `key` last passed; None if it never has."""
    sid = state["passed"].get(key)
    old = state["snaps"].get(sid) if sid else None
    if old is None:
        return None
    return sorted(k for k in set(snap) | set(old) if snap.get(k) != old.get(k))


# -- the selection -----------------------------------------------------------
def harnesses_for(files) -> list:
    out = set()
    every = all_harnesses()
    for f in files:
        if f.startswith(FOLDED):                                  # a vendored tree: everything
            out.update(every)
        if fnmatch.fnmatchcase(f, HARNESS_GLOB):
            out.add(Path(f).stem)
        for pattern, hs in HARNESS_RULES:
            if fnmatch.fnmatchcase(f, pattern):
                out.update(every if hs == ALL else hs)
    return sorted(out)


def tests_naming(files) -> list:
    """The test files a non-Python change reaches: those that name the file;
    or quote every folder it sits in and either a glob that matches it
    ("*.html") or a second folder (static/help/…)."""
    tests = [t for t in sorted((ROOT / "tests").glob("test_*.py")) if t.name not in NAMING_SKIP]
    text = {t: t.read_text(encoding="utf-8") for t in tests}
    globs = {t: re.findall(r"""["']([^"'\s]*\*[^"'\s]*)["']""", src) for t, src in text.items()}
    out = set()
    for f in files:
        p = Path(f)
        if p.suffix in (".py", ".md") or TOP in f or FN in f or f.startswith((HARNESS_DIR, FOLDED)):
            continue
        folders = [part for part in p.parent.parts if part not in (".", "")]
        for t, src in text.items():
            inside = folders and all(f'"{d}"' in src for d in folders)
            if p.name in src or inside and (len(folders) > 1 or any(fnmatch.fnmatch(p.name, g) for g in globs[t]))                     or any(fnmatch.fnmatchcase(f, pat) and re.search(rx, src) for pat, rx in TEST_RULES):
                out.add(t.relative_to(ROOT).as_posix())
    return sorted(out)


def all_harnesses() -> list:
    return sorted(p.stem for p in ROOT.glob(HARNESS_GLOB))


def all_tests() -> list:
    return sorted(t.relative_to(ROOT).as_posix() for t in (ROOT / "tests").glob("test_*.py"))


def why_run(key: str, test: str, snap: dict, state: dict, graph: dict, named: dict):
    """Why test file `test` must run again for `key`, or None if it needn't."""
    changed = changed_since(key, snap, state)
    if changed is None:
        return "never passed here"
    if not changed:
        return None
    if test in changed:
        return "the test file changed"
    every = [f for f in changed if f in EVERYTHING or f.startswith(FOLDED)]
    if every:
        return f"{every[0]} changed"
    old = state["snaps"][state["passed"][key]]
    hit = set(changed)
    for path, q in state["ran"].get(key, ()):
        k = fn_key(path, q, (snap, old))
        if k in hit:
            return f"it ran {path} {q.split('.<locals>')[0] if FN in k else '(module level)'}, which changed"
    tops = {f[:-len(TOP)] for f in changed if f.endswith(TOP)} & reached(test, graph)
    if tops:
        return f"it imports {sorted(tops)[0]}, whose module level changed"
    key_files = tuple(changed)
    if key_files not in named:
        named[key_files] = tests_naming(changed)
    if test in named[key_files]:
        return "it names a file that changed"
    return None


def select(fast_only: bool = False, snap: dict | None = None, state: dict | None = None) -> Pick:
    snap = snapshot() if snap is None else snap
    state = load_state() if state is None else state
    pick = Pick(snapshot=snap)
    for h in all_harnesses():
        files = changed_since(f"harness:{h}", snap, state)
        if files is None or h in harnesses_for(files):
            pick.harnesses.append(h)
            pick.why[h] = "never passed here" if files is None else "a file it covers changed"
    graph, named = import_graph(snap), {}
    for kind in KINDS[:1] if fast_only else KINDS:
        for t in all_tests():
            why = why_run(f"{kind}:{t}", t, snap, state, graph, named)
            if why:
                pick.tests[kind].append(t)
                pick.why[f"{kind}:{t}"] = why
    return pick


def describe(pick: Pick) -> str:
    lines = ["=== affected (since each last passed)"]
    for kind, tests in pick.tests.items():
        lines.append(f"    {kind}: {len(tests)} test file(s)")
        lines += [f"      {t}: {pick.why[f'{kind}:{t}']}" for t in tests]
    lines.append(f"    harnesses: {', '.join(pick.harnesses) or 'none'}")
    return "\n".join(lines)



def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.reconfigure(encoding="utf-8")
    print(describe(select("--fast-only" in argv)))
