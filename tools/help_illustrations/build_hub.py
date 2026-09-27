"""Build the Hub (3D) illustrations from the app's own downloads.

Each picture pairs, where useful, a snapshot of the app's 3D preview
(hub/shot_<name>.png, taken by shoot3d.js from hub/shot_<name>.json) with a
vector section of the app's STL (hub/<name>.stl), sliced with trimesh:
  * a vertical section through the axis (x-z) for Hub Height / Hub OD
  * horizontal slices at the set-screw height (x-y) for everything else

Sources, all HTD 5M, 24 teeth, 12 mm bore, belt 10, clearance 0.5, hub OD 26 x 12:
  /download/stl?family=HTD&pitch=5M&teeth=24&bore=12&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&belt_height=10
    &clearance_height=0.5&hub_od=26&hub_height=12&<extra>
  none                 (nothing extra)
  screw_std            hub_screw_dia=5&hub_screw_count=2&hub_captured_nut=0
  screw_nut            hub_screw_dia=5&hub_screw_count=1&hub_captured_nut=1
  dshaft               hub_flat_depth=1
  keyway               hub_keyway_w=4&hub_keyway_h=2.6
  size_std_<d>         hub_screw_dia=<d>&hub_screw_count=1&hub_captured_nut=0   (d = 3, 5, 8)
  size_nut_<d>         hub_screw_dia=<d>&hub_screw_count=1&hub_captured_nut=1
  count_std_<n>        hub_screw_dia=5&hub_screw_count=<n>&hub_captured_nut=0   (n = 1, 2)
  count_nut_<n>        hub_screw_dia=5&hub_screw_count=<n>&hub_captured_nut=1
  flat_<f>             hub_flat_depth=<f>                                       (f = 0.5, 1, 2)
  kw_w_<w>             hub_keyway_w=<w>&hub_keyway_h=2.6                        (w = 3, 4, 6)
  kw_h_<h>             hub_keyway_w=4&hub_keyway_h=<h>                          (h = 1.5, 2.6, 4)
Output: static/help/hub_*.svg (hover pictures on the Hub panel, and Hub_help.html).
Pictures are sized to be read at their own size in the hover pop-up (<= ~1000 px wide).
"""
import base64
import io
from pathlib import Path

import numpy as np
import trimesh

from build_spoke_count import BLUE, CALLOUT

HERE = Path(__file__).parent
SRC = HERE / "hub"
HELP = HERE.parent.parent / "static" / "help"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
MATERIAL = "#cbd5e1"
EDGE = "#334155"
INK = "#1e293b"
GREY = "#475569"
GAP = 26
TOP = 70                                      # page px: below the title

BELT, CLEAR, HUB_H, HUB_OD, BORE = 10.0, 0.5, 12.0, 26.0, 12.0
TEETH_TOP = BELT + CLEAR                      # the hub starts here
SCREW_Z = TEETH_TOP + HUB_H / 2               # set screws sit mid-hub
R_BORE, R_HUB = BORE / 2, HUB_OD / 2


# ── sources ──────────────────────────────────────────────────────────────────

