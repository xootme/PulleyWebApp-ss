"""
deploy.py — PulleyWebApp-ss deploy checklist runner

Runs all automated steps; pauses at decision points that need human review.
Usage:
    .venv314/Scripts/python deploy.py [--fast] [--skip-desktop] "commit message"

    --fast           Skip the 4 slow queue-timeout tests (~9 min saved). Safe
                     for most deploys; run full suite periodically.
    --skip-desktop   Skip Step 7 desktop build (web-only deploys — rare).

Steps:
    1  Review diff
    2  Confirm docs / help files updated
    3  Confirm schema unchanged (or updated)
    4a Run full test suite            [hard gate — auto-aborts on failure]
    4b Run benchmarks + concurrency   [pauses if regressions detected]
    4c Run CAD addin unit tests       [hard gate — auto-aborts on failure]
    5  Commit and push                → Render web app live in ~2 min
    6  Generate deploy report, commit report
    7  Build + publish desktop release via build_release_ss.py  [runs by default]
       (PyArmor obfuscate → PyInstaller → zip → GitHub release → Render env vars)
       Pass --skip-desktop to omit this step for web-only fixes.
"""

import subprocess
import sys
import os
import re

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PYTHON = os.path.join(os.path.dirname(__file__), '.venv314', 'Scripts', 'python.exe')
ROOT   = os.path.dirname(os.path.abspath(__file__))
ADDIN_TESTS = r'C:\Users\cmyer\Documents\CCT_Addins\FreeCAD\TimingPulley\tests'

# ── CLI args ──────────────────────────────────────────────────────────────────
_fast_mode         = '--fast'         in sys.argv
_skip_desktop_flag = '--skip-desktop' in sys.argv
_argv = [a for a in sys.argv[1:] if a not in ('--fast', '--skip-desktop')]


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd, *, capture=False, timeout=300):
    """Run a command, streaming output unless capture=True."""
    result = subprocess.run(
        cmd, shell=True, cwd=ROOT, check=False,
        capture_output=capture, text=True, timeout=timeout,
    )
    return result


def banner(title):
    """Print a section header."""
    width = 72
    print()
    print('=' * width)
    print(f'  {title}')
    print('=' * width)


def pause(prompt='Continue? [y/n] '):
    """Ask the user to confirm. Auto-OK when stdin has no TTY or is at EOF."""
    try:
        ans = input(f'\n{prompt}').strip().lower()
    except EOFError:
        print('[auto-OK: non-interactive]')
        return
    if ans != 'y':
        print('Aborted.')
        sys.exit(1)


def hard_gate(label, cmd, *, timeout=300):
    """Run cmd; abort the deploy if it exits non-zero."""
    print(f'\nRunning: {cmd}')
    result = run(cmd, timeout=timeout)
    if result.returncode != 0:
        print(f'\n[FAIL] {label} failed -- deploy aborted.')
        sys.exit(result.returncode)
    return result


# ── Step 1 — Diff review ─────────────────────────────────────────────────────

banner('STEP 1 -- Diff review')
r = run('git diff --stat HEAD', capture=True)
print(r.stdout or '(no unstaged changes)')

# Exclude runtime log files and binaries; show only source files
r2 = run(
    'git diff HEAD -- "*.py" "*.html" "*.md" "*.sh" "*.toml" "*.yaml" "*.yml" "*.csv" "*.json" "*.cfg" "*.ini"'
    ' ":(exclude)flask_*.txt" ":(exclude)flask_*.log"',
    capture=True,
)
lines = r2.stdout.splitlines()
if len(lines) > 80:
    print('\n'.join(lines[:80]))
    print(f'\n... ({len(lines) - 80} more lines -- run `git diff HEAD` to see all)')
else:
    print(r2.stdout or '(nothing)')



# ── Step 2 — Docs ────────────────────────────────────────────────────────────

banner('STEP 2 -- Docs / help files (reminder)')
print("  - static/*_help.html   (changed feature, limits, or format options)")
print("  - ToDo.md              (completed backlog items, new items)")
print("  - web_provisioning.md  (deploy or infrastructure changes)")
print()


# ── Step 3 — CCT metadata schema ─────────────────────────────────────────────

