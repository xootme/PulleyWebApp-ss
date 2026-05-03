"""
concurrency_test.py — Concurrency and GIL-blocking test harness.

Tests each endpoint at increasing concurrency levels and reports whether
requests are being serialised (GIL blocking or single-worker queuing) or
running in parallel.

Usage:
    # Against the dev server (python app.py — single-threaded):
    .venv312/Scripts/python concurrency_test.py

    # Against a multi-worker gunicorn instance:
    .venv312/Scripts/python concurrency_test.py --gunicorn

    # Against an already-running server at a custom URL:
    .venv312/Scripts/python concurrency_test.py --url http://localhost:8000

    # Record results to Perf_History.csv (append rows with ratio):
    .venv312/Scripts/python concurrency_test.py --csv Perf_History.csv

    # Verbose: print every individual request time:
    .venv312/Scripts/python concurrency_test.py --verbose

Interpreting results
--------------------
Serialisation ratio = mean_concurrent / mean_baseline
  ~1.0  -> requests ran in parallel (good)
  ~N    -> fully serialised (GIL or single worker)
  1-N   -> partial parallelism (some workers free, some blocked)

A ratio > 1.5 at concurrency=2 is flagged as a potential blocking issue.
"""

import argparse
import csv
import io
import os
import statistics
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Force UTF-8 output on Windows so arrow/tick characters don't crash
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import requests as _requests
except ImportError:
    print('requests not installed — run:  .venv312/Scripts/pip install requests')
    sys.exit(1)


# ---------------------------------------------------------------------------
# Endpoint definitions
# ---------------------------------------------------------------------------
# Each entry: (label, path, params, csv_key)
# csv_key is used as the test name prefix in Perf_History.csv: conc/<csv_key>/N<n>
# Ordered roughly fastest -> slowest so the report reads naturally.

BARE_PARAMS   = {'family': 'HTD', 'pitch': '5M', 'teeth': 40, 'bore': 8,
                 'belt_height': 15}
SPOKE_PARAMS  = {**BARE_PARAMS, 'spoke_count': 5, 'spoke_width': 8,
                 'spoke_hub_od': 25, 'rim_depth': 5, 'spoke_height': 10,
                 'spoke_fillet_tip': 3, 'spoke_fillet_base': 2}
FLANGE_PARAMS = {**SPOKE_PARAMS,
                 'flange_enabled': 1, 'flange_3dprint': 1,
                 'flange_angle': 15, 'flange_rim_radius': 3,
                 'flange_height': 1.5, 'flange_plate_height': 1.5,
                 'flange_bend_radius': 3, 'flange_top_separate': 1}

# Worst-case payload: large pulley, spokes, integrated 3D-print flanges + supports.
# Used by --heavy to test the OOM / serialisation cliff.
HEAVY_PARAMS  = {'family': 'HTD', 'pitch': '5M', 'teeth': 80, 'bore': 12,
                 'belt_height': 25,
                 'spoke_count': 5, 'spoke_width': 8, 'spoke_hub_od': 30,
                 'rim_depth': 8,   'spoke_height': 20,
                 'spoke_fillet_tip': 3, 'spoke_fillet_base': 2,
                 'flange_enabled': 1, 'flange_3dprint': 1,
                 'flange_angle': 15, 'flange_rim_radius': 3, 'flange_height': 1.5,
                 'flange_plate_height': 1.5, 'flange_bend_radius': 3,
                 # integrated mode (no top_separate) + supports
                 'supports_enabled': 1, 'support_nozzle_dia': 0.4,
                 'support_max_spacing': 10, 'support_air_gap': 0.2}

