"""Build the Belt Profile illustration: every belt family the app makes, its
tooth shape, and the other names the same belt is sold under.

Each drawing is the pulley groove the belt's teeth sit in, straight from the
app's geometry (generate_profile_groove, the flat groove before it is wrapped
round the pulley) at zero clearance and backlash: three teeth, every family at
a 5 mm pitch (Imperial XL, 5.08 mm) and one scale, so the shapes compare. The
pitch lists come from PROFILE_PITCHES and PULLEY_SPECS; the names and
standards are the Pulley help page's Belt Profile table and its STD / RPP
notes, plus the trade names people search for (CCT_WebSite/Pages/TimingPulley/
google_keyword.md).

Output: static/help/belt_profile.svg (hover picture on Family and Pitch, and
the Belt Profile section of the Pulley help pages).
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from build_bore_shape import BLUE, FONT, GREY, HELP, MATERIAL, text  # noqa: E402
from geometry.pulley_geometry import (PROFILE_KEY_PREFIX, PROFILE_PITCHES,  # noqa: E402
                                      PULLEY_SPECS, _build_groove_points, generate_profile_groove)

BELT = "#3f4a59"           # the belt's teeth, in the groove
INK = "#1e293b"

# family, the pitch drawn, [other names], [shape, standard]
FAMILIES = [
    ("HTD", "5M", ["5M, HTD 5M, HTD-5M", "Gates PowerGrip HTD"],
     ["round (curvilinear) teeth", "ISO 13050"]),
    ("GT", "5M", ["GT2 · 2GT · 2MGT (2 mm), GT3,", "3MGT, 5MGT · Gates PowerGrip GT"],
     ["round teeth, shallower than HTD", "Gates"]),
    ("STD", "5M", ["S-series · S5M · S-HTD ·", "Synchroflex S (ContiTech)"],
     ["large-radius round teeth", "ISO 13050"]),
    ("RPP", "5M", ["R-series · R5M · RPP (Megadyne)", "— not the same as STD"],
     ["parabolic teeth, tip fillet", "ISO 13050"]),
    ("T", "T5", ["metric trapezoidal · T5 ·", "DIN 7721 · Synchroflex T"],
     ["trapezoid, 40° included", "ISO 17396"]),
    ("AT", "AT5", ["AT5 · metric trapezoidal, heavy", "duty · Synchroflex AT"],
     ["trapezoid, 50° included", "ISO 17396"]),
    ("Imperial", "XL", ["inch trapezoidal · Gates PowerGrip", "Timing · names by pitch (below)"],
     ["trapezoid, 40°–50° included", "ISO 5294"]),
]

S = 15.0                    # px per mm
CELL_W, CELL_H = 470, 128
COLS, GAP, TOP = 2, 20, 64
DRAW_W = 3 * 5.0 * S        # three 5 mm teeth
BASE_D = 1.4                # mm of pulley below the deepest groove


def groove(family, pitch):
    key = PROFILE_KEY_PREFIX.get(family, "") + pitch
    spec = PULLEY_SPECS[key]
    c = generate_profile_groove(family, key, 40, 0.0, 0.0, 0.0)
    return _build_groove_points(c.primitives[1:-1], family), spec["pitch"]


def cell(family, pitch, names, shape, x0, y0):
    pts, p = groove(family, pitch)
    depth = -min(y for _, y in pts)
    y_face = y0 + 58                          # the pulley's surface, px
    X = lambda x: x0 + 12 + DRAW_W / 2 + x * S   # noqa: E731
    Y = lambda y: y_face - y * S                 # noqa: E731  (y down into the pulley is negative)
    centres = (-p, 0.0, p)
    # the pulley: its surface with the three grooves cut, down to a flat base
    outline = [(-1.5 * p, 0.0)]
    for c in centres:
        outline += [(c + x, y) for x, y in pts]
    outline += [(1.5 * p, 0.0), (1.5 * p, -2.1 - BASE_D), (-1.5 * p, -2.1 - BASE_D)]
    d = "M " + " L ".join(f"{X(x):.2f} {Y(y):.2f}" for x, y in outline) + " Z"
    out = [f'<path d="{d}" fill="{MATERIAL}" stroke="#334155" stroke-width="1.1"/>']
    for c in centres:                         # the belt's tooth fills each groove
        d = "M " + " L ".join(f"{X(c + x):.2f} {Y(y):.2f}" for x, y in pts) + " Z"
        out.append(f'<path d="{d}" fill="{BELT}" stroke="{BELT}" stroke-width="0.8"/>')
    # the pitch, tooth to tooth, above the teeth
    yb = Y(0) - 12
    for c in (0.0, p):
        out.append(f'<line x1="{X(c):.2f}" y1="{yb - 5:.2f}" x2="{X(c):.2f}" y2="{Y(0) - 2:.2f}" '
                   f'stroke="{GREY}" stroke-width="0.8"/>')
    out.append(f'<line x1="{X(0):.2f}" y1="{yb:.2f}" x2="{X(p):.2f}" y2="{yb:.2f}" stroke="{GREY}" '
               f'stroke-width="1" marker-start="url(#arr)" marker-end="url(#arr)"/>')
    out.append(text((X(0) + X(p)) / 2, yb - 6, f"pitch {p:g} mm", 12, GREY))
    # the words
    tx = x0 + 12 + DRAW_W + 22
    pitches = ", ".join(PROFILE_PITCHES[family])
    out.append(text(tx, y0 + 20, family, 18, BLUE, 700, anchor="start"))
    out.append(text(tx + 12 + 10.5 * len(family), y0 + 20, pitches, 12, GREY, anchor="start"))
    for i, line in enumerate(names):
        out.append(text(tx, y0 + 42 + 17 * i, line, 13, INK, anchor="start"))
    out.append(text(tx, y0 + 42 + 17 * len(names) + 4, " · ".join(shape), 12, GREY, anchor="start"))
    out.append(text(tx, y0 + 42 + 17 * len(names) + 21, f"drawn: {PROFILE_KEY_PREFIX.get(family, '')}{pitch}"
                    f" · {depth:.2f} mm deep", 11, GREY, anchor="start"))
    return "".join(out)


def imperial_note():
    """The inch belts' names are their pitches: MXL 0.080 in, and so on."""
    parts = []
    for p in PROFILE_PITCHES["Imperial"]:
        mm = PULLEY_SPECS[p]["pitch"]
        parts.append(f"{p} {mm / 25.4:.3f}″ ({mm:g} mm)")
    return "Imperial pitches: " + " · ".join(parts)


