"""
test_set_screw_step.py — the cadquery STEP cuts the same set-screw holes as
the STL (ADR-013): threaded round / hex, a captured nut's clearance hole, a
heat-set insert's hole, and the nominal hole of an old design.

The STEP is made with PULLEY_STEP_BACKEND=cadquery, tessellated finely, and
measured with the STL tests' own slicing (test_set_screw_holes._hole_width).
Skipped without cadquery (the .venv312 track has it).
"""
import math
import tempfile

import numpy as np
import pytest

cq = pytest.importorskip("cadquery")
trimesh = pytest.importorskip("trimesh")

from test_hub_setscrew import BELT, BORE, CLEAR, HUB_H, R_MID, Z_SCREW   # noqa: E402
from test_set_screw_holes import HUB_TOP, _hole_width                      # noqa: E402

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '24', 'bore': str(BORE), 'print_extra': '0',
        'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD',
        'belt_height': str(BELT), 'clearance_height': str(CLEAR), 'hub_od': '26', 'hub_height': str(HUB_H)}


@pytest.fixture
def step_mesh(client, monkeypatch):
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')

    def build(**extra):
        r = client.get('/download/step', query_string={**BASE, **{k: str(v) for k, v in extra.items()}})
        assert r.status_code == 200 and r.data.startswith(b'ISO-10303'), r.data[:300]
        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
            f.write(r.data)
        verts, tris = [], []
        for shape in cq.importers.importStep(f.name).vals():
            for solid in shape.Solids():
                vs, ts = solid.tessellate(0.005, 0.05)
                off = len(verts)
                verts += [(v.x, v.y, v.z) for v in vs]
                tris += [(a + off, b + off, c + off) for a, b, c in ts]
        return trimesh.Trimesh(vertices=verts, faces=tris, process=True)
    return build


@pytest.mark.parametrize('extra,width', [
    (dict(hub_screw_size='M5', hub_screw_count=1, hub_screw_hold='thread'), 4.567),
    (dict(hub_screw_size='M5', hub_screw_count=1, hub_screw_hold='thread', screw_hole_shape='hex'), 4.10),
    (dict(hub_screw_size='M4', hub_screw_count=1, hub_screw_hold='insert', hub_screw_hole_dia=5.6), 5.6),
    (dict(hub_screw_dia=5, hub_screw_count=1, hub_captured_nut=0), 5.0),          # an old design
], ids=['thread-round', 'thread-hex', 'insert', 'old-design'])
def test_threaded_insert_and_old_holes(step_mesh, extra, width):
    assert _hole_width(step_mesh(**extra), Z_SCREW, R_MID) == pytest.approx(width, abs=0.05)


def test_hex_hole_has_a_corner_up(step_mesh):
    m = step_mesh(hub_screw_size='M5', hub_screw_count=1, hub_screw_hold='thread', screw_hole_shape='hex')
    r = 5.0 * 0.82 / math.sqrt(3)
    assert _hole_width(m, Z_SCREW + 0.8 * r, R_MID) < 5.0 * 0.82 * 0.6


def test_captured_nut_clearance_hole_and_nut(step_mesh):
    m = step_mesh(hub_screw_size='#8-32', hub_screw_count=1, hub_screw_hold='nut', hub_captured_nut=1)
    waf = 8.731                                   # the #8-32 nut, not the nearest metric one
    z = HUB_TOP - waf / math.sqrt(3)
    assert _hole_width(m, z, BORE / 2 + 3.175 + 0.5 + 3.0) == pytest.approx(4.7, abs=0.05)