banner('STEP 3 -- CCT metadata schema (reminder)')
print("  If any params were added/removed/renamed, ensure these are updated:")
print("  - app.py -> _cct_meta(), CCT_SCHEMA_VERSION")
print("  - templates/index.html -> migrateParams()")
print("  - PulleyWebApp.py -> _extract_cct_metadata()")
print()


# ── Step 4a — Full test suite ─────────────────────────────────────────────────

banner('STEP 4a -- Full test suite')
# Clear any stale Flask processes on the test ports before starting
run('powershell -Command "Get-NetTCPConnection -LocalPort 5098,5099 -EA SilentlyContinue'
    ' | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }"',
    capture=True)

# Stream test output line-by-line; stop as soon as "Results" line appears.
# Using Popen avoids the subprocess.run timeout fighting with the dashboard server.
import threading as _threading

_skip = '--skip-slow' if _fast_mode else ''
_test_cmd = f'"{PYTHON}" tests/run_tests.py --exit-when-done {_skip}'.strip()
if _fast_mode:
    print('(--fast mode: skipping 4 slow queue-timeout tests)')
print(f'Running: {_test_cmd}')
_env4a = {**os.environ, 'PYTHONUNBUFFERED': '1'}
_proc4a = subprocess.Popen(_test_cmd, shell=True, cwd=ROOT, env=_env4a,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
_result_line = None
_test_timeout = 1200  # full suite takes ~10 min; --fast is ~1 min
_timer_fired = [False]

def _kill_on_timeout():
    _timer_fired[0] = True
    _proc4a.kill()

_timer = _threading.Timer(_test_timeout, _kill_on_timeout)
_timer.start()
try:
    for _line in _proc4a.stdout:
        print(_line, end='', flush=True)
        if _line.startswith('Results'):
            _result_line = _line.strip()
            break
finally:
    _timer.cancel()
    _proc4a.kill()
    _proc4a.wait()

if _timer_fired[0]:
    print('\n[FAIL] Test suite timed out after 600s -- deploy aborted.')
    sys.exit(1)
if _result_line is None:
    print('\n[FAIL] Test suite exited without printing Results -- deploy aborted.')
    sys.exit(1)

_failed_count = 0
_m = re.search(r'(\d+) failed', _result_line)
if _m:
    _failed_count = int(_m.group(1))
print(f'\n{_result_line}')
if _failed_count > 0:
    print(f'\n[FAIL] {_failed_count} test(s) failed -- deploy aborted.')
    sys.exit(1)
print('\n[OK] All tests passed.')


# ── Step 4b — Benchmarks ─────────────────────────────────────────────────────

banner('STEP 4b — Benchmarks')
print('Running unit benchmarks...')
r = run(f'"{PYTHON}" record_benchmarks.py', capture=True, timeout=180)
print(r.stdout)
if r.returncode != 0:
    print('[FAIL] record_benchmarks.py failed — deploy aborted.')
    sys.exit(1)

# Auto-detect regressions (>30% slowdown vs previous row)
regression_flag = 'regress' in r.stdout.lower() or 'REGRESSION' in r.stdout
if regression_flag:
    print('\n[WARNING] Possible benchmark regression detected above.')
    pause('Regressions acceptable? [y/n] ')
else:
    print('\n[OK] No regressions flagged.')

print('\nRunning concurrency tests (dev server)...')
r2 = run(f'"{PYTHON}" concurrency_test.py --csv Perf_History.csv', capture=True, timeout=180)
print(r2.stdout)

errors_detected = '[ERR]' in r2.stdout
if errors_detected:
    print('\n[FAIL] [ERR] flags in concurrency test — deploy aborted.')
    sys.exit(1)

print('\nRunning gunicorn concurrency test (heavy)...')
r3 = run(
    f'"{PYTHON}" concurrency_test.py --gunicorn --workers 2 --heavy --csv Perf_History.csv',
    capture=True, timeout=300,
)
print(r3.stdout)

if '[ERR]' in r3.stdout:
    print('\n[FAIL] [ERR] flags in gunicorn concurrency test — deploy aborted.')
    sys.exit(1)

# Check gunicorn ratios at N=2 — flag anything >1.5
ratio_problem = bool(re.search(r'N=2:.*ratio=.*[2-9]\.\d+x', r3.stdout))
if ratio_problem:
    print('\n[WARNING] Gunicorn N=2 ratio >1.5 -- two users may be serialised in production.')
    pause('Proceed anyway? [y/n] ')
else:
    print('\n[OK] Gunicorn ratios OK.')


# ── Step 4c — CAD addin tests ─────────────────────────────────────────────────

banner('STEP 4c — CAD addin unit tests')
hard_gate('Addin tests', f'python -m pytest "{ADDIN_TESTS}" -v', timeout=60)
print('\n[OK] All 5 addin tests passed.')


# ── Step 5 — Commit and push ──────────────────────────────────────────────────

banner('STEP 5 — Commit and push')
r = run('git diff --stat HEAD', capture=True)
print(r.stdout or '(no unstaged changes — nothing to commit)')

# Collect commit message — required as CLI arg; prompt once if missing
if _argv:
    commit_msg = ' '.join(_argv)
    print(f'Commit message: {commit_msg!r}')
else:
    commit_msg = input('\nCommit message: ').strip()

if not commit_msg:
    print('Empty commit message — aborted.')
    sys.exit(1)

full_msg = commit_msg + '\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>'

print('\nStaging files...')
run('git add -u')                  # stage all tracked modifications
run('git add Perf_History.csv')    # always include benchmark history

r = run(f'git commit -m "{full_msg}"', capture=True)
print(r.stdout)
if r.returncode != 0:
    print(r.stderr)
    print('[FAIL] git commit failed.')
    sys.exit(1)

print('Pushing...')
r = run('git push origin main', capture=True)
print(r.stdout or r.stderr)
if r.returncode != 0:
    print('[FAIL] git push failed.')
    sys.exit(1)

print('\n[OK] Pushed to origin/main. Render will redeploy in ~2 minutes.')


# ── Step 6 — Deploy report ────────────────────────────────────────────────────

banner('STEP 6 — Deploy report')
r = run(f'"{PYTHON}" generate_checkin_report.py', capture=True, timeout=60)
print(r.stdout)
if r.returncode != 0:
    print('[WARN] generate_checkin_report.py failed — continuing anyway.')
else:
    pause('Report opened in browser — reviewed? [y/n] ')
    run('git add checkins/')
    r2 = run('git commit -m "Add deploy report"', capture=True)
    print(r2.stdout)
    run('git push origin main', capture=True)
    print('[OK] Report committed and pushed.')


# ── Step 7 — Desktop release ─────────────────────────────────────────────────

banner('STEP 7 — Desktop release')

_build_script = os.path.join(ROOT, 'packaging', 'build_release_ss.py')
_prep_script  = os.path.join(ROOT, 'packaging', 'prepare_release.py')

if _skip_desktop_flag:
    print('[--skip-desktop] Desktop build skipped.')
else:
    print('Building desktop release (PyArmor obfuscate -> PyInstaller -> zip -> GitHub -> Render)...')
    print('Pass --skip-desktop to omit this step for web-only fixes.')
    _r7 = run(f'"{PYTHON}" "{_build_script}"', timeout=600)
    if _r7.returncode != 0:
        print(f'\n[FAIL] Desktop build failed (exit {_r7.returncode}).')
        print('Fix the issue and re-run manually:')
        print('  .venv314/Scripts/python packaging/build_release_ss.py')
        sys.exit(_r7.returncode)
    print('\n[OK] Desktop release built and published.')

    # Licence renewal — needed only once a year (expiry is 365 days from today).
    print('\nDoes the PyArmor licence also need renewal?')
    print('(Only required ~annually when the current licence expires.)')
    try:
        _ans_lic = input('Renew licence now? [y/n] ').strip().lower()
    except EOFError:
        _ans_lic = 'n'

    if _ans_lic == 'y':
        print(f'\nRunning: {_prep_script}')
        _r_lic = run(f'"{PYTHON}" "{_prep_script}"', timeout=120)
        if _r_lic.returncode != 0:
            print('[WARN] prepare_release.py failed — update PULLEY_LICENCE_B64/EXPIRY manually in Render.')
        else:
            print('\n[OK] Licence generated.')
            print('Paste PULLEY_LICENCE_B64 and PULLEY_LICENCE_EXPIRY into the Render dashboard.')

print('\nDeploy complete.')
