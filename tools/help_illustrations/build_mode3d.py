"""Build the 3D Mode and Belt/Clearance Height illustrations.

mode3d.svg: the preview areas cropped from the two sample screenshots in
static/ ("2d sample view v0_1.png", "3d sample view v0_1.png" — an older
version of the app, but the previews are what 3D Mode off/on still show),
embedded as JPEG so the picture is one self-contained file.

heights.svg: a side-view diagram (drawn here, not taken from the app) of how
the pulley's height is made up: Belt Height + Clearance Height, the belt
centred so each face gets half the clearance (app.py adds clearance_height to
belt_height; step_exporter centres the belt by clearance/2).
Output: static/help/mode3d.svg, static/help/heights.svg.
"""
import base64
import io
from pathlib import Path

from PIL import Image

from build_spoke_count import BLUE, CALLOUT

HERE = Path(__file__).parent
STATIC = HERE.parent.parent / "static"
HELP = STATIC / "help"
FONT = 'font-family="system-ui,Segoe UI,sans-serif"'
GAP = 30


def crop_b64(name, box_frac, width):
    """Crop a fraction box (x0, y0, x1, y1) of a screenshot, scale to width px,
    return (data URI, height)."""
    im = Image.open(STATIC / name).convert("RGB")
    w, h = im.size
    x0, y0, x1, y1 = box_frac
    im = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), im.height


def svg_page(width, height, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}"
     viewBox="0 0 {width:.0f} {height:.0f}" {FONT}>
<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
          orient="auto-start-reverse" markerUnits="strokeWidth">
    <path d="M0,1 L9,5 L0,9 z" fill="{BLUE}"/>
  </marker>
</defs>
<rect width="{width:.0f}" height="{height:.0f}" fill="#fff"/>
{body}
</svg>"""


def mode3d():
    pw = 380
    # the preview areas only (no old UI), as fractions of each screenshot
    off, h_off = crop_b64("2d sample view v0_1.png", (0.135, 0.155, 0.87, 0.94), pw)
    on, h_on = crop_b64("3d sample view v0_1.png", (0.285, 0.36, 0.79, 0.965), pw)
    top = 84
    h = max(h_off, h_on)
    x2 = GAP + pw + GAP
    body = f"""
<text x="{GAP}" y="34" font-size="22" font-weight="700" fill="{BLUE}">3D Mode</text>
<text x="{GAP + pw / 2}" y="{top - 12}" text-anchor="middle" font-size="16" font-weight="700" fill="#1e293b">Off: flat 2D drawings</text>
<text x="{x2 + pw / 2}" y="{top - 12}" text-anchor="middle" font-size="16" font-weight="700" fill="#1e293b">On: 3D parts</text>
<image x="{GAP}" y="{top + (h - h_off) / 2}" width="{pw}" height="{h_off}" href="{off}"/>
<image x="{x2}" y="{top + (h - h_on) / 2}" width="{pw}" height="{h_on}" href="{on}"/>
<rect x="{GAP}" y="{top}" width="{pw}" height="{h}" fill="none" stroke="#cbd5e1"/>
<rect x="{x2}" y="{top}" width="{pw}" height="{h}" fill="none" stroke="#cbd5e1"/>
<text x="{GAP + pw / 2}" y="{top + h + 22}" text-anchor="middle" font-size="13" fill="#475569">SVG and DXF — for laser and waterjet cutting</text>
<text x="{x2 + pw / 2}" y="{top + h + 22}" text-anchor="middle" font-size="13" fill="#475569">STL and STEP — hubs, flanges, spokes; for 3D printing</text>
"""
    width = x2 + pw + GAP
    (HELP / "mode3d.svg").write_text(svg_page(width, top + h + 44, body), encoding="utf-8")
    print("wrote static/help/mode3d.svg", f"{width} x {top + h + 44}")


def heights():
    belt, clear = 10.0, 2.0                   # mm: example values (clearance large enough to see)
    s = 14.0                                  # px per mm
    x0, top = GAP + 150, 96
    pw = 300                                  # the pulley's width across, drawn
    body_h = (belt + clear) * s
    face = clear / 2 * s
    teeth = "".join(f'<rect x="{x0 + i * 20 + 4}" y="{top}" width="10" height="{body_h}" '
                    f'fill="#e2e8f0"/>' for i in range(pw // 20))
    xd = x0 + pw + 24                         # dimension lines, right of the part
    dims = f"""
