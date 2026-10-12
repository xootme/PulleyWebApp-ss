"""
assembly.py — the manifest for one assembly STEP of a whole design (the
owner, 2026-10-06: the STEP download becomes one file, a real assembly, its
parts placed as assembled; small_step's `assemble`, Documents\\CCT_Assembly_STEP_Handoff.md).

    m = manifest(args)            # the page's query
    m == {"name": "HTD-5M-20T-30T",
          "parts": [{"name": ..., "make": {...}  or  "extrude": {...},
                     "placement": {"origin": [x, y, z], "rotate_z_deg": deg},
                     "instances": [placement, ...],
                     "color": "#RRGGBB"}, ...]}       # the 3D view's (exporters/colors.py)

Every part is in the frame the app's STL and small_step's pulley STEP share
(checked to 1e-3 mm on hub, flange, metal plate and spline designs): the bore
on the z axis, the pulley body from z = 0 up. The parts:

  pulley n   "make": {"kind": "pulley", "pulley": n} — the STEP the worker already
             makes (its flanges, plates and separate top inside). A drive's are
             placed as the existing two-pulley STEP placed them: pulley 2 at the
             centre distance on +x, each turned so its teeth mesh with the belt.
  belt       a drive's, "make": {"kind": "belt"}, where the belt STEP puts it.
  shaft      a splined bore's sample shaft (nominal: STEP is never compensated),
             ONE part of stacked "bodies" (cct_common.assembly_step.shaft_part) —
             the spline's outline, and at each ring's groove the outline clipped
             to the groove's floor (d2), so the ring sits in a groove rather than
             through the teeth.
  washer     the splined washer under a ring: its OD less the bore's spline.
  ring       the retaining ring as fitted in its groove (ring_outline.installed),
             at each ringed face.

Placements come from where the 3D view puts the same parts
(spline_parts.preview_parts, retaining_rings.stack()). A part that appears
more than once (a ring on both faces, the same ring on both pulleys) is one
part with several occurrences ("instances").

The groove's clip, the JSON loops, the single-part shaft and the small_step
calls are cct_common's (ring_outline, assembly_step: the owner, 2026-10-11,
shared so every app's are this app's); this module builds the manifest.

Loops are ring_outline's JSON form: ["line", [x0, y0], [x1, y1]] or
["arc", [cx, cy], r, start_deg, end_deg, ccw]; an outer boundary anticlockwise,
holes clockwise.
"""
from __future__ import annotations

import math

from cct_common import assembly_step as asm
from cct_common import retaining_rings as rr
from cct_common import ring_outline as ro
from cct_common import splines as spl
from cct_common.assembly_step import (AssembleError, ExtrudeError, can_assemble, can_bodies,  # noqa: F401
                                      can_extrude, single_make, split_bodies)
from cct_common.ring_outline import (circle, clip_to_circle, loop_json, reversed_loop,  # noqa: F401
                                     seg_json)

from exporters import colors


# ── the binary ───────────────────────────────────────────────────────────────

def ss_binary() -> str | None:
    """The small_step binary the worker would run: SMALL_STEP_BIN, else the
    repo's Linux build (Cloud Run sets SMALL_STEP_BIN to it)."""
    import os
    env = os.environ.get("SMALL_STEP_BIN", "").strip()
    if env and os.path.isfile(env):
        return env
    repo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "small_step_linux")
    return repo if os.name != "nt" and os.path.isfile(repo) else None


def extrude_step(spec: dict, ss_bin: str | None = None) -> bytes:
    """One part's STEP from an extrude spec (ring_outline.Outline.as_dict(), the
    washer's): `small_step extrude` (cct_common.assembly_step)."""
    return asm.extrude_step(spec, ss_bin or ss_binary())


def assemble_step(m: dict, make_step, ss_bin: str | None = None) -> bytes:
    """The manifest as one assembly STEP (cct_common.assembly_step). make_step(make)
    -> bytes builds each "make" part (the worker's pulley and belt STEPs); a lone
    one is the whole file, its make carrying its colour."""
    return asm.assemble_step(m, lambda mk, c: make_step(dict(mk, color=c) if c else mk),
                             ss_bin or ss_binary())


# ── the manifest ─────────────────────────────────────────────────────────────

_place = asm.place
_add = asm.add_part


