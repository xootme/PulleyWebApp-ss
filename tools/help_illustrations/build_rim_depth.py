"""Build the Rim Depth illustration from the app's own SVG downloads.

A locator view of the whole pulley marks the top of the rim; three zoomed
views of it show Rim Depth 2, 4 and 6 mm, with the rim band shaded and its
depth dimensioned from the bottom of the tooth grooves (which is where the
app measures it from, not the tooth tips).

The sources rim_<d>.svg (d = 2, 4, 6) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&spokes_enabled=1
    &spokes_hub_od=18&spokes_width=5&spokes_count=6&spokes_fillet_tip=1.5
    &spokes_fillet_base=2&spokes_rim_depth=<d>
Output: static/help/rim_depth.svg (shown in static/spokes_2d_help.html).
"""
import math
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT, pol, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "rim_depth.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
DEPTHS = [2, 4, 6]
BAND = "#fde2d3"                          # the rim band's shading
# the zoom window, in drawing mm: the top of the pulley
ZX0, ZY0, ZW, ZH = -15.0, -32.5, 30.0, 17.0
ZS = 9.0                                  # px per mm in the zoom panels
LOC = 180                                 # px, locator view size
R_CROP = 34.0
GAP = 30
TOP = 78
DIM_ANGLE = -114.0                        # deg (SVG y-down): through an opening, clear of a spoke


def radii(svg_text):
    """(groove-bottom radius, rim inner radius) read from the drawing."""
    outline = max(re.findall(r'<path d="([^"]+)"', svg_text), key=len)
    nums = [float(v) for v in re.findall(r"-?\d+\.\d+", outline)]
    root = min(math.hypot(x, y) for x, y in zip(nums[0::2], nums[1::2]))
    inner = [float(r) for r in re.findall(r"A ([\d.]+),", svg_text) if 15 < float(r) < root]
    return root, max(inner)


def band(root, inner):
    """The rim band as a filled ring (even-odd between two circles)."""
    ring = lambda r: (f"M {r} 0 A {r} {r} 0 1 0 {-r} 0 A {r} {r} 0 1 0 {r} 0 Z")
    return f'<path d="{ring(root)} {ring(inner)}" fill="{BAND}" fill-rule="evenodd"/>'


def zoom_panel(x, depth, svg_text):
    root, inner = radii(svg_text)
    assert abs(root - inner - depth) < 1e-3, (depth, root, inner)
    w, h = ZW * ZS, ZH * ZS
    fs = 13 / ZS
    xa, ya = pol(inner, DIM_ANGLE)
    xb, yb = pol(root, DIM_ANGLE)
    lx, ly = pol(inner - 1.6, DIM_ANGLE)           # just inside the opening, under the arrow
    return "\n".join([
        f'<text x="{x + w / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
        f'font-weight="700" fill="#1e293b" {FONT}>Rim Depth = {depth} mm</text>',
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="#fff"/>',
        f'<svg x="{x}" y="{TOP}" width="{w}" height="{h}" viewBox="{ZX0} {ZY0} {ZW} {ZH}">',
        band(root, inner),
        f'<circle r="{root}" fill="none" stroke="{BLUE}" stroke-width="0.12" '
        f'stroke-dasharray="0.6 0.4"/>',
        f'<g style="fill:none">{pulley_body(svg_text)}</g>',
        f'<line x1="{xa:.3f}" y1="{ya:.3f}" x2="{xb:.3f}" y2="{yb:.3f}" stroke="{BLUE}" '
        f'stroke-width="0.14" marker-start="url(#arr)" marker-end="url(#arr)"/>',
        f'<text x="{lx:.3f}" y="{ly:.3f}" dy="0.35em" text-anchor="middle" font-size="{fs:.3f}" '
        f'font-weight="700" fill="{BLUE}" paint-order="stroke" stroke="#fff" '
        f'stroke-width="{fs * 0.3:.3f}" {FONT}>{depth} mm</text>',
        "</svg>",
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="none" '
        f'stroke="{CALLOUT}" stroke-width="2"/>',
    ])


def locator(x, svg_text):
    """The whole pulley with its rim shaded and the zoomed area boxed."""
    root, inner = radii(svg_text)
    s = LOC / (2 * R_CROP)
    y = TOP
    bx, by = x + (ZX0 + R_CROP) * s, y + (ZY0 + R_CROP) * s
    return (f'<text x="{x + LOC / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
            f'font-weight="700" fill="#1e293b" {FONT}>Zoomed area</text>'
            f'<svg x="{x}" y="{y}" width="{LOC}" height="{LOC}" '
            f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">'
            f'{band(root, inner)}<g style="fill:none">{pulley_body(svg_text)}</g></svg>'
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{ZW * s:.1f}" height="{ZH * s:.1f}" '
            f'fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>')


def main():
    src = {d: (HERE / f"rim_{d}.svg").read_text(encoding="utf-8") for d in DEPTHS}
    zw = ZW * ZS
    parts = [locator(GAP, src[DEPTHS[0]])]
    x = GAP + LOC + GAP
    for d in DEPTHS:
        parts.append(zoom_panel(x, d, src[d]))
        x += zw + GAP
    width = x
    legend_y = TOP + max(ZH * ZS, LOC) + 26
    height = legend_y + 58
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">Rim Depth</text>
{chr(10).join(parts)}
<rect x="{GAP + LOC + GAP}" y="{legend_y - 11}" width="22" height="13" fill="{BAND}" stroke="#94a3b8" stroke-width="0.5"/>
<text x="{GAP + LOC + GAP + 30}" y="{legend_y}" font-size="13" fill="#475569">the rim: solid ring under the teeth</text>
<line x1="{GAP + LOC + GAP + 290}" y1="{legend_y - 4}" x2="{GAP + LOC + GAP + 312}" y2="{legend_y - 4}" stroke="{BLUE}" stroke-width="1.2" stroke-dasharray="5 3"/>
<text x="{GAP + LOC + GAP + 320}" y="{legend_y}" font-size="13" fill="#475569">bottom of the tooth grooves, where Rim Depth is measured from</text>
<text x="{width / 2:.0f}" y="{height - 14:.0f}" text-anchor="middle" font-size="13" fill="#475569">A deeper rim is stiffer and carries the tooth loads better, but leaves smaller openings. Zoom ×{ZS / (LOC / (2 * R_CROP)):.1f}.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    main()
