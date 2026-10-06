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
    assert rt['faces'] == ['top'] and rt['cb_d'] == ring.d4 == 35.5
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
    w = ' | '.join(client.get('/api/dimensions', query_string={**q, 'hub_od': '30', 'hub_height': '10'})
                   .get_json()['warnings'])
    assert 'the Ø30 mm hub' in w
    # a flange or plate over the ring's face carries the counterbore: nothing to warn
    for extra in ({'flange_enabled': '1', 'flange_3dprint': '0'},
                  {'flange_enabled': '1', 'flange_3dprint': '1', 'flange_top_separate': '1'}):
        d = client.get('/api/dimensions', query_string={**q, **extra}).get_json()
        assert not [x for x in d['warnings'] if 'counterbore' in x], d['warnings']


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


# ── flanges over the ring's face (2026-09-29) ────────────────────────────────

FLANGE = {'flange_enabled': '1', 'flange_angle': '15', 'flange_rim_radius': '3', 'flange_height': '1.5',
          'flange_plate_height': '1'}
CB_AREA = math.pi * 35.5 ** 2 / 4


def _recess(mesh, face, z_face):
    """How deep the counterbore goes in from z_face (0.05 mm steps)."""
    d = 0.0
    for dz in np.arange(0.05, 6.0, 0.05):
        z = z_face - dz if face == 'top' else z_face + dz
        if _hole_area(mesh, z) < 0.9 * CB_AREA:
            break
        d = dz
    return d + 0.025


def test_ring_cover_rule():
    from app import _ring_cover
    q = {**STRAIGHT, **FLANGE}
    assert _ring_cover({**q, 'flange_3dprint': '0'}, '', 'bottom') == (1.0, False)
    assert _ring_cover({**q, 'flange_3dprint': '1', 'flange_top_separate': '1'}, '', 'top') == (1.5, False)
    assert _ring_cover({**q, 'flange_3dprint': '1', 'flange_top_separate': '0'}, '', 'top') == (1.5, True)
    assert _ring_cover({**q, 'flange_3dprint': '1'}, '', 'bottom') == (1.5, True)
    hub = {**q, 'flange_3dprint': '0', 'hub_od': '44', 'hub_height': '12'}
    assert _ring_cover(hub, '', 'top') == (0.0, False)                           # the hub stands through
    assert _ring_cover(hub, '', 'bottom') == (1.0, False)
    assert _ring_cover(STRAIGHT, '', 'top') == (0.0, False)                      # no flanges


@pytest.mark.parametrize('extra, face, z_face', [
    ({'flange_3dprint': '1', 'flange_top_separate': '0', **WASHER}, 'top', None),      # joined
    ({'flange_3dprint': '1', 'flange_top_separate': '1', **WASHER}, 'top', 'sep'),     # separate
    ({'flange_3dprint': '1', 'flange_top_separate': '0', 'spline_ring': 'bottom'}, 'bottom', None),
    ({'flange_3dprint': '0', **WASHER}, 'top', 12.0),                                  # a 1 mm plate on 11
    ({'flange_3dprint': '0', 'spline_ring': 'bottom'}, 'bottom', -1.0),
], ids=['joined-top', 'separate-top', 'joined-bottom', 'metal-top', 'metal-bottom'])
def test_a_flange_over_the_ring_carries_the_counterbore(client, extra, face, z_face):
    """The counterbore is measured from the part's outer face: a flange or
    plate there has it through it, the pulley the rest, so the ring sits
    flush with the outer face whatever covers the pulley."""
    q = {**BASE, **STRAIGHT, **FLANGE, **extra}
    rt = __import__('app')._spline_of(q)['retainer']
    m = _stl(client, q)
    if z_face == 'sep':                                  # the separate top flange, in place
        top = _load(client.get('/download/flange-stl', query_string={**q, 'flange_which': 'top'}).data)
        assert top.is_watertight
        assert _hole_area(top, float(top.bounds[0][2]) + 0.7) == pytest.approx(CB_AREA, rel=3e-3)
        m = trimesh.util.concatenate([m, top])
        z_face = float(top.bounds[1][2])
    elif z_face is None:
        z_face = float(m.bounds[1][2] if face == 'top' else m.bounds[0][2])
    assert _recess(m, face, z_face) == pytest.approx(rt['cb_depth'], abs=0.06)


