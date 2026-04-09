"""
belt_svg_exporter.py
Generates SVG and PNG cross-section views of timing belt teeth.

Supported families:
  HTD  — ISO 13050:2014 Table 9 (H-series curvilinear arc-flank)
  STD  — ISO 13050:2014 Table 27 (S-series large-radius arc-flank)
"""
import io
import math
from geometry.pulley_geometry import (
    H_BELT_SPECS, S_BELT_SPECS, R_BELT_SPECS, G_BELT_SPECS,
    T_BELT_SPECS, AT_BELT_SPECS, IMPERIAL_BELT_SPECS, BELT_FAMILIES,
    generate_h_belt_profile, generate_s_belt_profile, generate_r_belt_profile,
    generate_g_belt_profile, generate_t_belt_profile, generate_at_belt_profile,
    generate_imperial_belt_profile,
)

_BELT_STD_REF = {
    'HTD': 'ISO 13050:2014 Table 9 — H-series curvilinear arc-flank',
    'GT':  'Gates PowerGrip GT3 — modified curvilinear arc-flank (community-derived dimensions)',
    'STD': 'ISO 13050:2014 Table 27 — S-series large-radius arc-flank (estimated for 2M/3M/5M)',
    'RPP': 'ISO 13050:2014 Table 18 — R-series two-lobe parabolic flank',
    'T':        'ISO 5296 Table 1 — T-series trapezoidal (2\u03b2=40\u00b0)',
    'AT':       'ISO 17396 Table 2 — AT-series trapezoidal (2\u03b2=50\u00b0)',
    'Imperial': 'ISO 5296-1:1989 Table 2 — Imperial trapezoidal (XXL interpolated)',
}


