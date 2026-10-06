"""
nut_counterbore.py — a captured nut and a retaining ring's counterbore on the
same hub face (the small_step session's handoff, 2026-10-06; Sprocket's rule 1).

The nut's pocket opens from the hub's top face, and a ring's top counterbore is
cut into that same face. Two independent clauses must hold:

  in plan   hypot(xo, half_y) <= cb_d / 2       the pocket's far corner is inside
                                                 the counterbore, or the counterbore's
                                                 lip roofs the pocket over and the nut
                                                 can't be fitted (a bad part);
  in z      cb_depth <= 0.75 * r_pkt - r_screw  the screw's stub stays below the
                                                 counterbore's floor.

with xi the pocket's inner face (the bore, the D-flat or the key's / spline slot's
face, as step_exporter places it), xo = xi + t_nut + 0.5, half_y = (af + 0.5)/2,
r_pkt = (af + 0.5)/sqrt(3), and r_screw the screw hole's radius (a hex hole's
circumradius) — the nominal hole when none is given, which is what small_step
cuts for Pulley (its worker sends no --screw-hole); the printed part's
clearance hole is a few tenths wider, which moves the z limit by as much and
breaks nothing a print would notice. Measured on Pulley (hub Ø34 x 18 over a Ø10 bore, M4 captured
nut): every top counterbore gave an invalid STEP before small_step 0.7.0, which
now builds the conforming case and refuses the rest by name. Here the app
says so first, with the number that is wrong and what to change.

Only the top face: the nut pocket opens there (a hub is on top). The bottom
counterbore is on the pulley's other face.
"""
from __future__ import annotations

import math
from typing import Optional


def problems(*, bore_mm: float, hub_od_mm: float, hub_height_mm: float, screw_count: int,
             captured_nut: bool, screw_dia_mm: float, spline: Optional[dict],
             flat_depth_mm: float = 0.0, keyway_h_mm: float = 0.0,
             nut=None, hole: Optional[dict] = None, who: str = '') -> list[str]:
    """The clauses this design breaks, as messages the user can act on ([] if none)."""
    rt = (spline or {}).get('retainer') or {}
    faces = rt.get('cb_faces', rt.get('faces')) or []
    cb_d, cb_depth = float(rt.get('cb_d') or 0.0), float(rt.get('cb_depth') or 0.0)
    if not (captured_nut and screw_count > 0 and screw_dia_mm > 0 and hub_od_mm > bore_mm
            and hub_height_mm > 0 and 'top' in faces and cb_d > 0 and cb_depth > 0):
        return []
    from exporters.step_exporter import _nut_dims, _spline_slot_h
    af, t_nut = nut if nut else _nut_dims(screw_dia_mm)
    hole = hole or {'shape': 'circle', 'diameter': screw_dia_mm}
    r_screw = hole['flats'] / math.sqrt(3) if hole.get('shape') == 'hex' else hole['diameter'] / 2.0

    r_bore = bore_mm / 2.0
    # a hex bar's flat is on the bore's radius (no slot); a spline's slot is a key's face
    slot = keyway_h_mm or (0.0 if not spline or spline.get('kind') == 'hex'
                           else _spline_slot_h(spline, bore_mm))
    xi = r_bore - flat_depth_mm if flat_depth_mm > 0 else r_bore + slot if slot > 0 else r_bore
    xo = xi + t_nut + 0.5
    half_y = (af + 0.5) / 2.0
    corner = math.hypot(xo, half_y)
    r_pkt = (af + 0.5) / math.sqrt(3)
    budget = 0.75 * r_pkt - r_screw

    out = []
    if corner > cb_d / 2.0 + 1e-9:
        out.append(f"{who}the retaining ring's top counterbore is Ø{cb_d:.2f} mm and the captured nut's "
                   f"pocket reaches Ø{2 * corner:.2f} mm across, so the counterbore's lip would roof over "
                   f"the pocket and the nut couldn't be fitted — untick Make counterbore on the top face, "
                   f"or hold the screw by its thread or an insert instead of a nut.")
    if cb_depth > budget + 1e-9:
        out.append(f"{who}the retaining ring's top counterbore is {cb_depth:.2f} mm deep and the captured "
                   f"nut's screw leaves room for no more than {max(budget, 0.0):.2f} mm before the screw "
                   f"breaks into it — untick Make counterbore on the top face, or use a smaller screw.")
    return out