def test_the_top_flange_hole_is_the_bore_shape(client):
    """A spline's slots and a key's slot go on through the top flange, as
    through the bottom one, not stopped by a round hole at the bore."""
    for q in ({**BASE, **STRAIGHT}, {**BASE, 'bore': '12', 'hub_keyway_w': '4', 'hub_keyway_h': '2'}):
        for sep in ('0', '1'):
            qq = {**q, **FLANGE, 'flange_3dprint': '1', 'flange_top_separate': sep}
            m = _stl(client, qq)
            body = _hole_area(m, 5.0)
            if sep == '0':
                assert _hole_area(m, float(m.bounds[1][2]) - 0.5) == pytest.approx(body, rel=3e-3)
            else:
                top = _load(client.get('/download/flange-stl', query_string={**qq, 'flange_which': 'top'}).data)
                assert _hole_area(top, float(top.bounds[0][2]) + 0.7) == pytest.approx(body, rel=3e-3)


def test_shaft_length_with_a_hub_through_a_separate_top_flange(client):
    from exporters.spline_parts import TAIL
    q = {**BASE, **STRAIGHT, **RING, **FLANGE, 'flange_3dprint': '1', 'flange_top_separate': '1',
         'hub_od': '44', 'hub_height': '12'}
    part = _stl(client, q)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    span = float(part.bounds[1][2] - part.bounds[0][2])
    assert float(shaft.bounds[1][2] - shaft.bounds[0][2]) == pytest.approx(span + TAIL + 1.7, abs=1e-3)


@pytest.mark.parametrize('extra', [
    {'flange_3dprint': '1', 'flange_top_separate': '0', **WASHER},
    {'flange_3dprint': '1', 'flange_top_separate': '1', **WASHER},
    {'flange_3dprint': '0', 'spline_ring': 'bottom'},
], ids=['joined-top', 'separate-top', 'metal-bottom'])
def test_cadquery_step_counterbore_under_flanges(client, monkeypatch, extra):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, **STRAIGHT, **FLANGE, **extra}
    _, path = _step_solid(client, monkeypatch, q)
    stl = _stl(client, q).volume
    if extra['flange_3dprint'] == '0':                  # the STL carries the plates, the STEP doesn't
        stl -= _stl(client, q, '/download/flange-stl').volume
    assert _step_mesh_volume(path) == pytest.approx(stl, rel=5e-3)


# ── rings on either or both faces: the Spline card (2026-09-29) ──────────────

BOTH = {'spline_ring_top': '1', 'spline_ring_bottom': '1'}


def test_ring_faces_new_keys_and_old_links():
    from app import _ring_faces
    assert _ring_faces(BOTH, '') == ['top', 'bottom']
    assert _ring_faces({'spline_ring_top': '0', 'spline_ring_bottom': '1'}, '') == ['bottom']
    assert _ring_faces({'spline_ring_top': '0', 'spline_ring_bottom': '0', 'spline_ring': 'top'}, '') == []
    assert _ring_faces({'spline_ring': 'bottom'}, '') == ['bottom']            # a design saved before
    assert _ring_faces({'spline_ring': 'none'}, '') == []
    assert _ring_faces({'p2_spline_ring_top': '1'}, 'p2_') == ['top']


def test_both_faces_counterbored(client):
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1'}
    m = _stl(client, q)
    assert m.is_watertight
    lo, hi = float(m.bounds[0][2]), float(m.bounds[1][2])
    bore = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01)).area
    for z in (hi - 2.6, lo + 2.6):
        assert _hole_area(m, z) == pytest.approx(CB_AREA, rel=3e-3)
    assert _hole_area(m, (lo + hi) / 2) == pytest.approx(bore, rel=3e-3)


def test_shaft_with_two_rings(client):
    """A groove at each face, DIN's n past each; no TAIL."""
    q = {**BASE, **STRAIGHT, **BOTH}
    part = _stl(client, q)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    span = float(part.bounds[1][2] - part.bounds[0][2])
    length = float(shaft.bounds[1][2] - shaft.bounds[0][2])
    assert length == pytest.approx(span + 2 * 1.7, abs=1e-3)
    body = _section(shaft, length / 2).area
    for z in (1.7 + 1.3 / 2, length - 1.7 - 1.3 / 2):                     # each groove
        assert _section(shaft, z).area < body - 5
    assert _section(shaft, 1.7 + 1.3 + 0.1).area == pytest.approx(body, rel=3e-3)