def generate_belt_svg(family: str, pitch: str, n_teeth: int = 3) -> str:
    """
    Returns an SVG string showing the belt tooth cross-section.

    Parameters
    ----------
    family  : 'HTD' or 'STD'
    pitch   : pitch code, e.g. '5M', '8M'
    n_teeth : number of tooth pitches to show (default 3)
    """
    if family == 'HTD':
        key = 'H' + pitch           # "H5M"
        if key not in H_BELT_SPECS:
            raise ValueError(f"No HTD belt spec for pitch '{pitch}'")
        spec      = H_BELT_SPECS[key]
        pts, geo  = generate_h_belt_profile(key, n_teeth=n_teeth)
        extra_row = f'r1={spec["r1"]}mm  r2={spec["r2"]}mm'
    elif family == 'GT':
        key = 'G' + pitch           # "G3M"
        if key not in G_BELT_SPECS:
            raise ValueError(f"No GT belt spec for pitch '{pitch}'")
        spec      = G_BELT_SPECS[key]
        pts, geo  = generate_g_belt_profile(key, n_teeth=n_teeth)
        extra_row = f'r1={spec["r1"]}mm  r2={spec["r2"]}mm'
    elif family == 'STD':
        key = 'S' + pitch           # "S8M"
        if key not in S_BELT_SPECS:
            raise ValueError(f"No STD belt spec for pitch '{pitch}'")
        spec      = S_BELT_SPECS[key]
        pts, geo  = generate_s_belt_profile(key, n_teeth=n_teeth)
        extra_row = f'Bg={spec["Bg"]}mm  R1={spec["R1"]}mm  rr={spec["rr"]}mm  ra={spec["ra"]}mm'
    elif family == 'RPP':
        key = 'R' + pitch           # "R5M"
        if key not in R_BELT_SPECS:
            raise ValueError(f"No RPP belt spec for pitch '{pitch}'")
        spec      = R_BELT_SPECS[key]
        pts, geo  = generate_r_belt_profile(key, n_teeth=n_teeth)
        extra_row = f'S={spec["S"]}mm  rr={spec["rr"]}mm  C={spec["C"]}'
    elif family == 'T':
        if pitch not in T_BELT_SPECS:
            raise ValueError(f"No T belt spec for pitch '{pitch}'")
        spec      = T_BELT_SPECS[pitch]
        pts, geo  = generate_t_belt_profile(pitch, n_teeth=n_teeth)
        extra_row = f'S_r={spec["S"]}mm  2\u03b2={spec["beta2"]}\u00b0  rr={spec["rr"]}mm  ra={spec["ra"]}mm'
    elif family == 'AT':
        if pitch not in AT_BELT_SPECS:
            raise ValueError(f"No AT belt spec for pitch '{pitch}'")
        spec      = AT_BELT_SPECS[pitch]
        pts, geo  = generate_at_belt_profile(pitch, n_teeth=n_teeth)
        extra_row = f'S_h={spec["S"]}mm  2\u03b2={spec["beta2"]}\u00b0  rr={spec["rr"]}mm  ra={spec["ra"]}mm'
    elif family == 'Imperial':
        if pitch not in IMPERIAL_BELT_SPECS:
            raise ValueError(f"No Imperial belt spec for pitch '{pitch}'")
        spec      = IMPERIAL_BELT_SPECS[pitch]
        pts, geo  = generate_imperial_belt_profile(pitch, n_teeth=n_teeth)
        extra_row = f'S={spec["S"]}mm  2\u03b2={spec["beta2"]}\u00b0  rr={spec["rr"]}mm  ra={spec["ra"]}mm'
    else:
        raise ValueError(f"Belt SVG not supported for family '{family}'")

    pitch_val = spec["pitch"]
    aa        = spec["aa"]
    hs        = spec["hs"]
    ht        = spec["ht"]
    belt_y    = geo["belt_y"]

    # ── Bounding box (mm, y-up) ───────────────────────────────────────────────
    xs = [x for x, _ in pts];  ys = [y for _, y in pts]
    pad    = pitch_val * 0.35
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad    # y0 = tooth tip side, y1 = belt back side

    vw = x1 - x0;  vh = y1 - y0

    # ── Pixel canvas ─────────────────────────────────────────────────────────
    OUT_W    = 700
    PAD_PX   = 32
    LABEL_H  = 72      # pixels below belt for title + spec rows
    s        = (OUT_W - 2 * PAD_PX) / vw
    content_h = vh * s
    OUT_H    = int(PAD_PX + content_h + PAD_PX + LABEL_H)

    def px(x_mm):  return PAD_PX + (x_mm - x0) * s
    def py(y_mm):  return PAD_PX + (y1 - y_mm) * s   # y-up → y-down

    # ── Polygon ───────────────────────────────────────────────────────────────
    poly_pts = ' '.join(f'{px(x):.2f},{py(y):.2f}' for x, y in pts)
    sw = max(0.5, pitch_val * s * 0.014)

    # ── Reference lines ───────────────────────────────────────────────────────
    lx = PAD_PX
    rx = OUT_W - PAD_PX

    od_scr   = py(0.0)
    pl_scr   = py(aa)
    back_scr = py(belt_y)

    fs = max(9, int(pitch_val * s * 0.12))

    def ref_line(y_scr, color, dash, label, y_mm):
        lbl_x = rx + 6
        lbl_y = y_scr + fs * 0.35
        return (
            f'<line x1="{lx}" y1="{y_scr:.1f}" x2="{rx}" y2="{y_scr:.1f}"'
            f' stroke="{color}" stroke-width="1" stroke-dasharray="{dash}"/>\n'
            f'  <text x="{lbl_x}" y="{lbl_y:.1f}" fill="{color}"'
            f' font-size="{fs}" font-family="sans-serif">'
            f'{label}  y={y_mm:.3f}</text>'
        )

    # ── Info panel ────────────────────────────────────────────────────────────
    panel_y  = int(PAD_PX + content_h + PAD_PX * 0.6)
    fs_t     = max(11, fs + 2)
    fs_s     = max(9,  fs)
    fs_ref   = max(8,  fs - 1)

    spec_row = (f'pitch={pitch_val}mm  ht={ht}mm  hs={hs}mm  aa={aa}mm  {extra_row}')
    std_ref  = _BELT_STD_REF.get(family, '')

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {OUT_W} {OUT_H}"
     width="{OUT_W}" height="{OUT_H}">
  <title>{family} {pitch} Belt Cross-Section</title>
  <rect width="100%" height="100%" fill="#f8fafc"/>

  <!-- Reference lines -->
  {ref_line(back_scr, "#94a3b8", "4,4", "belt back", belt_y)}
  {ref_line(od_scr,   "#64748b", "6,3", "OD",        0.0)}
  {ref_line(pl_scr,   "#7c3aed", "8,4", "pitch line", aa)}

  <!-- Belt body -->
  <polygon points="{poly_pts}"
           fill="#e2e8f0" stroke="#1e293b"
           stroke-width="{sw:.2f}" stroke-linejoin="round"/>

  <!-- Info -->
  <text x="{PAD_PX}" y="{panel_y}"
        fill="#0f172a" font-size="{fs_t}" font-weight="bold"
        font-family="Helvetica, Arial, sans-serif">
    {family} {pitch} Belt Cross-Section — {n_teeth} tooth pitches
  </text>
  <text x="{PAD_PX}" y="{panel_y + fs_t + 4}"
        fill="#475569" font-size="{fs_s}"
        font-family="Helvetica, Arial, sans-serif">{spec_row}</text>
  <text x="{PAD_PX}" y="{panel_y + fs_t + 4 + fs_s + 3}"
        fill="#94a3b8" font-size="{fs_ref}"
        font-family="Helvetica, Arial, sans-serif">{std_ref}</text>
  <text x="{OUT_W // 2}" y="{panel_y + fs_t + 4 + fs_s + 3 + fs_ref + 2}"
        fill="#0078d4" font-size="{fs_ref}" text-anchor="middle"
        font-family="Helvetica, Arial, sans-serif">
    Generated by Sketch Timing Pulley · cheapcadtools.com
  </text>
