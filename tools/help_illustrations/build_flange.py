"""Build the Flanges (3D) illustrations from the app's own downloads.

Same approach as build_hub.py (whose slicing and drawing helpers it reuses):
snapshots of the app's 3D preview (flange/shot_<name>.png, from shoot3d.js and
flange/shot_<name>.json) and vector cuts of its STLs through the axis, zoomed
on the rim, with the flange settings dimensioned where the geometry puts them.

Sources in flange/, all HTD 5M, 24 teeth, 12 mm bore, belt 10, clearance 0.5,
flange angle 15, rim radius 3:
  /download/stl?family=HTD&pitch=5M&teeth=24&bore=12&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&belt_height=10
    &clearance_height=0.5&flange_enabled=1&flange_angle=15&flange_rim_radius=3&<extra>
  metal_pulley   flange_3dprint=0&flange_plate_height=1&flange_bend_radius=0
  p3d_merged     flange_3dprint=1&flange_height=1.5&flange_top_separate=0
  nub_pulley     flange_3dprint=1&flange_height=1.5&<NUB>                 (the file is re-centred)
  nub_top        /download/flange-stl?...&<NUB>&flange_which=top
    NUB = flange_top_separate=1&flange_nubs_enabled=1&flange_nub_count=4&flange_nub_dia=3
          &flange_nub_height=2&flange_nub_allowance=0.2
  sup_asm        /download/flange-assembly?...&flange_3dprint=1&flange_height=1.5
                 &flange_top_separate=0&flange_supports_enabled=1&flange_support_nozzle_dia=0.4
                 &flange_support_max_spacing=10&flange_support_air_gap=0.2&hub_height=0
                 &spokes_width=4&spokes_fillet_tip=1&spokes_fillet_base=1.5&spokes_count=4
Snapshots: flange/shot_{metal,p3d_sep,p3d_merged}.json.
Output: static/help/flange_type.svg, flange_shape_3dp.svg, flange_shape_metal.svg,
flange_top_separate.svg, flange_nubs.svg, flange_supports.svg.
"""
import math
from pathlib import Path

import build_hub as H
from build_hub import BLUE, GREY, INK, dim, ext, filled, image, page, text

HERE = Path(__file__).parent
H.SRC = HERE / "flange"                      # build_hub's loaders read from here

R_OD = 18.53                                # tooth OD radius of this pulley (from its STL)
BELT_TOP = 10.5                             # belt height + clearance height
ANGLE, RIM = 15.0, 3.0
FL_H = 1.5                                  # 3D-print flange height
PLATE, BEND = 1.0, 1.5                      # metal plate thickness, automatic bend (1.5 x plate)
GAP, TOP = H.GAP, H.TOP


def axial_cut(name):
    """The STL cut through its axis, z up (SVG y = -z)."""
    return H.section(H.load(name), [0, 0, 0], [0, 1, 0], lambda p: (p[0], -p[2]))


def box(x, y, w_px, h_px, vb, inner):
    """A px box at (x, y) showing the drawing-mm box vb = (x0, y0, w, h)."""
    return (f'<svg x="{x:.1f}" y="{y:.1f}" width="{w_px:.1f}" height="{h_px:.1f}" '
            f'viewBox="{vb[0]:.3f} {vb[1]:.3f} {vb[2]:.3f} {vb[3]:.3f}">{inner}</svg>')


def angle_arc(cx, cy, r, deg, w):
    """Arc from horizontal (outward, +x) up by `deg`, centred on (cx, cy) in SVG y-down."""
    x1, y1 = cx + r, cy
    x2, y2 = cx + r * math.cos(math.radians(deg)), cy - r * math.sin(math.radians(deg))
    return (f'<path d="M {x1:.3f} {y1:.3f} A {r} {r} 0 0 0 {x2:.3f} {y2:.3f}" fill="none" '
            f'stroke="{BLUE}" stroke-width="{2 * w:.4f}"/>')


