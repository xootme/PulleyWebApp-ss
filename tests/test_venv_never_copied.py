"""A lane's copy of the tree (cct_common's copy_tree, tools/hand_control.py) is what
`git ls-files -co --exclude-standard` lists, so it holds every file git doesn't ignore. A venv
made by Python 3.12's `-m venv` writes no .gitignore of its own: on msi the lanes got 15,663 of
.venv312's files without their .pyd ones, the cadquery worker ran that broken python, numpy's
import failed and STEP answered 400 (the 2026-11-17 gate). The repo's own rule ignores every
.venv* folder, whatever wrote it."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize('path', ['.venv312/Lib/site-packages/numpy/__init__.py', '.venv314/Scripts/python.exe',
                                  '.venv/pyvenv.cfg', '.venv399/pyvenv.cfg'])
def test_the_repo_ignores_every_venv_folder(path):
    """By the repo's rules alone (--no-index: the path need not exist, and a venv's own .gitignore can't help)."""
    r = _git('check-ignore', '--no-index', '-q', path)
    assert r.returncode == 0, f'{path} is not ignored by the repo: {r.stderr}'


def test_no_venv_file_reaches_a_lane():
    r = _git('ls-files', '-co', '--exclude-standard')
    assert r.returncode == 0, r.stderr
    venv = [p for p in r.stdout.splitlines() if p.split('/', 1)[0].startswith('.venv')]
    assert not venv, f'{len(venv)} venv file(s) would be copied, e.g. {venv[:3]}'