ENDPOINTS = [
    ('SVG  (fast / pure-Python)',    '/download/svg',        BARE_PARAMS,   'svg_bare'),
    ('DXF  (medium / ezdxf)',        '/download/dxf',        BARE_PARAMS,   'dxf_bare'),
    ('SVG  with spokes',             '/download/svg',        SPOKE_PARAMS,  'svg_spokes'),
    ('Preview STL  bare',            '/api/preview-stl',     BARE_PARAMS,   'preview_bare'),
    ('Preview STL  with spokes',     '/api/preview-stl',     SPOKE_PARAMS,  'preview_spokes'),
    ('Download STL  bare',           '/download/stl',        BARE_PARAMS,   'stl_bare'),
    ('Download STL  with spokes',    '/download/stl',        SPOKE_PARAMS,  'stl_spokes'),
    ('Flange STL  3D-print top',     '/download/flange-stl',
     {**FLANGE_PARAMS, 'which': 'top'},                                     'flange_top'),
]

# Heavy endpoints — subset of endpoints using HEAVY_PARAMS.
# Only the most expensive operations are worth testing at worst-case size.
HEAVY_ENDPOINTS = [
    ('HEAVY Preview STL',            '/api/preview-stl',     HEAVY_PARAMS,  'heavy_preview'),
    ('HEAVY Download STL',           '/download/stl',        HEAVY_PARAMS,  'heavy_stl'),
]

CONCURRENCY_LEVELS = [1, 2, 4, 8]
WARMUP_REQUESTS   = 2   # discard these before measuring baseline

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV_COLUMNS = [
    'date', 'commit', 'branch',
    'test',
    'mean_ms', 'min_ms', 'max_ms', 'stddev_ms', 'median_ms', 'p95_ms', 'p99_ms',
    'rounds', 'ratio', 'error_count', 'error_rate',
]


# ---------------------------------------------------------------------------
# Core timing helpers
# ---------------------------------------------------------------------------

def _single_request(session, base_url, path, params):
    """Return (elapsed_seconds, status_code)."""
    t0 = time.perf_counter()
    try:
        r = session.get(base_url + path, params=params, timeout=120)
        elapsed = time.perf_counter() - t0
        return elapsed, r.status_code
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return elapsed, f'ERR:{exc}'


def _run_concurrent(session, base_url, path, params, n):
    """Fire n requests simultaneously; return list of elapsed times."""
    results = [None] * n
    barrier = threading.Barrier(n)          # all threads start at the same moment

    def worker(i):
        barrier.wait()
        results[i] = _single_request(session, base_url, path, params)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_elapsed = time.perf_counter() - wall_start

    times    = [r[0] for r in results if r]
    statuses = [r[1] for r in results if r]
    return times, statuses, wall_elapsed


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

RESET  = '\033[0m'
RED    = '\033[31m'
YELLOW = '\033[33m'
GREEN  = '\033[32m'
BOLD   = '\033[1m'

def _colour(ratio):
    if ratio < 1.3:   return GREEN
    if ratio < 2.0:   return YELLOW
    return RED

def _ratio_label(ratio):
    if ratio < 1.3:   return 'parallel   [OK]'
    if ratio < 2.0:   return 'partial    [~~]'
    return 'SERIALISED [!!]'


