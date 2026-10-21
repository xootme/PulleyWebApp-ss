"""
nightly_test.py — the full pre-release run, for CCT_Controller's nightly.py (nightly.json "test").

nightly.py runs it inside a test turn with cwd = a clean worktree of HEAD, with the repo's
.venv312 (so a test host's night copy builds that venv: remote.py rebuilds the command's):

    .venv312/Scripts/python.exe tools/nightly_test.py --keep-logs <repo> [pytest args]

It runs tests/suite.py (pytest-xdist on CCT_TEST_CPUS workers) with the interpreter and
small_step binary the day runs use, plus the nightly random designs (PULLEY_NIGHTLY=1,
test_nightly_random.py), then every negative control (tools/neg_controls.py --all), scoped
by what the suite just recorded (pytest args narrow the suite and skip the controls). The
random designs' repro files are written under the worktree,
which nightly.py removes afterwards, so --keep-logs copies them to <repo>/logs/nightly_random.
Exits non-zero when the suite or a control fails.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The suite's interpreter: Python 3.12 with OCP and cadquery (.venv314 has neither) and the
# app's packages. The repo's own .venv312 (2026-10-21; a test host rebuilds it from its
# pip freeze), else the abandoned repo's, which the day runs used before.
TEST_PYS = [ROOT / '.venv312' / 'Scripts' / 'python.exe',
            Path(r'C:\Users\cmyer\Documents\PulleyWebApp\.venv312\Scripts\python.exe')]
# The Windows small_step build the STEP tests run (the app's bin/ holds only the Linux one).
SMALL_STEP = r'C:\Users\cmyer\Documents\small_step\target\x86_64-pc-windows-gnu\release\small_step.exe'


def main(args):
    keep = None
    if '--keep-logs' in args:
        i = args.index('--keep-logs')
        keep = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    # the nightly worktree has no venv: the repo's (--keep-logs names it) comes next
    found = [p for p in [*TEST_PYS[:1], *([keep / '.venv312' / 'Scripts' / 'python.exe'] if keep else []),
                         *TEST_PYS[1:]] if p.is_file()]
    test_py = os.environ.get('PULLEY_TEST_PY') or str(found[0] if found else TEST_PYS[-1])
    env = {**os.environ, 'QUEUE_DISABLED': '1', 'PULLEY_NIGHTLY': '1'}
    env.setdefault('SMALL_STEP_BIN', SMALL_STEP)
    for what, p in (('test interpreter', test_py), ('SMALL_STEP_BIN', env['SMALL_STEP_BIN'])):
        if not Path(p).is_file():
            print(f'nightly_test: {what} not found: {p}', flush=True)
            return 2
    ver = subprocess.run([env['SMALL_STEP_BIN'], '--version'], capture_output=True, text=True)
    print(f"nightly_test: {ROOT} at {_git('rev-parse', '--short', 'HEAD')}; "
          f"{ver.stdout.strip() or 'small_step ?'} ({env['SMALL_STEP_BIN']})", flush=True)
    rc = subprocess.call([test_py, str(ROOT / 'tests' / 'suite.py'), '-p', 'no:warnings', '-rfEX', *args],
                         cwd=ROOT, env=env)
    if not args:              # the whole set: every negative control too, scoped by the record just made
        print('nightly_test: negative controls (tools/neg_controls.py --all)', flush=True)
        rc = subprocess.call([test_py, str(ROOT / 'tools' / 'neg_controls.py'), '--all'], cwd=ROOT, env=env) or rc
    if keep:
        src, dst = ROOT / 'logs' / 'nightly_random', keep / 'logs' / 'nightly_random'
        for f in sorted(src.glob('*.json')) if src.is_dir() else []:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.name)
            print(f'nightly_test: random designs kept in {dst / f.name}', flush=True)
    return rc


def _git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True).stdout.strip()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
