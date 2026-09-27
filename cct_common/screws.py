"""
screws.py — screw sizes and the holes 3D-printed parts need for them,
shared by the CCT tools (E-Box Designer's standoffs, tabs and cap screws;
Timing Pulleys' hub set screws).

    from cct_common import screws

    m3 = screws.get("M3")
    screws.self_tap_diameter(m3, engagement_percent=50)   # 2.730 mm bore
    screws.hole("M3", engagement_percent=50)              # {"shape": "circle", "diameter": 2.73}
    screws.clearance_diameter(m3)                         # 3.4 (ISO 273 medium fit)
    screws.nut("M5")                                      # (8.0, 4.0) hex nut across flats, height

Only sizes and diameters live here. Cutting the hole — where it goes, which
way it points, how the part's mesh or solid is built — stays in each app,
and so does print compensation (each app grows holes by its own offset at
export time).

Sizes: ISO metric coarse M2–M10 and UNC #2-56 to 1/4-20. Moved here from
E-Box Designer's geometry/thread_table.py (which now re-exports this, limited
to the sizes it offers) so every tool sizes a screw hole the same way.

Sources, rounded to 3 decimal places:
  major / minor  ISO 724 (metric: minor = basic internal minor D1 = d - 1.0825 P);
                 ASME B1.1 basic minor diameter (UNC)
  pan head       ISO 7045 dk, k (metric); ASME B18.6.3 (UNC)
  flat head      ISO 7046-1 dk THEORETICAL max (metric) — the sharp-corner cone
                 diameter a countersink pocket must accept; ASME B18.6.3 Type I (UNC)
  clearance      ISO 273 medium fit (metric); UNC interpolated from ISO 273's
                 deltas (no direct equivalent)
  nut            DIN 934 hex nut, across flats and height (metric); ASME
                 B18.6.3 square/hex machine-screw nut (UNC)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

# One countersink cone half-angle for every size: metric's 90° included and
# UNC's 82° are close enough for printed parts that a per-size angle isn't
# worth a column.
COUNTERSINK_HALF_ANGLE_DEG = 45.0

# A size with no table entry: the hole diameter is given directly (a
# heat-set insert, an odd screw). Everywhere a real size would supply a
# diameter, the override does.
CUSTOM = "Custom"

# A screw that passes through (it threads into something beyond): a plain
# round hole this much bigger than the thread's outer diameter.
THROUGH_HOLE_MARGIN = 0.05

# A boss (a post a self-tapping screw goes into) this many times the
# screw's outer diameter: common boss-design guidance for enough wall.
AUTO_BOSS_OD_RATIO = 2.5


@dataclass(frozen=True)
class ThreadSize:
    name: str               # "M3", "#6-32"
    major_diameter: float   # mm, the thread's outer diameter
    minor_diameter: float   # mm, its root diameter
    heads: dict             # {"pan": (od_mm, height_mm), "flat": od_mm}
    clearance_diameter: float = 0.0            # mm, plain clearance hole
    nut: Optional[tuple] = None                # (across_flats_mm, height_mm)


METRIC_COARSE: dict[str, ThreadSize] = {
    "M2":   ThreadSize("M2",   2.0,  1.567, {"pan": (4.0, 1.6),  "flat": 4.4},  2.4,  (4.0, 1.6)),
    "M2.5": ThreadSize("M2.5", 2.5,  2.013, {"pan": (5.0, 2.1),  "flat": 5.5},  2.9,  (5.0, 2.0)),
    "M3":   ThreadSize("M3",   3.0,  2.459, {"pan": (5.6, 2.4),  "flat": 6.3},  3.4,  (5.5, 2.4)),
    "M4":   ThreadSize("M4",   4.0,  3.242, {"pan": (8.0, 3.1),  "flat": 9.4},  4.5,  (7.0, 3.2)),
    "M5":   ThreadSize("M5",   5.0,  4.134, {"pan": (9.5, 3.7),  "flat": 10.4}, 5.5,  (8.0, 4.0)),
    "M6":   ThreadSize("M6",   6.0,  4.917, {"pan": (12.0, 4.6), "flat": 12.6}, 6.6,  (10.0, 5.0)),
    "M8":   ThreadSize("M8",   8.0,  6.647, {"pan": (16.0, 6.0), "flat": 17.3}, 9.0,  (13.0, 6.5)),
    "M10":  ThreadSize("M10", 10.0,  8.376, {"pan": (20.0, 7.5), "flat": 20.0}, 11.0, (17.0, 8.0)),
}

UNC: dict[str, ThreadSize] = {
    "#2-56":  ThreadSize("#2-56",  2.184, 1.595, {"pan": (4.1, 1.4),  "flat": 4.17}, 2.6, (4.763, 1.588)),
    "#4-40":  ThreadSize("#4-40",  2.845, 2.088, {"pan": (5.3, 1.8),  "flat": 5.72}, 3.3, (6.350, 2.381)),
    "#6-32":  ThreadSize("#6-32",  3.505, 2.533, {"pan": (6.5, 2.2),  "flat": 7.09}, 4.0, (7.938, 2.778)),
    "#8-32":  ThreadSize("#8-32",  4.166, 3.193, {"pan": (7.7, 2.6),  "flat": 8.48}, 4.7, (8.731, 3.175)),
    "#10-24": ThreadSize("#10-24", 4.826, 3.528, {"pan": (8.9, 3.0),  "flat": 9.88}, 5.4, (9.525, 3.175)),
    "1/4-20": ThreadSize("1/4-20", 6.350, 4.793, {"pan": (12.5, 4.4), "flat": 12.5}, 7.0, (11.113, 5.556)),
}

ALL_SIZES: dict[str, ThreadSize] = {**METRIC_COARSE, **UNC}

Size = Union[str, ThreadSize]


def get(name: Size) -> ThreadSize:
    if isinstance(name, ThreadSize):
        return name
    try:
        return ALL_SIZES[name]
    except KeyError:
        raise ValueError(f"unknown screw size {name!r}; known sizes: {sorted(ALL_SIZES)}") from None


def major_diameter(size: Size, override: float = 0.0) -> float:
    """The screw's outer diameter; for CUSTOM, the override stands in."""
    if size == CUSTOM:
        return override
    return get(size).major_diameter


