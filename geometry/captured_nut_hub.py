"""
captured_nut_hub.py — a captured nut in a hub too narrow for it, on a bore that
isn't round (small_step 0.8.1's gap 57 refusal; the small_step session,
2026-10-07).

A captured nut needs 2·t_nut of wall past its pocket, so a hub of radius at
least bore_r + 3·t_nut. A narrower hub is built with a lobe bulging out in each
screw direction. small_step builds that lobed hub's bore ROUND whatever was
asked: before 0.8.1 a D-flat was silently left off the hub (a valid STEP, the
wrong part), and a keyway gave an invalid one (fuzz #103, #109). 0.8.1 refuses
the lobed hub on a D-flat, a keyway or a shaped (hex) bore, by name. Here the
app says so first, with the hub diameter that works.

The rule is small_step's exactly (ss-pulley `build_hub`): refused when

    hub_r < min_hub_r < 2.9 · hub_r        min_hub_r = bore_r + 3 · t_nut

Past 2.9 · hub_r the lobes would detach, and small_step grows the hub round to
min_hub_r instead, which carries the bore's shape. bore_r is the inscribed
radius for a hex bar (its across-flats / 2), the bore everywhere else.

The STL lobes the hub too and cuts the bore's shape through it, so it is right
as it is; only the STEP can't be made.
"""
from __future__ import annotations

from typing import Optional


def min_hub_od(*, bore_mm: float, screw_dia_mm: float, nut=None) -> float:
    """The narrowest hub (diameter) that holds a captured nut without lobes."""
    from exporters.step_exporter import _nut_dims
    t_nut = (nut if nut else _nut_dims(screw_dia_mm))[1]
    return bore_mm + 6.0 * t_nut


def problems(*, bore_mm: float, hub_od_mm: float, hub_height_mm: float, screw_count: int,
             captured_nut: bool, screw_dia_mm: float, flat_depth_mm: float = 0.0,
             keyway_w_mm: float = 0.0, keyway_h_mm: float = 0.0, spline: Optional[dict] = None,
             nut=None, who: str = '') -> list[str]:
    """Why STEP can't make this hub ([] if it can)."""
    if not (captured_nut and screw_count > 0 and screw_dia_mm > 0 and hub_od_mm > bore_mm
            and hub_height_mm > 0):
        return []
    has_keyway = keyway_w_mm > 0 and keyway_h_mm > 0
    what = ('keyway' if has_keyway else 'D-flat' if flat_depth_mm > 0
            else 'hex bore' if spline else None)
    if what is None:
        return []
    need = min_hub_od(bore_mm=bore_mm, screw_dia_mm=screw_dia_mm, nut=nut)
    if not (hub_od_mm < need - 2e-6 and need < 2.9 * hub_od_mm):
        return []
    return [f"{who}a captured nut this size needs a Hub OD of at least Ø{need:.2f} mm; the "
            f"Ø{hub_od_mm:g} mm hub would be built with lobes, and STEP can't make a lobed hub "
            f"with a {what} yet — widen the Hub OD, or hold the screw by its thread or an "
            f"insert instead of a nut."]
