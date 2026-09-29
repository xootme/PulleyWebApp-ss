"""
test_spline_retainer.py — what goes with a splined bore (ADR-017): the fit
(ISO 14 / ISO 4156 default fits from cct_common.splines), the DIN 471 ring's
counterbore in one face, the splined washer under the ring, the sample shaft,
print compensation on the printed parts only, and no set screw.

The reference figures are cct_common's (splines.hole / shaft, retaining_rings);
these tests check the app builds what they say, in every export.
"""
import io
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, Polygon

from cct_common import retaining_rings as rr
from cct_common import splines as spl

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'belt_height': '10', 'clearance_height': '1',
        'print_extra': '0', 'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD'}
STRAIGHT = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '6',
            'spline_minor': '23', 'spline_major': '26', 'spline_width': '6'}
INVOLUTE = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'involute', 'spline_m': '1.5',
            'spline_z': '16', 'spline_pa': '30', 'spline_root': 'flat'}
SP = {'straight': spl.straight(6, 23, 26, 6), 'involute': spl.involute(1.5, 16, 30, 'flat')}
Q = {'straight': STRAIGHT, 'involute': INVOLUTE}
RING = {'spline_ring': 'top'}
WASHER = {'spline_ring': 'top', 'spline_washer': '1'}
SCREW = {'hub_od': '44', 'hub_height': '12', 'hub_screw_size': 'M4', 'hub_screw_hold': 'nut',
         'hub_screw_count': '2', 'hub_captured_nut': '1'}


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3), faces=np.arange(3 * n).reshape(-1, 3),
                           process=True)


def _section(mesh, z):
    from test_hub_setscrew import _material_at
    return _material_at(mesh, z)


def _hole_area(mesh, z):
    return sum(Polygon(i).area for i in _section(mesh, z).interiors)


def _stl(client, q, route='/download/stl'):
    r = client.get(route, query_string=q)
    assert r.status_code == 200, r.data[:300]
    return _load(r.data)


# ── the fit (cct_common) ─────────────────────────────────────────────────────

@pytest.mark.parametrize('kind', ['straight', 'involute'])
def test_the_sample_shaft_fits_the_bore(kind):
    """At the default fit the shaft goes in with clearance all round, and
    still does once both are printed with the same compensation."""
    sp = SP[kind]
    hole = Polygon(spl.sample_closed(spl.path(sp), 0.01))
    shaft = Polygon(spl.sample_closed(spl.shaft_path(sp), 0.01))
    assert hole.contains(shaft)
    gap = hole.exterior.distance(shaft)
    assert 0.005 < gap < 0.1
    for c in (0.1, 0.2):
        ph = Polygon(spl.printed(spl.sample_closed(spl.path(sp), 0.01), c, hole=True))
        ps = Polygon(spl.printed(spl.sample_closed(spl.shaft_path(sp), 0.01), c, hole=False))
        assert ph.contains(ps) and ph.exterior.distance(ps) > gap + c   # compensation opens the fit


def test_spline_of_carries_the_bore_print_and_ring():
    from app import _spline_of
    s = _spline_of({**STRAIGHT, 'print_extra': '0.2', **WASHER})
    assert s['bore'] == pytest.approx(spl.hole(SP['straight']).inner, abs=1e-4)
    assert s['print'] == 0.2
    rt = s['retainer']
    ring = rr.for_shaft(spl.shaft(SP['straight']).outer)
    assert rt['ring'] == ring.label() == 'DIN 471 26 × 1.2' and rt['mcmaster'] == '98541A128'
    assert rt['face'] == 'top' and rt['cb_d'] == ring.d4 == 35.5
    assert rt['cb_depth'] == pytest.approx(ring.m + rr.WASHER_THICKNESS)
    assert rt['washer_od'] == pytest.approx(ring.d4 - 2 * rr.WASHER_CLEARANCE)
    assert _spline_of({**STRAIGHT, **RING})['retainer']['cb_depth'] == pytest.approx(ring.m)
    assert _spline_of({**STRAIGHT, 'spline_ring': 'none', 'spline_washer': '1'})['retainer'] is None
    assert _spline_of(STRAIGHT)['retainer'] is None


def test_api_spline(client):
    d = client.get('/api/spline', query_string={**STRAIGHT, **WASHER}).get_json()
    assert d['bore'] == pytest.approx(23.0105) and d['shaft_major'] < 26 < d['hole_major']
    assert 'ISO 14' in d['fit'] and d['retainer']['washer_t'] == rr.WASHER_THICKNESS
    p2 = {'pulley': '2', **{f'p2_{k}': v for k, v in INVOLUTE.items()}}
    assert 'ISO 4156' in client.get('/api/spline', query_string=p2).get_json()['fit']
    assert client.get('/api/spline', query_string={'bore': '8'}).get_json() is None
    assert client.get('/api/spline', query_string={**STRAIGHT, 'spline_width': '30'}).status_code == 400


