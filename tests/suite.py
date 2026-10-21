"""
suite.py — the full pytest suite, side by side (pytest-xdist), with as many
workers as the machine's share (tests/cpu_cap.py: CCT_TEST_CPUS inside a test
turn). Prints the worker count and wall time. Run it inside a turn:

    python C:/Users/cmyer/Documents/CCT_Controller/bin/cct.py test --from PulleyWebApp --label "full suite" -- \
      C:/Users/cmyer/Documents/PulleyWebApp/.venv312/Scripts/python.exe tests/suite.py [--affected] [pytest args]

--affected runs only the test files the work in progress reaches since each last
passed (tools/affected.py, cct_common.testing.affected); the whole suite still runs
at night (tools/nightly_test.py). Every run records what each test ran
(cct_common.testing.record, which pytest.ini loads), for --affected and for
tools/neg_controls.py's scopes; a run's test files that all passed are recorded
as passed on the files as they were when it started.

--serial runs it in one process (no xdist), to compare counts and times.
(Before 2026-10-16 a turn started `bash` as WSL's System32 bash.exe, so
`bash -c '... -n $CCT_TEST_CPUS'` silently ran nothing; testturn.py now
finds programs along PATH. This script needs no shell either way.)
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'tools'))
from cpu_cap import test_cpus   # noqa: E402
import affected                 # noqa: E402  (cct_common.testing.affected with the pulley's rules)


def main(args):
    serial = '--serial' in args
    only_affected = '--affected' in args
    args = [a for a in args if a not in ('--serial', '--affected')]
    snap = affected.snapshot()                      # the files as this run tests them
    if only_affected:
        pick = affected.select(fast_only=True, snap=snap)
        print(affected.describe(pick), flush=True)
        if not pick.tests['fast']:
            print('suite: nothing to run (every test file passed on these files)', flush=True)
            return 0
        args = [*args, *pick.tests['fast']]
    n = test_cpus()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='pulley-run-map-') as folder:
        cmd = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',
               *([] if serial else ['-n', str(n)]), *args]
        # CCT_TEST_MAP, and CCT_TEST_LINES: big functions get line scopes for the controls (0.25.2)
        rc = subprocess.call(cmd, cwd=ROOT, env={**os.environ, **affected.record_env(Path(folder))})
        if rc in (0, 1):                            # ran (1: some failed); not a usage or collection error
            affected.record_run('fast', Path(folder), snap)
    print(f"suite: {'serial' if serial else f'{n} workers'}, {time.monotonic() - start:.0f} s wall", flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
