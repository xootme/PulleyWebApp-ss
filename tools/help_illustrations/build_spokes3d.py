"""Build the Spoke Height (3D) illustration from the app's own downloads.

Spoke Height is the thickness of the spoke web, centred in the pulley's
height: the hub and rim stay full height, and a pocket of
(pulley height − Spoke Height) / 2 is left above and below each spoke.
0 means full height. Shown with the app's 3D preview (spokes3d/shot_h<h>.png,
from shoot3d.js) over a cut of its STL along one spoke, bore to rim.

Sources spokes3d/h<h>.stl (h = 0, 6, 3), from a local server:
  /download/stl?family=HTD&pitch=5M&teeth=40&bore=8&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&belt_height=10
    &clearance_height=0.5&spokes_enabled=1&spokes_hub_od=18&spokes_rim_depth=3
    &spokes_width=5&spokes_count=6&spokes_fillet_tip=1.5&spokes_fillet_base=2
    &spokes_height=<h>
Output: static/help/spoke_height.svg.
"""
import math
from pathlib import Path

import build_hub as H
from build_flange import box
from build_hub import BLUE, GREY, dim, ext, filled, image, page, text

HERE = Path(__file__).parent
H.SRC = HERE / "spokes3d"

FULL = 10.5                                  # belt height + clearance height
SPOKE_DEG = 60.0                             # a spoke (measured from the STL)
R_MID = 17.0                                 # mid-spoke, between hub (9) and rim (26.2)
GAP, TOP = H.GAP, H.TOP


def along_spoke(name):
    """The STL cut through the axis along one spoke, z up (SVG y = -z)."""
    t = math.radians(SPOKE_DEG)
    return H.section(H.load(name), [0, 0, 0], [-math.sin(t), math.cos(t), 0],
                     lambda p: (p[0] * math.cos(t) + p[1] * math.sin(t), -p[2]))


def spoke_height():
    heights = [0.0, 6.0, 3.0]
    col_w = 280
    vb = (2.5, -13.4, 29.5, 16.0)             # radius 2.5..32, z -2.6..13.4
    cut_h = col_w * vb[3] / vb[2]
    s = col_w / vb[2]
    w, fs = 1 / s, 14 / s
    snaps = {h: H.snapshot(f"h{h:g}", col_w) for h in heights}
    snap_h = max(v for _, v in snaps.values())
    y_snap = TOP + 34
    y_cut = y_snap + snap_h + 14
    parts = []
    for i, h in enumerate(heights):
        x = GAP + i * (col_w + GAP)
        web = FULL if h == 0 else h
        lo = (FULL - web) / 2
        head = "Spoke Height 0 (full)" if h == 0 else f"Spoke Height {h:g} mm"
        parts.append(text(x + col_w / 2, TOP + 16, head, 18, weight=700))
        uri, sh = snaps[h]
        parts.append(image(x, y_snap + (snap_h - sh) / 2, col_w, sh, uri))
        inner = filled(along_spoke(f"h{h:g}"), 1.3 * w)
        inner += dim(R_MID, -lo, R_MID, -(lo + web), 2 * w)
        inner += text(R_MID + 0.8, -(lo + web / 2) + 0.55, f"{web:g}", fs * 1.15, BLUE, 700,
                      anchor="start", halo=True)
        if lo > 0:
            inner += text(R_MID, -(FULL + 0.55), f"pocket {lo:g}", fs * 0.9, GREY, 700, halo=True)
            inner += text(R_MID, 1.65, f"pocket {lo:g}", fs * 0.9, GREY, 700, halo=True)
        inner += text(6.4, 1.8, "hub", fs * 0.9, GREY, 700)
        inner += text(28.6, 1.8, "rim", fs * 0.9, GREY, 700)
        parts.append(box(x, y_cut, col_w, cut_h, vb, inner))
    parts.append(text(GAP + col_w / 2, y_cut + cut_h + 18, "cut along one spoke, bore → rim",
                      13, GREY))
    caps = ["Spoke Height is the spoke web's thickness, centred: the hub and rim stay full height",
            "and a pocket is left above and below each spoke. 0 = spokes the full height."]
    width = GAP + len(heights) * (col_w + GAP)
    page("Spoke Height (3D)", "".join(parts), width,
         y_cut + cut_h + 30 + H.caption_h(len(caps)), "spoke_height.svg", caps)


if __name__ == "__main__":
    spoke_height()
