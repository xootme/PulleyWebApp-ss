"""
assembly.py — the manifest for one assembly STEP of a whole design (the
owner, 2026-10-06: the STEP download becomes one file, a real assembly, its
parts placed as assembled; small_step's `assemble`, Documents\\CCT_Assembly_STEP_Handoff.md).

    m = manifest(args)            # the page's query
    m == {"name": "HTD-5M-20T-30T",
          "parts": [{"name": ..., "make": {...}  or  "extrude": {...},
                     "placement": {"origin": [x, y, z], "rotate_z_deg": deg},
                     "instances": [placement, ...]}, ...]}

Every part is in the frame the app's STL and small_step's pulley STEP share
(checked to 1e-3 mm on hub, flange, metal plate and spline designs): the bore
on the z axis, the pulley body from z = 0 up. The parts:

  pulley n   "make": {"kind": "pulley", "pulley": n} — the STEP the worker already
             makes (its flanges, plates and separate top inside). A drive's are
             placed as the existing two-pulley STEP placed them: pulley 2 at the
             centre distance on +x, each turned so its teeth mesh with the belt.
  belt       a drive's, "make": {"kind": "belt"}, where the belt STEP puts it.
  shaft      a splined bore's sample shaft (nominal: STEP is never compensated),
             as stacked "sections" of one solid — the spline's outline, and at each
             ring's groove the outline clipped to the groove's floor (d2), so the
             ring sits in a groove rather than through the teeth.
  washer     the splined washer under a ring: its OD less the bore's spline.
  ring       the retaining ring as fitted in its groove (ring_outline.installed),
             at each ringed face.

Placements come from where the 3D view puts the same parts
(spline_parts.preview_parts, retaining_rings.stack()). A part that appears
more than once (a ring on both faces, the same ring on both pulleys) is one
part with several occurrences ("instances").

Loops are ring_outline's JSON form: ["line", [x0, y0], [x1, y1]] or
["arc", [cx, cy], r, start_deg, end_deg, ccw]; an outer boundary anticlockwise,
holes clockwise.
"""
from __future__ import annotations

import math

from cct_common import retaining_rings as rr
from cct_common import ring_outline as ro
from cct_common import splines as spl


# ── outlines as JSON loops ───────────────────────────────────────────────────

def seg_json(seg) -> list:
    """A cct_common.splines Line / Arc as ring_outline's JSON segment."""
    if isinstance(seg, spl.Line):
        return ["line", list(seg.p0), list(seg.p1)]
    a0 = math.degrees(seg.start_angle)
    full = abs(abs(seg.sweep) - 2 * math.pi) < 1e-12
    a1 = a0 if full else math.degrees(seg.start_angle + seg.sweep)
    return ["arc", list(seg.center), seg.radius, a0, a1, seg.sweep > 0]


def loop_json(segs) -> list:
    return [seg_json(s) for s in segs]


def reversed_loop(segs) -> list:
    """A closed path run the other way round (an outline used as a hole)."""
    return [s.reversed() for s in reversed(segs)]


def circle(r: float, ccw: bool = True) -> list:
    return [spl.Arc((0.0, 0.0), r, 0.0, 2 * math.pi if ccw else -2 * math.pi)]


# ── an outline clipped to a circle (a shaft's groove) ────────────────────────

def _hits(seg, r: float) -> list:
    """Parameters t in (0, 1) where the segment crosses the circle of radius r on the origin."""
    eps = 1e-12
    if isinstance(seg, spl.Line):
        (x0, y0), (x1, y1) = seg.p0, seg.p1
        dx, dy = x1 - x0, y1 - y0
        a, b, c = dx * dx + dy * dy, 2 * (x0 * dx + y0 * dy), x0 * x0 + y0 * y0 - r * r
        disc = b * b - 4 * a * c
        if a < eps or disc < 0:
            return []
        q = math.sqrt(disc)
        return sorted(t for t in ((-b - q) / (2 * a), (-b + q) / (2 * a)) if eps < t < 1 - eps)
    cx, cy = seg.center
    L = math.hypot(cx, cy)
    R = seg.radius
    if L < 1e-12:
        return []                              # concentric: inside or outside throughout
    k = (r * r - L * L - R * R) / (2 * R)       # L cos(theta - gamma) = k
    if abs(k) > L:
        return []
    gamma, d = math.atan2(cy, cx), math.acos(k / L)
    out = []
    for th in (gamma + d, gamma - d):
        off = (th - seg.start_angle) % (2 * math.pi)
        if seg.sweep < 0:
            off -= 2 * math.pi
        t = off / seg.sweep
        if eps < t < 1 - eps:
            out.append(t)
    return sorted(out)


