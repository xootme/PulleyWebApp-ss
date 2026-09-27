"""Build the Advanced-settings illustrations from the app's own SVG downloads.

Tooth Clearance, Backlash and 3D Print Compensation each move a tooth groove by
a fraction of a millimetre, so each picture zooms right in on one groove and
overlays the variants on the Standard outline, with a key giving the measured
change. Shown on an Imperial L belt, where the presets differ most; on HTD
belts Tight is the same as Standard.

The sources adv_<name>.svg come from a local server:
  /download/svg?family=Imperial&pitch=L&teeth=20&bore=8&print_extra=<pe>
    &clearance_preset=<cl>&backlash_preset=<bl>
  std       pe=0    cl=STANDARD bl=STANDARD
  cl_tight  pe=0    cl=TIGHT    bl=STANDARD
  cl_loose  pe=0    cl=LOOSE    bl=STANDARD
  bl_ex     pe=0    cl=STANDARD bl=CUSTOM backlash_custom=1.0 (exaggerated)
  bl_tight  pe=0    cl=STANDARD bl=TIGHT
  bl_loose  pe=0    cl=STANDARD bl=LOOSE
  pe02      pe=0.2  cl=STANDARD bl=STANDARD
They are fetched fresh through the app's test client on every run
(fetch_sources), so the pictures always show the current geometry.
pe02 is drawn with the perpendicular offset of the whole outline (generate_profile_groove).
Output: static/help/clearance.svg, backlash.svg, print_comp.svg
(shown in static/2d_advanced_help.html).
"""
import math
import re
from pathlib import Path

from build_spoke_count import BLUE, CALLOUT

HERE = Path(__file__).parent
HELP = HERE.parent.parent / "static" / "help"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
INK = "#1a1a1a"
# the zoom window, in drawing mm after turning the pulley so a groove faces up
ZX0, ZY0, ZW, ZH = -6.5, -30.5, 13.0, 3.8
ZS = 44.0                                 # px per mm in the zoom
LOC = 150                                 # px, locator view size
R_CROP = 32.0
GAP = 30
TOP = 50


SOURCES = {
    "std": {}, "cl_tight": {"clearance_preset": "TIGHT"}, "cl_loose": {"clearance_preset": "LOOSE"},
    "bl_ex": {"backlash_preset": "CUSTOM", "backlash_custom": "1.0"},
    "bl_tight": {"backlash_preset": "TIGHT"}, "bl_loose": {"backlash_preset": "LOOSE"},
    "pe02": {"print_extra": "0.2"},
}


def fetch_sources():
    """(Re)write adv_<name>.svg from the app's own /download/svg (test client)."""
    import sys
    sys.path.insert(0, str(HERE.parent.parent))
    from app import app
    c = app.test_client()
    base = {"family": "Imperial", "pitch": "L", "teeth": "20", "bore": "8", "print_extra": "0",
            "clearance_preset": "STANDARD", "backlash_preset": "STANDARD"}
    for name, extra in SOURCES.items():
        r = c.get("/download/svg", query_string={**base, **extra})
        assert r.status_code == 200, (name, r.status_code)
        (HERE / f"adv_{name}.svg").write_bytes(r.data)


def outline(name):
    """The tooth outline path's d attribute (the drawing's longest path)."""
    svg = (HERE / f"adv_{name}.svg").read_text(encoding="utf-8")
    return max(re.findall(r'<path d="([^"]+)"', svg), key=len)


def radii(d):
    """(groove bottom, tooth tip) radius of an outline."""
    n = [float(v) for v in re.findall(r"-?\d+\.\d+", d)]
    r = [math.hypot(x, y) for x, y in zip(n[0::2], n[1::2])]
    r = [v for v in r if v < 40]              # drop arc radii read as coordinates
    return min(r), max(r)


def groove_width(d, at_r):
    """Width of the groove centred on x=0 at radius at_r (chord between its walls)."""
    pts = [(float(a), float(b)) for a, b in re.findall(r"[ML]\s*(-?[\d.]+)[ ,](-?[\d.]+)", d)]
    pts = [p for p in pts if abs(p[0]) < 5 and p[1] > 0]
    xs = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        ra, rb = math.hypot(ax, ay), math.hypot(bx, by)
        if (ra - at_r) * (rb - at_r) < 0:
            t = (at_r - ra) / (rb - ra)
            xs.append(ax + t * (bx - ax))
    return max(xs) - min(xs)