<line x1="{xd}" y1="{top + face}" x2="{xd}" y2="{top + face + belt * s}" stroke="{BLUE}" stroke-width="1.4" marker-start="url(#arr)" marker-end="url(#arr)"/>
<text x="{xd + 10}" y="{top + body_h / 2 + 5}" font-size="14" font-weight="700" fill="{BLUE}">Belt Height {belt:g} mm</text>
<line x1="{xd + 180}" y1="{top}" x2="{xd + 180}" y2="{top + body_h}" stroke="{CALLOUT}" stroke-width="1.4" marker-start="url(#arr)" marker-end="url(#arr)"/>
<text x="{xd + 188}" y="{top + body_h / 2 - 4}" font-size="13" font-weight="700" fill="{CALLOUT}">pulley height</text>
<text x="{xd + 188}" y="{top + body_h / 2 + 13}" font-size="13" fill="{CALLOUT}">{belt:g} + {clear:g} = {belt + clear:g} mm</text>
<text x="{x0 - 10}" y="{top + face / 2 + 5}" text-anchor="end" font-size="12.5" fill="#1e293b">½ Clearance ({clear / 2:g} mm)</text>
<text x="{x0 - 10}" y="{top + body_h - face / 2 + 5}" text-anchor="end" font-size="12.5" fill="#1e293b">½ Clearance ({clear / 2:g} mm)</text>
<line x1="{x0}" y1="{top}" x2="{xd + 186}" y2="{top}" stroke="#94a3b8" stroke-dasharray="3 3"/>
<line x1="{x0}" y1="{top + body_h}" x2="{xd + 186}" y2="{top + body_h}" stroke="#94a3b8" stroke-dasharray="3 3"/>
<line x1="{x0 + pw}" y1="{top + face}" x2="{xd - 6}" y2="{top + face}" stroke="#94a3b8" stroke-dasharray="3 3"/>
<line x1="{x0 + pw}" y1="{top + face + belt * s}" x2="{xd - 6}" y2="{top + face + belt * s}" stroke="#94a3b8" stroke-dasharray="3 3"/>"""
    body = f"""
<text x="{GAP}" y="34" font-size="22" font-weight="700" fill="{BLUE}">Belt Height and Clearance Height</text>
<text x="{GAP}" y="58" font-size="13" fill="#475569">Side view of a pulley's teeth, with the belt wrapped around them (diagram)</text>
<rect x="{x0}" y="{top}" width="{pw}" height="{body_h}" fill="#cbd5e1" stroke="#475569" stroke-width="1.2"/>
{teeth}
<rect x="{x0}" y="{top + face}" width="{pw}" height="{belt * s}" fill="#2f2f2f" opacity="0.88"/>
<text x="{x0 + pw / 2}" y="{top + body_h / 2 + 5}" text-anchor="middle" font-size="14" font-weight="700" fill="#fff">belt</text>
{dims}
"""
    width = xd + 188 + 150
    height = top + body_h + 95
    body += (f'<text x="{GAP}" y="{height - 53}" font-size="13" fill="#475569">Belt Height: set it to '
             f'your belt\'s width. Clearance Height adds to the pulley\'s height, half on each face, '
             f'so the belt sits inside it.</text>'
             f'<text x="{GAP}" y="{height - 34}" font-size="13" fill="#475569">Clearance is drawn at '
             f'{clear:g} mm so it shows. By default it makes the pulley as wide as the belt’s standard '
             f'asks (the Dimensions panel’s</text>'
             f'<text x="{GAP}" y="{height - 15}" font-size="13" fill="#475569">Minimum face width); '
             f'Belt Height ÷ 20 where no figure is published. Both apply to the whole drive.</text>')
    (HELP / "heights.svg").write_text(svg_page(width, height, body), encoding="utf-8")
    print("wrote static/help/heights.svg", f"{width:.0f} x {height:.0f}")


if __name__ == "__main__":
    mode3d()
    heights()
