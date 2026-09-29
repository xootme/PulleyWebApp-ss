"""
iso286.py — ISO 286 limits and fits: the tolerance grades and fundamental
deviations the spline fits name (ISO 14's H7 / H9 / H10 holes and f7 / d10 /
a11 shafts), shared by the CCT tools.

    from cct_common import iso286

    iso286.limits(23, "H7")     # (0.0, 0.021)   lower, upper deviation in mm
    iso286.limits(23, "f7")     # (-0.041, -0.020)
    iso286.middle(23, "f7")     # -0.0305        the middle of the zone

Values are ISO 286-2's tables (µm) for nominal sizes up to 180 mm, which covers
ISO 14's splines (D to 125 mm). ISO 286 isn't in the project's PDF folder;
these are the standard's published table values, cited as such.
"""
from __future__ import annotations

import bisect

# Upper bounds of ISO 286's nominal size steps (mm): a size belongs to the
# first step whose bound it doesn't exceed (e.g. 18 is in "over 10 up to 18").
_STEPS = [3, 6, 10, 18, 30, 50, 80, 120, 180]

# Standard tolerance grades, µm, per step.
_IT = {
    7:  [10, 12, 15, 18, 21, 25, 30, 35, 40],
    9:  [25, 30, 36, 43, 52, 62, 74, 87, 100],
    10: [40, 48, 58, 70, 84, 100, 120, 140, 160],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250],
}

# Shaft fundamental deviations (upper deviation es), µm, per step. "a" varies
# within the 80-120 and 120-180 steps, so it has its own finer steps below.
_ES = {
    "d": [-20, -30, -40, -50, -65, -80, -100, -120, -145],
    "f": [-6, -10, -13, -16, -20, -25, -30, -36, -43],
    "g": [-2, -4, -5, -6, -7, -9, -10, -12, -14],
    "h": [0, 0, 0, 0, 0, 0, 0, 0, 0],
}
_A_STEPS = [3, 6, 10, 18, 30, 40, 50, 65, 80, 100, 120, 140, 160, 180]
_A_ES = [-270, -270, -280, -290, -300, -310, -320, -340, -360, -380, -410, -460, -520, -580]

SOURCE = "ISO 286-2 tables (limits and fits)"


def _step(size: float, steps=_STEPS) -> int:
    if not 0 < size <= steps[-1]:
        raise ValueError(f"ISO 286 values here cover sizes up to {steps[-1]} mm, not {size:g}")
    return bisect.bisect_left(steps, size)


def it(size: float, grade: int) -> float:
    """Standard tolerance ITgrade for a nominal size, in mm."""
    return _IT[grade][_step(size)] / 1000.0


def limits(size: float, zone: str) -> tuple[float, float]:
    """(lower, upper) deviations in mm for a zone such as "H7" (hole) or
    "f7" (shaft): capital H is a hole at zero lower deviation; lower-case a,
    d, f, g, h are shafts with their upper deviation es."""
    letter, grade = zone[0], int(zone[1:])
    t = it(size, grade)
    if letter == "H":
        return 0.0, t
    if letter == "a":
        es = _A_ES[_step(size, _A_STEPS)] / 1000.0
    elif letter in _ES:
        es = _ES[letter][_step(size)] / 1000.0
    else:
        raise ValueError(f"no ISO 286 values here for zone {zone!r}")
    return es - t, es


def middle(size: float, zone: str) -> float:
    """The middle of a zone, as a deviation from the nominal size (mm)."""
    lo, hi = limits(size, zone)
    return (lo + hi) / 2.0