# ── holes ─────────────────────────────────────────────────────────────────

def self_tap_diameter(size: Size, engagement_percent: float) -> float:
    """A round hole the screw cuts its own thread into: 0% is the thread's
    outer diameter (no grip), 100% its root diameter (full thread depth)."""
    s = get(size)
    return s.major_diameter - engagement_percent / 100.0 * (s.major_diameter - s.minor_diameter)


def hex_flats(size: Size, flat_percent: float) -> float:
    """A hexagonal self-tapping hole: across-flats as a percentage of the
    outer diameter (the screw grips on the flats, the corners give the
    plastic somewhere to go)."""
    return get(size).major_diameter * flat_percent / 100.0


def hole(size: Size, *, shape: str = "round", engagement_percent: float = 0.0,
         hex_flat_percent: float = 0.0, override: float = 0.0) -> dict:
    """The self-tapping hole for `size`: {"shape": "circle", "diameter"} or
    {"shape": "hex", "flats"}. A nonzero `override` is always a round hole
    of that diameter (a known fastener such as a heat-set insert, which is
    round), and is the only size a CUSTOM screw has."""
    if override > 0:
        return {"shape": "circle", "diameter": override}
    if shape == "round":
        return {"shape": "circle", "diameter": self_tap_diameter(size, engagement_percent)}
    if shape == "hex":
        return {"shape": "hex", "flats": hex_flats(size, hex_flat_percent)}
    raise ValueError(f"unknown hole shape {shape!r}")


def clearance_diameter(size: Size) -> float:
    """A plain hole the screw passes through with room (ISO 273 medium fit)."""
    return get(size).clearance_diameter


def through_hole_diameter(size: Size, override: float = 0.0) -> float:
    """A close pass-through hole: the outer diameter plus THROUGH_HOLE_MARGIN
    of it — tighter than clearance_diameter, for a screw that must stay
    centred (it threads into a matching hole just beyond). A nonzero
    override is used as is."""
    if override > 0:
        return override
    return get(size).major_diameter * (1.0 + THROUGH_HOLE_MARGIN)


def auto_boss_od(size: Size, override: float = 0.0) -> float:
    """A boss's outer diameter for this screw (AUTO_BOSS_OD_RATIO x its outer
    diameter; for CUSTOM, x the override)."""
    return AUTO_BOSS_OD_RATIO * major_diameter(size, override)


def nut(size: Size) -> tuple:
    """(across_flats_mm, height_mm) of the hex nut for this size."""
    s = get(size)
    if s.nut is None:
        raise ValueError(f"no nut size for {s.name}")
    return s.nut
