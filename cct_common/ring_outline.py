"""
ring_outline.py — a retaining ring's 2D outline and thickness, for a model of
the ring (the STEP assembly: the ring placed on the shaft at retaining_rings.stack()).

    from cct_common import retaining_rings as rr, ring_outline as ro

    o = ro.outline(rr.get(26))     # DIN 471 26 x 1.2, free (inside Ø d3)
    o = ro.installed(rr.get(26))   # ... spread in its groove (inside Ø d2), as on the shaft
    o.thickness                    # 1.2  — extrude the outline this far
    o.loops                        # [outer boundary, hole, hole]: exact LINEs and ARCs
    o.polygon()                    # shapely, for a preview or a check
    o.to_dxf(path)                 # the loops as LINE / ARC entities
    o.as_dict()                    # JSON for a STEP job

    ro.HEIGHTS["DIN 471"][26]      # 1.2: every ring's thickness, by series and shaft size

The shape (DIN 471 Figure 1: "shape of ring at manufacturer's discretion"; the
standard fixes only these): an open ring round the shaft's axis at the origin,
its aperture on -Y.

  inside     the unstressed internal diameter d3, a circle on the axis;
  outside    a circle offset towards +Y, so the ring is b wide opposite the
             aperture (DIN's b) and narrows to TAPER x b towards it;
  lugs       one each side of the aperture, out to d3/2 + a (DIN's radial lug
             width), their faces apart by the gap GAP x d5;
  holes      the assembly holes d5, one in each lug — or, d1 <= 9 mm, a half
             hole notched into each lug's face (DIN's Detail X), the two making
             one hole d5 across the gap.

d3, a, b, d5 and s are the standard's; the taper, the gap and the lugs' width
are this model's (marked so in `assumed`). An SH (inch) ring's b and d5 come
from DIN's proportions (Ring.outline_approx).

Loops: the outer boundary runs counter-clockwise, holes clockwise. Each
segment is ("line", (x0, y0), (x1, y1)) or ("arc", (cx, cy), r, start_deg,
end_deg, ccw) — an arc from start to end, counter-clockwise when ccw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .retaining_rings import INCH_RINGS, RINGS, Ring

TAPER = 0.6            # the ring's width next to the lugs, as a fraction of b
GAP = 1.0              # the aperture between the lug faces, x d5
LUG_WIDTH = (1.9, 0.85)   # a lug's width along the gap face: max(1.9 x d5, 0.85 x a) ...
LUG_REACH = 0.85       # ... with its outer side no further from the axis than 0.85 R
HOLE_RADIAL = 0.55     # an assembly hole's centre: d3/2 + 0.55 a from the axis
NOTCH_LIMIT = 9.0      # d1 at and below which the holes are half holes in the lug faces

# Every ring's thickness s — the height its outline is extruded — by series and
# shaft size (mm; an SH ring's shaft size as stored, in mm).
HEIGHTS = {
    "DIN 471": {r.d1: r.s for r in RINGS},
    "SH": {r.number: r.s for r in INCH_RINGS},
}


@dataclass(frozen=True)
class Outline:
    ring: Ring
    thickness: float               # extrude by this (the ring's s)
    loops: list                    # [outer, holes...]: segment lists (module docstring)
    assumed: dict = field(default_factory=dict)   # the dimensions this model chose

    # ── output ──────────────────────────────────────────────────────────────
    def points(self, loop: list, step_deg: float = 2.0) -> list:
        """A loop as points (arcs sampled every step_deg), not repeating the first."""
        pts = []
        for seg in loop:
            if seg[0] == "line":
                pts.append(seg[1])
            else:
                _, (cx, cy), r, a0, a1, ccw = seg
                sweep = _sweep(a0, a1, ccw)
                n = max(2, int(math.ceil(abs(sweep) / step_deg)))
                pts += [(cx + r * math.cos(math.radians(a0 + sweep * k / n)),
                         cy + r * math.sin(math.radians(a0 + sweep * k / n))) for k in range(n)]
        return pts

    def polygon(self, step_deg: float = 2.0):
        """The outline as a shapely Polygon (the outer boundary less the holes)."""
        from shapely.geometry import Polygon
        outer, *holes = self.loops
        return Polygon(self.points(outer, step_deg), [self.points(h, step_deg) for h in holes])

    def as_dict(self) -> dict:
        """JSON for a STEP job: the thickness and the loops, exactly."""
        return {"name": self.ring.label(), "thickness": self.thickness,
                "loops": [[list(_jsonable(s)) for s in loop] for loop in self.loops],
                "assumed": dict(self.assumed)}

    def to_dxf(self, path=None, *, layer: str = "RING", hole_layer: str = "RING_HOLES"):
        """The loops as DXF LINE and ARC entities (ezdxf, which the apps carry).
        Returns the ezdxf document; writes it to `path` when given."""
        import ezdxf
        doc = ezdxf.new("R2010", setup=False)
        doc.units = ezdxf.units.MM
        msp = doc.modelspace()
        for i, loop in enumerate(self.loops):
            lay = layer if i == 0 else hole_layer
            for seg in loop:
                if seg[0] == "line":
                    msp.add_line(seg[1], seg[2], dxfattribs={"layer": lay})
                else:
                    _, c, r, a0, a1, ccw = seg
                    # a DXF arc always runs counter-clockwise from its start angle
                    s, e = (a0, a1) if ccw else (a1, a0)
                    if abs(_sweep(a0, a1, ccw)) >= 360 - 1e-9:
                        msp.add_circle(c, r, dxfattribs={"layer": lay})
                    else:
                        msp.add_arc(c, r, s, e, dxfattribs={"layer": lay})
        if path is not None:
            doc.saveas(path)
        return doc


def _sweep(a0: float, a1: float, ccw: bool) -> float:
    """Signed sweep from a0 to a1, degrees: positive counter-clockwise."""
    d = (a1 - a0) % 360.0
    if ccw:
        return d if d > 1e-12 else 360.0
    return -((360.0 - d) % 360.0 or 360.0)


def _jsonable(seg):
    return [x if not isinstance(x, tuple) else list(x) for x in seg]


def _deg(x: float, y: float) -> float:
    return math.degrees(math.atan2(y, x))


def _pieces(ring: Ring) -> dict:
    """The free ring's outline in pieces: each lug (its gap face, notch, bottom
    and outer side, as run anticlockwise round the outline) with its hole, and
    the points the back joins them at. outline() puts them together as they
    are; installed() moves the lugs first."""
    r_i = ring.d3 / 2.0
    r_l = r_i + ring.a                       # the lugs' outer edge
    b, d5 = ring.b, ring.d5
    if b <= 0 or d5 <= 0:
        raise ValueError(f"{ring.label()}: no b / d5 for its outline")
    # the outer edge: a circle on (0, e), b wide at +Y, TAPER x b at -Y
    top, bottom = r_i + b, r_i + TAPER * b
    R, e = (top + bottom) / 2.0, (top - bottom) / 2.0
    g = GAP * d5                              # the aperture between the lug faces
    lw = min(max(LUG_WIDTH[0] * d5, LUG_WIDTH[1] * ring.a), LUG_REACH * R - g / 2.0)
    xs = g / 2.0 + lw                         # each lug's outer side
    r5 = d5 / 2.0
    notch = ring.d1 <= NOTCH_LIMIT            # DIN's Detail X: half holes in the lug faces
    rc = r_i + (ring.a / 2.0 if notch else HOLE_RADIAL * ring.a)   # hole / notch centre radius

    def y_on(r, x):                           # the lower half of a circle on the axis
        return -math.sqrt(r * r - x * x)
    ya, yb, yc_ = y_on(r_i, g / 2), y_on(r_l, g / 2), y_on(r_l, xs)
    yd = e - math.sqrt(R * R - xs * xs)       # the outer circle, lower half
    if not (yd > yc_ and xs < R and yb < ya):
        raise ValueError(f"{ring.label()}: the lugs don't fit this outline")

    def lug(sign: int) -> list:
        x = sign * g / 2
        if notch:
            yc = y_on(rc, g / 2)
            if not (ya > yc + r5 and yc - r5 > yb):
                raise ValueError(f"{ring.label()}: the notch doesn't fit the lug face")
            hi, lo = (x, yc + r5), (x, yc - r5)
            # a half hole cut into the lug: the arc bulges away from the gap (CW either side)
            if sign > 0:
                face = [("line", (x, ya), hi), ("arc", (x, yc), r5, 90.0, -90.0, False),
                        ("line", lo, (x, yb))]
            else:
                face = [("line", (x, yb), lo), ("arc", (x, yc), r5, 270.0, 90.0, False),
                        ("line", hi, (x, ya))]
        else:
            face = [("line", (x, ya), (x, yb))] if sign > 0 else [("line", (x, yb), (x, ya))]
        if sign > 0:
            return face + [("arc", (0.0, 0.0), r_l, _deg(x, yb), _deg(xs, yc_), True),
                           ("line", (xs, yc_), (xs, yd))]
        return [("line", (-xs, yd), (-xs, yc_)),
                ("arc", (0.0, 0.0), r_l, _deg(-xs, yc_), _deg(x, yb), True)] + face

    holes = {1: [], -1: []}
    if not notch:
        xc = g / 2 + lw / 2
        yh = y_on(rc, xc)
        for sx in (1, -1):
            holes[sx] = [[("arc", (sx * xc, yh), r5, 0.0, 0.0, False)]]      # a full circle, clockwise
    return {"r_i": r_i, "R": R, "e": e,
            "inner": {1: (g / 2, ya), -1: (-g / 2, ya)},         # where each lug meets the inside
            "outer": {1: (xs, yd), -1: (-xs, yd)},               # ... and the outside
            "lugs": {1: lug(1), -1: lug(-1)}, "holes": holes,
            "assumed": {"taper": TAPER, "gap_mm": round(g, 4), "lug_width_mm": round(lw, 4),
                        "hole_centre_radius_mm": round(rc, 4), "half_holes": notch,
                        "b_d5_from": "DIN 471 proportions" if ring.outline_approx else "the ring's table"}}


def _assemble(ring, pc, back_arc, inner_arc, assumed) -> Outline:
    outer = pc["lugs"][1] + [back_arc] + pc["lugs"][-1] + [inner_arc]
    return Outline(ring, ring.s, [outer] + pc["holes"][1] + pc["holes"][-1], assumed)


def outline(ring: Ring) -> Outline:
    """The ring's outline, free (unstressed: inside diameter d3; module
    docstring). Raises ValueError for a ring whose numbers leave no room for
    its lugs or holes (none in the tables)."""
    pc = _pieces(ring)
    (xs, yd), (xa, ya) = pc["outer"][1], pc["inner"][1]
    e = pc["e"]
    back = ("arc", (0.0, e), pc["R"], _deg(xs, yd - e), _deg(-xs, yd - e), True)
    inside = ("arc", (0.0, 0.0), pc["r_i"], _deg(-xa, ya), _deg(xa, ya), False)   # clockwise
    return _assemble(ring, pc, back, inside, dict(pc["assumed"], state="free"))


def _moved(seg, rot_deg: float, dx: float, dy: float):
    """A segment turned rot_deg about the origin, then shifted by (dx, dy)."""
    c, s_ = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))

    def pt(p):
        return (p[0] * c - p[1] * s_ + dx, p[0] * s_ + p[1] * c + dy)
    if seg[0] == "line":
        return ("line", pt(seg[1]), pt(seg[2]))
    _, cen, r, a0, a1, ccw = seg
    if abs(_sweep(a0, a1, ccw)) >= 360 - 1e-9:        # a full circle: no angles to turn
        return ("arc", pt(cen), r, a0, a1, ccw)
    return ("arc", pt(cen), r, a0 + rot_deg, a1 + rot_deg, ccw)


def installed(ring: Ring, groove_d: float | None = None) -> Outline:
    """The ring as fitted in its groove: spread open until its inside lies on
    the groove's bottom, diameter groove_d (the ring's d2 unless given), so it
    is drawn the size it is on the shaft rather than its smaller free size.

    How a spread ring changes, as modelled: its inside keeps its length, so on
    the larger radius it goes round less of the circle and the aperture opens;
    the lugs, with their holes, move as rigid pieces — turned about the axis to
    where the inside now ends, and pushed out with it; the back keeps its width
    b; the outside is the circle through the back and the two moved lugs.
    Every piece stays an exact LINE or ARC. A groove no larger than d3 (no
    spread) gives the free outline."""
    pc = _pieces(ring)
    r0 = pc["r_i"]
    r1 = (ring.d2 if groove_d is None else groove_d) / 2.0
    if r1 <= r0 + 1e-9:
        return outline(ring)
    dr, k = r1 - r0, r0 / r1                      # the push out; the inside's angle shrinks by k
    turn = 0.0
    for sign in (1, -1):
        xa, ya = pc["inner"][sign]
        th = _deg(xa, ya)
        psi = th - 90.0 if th - 90.0 > -180.0 else th + 270.0     # signed angle from the back
        rot = psi * (k - 1.0)                                   # the lug turns towards the back
        th1 = math.radians(th + rot)
        dx, dy = dr * math.cos(th1), dr * math.sin(th1)
        turn = rot if sign > 0 else turn
        pc["lugs"][sign] = [_moved(sg, rot, dx, dy) for sg in pc["lugs"][sign]]
        pc["holes"][sign] = [[_moved(sg, rot, dx, dy) for sg in h] for h in pc["holes"][sign]]
        pc["inner"][sign] = _moved(("line", pc["inner"][sign], pc["inner"][sign]), rot, dx, dy)[1]
        pc["outer"][sign] = _moved(("line", pc["outer"][sign], pc["outer"][sign]), rot, dx, dy)[1]
    # the outside: the circle through the back (0, r1 + b) and the moved lug tops (symmetric)
    (xd, yd), top = pc["outer"][1], r1 + ring.b
    e1 = (xd * xd + yd * yd - top * top) / (2.0 * (yd - top))      # its centre (0, e1)
    r_out = top - e1
    back = ("arc", (0.0, e1), r_out, _deg(xd, yd - e1), _deg(-xd, yd - e1), True)
    xa, ya = pc["inner"][1]
    inside = ("arc", (0.0, 0.0), r1, _deg(-xa, ya), _deg(xa, ya), False)
    assumed = dict(pc["assumed"], state="installed", groove_d=round(2 * r1, 4),
                   lug_turn_deg=round(turn, 4), gap_installed_mm=round(2 * xa, 4))
    return _assemble(ring, pc, back, inside, assumed)


def all_outlines() -> list:
    """Every ring in the tables, DIN 471 then SH."""
    return [outline(r) for r in RINGS + INCH_RINGS]
