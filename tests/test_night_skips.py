"""A skip fails the night unless conftest.NIGHT_SKIPS says why the night can't run it
(Controller, 2026-10-25: Bevel's STEP checks skipped every night unseen). Run on a tiny
suite of its own, with this repo's hook, by day and as the night sets it (PULLEY_NIGHTLY)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = '''
import pytest

def test_unknown_skip():
    pytest.skip("small_step not found")

@pytest.mark.skipif(True, reason="needs CCT_TEST_POSTGRES")
def test_listed_skip():
    pass

@pytest.mark.xfail(reason="a known fault")
def test_xfail():
    assert False
'''


def _run(tmp_path, night):
    # this repo's conftest by its path (the probe's own is the module named conftest)
    (tmp_path / 'conftest.py').write_text(
        f'import importlib.util, sys\nsys.path[:0] = [{str(ROOT)!r}, {str(ROOT / "tests")!r}]\n'
        f'spec = importlib.util.spec_from_file_location("pulley_conftest", {str(ROOT / "tests" / "conftest.py")!r})\n'
        'repo = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(repo)\n'
        'pytest_runtest_makereport = repo.pytest_runtest_makereport\n', encoding='utf-8')
    (tmp_path / 'test_probe.py').write_text(PROBE, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'PULLEY_NIGHTLY'}
    if night:
        env['PULLEY_NIGHTLY'] = '1'
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:xdist',
                           '-rfs', str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True)


def test_by_day_a_skip_is_a_skip(tmp_path):
    r = _run(tmp_path, night=False)
    assert r.returncode == 0, r.stdout
    assert '2 skipped, 1 xfailed' in r.stdout, r.stdout


def test_at_night_an_unlisted_skip_fails(tmp_path):
    r = _run(tmp_path, night=True)
    assert r.returncode == 1, r.stdout
    assert '1 failed, 1 skipped, 1 xfailed' in r.stdout, r.stdout
    assert 'skipped at night (small_step not found)' in r.stdout, r.stdout