def _spline_parts(args, n: int, stem: str, x: float, rot: float, add) -> None:
    import app as A
    from exporters.spline_parts import _span_z, ends
    pfx = "p2_" if n == 2 else ""
    sp = A._spline_of(args, pfx)
    if not sp:
        return
    sp_obj = A._as_spline_obj(sp)
    rt = sp.get("retainer")
    bare = A._pulley_stl(dict(args, pulley=str(n)))[3]
    lo, hi = A._ring_face_z(args, pfx, *bare)
    span = hi - lo
    shaft = spl.shaft_path(sp_obj)

    # the shaft: one part from below the bottom face to above the top, a groove at each ring
    below, above = ends(sp)
    grooves = []
    if rt:
        for face in rt["faces"]:
            g0, g1 = _span_z(face, span, rt["stack"][face]["groove"])
            grooves.append((lo + g0, lo + g1))
    ring = rr.for_spline(sp_obj) if rt else None
    add(f"sh{n}", asm.shaft_part(f"{stem}-spline-shaft", shaft, ring, lo - below, hi + above, grooves,
                                 _place(x, 0.0, rot), colors.SHAFT))
    if not rt:
        return
    fitted = ro.installed(ring).as_dict()
    for face in rt["faces"]:
        st = rt["stack"][face]
        if st["washer"]:
            w0 = lo + _span_z(face, span, st["washer"])[0]
            add(f"wa{n}", {"name": f"{stem}-spline-washer",
                         "extrude": {"name": f"{stem}-spline-washer", "thickness": rt["washer_t"],
                                     "loops": [loop_json(circle(rt["washer_od"] / 2.0)),
                                               loop_json(reversed_loop(spl.path(sp_obj)))]},
                         "placement": _place(x, w0, rot), "color": colors.WASHER})
        r0 = lo + _span_z(face, span, st["ring"])[0]
        add(f"sh{n}", {"name": fitted["name"], "extrude": fitted, "placement": _place(x, r0, rot),
                       "color": colors.RING})   # in the shaft's groove


def manifest(args, only=None) -> dict:
    """The assembly's parts and where they go, for a design's query (module docstring).
    only: the Download window's part ids to keep (p1, p2, belt, sh1, wa1 …; a
    ring goes with its shaft, whose groove it sits in); None keeps them all."""
    import app as A
    from geometry.pulley_geometry import BELT_FAMILIES, build_two_pulley_belt
    family, pitch = args.get("family", "HTD"), args.get("pitch", "5M")
    dual = args.get("dual") == "true"
    teeth = {n: A._parse_stl_params(args, str(n))[2] for n in ((1, 2) if dual else (1,))}
    stems = {n: f"{family}-{pitch}-{teeth[n]}T{'-P2' if n == 2 else ''}" for n in teeth}
    name = f"{family}-{pitch}-{teeth[1]}T" + (f"-{teeth[2]}T" if dual else "")

    x = {1: 0.0, 2: 0.0}
    rot = {1: 0.0, 2: 0.0}
    belt = dual and family in BELT_FAMILIES
    if dual:
        key = A._resolve_key(family, pitch)
        pitch_mm = A.PULLEY_SPECS.get(key, {}).get("pitch", 5.0)
        cdist = float(args.get("center_distance", (teeth[1] + teeth[2]) * pitch_mm / (2 * math.pi)))
        x[2] = cdist
        if belt:
            try:
                _, _, phi_l, phi_r = build_two_pulley_belt(family, pitch, teeth[1], teeth[2], cdist, x_offset=0.0)
            except Exception:
                phi_l = phi_r = 0.0
            # the worker turns each pulley clockwise by phi (_rotate_step_z): -phi anticlockwise
            rot = {1: -math.degrees(phi_l), 2: -math.degrees(phi_r)}

    parts: list = []

    def add(pid: str, part: dict) -> None:
        if only is None or pid in only:
            _add(parts, part)

    for n in teeth:
        add(f"p{n}", {"name": stems[n], "make": {"kind": "pulley", "pulley": n},
                      "placement": _place(x[n], 0.0, rot[n]), "color": colors.PULLEY[n]})
    if belt:
        add("belt", {"name": f"{family}-{pitch}-belt", "make": {"kind": "belt"},
                     "placement": _place(0.0, 0.0, 0.0), "color": colors.BELT})
    for n in teeth:
        _spline_parts(args, n, stems[n], x[n], rot[n], add)
    return {"name": name, "parts": parts}