def _pct(lst, p):
    if not lst:
        return float('nan')
    s = sorted(lst)
    idx = max(0, int(len(s) * p / 100) - 1)
    return s[idx]


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(cmd):
    try:
        return subprocess.check_output(cmd, cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except subprocess.CalledProcessError:
        return 'unknown'


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def _append_csv(csv_file, rows):
    """Append concurrency result rows to Perf_History.csv."""
    write_header = not os.path.exists(csv_file)
    with open(csv_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f'Appended {len(rows)} concurrency rows to {os.path.basename(csv_file)}')


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests(base_url, verbose=False, csv_file=None, endpoints=None):
    """Run endpoint concurrency tests.

    endpoints: list of (label, path, params, csv_key) — defaults to ENDPOINTS.
    Returns a list of CSV row dicts if csv_file is set, else an empty list.
    """
    if endpoints is None:
        endpoints = ENDPOINTS

    session = _requests.Session()

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    commit    = _git(['git', 'rev-parse', '--short', 'HEAD'])
    branch    = _git(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])

    ratio_issues = []   # (label, n, ratio)
    error_issues = []   # (label, n, error_count, n_total)
    csv_rows     = []

    print(f'\n{BOLD}Concurrency test harness — {base_url}{RESET}')
    print('=' * 78)

    for endpoint in endpoints:
        label, path, params, csv_key = endpoint

        print(f'\n{BOLD}{label}{RESET}  ->  {path}')

        # Warm up
        for _ in range(WARMUP_REQUESTS):
            _single_request(session, base_url, path, params)

        # Baseline: N=1 repeated 5 times to get a stable mean
        baseline_times = []
        for _ in range(5):
            t, status = _single_request(session, base_url, path, params)
            if status == 200:
                baseline_times.append(t)
        if not baseline_times:
            print('  ERROR: baseline requests all failed — skipping')
            continue

        baseline_mean = statistics.mean(baseline_times)
        baseline_p95  = _pct(baseline_times, 95)

        print(f'  baseline (N=1 x 5): mean={baseline_mean*1000:.1f}ms  '
              f'p95={baseline_p95*1000:.1f}ms')

        # Concurrency sweep
        for n in [c for c in CONCURRENCY_LEVELS if c > 1]:
            times, statuses, wall = _run_concurrent(
                session, base_url, path, params, n)

            n_errors   = sum(1 for s in statuses if s != 200)
            error_rate = n_errors / len(statuses) if statuses else 0.0
            ok_times   = [t for t, s in zip(times, statuses) if s == 200]

            if not ok_times:
                print(f'  N={n}: {RED}ALL {n} REQUESTS FAILED{RESET}')
                error_issues.append((label, n, n_errors, n))
                continue

            mean_t   = statistics.mean(ok_times)
            min_t    = min(ok_times)
            max_t    = max(ok_times)
            p95_t    = _pct(ok_times, 95)
            p99_t    = _pct(ok_times, 99)
            stddev_t = statistics.stdev(ok_times) if len(ok_times) > 1 else 0.0
            ratio    = mean_t / baseline_mean
            colour   = _colour(ratio)

            err_str = (f'  {RED}{n_errors}/{n} errors ({error_rate:.0%}){RESET}'
                       if n_errors else '')
            detail  = ('  [' + '  '.join(f'{t*1000:.0f}ms' for t in ok_times) + ']'
                       if verbose else '')

            print(f'  N={n}: mean={mean_t*1000:.1f}ms  p95={p95_t*1000:.1f}ms  '
                  f'wall={wall*1000:.1f}ms  '
                  f'ratio={colour}{ratio:.2f}x  {_ratio_label(ratio)}{RESET}'
                  f'{err_str}{detail}')

            if ratio > 1.5:
                ratio_issues.append((label, n, ratio))
            if n_errors:
                error_issues.append((label, n, n_errors, n))

            if csv_file:
                csv_rows.append({
                    'date':        timestamp,
                    'commit':      commit,
                    'branch':      branch,
                    'test':        f'conc/{csv_key}/N{n}',
                    'mean_ms':     round(mean_t   * 1000, 3),
                    'min_ms':      round(min_t    * 1000, 3),
                    'max_ms':      round(max_t    * 1000, 3),
                    'stddev_ms':   round(stddev_t * 1000, 3),
                    'median_ms':   round(_pct(ok_times, 50) * 1000, 3),
                    'p95_ms':      round(p95_t    * 1000, 3),
                    'p99_ms':      round(p99_t    * 1000, 3),
                    'rounds':      len(ok_times),
                    'ratio':       round(ratio, 4),
                    'error_count': n_errors,
                    'error_rate':  round(error_rate, 4),
                })

    # Summary
    print('\n' + '=' * 78)
    any_issues = ratio_issues or error_issues
    if error_issues:
        print(f'{BOLD}{RED}Errors detected (hard fail):{RESET}')
        for label, n, n_err, n_total in error_issues:
            print(f'  {RED}[ERR]{RESET}  {label}  at N={n}: '
                  f'{n_err}/{n_total} requests failed')
    if ratio_issues:
        print(f'{BOLD}{RED}Potential blocking issues detected:{RESET}')
        for label, n, ratio in ratio_issues:
            print(f'  {RED}[!!]{RESET}  {label}  at N={n}: {ratio:.2f}x baseline '
                  f'(expected ~1.0 for true parallelism)')
        print()
        print('Interpretation:')
        print('  ratio ~= N  -> GIL holding the thread or single gunicorn worker')
        print('  ratio 1-N   -> some workers free; other requests queuing')
        print('  ratio ~= 1  -> requests running concurrently')
    if not any_issues:
        print(f'{GREEN}{BOLD}No blocking issues or errors detected.[OK]{RESET}')

    print()

    if csv_file and csv_rows:
        _append_csv(csv_file, csv_rows)

    return csv_rows


