"""Build the Fillet Tip / Fillet Base illustration from the app's own SVG downloads.

A locator view of the whole pulley marks one spoke; three zoomed views of that
spoke show no fillets, a tip fillet and a base fillet. The fillet arcs are
recoloured in the drawing itself and labelled with their radius.

The sources fillet_t<tip>_b<base>.svg (0/0, 3/0, 0/3) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&spokes_enabled=1
    &spokes_hub_od=18&spokes_rim_depth=3&spokes_width=5&spokes_count=6
    &spokes_fillet_tip=<tip>&spokes_fillet_base=<base>
Output: static/help/fillets.svg (shown in static/spokes_2d_help.html).
"""
import math
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "fillets.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
R = 3.0                                   # the fillet radius shown
# the zoom window, in drawing mm: the spoke that points straight up
ZX0, ZY0, ZW, ZH = -9.0, -29.0, 18.0, 23.0
ZS = 9.5                                  # px per mm in the zoom panels
LOC = 210                                 # px, locator view size
R_CROP = 34.0
GAP = 34
TOP = 78

ARC = re.compile(r'<path d="M ([-\d.]+),([-\d.]+) A ([\d.]+),[\d.]+ \S+ (\d) (\d) '
                 r'([-\d.]+),([-\d.]+)"([^>]*)/>')


def arc_mid(x0, y0, r, large, sweep, x1, y1):
    """Midpoint on an SVG arc (small arcs only), from its centre."""
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    h = math.sqrt(max(r * r - (d / 2) ** 2, 0.0))
    # centre is left of the chord for sweep==large, right otherwise (y-down)
    sgn = 1 if sweep != large else -1
    cx, cy = mx - sgn * dy / d * h, my + sgn * dx / d * h
    vx, vy = mx - cx, my - cy
    n = math.hypot(vx, vy) or 1.0
    return cx + vx / n * r, cy + vy / n * r


def highlight(svg_text):
    """The drawing with its R-mm fillet arcs in the callout colour, and their
    midpoints inside the zoom window."""
    mids = []

    def swap(m):
        x0, y0, r, large, sweep, x1, y1 = (float(m[1]), float(m[2]), float(m[3]),
                                           int(m[4]), int(m[5]), float(m[6]), float(m[7]))
        if abs(r - R) > 1e-6:
            return m[0]
        mx, my = arc_mid(x0, y0, r, large, sweep, x1, y1)
        if not (abs(mx) < 5 and ZY0 < my < ZY0 + ZH):   # the centre spoke's only
            return m[0]
        mids.append((mx, my))
        return (f'<path d="M {m[1]},{m[2]} A {m[3]},{m[3]} 0 {m[4]} {m[5]} {m[6]},{m[7]}" '
                f'fill="none" stroke="{CALLOUT}" stroke-width="0.6"/>')

    body = ARC.sub(swap, pulley_body(svg_text))
    return body, mids


def zoom_panel(x, title, svg_text):
    body, mids = highlight(svg_text)
    w, h = ZW * ZS, ZH * ZS
    fs = 13 / ZS
    g = [f'<text x="{x + w / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
         f'font-weight="700" fill="#1e293b" {FONT}>{title}</text>',
         f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="#fff" '
         f'stroke="{CALLOUT}" stroke-width="2"/>',
         f'<svg x="{x}" y="{TOP}" width="{w}" height="{h}" viewBox="{ZX0} {ZY0} {ZW} {ZH}">',
         f'<g style="fill:none">{body}</g>']
    for lab, yy in (("rim", ZY0 + 1.6), ("hub", ZY0 + ZH - 0.8)):
        g.append(f'<text x="0" y="{yy}" text-anchor="middle" font-size="{fs * 0.9:.3f}" '
                 f'fill="#64748b" font-style="italic" {FONT}>{lab}</text>')
    for mx, my in mids:
        side = 1 if mx > 0 else -1
        tx, ty = mx + side * 3.2, my + (2.2 if my < -17 else -2.2)
        g.append(f'<line x1="{tx - side * 0.2:.3f}" y1="{ty:.3f}" x2="{mx:.3f}" y2="{my:.3f}" '
                 f'stroke="{BLUE}" stroke-width="0.12" marker-end="url(#arr)"/>')
        g.append(f'<text x="{tx:.3f}" y="{ty:.3f}" dy="0.35em" '
                 f'text-anchor="{"start" if side > 0 else "end"}" font-size="{fs:.3f}" '
                 f'font-weight="700" fill="{BLUE}" {FONT}>R {R:g}</text>')
    g.append("</svg>")
    return "\n".join(g), mids


def locator(x, svg_text):
    """The whole pulley, with the zoomed spoke boxed."""
    s = LOC / (2 * R_CROP)
    y = TOP + (ZH * ZS - LOC) / 2
    bx, by = x + (ZX0 + R_CROP) * s, y + (ZY0 + R_CROP) * s
    return (f'<text x="{x + LOC / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
            f'font-weight="700" fill="#1e293b" {FONT}>Zoomed area</text>'
            f'<svg x="{x}" y="{y}" width="{LOC}" height="{LOC}" '
            f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">'
            f'<g style="fill:none">{pulley_body(svg_text)}</g></svg>'
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{ZW * s:.1f}" height="{ZH * s:.1f}" '
            f'fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>')


def main():
    src = {k: (HERE / f"fillet_{k}.svg").read_text(encoding="utf-8")
           for k in ("t0_b0", "t3_b0", "t0_b3")}
    zw = ZW * ZS
    parts = [locator(GAP, src["t0_b0"])]
    x = GAP + LOC + GAP
    for key, title, want in (("t0_b0", "No fillets (0 mm)", 0),
                             ("t3_b0", "Fillet Tip = 3 mm", 2),
                             ("t0_b3", "Fillet Base = 3 mm", 2)):
        g, mids = zoom_panel(x, title, src[key])
        assert len(mids) == want, (key, mids)
        parts.append(g)
        x += zw + GAP
    width = x
    height = TOP + ZH * ZS + 64
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">Fillet Tip and Fillet Base</text>
{chr(10).join(parts)}
<text x="{width / 2:.0f}" y="{height - 30:.0f}" text-anchor="middle" font-size="13" fill="#475569">Fillet Tip rounds the corners where the spoke meets the rim; Fillet Base rounds them where it meets the hub.</text>
<text x="{width / 2:.0f}" y="{height - 12:.0f}" text-anchor="middle" font-size="13" fill="#475569">Rounded corners spread the load and make a spoke less likely to crack. Zoom ×{ZS / (LOC / (2 * R_CROP)):.1f}.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    main()
