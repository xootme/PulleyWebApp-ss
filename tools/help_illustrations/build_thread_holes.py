"""Build the Threaded screw holes pictures (ADR-013) from the app's own STL.

Each picture looks down an M5 set-screw hole: the hub wall is sliced across
the hole, half-way through the wall, and the screw's outer and root diameters
(cct_common.screws) are drawn over the hole the app actually cut. What lies
between the hole and the outer circle is the plastic the screw cuts its
thread into.

Sources (the app's /download/stl, via its test client — no server needed):
  /download/stl?family=HTD&pitch=5M&teeth=24&bore=12&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&belt_height=10
    &clearance_height=0.5&hub_od=26&hub_height=12
    &hub_screw_size=M5&hub_screw_count=1&hub_screw_hold=thread&<extra>
  round   screw_hole_shape=round&thread_engagement=50
  hex     screw_hole_shape=hex&hex_flat=82
Output: static/help/thread_round.svg, static/help/thread_hex.svg (the
dialog shows the one for the selected shape; Hub_help.html shows both).

Run from the repo root:  python tools/help_illustrations/build_thread_holes.py
"""
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import numpy as np          # noqa: E402
import trimesh              # noqa: E402

from build_hub import EDGE, FONT, GREY, INK, MATERIAL, section, text   # noqa: E402
from build_spoke_count import BLUE, CALLOUT                            # noqa: E402
from cct_common import screws                                          # noqa: E402

HELP = ROOT / "static" / "help"
BELT, CLEAR, HUB_H, HUB_OD, BORE = 10.0, 0.5, 12.0, 26.0, 12.0
SCREW_Z = BELT + CLEAR + HUB_H / 2
X_CUT = (BORE / 2 + HUB_OD / 2) / 2          # half-way through the hub wall
SIZE = "M5"
M5 = screws.get(SIZE)
R_MAJ, R_MIN = M5.major_diameter / 2, M5.minor_diameter / 2

SCALE = 30                                    # px per mm
VIEW = 3.6                                    # mm either side of the screw axis
W, H = 470, 290
CX, CY = 130, 150                             # screw axis on the page


