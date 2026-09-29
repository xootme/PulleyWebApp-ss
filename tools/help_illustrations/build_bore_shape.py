"""Build the Bore Shape illustrations from the app's own SVG downloads.

The same pulley (HTD 5M, 40 teeth) with each bore shape, the bore shaded and
cropped close; then the two spline kinds dimensioned. The drawings are
fetched fresh through the app's test client each run:

  /download/svg?family=HTD&pitch=5M&teeth=40&include_data=0&print_extra=0
    &clearance_preset=STANDARD&backlash_preset=STANDARD&<shape>
  round      bore=23
  flat       bore=23&hub_flat_depth=2
  key        bore=23&hub_keyway_w=6&hub_keyway_h=3.3        (ISO 773's key for a 23 mm shaft)
  straight   bore_shape=spline&spline_type=straight&spline_n=6&spline_minor=23
             &spline_major=26&spline_width=6                (ISO 14 light 6 x 23 x 26)
  involute   bore_shape=spline&spline_type=involute&spline_m=1.5&spline_z=16
             &spline_pa=30&spline_root=flat                 (ISO 4156 m 1.5 x 16T, 30°)

Output: static/help/bore_shape.svg, spline_straight.svg, spline_involute.svg
(hover pictures on Bore Shape and the spline fields, and the Pulley help pages).
"""
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
HELP = ROOT / "static" / "help"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
BLUE = "#0078d4"
CALLOUT = "#d9480f"
GREY = "#475569"
SHADE = "#ffffff"
MATERIAL = "#dbe4ee"     # the pulley, as the app's preview draws it
GAP = 30
TOP = 70
PAGE_W = 680              # the spline pages: room for the captions

BASE = dict(family="HTD", pitch="5M", teeth="40", include_data="0", print_extra="0",
            clearance_preset="STANDARD", backlash_preset="STANDARD")
SOURCES = {
    "round": dict(bore="23"),
    "flat": dict(bore="23", hub_flat_depth="2"),
    "key": dict(bore="23", hub_keyway_w="6", hub_keyway_h="3.3"),
    "straight": dict(bore_shape="spline", spline_type="straight", spline_n="6", spline_minor="23",
                     spline_major="26", spline_width="6"),
    "involute": dict(bore_shape="spline", spline_type="involute", spline_m="1.5", spline_z="16",
                     spline_pa="30", spline_root="flat"),
}


def fetch():
    """Each source's SVG download, through the app's test client."""
    os.environ.setdefault("QUEUE_DISABLED", "1")
    sys.path.insert(0, str(ROOT))
    from app import app
    c = app.test_client()
    out = {}
    for name, extra in SOURCES.items():
        r = c.get("/download/svg", query_string={**BASE, **extra})
        assert r.status_code == 200, (name, r.status_code, r.data[:200])
        out[name] = r.data.decode("utf-8")
    return out


def shapes(svg_text):
    """The drawing's shapes: [pulley body, bore]."""
    found = re.findall(r"<(?:path|circle)\b[^>]*/>", svg_text, flags=re.S)
    assert len(found) >= 2, len(found)
    return found[0], found[1]


def shaded(bore_el):
    """The bore element again, filled, to sit under the outline."""
    return re.sub(r'fill="none"', f'fill="{SHADE}"', bore_el, count=1)


def text(x, y, s, size, colour="#1e293b", weight=400, anchor="middle", halo=False):
    h = f' paint-order="stroke" stroke="#fff" stroke-width="{size * 0.3:.3f}"' if halo else ""
    return (f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}" font-size="{size:.3f}" '
            f'font-weight="{weight}" fill="{colour}"{h} {FONT}>{s}</text>')


def circle(r, w, colour=CALLOUT):
    return (f'<circle r="{r:.4f}" fill="none" stroke="{colour}" stroke-width="{w:.4f}" '
            f'stroke-dasharray="{4 * w:.4f} {3 * w:.4f}"/>')


def dim(x1, y1, x2, y2, w, colour=BLUE):
    return (f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" stroke="{colour}" '
            f'stroke-width="{w:.4f}" marker-start="url(#arr)" marker-end="url(#arr)"/>')


def panel(x, y, px, half, svg_text, extra=""):
    """A px-square view of the drawing, mm [-half, half] around the bore."""
    body, bore = shapes(svg_text)
    # the crop sits inside the teeth: fill the pulley's material behind the hole
    material = re.sub(r'fill="[^"]*"', f'fill="{MATERIAL}"', body, count=1)
    return (f'<svg x="{x}" y="{y}" width="{px}" height="{px}" viewBox="{-half} {-half} {2 * half} {2 * half}">'
            f'{material}{shaded(bore)}<g style="fill:none">{bore}</g>{extra}</svg>')


def page(title, body, width, height, out_name, captions):
    caps = "".join(text(width / 2, height - 16 - 20 * (len(captions) - 1 - i), c, 14, GREY)
                   for i, c in enumerate(captions))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width}" height="{height}" fill="#fff"/>
