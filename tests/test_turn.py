"""
test_turn.py — the 3D drive view's ⟳ Turn (geometry/belt_motion.py): both
pulleys turn the same way, pulley 2 at z1/z2 of pulley 1, and the belt runs
round them in mesh. The page's side: tests/browser/turn_ui.js.

  - the motion answer: rates, each pulley's axis where the preview puts it,
    the path one whole belt (belt teeth × pitch);
  - the pieces at rest ARE the still belt (volume and extent);
  - one tooth on, every piece is exactly at the next one's rest place;
  - in mesh (Gear's touch-without-overlap oracle): turned part of a tooth, the
    belt digs no deeper into either pulley than the still drive does.
    Negative controls, which must dig in: the wrong ratio, a belt standing
    still, and the belt run the wrong way.
"""
import base64
import io
import math

import numpy as np
import pytest
import trimesh

from geometry.belt_motion import belt_at, place

# A small-to-large GT2, a 1:1 GT2 and an HTD; the smallest GT2 the app takes
# (10 teeth), where a tooth wraps 36° and a chord would be furthest off.
DRIVES = {
    'gt2_16_40': ('GT', '2M', 16, 40, 80.0),
    'gt2_20_20': ('GT', '2M', 20, 20, 60.0),
    'htd5_18_30': ('HTD', '5M', 18, 30, 120.0),
    'gt2_10_12': ('GT', '2M', 10, 12, 30.0),
}


def _query(client, family, pitch, z1, z2, centre):
    """The page's query: the centre snapped to a whole belt, as the page sends it."""
    c = client.get('/api/belt', query_string={'family': family, 'pitch': pitch, 'teeth1': z1,
                                              'teeth2': z2, 'center_distance': centre}).get_json()
    return {'family': family, 'pitch': pitch, 'teeth': z1, 'bore': 5, 'dual': 'true',
            'p2_teeth': z2, 'p2_bore': 5, 'center_distance': c['center_dist_mm'],
            'belt_height': 6}, c['n_belt']


@pytest.fixture(scope='module')
def drives():
    from app import app
    client = app.test_client()
    out = {}
    for name, d in DRIVES.items():
        q, n_belt = _query(client, *d)
        r = client.get('/api/preview-stl', query_string=dict(q, part='belt-motion'))
        assert r.status_code == 200, r.data[:300]
        m = r.get_json()
        tri = np.frombuffer(base64.b64decode(m['piece']), dtype=np.float32).reshape(-1, 3).astype(float)
        piece = trimesh.Trimesh(tri, np.arange(len(tri)).reshape(-1, 3), process=True)
        stl = {p: trimesh.load(io.BytesIO(client.get('/api/preview-stl', query_string=dict(q, part=p)).data),
                               file_type='stl') for p in ('p1', 'p2', 'belt')}
        out[name] = {'drive': d, 'motion': m, 'piece': piece, 'stl': stl, 'n_belt': n_belt,
                     'centre': q['center_distance']}
    return out


@pytest.mark.parametrize('name', DRIVES)
def test_rates_axes_and_one_whole_belt(drives, name):
    _, _, z1, z2, _ = DRIVES[name]
    m, b = drives[name]['motion'], drives[name]['motion']['belt']
    assert m['rates'] == [1.0, z1 / z2]                        # the same way, z1/z2
    assert m['teeth'] == [z1, z2]
    # the axes the centre distance apart, the belt's arcs round them (that the
    # pulleys turn about them in mesh: test_turning_the_belt_stays_in_mesh)
    (x1, y1, _), (x2, y2, _) = m['origins']
    assert x2 - x1 == pytest.approx(drives[name]['centre'], abs=1e-9) and y1 == y2
    for i in (0, 1):
        assert b['centre'][i] == pytest.approx(m['origins'][i][:2], abs=1e-9)
    assert b['n'] == drives[name]['n_belt']
    assert b['L'] == pytest.approx(b['n'] * b['pitch'], abs=1e-3)   # belt teeth × pitch
    assert b['r_eff'] == pytest.approx(z1 * b['p_eff'] / (2 * math.pi))


