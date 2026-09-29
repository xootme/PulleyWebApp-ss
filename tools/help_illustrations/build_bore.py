"""Build the Bore Diameter illustration from the app's own SVG downloads.

The same small pulley with a 5, 8 and 12 mm bore, the bore shaded and its
diameter dimensioned. Small enough to show whole, so there is no zoom.

The sources bore_<d>.svg (d = 5, 8, 12) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=20&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&bore=<d>
Output: static/help/bore.svg (shown as a hover picture on Bore Diameter).
"""
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "bore.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
BORES = [5, 8, 12]
SHADE = "#fde2d3"
R_CROP = 17.5                             # mm, half-size of the view around the pulley
PANEL = 220                               # px per panel
GAP = 30
TOP = 78


def bore_radius(svg_text):
    """The bore: the drawing's only centred circle."""
    rs = re.findall(r'<circle cx="0" cy="0" r="([\d.]+)"', svg_text)
    assert len(rs) == 1, rs
    return float(rs[0])


def panel(x, bore, svg_text):
    """One whole pulley with its bore shaded and its diameter marked."""
    r = bore_radius(svg_text)
    assert abs(2 * r - bore) < 1e-6, (bore, r)
    fs = 13 / (PANEL / (2 * R_CROP))
    return "\n".join([
        f'<text x="{x + PANEL / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
        f'font-weight="700" fill="#1e293b" {FONT}>Bore Diameter = {bore} mm</text>',
        f'<svg x="{x}" y="{TOP}" width="{PANEL}" height="{PANEL}" '
        f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">',
        f'<circle r="{r}" fill="{SHADE}"/>',
        f'<g style="fill:none">{pulley_body(svg_text)}</g>',
        f'<line x1="{-r}" y1="0" x2="{r}" y2="0" stroke="{BLUE}" stroke-width="0.25" '
        f'marker-start="url(#arr)" marker-end="url(#arr)"/>',
        f'<text x="0" y="{r + 2.4}" dy="0.35em" text-anchor="middle" font-size="{fs:.3f}" '
        f'font-weight="700" fill="{BLUE}" paint-order="stroke" stroke="#fff" '
        f'stroke-width="{fs * 0.3:.3f}" {FONT}>Ø {bore} mm</text>',
        "</svg>",
    ])


def main():
    """Assemble the figure and write it next to the help pages."""
    parts = []
    x = GAP
    for b in BORES:
        parts.append(panel(x, b, (HERE / f"bore_{b}.svg").read_text(encoding="utf-8")))
        x += PANEL + GAP
    width = x
    height = TOP + PANEL + 50
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width}" height="{height}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">Bore Diameter</text>
{chr(10).join(parts)}
<text x="{width / 2:.0f}" y="{height - 16}" text-anchor="middle" font-size="13"
      fill="#475569">The bore is the hole for the shaft. Match it to your shaft; Bore Shape,
just below, makes it a D-flat, keyway or spline.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width} x {height}")


if __name__ == "__main__":
    main()