def flange_type():
    """Metal vs 3D print: the 3D preview above a cut through the rim."""
    col_w = 330
    vb = (6.5, -13.8, 17.0, 17.4)             # x 6.5..23.5, z -3.6..13.8
    cut_h = col_w * vb[3] / vb[2]
    s = col_w / vb[2]
    w, fs = 1 / s, 15 / s
    cols = [("metal_pulley", "metal", "Metal (3D Print off)",
             [(17.2, -12.9, "thin plates, bent up at the rim"),
              (17.2, 3.1, "separate parts, on both faces")]),
            ("p3d_merged", "p3d_merged", "3D Print",
             [(17.2, -12.9, "solid wedge"),
              (15.0, 3.1, "bottom flange is part of the pulley")])]
    snaps = {shot: H.snapshot(shot, col_w) for _, shot, _, _ in cols}
    snap_h = max(h for _, h in snaps.values())
    y_snap = TOP + 34
    y_cut = y_snap + snap_h + 16
    parts = []
    for i, (stl, shot, head, notes) in enumerate(cols):
        x = GAP + i * (col_w + 40)
        parts.append(text(x + col_w / 2, TOP + 16, head, 19, weight=700))
        uri, h = snaps[shot]
        parts.append(image(x, y_snap + (snap_h - h) / 2, col_w, h, uri))
        inner = filled(axial_cut(stl), 1.4 * w)
        inner += "".join(text(nx, ny, t, fs, GREY, 700, halo=True) for nx, ny, t in notes)
        parts.append(box(x, y_cut, col_w, cut_h, vb, inner))
    parts.append(text(GAP + 2 * col_w + 40 + 10, y_cut + cut_h / 2, "", 12))
    caps = ["Top: the 3D preview. Bottom: cut through the axis, near the rim.",
            "Metal flanges are stamped or laser-cut plates; 3D-printed flanges are solid."]
    width = GAP + 2 * col_w + 40 + GAP
    page("Flange type — the 3D Print box", "".join(parts), width,
         y_cut + cut_h + H.caption_h(len(caps)), "flange_type.svg", caps)


def _zoom_page(title, stl, dims_fn, caps, out_name):
    vb = (15.0, -13.4, 8.8, 4.8)              # x 15..23.8, z 8.6..13.4 — the top flange's rim
    w_px = 720
    h_px = w_px * vb[3] / vb[2]
    s = w_px / vb[2]
    inner = filled(axial_cut(stl), 1.4 / s) + dims_fn(1 / s, s)
    body = box(GAP, TOP + 6, w_px, h_px, vb, inner)
    page(title, body, GAP + w_px + GAP, TOP + 6 + h_px + H.caption_h(len(caps)), out_name, caps)


def _tooth_od(w, fs, z_lo=-9.0, z_hi=-13.2):
    return (ext(R_OD, z_lo, R_OD, z_hi, 1.2 * w) +
            text(R_OD - 0.12, z_lo - 0.1, "tooth OD", fs * 0.8, GREY, 700, anchor="end"))


def flange_shape_3dp():
    def dims(w, s):
        fs = 16 / s
        out = _tooth_od(w, fs)
        # Flange Angle: from the belt-side face's horizontal, up to the angled face
        out += ext(R_OD, -BELT_TOP, R_OD + RIM + 0.5, -BELT_TOP, w)
        out += angle_arc(R_OD, -BELT_TOP, 2.2, ANGLE, w)
        out += text(R_OD + 2.35, -BELT_TOP - 0.2, f"Flange Angle {ANGLE:g}°", fs, BLUE, 700,
                    anchor="start", halo=True)
        # Rim Radius: tooth OD to the flange's outer edge
        out += ext(R_OD + RIM, -BELT_TOP - 0.8, R_OD + RIM, -9.25, w)
        out += dim(R_OD, -9.5, R_OD + RIM, -9.5, 2 * w)
        out += text(R_OD + RIM / 2, -9.62, f"Rim Radius {RIM:g} mm", fs, BLUE, 700, halo=True)
        # Flange Height: the thickness where the flange leaves the teeth
        out += dim(R_OD - 0.9, -BELT_TOP, R_OD - 0.9, -BELT_TOP - FL_H, 2 * w)
        out += text(R_OD - 1.05, -BELT_TOP - FL_H / 2 - 0.15, "Flange", fs, BLUE, 700,
                    anchor="end", halo=True)
        out += text(R_OD - 1.05, -BELT_TOP - FL_H / 2 + 0.25, f"Height {FL_H:g}", fs, BLUE, 700,
                    anchor="end", halo=True)
        lip = FL_H - RIM * math.tan(math.radians(ANGLE))
        out += text(R_OD + RIM + 0.12, -BELT_TOP - FL_H + lip / 2 + 0.12, f"lip {lip:.1f}",
                    fs * 0.8, GREY, 700, anchor="start")
        out += text(16.2, -BELT_TOP + 0.55, "belt side", fs * 0.8, GREY)
        return out

    _zoom_page("3D-printed flange shape", "p3d_merged", dims,
               ["Top flange, cut through the axis. Flange Height is its thickness at the teeth;",
                "the angled face rises toward the rim, so the outer lip is thinner."],
               "flange_shape_3dp.svg")


