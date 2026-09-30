"""test_step_small_step.py — the splined / hex STEP path through small_step.

`test_spline_retainer.py` and `test_hex_bore.py` assert the STEP is the STL,
but only on the cadquery track: they `importorskip('cadquery')` and force
`PULLEY_STEP_BACKEND=cadquery`. small_step gained the same ground in 0.5.0 —
a bore from any closed LINE/ARC loop, carried through the body, the hub and
the flanges, plus retaining-ring counterbores and set screws on a hex's flats
— so the assertions are repeated here against that backend, at the same
tolerance.

Skipped when the binary is absent or older than the app requires, because a
test that silently passes without exercising anything is worse than no test.

What small_step still refuses is asserted as a refusal, not skipped: a
refusal that quietly stops refusing is how an unsupported case starts
building wrong.
"""
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'belt_height': '12'}
HEX = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'hex',
       'spline_af': '17', 'spline_series': 'metric'}
STRAIGHT = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'straight',
            'spline_n': '6', 'spline_minor': '23', 'spline_major': '26',
            'spline_width': '6'}
INVOLUTE = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'involute',
            'spline_m': '1.5', 'spline_z': '16', 'spline_pa': '30',
            'spline_root': 'flat'}
SCREWS = {'hub_od': '44', 'hub_height': '12', 'hub_screw_size': 'M4',
          'hub_screw_hold': 'thread', 'hub_screw_count': '2'}


def _ss_ready():
    from exporters.step_worker_ss import SMALL_STEP_MIN_VERSION
    import subprocess
    binp = os.environ.get('SMALL_STEP_BIN', '')
    if not binp or not os.path.isfile(binp):
        return False
    try:
        out = subprocess.run([binp, '--version'], capture_output=True,
                             text=True, timeout=20).stdout.split()
        got = tuple(int(x) for x in out[1].split('.'))
    except Exception:
        return False
    return got >= SMALL_STEP_MIN_VERSION


ss_only = pytest.mark.skipif(not _ss_ready(),
                             reason='SMALL_STEP_BIN unset, missing, or too old')


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def _step(client, monkeypatch, q):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    return client.get('/download/step', query_string=q)


def _solids(data):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(data)
        path = f.name
    rd = STEPControl_Reader(); rd.ReadFile(path); rd.TransferRoots()
    ex = TopExp_Explorer(rd.OneShape(), TopAbs_SOLID)
    n = 0; ok = True; vol = 0.0
    while ex.More():
        s = ex.Current(); n += 1
        if not BRepCheck_Analyzer(s).IsValid():
            ok = False
        g = GProp_GProps(); BRepGProp.VolumeProperties_s(s, g); vol += g.Mass()
        ex.Next()
    Path(path).unlink(missing_ok=True)
    return n, ok, vol


@ss_only
@pytest.mark.parametrize('shape', [HEX, STRAIGHT, INVOLUTE],
                         ids=['hex', 'straight', 'involute'])
def test_small_step_step_is_the_stl(client, monkeypatch, shape):
    """The shaped bore's STEP is the STL, at the cadquery track's tolerance."""
    q = {**BASE, **shape}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 1 and ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


@ss_only
def test_small_step_hex_takes_set_screws_and_keeps_its_hub(client, monkeypatch):
    """Two solids, not one.

    This built ONE solid before small_step 0.5.0 -- the hub silently gone,
    while the file reported no unpaired edge -- so the count is the assertion
    that matters here, not just validity.
    """
    q = {**BASE, **HEX, **SCREWS}
    r = _step(client, monkeypatch, q)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert n == 2 and ok
    stl = _load(client.get('/download/stl', query_string=q).data).volume
    assert vol == pytest.approx(stl, rel=5e-3)


@ss_only
def test_small_step_refuses_a_captured_nut_in_a_shaped_bore(client, monkeypatch):
    """Still unsupported, and it must SAY so rather than lose the hub."""
    q = {**BASE, **HEX, **SCREWS, 'hub_screw_hold': 'nut',
         'hub_captured_nut': '1'}
    r = _step(client, monkeypatch, q)
    assert r.status_code != 200
    assert b'captured nut' in r.data
