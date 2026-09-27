"""Build the Spoke Count illustration from the app's own SVG downloads.

Each panel is the real /download/svg drawing, cropped to the pulley with a
nested viewBox (vector, so the zoom inset stays sharp). Callouts are drawn on
top in the drawing's own mm coordinates.

The sources spokes_<n>.svg (n = 4, 6, 8) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&spokes_enabled=1
    &spokes_hub_od=18&spokes_rim_depth=3&spokes_width=5&spokes_fillet_tip=1.5
    &spokes_fillet_base=2&spokes_count=<n>
Output: static/help/spoke_count.svg (shown in static/spokes_2d_help.html).
"""
import math
import re
from pathlib import Path

HERE = Path(__file__).parent
COUNTS = [4, 6, 8]
R_CROP = 34.0          # mm, half-size of the crop around the pulley
PANEL = 300            # px per panel
GAP = 30
TOP = 70               # px for the heading
BOTTOM = 70            # px for the panel captions and footnote
BLUE = "#0078d4"
CALLOUT = "#d9480f"


def pulley_body(svg_text):
    """The drawing's paths only: no metadata, background rect or title block."""
    shapes = re.findall(r"<(?:path|line|circle)\b[^>]*/>", svg_text, flags=re.S)
    return "\n".join(shapes)   # the title block lies outside the crop


def void_angles(svg_text):
    """Angles (deg, SVG y-down) of the spoke openings.

    The drawing is one arc per path; each opening has exactly one big arc
    along the rim's inner face (radius 20-30 mm), so its midpoint direction
    is the opening's direction.
    """
    angles = []
    for d in re.findall(r'<path\b[^>]*\bd="([^"]+)"', svg_text, flags=re.S):
        m = re.fullmatch(r"\s*M\s*(\S+),(\S+)\s+A\s*(\S+),\S+\s+\S+\s+\S+\s+\S+\s+(\S+),(\S+)\s*", d)
        if not m:
            continue
        x0, y0, r, x1, y1 = map(float, m.groups())
        if 20 < r < 30:
            angles.append(math.degrees(math.atan2(y0 + y1, x0 + x1)) % 360)
    return sorted(angles)


def spoke_angles(voids):
    """A spoke sits halfway between two neighbouring openings."""
    out = []
    for a, b in zip(voids, voids[1:] + [voids[0] + 360]):
        out.append(((a + b) / 2) % 360)
    return out


def pol(r, deg):
    t = math.radians(deg)
    return r * math.cos(t), r * math.sin(t)


def arc(r, a0, a1):
    x0, y0 = pol(r, a0)
    x1, y1 = pol(r, a1)
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return f"M {x0:.3f} {y0:.3f} A {r} {r} 0 {large} 1 {x1:.3f} {y1:.3f}"


def panel(n, svg_text, x0):
    body = pulley_body(svg_text)
    spokes = spoke_angles(void_angles(svg_text))
    assert len(spokes) == n, (n, len(spokes))
    s = PANEL / (2 * R_CROP)                       # px per mm, for sizing text
    fs = 13 / s                                    # 13 px text, in mm
    g = [f'<svg x="{x0}" y="{TOP}" width="{PANEL}" height="{PANEL}" '
         f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">',
         f'<g style="fill:none">{body}</g>']
    # numbered markers on the spokes, at mid-span
    for i, a in enumerate(sorted(spokes, key=lambda a: (a + 90) % 360), 1):
        x, y = pol(15.5, a)
        g.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{2.4}" fill="{CALLOUT}"/>'
                 f'<text x="{x:.3f}" y="{y:.3f}" dy="0.36em" text-anchor="middle" '
                 f'font-size="{fs * 0.95:.3f}" font-weight="700" fill="#fff" '
                 f'font-family="system-ui,Segoe UI,sans-serif">{i}</text>')
    # the spacing angle between two neighbouring spokes
    top = sorted(spokes, key=lambda a: (a + 90) % 360)
    a0, a1 = top[0], top[1]
    if (a1 - a0) % 360 > 180:
        a0, a1 = a1, a0
    for a in (a0, a1):
        xa, ya = pol(10.5, a)
        xb, yb = pol(25.0, a)
        g.append(f'<line x1="{xa:.3f}" y1="{ya:.3f}" x2="{xb:.3f}" y2="{yb:.3f}" '
                 f'stroke="{BLUE}" stroke-width="0.35" stroke-dasharray="1.2 0.8"/>')
    g.append(f'<path d="{arc(22.0, a0, a1)}" fill="none" stroke="{BLUE}" stroke-width="0.5" '
             f'marker-start="url(#arr)" marker-end="url(#arr)"/>')
    mid = a0 + ((a1 - a0) % 360) / 2
    tx, ty = pol(17.0, mid)
    g.append(f'<text x="{tx:.3f}" y="{ty:.3f}" dy="0.35em" text-anchor="middle" '
             f'font-size="{fs:.3f}" font-weight="700" fill="{BLUE}" '
             f'paint-order="stroke" stroke="#fff" stroke-width="{fs * 0.35:.3f}" '
             f'font-family="system-ui,Segoe UI,sans-serif">{360 // n}°</text>')
    g.append("</svg>")
    cap_y = TOP + PANEL + 26
    g.append(f'<text x="{x0 + PANEL / 2}" y="{cap_y}" text-anchor="middle" font-size="17" '
             f'font-weight="700" fill="#1e293b">Spoke Count = {n}</text>')
    return "\n".join(g), spokes