def flange_shape_metal():
    def dims(w, s):
        fs = 16 / s
        out = _tooth_od(w, fs)
        # Plate Thickness
        out += dim(16.3, -BELT_TOP, 16.3, -BELT_TOP - PLATE, 2 * w)
        out += text(16.15, -BELT_TOP - PLATE / 2 + 0.12, f"Plate {PLATE:g}", fs, BLUE, 700,
                    anchor="end", halo=True)
        # Bend Radius: tooth OD to the end of the bend
        out += ext(R_OD + BEND, -BELT_TOP - 0.3, R_OD + BEND, -9.9, w)
        out += dim(R_OD, -10.1, R_OD + BEND, -10.1, 2 * w)
        out += text(R_OD + BEND / 2, -10.22, f"Bend {BEND:g}", fs, BLUE, 700, halo=True)
        # Rim Radius: tooth OD to the outer edge
        out += ext(R_OD + RIM, -BELT_TOP - 0.7, R_OD + RIM, -9.1, w)
        out += dim(R_OD, -9.3, R_OD + RIM, -9.3, 2 * w)
        out += text(R_OD + RIM / 2, -9.42, f"Rim Radius {RIM:g} mm", fs, BLUE, 700, halo=True)
        # Flange Angle, on the straight part after the bend (its underside)
        x0, z0 = 20.29, 10.73
        out += ext(x0, -z0, x0 + 2.0, -z0, w)
        out += angle_arc(x0, -z0, 1.6, ANGLE, w)
        out += text(x0 + 1.75, -z0 - 0.15, f"{ANGLE:g}°", fs, BLUE, 700, anchor="start", halo=True)
        out += text(16.2, -BELT_TOP + 0.55, "pulley face", fs * 0.8, GREY)
        return out

    _zoom_page("Metal flange shape", "metal_pulley", dims,
               ["Top plate, cut through the axis. Bend Radius 0 = automatic (1.5 × plate).",
                "Angle and Rim Radius work as for 3D-printed flanges."],
               "flange_shape_metal.svg")


def flange_top_separate():
    col_w = 330
    cols = [("p3d_sep", "Ticked: separate part",
             "printed on its own, glued on"),
            ("p3d_merged", "Unticked: one piece",
             "top flange printed with the pulley")]
    snaps = {n: H.snapshot(n, col_w) for n, _, _ in cols}
    snap_h = max(h for _, h in snaps.values())
    parts = []
    for i, (n, head, note) in enumerate(cols):
        x = GAP + i * (col_w + 40)
        parts.append(text(x + col_w / 2, TOP + 16, head, 19, weight=700))
        uri, h = snaps[n]
        parts.append(image(x, TOP + 34 + (snap_h - h) / 2, col_w, h, uri))
        parts.append(text(x + col_w / 2, TOP + 34 + snap_h + 22, note, 15, GREY))
    caps = ["A separate top flange prints flat with no overhang; add Gluing Nubs to line it up.",
            "One piece needs the overhang bridged — see Add Print Supports."]
    width = GAP + 2 * col_w + 40 + GAP
    page("Top flange as separate part", "".join(parts), width,
         TOP + 34 + snap_h + 34 + H.caption_h(len(caps)), "flange_top_separate.svg", caps)


FLANGE_FILL = "#bfdbfe"                        # the separate top flange, told apart from the pulley


def _filled_as(loops, w, fill):
    return filled(loops, w).replace(f'fill="{H.MATERIAL}"', f'fill="{fill}"')


