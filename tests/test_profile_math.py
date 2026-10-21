"""Small exact checks the negative controls showed were missing (tools/neg_controls.py,
2026-10-21): no test caught a wrong RPP pitch diameter, and only the whole suite caught
teeth-from-OD losing its pitch line offset."""
import math

import pytest

from geometry import pulley_geometry as pg
from geometry import spoke_fit


@pytest.mark.parametrize("name", ["R3M", "R5M", "R14M"])
@pytest.mark.parametrize("teeth", [12, 20, 40, 80])
def test_rpp_root_arc_is_round_the_pulleys_centre(name, teeth):
    """The RPP groove's root is an arc about the pulley's centre, which sits one OD
    radius (from the pitch diameter) below the groove's OD line: its points share
    one radius about that centre. A wrong pitch diameter bends the arc off it."""
    spec = pg.PULLEY_SPECS[name]
    r_od = pg.getOuterDiameter(teeth, spec["pitch"], spec["pitch_line_diff"]) / 2
    radii = [math.hypot(x, y + r_od) for x, y in pg.generate_rpp_groove(name, teeth).to_point_cloud()]
    deepest = min(radii)
    assert sum(1 for r in radii if r - deepest < 1e-6) >= 20


@pytest.mark.parametrize("key", ["5M", "GT2M", "S8M", "T5", "AT10", "XL", "R8M"])
def test_teeth_from_the_outside_diameter_round_trips(key):
    spec = pg.PULLEY_SPECS[key]
    p, off = spec["pitch"], spec["pitch_line_diff"]
    for n in (10, 17, 25, 60, 150):
        assert pg.getTeethFromOD(pg.getOuterDiameter(n, p, off), p, off) == n


def test_spokes_need_a_web_between_hub_and_rim():
    """The builder needs the rim's inside 0.5 mm clear of the hub (spoke_fit.MIN_WEB; the
    void builder refuses it too, so this holds with either check)."""
    s = {"hub_od": 20.0, "count": 4, "width": 3.0, "fillet_tip": 0.0, "fillet_base": 0.0}
    r_root = 30.0
    assert spoke_fit._valid(r_root, 5.0, dict(s, rim_depth=r_root - 10.0 - 2.0))      # a 2 mm web
    assert not spoke_fit._valid(r_root, 5.0, dict(s, rim_depth=r_root - 10.0 - 0.3))  # 0.3 mm