def test_shaft_groove_on_a_metal_plates_flat_face(client):
    """The ring sits on the plate's flat face, not on its bent lip (which
    stands higher): the groove's outer wall is on the flat face."""
    from app import _ring_face_z
    q = {**BASE, **STRAIGHT, **RING, **FLANGE, 'flange_3dprint': '0'}
    assert _ring_face_z(q, '', 0.0, 11.0) == (-1.0, 12.0)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    from exporters.spline_parts import TAIL
    assert float(shaft.bounds[1][2] - shaft.bounds[0][2]) == pytest.approx(13.0 + TAIL + 1.7, abs=1e-3)


@pytest.mark.parametrize('extra', [{}, {**FLANGE, 'flange_3dprint': '1', 'flange_top_separate': '0'}],
                         ids=['plain', 'flanged'])
def test_cadquery_step_both_faces(client, monkeypatch, extra):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1', **extra}
    _, path = _step_solid(client, monkeypatch, q)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, q).volume, rel=5e-3)


def _parts(client, q):
    import base64
    d = client.get('/api/preview-stl', query_string=q).get_json()
    return {k: (_load_any(base64.b64decode(v)) if v else None) for k, v in d.items()}


def _load_any(data):
    return trimesh.load(io.BytesIO(data), file_type='stl')


def test_preview_parts_sit_in_the_pulley(client):
    """The 3D view's shaft, rings and washers are placed as the preview
    placed the pulley: through its bore, the rings flush in the counterbores."""
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1', 'hub_od': '44', 'hub_height': '12'}
    pulley = _load_any(client.get('/api/preview-stl', query_string=q).data)
    parts = _parts(client, {**q, 'part': 'spline'})
    lo, hi = float(pulley.bounds[0][2]), float(pulley.bounds[1][2])
    sh = parts['shaft']
    assert sh.is_watertight
    assert float(sh.bounds[0][2]) == pytest.approx(lo - 1.7, abs=0.01)
    assert float(sh.bounds[1][2]) == pytest.approx(hi + 1.7, abs=0.01)
    assert np.allclose(sh.bounds.mean(axis=0)[:2], pulley.bounds.mean(axis=0)[:2], atol=0.05)
    rings, washers = parts['rings'], parts['washers']
    assert float(rings.bounds[1][2]) == pytest.approx(hi - 1.3 + 1.2, abs=0.01)     # s under the face
    assert float(rings.bounds[0][2]) == pytest.approx(lo + 1.3 - 1.2, abs=0.01)
    assert float(washers.bounds[1][2]) == pytest.approx(hi - 1.3, abs=0.01)
    assert float(washers.bounds[0][2]) == pytest.approx(lo + 1.3, abs=0.01)
    # no ring, no washer: the shaft alone
    parts = _parts(client, {**q, 'spline_ring_top': '0', 'spline_ring_bottom': '0', 'part': 'spline'})
    assert parts['shaft'] is not None and parts['rings'] is None and parts['washers'] is None


def test_preview_ring_is_the_shaped_ring(client):
    """The 3D view's ring is the DIN 471 outline fitted to its groove — the lugs
    with their holes, the tapered band — the shape the assembly STEP extrudes,
    not the plain open annulus it once was."""
    from app import _spline_of
    from cct_common import retaining_rings as rr
    from cct_common import ring_outline as ro
    from exporters.spline_parts import _as_spline
    q = {**BASE, **STRAIGHT, 'spline_ring_top': '1', 'spline_ring_bottom': '0'}
    ring = _parts(client, {**q, 'part': 'spline'})['rings']
    fitted = ro.installed(rr.for_spline(_as_spline(_spline_of(q))))
    poly = fitted.polygon()
    assert ring.is_watertight
    assert len(poly.interiors) == 2                                     # a hole in each lug
    assert ring.volume == pytest.approx(poly.area * fitted.thickness, rel=1e-3)
    assert float(ring.bounds[1][2] - ring.bounds[0][2]) == pytest.approx(fitted.thickness, abs=1e-6)


