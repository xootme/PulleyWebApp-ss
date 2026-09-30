"""Build the retaining-ring illustration (ADR-017): a section through a
splined pulley on its sample shaft, cut through a spline slot, with the DIN 471
ring in its counterbore and the splined washer under it.

Every figure comes from the app (/api/spline, the same cct_common code the
downloads use) for ISO 14 light 6 x 23 x 26 with a ring on the top face and
the washer; the pulley is HTD 5M 40T, 11 mm face, with a 44 x 12 mm hub.
Radial sizes are drawn to scale; the groove (0.4 mm deep across the tooth
tops) is to scale too, so it is small — the callout says so.

Output: static/help/spline_ring.svg (hover picture on Retaining Ring and
Splined washer, and the Pulley help pages), and static/help/spline_counterbore.svg
(Make counterbore: the same section with it on and off, side by side — Sprocket
Designer's picture of the same choice, drawn from this app's output).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from build_bore_shape import BLUE, CALLOUT, FONT, GREY, HELP, MATERIAL, text  # noqa: E402

SHAFT = "#94a3b8"
WASHER = "#7fb3e6"
RING = "#1e293b"
QUERY = dict(bore="8", bore_shape="spline", spline_type="straight", spline_n="6", spline_minor="23",
             spline_major="26", spline_width="6", spline_ring="top", spline_washer="1")
FACE, HUB_OD, HUB_H, BODY_R = 11.0, 44.0, 12.0, 27.0     # mm: the pulley around the bore


def fetch(**extra):
    os.environ.setdefault("QUEUE_DISABLED", "1")
    sys.path.insert(0, str(ROOT))
    from app import app
    from exporters.spline_parts import TAIL
    r = app.test_client().get("/api/spline", query_string={**QUERY, **extra})
    assert r.status_code == 200, r.data[:200]
    return r.get_json(), TAIL


def main():
    fit, tail = fetch()
    rt = fit["retainer"]
    s = 9.5                                    # px per mm
    H = FACE + HUB_H                           # the part, z 0..H
    z_lo, z_hi = -tail, H + rt["n"]            # the shaft
    W, top = 1000, 80
    cx = 330                                   # the axis, px
    y0 = top + 40 + z_hi * s                   # z = 0, px (y down)
    X = lambda r: cx + r * s                   # noqa: E731
    Y = lambda z: y0 - z * s                   # noqa: E731

    def rect(r0, r1, z0, z1, fill, stroke="#334155", sw=1.0):
        x0, x1 = sorted((X(r0), X(r1)))
        y1, y2 = sorted((Y(z0), Y(z1)))
        return (f'<rect x="{x0:.2f}" y="{y1:.2f}" width="{x1 - x0:.2f}" height="{y2 - y1:.2f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    hole = fit["hole_major"] / 2               # the section is through a slot
    cb_r, cb_z = rt["cb_d"] / 2, H - rt["cb_depth"]
    sh = fit["shaft_major"] / 2                # the shaft's tooth in that slot
    g0, g1 = H - rt["m"], H                    # the groove: outer wall at the face
    washer_top = cb_z + rt["washer_t"]
    ring_out = rt["d1"] / 2 + rt["a"]           # the lugs: DIN 471's a past d1
    parts = []
    for sign in (-1, 1):
        # the pulley: the body and the hub, less the bore and the counterbore
        pts = [(hole, 0), (BODY_R, 0), (BODY_R, FACE), (HUB_OD / 2, FACE), (HUB_OD / 2, H),
               (cb_r, H), (cb_r, cb_z), (hole, cb_z)]
        d = "M " + " L ".join(f"{X(sign * r):.2f} {Y(z):.2f}" for r, z in pts) + " Z"
        parts.append(f'<path d="{d}" fill="{MATERIAL}" stroke="#334155" stroke-width="1.2"/>')
        # the washer on the counterbore floor, the ring on the washer, in the groove
        parts.append(rect(sign * (fit["hole_major"] / 2 + 0.05), sign * rt["washer_od"] / 2, cb_z, washer_top, WASHER))
        parts.append(rect(sign * rt["d2"] / 2, sign * ring_out, washer_top, washer_top + rt["s"], RING, RING))
    # the shaft: through a slot its tooth reaches the shaft major, less the groove
    spts = [(-sh, z_lo), (sh, z_lo), (sh, g0), (rt["d2"] / 2, g0), (rt["d2"] / 2, g1), (sh, g1), (sh, z_hi),
            (-sh, z_hi), (-sh, g1), (-rt["d2"] / 2, g1), (-rt["d2"] / 2, g0), (-sh, g0)]
    d = "M " + " L ".join(f"{X(r):.2f} {Y(z):.2f}" for r, z in spts) + " Z"
    parts.insert(0, f'<path d="{d}" fill="{SHAFT}" stroke="#334155" stroke-width="1.2"/>')
    parts.append(f'<line x1="{cx}" y1="{Y(z_lo) + 10:.2f}" x2="{cx}" y2="{Y(z_hi) - 14:.2f}" stroke="{GREY}" '
                 f'stroke-width="1" stroke-dasharray="10 3 2 3"/>')

    def dim_h(r0, r1, z, label, colour=BLUE, above=True):
        y = Y(z)
        return (f'<line x1="{X(r0):.2f}" y1="{y:.2f}" x2="{X(r1):.2f}" y2="{y:.2f}" stroke="{colour}" '
                f'stroke-width="1.4" marker-start="url(#arr)" marker-end="url(#arr)"/>'
                + text((X(r0) + X(r1)) / 2, y + (-7 if above else 17), label, 14, colour, 700, halo=True))

    def dim_v(r, z0, z1, label, colour=BLUE):
        x = X(r)
        return (f'<line x1="{x:.2f}" y1="{Y(z0):.2f}" x2="{x:.2f}" y2="{Y(z1):.2f}" stroke="{colour}" '
                f'stroke-width="1.4" marker-start="url(#arr)" marker-end="url(#arr)"/>'
                + text(x + 8, (Y(z0) + Y(z1)) / 2 + 5, label, 14, colour, 700, anchor="start", halo=True))

    parts.append(dim_h(-cb_r, cb_r, H + 4.8, f"counterbore Ø{rt['cb_d']:g} (DIN 471 d4)"))
    # the depth beside the hub, extension lines from the face and the floor
    xd = -HUB_OD / 2 - 1.2
    for z in (cb_z, H):
        parts.append(f'<line x1="{X(-cb_r) - 3:.2f}" y1="{Y(z):.2f}" x2="{X(xd) - 5:.2f}" y2="{Y(z):.2f}" '
                     f'stroke="{BLUE}" stroke-width="0.8"/>')
    parts.append(dim_v(xd, cb_z, H, "", BLUE))
    parts.append(text(X(xd) - 6, (Y(cb_z) + Y(H)) / 2 + 5, f"{rt['cb_depth']:g} deep", 14, BLUE, 700,
                      anchor="end", halo=True))
    parts.append(dim_v(-sh - 0.2, H, z_hi, "", CALLOUT))
    parts.append(text(X(-sh - 0.8), (Y(H) + Y(z_hi)) / 2 + 5, f"n {rt['n']:g}", 14, CALLOUT, 700,
                      anchor="end", halo=True))

    # callouts on the right, top to bottom, with leaders
    lx = X(BODY_R) + 40
    notes = [
        (ring_out - 0.8, washer_top + rt["s"] / 2, Y(H) - 4, f"retaining ring {rt['ring']}",
         f"McMaster {rt['mcmaster']}, in a groove {rt['m']:g} wide to Ø{rt['d2']:g} (DIN d2)"),
        (rt["washer_od"] / 2 - 0.8, cb_z + rt["washer_t"] / 2, Y(H) + 50,
         f"splined washer Ø{rt['washer_od']:g} × {rt['washer_t']:g}",
         "keys to the shaft, so the ring rubs it, not the pulley"),
        (BODY_R - 3, FACE / 2, Y(FACE / 2) + 5, "pulley, cut through a spline slot",
         f"hole {fit['bore']:.3f} × {fit['hole_major']:.3f} mm"),
        (sh - 1.5, -tail / 2, Y(-tail / 2) + 22, f"sample shaft {fit['shaft_minor']:.3f} × {fit['shaft_major']:.3f} mm",
         f"{tail:g} mm past the other face, {rt['n']:g} (DIN n) past the groove"),
    ]
    for r, z, ty, head, sub in notes:
        parts.append(f'<path d="M {X(r):.2f} {Y(z):.2f} L {lx - 6:.2f} {ty - 5:.2f}" fill="none" '
                     f'stroke="{GREY}" stroke-width="1"/><circle cx="{X(r):.2f}" cy="{Y(z):.2f}" r="2.5" fill="{GREY}"/>')
        parts.append(text(lx, ty, head, 15, "#1e293b", 700, anchor="start"))
        parts.append(text(lx, ty + 18, sub, 13, GREY, anchor="start"))

    height = int(Y(z_lo) + 40 + 3 * 20 + 20)
    caps = [f"{fit['fit']}: the hole and shaft each at the middle of its tolerance zone.",
            "The ring's counterbore is DIN 471's d4 wide and the groove's width deep (plus the washer), so the",
            "ring sits flush. STL parts add the print compensation: the bore grows, the shaft shrinks."]
    capy = [height - 16 - 20 * (len(caps) - 1 - i) for i in range(len(caps))]
    body = "".join(parts) + "".join(text(W / 2, y, c, 14, GREY) for y, c in zip(capy, caps))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}"
     viewBox="0 0 {W} {height}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="context-stroke"/>
  </marker>
</defs>
<rect width="{W}" height="{height}" fill="#fff"/>
<text x="30" y="36" font-size="24" font-weight="700" fill="{BLUE}">Splined bore: retaining ring and washer</text>
{body}
</svg>"""
    (HELP / "spline_ring.svg").write_text(svg, encoding="utf-8")
    print("wrote static/help/spline_ring.svg", W, "x", height)


