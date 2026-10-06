"""test_nut_counterbore.py — a captured nut and a ring's top counterbore, and a
D-flat set screw that reaches the flat (the small_step session's handoff,
2026-10-06).

1. The nut's pocket opens from the hub's top face, where a ring's top
   counterbore is cut. In plan its far corner must sit inside the counterbore
   (else the lip roofs the pocket and the nut can't go in); in z the screw's
   stub must stay below the counterbore's floor. Every such STEP was invalid
   on small_step before 0.7.0; the app now says which clause breaks, with an
   Auto-fix, and the worker refuses (the whole combination on an older binary).
2. A plain set screw on a D-flat: the STL cut its hole only in to the round
   bore, so the D's segment plugged it and the screw couldn't reach the shaft.
"""
import math
import os

import numpy as np
import pytest

from geometry.nut_counterbore import problems

HEX10 = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'bore': '10', 'belt_height': '10', 'feature_build': '1',
         'bore_shape': 'spline', 'spline_type': 'hex', 'spline_af': '10', 'spline_series': 'metric',
         'spline_ring_top': '1', 'hub_od': '34', 'hub_height': '18', 'hub_screw_size': 'M4',
         'hub_screw_count': '1', 'hub_screw_hold': 'nut'}


# ── 1. the two clauses ────────────────────────────────────────────────────────

def _p(cb_d, cb_depth, faces=('top',), **kw):
    """The handoff's case: hub Ø34 × 18 over a Ø10 bore, M4 captured nut (af 7,
    3.2 thick), the nominal Ø4 hole small_step cuts."""
    args = dict(bore_mm=10.0, hub_od_mm=34.0, hub_height_mm=18.0, screw_count=1, captured_nut=True,
                screw_dia_mm=4.0, nut=(7.0, 3.2), hole=None,
                spline={'kind': 'hex', 'retainer': {'cb_faces': list(faces), 'cb_d': cb_d, 'cb_depth': cb_depth}})
    args.update(kw)
    return problems(**args)


def test_the_handoff_cases():
    assert _p(26, 1.0) == []                                   # builds on 0.7.0
    assert _p(26, 0.5) == []
    [plan] = _p(18, 1.0)                                       # refused by name on 0.7.0
    assert 'roof over' in plan and 'Ø18.00' in plan and 'Ø18.95' in plan
    [z] = _p(26, 1.3)                                          # the z clause alone, not implied by plan
    assert 'breaks into it' in z and '1.25 mm' in z
    assert len(_p(18, 1.3)) == 2


@pytest.mark.parametrize('kw', [{'captured_nut': False}, {'screw_count': 0}, {'hub_height_mm': 0.0}],
                         ids=['screw held by thread', 'no screws', 'no hub'])
def test_no_nut_pocket_no_check(kw):
    assert _p(18, 1.3, **kw) == []


def test_only_the_top_face():
    assert _p(18, 1.3, faces=('bottom',)) == []


# ── the Dimensions panel ──────────────────────────────────────────────────────

def _dims(client, q):
    r = client.get('/api/dimensions', query_string=q)
    assert r.status_code == 200, r.data[:200]
    return r.get_json()


def test_the_panel_warns_and_the_fix_takes_the_counterbore_off(client):
    d = _dims(client, HEX10)
    # a Ø10 hex bar's ring cuts Ø17 × 1.1: the M4 nut's pocket is Ø18.99 across (plan); 1.1 is
    # within the 1.25 mm the nominal Ø4 screw leaves (z)
    w = [x for x in d['warnings'] if 'counterbore' in x and ('roof over' in x or 'breaks into it' in x)]
    assert len(w) == 1 and 'roof over' in w[0] and 'Ø17.00' in w[0], d['warnings']
    assert d['fix']['set']['spline1_cb_top'] is False
    from agent import _fix_params
    assert _fix_params(d['fix']['set'])['spline_cb_top'] is False
    d = _dims(client, {**HEX10, 'spline_cb_top': '0'})
    assert not any('roof over' in x or 'breaks into it' in x for x in d['warnings'])


@pytest.mark.parametrize('extra', [{'hub_screw_hold': 'thread'}, {'spline_ring_top': '0'}, {'feature_build': '0'}],
                         ids=['thread', 'no top ring', '2D'])
def test_the_panel_is_quiet_without_the_clash(client, extra):
    d = _dims(client, {**HEX10, **extra})
    assert not any('roof over' in x or 'breaks into it' in x for x in d['warnings'])


# ── the STEP worker ───────────────────────────────────────────────────────────