def stl(**extra):
    from app import app
    q = {"family": "HTD", "pitch": "5M", "teeth": "24", "bore": str(BORE), "print_extra": "0",
         "clearance_preset": "STANDARD", "backlash_preset": "STANDARD",
         "belt_height": str(BELT), "clearance_height": str(CLEAR),
         "hub_od": str(HUB_OD), "hub_height": str(HUB_H),
         "hub_screw_size": SIZE, "hub_screw_count": "1", "hub_screw_hold": "thread"}
    q.update({k: str(v) for k, v in extra.items()})
    r = app.test_client().get("/download/stl", query_string=q)
    assert r.status_code == 200, r.status_code
    b = r.data
    n = int(np.frombuffer(b[80:84], "<u4")[0])          # the design data follows the triangles
    rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    t = np.frombuffer(b[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t["v"].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def across_the_hole(mesh):
    """The hub wall cut across the screw (plane x = X_CUT), in mm about the
    screw axis: u sideways, v up (page y = -v)."""
    return section(mesh, [X_CUT, 0, SCREW_Z], [1, 0, 0], lambda p: (p[1], p[2] - SCREW_Z))


def hole_loop(loops):
    """The loop around the screw axis: the hole's outline."""
    from shapely.geometry import Point, Polygon
    inner = [lp for lp in loops if Polygon(lp).contains(Point(0, 0)) and Polygon(lp).area < 40]
    assert len(inner) == 1, [Polygon(lp).area for lp in loops]
    return inner[0]


def px(u, v):
    return CX + u * SCALE, CY - v * SCALE


def path(loops):
    return " ".join("M " + " L ".join("%.2f %.2f" % px(u, v) for u, v in loop) + " Z" for loop in loops)


def circle(r, **attrs):
    a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<circle cx="{CX}" cy="{CY}" r="{r * SCALE:.2f}" {a}/>'


def leader(x0, y0, x1, y1, colour):
    return (f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="{colour}" '
            f'stroke-width="1"/><circle cx="{x0:.1f}" cy="{y0:.1f}" r="2" fill="{colour}"/>')


def page(title, body, loops, hole):
    """Material around the hole, clipped to a square window; the orange band is
    the plastic inside the screw's outer diameter (where its thread cuts)."""
    half = VIEW * SCALE
    band = (f'<path d="M {CX + R_MAJ * SCALE:.2f} {CY} a {R_MAJ * SCALE:.2f} {R_MAJ * SCALE:.2f} 0 1 0 '
            f'{-2 * R_MAJ * SCALE:.2f} 0 a {R_MAJ * SCALE:.2f} {R_MAJ * SCALE:.2f} 0 1 0 '
            f'{2 * R_MAJ * SCALE:.2f} 0 Z {path([hole])}" fill="{CALLOUT}" fill-opacity="0.35" '
            f'fill-rule="evenodd"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<rect width="{W}" height="{H}" fill="#fff"/>
<clipPath id="win"><rect x="{CX - half}" y="{CY - half}" width="{2 * half}" height="{2 * half}" rx="6"/></clipPath>
{text(16, 24, title, 15, INK, 600, anchor="start")}
<g clip-path="url(#win)">
<path d="{path(loops)}" fill="{MATERIAL}" fill-rule="evenodd" stroke="{EDGE}" stroke-width="1.2" stroke-linejoin="round"/>
{band}
</g>
<rect x="{CX - half}" y="{CY - half}" width="{2 * half}" height="{2 * half}" rx="6" fill="none" stroke="#cbd5e1"/>
{body}
{text(16, H - 10, "Looking into an M5 hole, cut half-way through the hub wall.", 10.5, GREY, anchor="start")}
</svg>
'''


def round_picture():
    m = stl(screw_hole_shape="round", thread_engagement=50)
    loops = across_the_hole(m)
    hole = hole_loop(loops)
    from shapely.geometry import Polygon
    d_cut = 2 * math.sqrt(Polygon(hole).area / math.pi)
    d_rule = screws.self_tap_diameter(SIZE, 50)
    assert abs(d_cut - d_rule) < 0.03, (d_cut, d_rule)       # the app cut what the rule says
    lx = CX + VIEW * SCALE + 18
    body = "\n".join([
        circle(R_MAJ, fill="none", stroke=INK, stroke_width="1.4", stroke_dasharray="6 4"),
        circle(R_MIN, fill="none", stroke=INK, stroke_width="1.2", stroke_dasharray="2 3"),
        circle(d_rule / 2, fill="none", stroke=BLUE, stroke_width="2.2"),
        leader(*px(0, R_MAJ), lx, 82, INK), text(lx + 4, 86, "Screw's outer Ø 5.00 — 0%", 13, INK, anchor="start"),
        text(lx + 4, 101, "a hole this size: no grip", 11.5, GREY, anchor="start"),
        leader(*px(R_MIN * 0.71, R_MIN * 0.71), lx, 130, INK),
        text(lx + 4, 134, "Thread root Ø 4.13 — 100%", 13, INK, anchor="start"),
        text(lx + 4, 149, "a full-depth thread", 11.5, GREY, anchor="start"),
        leader(*px(d_rule / 2 * 0.8, -d_rule / 2 * 0.6), lx, 186, BLUE),
        text(lx + 4, 190, f"The hole at 50%: Ø {d_rule:.2f}", 13, BLUE, 600, anchor="start"),
        f'<rect x="{lx}" y="218" width="14" height="10" fill="{CALLOUT}" fill-opacity="0.35"/>',
        text(lx + 20, 227, "plastic the thread cuts into", 12, INK, anchor="start"),
    ])
    return page("Round — Thread engagement", body, loops, hole)


def hex_picture():
    m = stl(screw_hole_shape="hex", hex_flat=82)
    loops = across_the_hole(m)
    hole = hole_loop(loops)
    flats = screws.hex_flats(SIZE, 82)
    us = [u for u, _ in hole]
    assert abs((max(us) - min(us)) - flats) < 0.03, (max(us) - min(us), flats)   # flats are sideways
    r_corner = flats / math.sqrt(3)
    assert r_corner < R_MAJ          # at 82% the corners are inside the screw's outer circle
    assert abs(flats / 2 - R_MIN) < 0.05   # …and the flats sit at the thread root
    lx = CX + VIEW * SCALE + 18
    x_flat = CX + flats / 2 * SCALE
    body = "\n".join([
        circle(R_MAJ, fill="none", stroke=INK, stroke_width="1.4", stroke_dasharray="6 4"),
        f'<path d="{path([hole])}" fill="none" stroke="{BLUE}" stroke-width="2.2" stroke-linejoin="round"/>',
        # across the flats
        f'<line x1="{CX - flats / 2 * SCALE:.1f}" y1="{CY}" x2="{x_flat:.1f}" y2="{CY}" stroke="{BLUE}" '
        f'stroke-width="1.2" stroke-dasharray="3 2"/>',
        circle(R_MIN, fill="none", stroke=INK, stroke_width="1.2", stroke_dasharray="2 3"),
        leader(*px(R_MAJ * 0.71, R_MAJ * 0.71), lx, 56, INK),
        text(lx + 4, 60, "Screw's outer Ø 5.00", 13, INK, anchor="start"),
        # on the root circle where it runs inside the hole, clear of the flats
        leader(*px(R_MIN * math.cos(math.radians(15)), R_MIN * math.sin(math.radians(15))), lx, 94, INK),
        text(lx + 4, 98, f"Thread root Ø {2 * R_MIN:.2f}", 13, INK, anchor="start"),
        text(lx + 4, 113, "the flats reach it: a full-depth thread", 11.5, GREY, anchor="start"),
        leader(x_flat, CY, lx, 142, BLUE),
        text(lx + 4, 146, f"Across the flats: {flats:.2f}", 13, BLUE, 600, anchor="start"),
        text(lx + 4, 161, "82% of the screw's Ø — the thread", 11.5, GREY, anchor="start"),
        text(lx + 4, 175, "cuts deepest here, and grips", 11.5, GREY, anchor="start"),
        leader(*px(flats / 2, -r_corner / 2), lx, 200, INK),
        text(lx + 4, 204, f"Corners (Ø {2 * r_corner:.2f}): cut shallow,", 13, INK, anchor="start"),
        text(lx + 4, 219, "room for the plastic pushed aside;", 11.5, GREY, anchor="start"),
        text(lx + 4, 233, "a corner up prints without support", 11.5, GREY, anchor="start"),
        f'<rect x="{lx}" y="245" width="14" height="10" fill="{CALLOUT}" fill-opacity="0.35"/>',
        text(lx + 20, 254, "plastic the thread cuts into", 12, INK, anchor="start"),
    ])
    return page("Hex — Hex flats", body, loops, hole)


def main():
    HELP.mkdir(exist_ok=True)
    for name, build in (("thread_round.svg", round_picture), ("thread_hex.svg", hex_picture)):
        (HELP / name).write_text(build(), encoding="utf-8")
        print("wrote", HELP / name)


if __name__ == "__main__":
    main()
