"""test_step_verifiers.py — the STEP files read cleanly in two readers that
aren't OCCT's own checks: NIST's STEP File Analyzer (built on IFCsvr, the one
parser independent of OCCT) and FreeCAD.

    pytest -m sfa -rs        pytest -m freecad -rs

CCT_Build_and_Deploy.md asks for both whenever STEP geometry changes. Until
2026-10-02 neither marker existed here, so `-m sfa` ran nothing (exit 5) and
looked like a pass (bug hunt section 9); SFA was only a script. A missing tool
or small_step build is a skip that says what was looked for, never a pass.
Each run is negative-controlled: a STEP with one entity misspelled must fail.
"""
import glob
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import validate_nist_sfa as sfa            # noqa: E402

_FC_SCRIPT = os.path.join(os.path.dirname(__file__), '_freecad_import.py')


def _freecadcmd():
    for c in [os.environ.get('FREECADCMD', '').strip()] + sorted(
            glob.glob(r'C:\Program Files\FreeCAD*\bin\freecadcmd.exe'), reverse=True):
        if c and os.path.isfile(c):
            return c
    return None


FREECADCMD = _freecadcmd()

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '30', 'bore': '8', 'belt_height': '10',
        'feature_build': '1'}
HUB = {'hub_od': '24', 'hub_height': '10', 'hub_screw_size': 'M3', 'hub_screw_count': '2',
       'hub_screw_hold': 'thread'}
# One design per kind of STEP geometry the app writes.
DESIGNS = {
    'round bore': {},
    'hub, two set screws': HUB,
    'hub, captured nuts': {**HUB, 'hub_screw_hold': 'nut'},
    'D-flat': {**HUB, 'hub_flat_depth': '0.8', 'hub_screw_count': '1'},
    'keyway': {**HUB, 'bore': '10', 'hub_keyway_w': '3', 'hub_keyway_h': '1.4', 'hub_screw_count': '1'},
    'spokes': {'teeth': '40', 'spokes_enabled': '1', 'spokes_hub_od': '18', 'spokes_rim_depth': '3',
               'spokes_width': '5', 'spokes_count': '5'},
    'metal flanges': {'flange_enabled': '1', 'flange_3dprint': '0', 'flange_rim_radius': '4.2',
                      'flange_plate_height': '1'},
    'printed flanges on spokes': {'teeth': '30', 'bore': '10', 'flange_enabled': '1', 'flange_3dprint': '1',
                                  'flange_angle': '15', 'flange_rim_radius': '4.8', 'flange_height': '1.5',
                                  'flange_top_separate': '0', 'flange_supports_enabled': '1',
                                  'spokes_enabled': '1', 'spokes_hub_od': '16', 'spokes_rim_depth': '3',
                                  'spokes_width': '6', 'spokes_count': '4', 'family': 'RPP'},
    'involute spline, rings': {'teeth': '40', 'bore_shape': 'spline', 'spline_type': 'involute',
                               'spline_m': '1.5', 'spline_z': '16', 'spline_pa': '30',
                               'spline_root': 'flat', 'spline_ring_top': '1', 'spline_ring_bottom': '1'},
}


@pytest.fixture(scope='module')
def steps(tmp_path_factory):
    """Every design's STEP, made by the app's small_step backend."""
    if not os.environ.get('SMALL_STEP_BIN'):
        pytest.skip('SMALL_STEP_BIN unset: no small_step build to make the STEP files with')
    pytest.importorskip('OCP')
    os.environ['QUEUE_DISABLED'] = '1'
    os.environ.pop('PULLEY_STEP_BACKEND', None)
    from app import app
    client = app.test_client()
    out, d = {}, tmp_path_factory.mktemp('steps')
    for name, q in DESIGNS.items():
        r = client.get('/download/step', query_string={**BASE, **q})
        assert r.status_code == 200, (name, r.data[:300])
        path = d / (name.replace(' ', '_').replace(',', '') + '.step')
        path.write_bytes(r.data)
        out[name] = str(path)
    return out


@pytest.fixture(scope='module')
def broken(steps, tmp_path_factory):
    """A STEP with one entity's name misspelled: every reader must refuse it."""
    text = open(steps['hub, two set screws'], encoding='latin-1').read()
    assert 'ADVANCED_FACE(' in text
    path = tmp_path_factory.mktemp('broken') / 'broken.step'
    path.write_text(text.replace('ADVANCED_FACE(', 'ADVANCED_FAEC(', 1), encoding='latin-1')
    return str(path)


def _need_sfa():
    if sfa.SFA_EXE is None:
        pytest.skip('NIST SFA (sfa-cl.exe) not found; looked in: ' + '; '.join(sfa.SFA_CANDIDATES))


@pytest.mark.sfa
@pytest.mark.parametrize('name', list(DESIGNS))
def test_sfa_reads_it_cleanly(steps, name):
    _need_sfa()
    ok, lines = sfa.validate_step(steps[name])
    assert ok, (name, lines)


@pytest.mark.sfa
def test_sfa_refuses_a_broken_file(broken):
    _need_sfa()
    ok, lines = sfa.validate_step(broken)
    assert not ok, lines


def _freecad(path):
    """(solids, volume) as FreeCAD reads the file, or None when it reads no solid."""
    r = subprocess.run([FREECADCMD, _FC_SCRIPT, path], capture_output=True, text=True, timeout=180)
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith('OK:') and r.returncode == 0:      # "OK: 2 solids, volume=18926.1 mm3"
            n, vol = line[3:].split(' solids, volume=')
            return int(n), float(vol.split()[0])
    return None


def _occt(path):
    """(solids, volume) as this app's own OCP check reads the file."""
    from test_step_small_step import _solids
    n, _ok, vol = _solids(open(path, 'rb').read())
    return n, vol


def _agrees(got, want):
    # FreeCAD says "OK" for a file it read only part of (2026-10-02: one entity
    # misspelled, it dropped a solid and still said OK), so the count and the
    # volume have to match, not just the word.
    return got is not None and got[0] == want[0] and abs(got[1] - want[1]) <= 1e-3 * want[1] + 0.1


def _need_freecad():
    if FREECADCMD is None:
        pytest.skip(r'FreeCAD (freecadcmd.exe) not found: set FREECADCMD or install under C:\Program Files')


@pytest.mark.freecad
@pytest.mark.parametrize('name', list(DESIGNS))
def test_freecad_reads_every_solid(steps, name):
    _need_freecad()
    got, want = _freecad(steps[name]), _occt(steps[name])
    assert _agrees(got, want), (name, 'FreeCAD', got, 'OCCT', want)


@pytest.mark.freecad
def test_freecad_check_refuses_a_broken_file(steps, broken, tmp_path):
    """The misspelled file, and one cut off halfway, against the intact file."""
    _need_freecad()
    intact = steps['hub, two set screws']
    want = _occt(intact)
    assert _agrees(_freecad(intact), want)
    assert not _agrees(_freecad(broken), want)
    lines = open(intact, encoding='latin-1').read().splitlines()
    half = tmp_path / 'half.step'
    half.write_text('\n'.join(lines[:len(lines) // 2] + ['ENDSEC;', 'END-ISO-10303-21;']), encoding='latin-1')
    assert _freecad(str(half)) is None
