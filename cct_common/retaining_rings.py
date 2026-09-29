"""
retaining_rings.py — external retaining rings (DIN 471 circlips) and the
face recess a part needs for one, shared by the CCT tools for any part with a
splined bore (Timing Pulleys first; sprockets and gears to follow).

    from cct_common import retaining_rings as rr

    ring = rr.for_shaft(26.0)          # DIN 471 26 x 1.2: groove Ø24.9, width 1.3
    cb = rr.counterbore(ring)          # recess Ø35.5 x 1.3 deep in the part's face
    cb = rr.counterbore(ring, washer=1.5)   # a washer under the ring: 2.8 deep

The ring clips into a groove round the shaft next to the part. The part's
face gets a **counterbore** — a short, wider hole at the mouth of the bore —
so the ring (and a washer under it) sits in the part, flush with its face; the
counterbore's floor is the step the ring holds the part by.

Sizes: DIN 471:2011 Table 1, normal type, 3-135 mm (the standard's own table,
in the project's PDF folder). McMaster-Carr part numbers where their catalogue
lists the ring as DIN 471 (checked 2026-09-29: every listed ring's groove
diameter, groove width, ring ID and thickness match the table).

A splined shaft's ring is sized to the spline's outside diameter: the groove
then cuts the tooth tops only and the ring slides on exactly as designed. (A
ring small enough for its groove to run round the spline's roots would have to
open over the teeth well past its design spread.)
"""
from __future__ import annotations

from dataclasses import dataclass

SOURCE = "DIN 471:2011 Table 1 (normal type)"


@dataclass(frozen=True)
class Ring:
    d1: float        # shaft diameter the ring is made for
    s: float         # ring thickness
    d3: float        # ring internal diameter, unstressed
    a: float         # radial width of the lug
    d2: float        # groove diameter
    m: float         # groove width (H13)
    t: float         # groove depth, (d1 - d2) / 2
    n: float         # edge margin: minimum shaft beyond the groove
    d4: float        # clearance diameter the ring needs while it's fitted (d1 + 2.1 a)
    mcmaster: str = ""   # McMaster-Carr part number, where listed

    def label(self) -> str:
        return f"DIN 471 {self.d1:g} × {self.s:g}"


