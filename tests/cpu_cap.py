"""
cpu_cap.py — how many workers a test may start.

The CCT sessions share one machine (~/.claude/CLAUDE.md, "Tests take turns"):
inside a test turn (cct.py test) CCT_TEST_CPUS says how many CPUs the run may
use; outside one, half the machine. Size any pool, xdist -n or harness
parallelism from test_cpus(), never os.cpu_count() or -n auto on their own.
tests/test_cpu_cap.py holds the test code to this.
"""
import os


def test_cpus() -> int:
    try:
        n = int(os.environ.get('CCT_TEST_CPUS', ''))
    except ValueError:
        n = 0
    return n if n > 0 else max(1, (os.cpu_count() or 2) // 2)


test_cpus.__test__ = False   # a helper, not a pytest test
