"""pytest plugin: run only the tests listed in the file CCT_SELECT names, one
node id a line, in that order (cct_common 0.25.0; cct_common.testing.controls
uses it for a control's tests, cheapest first, so -x stops at the first that
catches it). Load it with `-p cct_common.testing.select`; without CCT_SELECT
it does nothing.

With CCT_CONTROL_CAUGHT set (0.62.1), the first failure is written to that
file as {"by": node id, "when": setup|call|teardown|collect}: a failed test
phase, or a test file that didn't collect. The controls read the catcher
from it, whatever the app's -r, -q or addopts print (Planetary, 2026-11-08:
an addopts -rs hid pytest's "FAILED" line, so every catch was saved bare),
and stop the run on it (-x under xdist waits for the tests still running).
The first write wins; under xdist the workers and the controller both see
the report."""
import json
import os
from pathlib import Path

CAUGHT_ENV = "CCT_CONTROL_CAUGHT"


def _caught(nodeid, when):
    path = os.environ.get(CAUGHT_ENV)
    if not path or os.path.exists(path):
        return
    part = f"{path}.{os.getpid()}.part"               # whole or not there: the runner polls it
    try:
        Path(part).write_text(json.dumps({"by": nodeid, "when": when}), encoding="utf-8")
        if not os.path.exists(path):
            os.replace(part, path)
    except OSError:
        pass
    finally:
        try:
            os.unlink(part)
        except OSError:
            pass


def pytest_runtest_logreport(report):
    if report.failed:
        _caught(report.nodeid, report.when)


def pytest_collectreport(report):
    if report.failed:
        _caught(report.nodeid, "collect")


def pytest_collection_modifyitems(session, config, items):
    listed = os.environ.get("CCT_SELECT")
    if not listed:
        return
    order = {n: i for i, n in enumerate(Path(listed).read_text(encoding="utf-8").splitlines()) if n}
    keep = sorted((it for it in items if it.nodeid in order), key=lambda it: order[it.nodeid])
    dropped = [it for it in items if it.nodeid not in order]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep
