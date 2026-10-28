"""
conftest.py — shared fixtures and test-matrix helpers.

The suite runs side by side under pytest-xdist, sized from the machine's
share (tests/cpu_cap.py), by tests/suite.py inside a test turn.
"""
import os
import tempfile

import pytest

from cpu_cap import test_cpus

# The app's logs folder (bug reports, download counts, metrics, results,
# bundles, the failure log): each run and each xdist worker gets its own, set
# before any test imports app — workers writing one folder would race, and
# tests have no business writing the repo's logs/ anyway.
if not os.environ.get('PULLEY_LOG_DIR'):
    os.environ['PULLEY_LOG_DIR'] = tempfile.mkdtemp(
        prefix=f"pulley-tests-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-")


def pytest_xdist_auto_num_workers(config):
    """`-n auto` is the machine's share, never every CPU."""
    return test_cpus()


# The night runs all it can: under PULLEY_NIGHTLY (tools/nightly_test.py sets it, so a test
# host has it too) a skip fails, unless its reason is one the night can't help yet, each with
# what would end it. Bevel's 56 STEP checks skipped every night from 10-21 unseen (Controller,
# 2026-10-25).
NIGHT_SKIPS = {
    'the session queue is compiled out (app.SESSION_QUEUE)': 'never: the queue is not in the app',
    'needs CCT_TEST_POSTGRES': 'a test Postgres on the night host, CCT_TEST_POSTGRES set there',
    'CCT_TEST_POSTGRES not set': 'a test Postgres on the night host, CCT_TEST_POSTGRES set there',
    'needs os.fork (Linux; run in WSL)': 'a Linux night host: test_fork_safe.py and tools/fork_probe.py run in WSL by hand',
}
# (test_cct_theme's checks run at night too: msi's clone, CCT_COMMON_CHECKOUT in remote.json; it
# needs refreshing when the vendored commit moves, which Controller does on being told of a sync)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    if os.environ.get('PULLEY_NIGHTLY') != '1' or not rep.skipped or hasattr(rep, 'wasxfail'):
        return
    reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
    reason = reason.removeprefix('Skipped: ')
    if reason not in NIGHT_SKIPS:
        rep.outcome = 'failed'
        rep.longrepr = (f'skipped at night ({reason}): the night must run it, or conftest.NIGHT_SKIPS '
                        'must say why it can\'t')

from geometry.pulley_geometry import (
    PULLEY_SPECS, PROFILE_KEY_PREFIX, BELT_FAMILIES,
    H_BELT_SPECS, S_BELT_SPECS, G_BELT_SPECS, R_BELT_SPECS,
    T_BELT_SPECS, AT_BELT_SPECS, IMPERIAL_BELT_SPECS,
)

# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------
@pytest.fixture(scope='session')
def client():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


# Clear queue state before tests
@pytest.fixture(scope='session', autouse=True)
def clear_queue_state():
    """Clear all queue and session state before running tests."""
    # The import was outside the try, so an environment without `requests`
    # could not collect ANY test -- the reset is best-effort by design and
    # the import should be too.
    try:
        import requests
        requests.post('http://localhost:5001/api/test/reset', timeout=5)
    except Exception:
        pass  # Server may not be running, or requests may not be installed
    yield


# ---------------------------------------------------------------------------
# Test matrix
# ---------------------------------------------------------------------------
_FAMILY_PITCHES = {
    'HTD':      ['3M', '5M', '8M', '14M', '20M'],
    'GT':       ['2M', '3M', '5M', '8M', '14M'],
    'STD':      ['2M', '3M', '5M', '8M', '14M'],
    'T':        ['T2.5', 'T5', 'T10', 'T20'],
    'AT':       ['AT3', 'AT5', 'AT10', 'AT20'],
    'Imperial': ['MXL', 'XL', 'L', 'H', 'XH', 'XXH'],
    'RPP':      ['3M', '5M', '8M', '14M', '20M'],
}

def _key(family, pitch):
    return PROFILE_KEY_PREFIX.get(family, '') + pitch

def _has_pulley_spec(family, pitch):
    return _key(family, pitch) in PULLEY_SPECS

def _has_belt_spec(family, pitch):
    checks = {
        'HTD':      lambda p: ('H' + p) in H_BELT_SPECS,
        'GT':       lambda p: ('G' + p) in G_BELT_SPECS,
        'STD':      lambda p: ('S' + p) in S_BELT_SPECS,
        'RPP':      lambda p: ('R' + p) in R_BELT_SPECS,
        'T':        lambda p: p in T_BELT_SPECS,
        'AT':       lambda p: p in AT_BELT_SPECS,
        'Imperial': lambda p: p in IMPERIAL_BELT_SPECS,
    }
    return checks.get(family, lambda p: False)(pitch)

# Parametrize lists built once at import time
PULLEY_CASES = [
    pytest.param(family, pitch, id=f'{family}-{pitch}')
    for family, pitches in _FAMILY_PITCHES.items()
    for pitch in pitches
    if _has_pulley_spec(family, pitch)
]

BELT_CASES = [
    pytest.param(family, pitch, id=f'{family}-{pitch}')
    for family, pitches in _FAMILY_PITCHES.items()
    for pitch in pitches
    if _has_pulley_spec(family, pitch) and _has_belt_spec(family, pitch)
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
BORE_MM = 8.0
CLEARANCE_PRESETS = ['TIGHT', 'STANDARD', 'LOOSE']
BACKLASH_PRESETS  = ['NONE', 'TIGHT', 'STANDARD', 'LOOSE']
PRINT_EXTRA_VALS  = [0.0, 0.1]

def get_spec(family, pitch):
    return PULLEY_SPECS[_key(family, pitch)]

def std_cl(spec):
    return spec['clearances']['STANDARD']

def std_bl(spec):
    return spec.get('backlash', {}).get('STANDARD', 0.0)

def preset_val(spec, kind, preset):
    if preset == 'NONE':
        return 0.0
    table = spec['clearances'] if kind == 'clearance' else spec.get('backlash', {})
    return table.get(preset, 0.0)