def test_preview_parts_in_a_drive(client):
    q = {**BASE, 'dual': 'true', 'p2_teeth': '30', 'center_distance': '80', 'p2_bore': '8',
         **{f'p2_{k}': v for k, v in {**INVOLUTE, **BOTH}.items()}}
    p2 = _load_any(client.get('/api/preview-stl', query_string={**q, 'part': 'p2'}).data)
    sh = _parts(client, {**q, 'part': 'spline2'})['shaft']
    assert np.allclose(sh.bounds.mean(axis=0)[:2], p2.bounds.mean(axis=0)[:2], atol=0.05)
    assert _parts(client, {**q, 'part': 'spline1'}) == {'shaft': None, 'rings': None, 'washers': None}


def test_spokes_flanges_and_a_ringed_spline(client):
    """Spokes + flanges + a ringed spline recursed (_ring_cover -> the spoke
    fit -> the bore -> _spline_of -> _ring_cover) until the stack ran out —
    found by the fuzzer, 2026-09-29."""
    q = {**BASE, 'teeth': '40', **STRAIGHT, **BOTH, **FLANGE, 'flange_3dprint': '1', 'flange_top_separate': '0',
         'spokes_enabled': '1', 'spokes_hub_od': '40', 'spokes_rim_depth': '3', 'spokes_width': '5',
         'spokes_count': '5', 'spokes_fillet_tip': '1', 'spokes_fillet_base': '1.5'}
    from app import _ring_cover
    assert _ring_cover(q, '', 'top') == (0.0, False)          # the flange stops at the spoke rim
    assert _stl(client, q).is_watertight
    assert client.get('/api/dimensions', query_string={**q, 'feature_build': '1'}).status_code == 200
    assert client.get('/api/preview-stl', query_string={**q, 'part': 'spline'}).status_code == 200


# ── the counterbore is optional, per face (Spline card: "Make counterbore") ──
NO_CB = {'spline_cb_top': '0', 'spline_cb_bottom': '0'}


def test_counterbore_optional_per_face():
    """spline_cb_<face>=0 drops that face's counterbore; the ring stays. A
    design from before the choice (no spline_cb_ keys) keeps both."""
    from app import _spline_of
    rt = _spline_of({**STRAIGHT, **BOTH, 'spline_washer': '1'})['retainer']
    assert rt['faces'] == rt['cb_faces'] == ['top', 'bottom']
    assert rt['stack']['top']['ring'] == pytest.approx((-1.3, -0.1))           # sunk, flush
    rt = _spline_of({**STRAIGHT, **BOTH, 'spline_washer': '1', 'spline_cb_top': '0'})['retainer']
    assert rt['faces'] == ['top', 'bottom'] and rt['cb_faces'] == ['bottom']
    assert rt['stack']['top']['washer'] == pytest.approx((0.0, 1.5))           # on the face
    assert rt['stack']['top']['ring'] == pytest.approx((1.5, 2.7))
    assert rt['stack']['top']['shaft_end'] == pytest.approx(1.5 + 1.3 + 1.7)
    assert _spline_of({**STRAIGHT, 'spline_ring': 'top'})['retainer']['cb_faces'] == ['top']   # old link


def test_no_counterbore_stl(client):
    """Without counterbores the bore runs plain to both faces, and the part
    keeps the material the counterbores would have taken."""
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1'}
    with_cb, without = _stl(client, q), _stl(client, {**q, **NO_CB})
    assert without.is_watertight
    lo, hi = float(without.bounds[0][2]), float(without.bounds[1][2])
    bore = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01)).area
    for z in (hi - 0.3, lo + 0.3, hi - 2.6, lo + 2.6):
        assert _hole_area(without, z) == pytest.approx(bore, rel=3e-3)
    assert without.volume - with_cb.volume == pytest.approx(2 * (CB_AREA - bore) * 2.8, rel=1e-2)


def test_one_face_counterbored(client):
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1', 'spline_cb_bottom': '0'}
    m = _stl(client, q)
    lo, hi = float(m.bounds[0][2]), float(m.bounds[1][2])
    bore = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01)).area
    assert _hole_area(m, hi - 2.6) == pytest.approx(CB_AREA, rel=3e-3)
    assert _hole_area(m, lo + 0.3) == pytest.approx(bore, rel=3e-3)


