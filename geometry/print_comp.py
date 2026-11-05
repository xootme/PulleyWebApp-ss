"""print_comp.py — how much 3D Print Compensation a pulley's teeth can take.

The owner's rule for every tool with teeth (2026-10-31, Controller's campaign
2026-10-31-print-compensation): 0 to 1.00 mm (cct_common's COMPENSATION_MIN/MAX_MM), and
each tooth keeps at least half its width. On a pulley the tooth is measured half-way up its
height, between the OD and the groove floor (the owner's choice, 2026-11-02: the tips are
fully rounded, so their flat land is nearly nothing and a cap there would always be 0). The
compensation moves the outline comp mm in, so the tooth loses about 2 x comp there (GT2 20T:
0.851 mm -> 0.663 mm at 0.1): the cap is a quarter of that width, on the nominal outline at
the design's own tooth count, rounded down to 0.01 by clamp_compensation and never above 1.00.
GT2 10T 0.18, HTD 5M 12T 0.40; 14M and up 1.00."""
from __future__ import annotations

import math
from functools import lru_cache

from cct_common.printer_settings import COMPENSATION_MAX_MM, clamp_compensation


@lru_cache(maxsize=1024)
def tooth_width_mid(family: str, pitch: str, teeth: int) -> float:
    """A typical tooth's width (mm, along the circle) half-way between the OD and the groove
    floor, on the app's own outline with no compensation."""
    from shapely.geometry import LineString, Point, Polygon
    from exporters.step_exporter import _build_outline_points, _profile_key
    from geometry.pulley_geometry import PULLEY_SPECS
    spec = PULLEY_SPECS[_profile_key(family, pitch)]
    pts = _build_outline_points(family, pitch, int(teeth))[0]
    r_od = max(math.hypot(x, y) for x, y in pts)
    r = r_od - 1e-4 - 0.5 * spec['tooth_ht']
    circle = LineString(Point(0, 0).buffer(r, resolution=4096).exterior.coords)
    inter = Polygon(pts).buffer(0).intersection(circle)
    arcs = sorted(s.length for s in getattr(inter, 'geoms', [inter]) if s.length > 1e-6)
    return arcs[len(arcs) // 2] if arcs else 0.0


def cap(family: str, pitch: str, teeth: int) -> float:
    """The most compensation these teeth can take (mm): a quarter of the tooth's width at
    half its height, on the 0.01 grid, at most COMPENSATION_MAX_MM."""
    return clamp_compensation(COMPENSATION_MAX_MM, cap=tooth_width_mid(family, pitch, int(teeth)) / 4.0)
