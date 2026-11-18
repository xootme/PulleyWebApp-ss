"""The Windows small_step the tests, fuzzers, night run and dev server use is the pinned one
(exporters/small_step_pin.py; campaign 2026-11-17-small-step-windows-pin): SmallStep's versioned
exe, by path and sha256, as production's bin/small_step_linux is pinned by its commit. Before it
they ran the shared exe SmallStep replaces at each build. A bump is the one PIN line."""
import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

from exporters import small_step_pin as pin  # noqa: E402


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_the_pin_is_a_version_and_a_sha256_in_its_folder():
    assert re.fullmatch(r'\d+\.\d+\.\d+', pin.VERSION) and re.fullmatch(r'[0-9a-f]{64}', pin.SHA256)
    assert Path(pin.EXE) == pin.WINDOWS_DIR / pin.VERSION / 'small_step.exe'


@pytest.mark.skipif(not Path(pin.EXE).is_file(), reason='SmallStep\'s target/windows is not on this host')
def test_the_pinned_exe_is_the_pinned_bytes():
    folder = Path(pin.EXE).parent
    assert _sha(pin.EXE) == pin.SHA256
    assert (folder / 'small_step.exe.sha256').read_text(encoding='utf-8').split()[0].lower() == pin.SHA256
    assert pin.VERSION in (folder / 'version.txt').read_text(encoding='utf-8')


def test_the_night_the_harnesses_and_the_fuzzer_default_to_the_pin():
    import nightly_test
    assert nightly_test.SMALL_STEP == pin.EXE
    src = (ROOT / 'fuzz_pulley.py').read_text(encoding='utf-8')
    assert 'from exporters.small_step_pin import EXE as _SS_PINNED' in src
    assert '_SS_BIN_DEFAULT = Path(_SS_PINNED)' in src
    assert 'x86_64-pc-windows-gnu' not in (ROOT / 'tools' / 'nightly_test.py').read_text(encoding='utf-8')


def test_the_dev_server_defaults_to_the_pin():
    src = (ROOT / 'app.py').read_text(encoding='utf-8')
    assert "_SS_BIN = os.environ.get('SMALL_STEP_BIN', _SS_PINNED)" in src


@pytest.mark.skipif(not os.environ.get('SMALL_STEP_BIN'), reason='no SMALL_STEP_BIN for this run')
def test_this_run_tests_the_pinned_bytes():
    """Where a run names its own copy (a test host's, from remote.json), it holds the pinned bytes."""
    p = os.environ['SMALL_STEP_BIN']
    if not p.lower().endswith('.exe') or not Path(p).is_file():
        pytest.skip(f'not a Windows exe here: {p}')
    assert _sha(p) == pin.SHA256, f'{p} is not small_step {pin.VERSION} ({pin.SHA256[:12]}...)'
