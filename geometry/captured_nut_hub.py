"""
captured_nut_hub.py — a captured nut in a hub too narrow for it (small_step's
gap 57 refusals; the small_step session, 2026-10-07).

A captured nut needs 2·t_nut of wall past its pocket. Two ways a hub falls
short, both refused by small_step by name, and both said here first with the
Hub OD that works:

1. Lobed, on a bore that isn't round. A hub of radius under bore_r + 3·t_nut
   is built with a lobe bulging out in each screw direction, and small_step
   0.8.1 builds that lobed hub's bore ROUND: before 0.8.1 a D-flat was
   silently left off the hub (a valid STEP, the wrong part), and a keyway gave
   an invalid one (fuzz #103, #109). 0.8.1 refuses a lobed hub on a D-flat,
   keyway or shaped (hex) bore. small_step's rule (ss-pulley `build_hub`):

       hub_r < min_hub_r < 2.9 · hub_r        min_hub_r = bore_r + 3 · t_nut

   Past 2.9 · hub_r the lobes would detach, and small_step grows the hub round
   to min_hub_r instead, which carries the bore's shape.

2. No wall round the screw. A keyway pushes the pocket out behind the slot,
   but min_hub_r measures the wall from the bore, so a deep key leaves the
   pocket through the hub wall (Ø9.9 bore, 6 × 3 key, #2-56 nut, Ø2.6 hole:
   round Ø19.6 and Ø20.2 INVALID, Ø21 valid). small_step refuses when the
   screw leaves the hub inside the pocket:

       off + sqrt(R² − r²) <= xo              xo = back_face + 0.01 + t_nut + 0.5

   R, off the hub's radius and centre (the lobe's, when lobed; min_hub_r when
   grown round), r the screw hole's reach (a hex hole's circumradius),
   back_face the key slot's outer face, else the bore (a D-flat measures from
   the bore in small_step).

The Auto-fix asks for the 2·t_nut wall the rule intends, measured from where
the pocket is: hub_od >= 2 · (back_face + 3 · t_nut), which is bore + 6·t_nut
without a keyway. bore_r is the inscribed radius for a hex bar (its across-
flats / 2), the bore everywhere else. The STL lobes the hub and cuts the
bore's shape through it, so case 1 is right there; case 2's pocket is through
its wall too, and the wider hub fixes both.
"""
from __future__ import annotations

import math
from typing import Optional

POCKET_KEEPOUT = 0.01        # small_step: the pocket's inner face this far past the back face
POCKET_CLEARANCE = 0.5       # small_step: the pocket's radial depth is t_nut + this
WELD_TOL = 1e-4              # small_step's weld tolerance


def _t_nut(screw_dia_mm, nut):
    from exporters.step_exporter import _nut_dims
    return (nut if nut else _nut_dims(screw_dia_mm))[1]


def _reach(screw_dia_mm, hole):
    """The screw hole's radius as small_step cuts it (a hex hole's circumradius)."""
    if hole and hole.get('shape') == 'hex' and hole.get('flats'):
        return float(hole['flats']) / math.sqrt(3)
    if hole and hole.get('diameter'):
        return float(hole['diameter']) / 2.0
    return screw_dia_mm / 2.0


def min_hub_od(*, bore_mm: float, screw_dia_mm: float, nut=None, keyway_h_mm: float = 0.0,
               hole: Optional[dict] = None) -> float:
    """The Hub OD the Auto-fix asks for: 2·t_nut of wall past the pocket's back
    face, and never under small_step's no-wall floor."""
    t = _t_nut(screw_dia_mm, nut)
    back = bore_mm / 2.0 + max(keyway_h_mm, 0.0)
    xo = back + POCKET_KEEPOUT + t + POCKET_CLEARANCE
    floor = 2.0 * math.hypot(xo, _reach(screw_dia_mm, hole)) + 0.01
    return max(2.0 * (back + 3.0 * t), bore_mm + 6.0 * t, floor)


def problems(*, bore_mm: float, hub_od_mm: float, hub_height_mm: float, screw_count: int,
             captured_nut: bool, screw_dia_mm: float, flat_depth_mm: float = 0.0,
             keyway_w_mm: float = 0.0, keyway_h_mm: float = 0.0, spline: Optional[dict] = None,
             nut=None, hole: Optional[dict] = None, who: str = '') -> list[str]:
    """Why STEP can't make this hub ([] if it can)."""
    if not (captured_nut and screw_count > 0 and screw_dia_mm > 0 and hub_od_mm > bore_mm
            and hub_height_mm > 0):
        return []
    has_keyway = keyway_w_mm > 0 and keyway_h_mm > 0
    kh = keyway_h_mm if has_keyway else 0.0
    t = _t_nut(screw_dia_mm, nut)
    hub_r, min_hub_r = hub_od_mm / 2.0, bore_mm / 2.0 + 3.0 * t
    need = min_hub_od(bore_mm=bore_mm, screw_dia_mm=screw_dia_mm, nut=nut, keyway_h_mm=kh, hole=hole)

    lobed = min_hub_r > hub_r + 1e-6 and min_hub_r - hub_r < 1.9 * hub_r
    what = ('keyway' if has_keyway else 'D-flat' if flat_depth_mm > 0
            else 'hex bore' if spline else None)
    if lobed and what:
        return [f"{who}a captured nut this size needs a Hub OD of at least Ø{need:.2f} mm; the "
                f"Ø{hub_od_mm:g} mm hub would be built with lobes, and STEP can't make a lobed hub "
                f"with a {what} yet — widen the Hub OD, or hold the screw by its thread or an "
                f"insert instead of a nut."]

    big_r, off = ((hub_r, min_hub_r - hub_r) if lobed else
                  (min_hub_r, 0.0) if min_hub_r > hub_r + 1e-6 else (hub_r, 0.0))
    r = _reach(screw_dia_mm, hole)
    xo = bore_mm / 2.0 + kh + POCKET_KEEPOUT + t + POCKET_CLEARANCE
    if off + math.sqrt(max(big_r * big_r - r * r, 0.0)) <= xo + WELD_TOL:
        why = ' (the key slot pushes it out)' if has_keyway else ''
        return [f"{who}the captured nut's pocket reaches {xo:.2f} mm from the axis{why} and leaves no "
                f"hub wall round its screw — widen the Hub OD to at least Ø{need:.2f} mm"
                + (", or use a shallower keyway" if has_keyway else "")
                + ", or hold the screw by its thread or an insert instead of a nut."]
    return []
