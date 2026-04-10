"""
svg_exporter.py
Generates an SVG of a full timing belt pulley profile (all teeth).

The pulley outline is a single closed path:
    groove_0 → OD_arc → groove_1 → OD_arc → … → close
This ensures the OD lands between teeth are arcs, not lines.

The download SVG includes an info panel below the profile listing all
parameters and a website callout.
"""
import math
from geometry.pulley_geometry import (
    generate_profile_groove, _build_groove_points,
    wrap_groove_to_pulley, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    build_two_pulley_belt, BELT_FAMILIES,
)

_POLYLINE_FAMILIES = {'Imperial', 'T', 'AT'}

_STANDARD_REF = {
    'HTD':      'ISO 13050:2014 — curvilinear arc-flank',
    'GT':       'Gates PowerGrip GT2/GT3 — curvilinear arc-flank',
    'STD':      'ISO 13050:2014 — S-series large-radius arc-flank',
    'Imperial': 'ISO 5294:2012 — trapezoidal (ANSI/RMA IP-24)',
    'T':        'ISO 17396:2017 — trapezoidal T-series',
    'AT':       'ISO 17396:2017 — trapezoidal AT-series',
    'RPP':      'ISO 13050:2014 — parabolic flank',
}

_CLEARANCE_LABEL = {
    'TIGHT': 'Tight', 'STANDARD': 'Standard', 'LOOSE': 'Loose', 'CUSTOM': 'Custom',
}
_BACKLASH_LABEL = {
    'NONE': 'None', 'TIGHT': 'Tight', 'STANDARD': 'Standard',
    'LOOSE': 'Loose', 'CUSTOM': 'Custom',
}


