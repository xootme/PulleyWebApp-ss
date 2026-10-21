"""pytest plugin: which of the repo's functions each test runs, how long it
took, and whether every selected test in each file passed (cct_common 0.25.0;
Bevel Gear Designer's tests/run_map.py, its ADR-031). Load it with
`-p cct_common.testing.record` and CCT_TEST_MAP set to a folder; without
CCT_TEST_MAP it does nothing. cct_common.testing.affected reads what it
writes to pick the tests a change reaches, and cct_common.testing.controls
the tests that should catch each negative control.

Python 3.12+'s sys.monitoring, not coverage or pytest-testmon (no extra
package): a PY_START callback notes a function's code object the first time
it starts in a test, then disables itself for that code until the next test
(restart_events), so each function costs one call per test. What runs in a
test's setup (a module-scoped fixture's work) is the first test's that
needs it; the file's record is the union of its tests', so a whole file
still sees everything its tests share. Measured on Bevel's geometry tests,
the record costs nothing: 31.1 s without it, 31.5 s with it.

The app's caches are cleared when the test file changes, or a later file
served from the cache would never be seen running the code that built it:
every functools.lru_cache in the repo's modules, and the dicts pytest.ini
names (`cct_test_caches = app:_CACHE geometry.spiral_design:_GEN`). Within
a file, a test served from a cache isn't seen building it: a control's
tests are the ones that did, and the whole suite is its fallback.

With CCT_TEST_LINES naming a file of "path<TAB>qualname" lines (the big
functions, affected.record_env's; 0.25.2), LINE events are turned on for
those functions' code objects alone (set_local_events), once a line each
per test, so a control in one can take only the tests that ran its line:
about 3 % of a fast suite's CPU time.

Each process that runs tests (each xdist worker) writes
`<CCT_TEST_MAP>/<pid>.json`: {"codes": [[path, qualname], ...],
"files": {file: {"selected", "finished", "ok", "seconds", "ran": [code]}},
"tests": {node id: {"ok", "seconds", "ran": [code]}},
"lines": {node id: {code: [line]}}}, the codes by their index in "codes". Module-level code runs at import, outside any test:
affected.py hashes it apart (top_hash). The repo is pytest's rootdir, or
CCT_TEST_MAP_ROOT."""
from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path

import pytest

MODULE = "<module>"