<text x="{GAP}" y="36" font-size="24" font-weight="700" fill="{BLUE}">{title}</text>
{body}
{caps}
</svg>"""
    (HELP / out_name).write_text(svg, encoding="utf-8")
    print("wrote static/help/" + out_name, f"{width} x {height}")


def bore_shape(src):
    """Round / D-flat / Keyway / Spline, side by side."""
    px, half = 200, 17.0
    cols = [("round", "Round", "plain bore"), ("flat", "D-flat", "flat for a D-shaft"),
            ("key", "Keyway", "slot for a key"), ("straight", "Spline", "for a splined shaft")]
    parts = []
    for i, (name, head, note) in enumerate(cols):
        x = GAP + i * (px + GAP)
        parts.append(text(x + px / 2, TOP + 6, head, 18, weight=700))
        parts.append(panel(x, TOP + 18, px, half, src[name]))
        parts.append(text(x + px / 2, TOP + 18 + px + 22, note, 14, GREY))
    width = GAP + len(cols) * (px + GAP)
    caps = ["Bore Shape (Size card) sets the hole every download cuts — 2D and 3D alike.",
            "A spline replaces the round bore; Bore Diameter is then its minor diameter."]
    page("Bore Shape", "".join(parts), width, TOP + 18 + px + 40 + 20 * len(caps) + 18,
         "bore_shape.svg", caps)


def spline_straight(src):
    """ISO 14: N slots, minor d, major D, slot width B."""
    n, d, D, B = 6, 23.0, 26.0, 6.0
    px, half = 460, 19.0
    s = px / (2 * half)
    w, fs = 1.0 / s, 15 / s
    extra = [circle(d / 2, 1.2 * w), circle(D / 2, 1.2 * w, BLUE),
             # d across the bore, under the slot at 0°... drawn at -60° between slots
             dim(-d / 2 * math.cos(math.radians(30)), d / 2 * math.sin(math.radians(30)),
                 d / 2 * math.cos(math.radians(30)), -d / 2 * math.sin(math.radians(30)), 1.6 * w, CALLOUT),
             text(0, 1.6, f"minor d = {d:g}", fs, CALLOUT, 700, halo=True),
             # D: across the slots at 60° and 240° (y flipped: -y up)
             dim(-D / 2 * math.cos(math.radians(60)), D / 2 * math.sin(math.radians(60)),
                 D / 2 * math.cos(math.radians(60)), -D / 2 * math.sin(math.radians(60)), 1.4 * w),
             text(0, -D / 2 - 1.8, f"major D = {D:g} (slot bottoms)", fs, BLUE, 700, halo=True),
             # B: across the slot on +x
             dim(D / 2 + 1.2, -B / 2, D / 2 + 1.2, B / 2, 1.6 * w),
             text(D / 2 + 1.6, 0.5, f"B = {B:g}", fs, BLUE, 700, anchor="start", halo=True),
             text(0, D / 2 + 2.4, f"N = {n} slots", fs, "#1e293b", 700, halo=True)]
    parts = [panel((PAGE_W - px) / 2, TOP, px, half, src["straight"], "".join(extra))]
    caps = ["ISO 14 straight-sided spline, light series 6 × 23 × 26 (B 6): the hub centres on the",
            "minor diameter d; the shaft's N splines slide in the slots, B wide, out to the major D.",
            "Pick a size from ISO 14's light or medium series, or type N, d, D and B."]
    page("Straight-sided spline (ISO 14)", "".join(parts), PAGE_W,
         TOP + px + 20 * len(caps) + 30, "spline_straight.svg", caps)


def spline_involute(src):
    """ISO 4156: module m, teeth z, pressure angle; minor, pitch and major circles."""
    from cct_common import splines as spl
    m, z = 1.5, 16
    hole = spl.hole(spl.involute(m, z, 30, "flat"))       # ISO 4156-1's figures, as the app cuts
    minor, major, pd = hole.inner, hole.outer, m * z
    px, half = 460, 17.5
    s = px / (2 * half)
    w, fs = 1.0 / s, 15 / s
    extra = [circle(minor / 2, 1.2 * w), circle(pd / 2, 1.0 * w, GREY), circle(major / 2, 1.2 * w, BLUE),
             text(0, -1.0, f"minor {minor:.2f} (form Ø + 0.2m)", fs, CALLOUT, 700, halo=True),
             text(0, 1.2, f"pitch Ø {pd:g} = m·z", fs, GREY, 700, halo=True),
             text(0, 3.4, f"major {major:g} = m(z + 1.5)", fs, BLUE, 700, halo=True),
             text(0, -major / 2 - 1.2, f"m {m:g} × {z} teeth, 30° flat root", fs, "#1e293b", 700, halo=True)]
    parts = [panel((PAGE_W - px) / 2, TOP, px, half, src["involute"], "".join(extra))]
    caps = ["ISO 4156 involute spline: module m, z teeth and a 30°, 37.5° or 45° pressure angle.",
            "Diameters from ISO 4156-1:2005; the fit is H/h, tolerance class 6, on the space width.",
            "The flanks are true involutes, drawn as arcs within 0.001 mm."]
    page("Involute spline (ISO 4156)", "".join(parts), PAGE_W,
         TOP + px + 20 * len(caps) + 30, "spline_involute.svg", caps)


def main():
    src = fetch()
    bore_shape(src)
    spline_straight(src)
    spline_involute(src)


if __name__ == "__main__":
    main()
