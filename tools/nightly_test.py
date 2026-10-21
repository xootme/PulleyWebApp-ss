"""
nightly_test.py — the full pre-release run, for CCT_Controller's nightly.py (nightly.json "test").

nightly.py runs it inside a test turn with cwd = a clean worktree of HEAD:

    .venv314/Scripts/python.exe tools/nightly_test.py --keep-logs <repo> [pytest args]

It runs tests/suite.py (pytest-xdist on CCT_TEST_CPUS workers) with the interpreter and
small_step binary the day runs use, plus the nightly random designs (PULLEY_NIGHTLY=1,
test_nightly_random.py). The random designs' repro files are written under the worktree,
which nightly.py removes afterwards, so --keep-logs copies them to <repo>/logs/nightly_random.
Exits with pytest's code.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The OCP/cadquery interpreter the STEP checks need (.venv314 has neither).
TEST_PY = r'C:\Users\cmyer\Documents\PulleyWebApp\.venv312\Scripts\python.exe'
# The Windows small_step build the STEP tests run (the app's bin/ holds only the Linux one).
SMALL_STEP = r'C:\Users\cmyer\Documents\small_step\target\x86_64-pc-windows-gnu\release\small_step.exe'


def main(args):
    keep = None
    if '--keep-logs' in args:
        i = args.index('--keep-logs')
        keep = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    test_py = os.environ.get('PULLEY_TEST_PY', TEST_PY)
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
