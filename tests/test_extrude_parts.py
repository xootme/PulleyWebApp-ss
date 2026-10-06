"""test_extrude_parts.py — the assembly's own parts made by small_step's
`extrude` (exporters/assembly.py::extrude_step): the retaining ring free and
fitted, the splined washer, the sample shaft.

Needs a small_step that can extrude (SMALL_STEP_BIN, or bin/small_step_linux on
Linux); skipped otherwise — there is no Windows build with it yet. With OCP
it also reads each STEP: one valid solid, its volume the outline's area times
its thickness."""
import math

import pytest

from cct_common import retaining_rings as rr
from cct_common import ring_outline as ro
from exporters import assembly as asm

BIN = asm.ss_binary()
pytestmark = pytest.mark.skipif(not asm.can_extrude(BIN),
                                reason=f'no small_step with extrude ({BIN or "none found"})')

SPLINE = dict(family='HTD', pitch='5M', teeth='40', bore='8', belt_height='10', feature_build='1',
              bore_shape='spline', spline_type='involute', spline_m='1.5', spline_z='16', spline_pa='30',
              spline_root='flat', spline_ring_top='1', spline_ring_bottom='1', spline_washer='1',
              hub_od='40', hub_height='10')


def _pts(loop, step=0.1):
    out = []
    for s in loop:
        if s[0] == 'line':
            out.append(tuple(s[1]))
        else:
            _, (cx, cy), r, a0, a1, ccw = s
            sw = ro._sweep(a0, a1, ccw)
            k = max(2, int(math.ceil(abs(sw) / step)))
            out += [(cx + r * math.cos(math.radians(a0 + sw * i / k)), cy + r * math.sin(math.radians(a0 + sw * i / k)))
                    for i in range(k)]
    return out


def _want_volume(spec):
    from shapely.geometry import Polygon
    area = lambda loops: Polygon(_pts(loops[0]), [_pts(h) for h in loops[1:]]).area   # noqa: E731
    if 'sections' in spec:
        return sum(area(s['loops']) * (s['z1'] - s['z0']) for s in spec['sections'])
    return area(spec['loops']) * spec['thickness']


def _check(spec):
    data = asm.extrude_step(spec, BIN)
    assert data.startswith(b'ISO-10303-21') and data.count(b'MANIFOLD_SOLID_BREP') == 1
    try:
        from test_step_small_step import _solids
        import OCP  # noqa: F401
    except ImportError:
        return                                       # Linux without OCP: the file is there; volume unchecked
    n, ok, vol = _solids(data)
    assert n == 1 and ok
    assert vol == pytest.approx(_want_volume(spec), rel=1e-4)


@pytest.mark.parametrize('d1', [3, 10, 26, 100])
@pytest.mark.parametrize('state', ['free', 'installed'])
def test_a_ring(d1, state):
    ring = rr.get(d1)
    _check((ro.outline if state == 'free' else ro.installed)(ring).as_dict())


def test_the_washer_and_the_shaft_as_one_piece(client):
    m = asm.manifest(SPLINE)
    _check(next(p for p in m['parts'] if p['name'].endswith('washer'))['extrude'])
    sections = next(p for p in m['parts'] if p['name'].endswith('shaft'))['extrude']['sections']
    _check({'name': 'shaft', 'thickness': sections[-1]['z1'] - sections[0]['z0'], 'loops': sections[0]['loops']})


@pytest.mark.xfail(strict=True, reason='small_step extrude takes no stacked sections yet (handoff item 1, '
                                       'added 2026-10-06): the grooved shaft waits on them')
def test_the_grooved_shaft(client):
    m = asm.manifest(SPLINE)
    _check(next(p for p in m['parts'] if p['name'].endswith('shaft'))['extrude'])


def test_an_extrude_failure_says_so():
    with pytest.raises(asm.ExtrudeError, match='open-loop'):
        asm.extrude_step({'name': 'open-loop', 'thickness': 1.0,
                          'loops': [[['line', [0, 0], [1, 0]], ['line', [1, 0], [1, 1]]]]}, BIN)