# d1, s, d3, a, d2, m, t, n, d4 — DIN 471:2011 Table 1, normal type, as printed.
# d4 = d1 + 2.1a to DIN's rounding, except 56 and 58 where DIN prints d4 0.27 mm
# larger than a = 7.3 gives; the printed d4 (the larger counterbore) is kept.
_TABLE = [
    (3, 0.4, 2.7, 1.9, 2.8, 0.5, 0.10, 0.3, 7.0),
    (4, 0.4, 3.7, 2.2, 3.8, 0.5, 0.10, 0.3, 8.6),
    (5, 0.6, 4.7, 2.5, 4.8, 0.7, 0.10, 0.3, 10.3),
    (6, 0.7, 5.6, 2.7, 5.7, 0.8, 0.15, 0.5, 11.7),
    (7, 0.8, 6.5, 3.1, 6.7, 0.9, 0.15, 0.5, 13.5),
    (8, 0.8, 7.4, 3.2, 7.6, 0.9, 0.20, 0.6, 14.7),
    (9, 1.0, 8.4, 3.3, 8.6, 1.1, 0.20, 0.6, 16.0),
    (10, 1.0, 9.3, 3.3, 9.6, 1.1, 0.20, 0.6, 17.0),
    (11, 1.0, 10.2, 3.3, 10.5, 1.1, 0.25, 0.8, 18.0),
    (12, 1.0, 11.0, 3.3, 11.5, 1.1, 0.25, 0.8, 19.0),
    (13, 1.0, 11.9, 3.4, 12.4, 1.1, 0.30, 0.9, 20.2),
    (14, 1.0, 12.9, 3.5, 13.4, 1.1, 0.30, 0.9, 21.4),
    (15, 1.0, 13.8, 3.6, 14.3, 1.1, 0.35, 1.1, 22.6),
    (16, 1.0, 14.7, 3.7, 15.2, 1.1, 0.40, 1.2, 23.8),
    (17, 1.0, 15.7, 3.8, 16.2, 1.1, 0.40, 1.2, 25.0),
    (18, 1.2, 16.5, 3.9, 17.0, 1.3, 0.50, 1.5, 26.2),
    (19, 1.2, 17.5, 3.9, 18.0, 1.3, 0.50, 1.5, 27.2),
    (20, 1.2, 18.5, 4.0, 19.0, 1.3, 0.50, 1.5, 28.4),
    (21, 1.2, 19.5, 4.1, 20.0, 1.3, 0.50, 1.5, 29.6),
    (22, 1.2, 20.5, 4.2, 21.0, 1.3, 0.50, 1.5, 30.8),
    (24, 1.2, 22.2, 4.4, 22.9, 1.3, 0.55, 1.7, 33.2),
    (25, 1.2, 23.2, 4.4, 23.9, 1.3, 0.55, 1.7, 34.2),
    (26, 1.2, 24.2, 4.5, 24.9, 1.3, 0.55, 1.7, 35.5),
    (28, 1.5, 25.9, 4.7, 26.6, 1.6, 0.70, 2.1, 37.9),
    (29, 1.5, 26.9, 4.8, 27.6, 1.6, 0.70, 2.1, 39.1),
    (30, 1.5, 27.9, 5.0, 28.6, 1.6, 0.70, 2.1, 40.5),
    (32, 1.5, 29.6, 5.2, 30.3, 1.6, 0.85, 2.6, 43.0),
    (34, 1.5, 31.5, 5.4, 32.3, 1.6, 0.85, 2.6, 45.4),
    (35, 1.5, 32.2, 5.6, 33.0, 1.6, 1.00, 3.0, 46.8),
    (36, 1.75, 33.2, 5.6, 34.0, 1.85, 1.00, 3.0, 47.8),
    (38, 1.75, 35.2, 5.8, 36.0, 1.85, 1.00, 3.0, 50.2),
    (40, 1.75, 36.5, 6.0, 37.5, 1.85, 1.25, 3.8, 52.6),
    (42, 1.75, 38.5, 6.5, 39.5, 1.85, 1.25, 3.8, 55.7),
    (45, 1.75, 41.5, 6.7, 42.5, 1.85, 1.25, 3.8, 59.1),
    (48, 1.75, 44.5, 6.9, 45.5, 1.85, 1.25, 3.8, 62.5),
    (50, 2.0, 45.8, 6.9, 47.0, 2.15, 1.50, 4.5, 64.5),
    (52, 2.0, 47.8, 7.0, 49.0, 2.15, 1.50, 4.5, 66.7),
    (55, 2.0, 50.8, 7.2, 52.0, 2.15, 1.50, 4.5, 70.2),
    (56, 2.0, 51.8, 7.3, 53.0, 2.15, 1.50, 4.5, 71.6),
    (58, 2.0, 53.8, 7.3, 55.0, 2.15, 1.50, 4.5, 73.6),
    (60, 2.0, 55.8, 7.4, 57.0, 2.15, 1.50, 4.5, 75.6),
    (62, 2.0, 57.8, 7.5, 59.0, 2.15, 1.50, 4.5, 77.8),
    (63, 2.0, 58.8, 7.6, 60.0, 2.15, 1.50, 4.5, 79.0),
    (65, 2.5, 60.8, 7.8, 62.0, 2.65, 1.50, 4.5, 81.4),
    (68, 2.5, 63.5, 8.0, 65.0, 2.65, 1.50, 4.5, 84.8),
    (70, 2.5, 65.5, 8.1, 67.0, 2.65, 1.50, 4.5, 87.0),
    (72, 2.5, 67.5, 8.2, 69.0, 2.65, 1.50, 4.5, 89.2),
    (75, 2.5, 70.5, 8.4, 72.0, 2.65, 1.50, 4.5, 92.7),
    (78, 2.5, 73.5, 8.6, 75.0, 2.65, 1.50, 4.5, 96.1),
    (80, 2.5, 74.5, 8.6, 76.5, 2.65, 1.75, 5.3, 98.1),
    (82, 2.5, 76.5, 8.7, 78.5, 2.65, 1.75, 5.3, 100.3),
    (85, 3.0, 79.5, 8.7, 81.5, 3.15, 1.75, 5.3, 103.3),
    (88, 3.0, 82.5, 8.8, 84.5, 3.15, 1.75, 5.3, 106.5),
    (90, 3.0, 84.5, 8.8, 86.5, 3.15, 1.75, 5.3, 108.5),
    (95, 3.0, 89.5, 9.4, 91.5, 3.15, 1.75, 5.3, 114.8),
    (100, 3.0, 94.5, 9.6, 96.5, 3.15, 1.75, 5.3, 120.2),
    (105, 4.0, 98.0, 9.9, 101.0, 4.15, 2.00, 6.0, 125.8),
    (110, 4.0, 103.0, 10.1, 106.0, 4.15, 2.00, 6.0, 131.2),
    (115, 4.0, 108.0, 10.6, 111.0, 4.15, 2.00, 6.0, 137.3),
    (120, 4.0, 113.0, 11.0, 116.0, 4.15, 2.00, 6.0, 143.1),
    (125, 4.0, 118.0, 11.4, 121.0, 4.15, 2.00, 6.0, 149.0),
    (130, 4.0, 123.0, 11.6, 126.0, 4.15, 2.00, 6.0, 154.4),
    (135, 4.0, 128.0, 11.8, 131.0, 4.15, 2.00, 6.0, 159.8),
]