class Recorder:
    def __init__(self, out: Path, root: Path, caches=(), big=()):
        self.out, self.root, self.caches = out, root, list(caches)
        self.big = set(big)                   # (path, qualname): functions whose lines each test runs are noted
        self.file = self.test = None
        self.files: dict = {}                 # test file -> {"selected", "finished", "ok", "seconds", "ran": set}
        self.tests: dict = {}                 # node id -> {"ok", "seconds", "ran": set}
        self.lines: dict = {}                 # node id -> {code index: {line}}, in big functions only
        self.codes: dict = {}                 # (path, qualname) -> its index
        self._paths: dict = {}
        self._watched: dict = {}              # code object -> its index, LINE events on
        mon = sys.monitoring
        self.tool = next(t for t in (4, 3, 5, 2) if mon.get_tool(t) is None)
        mon.use_tool_id(self.tool, "cct-test-record")
        mon.register_callback(self.tool, mon.events.PY_START, self._start)
        mon.register_callback(self.tool, mon.events.LINE, self._line)

    def _path(self, filename: str):
        p = self._paths.get(filename)
        if p is None:
            try:
                rel = Path(filename).resolve().relative_to(self.root).as_posix()
                p = "" if rel.startswith(".venv") or "/cct_common/testing/" in f"/{rel}" else rel
            except ValueError:                            # outside the repo
                p = ""
            self._paths[filename] = p
        return p

    def _start(self, code, offset):
        if self.file is not None:
            p = self._path(code.co_filename)
            if p:
                c = self.codes.setdefault((p, code.co_qualname), len(self.codes))
                self.files[self.file]["ran"].add(c)
                if self.test is not None:
                    self.tests[self.test]["ran"].add(c)
                if self.big and code not in self._watched and (p, code.co_qualname.split(".<locals>")[0]) in self.big:
                    self._watched[code] = c                   # its lines from now on, once a test each
                    sys.monitoring.set_local_events(self.tool, code, sys.monitoring.events.LINE)
        return sys.monitoring.DISABLE

    def _line(self, code, line):
        if self.test is not None:
            c = self._watched.get(code)
            if c is not None:
                self.lines.setdefault(self.test, {}).setdefault(c, set()).add(line)
        return sys.monitoring.DISABLE

    def _entry(self, f):
        return self.files.setdefault(f, {"selected": 0, "finished": 0, "ok": True, "seconds": 0.0, "ran": set()})

    def clear_caches(self):
        for name, attr in self.caches:
            cache = getattr(sys.modules.get(name), attr, None)
            if cache is not None:
                cache.clear()
        root = str(self.root).lower()
        for mod in list(sys.modules.values()):
            f = (getattr(mod, "__file__", None) or "").lower()
            if not f.startswith(root) or ".venv" in f:
                continue
            for v in list(vars(mod).values()):
                if isinstance(v, functools._lru_cache_wrapper):
                    v.cache_clear()

    # -- pytest hooks --------------------------------------------------
    def pytest_collectreport(self, report):
        f = report.nodeid.split("::")[0]
        if f.endswith(".py") and "::" not in report.nodeid:
            self._entry(f)
            if report.failed:                             # it didn't import
                self._entry(f)["ok"] = False

    def pytest_collection_finish(self, session):
        for item in session.items:
            self._entry(item.nodeid.split("::")[0])["selected"] += 1
        sys.monitoring.set_events(self.tool, sys.monitoring.events.PY_START)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        f = item.nodeid.split("::")[0]
        if f != self.file:
            self.file = f
            self.clear_caches()
        self.test = item.nodeid
        self.tests.setdefault(item.nodeid, {"ok": True, "seconds": 0.0, "ran": set()})
        sys.monitoring.restart_events()

    def pytest_runtest_logreport(self, report):
        e = self._entry(report.nodeid.split("::")[0])
        t = self.tests.setdefault(report.nodeid, {"ok": True, "seconds": 0.0, "ran": set()})
        if report.failed:
            e["ok"] = t["ok"] = False
        e["seconds"] += report.duration
        t["seconds"] += report.duration
        if report.when == "teardown":
            e["finished"] += 1

    def pytest_sessionfinish(self, session):
        sys.monitoring.set_events(self.tool, 0)
        sys.monitoring.free_tool_id(self.tool)
        if not any(e["selected"] for e in self.files.values()):   # xdist's controller: its workers write
            return
        self.out.mkdir(parents=True, exist_ok=True)
        data = {"codes": [list(k) for k in sorted(self.codes, key=self.codes.get)],
                "files": {f: {**e, "ran": sorted(e["ran"])} for f, e in self.files.items()},
                "tests": {t: {**e, "ran": sorted(e["ran"])} for t, e in self.tests.items()},
                "lines": {t: {str(c): sorted(ln) for c, ln in by.items()} for t, by in self.lines.items()}}
        (self.out / f"{os.getpid()}.json").write_text(json.dumps(data), encoding="utf-8")


def pytest_addoption(parser):
    parser.addini("cct_test_caches", "module:attribute dict caches to clear between test files "
                  "(cct_common.testing.record)", type="args", default=[])


def pytest_configure(config):
    out = os.environ.get("CCT_TEST_MAP")
    if not out:
        return
    root = Path(os.environ.get("CCT_TEST_MAP_ROOT") or config.rootpath).resolve()
    caches = [tuple(spec.partition(":")[::2]) for spec in config.getini("cct_test_caches")]   # cleared once imported
    big = []                                              # cct_common.testing.affected.record_env's list
    if os.environ.get("CCT_TEST_LINES"):
        try:
            text = Path(os.environ["CCT_TEST_LINES"]).read_text(encoding="utf-8")
            big = [tuple(ln.split("\t", 1)) for ln in text.splitlines() if "\t" in ln]
        except OSError:
            pass
    config.pluginmanager.register(Recorder(Path(out), root, caches, big), "cct-test-recorder")