def load(name):
    """A binary STL as a mesh. The app appends its design metadata after the
    triangles, so read exactly the declared triangle count."""
    b = (SRC / f"{name}.stl").read_bytes()
    n = int(np.frombuffer(b[80:84], "<u4")[0])
    rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    t = np.frombuffer(b[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t["v"].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def section(mesh, origin, normal, to2d):
    """Closed section loops as lists of 2D points; to2d maps a 3D point to (u, v).

    The plane cuts the mesh into segments (trimesh.intersections, no networkx
    needed); shapely's polygonize joins them into faces. Every face's outline
    is returned — drawn together with an even-odd fill, rings and holes come
    out right. A plane exactly on a row of vertices gives degenerate cuts, so
    it is nudged off, and segment ends are snapped so neighbours share them."""
    from shapely.geometry import MultiLineString
    from shapely.ops import polygonize, unary_union
    origin = np.asarray(origin, float) + np.asarray(normal, float) * 1.37e-3
    segs = trimesh.intersections.mesh_plane(mesh, plane_normal=normal, plane_origin=origin)

    def snap(p):
        return tuple(round(float(c), 4) for c in to2d(p))
    lines = MultiLineString([[snap(a), snap(b)] for a, b in segs if snap(a) != snap(b)])
    return [list(f.exterior.coords) for f in polygonize(unary_union(lines))]


def screw_z(name, mesh):
    """Height of the set screw. Standard screws sit mid-hub; a captured nut's
    screw sits at the nut's centre, hub top − hex circumradius (as in
    step_exporter; the mesh top already includes any auto-raised hub height)."""
    if "nut" not in name:
        return SCREW_Z
    from exporters.step_exporter import _nut_dims
    d = float(name.split("_")[2]) if name.startswith("size_nut_") else 5.0
    waf, _ = _nut_dims(d)
    return float(mesh.bounds[1][2]) - waf / np.sqrt(3)


def slice_at_screw(name):
    """Horizontal slice at the set-screw height, y up (0° right, 90° up)."""
    m = load(name)
    return section(m, [0, 0, screw_z(name, m)], [0, 0, 1], lambda p: (p[0], -p[1]))


def snapshot(name, width):
    """The app's 3D preview (hub/shot_<name>.png) trimmed to the model and scaled
    to `width` px: (data URI, height px)."""
    from PIL import Image, ImageChops
    im = Image.open(SRC / f"shot_{name}.png").convert("RGB")
    bg = Image.new("RGB", im.size, im.getpixel((2, 2)))
    box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 14 else 0).getbbox()
    pad = 24
    im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                  min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), im.height


# ── drawing ──────────────────────────────────────────────────────────────────

def filled(loops, stroke_w):
    d = " ".join("M " + " L ".join(f"{u:.3f} {v:.3f}" for u, v in loop) + " Z" for loop in loops)
    return (f'<path d="{d}" fill="{MATERIAL}" fill-rule="evenodd" stroke="{EDGE}" '
            f'stroke-width="{stroke_w}" stroke-linejoin="round"/>')


def text(x, y, s, size, colour=INK, weight=400, anchor="middle", halo=False):
    h = f' paint-order="stroke" stroke="#fff" stroke-width="{size * 0.32:.3f}"' if halo else ""
    return (f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}" font-size="{size:.3f}" '
            f'font-weight="{weight}" fill="{colour}"{h} {FONT}>{s}</text>')


def dim(x1, y1, x2, y2, w, colour=BLUE):
    return (f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="{colour}" '
            f'stroke-width="{w:.4f}" marker-start="url(#arr)" marker-end="url(#arr)"/>')


def ext(x1, y1, x2, y2, w):
    return (f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="#94a3b8" '
            f'stroke-width="{w:.4f}" stroke-dasharray="{3 * w:.4f} {2 * w:.4f}"/>')


def dashed_circle(r, w):
    return (f'<circle r="{r}" fill="none" stroke="{CALLOUT}" stroke-width="{w:.4f}" '
            f'stroke-dasharray="{4 * w:.4f} {3 * w:.4f}"/>')


def view(x, y, size_px, half, inner, cx=0.0, cy=0.0):
    """A square px box at (x, y) showing drawing mm [cx±half, cy±half]."""
    return (f'<svg x="{x:.1f}" y="{y:.1f}" width="{size_px:.1f}" height="{size_px:.1f}" '
            f'viewBox="{cx - half:.3f} {cy - half:.3f} {2 * half:.3f} {2 * half:.3f}">{inner}</svg>')


def image(x, y, w, h, uri):
    return f'<image x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" href="{uri}"/>'


def page(title, body, width, height, out_name, captions=()):
    caps = "".join(text(width / 2, height - 16 - 21 * (len(captions) - 1 - i), c, 14.5, GREY)
                   for i, c in enumerate(captions))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