# McMaster-Carr, phosphate-coated carbon steel, DIN 471 (as their catalogue lists them).
MCMASTER = {
    3: "98541A111", 4: "98541A112", 5: "98541A113", 6: "98541A114", 7: "98541A115",
    8: "98541A116", 9: "98541A117", 10: "98541A118", 11: "98541A445", 12: "98541A119",
    13: "98541A405", 14: "98541A120", 15: "98541A410", 16: "98541A121", 17: "98541A420",
    18: "98541A122", 19: "98541A430", 20: "98541A123", 21: "98541A461", 22: "98541A463",
    24: "98541A125", 25: "98541A440", 26: "98541A128", 28: "98541A130", 29: "98541A132",
}

RINGS = [Ring(*row, mcmaster=MCMASTER.get(row[0], "")) for row in _TABLE]

# Clearances for a printed or cut part round the ring (mm).
WASHER_CLEARANCE = 0.5      # washer OD this much under the counterbore, each side
WASHER_THICKNESS = 1.5      # default splined washer: a common sheet / print thickness


def get(d1: float) -> Ring:
    for r in RINGS:
        if abs(r.d1 - d1) < 1e-9:
            return r
    raise KeyError(f"no DIN 471 ring for a {d1:g} mm shaft")


def for_shaft(outside_diameter: float) -> Ring:
    """The ring for a (splined) shaft: the smallest DIN 471 size at least its
    outside diameter, so the ring opens over the teeth as it's designed to and
    its groove cuts the tooth tops."""
    for r in RINGS:
        if r.d1 >= outside_diameter - 1e-9:
            return r
    raise ValueError(f"DIN 471 goes up to {RINGS[-1].d1:g} mm; no ring for {outside_diameter:g} mm")


@dataclass(frozen=True)
class Counterbore:
    diameter: float     # the recess: room for the ring as it's fitted (DIN d4)
    depth: float        # groove width m, plus the washer under the ring
    washer_od: float    # 0 without a washer
    washer_t: float


def counterbore(ring: Ring, washer: float = 0.0) -> Counterbore:
    """The face recess for a ring (and a splined washer `washer` mm thick
    under it): as wide as the ring needs while it's fitted, as deep as the
    ring's groove is wide plus the washer, so the ring ends flush with the
    face. The groove on the shaft sits with its outer wall at the face."""
    od = ring.d4 - 2 * WASHER_CLEARANCE if washer > 0 else 0.0
    return Counterbore(ring.d4, ring.m + washer, od, washer)
