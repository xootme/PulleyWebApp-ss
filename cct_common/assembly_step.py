"""
assembly_step.py — a design's assembly STEP through small_step (`extrude`,
`assemble`), and a splined part's sample shaft as ONE part in it.

Timing Pulley Generator's exporters/assembly.py, shared (the owner,
2026-10-11) so every app with a splined bore builds the shaft the same way.

The shaft is one PART (the owner: "the fix to make splined shafts a single
part in step files … made common"). Its cross-section changes along z —
shaft, groove, shaft, groove, shaft for two rings — so it is several prisms;
small_step's `bodies` (c03179c, with `z0`) puts them under ONE product, each
built at its own height, the part's placement moving them together. At each
ring's groove the outline is clipped to the groove's floor d2
(ring_outline.groove_section), so the ring sits in a groove rather than
through the teeth. A small_step without `bodies` gets each body as its own
part (split_bodies): right shapes, the shaft in pieces. (The apps' earlier
"sections" extrude was never built into small_step — 0.12.1 answers
`missing key "thickness"` — so they always fell back to pieces.)

    from cct_common import assembly_step as asm, ring_outline as ro

    parts = []
    asm.add_part(parts, asm.shaft_part("shaft", shaft_outline, ring, z_from, z_to, grooves,
                                       asm.place(x, 0.0, rot), "#9aa3ad"))
    fitted = ro.installed(ring).as_dict()                  # the ring, as fitted
    asm.add_part(parts, {"name": fitted["name"], "extrude": fitted,
                         "placement": asm.place(x, z_ring, rot), "color": "#3a3f45"})
    step = asm.assemble_step({"name": "design", "parts": parts}, make_step, binary)

A manifest's parts are {"name", "make" | "extrude" | "bodies", "placement",
"instances", "color"}: a "make" part is built by the app (make_step(make,
color) -> STEP bytes: its gear, pulley, sprocket …), an "extrude" part by
small_step from its loops (ring_outline's JSON form), a "bodies" part from
several extrudes under one product. A name met twice is one part with
several occurrences (add_part).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .ring_outline import groove_section, loop_json
from .retaining_rings import Ring

TIMEOUT_S = 120


# ── the shaft as one part ────────────────────────────────────────────────────

def shaft_sections(outline, ring: Ring | None, z_from: float, z_to: float,
                   grooves=()) -> list:
    """The sample shaft from z_from to z_to as stacked sections: cut at each
    groove's ends, a section inside a groove (z0, z1) the outline clipped to
    the ring's d2, every other the outline itself. Each is {"z0", "z1",
    "loops": [loop], "groove": bool}."""
    grooves = [tuple(g) for g in grooves]
    if grooves and ring is None:
        raise ValueError("grooves need the ring whose floor d2 they're cut to")
    cuts = sorted({z_from, z_to} | {z for g in grooves for z in g if z_from < z < z_to})
    whole = loop_json(outline)
    clipped = loop_json(groove_section(outline, ring)) if grooves else None
    out = []
    for z0, z1 in zip(cuts, cuts[1:]):
        if z1 - z0 < 1e-9:
            continue
        in_groove = any(g0 - 1e-9 <= z0 and z1 <= g1 + 1e-9 for g0, g1 in grooves)
        out.append({"z0": z0, "z1": z1, "loops": [clipped if in_groove else whole],
                    "groove": in_groove})
    return out


def shaft_part(name: str, outline, ring: Ring | None, z_from: float, z_to: float,
               grooves=(), placement: dict | None = None, color: str | None = None) -> dict:
    """The sample shaft as ONE manifest part: a body per section
    (shaft_sections), each extruded at its own z0 in the part's frame."""
    bodies = [{"extrude": {"name": name, "z0": s["z0"], "thickness": s["z1"] - s["z0"],
                           "loops": s["loops"]}}
              for s in shaft_sections(outline, ring, z_from, z_to, grooves)]
    part = {"name": name, "bodies": bodies, "placement": placement or place(0.0, 0.0, 0.0)}
    if color:
        part["color"] = color
    return part


# ── the manifest ─────────────────────────────────────────────────────────────

def place(x: float, z: float, rot_deg: float, y: float = 0.0) -> dict:
    """A part's placement: its frame's origin, turned rot_deg about z."""
    return {"origin": [x, y, z], "rotate_z_deg": rot_deg}


def add_part(parts: list, part: dict) -> None:
    """One part per shape: a second occurrence of the same name is an instance."""
    for p in parts:
        if p["name"] == part["name"]:
            p.setdefault("instances", []).append(part["placement"])
            return
    parts.append(part)


def split_bodies(m: dict) -> dict:
    """The manifest for a small_step without `bodies`: each body its own part
    ("<name>-1", "<name>-2" …), lifted to its z0 (an extrude without `z0`
    builds from 0). The shaft then comes in pieces, but each is the right
    shape and the rings still sit in grooves; with `bodies` it is one part."""
    def lifted(pl, dz):
        o = list(pl.get("origin", [0.0, 0.0, 0.0]))
        return dict(pl, origin=[o[0], o[1], o[2] + dz])

    parts = []
    for p in m["parts"]:
        if "bodies" not in p:
            parts.append(p)
            continue
        for i, body in enumerate(p["bodies"], 1):
            name = f"{p['name']}-{i}"
            ex = dict(body["extrude"], name=name)
            dz = ex.pop("z0", 0.0)
            q = {"name": name, "extrude": ex, "placement": lifted(p.get("placement", {}), dz)}
            if p.get("color"):
                q["color"] = p["color"]
            if p.get("instances"):
                q["instances"] = [lifted(pl, dz) for pl in p["instances"]]
            parts.append(q)
    return dict(m, parts=parts)


