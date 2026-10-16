"""
suite.py — the full pytest suite, side by side (pytest-xdist), with as many
workers as the machine's share (tests/cpu_cap.py: CCT_TEST_CPUS inside a test
turn). Prints the worker count and wall time. Run it inside a turn:

    python C:/Users/cmyer/Documents/CCT_Controller/bin/cct.py test --from PulleyWebApp --label "full suite" -- \
      C:/Users/cmyer/Documents/PulleyWebApp/.venv312/Scripts/python.exe tests/suite.py [pytest args]

--serial runs it in one process (no xdist), to compare counts and times.
(Before 2026-10-16 a turn started `bash` as WSL's System32 bash.exe, so
`bash -c '... -n $CCT_TEST_CPUS'` silently ran nothing; testturn.py now
finds programs along PATH. This script needs no shell either way.)
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpu_cap import test_cpus   # noqa: E402


def main(args):
    serial = '--serial' in args
    args = [a for a in args if a != '--serial']
    n = test_cpus()
    cmd = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',
           *([] if serial else ['-n', str(n)]), *args]
    start = time.monotonic()
    rc = subprocess.call(cmd, cwd=Path(__file__).resolve().parent.parent)
    print(f"suite: {'serial' if serial else f'{n} workers'}, {time.monotonic() - start:.0f} s wall", flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