def main():
    rows = (len(FAMILIES) + COLS - 1) // COLS
    W = COLS * CELL_W + (COLS + 1) * GAP
    body = []
    for i, (fam, pitch, names, shape) in enumerate(FAMILIES):
        r, c = divmod(i, COLS)
        x0, y0 = GAP + c * (CELL_W + GAP), TOP + r * CELL_H
        body.append(f'<rect x="{x0}" y="{y0}" width="{CELL_W}" height="{CELL_H - 10}" rx="6" '
                    f'fill="#f8fafc" stroke="#e2e8f0"/>')
        body.append(cell(fam, pitch, names, shape, x0, y0))
    # the empty last cell: the key
    kx, ky = GAP + (len(FAMILIES) % COLS) * (CELL_W + GAP), TOP + (rows - 1) * CELL_H
    key = [
        (f'<rect x="{kx + 14}" y="{ky + 18}" width="22" height="14" fill="{MATERIAL}" stroke="#334155"/>',
         "the pulley, cut through its teeth"),
        (f'<rect x="{kx + 14}" y="{ky + 44}" width="22" height="14" fill="{BELT}"/>',
         "the belt's tooth, in its groove"),
    ]
    for j, (sw, label) in enumerate(key):
        body.append(sw + text(kx + 46, ky + 30 + 26 * j, label, 13, INK, anchor="start"))
    body.append(text(kx + 14, ky + 88, "Match the belt's pitch code:", 13, INK, 700, anchor="start"))
    body.append(text(kx + 14, ky + 106, "5M → HTD · GT5M/5MGT → GT · S5M → STD", 12, GREY, anchor="start"))
    body.append(text(kx + 14, ky + 122, "R5M → RPP · T5 → T · AT5 → AT · XL → Imperial", 12, GREY,
                     anchor="start"))
    height = TOP + rows * CELL_H + 58
    caps = [imperial_note(),
            "Each drawing: the app's own groove, three teeth at a 5 mm pitch (XL 5.08), all at one scale."]
    for j, cap in enumerate(caps):
        body.append(text(W / 2, height - 34 + 20 * j, cap, 13, GREY))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}"
     viewBox="0 0 {W} {height}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="context-stroke"/>
  </marker>
</defs>
<rect width="{W}" height="{height}" fill="#fff"/>
<text x="{GAP}" y="40" font-size="24" font-weight="700" fill="{BLUE}">Belt profile: the families and their other names</text>
{"".join(body)}
</svg>"""
    (HELP / "belt_profile.svg").write_text(svg, encoding="utf-8")
    print("wrote static/help/belt_profile.svg", W, "x", height)


if __name__ == "__main__":
    main()