# ── no set screw ─────────────────────────────────────────────────────────────

def test_a_spline_takes_no_set_screw(client):
    from app import _parse_hub_params, _set_screw
    _, _, sd, sc, cn, *_ = _parse_hub_params({**STRAIGHT, **SCREW})
    assert (sd, sc, cn) == (0.0, 0, False)
    assert _set_screw({**STRAIGHT, **SCREW}, '') is None
    with_screw = _stl(client, {**BASE, **STRAIGHT, **SCREW})
    plain = _stl(client, {**BASE, **STRAIGHT, 'hub_od': '44', 'hub_height': '12'})
    assert with_screw.volume == pytest.approx(plain.volume, rel=1e-6)


# ── the counterbore ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('face', ['top', 'bottom'])
@pytest.mark.parametrize('ring', [RING, WASHER], ids=['ring', 'washer'])
def test_stl_counterbore(client, face, ring):
    q = {**BASE, **STRAIGHT, **ring, 'spline_ring': face}
    m = _stl(client, q)
    assert m.is_watertight
    rt = __import__('app')._spline_of(q)['retainer']
    lo, hi = float(m.bounds[0][2]), float(m.bounds[1][2])
    inside = lambda d: hi - d if face == 'top' else lo + d          # noqa: E731
    cb = math.pi * (rt['cb_d'] / 2) ** 2
    assert _hole_area(m, inside(rt['cb_depth'] - 0.2)) == pytest.approx(cb, rel=3e-3)
    bore = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01)).area
    assert _hole_area(m, inside(rt['cb_depth'] + 0.3)) == pytest.approx(bore, rel=3e-3)
    far = _hole_area(m, hi - 0.5 if face == 'bottom' else lo + 0.5)
    assert far == pytest.approx(bore, rel=3e-3)                      # the other face is plain


def test_counterbore_through_a_hub_and_integrated_flanges(client):
    q = {**BASE, **STRAIGHT, **WASHER, 'hub_od': '44', 'hub_height': '12',
         'flange_enabled': '1', 'flange_angle': '15', 'flange_rim_radius': '3', 'flange_3dprint': '1',
         'flange_height': '1.5', 'flange_top_separate': '0'}
    m = _stl(client, q)
    assert m.is_watertight
    hi = float(m.bounds[1][2])
    assert _hole_area(m, hi - 1.0) == pytest.approx(math.pi * 35.5 ** 2 / 4, rel=3e-3)


def test_print_compensation_grows_the_bore_and_counterbore(client):
    q = {**BASE, **STRAIGHT, **RING}
    nominal, printed = _stl(client, q), _stl(client, {**q, 'print_extra': '0.2'})
    z = float(nominal.bounds[0][2]) + 3
    hole = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01))
    grown = Polygon(spl.printed(spl.sample_closed(spl.path(SP['straight']), 0.01), 0.2, hole=True)).area
    assert _hole_area(nominal, z) == pytest.approx(hole.area, rel=3e-3)
    assert _hole_area(printed, z) == pytest.approx(grown, rel=3e-3)
    assert grown - hole.area == pytest.approx(0.2 * hole.length, rel=0.05)
    top = float(printed.bounds[1][2]) - 0.5
    assert _hole_area(printed, top) == pytest.approx(math.pi * (35.5 / 2 + 0.2) ** 2, rel=3e-3)


def test_2d_drawings_are_nominal(client):
    """SVG / DXF are cut, not printed: the fitted hole, no compensation."""
    import ezdxf
    q = {**BASE, **STRAIGHT, **RING, 'print_extra': '0.3'}
    doc = ezdxf.read(io.StringIO(client.get('/download/dxf', query_string=q).data.decode('utf-8', 'ignore')))
    radii = sorted({round(e.dxf.radius, 6) for e in doc.modelspace()
                    if e.dxf.layer == 'BORE' and e.dxftype() == 'ARC'})
    hole = spl.hole(SP['straight'])
    assert radii == pytest.approx([hole.inner / 2, hole.outer / 2])


def _step_solid(client, monkeypatch, q):
    import cadquery as cq
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(client.get('/download/step', query_string=q).data)
    solids = [so for sh in cq.importers.importStep(f.name).vals() for so in sh.Solids()]
    assert len(solids) == 1 and solids[0].isValid()
    return solids[0], Path(f.name)


