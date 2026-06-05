"""
conftest.py — shared fixtures and test-matrix helpers.
"""
import pytest

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
    import requests
    try:
        requests.post('http://localhost:5001/api/test/reset', timeout=5)
    except:
        pass  # Server may not be running for unit tests
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
