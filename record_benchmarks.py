"""
record_benchmarks.py — run benchmarks and append results to Perf_History.csv.

Usage (from project root):
    .venv312/Scripts/python record_benchmarks.py

What it does:
  1. Runs the full pytest-benchmark suite, writing raw JSON to .benchmarks/latest.json
  2. Reads the current git commit hash and branch
  3. Appends one row per test to Perf_History.csv
  4. Prints a summary table sorted by mean time

Exit code 0 on success, 1 if any benchmark fails.
"""
import csv
import json
import os
import subprocess
import sys
from datetime import datetime

ROOT        = os.path.dirname(os.path.abspath(__file__))
VENV_PY     = os.path.join(ROOT, '.venv312', 'Scripts', 'python.exe')
JSON_OUT    = os.path.join(ROOT, '.benchmarks', 'latest.json')
CSV_FILE    = os.path.join(ROOT, 'Perf_History.csv')
CSV_COLUMNS = [
    'date', 'commit', 'branch',
    'test',
    'mean_ms', 'min_ms', 'max_ms', 'stddev_ms', 'median_ms',
    'rounds',
]


def _git(cmd):
    try:
        return subprocess.check_output(cmd, cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except subprocess.CalledProcessError:
        return 'unknown'


def run_benchmarks():
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    env = os.environ.copy()
    env['PYTHONPATH'] = ROOT
    result = subprocess.run(
        [VENV_PY, '-m', 'pytest',
         'tests/test_benchmarks.py',
         '--benchmark-only',
         f'--benchmark-json={JSON_OUT}',
         '-q', '--tb=short'],
        cwd=ROOT, env=env,
    )
    return result.returncode == 0


def record():
    with open(JSON_OUT, 'r', encoding='utf-8') as f:
        data = json.load(f)

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    commit    = _git(['git', 'rev-parse', '--short', 'HEAD'])
    branch    = _git(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])

    rows = []
    for b in data['benchmarks']:
        s = b['stats']
        rows.append({
            'date':      timestamp,
            'commit':    commit,
            'branch':    branch,
            'test':      b['name'],
            'mean_ms':   round(s['mean']   * 1000, 3),
            'min_ms':    round(s['min']    * 1000, 3),
            'max_ms':    round(s['max']    * 1000, 3),
            'stddev_ms': round(s['stddev'] * 1000, 3),
            'median_ms': round(s['median'] * 1000, 3),
            'rounds':    s['rounds'],
        })

    write_header = not os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    # Print summary table
    rows.sort(key=lambda r: r['mean_ms'], reverse=True)
    col_w = max(len(r['test']) for r in rows) + 2
    print(f'\n{"Test":<{col_w}}  {"Mean ms":>9}  {"Min ms":>8}  {"Max ms":>8}  {"±ms":>7}')
    print('-' * (col_w + 40))
    for r in rows:
        print(f'{r["test"]:<{col_w}}  {r["mean_ms"]:>9.3f}  '
              f'{r["min_ms"]:>8.3f}  {r["max_ms"]:>8.3f}  {r["stddev_ms"]:>7.3f}')
    print(f'\nAppended {len(rows)} rows to Perf_History.csv  '
          f'(commit {commit}, branch {branch})')


if __name__ == '__main__':
    print('Running benchmarks…')
    ok = run_benchmarks()
    if not ok:
        print('\nBenchmark run failed — check output above.', file=sys.stderr)
        sys.exit(1)
    record()