def test_cadquery_step_counterbore_is_the_stl(client, monkeypatch):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, **STRAIGHT, **WASHER, 'hub_od': '44', 'hub_height': '12'}
    _, path = _step_solid(client, monkeypatch, q)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, q).volume, rel=5e-3)


def test_cadquery_step_bore_and_counterbore_stay_nominal(client, monkeypatch):
    """STEP is CAD interchange: the fitted bore and the counterbore at their
    true sizes whatever the print compensation (which moves only the teeth)."""
    pytest.importorskip('cadquery')
    q = {**BASE, **STRAIGHT, **WASHER, 'hub_od': '44', 'hub_height': '12', 'print_extra': '0.3'}
    solid, _ = _step_solid(client, monkeypatch, q)
    radii = {round(f._geomAdaptor().Cylinder().Radius(), 4)
             for f in solid.Faces() if f.geomType() == 'CYLINDER'}
    hole = spl.hole(SP['straight'])
    for r in (35.5 / 2, hole.inner / 2, hole.outer / 2):
        assert round(r, 4) in radii, (r, sorted(radii))
    assert round(35.5 / 2 + 0.3, 4) not in radii


# ── the sample shaft and the washer ──────────────────────────────────────────

@pytest.mark.parametrize('kind', ['straight', 'involute'])
def test_shaft_stl(client, kind):
    from exporters.spline_parts import TAIL
    q = {**BASE, **Q[kind], **RING, 'hub_od': '44', 'hub_height': '12', 'print_extra': '0'}
    part = _stl(client, q)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    assert shaft.is_watertight
    rt = __import__('app')._spline_of(q)['retainer']
    span = float(part.bounds[1][2] - part.bounds[0][2])
    length = float(shaft.bounds[1][2] - shaft.bounds[0][2])
    assert length == pytest.approx(span + TAIL + rt['n'], abs=1e-3)
    # its section is the mating shaft; the groove (width m, Ø d2) is n below the top
    body = _section(shaft, 1.0)
    outline = Polygon(spl.sample_closed(spl.shaft_path(SP[kind]), 0.01))
    assert body.area == pytest.approx(outline.area, rel=3e-3)
    # d2 is above the spline's minor: the groove crosses the tooth tops only
    groove = _section(shaft, length - rt['n'] - rt['m'] / 2)
    floor = outline.intersection(Point(0, 0).buffer(rt['d2'] / 2, resolution=256))
    assert rt['d2'] / 2 < spl.shaft(SP[kind]).outer / 2
    assert groove.area == pytest.approx(floor.area, rel=3e-3) and groove.area < body.area
    assert _section(shaft, length - rt['n'] - rt['m'] - 0.1).area == pytest.approx(body.area, rel=3e-3)


def test_printed_shaft_shrinks(client):
    q = {**BASE, **STRAIGHT, **RING, 'part': 'shaft'}
    nominal = _stl(client, q, '/download/spline-stl')
    printed = _stl(client, {**q, 'print_extra': '0.2'}, '/download/spline-stl')
    outline = Polygon(spl.sample_closed(spl.shaft_path(SP['straight']), 0.01))
    assert _section(printed, 1.0).area == pytest.approx(
        Polygon(spl.printed(list(outline.exterior.coords)[:-1], 0.2, hole=False)).area, rel=3e-3)
    assert _section(printed, 1.0).area < _section(nominal, 1.0).area


def test_washer(client):
    q = {**BASE, **STRAIGHT, **WASHER, 'part': 'washer'}
    w = _stl(client, q, '/download/spline-stl')
    assert w.is_watertight
    assert float(w.bounds[1][2] - w.bounds[0][2]) == pytest.approx(rr.WASHER_THICKNESS)
    sec = _section(w, 0.5)
    assert Polygon(sec.exterior).area == pytest.approx(math.pi * (34.5 / 2) ** 2, rel=3e-3)
    hole = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01))
    assert sum(Polygon(i).area for i in sec.interiors) == pytest.approx(hole.area, rel=3e-3)
    # none without a ring and the box ticked
    r = client.get('/download/spline-stl', query_string={**BASE, **STRAIGHT, **RING, 'part': 'washer'})
    assert r.status_code == 400
    r = client.get('/download/spline-stl', query_string={**BASE, 'bore': '8', 'part': 'shaft'})
    assert r.status_code == 400


@pytest.mark.parametrize('part', ['shaft', 'washer'])
def test_shaft_and_washer_drawings(client, part):
    import ezdxf
    q = {**BASE, **STRAIGHT, **WASHER, 'part': part}
    svg = client.get('/download/spline-svg', query_string=q)
    assert svg.status_code == 200 and b'<svg' in svg.data and b' A ' in svg.data
    dxf = client.get('/download/spline-dxf', query_string=q)
    assert dxf.status_code == 200
    doc = ezdxf.read(io.StringIO(dxf.data.decode('utf-8', 'ignore')))
    kinds = sorted({e.dxftype() for e in doc.modelspace()})
    assert kinds == (['ARC', 'CIRCLE', 'LINE'] if part == 'washer' else ['ARC', 'LINE'])