<text x="{GAP}" y="38" font-size="25" font-weight="700" fill="{BLUE}">{title}</text>
{body}
{caps}
</svg>"""
    (HELP / out_name).write_text(svg, encoding="utf-8")
    print("wrote static/help/" + out_name, f"{width:.0f} x {height:.0f}")


def caption_h(n):
    return 24 + 21 * n


# ── pictures ─────────────────────────────────────────────────────────────────

def hub_size():
    """3D preview + a section through the axis, Hub Height and Hub OD dimensioned."""
    m = load("none")
    loops = section(m, [0, 0, 0], [0, 1, 0], lambda p: (p[0], -p[2]))
    r_od = float(m.bounds[1][0])
    s = 9.0                                              # px per mm
    w, fs = 1.0 / s, 17 / s
    top = -(TEETH_TOP + HUB_H)
    right = 17.0                                         # mm of room for the labels
    x0, y0 = -r_od - 2, top - 7.5
    vw, vh = 2 * r_od + 2 + right, TEETH_TOP + HUB_H + 9
    body = [filled(loops, 1.3 * w),
            ext(-R_HUB, top, -R_HUB, top - 6, w), ext(R_HUB, top, R_HUB, top - 6, w),
            dim(-R_HUB, top - 5, R_HUB, top - 5, 2 * w),
            text(0, top - 5.8, f"Hub OD {HUB_OD:g} mm", fs, BLUE, 700, halo=True),
            ext(R_HUB, top, r_od + 7, top, w), ext(r_od, -TEETH_TOP, r_od + 7, -TEETH_TOP, w),
            ext(r_od, 0, r_od + 7, 0, w),
            dim(r_od + 5, top, r_od + 5, -TEETH_TOP, 2 * w),
            text(r_od + 6.2, top + HUB_H / 2 - 0.4, "Hub", fs, BLUE, 700, anchor="start"),
            text(r_od + 6.2, top + HUB_H / 2 + 1.6, "Height", fs, BLUE, 700, anchor="start"),
            text(r_od + 6.2, top + HUB_H / 2 + 3.6, f"{HUB_H:g} mm", fs, BLUE, 700, anchor="start"),
            dim(r_od + 5, -TEETH_TOP, r_od + 5, 0, 1.4 * w, GREY),
            text(r_od + 6.2, -TEETH_TOP / 2 + 0.6, "teeth", fs * 0.85, GREY, 700, anchor="start"),
            text(0, -TEETH_TOP / 2 - 0.3, "bore", fs * 0.9, GREY, 700),
            text(0, -TEETH_TOP / 2 + 1.7, f"Ø {BORE:g}", fs * 0.9, GREY)]
    sec_w, sec_h = vw * s, vh * s
    snap_w = 300
    uri, snap_h = snapshot("none", snap_w)
    y = TOP + 34
    sx = GAP + snap_w + 36
    h = max(sec_h, snap_h)
    parts = [text(GAP + snap_w / 2, TOP + 14, "3D preview", 18, weight=700),
             image(GAP, y + (h - snap_h) / 2, snap_w, snap_h, uri),
             text(sx + sec_w / 2, TOP + 14, "Cut through the axis", 18, weight=700),
             f'<svg x="{sx}" y="{y + (h - sec_h) / 2:.0f}" width="{sec_w:.0f}" height="{sec_h:.0f}" '
             f'viewBox="{x0} {y0} {vw} {vh}">{"".join(body)}</svg>']
    caps = ["The hub is the boss on top of the teeth; the bore runs through both.",
            "Hub OD can be smaller or larger than the teeth."]
    page("Hub Height and Hub OD", "".join(parts), sx + sec_w + GAP, y + h + caption_h(len(caps)),
         "hub_size.svg", caps)


def _columns(title, cols, out_name, caps, col_w=176, snaps=True, half=None, extra=None,
             row_label=None):
    """Side-by-side panels: optional 3D snapshot above a slice at screw height.
    cols: [(stl name, heading, note)]. extra(name) -> SVG in slice mm (dimensions)."""
    slices = {name: slice_at_screw(name) for name, _, _ in cols}
    if half is None:
        half = max(abs(c) for loops in slices.values() for loop in loops for pt in loop
                   for c in pt) + 1.0
    s = col_w / (2 * half)
    w = 1.0 / s
    x_lab = GAP + (130 if row_label else 0)
    y_head = TOP + 18
    y_snap = y_head + 16
    snap = {n: snapshot(n, col_w) for n, _, _ in cols} if snaps else {}
    snap_h = max((h for _, h in snap.values()), default=0)
    y_cut = y_snap + snap_h + (18 if snaps else 0)
    parts = []
    for i, (name, head, note) in enumerate(cols):
        x = x_lab + i * (col_w + GAP)
        parts.append(text(x + col_w / 2, y_head, head, 18, weight=700))
        if snaps:
            uri, h = snap[name]
            parts.append(image(x, y_snap + (snap_h - h) / 2, col_w, h, uri))
        inner = filled(slices[name], 1.4 * w) + (extra(name, w, s) if extra else "")
        parts.append(view(x, y_cut, col_w, half, inner))
        if note:
            parts.append(text(x + col_w / 2, y_cut + col_w + 22, note, 15, GREY))
    if row_label:
        if snaps:
            parts.append(text(GAP, y_snap + snap_h / 2, "3D preview", 15, GREY, 700, anchor="start"))
        parts.append(text(GAP, y_cut + col_w / 2 - 8, row_label[0], 15, GREY, 700, anchor="start"))
        parts.append(text(GAP, y_cut + col_w / 2 + 11, row_label[1], 15, GREY, 700, anchor="start"))
    width = x_lab + len(cols) * (col_w + GAP)
    height = y_cut + col_w + 34 + caption_h(len(caps))
    page(title, "".join(parts), width, height, out_name, caps)


def hub_retention():
    _columns("Retention Method",
             [("none", "None", "plain round bore"),
              ("screw_std", "Set Screw (std.)", "holes to the bore"),
              ("screw_nut", "Captured Nut", "nut pocket + screw"),
              ("dshaft", "D-Shaft", "flat in the bore"),
              ("keyway", "Keyway", "slot for a key")],
             "hub_retention.svg",
             ["Top: the 3D preview. Bottom: the hub cut across at the set-screw height.",
              "Holes and slots point right (0°) and up (90°); M5 screws, 12 mm bore."],
             row_label=("cut at", "screw height"))


def hub_screw_size():
    """Hole size (and nut pocket) for M3 / M5 / M8, standard and captured nut."""
    sizes = [3, 5, 8]
    slices = {f"size_{k}_{d}": slice_at_screw(f"size_{k}_{d}") for k in ("std", "nut") for d in sizes}
    # one scale per row (the captured nut's lobe grows a lot with M8)
    row_half = {k: max(abs(c) for d in sizes for loop in slices[f"size_{k}_{d}"] for pt in loop
                       for c in pt) + 1.0 for k in ("std", "nut")}

    def hole_dim(name, w, s):
        d = int(name.rsplit("_", 1)[1])
        outer = max(x for loop in slices[name] for x, y in loop if abs(y) <= d / 2 + 0.3)
        xm = (R_BORE + R_HUB) / 2 if "_std_" in name else outer - 1.2
        return (dim(xm, d / 2, xm, -d / 2, 2 * w) +
                text(xm - 0.5, -d / 2 - 0.9, f"Ø {d}", 16 / s, BLUE, 700, anchor="end", halo=True))

    col_w = 190
    rows = [("std", "Set Screw (std.)"), ("nut", "Captured Nut")]
    parts, y = [], TOP + 20
    x_lab = GAP + 150
    for key, label in rows:
        parts.append(text(GAP, y + 26 + col_w / 2, label, 16, INK, 700, anchor="start"))
        for i, d in enumerate(sizes):
            x = x_lab + i * (col_w + GAP)
            name = f"size_{key}_{d}"
            if key == "std":
                parts.append(text(x + col_w / 2, y + 8, f"M{d}", 20, weight=700))
            half = row_half[key]
            inner = filled(slices[name], 1.4 / (col_w / (2 * half)))
            inner += hole_dim(name, 1 / (col_w / (2 * half)), col_w / (2 * half))
            parts.append(view(x, y + 20, col_w, half, inner))
        y += col_w + 30
    caps = ["The hole is the screw's size; with a captured nut the nut pocket grows with it.",
            "Hub cut across at the set-screw height; screws point right (0°)."]
    width = x_lab + len(sizes) * (col_w + GAP)
    page("Set Screw Size", "".join(parts), width, y + caption_h(len(caps)), "hub_screw_size.svg", caps)


def hub_screw_count():
    """1 vs 2 screws, one picture per method (the hover picks by Retention Method)."""
    for key, method, angles in (("std", "Set Screw (std.)", "90°"), ("nut", "Captured Nut", "180°")):
        _columns(f"Number of Screws — {method}",
                 [(f"count_{key}_1", "1 screw", "at 0° (right)"),
                  (f"count_{key}_2", "2 screws", f"at 0° and {angles}")],
                 f"hub_screw_count_{key}.svg",
                 ["Top: the 3D preview. Bottom: the hub cut across at the set-screw height.",
                  "Standard screws are 90° apart; captured nuts 180° apart." if key == "std"
                  else "Captured nuts are 180° apart (standard screws 90°)."],
                 col_w=250, row_label=("cut at", "screw height"))


def hub_flat_depth():
    """D-shaft flat for three depths, measured in from the bore wall."""
    depths = ["0.5", "1", "2"]
    half = R_BORE + 2.5

    def flat_dim(name, w, s):
        f = float(name.split("_")[1])
        xf = R_BORE - f
        return (dashed_circle(R_BORE, 1.6 * w) +
                dim(xf, 0, R_BORE, 0, 2 * w) +
                text((xf + R_BORE) / 2, -1.0, f"{f:g} mm", 17 / s, BLUE, 700, halo=True))

    _columns("Flat Depth (D-Shaft)",
             [(f"flat_{d}", f"Flat Depth {d} mm", None) for d in depths],
             "hub_flat_depth.svg",
             ["Measured from the round bore (dashed) in to the flat. Measure your shaft:",
              "flat depth = shaft diameter − the distance across the flat."],
             col_w=220, snaps=False, half=half, extra=flat_dim)


def hub_keyway():
    """Keyway width W and hub depth, three of each, measured from the bore wall."""
    half = R_BORE + 6.0

    def w_dim(name, w, s):
        kw = float(name.split("_")[2])
        xd = R_BORE + 2.6 + 1.6                        # just outside the slot's outer face
        return (dashed_circle(R_BORE, 1.6 * w) +
                dim(xd, -kw / 2, xd, kw / 2, 2 * w) +
                text(xd, -kw / 2 - 1.1, f"W {kw:g}", 17 / s, BLUE, 700, halo=True))

    def h_dim(name, w, s):
        h = float(name.split("_")[2])
        return (dashed_circle(R_BORE, 1.6 * w) +
                dim(R_BORE, 3.2, R_BORE + h, 3.2, 2 * w) +
                text(R_BORE + h / 2, 5.6, f"{h:g} mm", 17 / s, BLUE, 700, halo=True))

    _columns("Keyway Width W",
             [(f"kw_w_{w}", f"W = {w} mm", None) for w in ("3", "4", "6")],
             "hub_keyway_w.svg",
             ["The slot's width — the key's width b (ISO 773). Default Key fills it in",
              "for your bore. Hub Depth 2.6 mm here; 12 mm bore."],
             col_w=220, snaps=False, half=half, extra=w_dim)
    _columns("Keyway Hub Depth",
             [(f"kw_h_{h}", f"Hub Depth {h} mm", None) for h in ("1.5", "2.6", "4")],
             "hub_keyway_h.svg",
             ["How far the slot goes out from the round bore (dashed): ISO 773 t2 + r.",
              "Default Key fills it in for your bore. Width 4 mm here; 12 mm bore."],
             col_w=220, snaps=False, half=half, extra=h_dim)


if __name__ == "__main__":
    hub_size()
    hub_retention()
    hub_screw_size()
    hub_screw_count()
    hub_flat_depth()
    hub_keyway()
