"""Build the Hub OD illustration from the app's own SVG downloads.

A locator view of the whole pulley marks its centre; three zoomed views show
Hub OD 14, 20 and 26 mm, with the hub boss shaded and its diameter dimensioned
along a line that runs through two openings (clear of the spokes).

The sources hub_<d>.svg (d = 14, 20, 26) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&spokes_enabled=1
    &spokes_rim_depth=3&spokes_width=5&spokes_count=6&spokes_fillet_tip=1.5
    &spokes_fillet_base=2&spokes_hub_od=<d>
Output: static/help/hub_od.svg (shown in static/spokes_2d_help.html).
"""
import re
from pathlib import Path

from build_rim_depth import band
from build_spoke_count import BLUE, CALLOUT, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "hub_od.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
HUBS = [14, 20, 26]
SHADE = "#fde2d3"
# the zoom window, in drawing mm: the centre of the pulley
ZX0, ZY0, ZW, ZH = -25.0, -15.0, 50.0, 30.0
ZS = 5.2                                  # px per mm in the zoom panels
LOC = 170                                 # px, locator view size
R_CROP = 34.0
GAP = 30
TOP = 78


def circles(svg_text):
    """(bore radius, hub radius): the drawing's two centred circles."""
    rs = sorted(float(r) for r in re.findall(r'<circle cx="0" cy="0" r="([\d.]+)"', svg_text))
    assert len(rs) == 2, rs
    return rs[0], rs[1]


def zoom_panel(x, hub, svg_text):
    """One zoomed view of the centre, with the hub shaded and its diameter."""
    bore, r = circles(svg_text)
    assert abs(2 * r - hub) < 1e-6, (hub, r)
    w, h = ZW * ZS, ZH * ZS
    fs = 13 / ZS
    return "\n".join([
        f'<text x="{x + w / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
        f'font-weight="700" fill="#1e293b" {FONT}>Hub OD = {hub} mm</text>',
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="#fff"/>',
        f'<svg x="{x}" y="{TOP}" width="{w}" height="{h}" viewBox="{ZX0} {ZY0} {ZW} {ZH}">',
        band(r, bore).replace("#fde2d3", SHADE),
        f'<g style="fill:none">{pulley_body(svg_text)}</g>',
        f'<line x1="{-r}" y1="0" x2="{r}" y2="0" stroke="{BLUE}" stroke-width="0.2" '
        f'marker-start="url(#arr)" marker-end="url(#arr)"/>',
        f'<text x="{r + 1.0}" y="0" dy="0.35em" font-size="{fs:.3f}" font-weight="700" '
        f'fill="{BLUE}" {FONT}>Ø {hub} mm</text>',
        "</svg>",
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="none" '
        f'stroke="{CALLOUT}" stroke-width="2"/>',
    ])


def locator(x, svg_text):
    """The whole pulley with its hub shaded and the zoomed area boxed."""
    bore, r = circles(svg_text)
    s = LOC / (2 * R_CROP)
    bx, by = x + (ZX0 + R_CROP) * s, TOP + (ZY0 + R_CROP) * s
    return (f'<text x="{x + LOC / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
            f'font-weight="700" fill="#1e293b" {FONT}>Zoomed area</text>'
            f'<svg x="{x}" y="{TOP}" width="{LOC}" height="{LOC}" '
            f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">'
            f'{band(r, bore)}<g style="fill:none">{pulley_body(svg_text)}</g></svg>'
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{ZW * s:.1f}" height="{ZH * s:.1f}" '
            f'fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>')


def main():
    """Assemble the figure and write it next to the help pages."""
    src = {d: (HERE / f"hub_{d}.svg").read_text(encoding="utf-8") for d in HUBS}
    parts = [locator(GAP, src[HUBS[1]])]
    x = GAP + LOC + GAP
    for d in HUBS:
        parts.append(zoom_panel(x, d, src[d]))
        x += ZW * ZS + GAP
    width = x
    key_y = TOP + max(ZH * ZS, LOC) + 26
    height = key_y + 58
    kx = GAP + LOC + GAP
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">Hub OD</text>
{chr(10).join(parts)}
<rect x="{kx}" y="{key_y - 11}" width="22" height="13" fill="{SHADE}" stroke="#94a3b8"
      stroke-width="0.5"/>
<text x="{kx + 30}" y="{key_y}" font-size="13" fill="#475569">the hub: solid ring around
the bore that the spokes start from (must be larger than the bore)</text>
<text x="{width / 2:.0f}" y="{height - 14:.0f}" text-anchor="middle" font-size="13"
      fill="#475569">A bigger hub is stronger around the bore and has room for a set screw,
but the spokes and openings get shorter. Zoom ×{ZS / (LOC / (2 * R_CROP)):.1f}.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    main()