def test_pulley_2_spline_parts(client):
    q = {**BASE, 'dual': 'true', 'p2_teeth': '60', 'center_distance': '150', 'pulley': '2',
         **{f'p2_{k}': v for k, v in {**INVOLUTE, **WASHER}.items()}}
    for part in ('shaft', 'washer'):
        assert _stl(client, {**q, 'part': part}, '/download/spline-stl').is_watertight


# ── the Dimensions panel ─────────────────────────────────────────────────────

def test_dimensions_show_the_ring_and_warn(client):
    q = {**BASE, 'feature_build': '1', **STRAIGHT, **WASHER}
    d = client.get('/api/dimensions', query_string=q).get_json()
    p = d['pulleys'][0]
    assert (p['counterbore_d'], p['counterbore_depth'], p['washer_od']) == (35.5, 2.8, 34.5)
    assert p['shaft_major'] < p['spline_major'] and not [w for w in d['warnings'] if 'counterbore' in w]
    cases = {'the Ø30 mm hub': {'hub_od': '30', 'hub_height': '10'},
             'metal flange plate': {'flange_enabled': '1', 'flange_3dprint': '0'},
             'separate top flange': {'flange_enabled': '1', 'flange_3dprint': '1', 'flange_top_separate': '1'}}
    for words, extra in cases.items():
        w = ' | '.join(client.get('/api/dimensions', query_string={**q, **extra}).get_json()['warnings'])
        assert words in w, (words, w)
    bottom = client.get('/api/dimensions', query_string={**q, 'spline_ring': 'bottom', 'flange_enabled': '1',
                                                          'flange_3dprint': '1', 'flange_top_separate': '1'})
    assert not [w for w in bottom.get_json()['warnings'] if 'counterbore' in w]


# ── Auto-fix: a spline the part can't hold (bug report 2026-09-29) ───────────

REPORTED = {'family': 'STD', 'pitch': '8M', 'teeth': '22', 'bore': '23.0105', 'print_extra': '0',
            **STRAIGHT, **RING, 'hub_od': '25', 'hub_height': '10', 'clearance_height': '10',
            'belt_height': '10', 'feature_build': '1'}


def _fix(client, q):
    return client.get('/api/dimensions', query_string=q).get_json()['fix'] or {'set': {}, 'changes': []}


def test_autofix_grows_the_hub_round_the_spline_and_ring(client):
    """STD 8M 22T, 6 x 23 x 26 with a top ring in a 25 mm hub: the spline and the
    35.5 mm counterbore cut through it. The hub grows to counterbore + 2 mm."""
    f = _fix(client, REPORTED)
    assert f['set'] == {'hub1_od': 37.5} and 'hub OD 25 → 37.5' in f['changes'][0]
    assert not _fix(client, {**REPORTED, 'hub_od': '37.5'})['set']
    # the ring on the bottom face: the hub only needs to clear the spline
    assert _fix(client, {**REPORTED, 'spline_ring': 'bottom'})['set'] == {'hub1_od': 28.5}


def test_autofix_shrinks_a_spline_the_part_cannot_hold(client):
    small = {**REPORTED, 'family': 'HTD', 'pitch': '5M', 'teeth': '24', 'clearance_height': '10'}
    f = _fix(client, small)
    assert f['set'] == {'spline1_preset': '6,16,20,4', 'hub1_od': 30.5}, f
    f = _fix(client, {**small, 'spline_ring': 'bottom'})
    assert f['set'] == {'spline1_preset': '6,18,22,5'}, f            # the hub already clears 22
    inv = {**small, **INVOLUTE, 'spline_m': '1.25', 'spline_z': '30', **RING}
    f = _fix(client, inv)
    assert set(f['set']) == {'spline1_z', 'hub1_od'} and f['set']['spline1_z'] < 30, f
    # nothing smaller fits: warnings only
    assert not _fix(client, {**small, 'teeth': '16'})['set'].keys() - {'clearance_height'}


def test_a_blank_hub_field_is_no_hub(client):
    r = client.get('/api/preview-stl', query_string={**REPORTED, 'hub_od': '', 'hub_height': ''})
    assert r.status_code == 200
    from app import _parse_hub_params
    assert _parse_hub_params({'hub_od': '', 'hub_screw_count': ''})[:4] == (0.0, 0.0, 0.0, 0)
