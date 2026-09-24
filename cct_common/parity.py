"""Cross-repo parity: proving two copies of one rule still agree.

Some rules are implemented twice on purpose. The clearest case is D047's
rect-side snap: E-Box Designer decides it server-side in Python, on a
normalize pass PCB Importer does not have, so PCB Importer carries a
JavaScript port that runs while drawing. The flags it sets travel into a
board library and are re-derived by the Python when the board is
imported, so a disagreement between the two produces a wrong board and
nothing anywhere reports it.

A parity test runs both copies over the same inputs and diffs the
answers. That inherently needs both repos on disk -- no amount of moving
the test changes it, since one of the two implementations lives in the
other repo. What this module provides is the part that IS shareable:

  * one documented, overridable way to find a peer repo, instead of a
    relative path written out per test;
  * a skip that can be made loud, so the guard cannot quietly stop
    running on a machine where the layout differs;
  * one node bridge, so a Python test can call the JavaScript copy.

The CCT repos are siblings under one directory and install each other
editable (`-e ../cct_common`), so the sibling default is right on a
normal checkout. `CCT_REPO_<NAME>` overrides it for one that is not.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Set to 1/true to turn a missing peer repo into a failure rather than a
#: skip. Meant for a full or release run: a parity guard that skips is
#: indistinguishable from one that passes, and this is the difference.
REQUIRED_ENV = "CCT_PARITY_REQUIRED"


def _env_key(name: str) -> str:
    return "CCT_REPO_" + "".join(
        c if c.isalnum() else "_" for c in name).upper()


def parity_required() -> bool:
    return os.environ.get(REQUIRED_ENV, "").strip().lower() in ("1", "true", "yes")


def peer_repo(name, near, marker=None):
    """Locate sibling repo `name`, or return None.

    `near` is any path inside the calling repo (pass `__file__`); the
    peer is looked for beside that repo. `marker` is a repo-relative path
    that must exist for the answer to count -- pass the actual file the
    test needs, so a stale or partial checkout reads as absent here
    rather than as a confusing failure later.
    """
    override = os.environ.get(_env_key(name))
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    else:
        # Walk up from `near`: the caller may be at tests/x.py, and the
        # sibling sits beside the repo root, not beside the test.
        start = Path(near).resolve()
        for parent in [start] + list(start.parents):
            candidates.append(parent.parent / name)

    for c in candidates:
        if c.is_dir() and (marker is None or (c / marker).exists()):
            return c
    return None


def require_peer(name, near, marker=None, reason=None):
    """`peer_repo`, but skip the test when it is not there.

    Skips by default -- a contributor with one repo checked out should
    not see a failure for a test that cannot apply to them. Fails
    instead under CCT_PARITY_REQUIRED, so a full run cannot pass while
    silently skipping the check.
    """
    import pytest

    found = peer_repo(name, near, marker)
    if found:
        return found

    detail = reason or f"parity against {name}"
    msg = (f"{name} not found beside this repo"
           + (f" (looked for {marker!r} in it)" if marker else "")
           + f"; set {_env_key(name)} to point at it. Needed for: {detail}.")
    if parity_required():
        pytest.fail(f"{REQUIRED_ENV} is set but {msg}")
    pytest.skip(msg)


def node() -> str:
    """Path to node, or skip -- the JS copy cannot be run without it."""
    import pytest

    found = shutil.which("node")
    if not found:
        if parity_required():
            pytest.fail(f"{REQUIRED_ENV} is set but node is not installed")
        pytest.skip("node is not installed")
    return found


def run_js(script, payload=None, timeout=120, cwd=None):
    """Run `script` under node and return the JSON it writes to stdout.

    `payload` is handed over as a file rather than argv or stdin: these
    parity runs pass hundreds of cases, which is well past a Windows
    command line and awkward to stream. The script receives two
    arguments -- the payload's path, and the directory holding this
    module, so it can `require` the shared js_extract helper without
    knowing where cct_common was installed.
    """
    exe = node()
    tmp = Path(tempfile.mkdtemp(prefix="cct_parity_"))
    try:
        script_path = tmp / "run.js"
        script_path.write_text(script, encoding="utf-8")
        payload_path = tmp / "payload.json"
        payload_path.write_text(json.dumps(payload if payload is not None else {}),
                                encoding="utf-8")

        res = subprocess.run(
            [exe, str(script_path), str(payload_path), str(HERE)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None)
        if res.returncode != 0:
            raise AssertionError(
                "the JavaScript side failed:\n"
                f"--- stderr ---\n{res.stderr}\n--- stdout ---\n{res.stdout}")
        if not res.stdout.strip():
            raise AssertionError(
                "the JavaScript side wrote nothing to stdout; it must "
                "write its result as JSON.\n"
                f"--- stderr ---\n{res.stderr}")
        return json.loads(res.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: Preamble for a script passed to `run_js`. Gives it `PAYLOAD`, the
#: shared extraction helpers, and `emit()` for its result.
JS_PRELUDE = """
const fs = require('fs'), path = require('path');
const PAYLOAD = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const { extractFunction, extractFunctions, extractFrom, buildCallable } =
  require(path.join(process.argv[3], 'js_extract.js'));
const emit = (v) => process.stdout.write(JSON.stringify(v));
"""


def js_answers(script, payload=None, **kw):
    """`run_js` with JS_PRELUDE already in front of `script`."""
    return run_js(JS_PRELUDE + script, payload, **kw)


def diff_answers(cases, left, right, label_left="python", label_right="js",
                 describe=None, limit=8):
    """Compare two answer lists case by case; return a report, or ''.

    Returned rather than asserted so the caller decides how it surfaces,
    and so this stays testable without pytest.
    """
    if len(left) != len(right) or len(left) != len(cases):
        return (f"length mismatch: {len(cases)} cases, {len(left)} "
                f"{label_left}, {len(right)} {label_right}")

    diffs = [(c, a, b) for c, a, b in zip(cases, left, right) if a != b]
    if not diffs:
        return ""

    lines = [f"{len(diffs)} of {len(cases)} cases disagree:"]
    for c, a, b in diffs[:limit]:
        lines.append(f"  {describe(c) if describe else c}")
        lines.append(f"    {label_left:<8}: {a}")
        lines.append(f"    {label_right:<8}: {b}")
    if len(diffs) > limit:
        lines.append(f"  ... and {len(diffs) - limit} more")
    return "\n".join(lines)
