"""test_assembly_manifest.py — the assembly STEP's manifest (exporters/assembly.py):
which parts, where they go, and outlines small_step's `extrude` can take
(Documents\\CCT_Assembly_STEP_Handoff.md). Checked against the standard's own
numbers, not the code's."""
import json
import math

import pytest
from shapely.geometry import Point, Polygon

from cct_common import retaining_rings as rr
from cct_common import ring_outline as ro
from cct_common import splines as spl
from exporters import assembly as asm

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '30', 'bore': '8', 'belt_height': '10', 'feature_build': '1'}
SPLINE = {'teeth': '40', 'bore_shape': 'spline', 'spline_type': 'involute', 'spline_m': '1.5', 'spline_z': '16',
          'spline_pa': '30', 'spline_root': 'flat', 'spline_ring_top': '1', 'spline_ring_bottom': '1',
          'spline_washer': '1', 'hub_od': '40', 'hub_height': '10'}
DRIVE = {'dual': 'true', 'p2_teeth': '40', 'p2_bore': '8', 'center_distance': '120'}


def _pt(seg, end):
    if seg[0] == 'line':
        return seg[2] if end else seg[1]
    _, (cx, cy), r, a0, a1, _ = seg
    a = a1 if end else a0
    return (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))


def _points(loop, step=0.5):
    """A JSON loop as points (arcs sampled)."""
    pts = []
    for s in loop:
        if s[0] == 'line':
            pts.append(tuple(s[1]))
        else:
            _, (cx, cy), r, a0, a1, ccw = s
            sw = ro._sweep(a0, a1, ccw)
            k = max(2, int(abs(sw) / step))
            pts += [(cx + r * math.cos(math.radians(a0 + sw * i / k)), cy + r * math.sin(math.radians(a0 + sw * i / k)))
                    for i in range(k)]
    return pts


def _area(pts):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / 2


def _check_loops(loops):
    for i, loop in enumerate(loops):
        for s0, s1 in zip(loop, loop[1:] + loop[:1]):
            assert math.dist(_pt(s0, True), _pt(s1, False)) < 1e-7, (i, s0, s1)
        assert (_area(_points(loop)) > 0) == (i == 0), f'loop {i} runs the wrong way round'


def _part(m, name):
    return next(p for p in m['parts'] if p['name'] == name)


def _all_loops(part):
    e = part.get('extrude')
    if not e:
        return []
    return [s['loops'] for s in e['sections']] if 'sections' in e else [e['loops']]


# ── pulleys and belt ─────────────────────────────────────────────────────────

def test_one_pulley_is_one_part_at_the_origin(client):
    m = asm.manifest(BASE)
    assert m == {'name': 'HTD-5M-30T', 'parts': [{'name': 'HTD-5M-30T', 'make': {'kind': 'pulley', 'pulley': 1},
                                                    'placement': {'origin': [0.0, 0.0, 0.0], 'rotate_z_deg': 0.0},
                                                    'color': '#4A9FD4'}]}


def test_a_drive_places_pulley_2_and_the_belt_as_the_merged_step_did(client):
    from geometry.pulley_geometry import build_two_pulley_belt
    m = asm.manifest({**BASE, **DRIVE})
    assert m['name'] == 'HTD-5M-30T-40T'
    assert [p['name'] for p in m['parts']] == ['HTD-5M-30T', 'HTD-5M-40T-P2', 'HTD-5M-belt']
    _, _, phi_l, phi_r = build_two_pulley_belt('HTD', '5M', 30, 40, 120.0, x_offset=0.0)
    p1, p2 = _part(m, 'HTD-5M-30T')['placement'], _part(m, 'HTD-5M-40T-P2')['placement']
    assert p1 == {'origin': [0.0, 0.0, 0.0], 'rotate_z_deg': pytest.approx(-math.degrees(phi_l))}
    assert p2['origin'] == [120.0, 0.0, 0.0] and p2['rotate_z_deg'] == pytest.approx(-math.degrees(phi_r))
    assert _part(m, 'HTD-5M-belt')['make'] == {'kind': 'belt'}


# ── a splined bore's parts ───────────────────────────────────────────────────

def _spline_design(client):
    import app as A
    q = {**BASE, **SPLINE}
    sp = A._spline_of(q, '')
    ring = rr.for_spline(A._as_spline_obj(sp))
    bare = A._pulley_stl(dict(q, pulley='1'))[3]
    lo, hi = A._ring_face_z(q, '', *bare)
    return q, sp, ring, lo, hi