def flange_nubs():
    """Sockets in the pulley face, and a cut through one nub: pin in socket."""
    from shapely.geometry import Polygon
    NUB_D, NUB_H, ALLOW = 3.0, 2.0, 0.2
    pul, top = H.load("nub_pulley"), H.load("nub_top")
    dz = BELT_TOP - float(pul.bounds[1][2])          # that file is re-centred: seat it
    face = H.section(pul, [0, 0, BELT_TOP - 1.0 - dz], [0, 0, 1], lambda p: (p[0], -p[1]))
    pin_x = 12.87                                     # nub at 0° (measured from the flange STL)
    cut_p = H.section(pul, [0, 0, 0], [0, 1, 0], lambda p: (p[0], -(p[2] + dz)))
    cut_f = H.section(top, [0, 0, 0], [0, 1, 0], lambda p: (p[0], -p[2]))

    left_w = 330
    half = 20.0
    s_l = left_w / (2 * half)
    left = H.view(GAP, TOP + 30, left_w, half,
                  filled(face, 1.3 / s_l) +
                  text(0, 0.8, "4 sockets", 16 / s_l, BLUE, 700, halo=True) +
                  "".join(f'<circle cx="{pin_x * c:.2f}" cy="{-pin_x * sn:.2f}" r="{NUB_D / 2 + 0.9}" '
                          f'fill="none" stroke="{BLUE}" stroke-width="{2 / s_l:.3f}"/>'
                          for c, sn in ((1, 0), (0, 1), (-1, 0), (0, -1))))

    vb = (pin_x - 4.2, -12.9, 8.4, 5.6)              # around the nub at 0°
    w_px = 470
    s = w_px / vb[2]
    w, fs = 1 / s, 15 / s
    rs, rp = NUB_D / 2, (NUB_D - ALLOW) / 2
    sock_bot, pin_bot = BELT_TOP - NUB_H, BELT_TOP - (NUB_H - ALLOW)
    inner = (filled(cut_p, 1.3 * w) + _filled_as(cut_f, 1.3 * w, FLANGE_FILL) +
             # Nub Diameter: the socket, and the thinner pin
             dim(pin_x - rs, -(sock_bot - 0.45), pin_x + rs, -(sock_bot - 0.45), 2 * w) +
             text(pin_x, -(sock_bot - 0.45) + 0.55, f"socket Ø {NUB_D:g} = Nub Diameter", fs,
                  BLUE, 700, halo=True) +
             dim(pin_x - rp, -(BELT_TOP + 0.75), pin_x + rp, -(BELT_TOP + 0.75), 2 * w) +
             text(pin_x, -(BELT_TOP + 0.9), f"pin Ø {NUB_D - ALLOW:g}", fs, BLUE, 700, halo=True) +
             # Nub Height: the socket's depth
             dim(pin_x + rs + 0.55, -BELT_TOP, pin_x + rs + 0.55, -sock_bot, 2 * w) +
             text(pin_x + rs + 0.7, -(BELT_TOP + sock_bot) / 2 + 0.1, "Nub", fs, BLUE, 700,
                  anchor="start", halo=True) +
             text(pin_x + rs + 0.7, -(BELT_TOP + sock_bot) / 2 + 0.55, f"Height {NUB_H:g}", fs,
                  BLUE, 700, anchor="start", halo=True) +
             # the fit allowance left under the pin
             text(pin_x - rs - 0.25, -(sock_bot + pin_bot) / 2 + 0.12, f"gap {ALLOW:g}", fs * 0.85,
                  GREY, 700, anchor="end", halo=True) +
             text(vb[0] + 0.2, -(BELT_TOP + 1.25), "top flange", fs * 0.9, "#1d4ed8", 700,
                  anchor="start") +
             text(vb[0] + 0.2, -(BELT_TOP - 1.0), "pulley", fs * 0.9, GREY, 700, anchor="start"))
    x_r = GAP + left_w + 40
    right = box(x_r, TOP + 30, w_px, w_px * vb[3] / vb[2], vb, inner)
    h = max(left_w, w_px * vb[3] / vb[2])
    body = (text(GAP + left_w / 2, TOP + 16, "Pulley face, from above", 17, weight=700) + left +
            text(x_r + w_px / 2, TOP + 16, "Cut through one nub", 17, weight=700) + right)
    caps = ["The nubs are spaced evenly; each pin on the top flange drops into a socket.",
            "Socket Fit Allowance makes the pin thinner and shorter than its socket."]
    page("Gluing Nubs", body, x_r + w_px + GAP, TOP + 30 + h + H.caption_h(len(caps)),
         "flange_nubs.svg", caps)


