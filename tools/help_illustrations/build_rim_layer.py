"""Build the Gen. Rim Layer illustration from the app's own SVG downloads.

Left: the normal download (the spoked layer). Middle: the extra file that
Gen. Rim Layer adds, with its two parts shaded — the toothed ring and the hub
washer. Right: an exploded side view of a layer-cake stack built from them,
in the colours of static/LayerCakePulley.png.

The sources come from a local server, with the same settings:
  layer_spoked.svg  /download/svg?<q>
  layer_rim.svg     /download/svg-rim?<q>
  q = family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0&clearance_preset=STANDARD
      &backlash_preset=STANDARD&spokes_enabled=1&spokes_hub_od=18&spokes_rim_depth=3
      &spokes_width=5&spokes_count=6&spokes_fillet_tip=1.5&spokes_fillet_base=2
Output: static/help/rim_layer.svg (hover picture on Gen. Rim Layer).
"""
import re
from pathlib import Path

from build_spoke_count import BLUE, pulley_body

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "static" / "help" / "rim_layer.svg"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
RING, HUB, SPOKED = "#b9b3a8", "#5a63b8", "#2f2f2f"      # the photo's grey, blue, black
R_CROP = 34.0
PANEL = 230
GAP = 34
TITLE_Y = 32
TOP = 100


def ring(r):
    """A circle as path data, for even-odd filled bands."""
    return f"M {r} 0 A {r} {r} 0 1 0 {-r} 0 A {r} {r} 0 1 0 {r} 0 Z"


def layer_panel(x, title, sub, inner):
    return (f'<text x="{x + PANEL / 2}" y="{TOP - 30}" text-anchor="middle" font-size="16" '
            f'font-weight="700" fill="#1e293b" {FONT}>{title}</text>'
            f'<text x="{x + PANEL / 2}" y="{TOP - 11}" text-anchor="middle" font-size="12.5" '
            f'fill="#475569" {FONT}>{sub}</text>'
            f'<svg x="{x}" y="{TOP}" width="{PANEL}" height="{PANEL}" '
            f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">{inner}</svg>')


def main():
    spoked = (HERE / "layer_spoked.svg").read_text(encoding="utf-8")
    rim = (HERE / "layer_rim.svg").read_text(encoding="utf-8")
    outline = re.search(r'<path d="([^"]+)"', rim)[1]
    circles = sorted(float(r) for r in re.findall(r'<circle cx="0" cy="0" r="([\d.]+)"', rim))
    bore, hub, rim_in = circles                        # 4, 9, 26.207 here
    r_od = max(float(v) for v in re.findall(r"-?\d+\.\d+", outline) if float(v) < 40)

    x1 = GAP
    x2 = x1 + PANEL + GAP
    x3 = x2 + PANEL + GAP + 10
    s = PANEL / (2 * R_CROP)                           # px per mm, same scale everywhere

    parts = [
        layer_panel(x1, "Spoked layer", "the normal download",
                    f'<g style="fill:none">{pulley_body(spoked)}</g>'),
        layer_panel(x2, "Rim layer", "the extra file Gen. Rim Layer adds",
                    f'<path d="{outline} {ring(rim_in)}" fill="{RING}" fill-rule="evenodd"/>'
                    f'<path d="{ring(hub)} {ring(bore)}" fill="{HUB}" fill-rule="evenodd"/>'
                    f'<g style="fill:none">{pulley_body(rim)}</g>'),
        # labels on the rim layer's two parts
        f'<text x="{x2 + PANEL / 2}" y="{TOP + (R_CROP - rim_in + 5.0) * s:.1f}" '
        f'text-anchor="middle" font-size="11.5" font-weight="700" fill="#1e293b" {FONT}>'
        f'toothed ring</text>',
        f'<text x="{x2 + PANEL / 2}" y="{TOP + (R_CROP + hub) * s + 14:.1f}" '
        f'text-anchor="middle" font-size="11.5" font-weight="700" fill="{HUB}" {FONT}>'
        f'hub washer</text>',
        f'<text x="{x2 + (R_CROP - (rim_in + hub) / 2) * s:.1f}" y="{TOP + R_CROP * s + 4:.1f}" '
        f'text-anchor="middle" font-size="11" fill="#64748b" font-style="italic" {FONT}>'
        f'cut away</text>',
    ]

    # exploded side view: spoked layer at the bottom, rings and washers stacked on it
    sw = 2 * r_od * s * 0.78                           # a little narrower than the plan views
    k = sw / (2 * r_od)
    cx = x3 + sw / 2
    t, gap_px, n_rims = 13, 6, 3
    base_y = TOP + PANEL - 30
    parts.append(f'<text x="{cx}" y="{TOP - 30}" text-anchor="middle" font-size="16" '
                 f'font-weight="700" fill="#1e293b" {FONT}>Stacked (side view)</text>'
                 f'<text x="{cx}" y="{TOP - 11}" text-anchor="middle" font-size="12.5" '
                 f'fill="#475569" {FONT}>exploded to show the layers</text>')
    parts.append(f'<rect x="{cx - r_od * k:.1f}" y="{base_y}" width="{2 * r_od * k:.1f}" '
                 f'height="{t}" fill="{SPOKED}"/>')
    for i in range(1, n_rims + 1):
        y = base_y - i * (t + gap_px)
        for side in (-1, 1):
            a, b = sorted((side * r_od * k, side * rim_in * k))
            parts.append(f'<rect x="{cx + a:.1f}" y="{y}" width="{b - a:.1f}" height="{t}" '
                         f'fill="{RING}" stroke="#8a8378" stroke-width="0.8"/>')
            a, b = sorted((side * hub * k, side * bore * k))
            parts.append(f'<rect x="{cx + a:.1f}" y="{y}" width="{b - a:.1f}" height="{t}" '
                         f'fill="{HUB}"/>')
    top_y = base_y - n_rims * (t + gap_px)
    parts.append(f'<line x1="{cx}" y1="{top_y - 10}" x2="{cx}" y2="{base_y + t + 10}" '
                 f'stroke="#94a3b8" stroke-width="1" stroke-dasharray="4 3"/>')
    key_y = base_y + t + 34
    for i, (colour, text) in enumerate([(RING, "toothed rings — from the rim layer"),
                                        (HUB, "hub washers — from the rim layer"),
                                        (SPOKED, "spoked layer — the normal download")]):
        y = key_y + i * 19
        parts.append(f'<rect x="{x3}" y="{y - 10}" width="18" height="12" fill="{colour}"/>'
                     f'<text x="{x3 + 25}" y="{y}" font-size="12.5" fill="#1e293b" {FONT}>'
                     f'{text}</text>')

    width = x3 + max(sw, 250) + GAP
    height = max(TOP + PANEL, key_y + 3 * 19) + 44
    out = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="{TITLE_Y}" font-size="22" font-weight="700" fill="{BLUE}">Gen. Rim Layer</text>
{chr(10).join(parts)}
<text x="{width / 2:.0f}" y="{height - 16:.0f}" text-anchor="middle" font-size="13"
      fill="#475569">Cut one spoked layer plus as many rim layers as the belt is wide, and stack
them: a light pulley with full-width teeth. Shown only when Spokes is on.</text>
</svg>"""
    OUT.write_text(out, encoding="utf-8")
    print("wrote", OUT.relative_to(HERE.parent.parent), f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    main()