def _panel(fit, tail, x_left, top, s, title, z_top):
    """One section (as main()'s) of the top face, placed from the retainer's
    stack: the washer, ring and groove in the counterbore, or on the face.
    z_top: the highest shaft end of the panels, so their faces line up."""
    rt = fit["retainer"]
    st = rt["stack"]["top"]
    H = FACE + HUB_H
    z_lo, z_hi = -tail, H + st["shaft_end"]
    half = BODY_R * s
    cx = x_left + half + 10
    y0 = top + 46 + z_top * s
    X = lambda r: cx + r * s                   # noqa: E731
    Y = lambda z: y0 - z * s                   # noqa: E731

    def rect(r0, r1, z0, z1, fill, stroke="#334155"):
        x0, x1 = sorted((X(r0), X(r1)))
        y1, y2 = sorted((Y(z0), Y(z1)))
        return (f'<rect x="{x0:.2f}" y="{y1:.2f}" width="{x1 - x0:.2f}" height="{y2 - y1:.2f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>')

    hole, sh = fit["hole_major"] / 2, fit["shaft_major"] / 2
    cb = "top" in rt["cb_faces"]
    cb_r, cb_z = rt["cb_d"] / 2, H - rt["cb_depth"]
    ring_out = rt["d1"] / 2 + rt["a"]
    parts = [text(cx, top + 22, title, 17, BLUE, 700)]
    g0, g1 = (H + z for z in st["groove"])
    spts = [(-sh, z_lo), (sh, z_lo), (sh, g0), (rt["d2"] / 2, g0), (rt["d2"] / 2, g1), (sh, g1), (sh, z_hi),
            (-sh, z_hi), (-sh, g1), (-rt["d2"] / 2, g1), (-rt["d2"] / 2, g0), (-sh, g0)]
    parts.append('<path d="M ' + " L ".join(f"{X(r):.2f} {Y(z):.2f}" for r, z in spts)
                 + f' Z" fill="{SHAFT}" stroke="#334155" stroke-width="1.2"/>')
    for sign in (-1, 1):
        pts = [(hole, 0), (BODY_R, 0), (BODY_R, FACE), (HUB_OD / 2, FACE), (HUB_OD / 2, H)]
        pts += [(cb_r, H), (cb_r, cb_z), (hole, cb_z)] if cb else [(hole, H)]
        parts.append('<path d="M ' + " L ".join(f"{X(sign * r):.2f} {Y(z):.2f}" for r, z in pts)
                     + f' Z" fill="{MATERIAL}" stroke="#334155" stroke-width="1.2"/>')
        if st["washer"]:
            w0, w1 = (H + z for z in st["washer"])
            parts.append(rect(sign * (hole + 0.05), sign * rt["washer_od"] / 2, w0, w1, WASHER))
        r0, r1 = (H + z for z in st["ring"])
        parts.append(rect(sign * rt["d2"] / 2, sign * ring_out, r0, r1, RING, RING))
    parts.append(f'<line x1="{cx}" y1="{Y(z_lo) + 8:.2f}" x2="{cx}" y2="{Y(z_hi) - 10:.2f}" stroke="{GREY}" '
                 f'stroke-width="1" stroke-dasharray="10 3 2 3"/>')
    # what changes, under the section
    if cb:
        notes = (f"counterbore Ø{rt['cb_d']:g} × {rt['cb_depth']:g} deep", "the ring sits flush")
    else:
        notes = ("nothing cut", f"washer and ring stand {st['ring'][1]:g} mm proud")
    for i, note in enumerate(notes):
        parts.append(text(cx, Y(z_lo) + 26 + 19 * i, note, 14, "#1e293b", 700 if i == 0 else 400))
    return "".join(parts), 2 * half + 20, Y(z_lo) + 56 - top


