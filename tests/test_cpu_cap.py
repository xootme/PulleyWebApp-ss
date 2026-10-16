"""
test_cpu_cap.py — the tests stay within their share of the shared machine
(~/.claude/CLAUDE.md, "Tests take turns"; tests/cpu_cap.py).

The suite runs under pytest-xdist as `pytest -n $CCT_TEST_CPUS` (inside a test
turn). These fail if anything starts more: a pool, xdist's -n or harness
parallelism must take its size from test_cpus() or CCT_TEST_CPUS.
"""
import os
import re
from pathlib import Path

import conftest
from cpu_cap import test_cpus as cpus

ROOT = Path(__file__).resolve().parent.parent
# Anything that starts several workers at once.
PARALLEL = re.compile(r'ProcessPoolExecutor|ThreadPoolExecutor|multiprocessing|\bPool\(|os\.cpu_count'
                      r'|-n\s+auto|["\']-n["\']|numprocesses|Promise\.all|worker_threads|child_process\.fork')
# A pytest command's worker count: -n N, -nN, --numprocesses N / =N.
WORKERS = re.compile(r'''(?:\s-n\s*|--numprocesses[=\s]+)([^\s'"]+)''')
CAPPED = {'$CCT_TEST_CPUS', '${CCT_TEST_CPUS}', '%CCT_TEST_CPUS%', '$env:CCT_TEST_CPUS'}


def test_the_cap_follows_cct_test_cpus(monkeypatch):
    monkeypatch.setenv('CCT_TEST_CPUS', '5')
    assert cpus() == 5
    monkeypatch.setenv('CCT_TEST_CPUS', '0')
    assert cpus() == max(1, (os.cpu_count() or 2) // 2)
    monkeypatch.delenv('CCT_TEST_CPUS')
    assert cpus() == max(1, (os.cpu_count() or 2) // 2)
    assert cpus() <= max(1, (os.cpu_count() or 2) // 2)


def test_n_auto_is_the_cap_not_every_cpu(monkeypatch):
    """A stray `-n auto` asks conftest's hook, which answers the cap."""
    monkeypatch.setenv('CCT_TEST_CPUS', '3')
    assert conftest.pytest_xdist_auto_num_workers(None) == 3
    monkeypatch.delenv('CCT_TEST_CPUS')
    assert conftest.pytest_xdist_auto_num_workers(None) == max(1, (os.cpu_count() or 2) // 2)


def test_pytest_ini_starts_no_workers():
    """Plain `pytest` (a single test, a file) stays serial; the full suite asks for -n."""
    ini = (ROOT / 'pytest.ini').read_text(encoding='utf-8')
    assert not re.search(r'-n\s*\S|numprocesses|--dist', ini), 'pytest.ini starts xdist workers'


def _worker_counts(text):
    return [m.group(1) for line in text.splitlines() if 'pytest' in line
            for m in WORKERS.finditer(' ' + line)]


def test_the_worker_count_check_catches_what_it_should():
    assert _worker_counts('pytest -n $CCT_TEST_CPUS tests') == ['$CCT_TEST_CPUS']
    assert _worker_counts('python -m pytest -n auto') == ['auto']
    assert _worker_counts('pytest -n 8 -q') == ['8']
    assert _worker_counts('pytest --numprocesses=12') == ['12']
    assert _worker_counts('git log -n 5') == []           # not a pytest line


def test_every_written_pytest_command_takes_its_workers_from_the_cap():
    """Docs and scripts that spell out a pytest run use -n $CCT_TEST_CPUS, never
    -n auto or a fixed number (which ignores the turn's share)."""
    files = [ROOT / 'pytest.ini', ROOT / 'CLAUDE.md', ROOT / 'web_provisioning.md',
             *(ROOT / 'tests').glob('*.py'), *(ROOT / 'tests').glob('*.md'), *(ROOT / 'tests' / 'browser').glob('*.js')]
    me = Path(__file__).name
    bad = [f'{f.relative_to(ROOT)}: -n {n}' for f in files if f.is_file() and f.name != me
           for n in _worker_counts(f.read_text(encoding='utf-8', errors='replace')) if n not in CAPPED]
    assert bad == [], f'pytest workers not from CCT_TEST_CPUS: {bad}'


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
