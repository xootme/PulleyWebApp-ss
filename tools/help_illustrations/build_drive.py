"""Build the top-bar (Two Pulley Drive) illustrations from the app's own belt SVG.

One drawing of a real drive — HTD 5M, 20T and 30T pulleys, a whole-tooth belt —
annotated six ways: Two Pulley Drive, Centre Distance (with the Min rule),
Belt Teeth (each tooth numbered), Belt Length (the pitch line), Gear Ratio, and Lock
Ratio (before and after).

drive_params.txt holds, per drawing: tag, teeth 1, teeth 2, belt teeth, centre
distance — the distance solved so the belt has a whole number of teeth, with
the app's own belt-length formula (openBeltLength in index.html). Sources:
  drive_<tag>.svg  /download/belt-svg?family=HTD&pitch=5M&teeth=<n1>&p2_teeth=<n2>
                   &bore=8&p2_bore=8&dual=true&center_distance=<C>&print_extra=0...
Output: static/help/two_pulley.svg, centre_distance.svg, belt_teeth.svg, belt_length.svg,
gear_ratio.svg, lock_ratio.svg (hover pictures on the top bar).
"""
import math
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT

HERE = Path(__file__).parent
HELP = HERE.parent.parent / "static" / "help"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
PITCH = 5.0
BORE = 4.0                                # bore radius in the drawings (8 mm bore)
MARGIN = 6.5                              # mm of paper around the belt
SCALE = 5.0                               # px per mm
GAP = 30
TOP = 60

DRIVES = {}
for line in (HERE / "drive_params.txt").read_text().split("\n"):
    if line.strip():
        tag, n1, n2, nb, c = line.split()
        DRIVES[tag] = dict(n1=int(n1), n2=int(n2), nb=int(nb), c=float(c))


def parts_of(tag):
    """(belt path, [pulley paths], [bore circles]) of a drive drawing."""
    svg = (HERE / f"drive_{tag}.svg").read_text(encoding="utf-8")
    paths = re.findall(r'<path\b[^>]*/>', svg)
    circles = re.findall(r'<circle cx="[^"]+" cy="[^"]+" r="[^"]+"[^>]*/>', svg)
    assert len(paths) == 3 and len(circles) == 2, (len(paths), len(circles))
    return paths[0], paths[1:], circles


def drive(tag, x, y, scale=SCALE, belt_colour=None, extra=""):
    """The drive drawing at page (x, y), optionally with the belt recoloured,
    plus extra SVG in drawing mm."""
    belt, pulleys, bores = parts_of(tag)
    if belt_colour:
        belt = re.sub(r'fill="[^"]*"', f'fill="{belt_colour[0]}"', belt)
        belt = re.sub(r'stroke="[^"]*"', f'stroke="{belt_colour[1]}"', belt)
    vx, vy, vw, vh = view(tag)
    return (f'<svg x="{x}" y="{y}" width="{vw * scale}" height="{vh * scale}" '
            f'viewBox="{vx} {vy} {vw} {vh}">{belt}{"".join(pulleys)}{"".join(bores)}'
            f'{extra}</svg>')


def view(tag):
    """The drawing-mm box around one drive (the title block lies below it)."""
    d = DRIVES[tag]
    r1, r2 = radii(d)
    return (-d["c"] / 2 - r1 - MARGIN, -r2 - MARGIN,
            d["c"] + r1 + r2 + 2 * MARGIN, 2 * r2 + 2 * MARGIN)


def text(x, y, s, size, colour="#1e293b", weight=400, anchor="middle", halo=False):
    h = (f' paint-order="stroke" stroke="#fff" stroke-width="{size * 0.3:.3f}"'
         if halo else "")
    return (f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}" font-size="{size:.3f}" '
            f'font-weight="{weight}" fill="{colour}"{h} {FONT}>{s}</text>')


def radii(d):
    r1, r2 = d["n1"] * PITCH / (2 * math.pi), d["n2"] * PITCH / (2 * math.pi)
    return r1, r2


def pitch_loop(tag):
    """The belt's pitch line: the open-belt loop around both pitch circles
    (the convex hull of the two circles), as a closed shapely ring."""
    from shapely.geometry import Point
    d = DRIVES[tag]
    r1, r2 = radii(d)
    hull = Point(-d["c"] / 2, 0).buffer(r1, 256).union(Point(d["c"] / 2, 0).buffer(r2, 256))
    return hull.convex_hull.exterior


def loop_path(ring):
    """A shapely ring as SVG path data."""
    pts = list(ring.coords)
    return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts) + " Z"


