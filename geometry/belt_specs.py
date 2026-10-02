"""
belt_specs.py — the published design rules a pulley or two-pulley drive is
checked against, and the drive figures they need (the Dimensions panel under
the 2D view, like the Sprocket app's).

Every figure names its source. Where no source covers a profile the rule is
applied with this app's own profile data and marked approximate, or the
check is left out — never a guessed table.

Sources (copies in PulleyWebApp-ss/Pdf):
  ISO 13050:2014  curvilinear profiles G (GT 8M/14M), H (HTD), R (RPP),
                  S (STD 8M/14M): belt tooth height ht and pitch differential
                  a (Tables 1, 9, 18, 27); pulley widths (Tables 8, 17, 26,
                  34); flanges (Annex D: h >= ht + a, angle 8-25 deg).
  ISO 5294:2012   MXL-XXH pulleys: widths (Table 4), flange heights (Annex A).
  Gates, Light Power & Precision Drive Design Manual: GT 2/3/5 mm flange
                  heights (Table 22) and face widths (Table 23); teeth in
                  mesh and its torque factors (p. 104); 60 deg minimum wrap
                  (p. 64); flanging a two-pulley drive, long spans (pp. 62-63).
  Not covered: T and AT (the ISO 17396 copy here is a preview that stops
  before the pulley tables; the 2017 edition is DRM-locked) and STD 2/3/5 mm.
"""
import math
from dataclasses import dataclass

import numpy as np

# ── Flange height: minimum radial reach past the pulley OD ──────────────────
# ISO 13050 Annex D: h = belt tooth height ht + pitch differential a.
_ISO13050 = 'ISO 13050 Annex D (h = ht + a)'
FLANGE_MIN_H = {
    # profile system H (Table 9)
    '3M':  (1.21 + 0.381, _ISO13050), '5M':  (2.08 + 0.572, _ISO13050),
    '8M':  (3.38 + 0.686, _ISO13050), '14M': (6.02 + 1.397, _ISO13050),
    '20M': (8.68 + 2.159, _ISO13050),
    # profile system R (Table 18)
    'R3M':  (1.27 + 0.381, _ISO13050), 'R5M':  (2.15 + 0.570, _ISO13050),
    'R8M':  (3.25 + 0.686, _ISO13050), 'R14M': (6.13 + 1.397, _ISO13050),
    'R20M': (8.75 + 2.160, _ISO13050),
    # profile system G (Table 1) and S (Table 27)
    'GT8M': (3.43 + 0.80, _ISO13050), 'GT14M': (6.00 + 1.40, _ISO13050),
    'S8M':  (3.05 + 0.686, _ISO13050), 'S14M': (5.30 + 1.397, _ISO13050),
    # Gates Table 22, minimum flange height
    'GT2M': (1.10, 'Gates Table 22'), 'GT3M': (1.70, 'Gates Table 22'),
    'GT5M': (2.30, 'Gates Table 22'),
    # ISO 5294 Annex A
    'MXL': (0.5, 'ISO 5294 Annex A'), 'XL': (1.0, 'ISO 5294 Annex A'),
    'L':   (1.5, 'ISO 5294 Annex A'), 'H':  (2.0, 'ISO 5294 Annex A'),
    'XH':  (4.8, 'ISO 5294 Annex A'), 'XXH': (6.1, 'ISO 5294 Annex A'),
}

# ── Pulley face width: minimum over the belt width ──────────────────────────
# (standard belt widths, minimum flanged width, minimum unflanged width), mm.
# A width between standard ones takes the interpolated allowance; outside
# the table, the nearest end's.
_H3 = ([6, 9, 15], [8, 11, 17], [11, 14, 20])
_H5 = ([9, 15, 25], [11, 17, 27], [15, 21, 31])
_H8 = ([20, 30, 50, 85], [22, 32, 53, 89], [30, 40, 60, 96])
_H14 = ([40, 55, 85, 115, 170], [42, 58, 89, 120, 175], [55, 70, 101, 131, 186])
_H20 = ([115, 170, 230, 290, 340], [120, 175, 235, 300, 350], [134, 189, 251, 311, 361])
_IN = 25.4
WIDTH_TABLE = {
    '3M': (*_H3, 'ISO 13050 Table 17'), '5M': (*_H5, 'ISO 13050 Table 17'),
    '8M': (*_H8, 'ISO 13050 Table 17'), '14M': (*_H14, 'ISO 13050 Table 17'),
    '20M': (*_H20, 'ISO 13050 Table 17'),
    'R3M': (*_H3, 'ISO 13050 Table 26'), 'R5M': (*_H5, 'ISO 13050 Table 26'),
    'R8M': (*_H8, 'ISO 13050 Table 26'), 'R14M': (*_H14, 'ISO 13050 Table 26'),
    'R20M': (*_H20, 'ISO 13050 Table 26'),
    'GT8M': ([12, 21, 36, 62], [14, 23, 40, 66], [22, 30, 47, 74], 'ISO 13050 Table 8'),
    'GT14M': ([20, 37, 68, 90, 125], [26, 44, 76, 99, 134], [35, 52, 85, 107, 143],
              'ISO 13050 Table 8'),
    'S8M': ([15, 25, 40, 60], [18, 28, 43, 63], [25, 35, 50, 70], 'ISO 13050 Table 34'),
    'S14M': ([40, 60, 80, 100, 120], [43, 63, 84, 104, 125], [55, 76, 96, 116, 136],
             'ISO 13050 Table 34'),
    'MXL': ([3.2, 4.8, 6.4], [3.8, 5.3, 7.1], [5.6, 7.1, 8.9], 'ISO 5294 Table 4'),
    'XL': ([0.25 * _IN, 0.31 * _IN, 0.37 * _IN], [7.1, 8.6, 10.4], [8.9, 10.4, 12.2],
           'ISO 5294 Table 4'),
    'L': ([0.50 * _IN, 0.75 * _IN, 1.00 * _IN], [14.0, 20.3, 26.7], [17.0, 23.3, 29.7],
          'ISO 5294 Table 4'),
    'H': ([0.75 * _IN, 1.0 * _IN, 1.5 * _IN, 2.0 * _IN, 3.0 * _IN],
          [20.3, 26.7, 39.4, 52.8, 79.0], [24.8, 31.2, 43.9, 57.3, 83.5], 'ISO 5294 Table 4'),
    'XH': ([2.0 * _IN, 3.0 * _IN, 4.0 * _IN], [56.6, 83.8, 110.7], [62.6, 89.8, 116.7],
           'ISO 5294 Table 4'),
    'XXH': ([2.0 * _IN, 3.0 * _IN, 4.0 * _IN, 5.0 * _IN], [56.6, 83.8, 110.7, 137.7],
            [64.1, 91.3, 118.2, 145.2], 'ISO 5294 Table 4'),
}
# Gates Table 23: a fixed allowance over the belt width (flanged, unflanged).
WIDTH_ALLOWANCE = {
    'GT2M': (1.00, 3.00, 'Gates Table 23'),
    'GT3M': (1.25, 4.00, 'Gates Table 23'),
    'GT5M': (1.50, 5.00, 'Gates Table 23'),
}