def outlines_in(variants, px, py, pw, ph, box):
    """The variant outlines, turned groove-up, in a px rectangle showing the
    drawing-mm box (x, y, w, h). Lines keep the same px weight at any zoom."""
    g = [f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="#fff"/>',
         f'<svg x="{px}" y="{py}" width="{pw}" height="{ph}" '
         f'viewBox="{" ".join(f"{v:.3f}" for v in box)}">'
         f'<g transform="rotate(180)" fill="none">']
    for d, colour, dashed in variants[1:] + variants[:1]:   # Standard last, on top
        dash = ' stroke-dasharray="5 3.5"' if dashed else ""
        g.append(f'<path d="{d}" stroke="{colour}" stroke-width="2.2"{dash} '
                 f'vector-effect="non-scaling-stroke"/>')
    g += ["</g></svg>",
          f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="none" '
          f'stroke="{CALLOUT}" stroke-width="2"/>']
    return "\n".join(g)


def wall_point(d, at_r):
    """Where the groove's right-hand wall (as seen groove-up) crosses radius at_r,
    in groove-up coordinates."""
    pts = [(float(a), float(b)) for a, b in re.findall(r"[ML]\s*(-?[\d.]+)[ ,](-?[\d.]+)", d)]
    pts = [p for p in pts if -5 < p[0] < 0 and p[1] > 0]    # rotate(180) puts x<0 on the right
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        ra, rb = math.hypot(ax, ay), math.hypot(bx, by)
        if (ra - at_r) * (rb - at_r) < 0:
            t = (at_r - ra) / (rb - ra)
            return (-(ax + t * (bx - ax)), -(ay + t * (by - ay)))
    raise ValueError("no wall crossing")