def tooth_positions(tag):
    """Where each belt tooth is, read from the drawn belt: the inner edge's runs
    of points well inside the pitch line are the teeth. Returns each tooth's
    (point on the pitch line, outward unit normal), in order round the loop."""
    from shapely.geometry import Point, Polygon
    belt, _, _ = parts_of(tag)
    d_attr = re.search(r' d="([^"]+)"', belt)[1]
    rings = [[(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+)[ ,](-?[\d.]+)", sub)]
             for sub in d_attr.split("M")[1:]]
    inner = min(rings, key=lambda r: Polygon(r).area)       # the toothed edge
    loop = pitch_loop(tag)
    inside = Polygon(loop)
    deep = [inside.contains(Point(p)) and loop.distance(Point(p)) > 1.6 for p in inner]
    # group consecutive deep points (wrapping round) into teeth
    start = deep.index(False)
    order = inner[start:] + inner[:start]
    flags = deep[start:] + deep[:start]
    teeth, run = [], []
    for p, f in zip(order, flags):
        if f:
            run.append(p)
        elif run:
            teeth.append(run)
            run = []
    if run:
        teeth.append(run)
    out = []
    for run in teeth:
        cx = sum(p[0] for p in run) / len(run)
        cy = sum(p[1] for p in run) / len(run)
        s = loop.project(Point(cx, cy))
        on = loop.interpolate(s)
        a, b = loop.interpolate(s - 0.05), loop.interpolate(s + 0.05)
        tx, ty = b.x - a.x, b.y - a.y
        n = math.hypot(tx, ty)
        nx, ny = ty / n, -tx / n                              # a normal; make it point outward
        if inside.contains(Point(on.x + nx * 0.5, on.y + ny * 0.5)):
            nx, ny = -nx, -ny
        out.append((s, (on.x, on.y), (nx, ny)))
    out.sort()
    return [(p, n) for _, p, n in out]


def page(title, body, width, height, captions, out_name):
    caps = "".join(text(width / 2, height - 14 - 19 * (len(captions) - 1 - i), c, 13,
                        "#475569") for i, c in enumerate(captions))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
  <marker id="arr-o" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{CALLOUT}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="34" font-size="22" font-weight="700" fill="{BLUE}">{title}</text>
{body}
{caps}
</svg>"""
    (HELP / out_name).write_text(svg, encoding="utf-8")
    print("wrote static/help/" + out_name, f"{width:.0f} x {height:.0f}")


def one_drive_page(title, tag, extra, captions, out_name, belt_colour=None):
    vw, vh = view(tag)[2] * SCALE, view(tag)[3] * SCALE
    body = drive(tag, GAP, TOP, belt_colour=belt_colour, extra=extra)
    page(title, body, vw + 2 * GAP, TOP + vh + 20 + 19 * len(captions) + 10, captions, out_name)


def main():
    a, b = DRIVES["a"], DRIVES["b"]
    r1, r2 = radii(a)
    c = a["c"]
    x1, x2 = -c / 2, c / 2
    fs = 13 / SCALE

    # Two Pulley Drive: the drive, labelled
    one_drive_page(
        "Two Pulley Drive", "a",
        text(x1, BORE + 3.2, "Pulley 1", fs, weight=700, halo=True)
        + text(x2, BORE + 3.2, "Pulley 2", fs, weight=700, halo=True)
        + text(0, -(r1 + r2) / 2 + 4.5, "belt", fs, colour="#475569", weight=700, halo=True),
        ["Ticked: design two pulleys and the belt that joins them (Pulley 2's panel appears on the right).",
         "Unticked: design a single pulley."],
        "two_pulley.svg")

    # Centre Distance: centre to centre, with the pitch circles that set Min
    pc = "".join(f'<circle cx="{cx}" cy="0" r="{r}" fill="none" stroke="{CALLOUT}" '
                 f'stroke-width="0.25" stroke-dasharray="1.2 0.8"/>' for cx, r in ((x1, r1), (x2, r2)))
    dim = (f'<line x1="{x1}" y1="0" x2="{x2}" y2="0" stroke="{BLUE}" stroke-width="0.35" '
           f'marker-start="url(#arr)" marker-end="url(#arr)"/>'
           + "".join(f'<circle cx="{cx}" cy="0" r="0.6" fill="{BLUE}"/>' for cx in (x1, x2))
           + text(0, -1.4, f"Centre Distance {c:.2f} mm", fs, BLUE, 700, halo=True))
    one_drive_page(
        "Centre Distance", "a", pc + dim,
        ["The distance between the two pulleys' centres. Min sets the smallest distance:",
         "the two pitch circles (dashed) just touch."],
        "centre_distance.svg")

    # Belt Teeth: every tooth numbered, just outside the belt
    teeth = tooth_positions("a")
    assert len(teeth) == a["nb"], (len(teeth), a["nb"])
    top_i = min(range(len(teeth)), key=lambda i: math.hypot(teeth[i][0][0] - x1,
                                                             teeth[i][0][1] + r1 + 30))
    fs_n = 8.5 / SCALE
    nums = ""
    for k in range(len(teeth)):
        (px, py), (nx, ny) = teeth[(top_i + k) % len(teeth)]
        nums += text(px + nx * 3.4, py + ny * 3.4 + fs_n * 0.35, str(k + 1), fs_n, CALLOUT, 700)
    one_drive_page(
        "Belt Teeth", "a",
        nums + text(0, 1.0, f"{a['nb']} teeth", fs * 1.2, CALLOUT, 700, halo=True),
        [f"The number of teeth on the belt — every tooth is numbered here, 1 to {a['nb']}.",
         "Centre Distance, Belt Teeth and Belt Length are linked: change one and the others follow."],
        "belt_teeth.svg")

    # Belt Length: the pitch line dashed, with the label pointing at it
    loop = pitch_loop("a")
    lx, ly = 0.0, 1.0                                          # label, between the pulleys
    tgt = loop.interpolate(loop.project(__import__("shapely.geometry", fromlist=["Point"])
                                        .Point(0, 30)))        # the lower run, below the label
    pitch_line = (f'<path d="{loop_path(loop)}" fill="none" stroke="{CALLOUT}" '
                  f'stroke-width="0.4" stroke-dasharray="1.6 1.0"/>'
                  f'<line x1="{lx}" y1="{ly + 1.2}" x2="{tgt.x:.3f}" y2="{tgt.y - 0.5:.3f}" '
                  f'stroke="{CALLOUT}" stroke-width="0.3" marker-end="url(#arr-o)"/>')
    one_drive_page(
        "Belt Length", "a",
        pitch_line + text(lx, ly - 1.8, f"Belt Length {a['nb'] * PITCH:g} mm", fs, CALLOUT, 700,
                          halo=True)
        + text(lx, ly + 0.2, "along the pitch line (dashed)", fs * 0.8, CALLOUT, 400, halo=True),
        [f"Belt Length is measured along the belt's pitch line: Belt Teeth × pitch "
         f"({a['nb']} × {PITCH:g} mm on HTD 5M).",
         "Centre Distance, Belt Teeth and Belt Length are linked: change one and the others follow."],
        "belt_length.svg")

    # Gear Ratio: teeth counts and turns
    ratio = a["n2"] / a["n1"]
    turn = lambda cx, r, lab: (
        f'<path d="M {cx - r * 0.55:.3f} {-r * 0.83:.3f} A {r} {r} 0 0 1 {cx + r * 0.55:.3f} '
        f'{-r * 0.83:.3f}" transform="translate(0 0)" fill="none" stroke="{CALLOUT}" '
        f'stroke-width="0.5" marker-end="url(#arr-o)"/>'
        + text(cx, BORE + 3.2, lab, fs, weight=700, halo=True))
    one_drive_page(
        "Gear Ratio", "a",
        turn(x1, r1 + 3, f"{a['n1']} teeth") + turn(x2, r2 + 3, f"{a['n2']} teeth"),
        [f"Gear Ratio = Pulley 2 teeth ÷ Pulley 1 teeth = {a['n2']} ÷ {a['n1']} = {ratio:.3f}.",
         f"Pulley 1 turns {ratio:g} times for each turn of Pulley 2. ▲ ▼ add or remove a tooth on Pulley 2."],
        "gear_ratio.svg")

    # Lock Ratio: before and after changing Pulley 1
    s2 = 3.2
    vw = max(view("a")[2], view("b")[2]) * s2
    vh = max(view("a")[3], view("b")[3]) * s2
    fs2 = 12.5 / s2
    lab = lambda d: (text(-d["c"] / 2, BORE + 4.2, f"{d['n1']}T", fs2, weight=700, halo=True)
                     + text(d["c"] / 2, BORE + 4.2, f"{d['n2']}T", fs2, weight=700, halo=True))
    xa, xb = GAP, GAP + vw + 70
    body = (drive("a", xa, TOP + 22, s2, extra=lab(a)) + drive("b", xb, TOP + 22, s2, extra=lab(b))
            + text(xa + vw / 2, TOP + 8, f"Before: {a['n1']} : {a['n2']}  (ratio {ratio:.3f})", 15, weight=700)
            + text(xb + vw / 2, TOP + 8, f"After: {b['n1']} : {b['n2']}  (ratio {b['n2'] / b['n1']:.3f})", 15, weight=700)
            + f'<line x1="{xa + vw + 12}" y1="{TOP + 22 + vh / 2}" x2="{xb - 12}" y2="{TOP + 22 + vh / 2}" '
              f'stroke="{CALLOUT}" stroke-width="2.5" marker-end="url(#arr-o)"/>')
    width = xb + vw + GAP
    page("Lock Ratio", body, width, TOP + 22 + vh + 20 + 19 * 2 + 10,
         [f"With Lock Ratio ticked, changing Pulley 1 from {a['n1']} to {b['n1']} teeth changes Pulley 2 "
          f"to {b['n2']}, keeping the ratio.",
          "Untick it to change each pulley on its own. The belt is resized to fit."],
         "lock_ratio.svg")


if __name__ == "__main__":
    main()