FLANGE_ANGLE_RANGE = (8.0, 25.0)          # ISO 13050 Annex D / ISO 5294 Annex A
MIN_TEETH_IN_MESH = 6                     # Gates p. 104, Fenner p. 13
TIM_FACTOR = {5: 0.8, 4: 0.6, 3: 0.4}     # Gates p. 104; 2 or fewer: redesign
MIN_WRAP_DEG = 60.0                       # Gates p. 64
LONG_SPAN_RATIO = 12.0                    # Gates p. 63


@dataclass
class Figure:
    value: float
    source: str
    approximate: bool = False


def min_flange_height(key: str, spec: dict) -> Figure:
    """The minimum flange height past the OD for a profile. Without a
    published figure, ISO 13050's rule (ht + a) with this app's own groove
    depth and pitch differential, marked approximate."""
    if key in FLANGE_MIN_H:
        h, src = FLANGE_MIN_H[key]
        return Figure(round(h, 3), src)
    h = spec['tooth_ht'] + spec.get('pitch_line_diff', 0.0)
    return Figure(round(h, 3), "ISO 13050's rule (h = ht + a) with this app's groove depth — "
                  'no published figure for this profile', approximate=True)


FLANGE_OVERLAP_FLOOR = 3.0      # mm: the least a flange may sit on the pulley


def min_flange_overlap(key: str, spec: dict) -> Figure:
    """How far a flange must reach inward past the groove bottom (the root
    diameter), so it sits on solid pulley: the larger of 3 mm and the minimum
    flange height h (min_flange_height). The owner's rule (2026-10-01): as much
    support inside the groove as the flange stands out past the OD, never under
    3 mm. No standard gives an inward overlap, so it is always marked
    approximate; h's own source is named."""
    h = min_flange_height(key, spec)
    value = max(FLANGE_OVERLAP_FLOOR, h.value)
    return Figure(round(value, 3), f'CCT rule (owner, 2026-10-01): the larger of 3 mm and the minimum '
                  f'flange height h ({h.source})', approximate=True)


def min_face_width(key: str, belt_width: float, flanged: bool):
    """The minimum pulley face width for a belt width, or None where no
    source covers the profile."""
    if key in WIDTH_ALLOWANCE:
        fl, unfl, src = WIDTH_ALLOWANCE[key]
        return Figure(round(belt_width + (fl if flanged else unfl), 3), src)
    if key in WIDTH_TABLE:
        widths, fl, unfl, src = WIDTH_TABLE[key]
        mins = fl if flanged else unfl
        allowance = np.interp(belt_width, widths, [m - w for m, w in zip(mins, widths)])
        exact = any(abs(belt_width - w) < 0.05 for w in widths)
        return Figure(round(belt_width + float(allowance), 3),
                      src if exact else src + ', interpolated between standard widths')
    return None


@dataclass
class Drive:
    small_pd: float
    large_pd: float
    centre: float
    wrap_small_deg: float
    teeth_in_mesh: int
    span: float
    ratio: float


def drive(pitch_mm: float, teeth1: int, teeth2: int, centre: float) -> Drive:
    """Open two-pulley drive figures: wrap on the smaller pulley, whole teeth
    in mesh on it (Gates p. 104: drop the fraction), and the free span."""
    pd1, pd2 = teeth1 * pitch_mm / math.pi, teeth2 * pitch_mm / math.pi
    d, D = min(pd1, pd2), max(pd1, pd2)
    n_small = min(teeth1, teeth2)
    s = min(1.0, (D - d) / (2.0 * centre))
    wrap = 180.0 - 2.0 * math.degrees(math.asin(s))
    tim = math.floor(wrap * n_small / 360.0 + 1e-9)
    span = math.sqrt(max(0.0, centre ** 2 - ((D - d) / 2.0) ** 2))
    return Drive(d, D, centre, wrap, tim, span, max(teeth1, teeth2) / min(teeth1, teeth2))