def flange_supports():
    """The support tube and ribs across, and a cut through a rib: the air gap."""
    from shapely.geometry import LineString, Polygon
    asm = H.load("sup_asm")
    across = H.section(asm, [0, 0, 5.0], [0, 0, 1], lambda p: (p[0], -p[1]))
    rib_deg = 11.2                                    # a rib (measured from the STL)
    t = math.radians(rib_deg)
    cut = H.section(asm, [0, 0, 0], [-math.sin(t), math.cos(t), 0],
                    lambda p: (p[0] * math.cos(t) + p[1] * math.sin(t), -p[2]))
    # rib top and flange underside at one radius, from the geometry
    xg = 21.3
    under = BELT_TOP + (xg - R_OD) * math.tan(math.radians(ANGLE))
    tops = [max(-y for x, y in LineString([(xg, -20), (xg, 20)]).intersection(Polygon(l)).coords)
            for l in cut if Polygon(l).bounds[0] > 18 and Polygon(l).bounds[2] < 22.6
            and not LineString([(xg, -20), (xg, 20)]).intersection(Polygon(l)).is_empty]
    rib_top = max(tops)

    left_w = 360
    half = 24.5
    s_l = left_w / (2 * half)
    fs_l = 15 / s_l
    left = H.view(GAP, TOP + 30, left_w, half,
                  filled(across, 1.2 / s_l) +
                  text(0, -9.0, "pulley", fs_l, GREY, 700, halo=True) +
                  text(0, -24.0 + 1.6, "tube", fs_l, BLUE, 700, halo=True) +
                  text(20.2, 3.0, "slit", fs_l, BLUE, 700, anchor="end", halo=True) +
                  text(-20.2, 3.0, "slit", fs_l, BLUE, 700, anchor="start", halo=True) +
                  text(13.5, -17.0, "ribs", fs_l, BLUE, 700, halo=True))

    vb = (17.6, -12.4, 6.0, 4.4)
    w_px = 470
    s = w_px / vb[2]
    w, fs = 1 / s, 15 / s
    inner = (filled(cut, 1.3 * w) +
             dim(xg, -rib_top, xg, -under, 1.8 * w) +
             ext(xg, -under, xg + 0.9, -under, w) +
             text(xg - 0.15, -under - 0.35, f"Air Gap {under - rib_top:.1f}", fs, BLUE, 700,
                  anchor="end", halo=True) +
             dim(22.53, -9.0, 22.92, -9.0, 1.8 * w) +
             text(22.72, -8.55, "wall = Nozzle Dia.", fs * 0.9, BLUE, 700, anchor="end", halo=True) +
             text(19.3, -9.3, "rib", fs, GREY, 700, halo=True) +
             text(18.0, -(BELT_TOP + FL_H) + 0.45, "top flange", fs * 0.9, GREY, 700, anchor="start"))
    x_r = GAP + left_w + 40
    right = box(x_r, TOP + 30, w_px, w_px * vb[3] / vb[2], vb, inner)
    h = max(left_w, w_px * vb[3] / vb[2])
    body = (text(GAP + left_w / 2, TOP + 16, "Cut across, below the flange", 17, weight=700) +
            left + text(x_r + w_px / 2, TOP + 16, "Cut through a rib", 17, weight=700) + right)
    caps = ["A thin tube 1 mm outside the flange, with ribs no more than Max Spacing apart,",
            "holds up the top flange's overhang; the slits let it snap off. Assembly STL only."]
    page("Print Supports", body, x_r + w_px + GAP, TOP + 30 + h + H.caption_h(len(caps)),
         "flange_supports.svg", caps)


if __name__ == "__main__":
    flange_type()
    flange_shape_3dp()
    flange_shape_metal()
    flange_top_separate()
    flange_nubs()
    flange_supports()
