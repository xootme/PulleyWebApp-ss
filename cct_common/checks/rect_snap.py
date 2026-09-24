"""D047: a circle locks onto a rectangle's side -- in Python and in JS.

The rule is implemented twice on purpose. E-Box Designer decides it
server-side in `geometry/holes.py::apply_rect_snap`, on a normalize pass
that runs on every model change. PCB Importer has no such pass -- marks
are drawn, held in the page and written into a board library without ever
going near a geometry endpoint -- so it carries a port in
`static/importer.js`.

The two are not independent. `snap_to_rect` and `rect_snapped` travel
with a mark into the library, and the Python re-derives them when E-Box
Designer imports that board. If the copies disagree, a circle attaches to
one side of a rectangle in the Importer and a different one in the
Designer, or attaches in one and not the other, and nothing reports it:
the enclosure simply comes out wrong.

So both are run over the same grid and the answers are diffed. The grid
is chosen to cover what a plausible-but-wrong port gets wrong:

  * the trigger is overlap with the circle's RADIUS, not its centre;
  * the derived diameter is the whole SIDE's length, so the resulting
    circle can be far larger than the one that triggered it (D048);
  * a derived circle that would leave the panel is skipped in favour of
    the next-nearest side, and if none fit the circle stays unsnapped --
    staying inside the panel outranks snapping;
  * it is re-derived from the current position every time, never
    remembered, which is what makes dragging away release it.

`check()` is pure: it takes the two repo paths and returns a report,
empty when they agree. Locating the repos, skipping, and failing are the
calling test's business -- see cct_common.checks' docstring.
"""
import sys
from pathlib import Path

from .. import parity

#: The panel the cases are placed on, in mm.
U, V = 60.0, 40.0

#: Rectangles to hang circles off, each chosen for a different reason.
RECTS = [
    dict(cx=30, cy=20, w=20, h=10),   # comfortably inside the panel
    dict(cx=8, cy=20, w=24, h=10),    # near the low-u edge: derived circle overhangs
    dict(cx=30, cy=20, w=50, h=10),   # a side too long to derive inside the depth
    dict(cx=45, cy=30, w=12, h=8),    # small, near a corner
]

#: The port's functions, in dependency order.
JS_FUNCTIONS = ["pcbRectSides", "pcbClosestPointOnSegment",
                "pcbFitsPanel", "pcbApplyRectSnap"]

#: What those close over in the running app. No case here is a walled
#: cutout, so the stub only has to make the real branch reachable.
JS_PREAMBLE = """
  const RECT_SNAP_TOL_MM = 1e-6;
  const isWalledCutout = (m) => !!(m && m.terminal);
"""

JS = """
const snap = buildCallable(PAYLOAD.importer, PAYLOAD.functions, {
  preamble: PAYLOAD.preamble, returns: 'pcbApplyRectSnap' });
const r6 = (n) => Math.round(n * 1e6) / 1e6;
emit(PAYLOAD.cases.map((c) => {
  const rect = { id: 'r1', shape: 'rect', ...PAYLOAD.rects[c.rect] };
  const circ = { id: 'c1', shape: 'circle', snap_to_rect: true,
                 cx: c.cx, cy: c.cy, r: c.r };
  const got = snap(circ, [circ, rect], PAYLOAD.u, PAYLOAD.v);
  return { snapped: !!got.rect_snapped, cx: r6(got.cx), cy: r6(got.cy),
           r: r6(got.r) };
}));
"""

#: Below this many snapping cases the grid is not exercising the rule,
#: and agreement between the two copies would prove nothing.
MIN_SNAPPING_CASES = 20


def cases():
    """A circle swept across each rectangle, at three radii."""
    out = []
    for ri in range(len(RECTS)):
        for cx in range(2, 59, 7):
            for cy in range(2, 39, 6):
                for rad in (1.5, 3.0, 6.0):
                    out.append({"rect": ri, "cx": float(cx), "cy": float(cy),
                                "r": rad})
    return out


def python_answers(designer, grid):
    """Run E-Box Designer's `apply_rect_snap` over the grid."""
    designer = str(Path(designer).resolve())
    if designer not in sys.path:
        sys.path.insert(0, designer)
    from geometry.holes import Hole, apply_rect_snap

    region = (0.0, 0.0, U, V)
    out = []
    for c in grid:
        rect = Hole(id="r1", face="front", shape="rect", **RECTS[c["rect"]])
        circ = Hole(id="c1", face="front", shape="circle",
                    cx=c["cx"], cy=c["cy"], r=c["r"], snap_to_rect=True)
        got = apply_rect_snap(circ, [circ, rect], region)
        out.append({"snapped": bool(got.rect_snapped), "cx": round(got.cx, 6),
                    "cy": round(got.cy, 6), "r": round(got.r, 6)})
    return out


def js_answers(importer_js, grid):
    """Run PCB Importer's port over the same grid, under node."""
    return parity.js_answers(JS, {
        "importer": str(Path(importer_js).resolve()), "functions": JS_FUNCTIONS,
        "preamble": JS_PREAMBLE, "rects": RECTS, "cases": grid, "u": U, "v": V,
    })


def check(designer, importer):
    """Compare the two copies. Returns a report, or '' when they agree.

    `designer` is the E-Box Designer repo root; `importer` is the PCB
    Importer repo root (or its importer.js directly).
    """
    importer = Path(importer)
    importer_js = (importer if importer.is_file()
                   else importer / "static" / "importer.js")
    if not importer_js.is_file():
        return f"importer.js not found at {importer_js}"

    grid = cases()
    py = python_answers(designer, grid)
    js = js_answers(importer_js, grid)

    snapping = sum(1 for a in py if a["snapped"])
    if snapping < MIN_SNAPPING_CASES:
        return (f"only {snapping} of {len(grid)} cases snap at all "
                f"(expected at least {MIN_SNAPPING_CASES}) -- the grid has "
                f"stopped exercising the rule, so agreement proves nothing")

    return parity.diff_answers(
        grid, py, js,
        describe=lambda c: (f"rect {c['rect']} {RECTS[c['rect']]}, circle "
                            f"({c['cx']}, {c['cy']}) r={c['r']}"))
