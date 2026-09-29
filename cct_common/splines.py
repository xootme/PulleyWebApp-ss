"""
splines.py — splined bores (the hub side, an internal spline), shared by the
CCT tools: Sprocket Designer's bores (its ADR-021, where this came from) and
Timing Pulleys'.

    from cct_common import splines

    sp = splines.straight(6, 23, 26, 6)          # ISO 14: N × d × D × B
    sp = splines.involute(1.5, 20, 30, "flat")   # ISO 4156: m, z, pressure angle, root
    segs = splines.path(sp)                      # exact lines and arcs, closed, CCW
    pts = splines.sample_closed(segs, 0.05)      # or points, for a mesh or a polygon

Two kinds:

* **Straight-sided, ISO 14:1982** — N equal slots of width B from the minor
  diameter d (the hub centres on it) out to the major diameter D. Table 1's
  light and medium series are the presets; any N × d × D × B can be typed.
* **Involute, ISO 4156** — module m, z teeth, pressure angle 30°, 37.5° or
  45° (30° also flat root). Pitch diameter m·z, base diameter m·z·cos α,
  basic space width πm/2 on the pitch circle, internal major diameter
  m(z + 1.5 / 1.8 / 1.4 / 1.2) (ISO 4156-1:2021 Table 1). The internal minor
  diameter is m(z − 1) at 30° (ANSI B92.1's (N − 1)/P); at 37.5° and 45°
  the table isn't in the public preview, so the looser of the published
  figures is used, m(z − 0.8) and m(z − 0.6) — EST, which an app marks ≈.

Profiles are exact Line/Arc primitives, so a DXF or SVG carries true
geometry: the straight-sided hole is lines and arcs outright; each involute
flank is replaced by tangent circular arcs within FLANK_TOL of the true
involute. Paths are closed, counter-clockwise, the first slot/space on +x
(where a D-flat or key would sit). Only the geometry lives here; where the
hole goes and how a part's mesh or solid is cut stays in each app.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

FLANK_TOL = 0.001          # mm: the arcs' furthest from the true involute

TYPES = ("straight", "involute")

# How a figure is known: the standard's own, a secondary source, or an
# estimate (the looser published value) that an app marks approximate.
STD, SEC, EST = "STD", "SEC", "EST"

Point = tuple[float, float]


# ── Primitives ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Line:
    p0: Point
    p1: Point

    @property
    def start(self) -> Point:
        return self.p0

    @property
    def end(self) -> Point:
        return self.p1

    @property
    def length(self) -> float:
        return math.dist(self.p0, self.p1)

    def reversed(self) -> "Line":
        return Line(self.p1, self.p0)

    def sample(self, max_step: float) -> list[Point]:
        return [self.p0, self.p1]


@dataclass(frozen=True)
class Arc:
    center: Point
    radius: float
    start_angle: float       # radians, CCW from +x
    sweep: float             # signed: > 0 is counter-clockwise

    def point_at(self, ang: float) -> Point:
        return (self.center[0] + self.radius * math.cos(ang),
                self.center[1] + self.radius * math.sin(ang))

    @property
    def end_angle(self) -> float:
        return self.start_angle + self.sweep

    @property
    def start(self) -> Point:
        return self.point_at(self.start_angle)

    @property
    def end(self) -> Point:
        return self.point_at(self.end_angle)

    @property
    def length(self) -> float:
        return abs(self.sweep) * self.radius

    def reversed(self) -> "Arc":
        return Arc(self.center, self.radius, self.end_angle, -self.sweep)

    def sample(self, max_step: float) -> list[Point]:
        n = max(1, math.ceil(self.length / max_step))
        return [self.point_at(self.start_angle + self.sweep * i / n) for i in range(n + 1)]


Segment = Line | Arc


def sample_closed(segments: list[Segment], max_step: float, eps: float = 1e-7) -> list[Point]:
    """Ring points for a closed path (chords no longer than max_step), without
    repeating the start point — a ~1e-14 mm closing edge reads as a
    self-intersection to shapely."""
    pts: list[Point] = []
    for seg in segments:
        s = seg.sample(max_step)
        pts.extend(s if not pts else s[1:])
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < eps:
        pts.pop()
    return pts


# ── Standards ─────────────────────────────────────────────────────────────────

# ISO 14:1982 Table 1, nominal dimensions (N, d, D, B), light and medium series.
ISO14_LIGHT = [
    (6, 23, 26, 6), (6, 26, 30, 6), (6, 28, 32, 7), (8, 32, 36, 6), (8, 36, 40, 7),
    (8, 42, 46, 8), (8, 46, 50, 9), (8, 52, 58, 10), (8, 56, 62, 10), (8, 62, 68, 12),
    (10, 72, 78, 12), (10, 82, 88, 12), (10, 92, 98, 14), (10, 102, 108, 16),
    (10, 112, 120, 18),
]
ISO14_MEDIUM = [
    (6, 11, 14, 3), (6, 13, 16, 3.5), (6, 16, 20, 4), (6, 18, 22, 5), (6, 21, 25, 5),
    (6, 23, 28, 6), (6, 26, 32, 6), (6, 28, 34, 7), (8, 32, 38, 6), (8, 36, 42, 7),
    (8, 42, 48, 8), (8, 46, 54, 9), (8, 52, 60, 10), (8, 56, 65, 10), (8, 62, 72, 12),
    (10, 72, 82, 12), (10, 82, 92, 12), (10, 92, 102, 14), (10, 102, 112, 16),
    (10, 112, 125, 18),
]
ISO14_SOURCE = "ISO 14:1982 Table 1"

# ISO 4156 modules (Introduction): 30° and 37.5°, and 45°.
MODULES = {30: (0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8, 10),
           37.5: (0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8, 10),
           45: (0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5)}
# (pressure angle, root) -> (major k, major basis, minor k, minor basis, root fillet / m)
# Internal major = m(z + k_major); internal minor = m(z − k_minor).
INVOLUTE = {
    (30, "flat"): (1.5, STD, 1.0, SEC, 0.2),
    (30, "fillet"): (1.8, STD, 1.0, SEC, 0.2),
    (37.5, "fillet"): (1.4, STD, 0.8, EST, 0.4),
    (45, "fillet"): (1.2, STD, 0.6, EST, 0.3),
}
INVOLUTE_SOURCES = {
    STD: "ISO 4156-1:2021 Table 1 (internal major diameter)",
    SEC: "ANSI B92.1 internal minor diameter (N − 1)/P, via Engineers Edge",
    EST: "The looser published figure; ISO 4156's table isn't in the public preview",
}


@dataclass(frozen=True)
class Spline:
    kind: str                  # straight / involute
    n: int                     # slots (straight) or teeth (involute)
    minor: float               # mm: the hole's smallest diameter (tooth tips / centring)
    major: float               # mm: the hole's largest diameter (slot bottoms / roots)
    width: float = 0.0         # straight: slot width B
    module: float = 0.0        # involute
    pressure: float = 30.0
    root: str = "flat"

    @property
    def pitch_diameter(self) -> float:
        return self.module * self.n

    def label(self) -> str:
        if self.kind == "straight":
            return f"{self.n} × {self.minor:g} × {self.major:g} (B {self.width:g})"
        return f"m {self.module:g} × {self.n}T, {self.pressure:g}° {self.root} root"


def straight(n: int, d: float, D: float, b: float) -> Spline:
    if n < 3:
        raise ValueError("a straight-sided spline needs at least 3 slots")
    if not 0 < d < D:
        raise ValueError("the spline's major diameter must be larger than its minor")
    if not 0 < b < d * math.sin(math.pi / n):
        raise ValueError(f"{n} slots {b:g} mm wide don't fit round a {d:g} mm minor diameter")
    return Spline("straight", n, d, D, width=b)


def involute(m: float, z: int, pressure: float = 30.0, root: str = "flat") -> Spline:
    key = (pressure, root if pressure == 30 else "fillet")
    if key not in INVOLUTE:
        raise ValueError("pressure angle must be 30°, 37.5° or 45°")
    if m <= 0:
        raise ValueError("the spline module must be more than 0")
    if not 6 <= z <= 100:
        raise ValueError("an involute spline has 6 to 100 teeth")
    k_major, _, k_minor, _, _ = INVOLUTE[key]
    return Spline("involute", z, m * (z - k_minor), m * (z + k_major), module=m,
                  pressure=pressure, root=key[1])


def basis(sp: Spline) -> dict[str, tuple[str, str]]:
    """Which figures are the standard's, and which are approximate:
    {"major": (STD/SEC/EST, source), "minor": (...)}."""
    if sp.kind == "straight":
        return {"major": (STD, ISO14_SOURCE), "minor": (STD, ISO14_SOURCE)}
    k = INVOLUTE[(sp.pressure, sp.root)]
    return {"major": (k[1], INVOLUTE_SOURCES[k[1]]), "minor": (k[3], INVOLUTE_SOURCES[k[3]])}


def presets() -> dict:
    """The page's choices: ISO 14's series and ISO 4156's modules."""
    return {"straight": {"light": ISO14_LIGHT, "medium": ISO14_MEDIUM, "source": ISO14_SOURCE},
            "involute": {"modules": {str(k): v for k, v in MODULES.items()},
                         "pressure_angles": [30, 37.5, 45]}}


