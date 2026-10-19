"""
test_hex_screw_hole.py — a hex set-screw hole stands corner-up, the same in the
STL and the STEP (cct_common.screws.hex_hole_outline; the owner, 2026-10-18:
"the apex points of the holes must be at the bottom and top of the holes",
so it prints without support).

The STL cuts the hole with this app's own cutter (step_exporter), the STEP with
small_step: two pieces of code that must agree. A hex turned 30° weighs the
same, so the volume checks (test_sample_designs_step) never see it; Gear's and
Sprocket's STLs were turned so on 2026-10-16. Each screw is cut across at mid
hub wall and its corners compared, by cct_common.checks.hex_hole.

Needs OCP (.venv312) and a small_step binary for the STEP half; skipped without.
"""
import math
import os
import sys
import tempfile

import numpy as np
import pytest
import trimesh

from cct_common.checks import hex_hole

HUB_H = 15.0
BASE = dict(family='Imperial', pitch='XL', teeth='30', hub_od='30', hub_height=str(HUB_H),
            hub_screw_size='M5', hub_screw_hold='thread', hub_screw_count='2', hub_screw_dia='5',
            hub_captured_nut='0', screw_hole_shape='hex', thread_engagement='50', hex_flat='82',
            belt_height='12.5', clearance_height='2')
ROUND = dict(BASE, bore='8')
HEX_BAR = dict(BASE, bore='8', bore_shape='spline', spline_type='hex', spline_af='12.7', spline_series='inch')
# (design, the bore's outer reach in mm, where the two screws point)
CASES = {'round bore': (ROUND, 4.0, (0, 90)), 'hex bar': (HEX_BAR, 12.7 / math.sqrt(3), (0, 120))}


def _flats(q):
    from geometry import set_screw
    return set_screw.parse(q).hole['flats']


def _stl_mesh(client, q):
    r = client.get('/download/stl', query_string=dict(q, pulley='1'))
    assert r.status_code == 200, r.data[:300]
    n = int(np.frombuffer(r.data[80:84], '<u4')[0])           # the design rides after the triangles
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    tris = np.frombuffer(r.data[84:84 + 50 * n], rec)['v'].astype(float)
    return trimesh.Trimesh(**trimesh.triangles.to_kwargs(tris))


def _frames(mesh, reach, angles):
    """One cut per screw, mid hub wall, at the screw's height (half way up the hub)."""
    r_mid = (reach + float(BASE['hub_od']) / 2) / 2
    z = mesh.bounds[1][2] - HUB_H / 2
    return [hex_hole.Frame(centre=(r_mid * math.cos(math.radians(a)), r_mid * math.sin(math.radians(a)), z),
                           across=(-math.sin(math.radians(a)), math.cos(math.radians(a)), 0.0),
                           along=(0.0, 0.0, 1.0)) for a in angles]


@pytest.mark.parametrize('case', CASES)
def test_the_stl_hex_stands_corner_up(client, case):
    q, reach, angles = CASES[case]
    mesh = _stl_mesh(client, q)
    for a, frame in zip(angles, _frames(mesh, reach, angles)):
        pts = [frame.to_2d(p) for p in hex_hole.mesh_section_points(mesh, frame)]
        corners = hex_hole.corner_angles(pts, _flats(q))
        assert hex_hole.corner_up(corners), f'{case}, screw at {a}°: corners at {corners}°'


def test_a_flats_up_hex_fails_the_check(client, monkeypatch):
    """Negative control: the cutter turned 30° (as Gear's was) is caught."""
    import exporters.step_exporter as se
    real = se.screws.hex_hole_outline

    def flats_up(flats, grow=0.0):
        c, s = math.cos(math.pi / 6), math.sin(math.pi / 6)
        return [(u * c - v * s, u * s + v * c) for u, v in real(flats, grow)]

    monkeypatch.setattr(se.screws, 'hex_hole_outline', flats_up)
    q, reach, angles = CASES['round bore']
    mesh = _stl_mesh(client, q)
    frame = _frames(mesh, reach, angles)[0]
    corners = hex_hole.corner_angles([frame.to_2d(p) for p in hex_hole.mesh_section_points(mesh, frame)], _flats(q))
    assert len(corners) == 6 and not hex_hole.corner_up(corners), corners


def _step_ready():
    try:
        import OCP  # noqa: F401
        from app import _SS_BIN
    except ImportError:
        return False
    return os.path.isfile(_SS_BIN)


@pytest.mark.skipif(not _step_ready(), reason='needs OCP (.venv312) and a small_step binary')
@pytest.mark.parametrize('case', CASES)
def test_the_step_hex_stands_as_the_stl(client, case):
    q, reach, angles = CASES[case]
    mesh = _stl_mesh(client, q)
    r = client.get('/download/assembly-step', query_string=dict(q, parts='p1'))
    assert r.status_code == 200, r.data[:300]
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
        f.write(r.data)
    try:
        for a, frame in zip(angles, _frames(mesh, reach, angles)):
            stl = hex_hole.mesh_section_points(mesh, frame)
            step = hex_hole.step_section_points(sys.executable, f.name, frame)
            assert hex_hole.check(stl, step, frame, _flats(q)) == '', f'{case}, screw at {a}°'
    finally:
        os.unlink(f.name)
