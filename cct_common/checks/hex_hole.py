"""A hex set-screw hole stands the same way in an app's STL and its STEP.

Every hub app cuts its hole twice, by different code: its own mesh cutter
for the STL and the 3D view, and small_step for the STEP. A hex turned 30°
weighs exactly the same, so a volume check never sees it. On 2026-10-16
SmallStep found Gear's STL hex turned 30° from its STEP, and Sprocket's
turned the same way (Gear's cutter was ported from Sprocket's): the STEP
stood corner-up, the STL had flats up.

The rule is `screws.hex_hole_outline`: corner-up, one corner along the
part's axis. `check()` takes points where one plane cuts each file across
the hole and reports where their corners point:

    from cct_common.checks import hex_hole

    frame = hex_hole.Frame(centre=(x0, 0, z), across=(0, 1, 0), along=(0, 0, 1))
    stl = hex_hole.mesh_section_points(mesh, frame)          # trimesh, in-process
    step = hex_hole.step_section_points(occ_python, path, frame)   # OpenCASCADE
    assert hex_hole.check(stl, step, frame, flats) == ""

`frame` is the cutting plane: through the hole's axis point `centre`, with
`along` the part's axis and `across` square to it and to the hole's axis
(their cross product). Pick `centre` mid-wall, clear of the bore and the
hub's outside, so only the hole is near it.
If the app's STEP is placed apart from its STL (moved along the axis, say),
cut the STEP with its own frame and pass it as `step_frame`.

The shape follows this package's: a pure `check(...)` returning a report
string, empty when the two agree; the calling test does the skipping (no
OpenCASCADE, no small_step) and the reporting.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

Vec = Sequence[float]

# An OpenCASCADE Python (an OCP install, as E-Box's .venv312) cuts the STEP
# and prints the section's vertices: python -c SCRIPT <step> ox oy oz nx ny nz
OCC_SECTION_SCRIPT = r'''
import json, sys
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
r = STEPControl_Reader()
if r.ReadFile(sys.argv[1]) != 1:
    sys.exit("could not read " + sys.argv[1])
r.TransferRoots()
o, n = [float(v) for v in sys.argv[2:5]], [float(v) for v in sys.argv[5:8]]
sec = BRepAlgoAPI_Section(r.OneShape(), gp_Pln(gp_Pnt(*o), gp_Dir(*n)))
sec.Build()
pts, ex = [], TopExp_Explorer(sec.Shape(), TopAbs_VERTEX)
while ex.More():
    p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(ex.Current()))
    pts.append([p.X(), p.Y(), p.Z()])
    ex.Next()
print(json.dumps(pts))
'''


@dataclass(frozen=True)
class Frame:
    """The cutting plane across a hole: `centre` on the hole's axis, `along`
    the part's axis, `across` square to it in the plane."""
    centre: Vec
    across: Vec
    along: Vec

    @property
    def normal(self) -> tuple:
        a, b = _unit(self.across), _unit(self.along)
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def to_2d(self, p: Vec) -> tuple:
        """(across, along) of a 3D point, from the centre."""
        d = [p[i] - self.centre[i] for i in range(3)]
        a, b = _unit(self.across), _unit(self.along)
        return (sum(d[i] * a[i] for i in range(3)), sum(d[i] * b[i] for i in range(3)))


def _unit(v: Vec) -> tuple:
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def corner_angles(points_2d: Iterable[Vec], flats: float, tol: float = 1e-3, merge: float = 0.5) -> list:
    """The hole's corners, in degrees round its centre from +across (90 is
    +along), from (across, along) points of a cut: the points within one
    corner radius (plus a little) of the centre, and of those the farthest
    out. A mesh (float32 vertices, a cut through a fan of triangles) puts
    several points at one corner a hair apart, so angles within `merge`
    degrees of each other, round the circle, are one corner: their mean,
    rounded to 0.1° (PlanetaryGear found ten "corners" on a sound hole
    without this)."""
    reach = flats / math.sqrt(3) * 1.25
    near = [(u, v, math.hypot(u, v)) for u, v in points_2d if math.hypot(u, v) < reach]
    if not near:
        return []
    far = max(r for _, _, r in near)
    angles = sorted(math.degrees(math.atan2(v, u)) % 360.0 for u, v, r in near if r > far - tol)
    groups = [[angles[0]]]
    for a in angles[1:]:
        if a - groups[-1][-1] <= merge:
            groups[-1].append(a)
        else:
            groups.append([a])
    if len(groups) > 1 and groups[0][0] + 360.0 - groups[-1][-1] <= merge:   # across 0°/360°
        groups[0] = [a - 360.0 for a in groups.pop()] + groups[0]
    out = []
    for g in groups:
        x = sum(math.cos(math.radians(a)) for a in g)
        y = sum(math.sin(math.radians(a)) for a in g)
        out.append(round(math.degrees(math.atan2(y, x)) % 360.0, 1) % 360.0)
    return sorted(out)


def corner_up(angles: Sequence[float], tol: float = 1.0) -> bool:
    """Six corners, one along the part's axis (90°) — `screws.hex_hole_outline`."""
    return len(angles) == 6 and all(
        min(abs((a - 90.0 - 60.0 * k + 180.0) % 360.0 - 180.0) for k in range(6)) <= tol for a in angles)


def check(stl_points: Iterable[Vec], step_points: Iterable[Vec], frame: Frame, flats: float,
          step_frame: Frame | None = None) -> str:
    """'' when both cuts show a corner-up hex; otherwise what each shows.
    `step_frame` is the STEP's own frame when the app places its STEP apart
    from its STL (WormGear's stands on z = 0, so its centre is shifted);
    `frame` serves both when it is left out."""
    stl = corner_angles([frame.to_2d(p) for p in stl_points], flats)
    sf = frame if step_frame is None else step_frame
    step = corner_angles([sf.to_2d(p) for p in step_points], flats)
    problems = []
    for name, angles in (("STL", stl), ("STEP", step)):
        if not corner_up(angles):
            problems.append(f"{name} hex corners at {angles}° (corner-up is 30, 90, 150, 210, 270, 330: "
                            "a corner along the part's axis)")
    return "; ".join(problems)


def mesh_section_points(mesh, frame: Frame) -> list:
    """Points where `frame`'s plane cuts a trimesh mesh (its segments' ends;
    trimesh.intersections needs no scipy, unlike mesh.section)."""
    from trimesh import intersections

    lines = intersections.mesh_plane(mesh, plane_normal=list(frame.normal), plane_origin=list(frame.centre))
    return [tuple(map(float, p)) for p in lines.reshape(-1, 3)]


def step_section_points(occ_python: str, step_path: str, frame: Frame, timeout: float = 300) -> list:
    """The vertices where `frame`'s plane cuts a STEP file, by OpenCASCADE in
    `occ_python` (OCP has no wheel for every Python, so it runs apart)."""
    args = [str(v) for v in (*frame.centre, *frame.normal)]
    run = subprocess.run([str(occ_python), "-c", OCC_SECTION_SCRIPT, str(step_path), *args],
                         capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise RuntimeError(f"OpenCASCADE section failed: {run.stderr.strip()[-500:]}")
    return [tuple(p) for p in json.loads(run.stdout)]
