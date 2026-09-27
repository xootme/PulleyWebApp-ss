"""
test_print_compensation.py — 3D Print Compensation is a true surface offset.

Every surface of the tooth outline (lands, tooth tips, groove walls and floor)
moves the full compensation value into the material, measured perpendicular
to the surface (generate_profile_groove -> _offset_groove_outline). Before
this, the generators moved the tips by half the value and the groove floor by
the full value on some profiles.
"""
import math

import pytest
from shapely.geometry import LineString, Point

from geometry.pulley_geometry import (
    Arc, PULLEY_SPECS, _build_groove_points, generate_htd_groove,
    generate_imperial_groove, generate_profile_groove, getOuterDiameter,
    pulley_outline_segments, wrap_groove_to_pulley,
)

PE = 0.2
# (family, key, teeth, radial clearance to pass)
PROFILES = [
    ('Imperial', 'L', 20, None), ('Imperial', 'MXL', 12, None),
    ('HTD', '5M', 40, 0.0), ('GT', 'GT2M', 20, 0.0), ('STD', 'S5M', 30, 0.0),
    ('T', 'T5', 20, None), ('AT', 'AT5', 20, None), ('RPP', 'R8M', 30, None),
]


def _flat(container):
    """The container's outline (lands included) as a dense point list."""
    pts = []
    for prim in container.primitives:
        seg = ([p.to_tuple() for p in prim.to_points(48)] if isinstance(prim, Arc)
               else [prim.p1.to_tuple(), prim.p2.to_tuple()])
        pts.extend(seg if not pts else seg[1:])
    return pts


def _wrapped_radii(family, key, n, cl, pe):
    spec = PULLEY_SPECS[key]
    c = generate_profile_groove(family, key, n, cl, pe, 0.0)
    g = _build_groove_points(c.primitives[1:-1], family)
    wrapped, r_od, _ = wrap_groove_to_pulley(g, spec, n, pe)
    r = [math.hypot(x, y) for x, y in wrapped]
    return r_od, min(r), max(r)


@pytest.mark.parametrize('family,key,n,cl', PROFILES)
def test_every_point_moves_the_full_value(family, key, n, cl):
    """Each point of the compensated outline is exactly PE from the nominal one."""
    nominal = LineString(_flat(generate_profile_groove(family, key, n, cl, 0.0, 0.0)))
    offset = _flat(generate_profile_groove(family, key, n, cl, PE, 0.0))
    d = [nominal.distance(Point(p)) for p in offset]
    assert min(d) == pytest.approx(PE, abs=2e-4)
    assert max(d) == pytest.approx(PE, abs=2e-4)


@pytest.mark.parametrize('family,key,n,cl', PROFILES)
def test_tips_and_groove_bottom_both_move_the_full_value(family, key, n, cl):
    r_od0, bottom0, tip0 = _wrapped_radii(family, key, n, cl, 0.0)
    r_od1, bottom1, tip1 = _wrapped_radii(family, key, n, cl, PE)
    assert r_od0 - r_od1 == pytest.approx(PE, abs=1e-6)          # outer radius
    assert tip0 - tip1 == pytest.approx(PE, abs=2e-3)             # tooth tips
    assert bottom0 - bottom1 == pytest.approx(PE, abs=2e-3)       # groove floor


@pytest.mark.parametrize('family,key,n,cl', PROFILES)
def test_zero_compensation_is_the_generators_own_outline(family, key, n, cl):
    """With no compensation the offset step is skipped entirely."""
    c = generate_profile_groove(family, key, n, cl, 0.0, 0.0)
    assert c.print_extra == 0.0
    if family in ('Imperial', 'T', 'AT'):
        direct = generate_imperial_groove(key, n, cl, 0.0, 0.0)
    elif family == 'HTD':
        direct = generate_htd_groove(key, n, cl, 0.0, 0.0)
    else:
        return
    assert _flat(c) == _flat(direct)


@pytest.mark.parametrize('family,key,n,cl', PROFILES)
def test_outline_agrees_with_the_other_od_calculations(family, key, n, cl):
    """Flanges, hub/rim features and the dual layout compute the OD as
    getOuterDiameter(n, pitch, pld + print_extra - clearance): a full-value
    shrink per surface. The tooth outline now matches them."""
    spec = PULLEY_SPECS[key]
    fam_pitch ={'Imperial': key, 'T': key, 'AT': key, 'GT': key[2:], 'STD': key[1:],
                 'RPP': key[1:], 'HTD': key}[family]
    _, r_od, _, _ = pulley_outline_segments(family, fam_pitch, n, cl or 0.0, 0.0, PE)
    expected = getOuterDiameter(n, spec['pitch'], spec['pitch_line_diff'] + PE) / 2.0
    assert r_od == pytest.approx(expected, abs=1e-9)


def test_land_sits_at_minus_the_value_in_the_flat_frame():
    c = generate_profile_groove('HTD', '5M', 40, 0.0, PE, 0.0)
    left, right = c.primitives[0], c.primitives[-1]
    for line in (left, right):
        assert line.p1.y == pytest.approx(-PE) and line.p2.y == pytest.approx(-PE)