def build_counterbore():
    """Make counterbore, on and off (as Sprocket Designer shows it): the same
    pulley, ring and washer, from the app's own /api/spline answers."""
    s, top, gap = 5.6, 70, 40
    panels, x, h_max = [], 30, 0.0
    fits = [(fetch(spline_cb_top=cb), title)
            for cb, title in (("1", "Make counterbore: on"), ("0", "Make counterbore: off"))]
    z_top = max(FACE + HUB_H + f["retainer"]["stack"]["top"]["shaft_end"] for (f, _), _ in fits)
    for (fit, tail), title in fits:
        xml, w, h = _panel(fit, tail, x, top, s, title, z_top)
        panels.append(xml)
        x += w + gap
        h_max = max(h_max, h)
    W = int(x - gap + 30)
    caps = ["On: the face is recessed so the ring sits flush (the hub needs room round it). Off: nothing",
            "is cut; the ring and washer sit on the face, and the shaft's groove moves out with them."]
    height = int(top + h_max + 20 + 20 * len(caps) + 10)
    capy = [height - 16 - 20 * (len(caps) - 1 - i) for i in range(len(caps))]
    body = "".join(panels) + "".join(text(W / 2, y, c, 14, GREY) for y, c in zip(capy, caps))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}"
     viewBox="0 0 {W} {height}" {FONT}>
<rect width="{W}" height="{height}" fill="#fff"/>
<text x="30" y="36" font-size="24" font-weight="700" fill="{BLUE}">Spline: Make counterbore</text>
{body}
</svg>"""
    (HELP / "spline_counterbore.svg").write_text(svg, encoding="utf-8")
    print("wrote static/help/spline_counterbore.svg", W, "x", height)


if __name__ == "__main__":
    main()
    build_counterbore()