@pytest.mark.parametrize('name', DRIVES)
def test_the_pieces_at_rest_are_the_still_belt(drives, name):
    still, rest = drives[name]['stl']['belt'], belt_at(drives[name]['motion']['belt'], drives[name]['piece'])
    assert rest.volume == pytest.approx(still.volume, rel=1e-3)
    assert np.abs(rest.bounds - still.bounds).max() < 0.01
    assert drives[name]['piece'].is_volume


@pytest.mark.parametrize('name', DRIVES)
def test_one_tooth_on_every_piece_is_at_the_next_rest_place(drives, name):
    b, piece = drives[name]['motion']['belt'], drives[name]['piece']
    v = piece.vertices
    u = 2 * math.pi / DRIVES[name][2]                  # one tooth of pulley 1
    for k in range(b['n']):
        now = place(b, v[:, 0] + k * b['p_eff'] - b['r_eff'] * u, v[:, 1])
        before = place(b, v[:, 0] + ((k - 1) % b['n']) * b['p_eff'], v[:, 1])
        assert np.abs(np.array(now) - np.array(before)).max() < 1e-6


def _dig(b, piece, pulley, origin, theta, shift):
    """The belt's volume inside a pulley turned theta about its axis, the
    pieces moved `shift` along the path."""
    pul = pulley.copy()
    pul.apply_transform(trimesh.transformations.rotation_matrix(theta, [0, 0, 1], [origin[0], origin[1], 0]))
    reach = math.hypot(*(pul.bounds[1][:2] - pul.bounds[0][:2])) / 2 + 1
    v, f, total = piece.vertices, piece.faces, 0.0
    for k in range(b['n']):
        x, y = place(b, v[:, 0] + k * b['p_eff'] + shift, v[:, 1])
        if np.min(np.hypot(x - origin[0], y - origin[1])) > reach:
            continue
        m = trimesh.Trimesh(np.column_stack([x, y, v[:, 2] + b['z0']]), f, process=False)
        i = trimesh.boolean.intersection([m, pul], engine='manifold')
        total += i.volume if i is not None and len(i.faces) else 0.0
    return total


def _digs(d, u, rate2=None, belt=1.0):
    m, b = d['motion'], d['motion']['belt']
    rates = [m['rates'][0], m['rates'][1] if rate2 is None else rate2]
    return [_dig(b, d['piece'], d['stl'][p], m['origins'][i], rates[i] * u, -belt * b['r_eff'] * u)
            for i, p in enumerate(('p1', 'p2'))]


@pytest.mark.parametrize('name', DRIVES)
def test_turning_the_belt_stays_in_mesh(drives, name):
    d = drives[name]
    tol = 0.05 * d['piece'].volume                      # a twentieth of one tooth's piece
    rest = _digs(d, 0.0)
    for frac in (0.25, 0.5, 0.75):
        u = frac * 2 * math.pi / DRIVES[name][2]
        for i, v in enumerate(_digs(d, u)):
            assert v <= rest[i] + tol, f'{frac} tooth on: pulley {i + 1} {v:.3f} mm³ vs {rest[i]:.3f} at rest'


@pytest.mark.parametrize('name', ['gt2_16_40', 'htd5_18_30'])
def test_negative_controls_dig_in(drives, name):
    """The oracle fails what's wrong: pulley 2 at pulley 1's rate, a belt
    standing still, and a belt run the wrong way."""
    d = drives[name]
    big = d['piece'].volume                              # at least one tooth's piece
    rest = _digs(d, 0.0)
    tooth = 2 * math.pi / DRIVES[name][2]
    assert _digs(d, 0.5 * tooth, rate2=1.0)[1] > rest[1] + big                  # wrong ratio
    assert min(v - r for v, r in zip(_digs(d, 0.5 * tooth, belt=0.0), rest)) > big   # belt still
    # run the wrong way, half a tooth lands every tooth in the next groove: a quarter
    assert min(v - r for v, r in zip(_digs(d, 0.25 * tooth, belt=-1.0), rest)) > big


def test_a_one_pulley_preview_has_no_motion():
    from app import app
    r = app.test_client().get('/api/preview-stl', query_string={
        'family': 'GT', 'pitch': '2M', 'teeth': 20, 'bore': 5, 'part': 'belt-motion'})
    # one pulley: the route answers its STL (no drive, no Turn)
    assert r.status_code == 200 and r.mimetype == 'model/stl'