def single_make(m: dict) -> dict | None:
    """The one part when the manifest is a single made part (one ticked
    alone): its STEP is the file, no assembly around it."""
    ps = m["parts"]
    return ps[0] if len(ps) == 1 and "make" in ps[0] and not ps[0].get("instances") else None


# ── the binary ───────────────────────────────────────────────────────────────

class ExtrudeError(RuntimeError):
    """small_step couldn't extrude a part: its message, for the download's error."""


class AssembleError(ExtrudeError):
    """small_step couldn't assemble the parts: its message, for the download's error."""


_HELP: dict = {}
_CAN_BODIES: dict = {}


def _exe(binary) -> str:
    """The binary as a path that still works from another directory."""
    b = str(binary)
    return os.path.abspath(b) if os.path.isfile(b) else b


def has_command(binary, cmd: str) -> bool:
    """Does this binary have `cmd`? Asked of the binary itself: small_step
    0.7.0 shipped with and without extrude and assemble under one version string."""
    if not binary:
        return False
    key = str(binary)
    if key not in _HELP:
        try:
            out = subprocess.run([_exe(key), "--help"], capture_output=True, text=True, timeout=10)
            _HELP[key] = out.stdout + out.stderr
        except (OSError, subprocess.SubprocessError):
            _HELP[key] = ""
    return f"{cmd}:" in _HELP[key]


def can_extrude(binary) -> bool:
    return has_command(binary, "extrude")


def can_assemble(binary) -> bool:
    return has_command(binary, "assemble") and can_extrude(binary)


def can_bodies(binary) -> bool:
    """Does this binary's assemble take a part's `bodies`, each at its `z0`
    (small_step c03179c)? Tried once per binary: two squares stacked in one part."""
    if not can_assemble(binary):
        return False
    key = str(binary)
    if key not in _CAN_BODIES:
        sq = [["line", [0, 0], [1, 0]], ["line", [1, 0], [1, 1]], ["line", [1, 1], [0, 1]], ["line", [0, 1], [0, 0]]]
        probe = {"name": "probe", "parts": [{"name": "p", "placement": place(0.0, 0.0, 0.0), "bodies": [
            {"extrude": {"name": "p", "z0": z, "thickness": 1.0, "loops": [sq]}} for z in (0.0, 1.0)]}]}
        try:
            _CAN_BODIES[key] = _assemble(probe, key, TIMEOUT_S).count(b"MANIFOLD_SOLID_BREP") == 2
        except AssembleError:
            _CAN_BODIES[key] = False
    return _CAN_BODIES[key]


def _run(binary, args: list, what: str, err, timeout: float, cwd=None):
    try:
        return subprocess.run([_exe(binary)] + args, capture_output=True, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise err(f"{what}: small_step took longer than {timeout:g} s") from None
    except OSError as e:
        raise err(f"{what}: {e}") from None


def _failed(r, out: str, what: str, err):
    if r.returncode != 0 or not os.path.isfile(out):
        msg = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        raise err(f"{what}: " + (msg[-1] if msg else f"exit {r.returncode}"))


def extrude_step(spec: dict, binary, timeout: float = TIMEOUT_S) -> bytes:
    """One part's STEP from an extrude spec (ring_outline.Outline.as_dict(), a
    washer's): `small_step extrude --json part.json -o part.step`."""
    if not can_extrude(binary):
        raise ExtrudeError(f"this small_step ({binary or 'none found'}) can't extrude")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "part.json"), os.path.join(tmp, "part.step")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        what = spec.get("name", "part")
        r = _run(binary, ["extrude", "--json", src, "-o", out], what, ExtrudeError, timeout)
        _failed(r, out, what, ExtrudeError)
        with open(out, "rb") as f:
            return f.read()


def _assemble(m: dict, binary, timeout: float, files: dict | None = None) -> bytes:
    """`small_step assemble --json manifest.json -o assembly.step`, with
    `files` ({name: STEP bytes}) written beside the manifest."""
    with tempfile.TemporaryDirectory() as tmp:
        for fname, data in (files or {}).items():
            with open(os.path.join(tmp, fname), "wb") as f:
                f.write(data)
        src, out = os.path.join(tmp, "manifest.json"), os.path.join(tmp, "assembly.step")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(m, f)
        r = _run(binary, ["assemble", "--json", src, "-o", out], "assembly", AssembleError, timeout, cwd=tmp)
        _failed(r, out, "assembly", AssembleError)
        with open(out, "rb") as f:
            return f.read()


def assemble_step(m: dict, make_step, binary, timeout: float = TIMEOUT_S) -> bytes:
    """The manifest as one assembly STEP. make_step(make, color) -> bytes
    builds each "make" part: alone (single_make) it is the whole file, in its
    own frame, painted `color`; in an assembly it is made with color None
    (small_step refuses a part coloured both in its file and in the
    manifest) and the manifest paints it. The made parts are written beside
    the manifest and named by path; extrude and bodies parts go in as they
    are (split_bodies first for a small_step without `bodies`)."""
    lone = single_make(m)
    if lone:
        return make_step(lone["make"], lone.get("color"))
    if not can_assemble(binary):
        raise AssembleError(f"this small_step ({binary or 'none found'}) can't assemble")
    if any("bodies" in p for p in m["parts"]) and not can_bodies(binary):
        m = split_bodies(m)
    parts, files = [], {}
    for i, p in enumerate(m["parts"]):
        q = {k: v for k, v in p.items() if k != "make"}
        if "make" in p:
            q["step"] = f"part{i}.step"
            files[q["step"]] = make_step(p["make"], None)
        parts.append(q)
    return _assemble(dict(m, parts=parts), binary, timeout, files)