# ── Outlines ──────────────────────────────────────────────────────────────────

def _straight_path(sp: Spline) -> list[Segment]:
    """Minor circle between the slots; each slot two radial-parallel walls
    and its bottom on the major circle."""
    r, R, h = sp.minor / 2, sp.major / 2, sp.width / 2
    a_in = math.asin(h / r)                  # half-angle of a slot mouth on the minor circle
    a_out = math.asin(h / R)                 # … and of its bottom on the major circle
    step = 2 * math.pi / sp.n
    out: list[Segment] = []
    for k in range(sp.n):
        c = k * step
        p_in_lo = (r * math.cos(c - a_in), r * math.sin(c - a_in))
        p_out_lo = (R * math.cos(c - a_out), R * math.sin(c - a_out))
        p_out_hi = (R * math.cos(c + a_out), R * math.sin(c + a_out))
        p_in_hi = (r * math.cos(c + a_in), r * math.sin(c + a_in))
        out.append(Line(p_in_lo, p_out_lo))
        out.append(Arc((0.0, 0.0), R, c - a_out, 2 * a_out))
        out.append(Line(p_out_hi, p_in_hi))
        out.append(Arc((0.0, 0.0), r, c + a_in, step - 2 * a_in))   # land to the next slot
    return out


def _inv(a):
    return math.tan(a) - a