def zoom_inset(svg_text, spokes, panel_x0):
    """A magnified view of one spoke on the 8-spoke panel, with its width."""
    body = pulley_body(svg_text)
    a = min(spokes, key=lambda a: abs(((a - 45) + 180) % 360 - 180))    # lower-right spoke
    cx, cy = pol(17, a)
    half = 6.5                                     # mm shown each way
    size = 200                                     # px
    ix, iy = panel_x0 + PANEL + GAP + 10, TOP + (PANEL - size) / 2
    s_main = PANEL / (2 * R_CROP)
    # where that area sits on the panel, in page px
    px = panel_x0 + (cx + R_CROP) * s_main
    py = TOP + (cy + R_CROP) * s_main
    pr = half * s_main
    # spoke width dimension across the spoke, perpendicular to it
    w = 5.0
    nx, ny = pol(1, a + 90)
    ex, ey = pol(1, a)
    bx, by = cx - ex * 2.5, cy - ey * 2.5
    p1 = (bx - nx * w / 2, by - ny * w / 2)
    p2 = (bx + nx * w / 2, by + ny * w / 2)
    fs = 12 / (size / (2 * half))
    lab = (bx - ex * 1.3, by - ey * 1.3)            # beside the arrow, inside the spoke
    return f"""
<rect x="{px - pr:.1f}" y="{py - pr:.1f}" width="{2 * pr:.1f}" height="{2 * pr:.1f}"
      fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>
<line x1="{px + pr:.1f}" y1="{py - pr:.1f}" x2="{ix:.1f}" y2="{iy:.1f}" stroke="{CALLOUT}" stroke-width="1"/>
<line x1="{px + pr:.1f}" y1="{py + pr:.1f}" x2="{ix:.1f}" y2="{iy + size:.1f}" stroke="{CALLOUT}" stroke-width="1"/>
<rect x="{ix}" y="{iy}" width="{size}" height="{size}" fill="#fff" stroke="{CALLOUT}" stroke-width="2"/>
<svg x="{ix}" y="{iy}" width="{size}" height="{size}" viewBox="{cx - half:.3f} {cy - half:.3f} {2 * half} {2 * half}">
  <g style="fill:none">{body}</g>
  <line x1="{p1[0]:.3f}" y1="{p1[1]:.3f}" x2="{p2[0]:.3f}" y2="{p2[1]:.3f}" stroke="{BLUE}"
        stroke-width="0.18" marker-start="url(#arr)" marker-end="url(#arr)"/>
  <text x="{lab[0]:.3f}" y="{lab[1]:.3f}" dy="0.35em" text-anchor="middle" font-size="{fs:.3f}"
        font-weight="700" fill="{BLUE}" font-family="system-ui,Segoe UI,sans-serif">5 mm</text>
</svg>
<text x="{ix + size / 2}" y="{iy - 6}" text-anchor="middle" font-size="12" fill="{CALLOUT}"
      font-weight="600">Spoke width — zoom ×{round((size / (2 * half)) / s_main, 1)}</text>"""


def main():
    W = 3 * PANEL + 5 * GAP + 210
    H = TOP + PANEL + BOTTOM
    parts, last = [], None
    for k, n in enumerate(COUNTS):
        svg = (HERE / f"spokes_{n}.svg").read_text(encoding="utf-8")
        x0 = GAP + k * (PANEL + GAP)
        g, spokes = panel(n, svg, x0)
        parts.append(g)
        last = (svg, spokes, x0)
    parts.append(zoom_inset(*last))
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"
     font-family="system-ui,Segoe UI,sans-serif">
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{W}" height="{H}" fill="#fff"/>
<text x="{GAP}" y="34" font-size="22" font-weight="700" fill="{BLUE}">Spoke Count</text>
<text x="{GAP}" y="56" font-size="14" fill="#475569">Spokes are spaced evenly: the angle between them is 360° ÷ count.</text>
{chr(10).join(parts)}
<text x="{W / 2}" y="{H - 14}" text-anchor="middle" font-size="13" fill="#475569">Spoke width, hub and rim stay the same as the count changes, so each opening gets narrower.</text>
</svg>"""
    (HERE.parent.parent / "static" / "help" / "spoke_count.svg").write_text(out, encoding="utf-8")
    print("wrote static/help/spoke_count.svg", W, "x", H)


if __name__ == "__main__":
    main()
