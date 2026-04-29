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

    # Verbose: print every individual request time:
    .venv312/Scripts/python concurrency_test.py --verbose

Interpreting results
--------------------
Serialisation ratio = mean_concurrent / mean_baseline
  ~1.0  → requests ran in parallel (good)
  ~N    → fully serialised (GIL or single worker)
  1–N   → partial parallelism (some workers free, some blocked)

A ratio > 1.5 at concurrency=2 is flagged as a potential blocking issue.
"""

import argparse
import io
import os
import statistics
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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
# Each entry: (label, path, params)
# Ordered roughly fastest → slowest so the report reads naturally.

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

ENDPOINTS = [
    ('SVG  (fast / pure-Python)',    '/download/svg',        BARE_PARAMS),
    ('DXF  (medium / ezdxf)',        '/download/dxf',        BARE_PARAMS),
    ('SVG  with spokes',             '/download/svg',        SPOKE_PARAMS),
    ('Preview STL  bare',            '/api/preview-stl',     BARE_PARAMS),
    ('Preview STL  with spokes',     '/api/preview-stl',     SPOKE_PARAMS),
    ('Download STL  bare',           '/download/stl',        BARE_PARAMS),
    ('Download STL  with spokes',    '/download/stl',        SPOKE_PARAMS),
    ('Flange STL  3D-print top',     '/download/flange-stl',
     {**FLANGE_PARAMS, 'which': 'top'}),
]

CONCURRENCY_LEVELS = [1, 2, 4, 8]
WARMUP_REQUESTS   = 2   # discard these before measuring baseline


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

    times   = [r[0]   for r in results if r]
    statuses = [r[1]  for r in results if r]
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
# Main test runner
# ---------------------------------------------------------------------------

def run_tests(base_url, verbose=False):
    session = _requests.Session()

    all_issues = []

    print(f'\n{BOLD}Concurrency test harness — {base_url}{RESET}')
    print('=' * 78)

    for label, path, params in ENDPOINTS:
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

        print(f'  baseline (N=1 × 5): mean={baseline_mean*1000:.1f}ms  '
              f'p95={baseline_p95*1000:.1f}ms')

        # Concurrency sweep
        for n in [c for c in CONCURRENCY_LEVELS if c > 1]:
            times, statuses, wall = _run_concurrent(
                session, base_url, path, params, n)

            errors  = [s for s in statuses if s != 200]
            ok_times = [t for t, s in zip(times, statuses) if s == 200]

            if not ok_times:
                print(f'  N={n}: ALL REQUESTS FAILED — {set(errors)}')
                continue

            mean_t = statistics.mean(ok_times)
            max_t  = max(ok_times)
            p95_t  = _pct(ok_times, 95)
            ratio  = mean_t / baseline_mean
            colour = _colour(ratio)

            err_str = f'  {len(errors)} error(s)' if errors else ''
            if verbose:
                detail = '  [' + '  '.join(f'{t*1000:.0f}ms' for t in ok_times) + ']'
            else:
                detail = ''

            print(f'  N={n}: mean={mean_t*1000:.1f}ms  max={max_t*1000:.1f}ms  '
                  f'wall={wall*1000:.1f}ms  '
                  f'ratio={colour}{ratio:.2f}x  {_ratio_label(ratio)}{RESET}'
                  f'{err_str}{detail}')

            if ratio > 1.5:
                all_issues.append((label, n, ratio))

    # Summary
    print('\n' + '=' * 78)
    if all_issues:
        print(f'{BOLD}{RED}Potential blocking issues detected:{RESET}')
        for label, n, ratio in all_issues:
            print(f'  {RED}[!!]{RESET}  {label}  at N={n}: {ratio:.2f}x baseline '
                  f'(expected ~1.0 for true parallelism)')
        print()
        print('Interpretation:')
        print('  ratio ≈ N   → GIL holding the thread or single gunicorn worker')
        print('  ratio 1–N   → some workers free; other requests queuing')
        print('  ratio ≈ 1   → requests running concurrently')
    else:
        print(f'{GREEN}{BOLD}No blocking issues detected at tested concurrency levels.[OK]{RESET}')

    print()


# ---------------------------------------------------------------------------
# Optional gunicorn launcher
# ---------------------------------------------------------------------------

def start_gunicorn(workers=4, port=8001):
    """Start a gunicorn instance; return (process, url)."""
    cmd = [
        sys.executable.replace('python.exe', 'gunicorn.exe')
        if 'python.exe' in sys.executable else 'gunicorn',
        f'--workers={workers}',
        f'--bind=127.0.0.1:{port}',
        '--timeout=120',
        'app:app',
    ]
    # Try venv gunicorn first
    import os
    venv_gunicorn = os.path.join(
        os.path.dirname(sys.executable), 'gunicorn.exe'
        if sys.platform == 'win32' else 'gunicorn')
    if os.path.exists(venv_gunicorn):
        cmd[0] = venv_gunicorn

    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.abspath(__file__)), env=env)
    time.sleep(4)   # give gunicorn time to bind
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
                        help='Also run against a 4-worker gunicorn instance on port 8001')
    parser.add_argument('--workers',  type=int, default=4,
                        help='Gunicorn worker count (default: 4)')
    parser.add_argument('--verbose',  action='store_true',
                        help='Print individual request times')
    args = parser.parse_args()

    # Test the target server (dev or user-specified)
    print(f'\n{"="*78}')
    print(f' DEV SERVER  ({args.url})')
    print(f'{"="*78}')
    run_tests(args.url, verbose=args.verbose)

    # Optionally test gunicorn
    if args.gunicorn:
        print(f'\n{"="*78}')
        print(f' GUNICORN ({args.workers} workers)  —  starting…')
        print(f'{"="*78}')
        proc, gurl = start_gunicorn(workers=args.workers)
        try:
            run_tests(gurl, verbose=args.verbose)
        finally:
            proc.terminate()
            proc.wait()
            print('gunicorn stopped.')


if __name__ == '__main__':
    main()