def _involute_point(rb, t, sign, c0):
    """Point on the involute of the base circle rb at roll parameter t, turned
    to start at polar angle c0 on the base circle, unwinding in `sign`."""
    x = rb * (math.cos(t) + t * math.sin(t))
    y = rb * (math.sin(t) - t * math.cos(t))
    ang = math.atan2(y, x) * sign + c0
    return (math.hypot(x, y) * math.cos(ang), math.hypot(x, y) * math.sin(ang))


def _arc3(p0, p1, p2):
    """The circle through three points, as an Arc from p0 through p1 to p2."""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(by - uy, bx - ux)
    a2 = math.atan2(cy - uy, cx - ux)
    s1 = math.remainder(a1 - a0, 2 * math.pi)
    s2 = math.remainder(a2 - a1, 2 * math.pi)
    return Arc((ux, uy), r, a0, s1 + s2)


def _flank_arcs(point_at, t0, t1, depth=0) -> list[Arc]:
    """Circular arcs through the curve point_at(t), t0..t1, each within
    FLANK_TOL of it (checked at the quarter points), split until they are."""
    tm = (t0 + t1) / 2
    arc = _arc3(point_at(t0), point_at(tm), point_at(t1))
    worst = 0.0
    for f in (0.25, 0.75, 0.125, 0.875):
        p = point_at(t0 + f * (t1 - t0))
        worst = max(worst, abs(math.dist(p, arc.center) - arc.radius))
    if worst <= FLANK_TOL or depth > 8:
        return [arc]
    return _flank_arcs(point_at, t0, tm, depth + 1) + _flank_arcs(point_at, tm, t1, depth + 1)


def _involute_path(sp: Spline) -> list[Segment]:
    """Spaces (where the shaft's teeth go) round the pitch circle, basic
    width πm/2 there, between internal teeth whose tips lie on the minor
    circle; each space's flanks are involutes of the base circle, its root
    on the major circle."""
    m, z, alpha = sp.module, sp.n, math.radians(sp.pressure)
    rp = m * z / 2
    rb = rp * math.cos(alpha)
    r_min, r_maj = sp.minor / 2, sp.major / 2
    half_space = (math.pi * m / 2) / 2 / rp          # half the space's angle at the pitch circle
    phi_p = _inv(alpha)
    step = 2 * math.pi / z

    def roll(r):                                    # involute roll parameter at radius r
        r = max(r, rb)
        return math.sqrt((r / rb) ** 2 - 1)

    t_lo, t_hi = roll(max(r_min, rb)), roll(r_maj)
    out: list[Segment] = []
    for k in range(z):
        c = k * step
        c_right = c - half_space - phi_p
        c_left = c + half_space + phi_p
        right = lambda t, c0=c_right: _involute_point(rb, t, +1, c0)   # noqa: E731
        left = lambda t, c0=c_left: _involute_point(rb, t, -1, c0)     # noqa: E731
        up = _flank_arcs(right, t_lo, t_hi)              # tip → root on the right
        down = [a.reversed() for a in reversed(_flank_arcs(left, t_lo, t_hi))]
        if r_min < rb:                                   # below the base circle: radial line
            p0 = (r_min * math.cos(c_right), r_min * math.sin(c_right))
            out.append(Line(p0, up[0].start))
        out += up
        a_r = math.atan2(up[-1].end[1], up[-1].end[0])
        a_l = math.atan2(down[0].start[1], down[0].start[0])
        out.append(Arc((0.0, 0.0), r_maj, a_r, math.remainder(a_l - a_r, 2 * math.pi)))
        out += down
        if r_min < rb:
            p1 = (r_min * math.cos(c_left), r_min * math.sin(c_left))
            out.append(Line(down[-1].end, p1))
        # The internal tooth's tip, on the minor circle, to the next space.
        e = out[-1].end
        nxt_right = c + step - half_space - phi_p
        start_next = (_involute_point(rb, t_lo, +1, nxt_right) if r_min >= rb
                      else (r_min * math.cos(nxt_right), r_min * math.sin(nxt_right)))
        a0 = math.atan2(e[1], e[0])
        a1 = math.atan2(start_next[1], start_next[0])
        out.append(Arc((0.0, 0.0), r_min, a0, math.remainder(a1 - a0, 2 * math.pi) % (2 * math.pi)))
    return out


def path(sp: Spline) -> list[Segment]:
    """The hole's outline: exact lines and arcs, closed, counter-clockwise."""
    return _straight_path(sp) if sp.kind == "straight" else _involute_path(sp)