</svg>'''

    return svg


def generate_belt_png(
    family: str,
    pitch: str,
    n_teeth: int = 3,
    size_px: int = 480,
    bg_color=(250, 251, 252),
    belt_color=(226, 232, 240),
    stroke_color=(30, 41, 59),
) -> bytes:
    """
    Render the belt cross-section as a PNG and return bytes.
    Uses Pillow (PIL) — same approach as png_exporter.py.
    """
    from PIL import Image, ImageDraw

    if family == 'HTD':
        key = 'H' + pitch
        spec = H_BELT_SPECS[key]
        pts, geo = generate_h_belt_profile(key, n_teeth=n_teeth)
    elif family == 'GT':
        key = 'G' + pitch
        spec = G_BELT_SPECS[key]
        pts, geo = generate_g_belt_profile(key, n_teeth=n_teeth)
    elif family == 'STD':
        key = 'S' + pitch
        spec = S_BELT_SPECS[key]
        pts, geo = generate_s_belt_profile(key, n_teeth=n_teeth)
    elif family == 'RPP':
        key = 'R' + pitch
        spec = R_BELT_SPECS[key]
        pts, geo = generate_r_belt_profile(key, n_teeth=n_teeth)
    elif family == 'T':
        spec = T_BELT_SPECS[pitch]
        pts, geo = generate_t_belt_profile(pitch, n_teeth=n_teeth)
    elif family == 'AT':
        spec = AT_BELT_SPECS[pitch]
        pts, geo = generate_at_belt_profile(pitch, n_teeth=n_teeth)
    elif family == 'Imperial':
        spec = IMPERIAL_BELT_SPECS[pitch]
        pts, geo = generate_imperial_belt_profile(pitch, n_teeth=n_teeth)
    else:
        raise ValueError(f"Belt PNG not supported for family '{family}'")

    pitch_val = spec["pitch"]
    aa        = spec["aa"]
    belt_y    = geo["belt_y"]

    xs = [x for x, _ in pts];  ys = [y for _, y in pts]
    pad  = pitch_val * 0.35
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad

    W = size_px
    # Height proportional to bounding box, with a minimum so reference lines show
    H = max(120, int(size_px * (y1 - y0) / (x1 - x0)))

    s  = (W - 2) / (x1 - x0)
    ox = 1 - x0 * s
    oy = H - 1 + y0 * s   # y-up → y-down

    def px(x): return ox + x * s
    def py(y): return oy - y * s

    # Supersample 2× for anti-aliasing
    SS = 2
    img  = Image.new('RGB', (W * SS, H * SS), bg_color)
    draw = ImageDraw.Draw(img)

    lw = max(1, int(s * SS * 0.018))

    def ss(pt):   return (pt[0] * SS, pt[1] * SS)
    def ssx(x):   return px(x) * SS
    def ssy(y):   return py(y) * SS

    # Reference lines
    def hline(y_mm, color, width=1):
        y_scr = ssy(y_mm)
        draw.line([(0, y_scr), (W * SS, y_scr)], fill=color, width=width)

    hline(belt_y, (148, 163, 184), 1)   # belt back — slate-300
    hline(0.0,    (100, 116, 139), 2)   # OD        — slate-500
    hline(aa,     (124, 58, 237),  2)   # pitch line — violet-600

    # Belt polygon
    ss_poly = [(ssx(x), ssy(y)) for x, y in pts]
    draw.polygon(ss_poly, fill=belt_color)
    draw.line(ss_poly + [ss_poly[0]], fill=stroke_color, width=lw, joint='curve')

    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.read()
