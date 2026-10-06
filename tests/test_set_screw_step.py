"""
test_set_screw_step.py — the STEP cuts the same set-screw holes as the STL
(ADR-013): threaded round / hex, a captured nut's clearance hole and its own
nut, a heat-set insert's hole, and the nominal hole of an old design.

Each STEP is made by both backends (cadquery, and small_step told the hole and
nut by --screw-hole / --nut), tessellated finely, and measured with the STL
tests' own slicing (test_set_screw_holes._hole_width). Skipped without cadquery
(the .venv312 track has it) to read the files.
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


@pytest.fixture(params=['cadquery', 'small_step'])
def step_mesh(request, client, monkeypatch):
    """Both backends cut the same holes. small_step is told them by --screw-hole
    and --nut (it cut the nominal hole and a nearest-metric nut until 2026-10-06);
    skipped when the binary the route runs doesn't take those flags."""
    if request.param == 'small_step':
        import app as A
        from exporters.step_worker_ss import _has_flag
        if not (A._SS_AVAILABLE and _has_flag(A._SS_BIN, '--screw-hole') and _has_flag(A._SS_BIN, '--nut')):
            pytest.skip(f'no small_step with --screw-hole and --nut ({A._SS_BIN})')
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery' if request.param == 'cadquery' else '')
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


def test_captured_nut_clearance_hole_and_nut(request, step_mesh):
    if request.node.callspec.params['step_mesh'] == 'small_step':
        request.applymarker(pytest.mark.xfail(strict=True, reason=(
            'small_step (0.6.0 and 0.7.0 18352fb6) cuts the --nut pocket at its size but centres the '
            'screw from its own nearest-metric nut: an #8-32 nut\'s screw sits 1.05 mm high (18.50, '
            'the STL 17.45)')))
    m = step_mesh(hub_screw_size='#8-32', hub_screw_count=1, hub_screw_hold='nut', hub_captured_nut=1)
    waf = 8.731                                   # the #8-32 nut, not the nearest metric one
    z = HUB_TOP - waf / math.sqrt(3)
    assert _hole_width(m, z, BORE / 2 + 3.175 + 0.5 + 3.0) == pytest.approx(4.7, abs=0.05)


@pytest.mark.parametrize('flange', [dict(flange_3dprint=1, flange_height=2.4),
                                    dict(flange_3dprint=0, flange_plate_height=1.2)], ids=['printed', 'metal'])
def test_a_flanged_hub_stands_its_height_proud(request, client, step_mesh, flange):
    """A flanged hub fills the flange's thickness and then stands hub_height proud
    of it, its screw centred in that boss — as the STL (small_step: --hub-skirt,
    2026-10-06; it built the hub a flange thickness low)."""
    if request.node.callspec.params['step_mesh'] == 'small_step':
        import app as A
        from exporters.step_worker_ss import _has_flag
        if not _has_flag(A._SS_BIN, '--hub-skirt'):
            pytest.skip(f'no small_step with --hub-skirt ({A._SS_BIN})')
    q = dict(hub_screw_size='M3', hub_screw_count=1, hub_screw_hold='thread',
             flange_enabled=1, flange_top_separate=0, **flange)
    import io
    import struct
    r = client.get('/download/stl', query_string={**BASE, **{k: str(v) for k, v in q.items()}})
    raw = r.data[:84 + 50 * struct.unpack('<I', r.data[80:84])[0]]      # the triangles, not the CCT tail
    stl = trimesh.load(io.BytesIO(raw), file_type='stl')
    step = step_mesh(**q)
    top = stl.bounds[1][2]
    assert step.bounds[1][2] == pytest.approx(top, abs=0.01)
    z = top - HUB_H / 2.0                                                # the boss's middle
    assert _hole_width(step, z, R_MID) == pytest.approx(_hole_width(stl, z, R_MID), abs=0.05)