# ---------------------------------------------------------------------------
# Optional gunicorn launcher
# ---------------------------------------------------------------------------

def start_gunicorn(workers=2, port=8001):
    """Start a gunicorn instance using gunicorn.conf.py; return (process, url).

    workers defaults to 2 to match the production Render Starter config.
    --workers and --bind CLI args override values in the config file.
    """
    venv_gunicorn = os.path.join(
        os.path.dirname(sys.executable),
        'gunicorn.exe' if sys.platform == 'win32' else 'gunicorn')
    gunicorn_exe = venv_gunicorn if os.path.exists(venv_gunicorn) else 'gunicorn'

    conf = os.path.join(ROOT, 'gunicorn.conf.py')
    cmd = [
        gunicorn_exe,
        f'--workers={workers}',
        f'--bind=127.0.0.1:{port}',
        'app:app',
    ]
    if os.path.exists(conf):
        cmd.insert(1, f'--config={conf}')

    env = os.environ.copy()
    env['PYTHONPATH'] = ROOT
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)   # give gunicorn time to fork workers and bind
    return proc, f'http://127.0.0.1:{port}'


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url',      default='http://127.0.0.1:5000',
                        help='Base URL of a running server (default: http://127.0.0.1:5000)')
    parser.add_argument('--gunicorn', action='store_true',
                        help='Also run against a multi-worker gunicorn instance on port 8001')
    parser.add_argument('--workers',  type=int, default=2,
                        help='Gunicorn worker count (default: 2, matching production)')
    parser.add_argument('--heavy',    action='store_true',
                        help='Also run worst-case payload scenarios (large pulley + all features)')
    parser.add_argument('--verbose',  action='store_true',
                        help='Print individual request times')
    parser.add_argument('--csv',      metavar='FILE', default=None,
                        help='Append concurrency results to this CSV file '
                             '(e.g. Perf_History.csv)')
    args = parser.parse_args()

    print(f'\n{"="*78}')
    print(f' DEV SERVER  ({args.url})')
    print(f'{"="*78}')
    run_tests(args.url, verbose=args.verbose, csv_file=args.csv)
    if args.heavy:
        print(f'\n{"="*78}')
        print(f' DEV SERVER — HEAVY PAYLOADS  ({args.url})')
        print(f'{"="*78}')
        run_tests(args.url, verbose=args.verbose, csv_file=args.csv,
                  endpoints=HEAVY_ENDPOINTS)

    if args.gunicorn:
        print(f'\n{"="*78}')
        print(f' GUNICORN ({args.workers} workers, gunicorn.conf.py)  —  starting...')
        print(f'{"="*78}')
        proc, gurl = start_gunicorn(workers=args.workers)
        try:
            run_tests(gurl, verbose=args.verbose, csv_file=args.csv)
            if args.heavy:
                print(f'\n{"="*78}')
                print(f' GUNICORN — HEAVY PAYLOADS')
                print(f'{"="*78}')
                run_tests(gurl, verbose=args.verbose, csv_file=args.csv,
                          endpoints=HEAVY_ENDPOINTS)
        finally:
            proc.terminate()
            proc.wait()
            print('gunicorn stopped.')


if __name__ == '__main__':
    main()