def test_no_counterbore_leaves_a_flange_whole(client):
    """No counterbore, nothing to carry through a flange or plate over the
    face: its hole stays the bore's shape (the flange-ID rule only follows a
    counterbore)."""
    bore = Polygon(spl.sample_closed(spl.path(SP['straight']), 0.01)).area
    q = {**BASE, **STRAIGHT, **FLANGE, **WASHER, 'flange_3dprint': '0', 'spline_cb_top': '0'}
    m = _stl(client, q)                                         # a 1 mm plate on the 11 mm pulley
    assert _hole_area(m, 11.5) == pytest.approx(bore, rel=3e-3)
    q = {**BASE, **STRAIGHT, **FLANGE, **WASHER, 'flange_3dprint': '1', 'flange_top_separate': '1',
         'spline_cb_top': '0'}
    top = _load(client.get('/download/flange-stl', query_string={**q, 'flange_which': 'top'}).data)
    assert top.is_watertight
    assert _hole_area(top, float(top.bounds[0][2]) + 0.7) == pytest.approx(bore, rel=3e-3)


def test_shaft_and_parts_without_counterbore(client):
    """The washer on the face, the ring on the washer, the groove moved out
    with them — and the shaft longer by that much at each face."""
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1', **NO_CB, 'hub_od': '44', 'hub_height': '12'}
    pulley = _load_any(client.get('/api/preview-stl', query_string=q).data)
    lo, hi = float(pulley.bounds[0][2]), float(pulley.bounds[1][2])
    parts = _parts(client, {**q, 'part': 'spline'})
    end = 1.5 + 1.3 + 1.7                                      # washer, groove, DIN n
    assert float(parts['shaft'].bounds[0][2]) == pytest.approx(lo - end, abs=0.01)
    assert float(parts['shaft'].bounds[1][2]) == pytest.approx(hi + end, abs=0.01)
    assert float(parts['washers'].bounds[1][2]) == pytest.approx(hi + 1.5, abs=0.01)
    assert float(parts['washers'].bounds[0][2]) == pytest.approx(lo - 1.5, abs=0.01)
    assert float(parts['rings'].bounds[1][2]) == pytest.approx(hi + 1.5 + 1.2, abs=0.01)
    assert float(parts['rings'].bounds[0][2]) == pytest.approx(lo - 1.5 - 1.2, abs=0.01)
    part = _stl(client, q)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    span = float(part.bounds[1][2] - part.bounds[0][2])
    length = float(shaft.bounds[1][2] - shaft.bounds[0][2])
    assert length == pytest.approx(span + 2 * end, abs=1e-3)
    body = _section(shaft, length / 2).area
    for z in (1.7 + 1.3 / 2, length - 1.7 - 1.3 / 2):         # each groove, n in from the end
        assert _section(shaft, z).area < body - 5
    assert _section(shaft, 1.7 + 1.3 + 0.1).area == pytest.approx(body, rel=3e-3)


def test_dimensions_without_counterbore(client):
    """The ring is listed; no counterbore figures, and no counterbore
    warning however little material a counterbore would have left."""
    from app import _counterbore_warnings, _spline_of
    q = {**BASE, **STRAIGHT, **BOTH, **NO_CB}
    d = client.get('/api/dimensions', query_string=q).get_json()
    p1 = d['pulleys'][0] if 'pulleys' in d else d
    text = str(p1)
    assert 'DIN 471' in text and 'counterbore_d' not in text
    rt = _spline_of(q)['retainer']
    assert _counterbore_warnings(q, '', '', rt, 30.0, True) == []
    rt_cb = _spline_of({**STRAIGHT, **BOTH})['retainer']
    assert len(_counterbore_warnings(q, '', '', rt_cb, 30.0, True)) == 2      # the negative control


@pytest.mark.parametrize('extra', [NO_CB, {'spline_cb_top': '0'}], ids=['neither', 'bottom-only'])
def test_cadquery_step_without_counterbore(client, monkeypatch, extra):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, **STRAIGHT, **BOTH, 'spline_washer': '1', **extra}
    _, path = _step_solid(client, monkeypatch, q)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, q).volume, rel=5e-3)
