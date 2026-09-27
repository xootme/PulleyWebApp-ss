"""
set_screw.py — what a hub set screw's hole and nut are, from the design's
settings (ADR-013). The sizes and hole rules are cct_common.screws, shared
with E-Box Designer; this module only reads this app's parameters.

How the screw holds decides the hole:
  thread  the screw cuts its own thread in the plastic: the self-tapping bore
          from the design's threaded-hole settings (round, by thread
          engagement %, or hex, by hex flats %) — the "Threaded screw holes"
          dialog, app-wide, recorded in every design
  nut     the screw passes through the hub wall into a captured nut: a plain
          ISO 273 clearance hole (never threaded), and the nut's pocket
  insert  a heat-set threaded insert: a round hole of the diameter entered

Parameters (prefix '' or 'p2_'):
  hub_screw_size      "M5", "#6-32", ... or "Custom"
  hub_screw_hold      thread | nut | insert   (default from hub_captured_nut)
  hub_screw_hole_dia  mm; the hole for Custom and insert
  hub_screw_count     0 means no screws
and, for the whole design (no prefix): screw_hole_shape (round | hex),
thread_engagement (%), hex_flat (%).

A design without hub_screw_size predates this (only hub_screw_dia): parse()
returns None and the exporters cut the nominal-diameter hole they always
did, so an old link or file re-exports exactly as before.

STEP: small_step still cuts the nominal diameter (step_worker_ss passes
hub_screw_dia) — the STEP side waits on the small_step work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cct_common import screws

HOLDS = ("thread", "nut", "insert")
HOLE_SHAPES = ("round", "hex")
# The same defaults and ranges as E-Box Designer's 3D Printer settings, so
# one tuned for a printer carries over.
DEFAULT_SHAPE = "round"
DEFAULT_ENGAGEMENT = 50.0
DEFAULT_HEX_FLAT = 82.0
ENGAGEMENT_RANGE = (0.0, 100.0)
HEX_FLAT_RANGE = (78.0, 92.0)

METRIC = ("M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10")
INCH = ("#2-56", "#4-40", "#6-32", "#8-32", "#10-24", "1/4-20")
CUSTOM = screws.CUSTOM


@dataclass(frozen=True)
class SetScrew:
    size: str
    hold: str
    hole: dict                  # {"shape": "circle", "diameter"} | {"shape": "hex", "flats"}
    major: float                # mm, the screw's outer diameter (Custom: the hole entered)
    nut: Optional[tuple]        # (across_flats, height) for hold == "nut", else None


def _num(args, key, default):
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


def thread_settings(args) -> dict:
    """The design's threaded-hole settings, defaulted and clamped."""
    shape = args.get("screw_hole_shape", DEFAULT_SHAPE)
    if shape not in HOLE_SHAPES:
        shape = DEFAULT_SHAPE
    lo, hi = ENGAGEMENT_RANGE
    engagement = min(hi, max(lo, _num(args, "thread_engagement", DEFAULT_ENGAGEMENT)))
    lo, hi = HEX_FLAT_RANGE
    hex_flat = min(hi, max(lo, _num(args, "hex_flat", DEFAULT_HEX_FLAT)))
    return {"shape": shape, "engagement_percent": engagement, "hex_flat_percent": hex_flat}


def _nearest_metric_nut(dia: float) -> tuple:
    """For a Custom screw in a captured nut: the metric nut nearest its hole
    (what every captured nut was before sizes had names)."""
    name = min(METRIC, key=lambda n: abs(screws.get(n).major_diameter - dia))
    return screws.nut(name)


def thread_hole(size: str, settings: dict) -> dict:
    return screws.hole(size, shape=settings["shape"],
                       engagement_percent=settings["engagement_percent"],
                       hex_flat_percent=settings["hex_flat_percent"])


def parse(args, prefix: str = "") -> Optional[SetScrew]:
    """The set screw of one hub, or None (no screws, or a design from before
    screw sizes had names). Raises ValueError for settings no hole can be
    cut from (an unknown size; Custom or insert without a hole diameter)."""
    size = (args.get(f"{prefix}hub_screw_size") or "").strip()
    if not size:
        return None
    if int(_num(args, f"{prefix}hub_screw_count", 0)) <= 0:
        return None
    hold = args.get(f"{prefix}hub_screw_hold") or (
        "nut" if args.get(f"{prefix}hub_captured_nut") == "1" else "thread")
    if hold not in HOLDS:
        raise ValueError(f"unknown set-screw hold {hold!r}")
    if size != CUSTOM:
        screws.get(size)                               # ValueError if unknown
    entered = max(0.0, _num(args, f"{prefix}hub_screw_hole_dia", 0.0))
    if (size == CUSTOM or hold == "insert") and entered <= 0:
        raise ValueError("enter the hole diameter for a Custom screw or a heat-set insert")

    major = screws.major_diameter(size, entered)
    nut = None
    if hold == "nut":
        if size == CUSTOM:
            hole, nut = {"shape": "circle", "diameter": entered}, _nearest_metric_nut(entered)
        else:
            hole, nut = {"shape": "circle", "diameter": screws.clearance_diameter(size)}, screws.nut(size)
    elif hold == "insert":
        hole = {"shape": "circle", "diameter": entered}
    elif size == CUSTOM:
        hole = {"shape": "circle", "diameter": entered}
    else:
        hole = thread_hole(size, thread_settings(args))
    return SetScrew(size=size, hold=hold, hole=hole, major=major, nut=nut)


def size_list() -> list[dict]:
    """The sizes the page offers, for its dropdowns: group, name, outer diameter."""
    return ([{"group": "Metric", "name": n, "major": screws.get(n).major_diameter} for n in METRIC]
            + [{"group": "Inch", "name": n, "major": screws.get(n).major_diameter} for n in INCH])


def holes_for(args) -> dict:
    """/api/screws: every size's holes under the given threaded-hole settings
    (the dialog's example line, and the hub panel's "Cuts:" note)."""
    settings = thread_settings(args)
    out = []
    for s in size_list():
        out.append({**s, "thread": thread_hole(s["name"], settings),
                    "clearance": screws.clearance_diameter(s["name"]),
                    "nut": list(screws.nut(s["name"]))})
    return {"settings": settings, "sizes": out}