def _piece(seg, t0: float, t1: float):
    if isinstance(seg, spl.Line):
        (x0, y0), (x1, y1) = seg.p0, seg.p1
        at = lambda t: (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)   # noqa: E731
        return spl.Line(at(t0), at(t1))
    return spl.Arc(seg.center, seg.radius, seg.start_angle + seg.sweep * t0, seg.sweep * (t1 - t0))


def _mid(seg):
    if isinstance(seg, spl.Line):
        return ((seg.p0[0] + seg.p1[0]) / 2, (seg.p0[1] + seg.p1[1]) / 2)
    return seg.point_at(seg.start_angle + seg.sweep / 2)


def clip_to_circle(segs, r: float) -> list:
    """The part of a closed anticlockwise outline (star-shaped about the origin,
    as a spline's is) inside the circle of radius r: exact lines and arcs, the
    outline's own where they lie inside, arcs of the circle where they don't."""
    pieces = []
    for s in segs:
        ts = [0.0] + _hits(s, r) + [1.0]
        pieces += [_piece(s, a, b) for a, b in zip(ts, ts[1:]) if b - a > 1e-12]
    inside = [math.hypot(*_mid(p)) < r for p in pieces]
    if all(inside):
        return list(segs)
    if not any(inside):
        return circle(r)
    n = len(pieces)
    start = next(i for i in range(n) if inside[i] and not inside[i - 1])   # where the outline comes back in
    out, i, gap_from = [], start, None
    for _ in range(n):
        if inside[i]:
            if gap_from is not None:                       # close the gap along the circle
                p, q = gap_from, pieces[i].start
                a0, a1 = math.atan2(p[1], p[0]), math.atan2(q[1], q[0])
                out.append(spl.Arc((0.0, 0.0), r, a0, (a1 - a0) % (2 * math.pi)))
                gap_from = None
            out.append(pieces[i])
        elif gap_from is None:
            gap_from = out[-1].end if out else pieces[i - 1].end
        i = (i + 1) % n
    if gap_from is not None:                               # back round to the start
        p, q = gap_from, out[0].start
        a0, a1 = math.atan2(p[1], p[0]), math.atan2(q[1], q[0])
        out.append(spl.Arc((0.0, 0.0), r, a0, (a1 - a0) % (2 * math.pi)))
    return out


# ── a part's STEP from its outline (small_step extrude) ──────────────────────

class ExtrudeError(RuntimeError):
    """small_step couldn't extrude a part: its message, for the download's error."""


def ss_binary() -> str | None:
    """The small_step binary the worker would run: SMALL_STEP_BIN, else the
    repo's Linux build (Cloud Run sets SMALL_STEP_BIN to it)."""
    import os
    env = os.environ.get("SMALL_STEP_BIN", "").strip()
    if env and os.path.isfile(env):
        return env
    repo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "small_step_linux")
    return repo if os.name != "nt" and os.path.isfile(repo) else None


_HELP: dict = {}


def _has_command(ss_bin: str | None, cmd: str) -> bool:
    """Does this binary have `cmd`? Asked of the binary itself: small_step
    0.7.0 shipped with and without extrude and assemble under one version string."""
    import subprocess
    if not ss_bin:
        return False
    if ss_bin not in _HELP:
        try:
            out = subprocess.run([ss_bin, "--help"], capture_output=True, text=True, timeout=10)
            _HELP[ss_bin] = out.stdout + out.stderr
        except (OSError, subprocess.SubprocessError):
            _HELP[ss_bin] = ""
    return f"{cmd}:" in _HELP[ss_bin]


