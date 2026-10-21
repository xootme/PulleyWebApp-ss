"""pytest plugin: run only the tests listed in the file CCT_SELECT names, one
node id a line, in that order (cct_common 0.25.0; cct_common.testing.controls
uses it for a control's tests, cheapest first, so -x stops at the first that
catches it). Load it with `-p cct_common.testing.select`; without CCT_SELECT
it does nothing."""
import os
from pathlib import Path


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