def test_the_rings_washers_and_shaft_sit_where_the_standard_puts_them(client):
    """Counterbores on both faces, a washer under each ring: per DIN 471 and
    retaining_rings.counterbore, the ring's outer face is flush with the part's
    face and the washer lies under it, the groove's outer wall on the face."""
    q, sp, ring, lo, hi = _spline_design(client)
    m = asm.manifest(q)
    names = [p['name'] for p in m['parts']]
    assert names == ['HTD-5M-40T', 'HTD-5M-40T-spline-shaft', 'HTD-5M-40T-spline-washer', ring.label()]
    w = rr.WASHER_THICKNESS
    rings = _part(m, ring.label())
    zr = sorted([rings['placement']['origin'][2]] + [i['origin'][2] for i in rings['instances']])
    # top: the ring from the face down m (its groove's width) — s thick, at the groove's inner wall
    # bottom (mirrored): its outer face m - s ... flush means it ends at lo, starts at lo + m - s
    assert zr == pytest.approx([lo + ring.m - ring.s, hi - ring.m])
    assert rings['extrude'] == ro.installed(ring).as_dict()
    washers = _part(m, 'HTD-5M-40T-spline-washer')
    zw = sorted([washers['placement']['origin'][2]] + [i['origin'][2] for i in washers['instances']])
    assert zw == pytest.approx([lo + ring.m, hi - ring.m - w])
    assert washers['extrude']['thickness'] == w

    shaft = _part(m, 'HTD-5M-40T-spline-shaft')['extrude']['sections']
    for a, b in zip(shaft, shaft[1:]):
        assert a['z1'] == pytest.approx(b['z0'])                              # one solid, no gaps
    assert shaft[0]['z0'] < lo and shaft[-1]['z1'] > hi
    grooves = [s for s in shaft if s['groove']]
    assert len(grooves) == 2
    for g, ring_z in zip(grooves, zr):
        assert g['z1'] - g['z0'] == pytest.approx(ring.m)                     # the groove is m wide
        assert g['z0'] - 1e-9 <= ring_z and ring_z + ring.s <= g['z1'] + 1e-9   # the ring in it
        r_max = max(math.hypot(*p) for p in _points(g['loops'][0]))
        assert r_max <= ring.d2 / 2 + 1e-6                                     # under the ring's inside


def test_every_outline_is_closed_and_runs_the_right_way(client):
    q, *_ = _spline_design(client)
    m = asm.manifest(q)
    for part in m['parts']:
        for loops in _all_loops(part):
            _check_loops(loops)
    json.dumps(m)


def test_the_same_ring_on_both_pulleys_is_one_part():
    q = {**BASE, **SPLINE, **DRIVE, 'p2_teeth': '40', 'p2_bore_shape': 'spline', 'p2_spline_type': 'involute',
         'p2_spline_m': '1.5', 'p2_spline_z': '16', 'p2_spline_pa': '30', 'p2_spline_root': 'flat',
         'p2_spline_ring_top': '1', 'p2_hub_od': '40', 'p2_hub_height': '10'}
    m = asm.manifest(q)
    rings = [p for p in m['parts'] if p['name'].startswith('DIN 471')]
    assert len(rings) == 1
    xs = sorted([rings[0]['placement']['origin'][0]] + [i['origin'][0] for i in rings[0]['instances']])
    assert xs == [0.0, 0.0, 120.0]                                             # 2 on pulley 1, 1 on pulley 2


# ── clipping an outline to a circle ──────────────────────────────────────────

@pytest.mark.parametrize('sp', [spl.involute(1.5, 16, 30, 'flat'), spl.involute(2.0, 12, 30, 'flat'),
                                spl.straight(6, 23, 26, 6)], ids=['inv 1.5x16', 'inv 2x12', 'straight 6x23x26'])
@pytest.mark.parametrize('frac', [0.3, 0.6, 0.9])
def test_clip_to_circle_is_exact(sp, frac):
    shaft = spl.shaft_path(sp)
    prof = spl.shaft(sp)
    r = prof.inner / 2 + frac * (prof.outer - prof.inner) / 2
    clipped = asm.loop_json(asm.clip_to_circle(shaft, r))
    _check_loops([clipped])
    want = Polygon(spl.sample_closed(shaft, 0.01)).intersection(Point(0, 0).buffer(r, quad_segs=512))
    assert Polygon(_points(clipped, 0.1)).area == pytest.approx(want.area, rel=2e-4)


def test_clip_wholly_inside_or_outside():
    sp = spl.involute(1.5, 16, 30, 'flat')
    shaft, prof = spl.shaft_path(sp), spl.shaft(sp)
    assert asm.clip_to_circle(shaft, prof.outer) == shaft                  # nothing to cut
    assert len(asm.clip_to_circle(shaft, prof.inner / 2 * 0.5)) == 1        # just the circle
