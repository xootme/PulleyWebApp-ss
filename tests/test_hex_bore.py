"""
test_hex_bore.py — the Hex bar spline type (ADR-018): a hex bore, metric or
inch, sharp or rounded, with the ring for its across flats, through every
export. cct_common.splines / retaining_rings are the reference.
"""
import math
import tempfile
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from cct_common import iso286
from cct_common import retaining_rings as rr
from cct_common import splines as spl

from test_spline_retainer import BASE, _hole_area, _load, _section, _stl, _step_solid

HALF_INCH = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'hex', 'spline_af': '12.7',
             'spline_series': 'inch'}
ROUNDED = {**HALF_INCH, 'spline_rounded': '1', 'spline_ac': '13.75'}      # REV 1/2" rounded hex
METRIC = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'hex', 'spline_af': '17',
          'spline_series': 'metric'}
RINGS = {'spline_ring_top': '1', 'spline_ring_bottom': '1'}


def _area(sp):
    return Polygon(spl.sample_closed(spl.path(sp), 0.01)).area


def test_spline_of_a_hex_bar():
    from app import _spline_of
    s = _spline_of({**ROUNDED, **RINGS})
    assert (s['kind'], s['minor'], s['major'], s['series']) == ('hex', 12.7, 13.75, 'inch')
    assert s['bore'] == pytest.approx(12.7 + iso286.it(12.7, 11) / 2, abs=1e-4)     # H11 middle
    assert s['retainer']['ring'] == 'SH-50 / 5100-50 (1/2")' and s['retainer']['mcmaster'] == ''
    assert s['retainer']['cb_d'] == pytest.approx(0.74 * 25.4)
    s = _spline_of({**HALF_INCH, 'spline_ac': '13.75'})              # not rounded: the ac is ignored
    assert s['major'] == pytest.approx(12.7 * 2 / math.sqrt(3))
    assert _spline_of({**METRIC, **RINGS})['retainer']['ring'] == 'DIN 471 17 × 1'


@pytest.mark.parametrize('q', [{'spline_af': '0'}, {'spline_af': '90'}, {'spline_series': 'imperial'},
                               {'spline_rounded': '1', 'spline_ac': '12'}])
def test_a_hex_the_app_cant_make_is_a_400(client, q):
    r = client.get('/download/svg', query_string={**BASE, **HALF_INCH, **q})
    assert r.status_code == 400


@pytest.mark.parametrize('q, sp', [(HALF_INCH, spl.hex_bar(12.7, None, 'inch')),
                                   (ROUNDED, spl.hex_bar(12.7, 13.75, 'inch')),
                                   (METRIC, spl.hex_bar(17))], ids=['sharp', 'rounded', 'metric'])
def test_stl_hole_is_the_hex(client, q, sp):
    m = _stl(client, {**BASE, **q})
    assert m.is_watertight
    assert _hole_area(m, 5.0) == pytest.approx(_area(sp), rel=2e-3)


def test_2d_drawings_carry_the_hex(client):
    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(client.get('/download/dxf', query_string={**BASE, **ROUNDED})
                                 .data.decode('utf-8', 'ignore')))
    bore = [e for e in doc.modelspace() if e.dxf.layer == 'BORE']
    assert sorted(e.dxftype() for e in bore).count('LINE') == 6
    assert sorted(e.dxftype() for e in bore).count('ARC') == 6
    radii = {round(e.dxf.radius, 4) for e in bore if e.dxftype() == 'ARC'}
    assert radii == {round(spl.hole(spl.hex_bar(12.7, 13.75, 'inch')).outer / 2, 4)}


def test_hex_rings_counterbores_and_shaft(client):
    """Two SH-50 rings: a counterbore in each face, the ring's released
    clearance wide; the sample shaft a hex bar with a round groove at each end."""
    q = {**BASE, **ROUNDED, **RINGS, 'spline_washer': '1'}
    m = _stl(client, q)
    assert m.is_watertight
    lo, hi = float(m.bounds[0][2]), float(m.bounds[1][2])
    cb = math.pi * (0.74 * 25.4 / 2) ** 2
    for z in (hi - 1.0, lo + 1.0):
        assert _hole_area(m, z) == pytest.approx(cb, rel=3e-3)
    shaft = _stl(client, {**q, 'part': 'shaft'}, '/download/spline-stl')
    assert shaft.is_watertight
    n = 0.048 * 25.4
    length = float(shaft.bounds[1][2] - shaft.bounds[0][2])
    assert length == pytest.approx((hi - lo) + 2 * n, abs=1e-3)
    groove = _section(shaft, length - n - 0.039 * 25.4 / 2)
    assert groove.area == pytest.approx(math.pi * (0.468 * 25.4 / 2) ** 2, rel=3e-3)   # round, inside the flats
    body = _section(shaft, length / 2).area
    assert body == pytest.approx(Polygon(spl.sample_closed(spl.shaft_path(spl.hex_bar(12.7, 13.75, 'inch')),
                                                            0.01)).area, rel=3e-3)


@pytest.mark.parametrize('q', [ROUNDED, METRIC], ids=['inch-rounded', 'metric'])
def test_cadquery_step_is_the_stl(client, monkeypatch, q):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    qq = {**BASE, **q, **RINGS, 'hub_od': '30', 'hub_height': '10'}
    _, path = _step_solid(client, monkeypatch, qq)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, qq).volume, rel=5e-3)


