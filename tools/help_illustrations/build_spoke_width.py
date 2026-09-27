"""Build the Spoke Width illustration from the app's own SVG downloads.

A locator view of the whole pulley marks one spoke; three zoomed views of that
spoke show Spoke Width 3, 5 and 8 mm, with the spoke's straight sides
recoloured and the width dimensioned at mid-span.

The sources width_<w>.svg (w = 3, 5, 8) come from a local server:
  /download/svg?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&spokes_enabled=1
    &spokes_hub_od=18&spokes_rim_depth=3&spokes_count=6&spokes_fillet_tip=1.5
    &spokes_fillet_base=2&spokes_width=<w>
Output: static/help/spoke_width.svg (shown in static/spokes_2d_help.html).
"""
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "spoke_width.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
WIDTHS = [3, 5, 8]
# the zoom window, in drawing mm: the spoke that points straight up
ZX0, ZY0, ZW, ZH = -11.0, -29.0, 22.0, 23.0
ZS = 8.5                                  # px per mm in the zoom panels
LOC = 200                                 # px, locator view size
R_CROP = 34.0
GAP = 30
TOP = 78

SIDE = re.compile(r'<line x1="(-?[\d.]+)" y1="(-[\d.]+)" x2="(-?[\d.]+)" y2="(-[\d.]+)"([^>]*)/>')


def highlight(svg_text, width):
    """The drawing with the up-pointing spoke's two sides in the callout colour;
    returns it with the sides' (top, bottom) y."""
    ys = []

    def swap(m):
        x1, x2 = float(m[1]), float(m[3])
        if not (x1 == x2 and abs(abs(x1) - width / 2) < 1e-6):
            return m[0]
        ys.append((float(m[2]), float(m[4])))
        return (f'<line x1="{m[1]}" y1="{m[2]}" x2="{m[3]}" y2="{m[4]}" '
                f'stroke="{CALLOUT}" stroke-width="0.5"/>')

    body = SIDE.sub(swap, pulley_body(svg_text))
    assert len(ys) == 2, (width, ys)
    return body, ys[0]


def zoom_panel(x, width, svg_text):
    """One zoomed view of the spoke with its width dimensioned."""
    body, (y_top, y_bot) = highlight(svg_text, width)
    w, h = ZW * ZS, ZH * ZS
    fs = 13 / ZS
    ym = (y_top + y_bot) / 2
    half = width / 2
    return "\n".join([
        f'<text x="{x + w / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
        f'font-weight="700" fill="#1e293b" {FONT}>Spoke Width = {width} mm</text>',
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="#fff"/>',
        f'<svg x="{x}" y="{TOP}" width="{w}" height="{h}" viewBox="{ZX0} {ZY0} {ZW} {ZH}">',
        f'<g style="fill:none">{body}</g>',
        f'<line x1="{-half}" y1="{ym:.3f}" x2="{half}" y2="{ym:.3f}" stroke="{BLUE}" '
        f'stroke-width="0.14" marker-start="url(#arr)" marker-end="url(#arr)"/>',
        f'<text x="{half + 0.8}" y="{ym:.3f}" dy="0.35em" font-size="{fs:.3f}" '
        f'font-weight="700" fill="{BLUE}" {FONT}>{width} mm</text>',
        "</svg>",
        f'<rect x="{x}" y="{TOP}" width="{w}" height="{h}" fill="none" '
        f'stroke="{CALLOUT}" stroke-width="2"/>',
    ])


def locator(x, svg_text):
    """The whole pulley, with the zoomed spoke boxed."""
    s = LOC / (2 * R_CROP)
    bx, by = x + (ZX0 + R_CROP) * s, TOP + (ZY0 + R_CROP) * s
    return (f'<text x="{x + LOC / 2}" y="{TOP - 12}" text-anchor="middle" font-size="16" '
            f'font-weight="700" fill="#1e293b" {FONT}>Zoomed area</text>'
            f'<svg x="{x}" y="{TOP}" width="{LOC}" height="{LOC}" '
            f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">'
            f'<g style="fill:none">{pulley_body(svg_text)}</g></svg>'
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{ZW * s:.1f}" height="{ZH * s:.1f}" '
            f'fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>')


def main():
    """Assemble the figure and write it next to the help pages."""
    src = {w: (HERE / f"width_{w}.svg").read_text(encoding="utf-8") for w in WIDTHS}
    parts = [locator(GAP, src[5])]
    x = GAP + LOC + GAP
    for w in WIDTHS:
        parts.append(zoom_panel(x, w, src[w]))
        x += ZW * ZS + GAP
    width = x
    height = TOP + max(ZH * ZS, LOC) + 62
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">Spoke Width</text>
{chr(10).join(parts)}
<text x="{width / 2:.0f}" y="{height - 32:.0f}" text-anchor="middle" font-size="13"
      fill="#475569">The spoke's sides are parallel, so it is the same width all the way
along; the fillets only flare it where it meets the rim and hub.</text>
<text x="{width / 2:.0f}" y="{height - 14:.0f}" text-anchor="middle" font-size="13"
      fill="#475569">Wider spokes are stronger but leave smaller openings.
Zoom ×{ZS / (LOC / (2 * R_CROP)):.1f}.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    main()