def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def generate_svg(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    padding_mm: float = 3.0,
    clearance_preset: str = 'STANDARD',
    backlash_preset: str = 'STANDARD',
) -> str:
    """
    Returns an SVG string: full pulley profile + info panel.
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}' for family '{family}', pitch '{pitch}'")

    spec = PULLEY_SPECS[key]
    pitch_val = spec['pitch']
    clearance_mm   = max(-pitch_val, min(clearance_mm,   pitch_val))
    backlash_mm    = max(-pitch_val, min(backlash_mm,    pitch_val))
    print_extra_mm = max(0.0,        min(print_extra_mm, pitch_val))

    container    = generate_profile_groove(family, key, num_teeth, clearance_mm, print_extra_mm, backlash_mm)
    groove_prims = container.primitives[1:-1]
    groove_pts   = _build_groove_points(groove_prims, family)

    wrapped, R_OD, edge_a = wrap_groove_to_pulley(groove_pts, spec, num_teeth, print_extra_mm)

    R_pitch = (spec['pitch'] * num_teeth) / (2.0 * math.pi)
    R_bore  = bore_mm / 2.0
    t_ang   = 2.0 * math.pi / num_teeth

    def rotate(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    # ── Pulley outline: single closed path ───────────────────────────────────
    d_parts = []
    for i in range(num_teeth):
        th        = i * t_ang
        tooth_pts = [rotate(gx, gy, th) for gx, gy in wrapped]

        d_parts.append(
            f"{'M' if i == 0 else 'L'} {tooth_pts[0][0]:.4f} {tooth_pts[0][1]:.4f}"
        )
        for gx, gy in tooth_pts[1:]:
            d_parts.append(f"L {gx:.4f} {gy:.4f}")

        a_end = th + t_ang - edge_a
        d_parts.append(
            f"A {R_OD:.4f} {R_OD:.4f} 0 0 1 "
            f"{R_OD * math.sin(a_end):.4f} {R_OD * math.cos(a_end):.4f}"
        )
    d_parts.append("Z")
    path_d = " ".join(d_parts)

    sw = max(0.15, R_OD * 2.0 * 0.004)   # stroke width scaled to pulley size
    bore_el = (
        f'<circle cx="0" cy="0" r="{R_bore:.4f}" '
        f'fill="none" stroke="#1a1a1a" stroke-width="{sw:.3f}"/>'
        if R_bore > 0 else ''
    )

    # ── Info panel ────────────────────────────────────────────────────────────
    # The panel has a fixed minimum width so two-column text always fits,
    # regardless of how small the pulley is. The pulley is centred above it.
    OD_mm = R_OD * 2.0
    PD_mm = R_pitch * 2.0

    cl_label = _CLEARANCE_LABEL.get(clearance_preset, 'Custom')
    bl_label = _BACKLASH_LABEL.get(backlash_preset, 'Custom')
    std_ref  = _STANDARD_REF.get(family, '')

    # Fixed font sizes (mm).  These look correct at 600px output width.
    fs_title = 4.0
    fs_body  = 2.8
    fs_small = 2.3
    line_h   = 4.2

    # Panel must be at least 120 mm wide so columns never overlap.
    # If the pulley is wider, extend to match.
    panel_w   = max(120.0, OD_mm + padding_mm * 2)
    panel_left  = -panel_w / 2.0
    panel_right =  panel_w / 2.0

    # Two-column layout: label col left-aligned, value col starts at 42 mm in
    col_label_x = panel_left + 4.0
    col_value_x = panel_left + 46.0     # fixed offset — always clear of labels

    panel_top = R_OD + padding_mm + 2.0

    rows = [
        # (label, value)
        ('Belt Family',      family),
        ('Pitch',            f'{pitch}  ({spec["pitch"]:.3f} mm)'),
        ('Number of Teeth',  str(num_teeth)),
        ('Outer Diameter',   f'{OD_mm:.3f} mm'),
        ('Pitch Diameter',   f'{PD_mm:.3f} mm'),
        ('Bore Diameter',    f'{bore_mm:.3f} mm'),
        ('Tooth Clearance',  f'{cl_label}  ({clearance_mm:+.3f} mm)'),
        ('Backlash',         f'{bl_label}  ({backlash_mm:+.3f} mm)'),
        ('Print Extra',      f'{print_extra_mm:.3f} mm'),
    ]

    def txt(x, y, content, font_size, color='#1a1a1a', weight='normal', anchor='start'):
        return (f'<text x="{x:.4f}" y="{y:.4f}" '
                f'font-family="Helvetica, Arial, sans-serif" '
                f'font-size="{font_size}" font-weight="{weight}" '
                f'fill="{color}" text-anchor="{anchor}">'
                f'{content}</text>')

    panel_els = []

    # Separator line
    panel_els.append(
        f'<line x1="{panel_left:.4f}" y1="{panel_top - 1.5:.4f}" '
        f'x2="{panel_right:.4f}" y2="{panel_top - 1.5:.4f}" '
        f'stroke="#cccccc" stroke-width="0.25"/>'
    )

    # Title
    y = panel_top + fs_title
    panel_els.append(txt(0, y, f'{family} {pitch} — {num_teeth} Teeth', fs_title,
                         color='#0078d4', weight='bold', anchor='middle'))
    y += line_h * 1.5

    # Parameter rows
    for label, value in rows:
        panel_els.append(txt(col_label_x, y, label, fs_body, color='#555555'))
        panel_els.append(txt(col_value_x, y, value, fs_body, color='#1a1a1a', weight='bold'))
        y += line_h

    y += line_h * 0.5

    # Standard reference
    panel_els.append(txt(col_label_x, y, 'Standard', fs_body, color='#555555'))
    panel_els.append(txt(col_value_x, y, std_ref,    fs_small, color='#1a1a1a'))
    y += line_h * 1.8

    # Divider before footer
    panel_els.append(
        f'<line x1="{panel_left:.4f}" y1="{y:.4f}" '
        f'x2="{panel_right:.4f}" y2="{y:.4f}" '
        f'stroke="#cccccc" stroke-width="0.2"/>'
    )
    y += line_h * 0.8

    # Website callout
    panel_els.append(txt(0, y,
        'Generated by Sketch Timing Pulley  ·  cheapcadtools.com',
        fs_small, color='#0078d4', anchor='middle'))

    panel_total_height = y - panel_top + line_h

    # ── Viewport: wide enough for both pulley and panel ──────────────────────
    vx   = panel_left - padding_mm
    vy   = -(R_OD + padding_mm)
    vw   = panel_w + padding_mm * 2
    vh   = (R_OD + padding_mm) + panel_top + panel_total_height + padding_mm
    vbox = f"{vx:.4f} {vy:.4f} {vw:.4f} {vh:.4f}"

    # Output width fixed at 600px; height scales proportionally
    out_w = 600
    out_h = int(out_w * vh / vw)

    panel_svg = '\n  '.join(panel_els)

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="{vbox}"
     width="{out_w}" height="{out_h}">
  <title>{family} {pitch} {num_teeth}T Pulley Profile — cheapcadtools.com</title>
  <rect x="{vx:.4f}" y="{vy:.4f}" width="{vw:.4f}" height="{vh:.4f}" fill="#ffffff"/>
  <path d="{path_d}"
        fill="none" stroke="#1a1a1a" stroke-width="{sw:.3f}"
        stroke-linejoin="round" stroke-linecap="round"/>
  {bore_el}
  {panel_svg}
</svg>'''

    return svg


def _pulley_path_d(wrapped, R_OD, edge_a, num_teeth, cx, cy, phi=0.0):
    """Build SVG path `d` string for one pulley centred at (cx, cy)."""
    t_ang = 2.0 * math.pi / num_teeth
    ARC_SAMPLES = max(4, int(num_teeth * 0.5))

    def rot(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    parts = []
    for i in range(num_teeth):
        th = phi + i * t_ang
        tooth_pts = [rot(gx, gy, th) for gx, gy in wrapped]

        cmd = 'M' if i == 0 else 'L'
        parts.append(f"{cmd} {cx + tooth_pts[0][0]:.4f} {cy - tooth_pts[0][1]:.4f}")
        for gx, gy in tooth_pts[1:]:
            parts.append(f"L {cx + gx:.4f} {cy - gy:.4f}")

        a_end = th + t_ang - edge_a
        ex = cx + R_OD * math.sin(a_end)
        ey = cy - R_OD * math.cos(a_end)
        parts.append(f"A {R_OD:.4f} {R_OD:.4f} 0 0 1 {ex:.4f} {ey:.4f}")
    parts.append("Z")
    return " ".join(parts)


def _poly_points(pts, cx, cy):
    """Convert (x,y) mm list to SVG points string with screen-y flip."""
    return " ".join(f"{cx + x:.4f},{cy - y:.4f}" for x, y in pts)


def _pts_to_path_d(pts, cx, cy):
    """Convert (x,y) mm list to a closed SVG path subpath (M...L...Z)."""
    it = iter(pts)
    first = next(it)
    d = f"M {cx + first[0]:.4f},{cy - first[1]:.4f}"
    for x, y in it:
        d += f" L {cx + x:.4f},{cy - y:.4f}"
    return d + " Z"


def generate_svg_dual(
    family: str,
    pitch: str,
    num_teeth1: int,
    bore_mm1: float,
    clearance_mm1: float = 0.0,
    backlash_mm1: float = 0.0,
    print_extra_mm1: float = 0.0,
    clearance_preset1: str = 'STANDARD',
    backlash_preset1: str = 'STANDARD',
    num_teeth2: int = 20,
    bore_mm2: float = 8.0,
    clearance_mm2: float = 0.0,
    backlash_mm2: float = 0.0,
    print_extra_mm2: float = 0.0,
    clearance_preset2: str = 'STANDARD',
    backlash_preset2: str = 'STANDARD',
    center_dist_mm: float = 100.0,
    n_belt_teeth: int = 0,
    padding_mm: float = 3.0,
) -> str:
    """
    SVG of two pulleys with the belt wrapped around them.
    Pulleys are centred at (-C/2, 0) and (+C/2, 0) in world space.
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}'")
    spec = PULLEY_SPECS[key]
    pitch_val = spec['pitch']

    def clamp(v, lo, hi): return max(lo, min(v, hi))

    cl1 = clamp(clearance_mm1, -pitch_val, pitch_val)
    bl1 = clamp(backlash_mm1,  -pitch_val, pitch_val)
    pe1 = clamp(print_extra_mm1, 0.0, pitch_val)
    cl2 = clamp(clearance_mm2, -pitch_val, pitch_val)
    bl2 = clamp(backlash_mm2,  -pitch_val, pitch_val)
    pe2 = clamp(print_extra_mm2, 0.0, pitch_val)

    def _build(num_teeth, cl, bl, pe):
        container  = generate_profile_groove(family, key, num_teeth, cl, pe, bl)
        prims      = container.primitives[1:-1]
        pts        = _build_groove_points(prims, family)
        wrapped, R_OD, edge_a = wrap_groove_to_pulley(pts, spec, num_teeth, pe)
        return wrapped, R_OD, edge_a

    wrapped1, R_OD1, edge_a1 = _build(num_teeth1, cl1, bl1, pe1)
    wrapped2, R_OD2, edge_a2 = _build(num_teeth2, cl2, bl2, pe2)

    R_pitch1 = num_teeth1 * pitch_val / (2.0 * math.pi)
    R_pitch2 = num_teeth2 * pitch_val / (2.0 * math.pi)
    C = max(float(center_dist_mm), R_pitch1 + R_pitch2)

    cx1 = -C / 2.0
    cx2 =  C / 2.0
    cy  = 0.0   # both pulleys on y=0

    # Belt geometry
    belt_ring, tooth_polys, phi_left, phi_right = [], [], 0.0, 0.0
    if family in BELT_FAMILIES:
        belt_ring, tooth_polys, phi_left, phi_right = build_two_pulley_belt(
            family, pitch, num_teeth1, num_teeth2, C, x_offset=cx1,
        )

    # Viewport extents
    x_min = cx1 - R_OD1 - padding_mm
    x_max = cx2 + R_OD2 + padding_mm
    y_ext = max(R_OD1, R_OD2) + padding_mm
    if belt_ring:
        xs = [p[0] for p in belt_ring]; ys = [p[1] for p in belt_ring]
        x_min = min(x_min, min(xs) - padding_mm)
        x_max = max(x_max, max(xs) + padding_mm)
        y_ext = max(y_ext, max(abs(v) for v in ys) + padding_mm)

    world_w = x_max - x_min
    world_h = y_ext * 2.0

    # SVG coordinate system: origin at centre of viewport
    # screen_x = (x_mm - x_min) * scale, screen_y = y_ext * scale - y_mm * scale
    # We'll use a viewBox so mm are preserved.

    # ── Info panel ────────────────────────────────────────────────────────────
    fs_title = max(3.5, world_w * 0.022)
    fs_body  = max(2.5, world_w * 0.016)
    fs_small = max(2.0, world_w * 0.013)
    line_h   = fs_body * 1.55

    panel_w   = world_w
    panel_left  = x_min
    col1_x = panel_left + panel_w * 0.03
    col2_x = panel_left + panel_w * 0.35
    col3_x = panel_left + panel_w * 0.53
    col4_x = panel_left + panel_w * 0.85

    panel_top = y_ext + padding_mm * 2.0

    def txt(x, y, content, font_size, color='#1a1a1a', weight='normal', anchor='start'):
        return (f'<text x="{x:.4f}" y="{y:.4f}" '
                f'font-family="Helvetica, Arial, sans-serif" '
                f'font-size="{font_size:.4f}" font-weight="{weight}" '
                f'fill="{color}" text-anchor="{anchor}">'
                f'{content}</text>')

    OD1 = R_OD1 * 2.0;  PD1 = R_pitch1 * 2.0
    OD2 = R_OD2 * 2.0;  PD2 = R_pitch2 * 2.0

    cl1_lbl = _CLEARANCE_LABEL.get(clearance_preset1, 'Custom')
    bl1_lbl = _BACKLASH_LABEL .get(backlash_preset1,  'Custom')
    cl2_lbl = _CLEARANCE_LABEL.get(clearance_preset2, 'Custom')
    bl2_lbl = _BACKLASH_LABEL .get(backlash_preset2,  'Custom')
    std_ref = _STANDARD_REF.get(family, '')

    panel_els = []
    panel_els.append(
        f'<line x1="{panel_left:.4f}" y1="{panel_top - 1.5:.4f}" '
        f'x2="{x_max:.4f}" y2="{panel_top - 1.5:.4f}" '
        f'stroke="#cccccc" stroke-width="0.25"/>'
    )

    y = panel_top + fs_title
    ratio_str = f'  (ratio {num_teeth2/num_teeth1:.3f})' if num_teeth1 != num_teeth2 else ''
    panel_els.append(txt(x_min + panel_w / 2, y,
        f'{family} {pitch} — Pulley 1: {num_teeth1}T  ·  Pulley 2: {num_teeth2}T{ratio_str}',
        fs_title, color='#0078d4', weight='bold', anchor='middle'))
    y += line_h * 1.4

    # Column headers
    for lx, label in ((col1_x, ''), (col2_x, 'Pulley 1'), (col4_x, 'Pulley 2')):
        panel_els.append(txt(lx, y, label, fs_body, color='#555555', weight='bold'))
    y += line_h

    rows = [
        ('Teeth',            str(num_teeth1),                          str(num_teeth2)),
        ('Outer Diameter',   f'{OD1:.3f} mm',                          f'{OD2:.3f} mm'),
        ('Pitch Diameter',   f'{PD1:.3f} mm',                          f'{PD2:.3f} mm'),
        ('Bore Diameter',    f'{bore_mm1:.3f} mm',                     f'{bore_mm2:.3f} mm'),
        ('Tooth Clearance',  f'{cl1_lbl} ({cl1:+.3f} mm)',             f'{cl2_lbl} ({cl2:+.3f} mm)'),
        ('Backlash',         f'{bl1_lbl} ({bl1:+.3f} mm)',             f'{bl2_lbl} ({bl2:+.3f} mm)'),
        ('Print Extra',      f'{pe1:.3f} mm',                          f'{pe2:.3f} mm'),
    ]
    for label, v1, v2 in rows:
        panel_els.append(txt(col1_x, y, label,  fs_body, color='#555555'))
        panel_els.append(txt(col2_x, y, v1,     fs_body, weight='bold'))
        panel_els.append(txt(col4_x, y, v2,     fs_body, weight='bold'))
        y += line_h

    y += line_h * 0.3
    panel_els.append(txt(col1_x, y, 'Centre Distance', fs_body, color='#555555'))
    panel_els.append(txt(col2_x, y, f'{C:.3f} mm', fs_body, weight='bold'))
    if n_belt_teeth:
        panel_els.append(txt(col3_x, y, 'Belt Teeth', fs_body, color='#555555'))
        panel_els.append(txt(col4_x, y, str(n_belt_teeth), fs_body, weight='bold'))
    y += line_h * 1.2

    panel_els.append(txt(col1_x, y, 'Standard', fs_body, color='#555555'))
    panel_els.append(txt(col2_x, y, std_ref, fs_small, color='#1a1a1a'))
    y += line_h * 1.5

    panel_els.append(
        f'<line x1="{panel_left:.4f}" y1="{y:.4f}" '
        f'x2="{x_max:.4f}" y2="{y:.4f}" stroke="#cccccc" stroke-width="0.2"/>'
    )
    y += line_h * 0.8
    panel_els.append(txt(x_min + panel_w / 2, y,
        'Generated by Sketch Timing Pulley  ·  cheapcadtools.com',
        fs_small, color='#0078d4', anchor='middle'))

    panel_h = y - panel_top + line_h

    # ── ViewBox ───────────────────────────────────────────────────────────────
    vx = x_min
    vy = -y_ext
    vw = world_w
    vh = world_h + panel_top - (-y_ext) + panel_h + padding_mm

    out_w = max(800, int(world_w * 8))
    out_h = int(out_w * vh / vw)

    # ── SVG elements ──────────────────────────────────────────────────────────
    # screen_y = -y_mm (SVG y-down, world y-up; world cy=0 → SVG cy=0)
    # All world coords map as: svg_x = x_mm, svg_y = -y_mm

    sw1 = max(0.15, R_OD1 * 2.0 * 0.004)
    sw2 = max(0.15, R_OD2 * 2.0 * 0.004)
    sw_belt = max(0.1, pitch_val * 0.015)

    path1 = _pulley_path_d(wrapped1, R_OD1, edge_a1, num_teeth1, cx1, cy, phi=phi_left)
    path2 = _pulley_path_d(wrapped2, R_OD2, edge_a2, num_teeth2, cx2, cy, phi=phi_right)

    # Belt: outer (back_path) + inner (toothed profile) as two evenodd subpaths.
    # The outer path fills the whole belt area; the inner path punches out the
    # groove cavities and loop interior — no seam needed.
    belt_el = ''
    tooth_els = ''
    if belt_ring:
        d_outer = _pts_to_path_d(belt_ring, 0, cy)
        d_inner = _pts_to_path_d(tooth_polys[0], 0, cy) if tooth_polys else ''
        belt_el = (
            f'<path fill-rule="evenodd" '
            f'd="{d_outer} {d_inner}" '
            f'fill="#c8d6e5" stroke="#7a8a9e" stroke-width="{sw_belt:.4f}" '
            f'stroke-linejoin="round"/>'
        )

    bore1_el = (
        f'<circle cx="{cx1:.4f}" cy="{cy:.4f}" r="{bore_mm1/2:.4f}" '
        f'fill="none" stroke="#1a1a1a" stroke-width="{sw1:.4f}"/>'
        if bore_mm1 > 0 else ''
    )
    bore2_el = (
        f'<circle cx="{cx2:.4f}" cy="{cy:.4f}" r="{bore_mm2/2:.4f}" '
        f'fill="none" stroke="#1a1a1a" stroke-width="{sw2:.4f}"/>'
        if bore_mm2 > 0 else ''
    )

    panel_svg = '\n  '.join(panel_els)

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="{vx:.4f} {vy:.4f} {vw:.4f} {vh:.4f}"
     width="{out_w}" height="{out_h}">
  <title>{family} {pitch} — {num_teeth1}T / {num_teeth2}T Dual Pulley Belt — cheapcadtools.com</title>
  <rect x="{vx:.4f}" y="{vy:.4f}" width="{vw:.4f}" height="{vh:.4f}" fill="#ffffff"/>
  {belt_el}
  {tooth_els}
  <path d="{path1}" fill="none" stroke="#1a1a1a" stroke-width="{sw1:.4f}"
        stroke-linejoin="round" stroke-linecap="round"/>
  {bore1_el}
  <path d="{path2}" fill="none" stroke="#1a1a1a" stroke-width="{sw2:.4f}"
        stroke-linejoin="round" stroke-linecap="round"/>
  {bore2_el}
  {panel_svg}
</svg>'''

    return svg