def can_extrude(ss_bin: str | None) -> bool:
    return _has_command(ss_bin, "extrude")


def can_assemble(ss_bin: str | None) -> bool:
    return _has_command(ss_bin, "assemble")


_CAN_SECTIONS: dict = {}


def can_sections(ss_bin: str | None) -> bool:
    """Does this binary's extrude take stacked "sections" (handoff item 1,
    added 2026-10-06)? Tried once on a small square."""
    if not can_extrude(ss_bin):
        return False
    if ss_bin not in _CAN_SECTIONS:
        sq = [["line", [0, 0], [1, 0]], ["line", [1, 0], [1, 1]], ["line", [1, 1], [0, 1]], ["line", [0, 1], [0, 0]]]
        try:
            extrude_step({"name": "probe", "sections": [{"z0": 0.0, "z1": 1.0, "loops": [sq]},
                                                         {"z0": 1.0, "z1": 2.0, "loops": [sq]}]}, ss_bin)
            _CAN_SECTIONS[ss_bin] = True
        except ExtrudeError:
            _CAN_SECTIONS[ss_bin] = False
    return _CAN_SECTIONS[ss_bin]


def extrude_step(spec: dict, ss_bin: str | None = None) -> bytes:
    """One part's STEP from an extrude spec (ring_outline.Outline.as_dict(), the
    washer's, the shaft's): `small_step extrude --json part.json -o part.step`."""
    import json
    import os
    import subprocess
    import tempfile
    ss_bin = ss_bin or ss_binary()
    if not can_extrude(ss_bin):
        raise ExtrudeError(f"this small_step ({ss_bin or 'none found'}) can't extrude")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "part.json"), os.path.join(tmp, "part.step")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        r = subprocess.run([ss_bin, "extrude", "--json", src, "-o", out], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.isfile(out):
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            raise ExtrudeError(f"{spec.get('name', 'part')}: " + (msg[-1] if msg else f"exit {r.returncode}"))
        with open(out, "rb") as f:
            return f.read()


# ── the assembly STEP (small_step assemble) ──────────────────────────────────

class AssembleError(ExtrudeError):
    """small_step couldn't assemble the parts: its message, for the download's error."""


def lower_sections(m: dict) -> dict:
    """The manifest for a small_step whose extrude takes no stacked sections:
    each section its own part ("<name>-1", "<name>-2" …), lifted to its z0.
    The shaft then comes in pieces, but each is the right shape and the rings
    still sit in grooves; with sections it is one solid again."""
    def lifted(pl, dz):
        o = list(pl.get("origin", [0.0, 0.0, 0.0]))
        return dict(pl, origin=[o[0], o[1], o[2] + dz])

    parts = []
    for p in m["parts"]:
        ex = p.get("extrude")
        if not ex or "sections" not in ex:
            parts.append(p)
            continue
        for i, s in enumerate(ex["sections"], 1):
            name = f"{p['name']}-{i}"
            q = {"name": name, "extrude": {"name": name, "thickness": s["z1"] - s["z0"], "loops": s["loops"]},
                 "placement": lifted(p.get("placement", {}), s["z0"])}
            if p.get("instances"):
                q["instances"] = [lifted(pl, s["z0"]) for pl in p["instances"]]
            parts.append(q)
    return dict(m, parts=parts)


def single_make(m: dict) -> dict | None:
    """The one part when the manifest is a single made part (one pulley ticked
    alone): its STEP is the file, no assembly around it."""
    ps = m["parts"]
    return ps[0] if len(ps) == 1 and "make" in ps[0] and not ps[0].get("instances") else None


def assemble_step(m: dict, make_step, ss_bin: str | None = None) -> bytes:
    """The manifest as one assembly STEP. make_step(make) -> bytes builds each
    "make" part (the worker's pulley and belt STEPs); they are written beside
    the manifest and named by path, the extrude parts go in as they are:
    `small_step assemble --json manifest.json -o assembly.step`."""
    import json
    import os
    import subprocess
    import tempfile
    lone = single_make(m)
    if lone:                                     # nothing to assemble: the part's own STEP, in its own frame
        return make_step(lone["make"])
    ss_bin = ss_bin or ss_binary()
    if not can_assemble(ss_bin):
        raise AssembleError(f"this small_step ({ss_bin or 'none found'}) can't assemble")
    if any("sections" in (p.get("extrude") or {}) for p in m["parts"]) and not can_sections(ss_bin):
        m = lower_sections(m)
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, p in enumerate(m["parts"]):
            q = {k: v for k, v in p.items() if k != "make"}
            if "make" in p:
                q["step"] = f"part{i}.step"
                with open(os.path.join(tmp, q["step"]), "wb") as f:
                    f.write(make_step(p["make"]))
            parts.append(q)
        src, out = os.path.join(tmp, "manifest.json"), os.path.join(tmp, "assembly.step")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(dict(m, parts=parts), f)
        r = subprocess.run([ss_bin, "assemble", "--json", src, "-o", out], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.isfile(out):
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            raise AssembleError("assembly: " + (msg[-1] if msg else f"exit {r.returncode}"))
        with open(out, "rb") as f:
            return f.read()


# ── the manifest ─────────────────────────────────────────────────────────────

def _place(origin_x: float, z: float, rot_deg: float) -> dict:
    return {"origin": [origin_x, 0.0, z], "rotate_z_deg": rot_deg}


def _add(parts: list, part: dict) -> None:
    """One part per shape: a second occurrence of the same name is an instance."""
    for p in parts:
        if p["name"] == part["name"]:
            p.setdefault("instances", []).append(part["placement"])
            return
    parts.append(part)


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

    # the shaft: from below the bottom face to above the top, a groove at each ring
    below, above = ends(sp)
    grooves = []
    if rt:
        for face in rt["faces"]:
            g0, g1 = _span_z(face, span, rt["stack"][face]["groove"])
            grooves.append((lo + g0, lo + g1))
    cuts = sorted({lo - below, hi + above} | {z for g in grooves for z in g})
    sections = []
    for z0, z1 in zip(cuts, cuts[1:]):
        if z1 - z0 < 1e-9:
            continue
        in_groove = any(g0 - 1e-9 <= z0 and z1 <= g1 + 1e-9 for g0, g1 in grooves)
        loop = clip_to_circle(shaft, rt["d2"] / 2.0) if in_groove else shaft
        sections.append({"z0": z0, "z1": z1, "loops": [loop_json(loop)], "groove": in_groove})
    add(f"sh{n}", {"name": f"{stem}-spline-shaft", "extrude": {"name": f"{stem}-spline-shaft",
                                                             "sections": sections},
                 "placement": _place(x, 0.0, rot)})
    if not rt:
        return
    ring = rr.for_spline(sp_obj)
    fitted = ro.installed(ring).as_dict()
    for face in rt["faces"]:
        st = rt["stack"][face]
        if st["washer"]:
            w0 = lo + _span_z(face, span, st["washer"])[0]
            add(f"wa{n}", {"name": f"{stem}-spline-washer",
                         "extrude": {"name": f"{stem}-spline-washer", "thickness": rt["washer_t"],
                                     "loops": [loop_json(circle(rt["washer_od"] / 2.0)),
                                               loop_json(reversed_loop(spl.path(sp_obj)))]},
                         "placement": _place(x, w0, rot)})
        r0 = lo + _span_z(face, span, st["ring"])[0]
        add(f"sh{n}", {"name": fitted["name"], "extrude": fitted, "placement": _place(x, r0, rot)})   # in the shaft's groove


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
                      "placement": _place(x[n], 0.0, rot[n])})
    if belt:
        add("belt", {"name": f"{family}-{pitch}-belt", "make": {"kind": "belt"},
                     "placement": _place(0.0, 0.0, 0.0)})
    for n in teeth:
        _spline_parts(args, n, stems[n], x[n], rot[n], add)
    return {"name": name, "parts": parts}