def test_autofix_picks_a_smaller_hex_of_the_same_series(client):
    """A 1" hex on a pulley that can't hold its ring: the largest inch size that fits."""
    q = {**BASE, 'teeth': '24', 'bore': '8', 'bore_shape': 'spline', 'spline_type': 'hex',
         'spline_af': '25.4', 'spline_series': 'inch', 'spline_ring_top': '1', 'feature_build': '1'}
    f = client.get('/api/dimensions', query_string=q).get_json()['fix']
    preset = f['set'].get('spline1_hex_preset', '')
    assert preset.startswith('inch:') and float(preset.split(':')[1]) < 25.4, f


def test_presets_route(client):
    d = client.get('/api/splines').get_json()['hex']
    assert ['1/2', 12.7] in d['inch'] and 17 in d['metric'] and d['rounded'][0][1:] == [12.7, 13.75, 'inch']


# ── every Retention method on a hex bar ─────────────────────────────────────

SCREW = {'hub_od': '34', 'hub_height': '12', 'hub_screw_size': 'M4', 'hub_screw_count': '2'}


@pytest.mark.parametrize('hold, count, angles', [
    ('thread', 1, {0}), ('nut', 1, {0}), ('insert', 1, {0}),
    ('thread', 2, {0, 120}), ('nut', 2, {0, 180}), ('insert', 2, {0, 120})])
def test_a_hex_bar_takes_set_screws_on_its_flats(client, hold, count, angles):
    """Unlike a spline: one or two screws, each on a flat — the first on +X
    (the bore's radius), a second 180 deg on with captured nuts, 120 deg for
    threaded or insert screws (a round bore's 90 deg is a hex's corner)."""
    import math
    from shapely.geometry import Point
    from app import _parse_hub_params, _set_screw
    q = {**BASE, **HALF_INCH, **SCREW, 'hub_screw_count': str(count), 'hub_screw_hold': hold,
         'hub_captured_nut': '1' if hold == 'nut' else '0',
         **({'hub_screw_hole_dia': '5.4'} if hold == 'insert' else {})}
    _, _, sd, sc, cn, *_ = _parse_hub_params(q)
    assert (sd, sc, cn) == (4.0, count, hold == 'nut') and _set_screw(q) is not None
    m = _stl(client, q)
    assert m.is_watertight and m.volume < _stl(client, {**BASE, **HALF_INCH, 'hub_od': '34', 'hub_height': '12'}).volume
    sec = _section(m, 17.0)                                        # the hub's middle: the screws' height
    at = lambda d: Point(10 * math.cos(math.radians(d)), 10 * math.sin(math.radians(d)))   # noqa: E731
    assert {d for d in (0, 60, 90, 120, 180, 240, 270) if not sec.contains(at(d))} == angles


def test_a_spline_still_takes_none(client):
    from app import _parse_hub_params
    q = {**BASE, 'bore': '8', 'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '6',
         'spline_minor': '23', 'spline_major': '26', 'spline_width': '6', **SCREW, 'hub_od': '44'}
    assert _parse_hub_params(q)[2:4] == (0.0, 0)


def test_cadquery_step_hex_with_a_captured_nut(client, monkeypatch):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, **ROUNDED, **SCREW, 'hub_screw_hold': 'nut', 'hub_captured_nut': '1', **RINGS}   # two nuts
    _, path = _step_solid(client, monkeypatch, q)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, q).volume, rel=5e-3)


# ── spokes fit round a spline's reach (fuzz, 2026-09-29) ─────────────────────

@pytest.mark.parametrize('bore', [
    {'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '8', 'spline_minor': '32',
     'spline_major': '38', 'spline_width': '6'},
    {**HALF_INCH}], ids=['spline', 'hex'])
def test_spokes_fit_round_the_bores_reach(client, bore):
    """The spoke hub was fitted to a spline's minor, so the slots cut through
    it (the fuzzer's STEP-vs-STL mismatch): it's fitted to the reach now."""
    from app import _spline_of, _as_spline_hole_outer
    q = {**BASE, 'family': 'RPP', 'pitch': '8M', 'teeth': '60', 'bore': '6.3', **bore,
         'spokes_enabled': '1', 'spokes_hub_od': '9.1', 'spokes_rim_depth': '4.8', 'spokes_width': '6.5',
         'spokes_fillet_tip': '2', 'spokes_fillet_base': '3.8', 'spokes_count': '3'}
    fit = client.get('/api/spoke-fit', query_string=q).get_json()
    assert fit['fitted']['hub_od'] > _as_spline_hole_outer(_spline_of(q))


def test_cadquery_step_spline_spokes_flanges(client, monkeypatch):
    pytest.importorskip('cadquery')
    from fuzz_pulley import _step_mesh_volume
    q = {**BASE, 'family': 'RPP', 'pitch': '8M', 'teeth': '60', 'bore': '6.3', 'belt_height': '20.4',
         'clearance_height': '0.02', 'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '8',
         'spline_minor': '32', 'spline_major': '38', 'spline_width': '6',
         'spokes_enabled': '1', 'spokes_hub_od': '9.1', 'spokes_rim_depth': '4.8', 'spokes_width': '6.5',
         'spokes_fillet_tip': '2', 'spokes_fillet_base': '3.8', 'spokes_count': '3', 'spokes_height': '0.4',
         'flange_enabled': '1', 'flange_3dprint': '1', 'flange_angle': '16.6', 'flange_rim_radius': '5.4',
         'flange_height': '4.8', 'flange_top_separate': '1'}
    _, path = _step_solid(client, monkeypatch, q)
    assert _step_mesh_volume(path) == pytest.approx(_stl(client, q).volume, rel=5e-3)