def figure(title, variants, key_lines, note, out_name, inset=None):
    """variants: [(outline d, colour, dashed)], Standard first.
    inset: (cx, cy, half) in groove-up mm — a closer zoom shown under the main one."""
    zw, zh = ZW * ZS, ZH * ZS
    x0 = GAP + LOC + GAP
    s = LOC / (2 * R_CROP)
    loc_y = TOP + 10
    # locator: the whole standard pulley turned the same way, groove boxed
    std = variants[0][0]
    bx, by = GAP + (ZX0 + R_CROP) * s, loc_y + (ZY0 + R_CROP) * s
    parts = [
        f'<svg x="{GAP}" y="{loc_y}" width="{LOC}" height="{LOC}" '
        f'viewBox="{-R_CROP} {-R_CROP} {2 * R_CROP} {2 * R_CROP}">'
        f'<path d="{std}" transform="rotate(180)" fill="none" stroke="{INK}" '
        f'stroke-width="0.3"/></svg>',
        f'<rect x="{bx:.1f}" y="{by:.1f}" width="{max(ZW * s, 6):.1f}" '
        f'height="{max(ZH * s, 4):.1f}" fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>',
        f'<text x="{GAP + LOC / 2}" y="{loc_y + LOC + 18}" text-anchor="middle" '
        f'font-size="13" fill="#475569" {FONT}>zoomed groove</text>',
        outlines_in(variants, x0, TOP, zw, zh, (ZX0, ZY0, ZW, ZH)),
    ]
    ky = TOP + zh + 26
    bottom = 0
    if inset:
        cx, cy, half = inset
        size = 170
        ix, iy = x0 + zw - size, TOP + zh + 22
        # the inset's area on the main zoom, and leaders down to the inset
        ax, ay = x0 + (cx - half - ZX0) * ZS, TOP + (cy - half - ZY0) * ZS
        a = 2 * half * ZS
        parts += [f'<rect x="{ax:.1f}" y="{ay:.1f}" width="{a:.1f}" height="{a:.1f}" '
                  f'fill="none" stroke="{CALLOUT}" stroke-width="1.5"/>',
                  f'<line x1="{ax:.1f}" y1="{ay + a:.1f}" x2="{ix}" y2="{iy}" '
                  f'stroke="{CALLOUT}" stroke-width="1"/>',
                  f'<line x1="{ax + a:.1f}" y1="{ay + a:.1f}" x2="{ix + size}" y2="{iy}" '
                  f'stroke="{CALLOUT}" stroke-width="1"/>',
                  outlines_in(variants, ix, iy, size, size,
                              (cx - half, cy - half, 2 * half, 2 * half)),
                  f'<text x="{ix - 8}" y="{iy + size - 6}" text-anchor="end" font-size="12" '
                  f'fill="{CALLOUT}" font-weight="600" {FONT}>groove wall, zoom '
                  f'×{size / (2 * half) / (LOC / (2 * R_CROP)):.0f}</text>']
        bottom = iy + size
    for i, (colour, dashed, text) in enumerate(key_lines):
        y = ky + i * 21
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        parts.append(f'<line x1="{x0}" y1="{y - 4}" x2="{x0 + 26}" y2="{y - 4}" '
                     f'stroke="{colour}" stroke-width="2.5"{dash}/>'
                     f'<text x="{x0 + 34}" y="{y}" font-size="13" fill="#1e293b" {FONT}>{text}</text>')
    width = x0 + zw + GAP
    height = max(ky + len(key_lines) * 21, loc_y + LOC + 24, bottom + 10) + 30
    parts.append(f'<text x="{x0}" y="{height - 14:.0f}" font-size="12.5" fill="#475569" '
                 f'{FONT}>{note}</text>')
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="30" font-size="22" font-weight="700" fill="{BLUE}">{title}</text>
{chr(10).join(parts)}
</svg>"""
    (HELP / out_name).write_text(svg, encoding="utf-8")
    print("wrote static/help/" + out_name, f"{width:.0f} x {height:.0f}")


def main():
    std, cl_t, cl_l = outline("std"), outline("cl_tight"), outline("cl_loose")
    pe = outline("pe02")
    zoom = f"Zoom ×{ZS / (LOC / (2 * R_CROP)):.0f}"

    b_std, t_std = radii(std)
    b_t, b_l = radii(cl_t)[0], radii(cl_l)[0]
    figure("Tooth Clearance",
           [(std, INK, True), (cl_t, BLUE, False), (cl_l, CALLOUT, False)],
           [(INK, True, "Standard: the ISO groove"),
            (BLUE, False, f"Tight: groove bottom raised {b_t - b_std:.2f} mm, to touch the belt tooth"),
            (CALLOUT, False, f"Loose: groove bottom {b_std - b_l:.2f} mm deeper")],
           f"L belt, 20 teeth, where the presets differ most. Mainly the groove bottom moves. {zoom}.",
           "clearance.svg")

    # Loose on L is only 0.10 mm — too small to see — so it is drawn with an
    # exaggerated Custom value and says so. Tight (−0.34 mm on L) is the real preset.
    bl_x, bl_t = outline("bl_ex"), outline("bl_tight")
    r_mid = (b_std + t_std) / 2
    w_std = groove_width(std, r_mid)
    tight_in = (w_std - groove_width(bl_t, r_mid)) / 2
    assert tight_in > 0.05, tight_in            # Tight really narrows the groove
    figure("Backlash",
           [(std, INK, True), (bl_t, BLUE, False), (bl_x, CALLOUT, False)],
           [(INK, True, "Standard: the ISO groove"),
            (BLUE, False, f"Tight: each groove wall moves in {tight_in:.2f} mm, to touch the belt tooth"),
            (CALLOUT, False, "More backlash: the groove walls move outward "
                             "(exaggerated: Custom 1.0 mm)")],
           f"L belt, 20 teeth. Loose is exaggerated: the real Loose preset here is only 0.10 mm. {zoom}.",
           "backlash.svg")

    b_pe, t_pe = radii(pe)
    figure("3D Print Compensation",
           [(std, INK, True), (pe, CALLOUT, False)],
           [(INK, True, "0 mm: the part as designed"),
            (CALLOUT, False, f"0.2 mm: every surface moves {t_std - t_pe:.2f} mm into the material "
                             f"(tips {t_std - t_pe:.2f}, groove bottom {b_std - b_pe:.2f})")],
           f"Printed plastic bulges outward, so the outline is drawn smaller to print at size. {zoom}.",
           "print_comp.svg")


if __name__ == "__main__":
    fetch_sources()
    main()
