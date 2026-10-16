"""
test_cpu_cap.py — the tests stay within their share of the shared machine
(~/.claude/CLAUDE.md, "Tests take turns"; tests/cpu_cap.py).

Pulley's tests run serially today. These fail if that changes without the cap:
a pool, xdist's -n or harness parallelism must take its size from test_cpus().
"""
import os
import re
from pathlib import Path

from cpu_cap import test_cpus as cpus

ROOT = Path(__file__).resolve().parent.parent
# Anything that starts several workers at once.
PARALLEL = re.compile(r'ProcessPoolExecutor|ThreadPoolExecutor|multiprocessing|\bPool\(|os\.cpu_count'
                      r'|-n\s+auto|["\']-n["\']|numprocesses|Promise\.all|worker_threads|child_process\.fork')


def test_the_cap_follows_cct_test_cpus(monkeypatch):
    monkeypatch.setenv('CCT_TEST_CPUS', '5')
    assert cpus() == 5
    monkeypatch.setenv('CCT_TEST_CPUS', '0')
    assert cpus() == max(1, (os.cpu_count() or 2) // 2)
    monkeypatch.delenv('CCT_TEST_CPUS')
    assert cpus() == max(1, (os.cpu_count() or 2) // 2)
    assert cpus() <= max(1, (os.cpu_count() or 2) // 2)


def test_pytest_is_not_set_to_run_workers_unbounded():
    ini = (ROOT / 'pytest.ini').read_text(encoding='utf-8')
    assert not re.search(r'-n\s*\S|numprocesses|--dist', ini), 'pytest.ini starts xdist workers'


def test_every_parallel_test_takes_its_size_from_the_cap():
    """A test file or browser harness that runs things side by side uses test_cpus
    (Python) or CCT_TEST_CPUS (a harness) for its size."""
    me = {Path(__file__).name, 'cpu_cap.py'}
    uncapped = []
    for f in [*(ROOT / 'tests').rglob('*.py'), *(ROOT / 'tests' / 'browser').glob('*.js')]:
        if f.name in me or '__pycache__' in f.parts:
            continue
        text = f.read_text(encoding='utf-8', errors='replace')
        if PARALLEL.search(text) and 'test_cpus' not in text and 'CCT_TEST_CPUS' not in text:
            uncapped.append(f'{f.relative_to(ROOT)}: {PARALLEL.search(text).group(0)}')
    assert uncapped == [], f'parallel without the CPU cap: {uncapped}'
