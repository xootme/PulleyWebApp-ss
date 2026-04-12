"""
png_exporter.py
Renders a timing belt pulley profile directly to PNG using Pillow.
Draws from geometry points — no SVG conversion needed.
"""
import math
import io
from PIL import Image, ImageDraw

from geometry.pulley_geometry import (
    generate_profile_groove, _build_groove_points,
    wrap_groove_to_pulley, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    build_two_pulley_belt, BELT_FAMILIES,
)

_POLYLINE_FAMILIES = {'Imperial', 'T', 'AT'}


def _profile_key(family, pitch):
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def generate_png(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    size_px: int = 500,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
) -> bytes:
    """
    Returns PNG bytes of the full pulley profile.

    Parameters
    ----------
    size_px   : output image size (square, pixels)
    bg_color  : RGB background
    groove_color : RGB groove stroke
    bore_color   : RGB bore circle stroke
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile '{key}'")

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

    # ── Coordinate transform: mm → pixels ────────────────────────────────────
    padding = size_px * 0.06
    scale   = (size_px / 2.0 - padding) / R_OD
    cx = cy = size_px / 2.0

    def to_px(x_mm, y_mm):
        return (cx + x_mm * scale, cy - y_mm * scale)   # y flipped for screen

    def rotate(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    # ── Build full pulley outline as flat pixel polygon ───────────────────────
    # One polygon: grooves connected by sampled OD arcs
    ARC_SAMPLES = max(4, int(num_teeth * 0.5))   # samples per land arc

    poly_px = []

    for i in range(num_teeth):
        th = i * t_ang

        # Groove points
        for gx, gy in wrapped:
            rx, ry = rotate(gx, gy, th)
            poly_px.append(to_px(rx, ry))

        # OD land arc: from right edge of this groove to left edge of next
        a_start = th + edge_a
        a_end   = th + t_ang - edge_a
        for j in range(1, ARC_SAMPLES + 1):
            a = a_start + (a_end - a_start) * j / ARC_SAMPLES
            x_mm = R_OD * math.sin(a)
            y_mm = R_OD * math.cos(a)
            poly_px.append(to_px(x_mm, y_mm))

    # ── Bore circle as sampled polygon ───────────────────────────────────────
    BORE_SAMPLES = max(64, num_teeth * 4)
    bore_px = []
    for i in range(BORE_SAMPLES):
        a = 2.0 * math.pi * i / BORE_SAMPLES
        bore_px.append(to_px(R_bore * math.sin(a), R_bore * math.cos(a)))

    # ── Draw ──────────────────────────────────────────────────────────────────
    # Supersample for anti-aliasing: render at 2× then downsample
    SS = 2
    render_size = size_px * SS
    img  = Image.new('RGB', (render_size, render_size), bg_color)
    draw = ImageDraw.Draw(img)

    # Scale polygon to supersampled space
    ss_poly  = [(x * SS, y * SS) for x, y in poly_px]
    ss_bore  = [(x * SS, y * SS) for x, y in bore_px]

    line_w = max(1, int(scale * SS * 0.28))   # ~0.28 mm stroke

    draw.polygon(ss_poly,  outline=groove_color, fill=None)
    draw.line(ss_poly + [ss_poly[0]], fill=groove_color, width=line_w, joint='curve')

    if R_bore > 0 and len(ss_bore) > 2:
        draw.polygon(ss_bore, outline=bore_color, fill=None)
        draw.line(ss_bore + [ss_bore[0]], fill=bore_color, width=line_w, joint='curve')

    # Downsample to final size
    img = img.resize((size_px, size_px), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.read()


def _build_pulley_poly(family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm):
    """Return (wrapped_pts, R_OD, edge_a, spec) for one pulley."""
    key  = PROFILE_KEY_PREFIX.get(family, '') + pitch
    spec = PULLEY_SPECS[key]
    pitch_val      = spec['pitch']
    clearance_mm   = max(-pitch_val, min(clearance_mm,   pitch_val))
    backlash_mm    = max(-pitch_val, min(backlash_mm,    pitch_val))
    print_extra_mm = max(0.0,        min(print_extra_mm, pitch_val))
    container    = generate_profile_groove(family, key, num_teeth, clearance_mm, print_extra_mm, backlash_mm)
    groove_prims = container.primitives[1:-1]
    groove_pts   = _build_groove_points(groove_prims, family)
    wrapped, R_OD, edge_a = wrap_groove_to_pulley(groove_pts, spec, num_teeth, print_extra_mm)
    return wrapped, R_OD, edge_a, spec


def generate_png_dual(
    family: str,
    pitch: str,
    num_teeth1: int,
    bore_mm1: float,
    clearance_mm1: float = 0.0,
    backlash_mm1: float = 0.0,
    print_extra_mm1: float = 0.0,
    num_teeth2: int = 20,
    bore_mm2: float = 8.0,
    clearance_mm2: float = 0.0,
    backlash_mm2: float = 0.0,
    print_extra_mm2: float = 0.0,
    center_dist_mm: float = 100.0,
    size_px: int = 480,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
) -> bytes:
    """
    Render two pulleys side by side, separated by center_dist_mm, into one PNG.
    Both pulleys use the same family/pitch (belt must match).
    """
    wrapped1, R_OD1, edge_a1, spec1 = _build_pulley_poly(
        family, pitch, num_teeth1, clearance_mm1, backlash_mm1, print_extra_mm1)
    wrapped2, R_OD2, edge_a2, spec2 = _build_pulley_poly(
        family, pitch, num_teeth2, clearance_mm2, backlash_mm2, print_extra_mm2)

    t_ang1 = 2.0 * math.pi / num_teeth1
    t_ang2 = 2.0 * math.pi / num_teeth2
    R_bore1 = bore_mm1 / 2.0
    R_bore2 = bore_mm2 / 2.0

    ARC_SAMPLES = max(4, int(max(num_teeth1, num_teeth2) * 0.5))

    def rotate(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    def pulley_poly(wrapped, R_OD, edge_a, num_teeth, t_ang, cx_off, cy_off, phi=0.0):
        pts = []
        for i in range(num_teeth):
            th = phi + i * t_ang
            for gx, gy in wrapped:
                rx, ry = rotate(gx, gy, th)
                pts.append((rx + cx_off, ry + cy_off))
            a_start = th + edge_a
            a_end   = th + t_ang - edge_a
            for j in range(1, ARC_SAMPLES + 1):
                a = a_start + (a_end - a_start) * j / ARC_SAMPLES
                pts.append((R_OD * math.sin(a) + cx_off, R_OD * math.cos(a) + cy_off))
        return pts

    def bore_poly(R_bore, cx_off, cy_off):
        BORE_SAMPLES = max(64, 4 * max(num_teeth1, num_teeth2))
        pts = []
        for i in range(BORE_SAMPLES):
            a = 2.0 * math.pi * i / BORE_SAMPLES
            pts.append((R_bore * math.sin(a) + cx_off, R_bore * math.cos(a) + cy_off))
        return pts

    # Clamp center distance to be at least the sum of pitch-line radii
    pitch_mm = spec1['pitch']
    R_pitch1 = num_teeth1 * pitch_mm / (2.0 * math.pi)
    R_pitch2 = num_teeth2 * pitch_mm / (2.0 * math.pi)
    min_c = R_pitch1 + R_pitch2
    center_dist_mm = max(center_dist_mm, min_c)

    # For RENDERING only, cap center distance so both pulleys stay visible.
    # When the gap is very large relative to pulley size the image becomes a
    # thin horizontal strip and the pulleys appear as tiny dots.  The actual
    # center distance is already displayed numerically in the UI.
    max_render_gap  = 4.0 * (R_OD1 + R_OD2)   # gap between pulley edges
    actual_gap      = center_dist_mm - R_pitch1 - R_pitch2
    render_gap      = min(actual_gap, max_render_gap)
    render_center   = max(min_c, R_pitch1 + R_pitch2 + render_gap)

    # Pulleys sit on y=0, P1 centred at (-d/2, 0), P2 at (+d/2, 0)
    cx1 = -render_center / 2.0
    cx2 =  render_center / 2.0

    # Belt geometry (only for HTD/STD families)
    belt_ring = []
    tooth_polys = []
    phi_left = phi_right = 0.0
    if family in BELT_FAMILIES:
        belt_ring, tooth_polys, phi_left, phi_right = build_two_pulley_belt(
            family, pitch, num_teeth1, num_teeth2,
            render_center, x_offset=cx1,
        )

    # Viewport: bounding box covering both pulley ODs
    x_min = cx1 - R_OD1
    x_max = cx2 + R_OD2
    y_max = max(R_OD1, R_OD2)

    # Expand to include belt ring (pitch circles are larger than OD)
    if belt_ring:
        xs_belt = [p[0] for p in belt_ring]
        ys_belt = [p[1] for p in belt_ring]
        x_min = min(x_min, min(xs_belt))
        x_max = max(x_max, max(xs_belt))
        y_max = max(y_max, max(abs(v) for v in ys_belt))

    world_cx = (x_min + x_max) / 2.0   # true bbox centre (not always 0)
    vw = x_max - x_min
    vh = y_max * 2.0

    # Ensure the image is never more than 3:1 wide/tall so pulleys don't
    # become invisible strips in the preview panel.
    max_aspect = 3.0
    if vw / max(vh, 1.0) > max_aspect:
        vh = vw / max_aspect

    padding = size_px * 0.05
    scale_x = (size_px - 2 * padding) / vw
    scale_y = (size_px * vh / vw - 2 * padding) / vh
    scale   = min(scale_x, scale_y)

    img_w = size_px
    img_h = int(size_px * vh / vw + 2 * padding)

    screen_cx = img_w / 2.0
    screen_cy = img_h / 2.0

    def to_px(x_mm, y_mm):
        return (
            screen_cx + (x_mm - world_cx) * scale,
            screen_cy - y_mm * scale,
        )

    poly1 = pulley_poly(wrapped1, R_OD1, edge_a1, num_teeth1, t_ang1, cx1, 0, phi=phi_left)
    poly2 = pulley_poly(wrapped2, R_OD2, edge_a2, num_teeth2, t_ang2, cx2, 0, phi=phi_right)

    SS = 2
    render_w = img_w * SS
    render_h = img_h * SS
    img  = Image.new('RGB', (render_w, render_h), bg_color)
    draw = ImageDraw.Draw(img)

    line_w = max(1, int(scale * SS * 0.28))

    def ss_pts(pts):
        return [(to_px(x, y)[0] * SS, to_px(x, y)[1] * SS) for x, y in pts]

    # ── Belt ring strip (drawn first, behind pulleys) ─────────────────────────
    BELT_FILL   = (184, 196, 212)   # slate-blue-grey
    BELT_STROKE = (122, 138, 158)
    TOOTH_FILL  = (138, 154, 178)
    TOOTH_STROKE= (90, 106, 130)

    if belt_ring:
        # Outer boundary: fill entire belt area
        sp_outer = ss_pts(belt_ring)
        draw.polygon(sp_outer, fill=BELT_FILL)
        # Inner boundary: punch out groove cavities and loop interior
        for tp in tooth_polys:
            sp_inner = ss_pts(tp)
            draw.polygon(sp_inner, fill=bg_color)
        # Stroke outer outline on top
        draw.line(sp_outer + [sp_outer[0]], fill=BELT_STROKE,
                  width=max(1, int(scale * SS * 0.10)), joint='curve')

    # ── Pulleys (on top of belt) ──────────────────────────────────────────────
    PULLEY_FILL   = (203, 213, 225)   # slate-300
    PULLEY_STROKE = (51,  65,  85)    # slate-700

    for pts in (poly1, poly2):
        sp = ss_pts(pts)
        draw.polygon(sp, fill=PULLEY_FILL)
        draw.line(sp + [sp[0]], fill=PULLEY_STROKE, width=line_w, joint='curve')

    for R_bore, cx_off in ((R_bore1, cx1), (R_bore2, cx2)):
        if R_bore > 0:
            bp = bore_poly(R_bore, cx_off, 0)
            sp = ss_pts(bp)
            if len(sp) > 2:
                draw.polygon(sp, fill=bg_color)
                draw.line(sp + [sp[0]], fill=bore_color, width=line_w, joint='curve')

    img = img.resize((img_w, img_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.read()