def _cmd_params(cb_d, cb_depth):
    return {'family': 'HTD', 'pitch': '5M', 'num_teeth': 40, 'bore_mm': 10.0, 'belt_height_mm': 10.0,
            'hub_od_mm': 34.0, 'hub_height_mm': 18.0, 'screw_dia_mm': 4.0, 'screw_count': 1,
            'captured_nut': True, 'screw_nut': [7.0, 3.2], 'screw_hole': {'shape': 'circle', 'diameter': 4.5},
            'spline': {'kind': 'hex', 'retainer': {'cb_faces': ['top'], 'cb_d': cb_d, 'cb_depth': cb_depth}}}


@pytest.mark.parametrize('version, cb, refused', [
    ((0, 6, 0), (26, 1.0), "can't be made in STEP together yet"),   # every such solid was invalid before 0.7.0
    ((0, 7, 0), (26, 1.0), None),                                    # conforming: built
    ((0, 7, 0), (18, 1.0), 'roof over'),                             # the plan clause, by name
    ((0, 7, 0), (26, 1.3), 'breaks into it'),                        # the z clause
])
def test_the_worker_refuses_by_name(monkeypatch, tmp_path, version, cb, refused):
    from exporters import step_worker_ss as w
    monkeypatch.setattr(w, '_binary_version', lambda _bin: version)
    params = _cmd_params(*cb)
    if refused:
        with pytest.raises(w.Refused, match=refused):
            w._build_pulley_cmd(params, 'small_step', str(tmp_path / 'p.dxf'))
    else:
        cmd = w._build_pulley_cmd(params, 'small_step', str(tmp_path / 'p.dxf'))
        assert '--counterbore' in cmd and '--captured-nut' in cmd


def test_a_step_download_says_why(client, monkeypatch):
    """End to end on the binary this machine has: a 400 with the message, not a
    traceback and not an invalid file."""
    if not os.environ.get('SMALL_STEP_BIN'):
        pytest.skip('SMALL_STEP_BIN unset')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    monkeypatch.delenv('PULLEY_STEP_BACKEND', raising=False)
    r = client.get('/download/step', query_string=HEX10)
    assert r.status_code == 400, r.data[:300]
    msg = r.get_json()['error']
    assert ('roof over' in msg or "can't be made in STEP together yet" in msg) and 'Traceback' not in msg


# ── 2. a D-flat set screw reaches the flat ───────────────────────────────────

DFLAT = {'family': 'HTD', 'pitch': '5M', 'teeth': '30', 'bore': '8', 'belt_height': '10',
         'hub_od': '24', 'hub_height': '10', 'hub_screw_size': 'M3', 'hub_screw_count': '1',
         'hub_screw_hold': 'thread', 'hub_flat_depth': '0.8'}


def _inside(tris, p):
    """Is p inside the closed mesh? Ray parity along +z (Möller–Trumbore)."""
    d = np.array([0.0, 0.0, 1.0])
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = v1 - v0, v2 - v0
    h = np.cross(d, e2)
    a = np.einsum('ij,ij->i', e1, h)
    ok = np.abs(a) > 1e-12
    f = np.where(ok, 1.0 / np.where(ok, a, 1), 0)
    s = p - v0
    u = f * np.einsum('ij,ij->i', s, h)
    q = np.cross(s, e1)
    v = f * (q @ d)
    t = f * np.einsum('ij,ij->i', e2, q)
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
    return int(hit.sum()) % 2 == 1


def _tris(client, q):
    data = client.get('/download/stl', query_string=q).data
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    return np.frombuffer(data[84:84 + 50 * n], rec)['v'].astype(float)


def test_a_d_flat_screw_hole_reaches_the_flat(client):
    """In the D's segment (between the flat at r 3.2 and the round bore at r
    4.0), on the screw's axis at the hub's middle: material without a screw,
    the hole with one. r 3.3: the old hole stopped at the bore less its 0.5 mm
    overshoot, r 3.5, leaving the 3.2 to 3.5 plug."""
    no_screw, screw = _tris(client, {**DFLAT, 'hub_screw_count': '0'}), _tris(client, DFLAT)
    z0 = no_screw[:, :, 2].min()
    p = np.array([3.3, 0.0, z0 + 10.0 + 5.0])           # r 3.3 on +x (the flat's side), hub mid-height
    assert _inside(no_screw, p), 'the probe should be in the D segment'
    assert not _inside(screw, p), 'the screw hole stops short of the flat'
    # and nowhere else: the hub wall beside the hole is still there
    assert _inside(screw, np.array([0.0, 8.0, z0 + 15.0]))
