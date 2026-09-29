"""
test_bore_shape.py — Bore Shape (ADR-014): splined bores from cct_common.splines
through every export, and the D-flat / keyway giving way to a spline.

The spline's own outline (cct_common.splines.sample_closed) is the reference:
the STL's hole must be it, the SVG and DXF must carry it as true arcs, the
cadquery STEP must be the same solid as the STL, and small_step must refuse
it rather than cut a round hole.
"""
import io
import math
import re
import tempfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon

from cct_common import splines as spl

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '40', 'belt_height': '10', 'clearance_height': '1',
        'print_extra': '0', 'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD'}
STRAIGHT = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'straight', 'spline_n': '6',
            'spline_minor': '23', 'spline_major': '26', 'spline_width': '6'}
INVOLUTE = {'bore': '8', 'bore_shape': 'spline', 'spline_type': 'involute', 'spline_m': '1.5',
            'spline_z': '16', 'spline_pa': '30', 'spline_root': 'flat'}
SPLINES = {'straight': (STRAIGHT, spl.straight(6, 23, 26, 6)),
           'involute': (INVOLUTE, spl.involute(1.5, 16, 30, 'flat'))}


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3), faces=np.arange(3 * n).reshape(-1, 3),
                           process=True)


def _hole_area(mesh, z):
    from test_hub_setscrew import _material_at
    sec = _material_at(mesh, z)
    return sum(Polygon(i).area for i in sec.interiors)


def _spline_area(sp):
    return Polygon(spl.sample_closed(spl.path(sp), 0.01)).area


# ── parsing ──────────────────────────────────────────────────────────────────

def test_spline_of_and_the_bore(client):
    from app import _get_bore, _spline_of
    assert _spline_of({'bore': '8'}) is None
    s = _spline_of(STRAIGHT)
    assert (s['kind'], s['n'], s['minor'], s['major'], s['width']) == ('straight', 6, 23, 26, 6)
    assert _get_bore(STRAIGHT, 'bore') == 23                       # the minor, not the typed 8
    p2 = {f'p2_{k}': v for k, v in INVOLUTE.items()}
    assert _spline_of(p2, 'p2_')['minor'] == pytest.approx(1.5 * 15)
    assert _get_bore(p2, 'p2_bore') == pytest.approx(22.5)


def test_one_bore_shape_at_a_time():
    from app import _parse_hub_params
    *_, fd, kw_w, kw_h = _parse_hub_params({**STRAIGHT, 'hub_flat_depth': '1', 'hub_keyway_w': '4',
                                            'hub_keyway_h': '2'})
    assert (fd, kw_w, kw_h) == (0, 0, 0)


def test_an_impossible_spline_is_a_400(client):
    r = client.get('/download/svg', query_string={**BASE, **STRAIGHT, 'spline_width': '30'})
    assert r.status_code == 400 and b"don't fit" in r.data


def test_splines_route(client):
    d = client.get('/api/splines').get_json()
    assert d['straight']['light'][0] == [6, 23, 26, 6] and d['involute']['modules']['45'][0] == 0.25


# ── the exports ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('kind', ['straight', 'involute'])
@pytest.mark.parametrize('route', ['/download/stl', '/api/preview-stl'])
def test_stl_hole_is_the_spline(client, route, kind):
    q, sp = SPLINES[kind]
    r = client.get(route, query_string={**BASE, **q})
    assert r.status_code == 200
    m = _load(r.data) if route == '/download/stl' else trimesh.load(io.BytesIO(r.data), file_type='stl')
    assert m.is_watertight
    assert _hole_area(m, float(m.bounds[0][2]) + 3) == pytest.approx(_spline_area(sp), rel=2e-3)


def test_pulley_2_has_its_own_spline(client):
    q = {**BASE, 'dual': 'true', 'p2_teeth': '60', 'bore': '8', 'center_distance': '150', 'pulley': '2',
         **{f'p2_{k}': v for k, v in INVOLUTE.items()}}
    m = _load(client.get('/download/stl', query_string=q).data)
    assert m.is_watertight
    assert _hole_area(m, 3) == pytest.approx(_spline_area(SPLINES['involute'][1]), rel=2e-3)


def test_svg_draws_the_spline_with_arcs(client):
    svg = client.get('/download/svg', query_string={**BASE, **STRAIGHT, 'include_data': '0'}).data.decode()
    bore = re.findall(r'<(?:path|circle)\b[^>]*/>', svg)[1]
    assert bore.startswith('<path') and bore.count(' A ') == 12      # 6 slot bottoms + 6 lands
    assert 'cx="0" cy="0"' not in bore                               # not the round bore


def test_dxf_draws_the_spline_as_lines_and_arcs(client):
    import ezdxf
    data = client.get('/download/dxf', query_string={**BASE, **STRAIGHT}).data
    doc = ezdxf.read(io.StringIO(data.decode('utf-8', errors='ignore')))
    bore = [e for e in doc.modelspace() if e.dxf.layer == 'BORE']
    kinds = sorted({e.dxftype() for e in bore})
    assert kinds == ['ARC', 'LINE'] and len(bore) == 24               # per slot: 2 walls, bottom, land
    radii = sorted({round(e.dxf.radius, 6) for e in bore if e.dxftype() == 'ARC'})
    assert radii == [11.5, 13.0]


def test_small_step_refuses_a_spline(client, monkeypatch):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    r = client.get('/download/step', query_string={**BASE, **STRAIGHT})
    assert r.status_code == 400 and b"splined bore" in r.data


@pytest.mark.parametrize('kind', ['straight', 'involute'])
def test_cadquery_step_is_the_stl(client, monkeypatch, kind):
    pytest.importorskip('cadquery')
    import cadquery as cq
    from fuzz_pulley import _step_mesh_volume
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    q = {**BASE, **SPLINES[kind][0], 'hub_od': '44', 'hub_height': '12', 'hub_screw_size': 'M4',
         'hub_screw_hold': 'nut', 'hub_screw_count': '1', 'hub_captured_nut': '1'}
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(client.get('/download/step', query_string=q).data)
    solids = [so for sh in cq.importers.importStep(f.name).vals() for so in sh.Solids()]
    assert len(solids) == 1 and solids[0].isValid()
    stl = _load(client.get('/download/stl', query_string=q).data)
    assert _step_mesh_volume(Path(f.name)) == pytest.approx(stl.volume, rel=5e-3)


# ── the Dimensions panel ─────────────────────────────────────────────────────

def test_dimensions_show_the_spline_and_warn_on_reach(client):
    q = {**BASE, 'teeth': '20', 'feature_build': '1', 'bore_shape': 'spline', 'spline_type': 'involute',
         'spline_m': '2', 'spline_z': '12', 'spline_pa': '45', 'spline_root': 'fillet', 'hub_od': '25'}
    d = client.get('/api/dimensions', query_string=q).get_json()
    p = d['pulleys'][0]
    assert p['spline_minor'] == pytest.approx(2 * 11.4) and p['spline_major'] == pytest.approx(2 * 13.2)
    assert 'spline_minor' in p['approx'] and 'spline_major' not in p['approx']   # 45°: minor is EST
    w = ' | '.join(d['warnings'])
    assert 'tooth root' in w and 'hub' in w
    ok = client.get('/api/dimensions', query_string={**BASE, **STRAIGHT}).get_json()
    assert not [x for x in ok['warnings'] if 'spline' in x]
