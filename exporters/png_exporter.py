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


# ── Spoke void geometry helpers (ported from generate_spoked_pulley_svg.py) ──

_EPS = 1e-9

def _sv_dot(ax, ay, bx, by): return ax * bx + ay * by
def _sv_cross(ax, ay, bx, by): return ax * by - ay * bx
def _sv_unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > _EPS else (1.0, 0.0)

def _sv_project(px, py, x0, y0, dx, dy):
    """Project point (px,py) onto line (x0,y0)+(dx,dy). Returns (foot_x, foot_y, t)."""
    dd = dx * dx + dy * dy
    if dd < _EPS:
        return x0, y0, 0.0
    t = _sv_dot(px - x0, py - y0, dx, dy) / dd
    return x0 + t * dx, y0 + t * dy, t

def _sv_intersect_lines(px, py, ux, uy, qx, qy, vx, vy):
    """Intersect ray (p+t*u) with ray (q+s*v). Returns (x,y) or None."""
    den = _sv_cross(ux, uy, vx, vy)
    if abs(den) < _EPS:
        return None
    t = _sv_cross(qx - px, qy - py, vx, vy) / den
    return px + ux * t, py + uy * t

def _sv_arc_pts(cx, cy, r, a_start, a_end, ccw=True, n=12):
    """Tessellate arc into n+1 points. ccw=True goes counter-clockwise."""
    pts = []
    if ccw:
        while a_end <= a_start:
            a_end += 2 * math.pi
    else:
        while a_end >= a_start:
            a_end -= 2 * math.pi
    for k in range(n + 1):
        a = a_start + (a_end - a_start) * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

def _sv_fillet_arc_pts(cx, cy, r, a1, a2, n=8):
    """Tessellate a fillet arc — always takes the SHORT path between a1 and a2."""
    diff = (a2 - a1) % (2 * math.pi)
    if diff > math.pi:
        diff -= 2 * math.pi   # flip to short arc
    pts = []
    for k in range(n + 1):
        a = a1 + diff * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

def _sv_line_circle_fillet(x0, y0, dx, dy, ccx, ccy, cr, fr, internal, inward_nx, inward_ny, prefer_high_t):
    """
    Find fillet circle of radius fr tangent to line (x0,y0)+(dx,dy) and circle (ccx,ccy,cr).
    internal=True: fillet inside target circle (hub side, d=cr+fr).
    internal=False: fillet outside target circle (rim side, d=cr-fr).
    Returns (fc_x, fc_y, tl_x, tl_y, tc_x, tc_y, s) or None.
    """
    ux, uy = _sv_unit(dx, dy)
    nx, ny = -uy, ux   # left normal of line direction

    d = (cr + fr) if internal else (cr - fr)
    if d <= 0:
        return None

    best = None
    best_score = 1e18
    for sign in (1.0, -1.0):
        # Offset line inward by ±fr
        ox, oy = x0 + nx * sign * fr, y0 + ny * sign * fr
        # Intersect offset line with circle(ccc, d)
        wx, wy = ox - ccx, oy - ccy
        b = 2.0 * _sv_dot(ux, uy, wx, wy)
        c = wx * wx + wy * wy - d * d
        disc = b * b - 4.0 * c
        if disc < -_EPS:
            continue
        disc = max(0.0, disc)
        sq = math.sqrt(disc)
        for t_sol in ((-b + sq) / 2.0, (-b - sq) / 2.0):
            fcx, fcy = ox + ux * t_sol, oy + uy * t_sol
            tlx, tly, s = _sv_project(fcx, fcy, x0, y0, dx, dy)
            # Tangent point on target circle
            rdx, rdy = fcx - ccx, fcy - ccy
            rn = math.hypot(rdx, rdy)
            if rn < _EPS:
                continue
            tcx = ccx + rdx / rn * cr
            tcy = ccy + rdy / rn * cr
            # s must be in [0,1]
            if s < -1e-3 or s > 1.0 + 1e-3:
                continue
            # Fillet center must be on void interior side
            if _sv_dot(fcx - x0, fcy - y0, inward_nx, inward_ny) < -1e-6:
                continue
            if prefer_high_t and s < 0.35:
                continue
            if (not prefer_high_t) and s > 0.65:
                continue
            score = math.hypot(tlx - x0, tly - y0) + math.hypot(tcx - x0, tcy - y0)
            if score < best_score:
                best_score = score
                best = (fcx, fcy, tlx, tly, tcx, tcy, s)
    return best

def _sv_line_line_fillet(rx0, ry0, rdx, rdy, lx0, ly0, ldx, ldy,
                          in_rx, in_ry, in_lx, in_ly, fr):
    """
    Fillet circle of radius fr tangent to two lines (fallback for hub-overlap base).
    Returns (fcx, fcy, tr_x, tr_y, tl_x, tl_y) or None.
    """
    rux, ruy = _sv_unit(rdx, rdy)
    lux, luy = _sv_unit(ldx, ldy)
    prx, pry = rx0 + in_rx * fr, ry0 + in_ry * fr
    plx, ply = lx0 + in_lx * fr, ly0 + in_ly * fr
    fc = _sv_intersect_lines(prx, pry, rux, ruy, plx, ply, lux, luy)
    if fc is None:
        return None
    fcx, fcy = fc
    trx, try_, _ = _sv_project(fcx, fcy, rx0, ry0, rdx, rdy)
    tlx, tly, _  = _sv_project(fcx, fcy, lx0, ly0, ldx, ldy)
    return fcx, fcy, trx, try_, tlx, tly


def _spoke_void_polygons(R_hub, R_rim_inner, spoke_count, spoke_width_mm,
                         fillet_tip_mm=0.0, fillet_base_mm=0.0, n_arc=16):
    """
    Return one point-list polygon per gap between spokes.
    Tip fillets tangent to spoke wall + inner rim (2 per void).
    Base fillets tangent to spoke wall + hub circle, or spoke-to-spoke if overlapping.
    """
    if spoke_count <= 0 or spoke_width_mm <= 0.0 or R_rim_inner <= R_hub + 0.5:
        return []

    half_w = min(spoke_width_mm / 2.0, R_rim_inner * 0.45)
    if half_w <= 0.0:
        return []

    theta_step = 2.0 * math.pi / spoke_count
    theta_hub  = math.asin(min(half_w / R_hub,        1.0))
    theta_rim  = math.asin(min(half_w / R_rim_inner, 0.9999))
    hub_overlap = theta_hub >= theta_step / 2.0

    result = []
    for i in range(spoke_count):
        # Mid-angle of this void gap
        theta_mid = (i + 0.5) * theta_step

        # Gap half-angles at each radius
        spoke_half_hub = theta_hub
        spoke_half_rim = theta_rim
        gap_half_hub = max(_EPS, theta_step / 2.0 - spoke_half_hub)
        gap_half_rim = max(_EPS, theta_step / 2.0 - spoke_half_rim)

        # Four corners of the raw void (before fillets)
        # LB = left-bottom (hub), LT = left-top (rim), RT = right-top, RB = right-bottom
        if not hub_overlap:
            lb_x = R_hub       * math.cos(theta_mid - gap_half_hub)
            lb_y = R_hub       * math.sin(theta_mid - gap_half_hub)
            rb_x = R_hub       * math.cos(theta_mid + gap_half_hub)
            rb_y = R_hub       * math.sin(theta_mid + gap_half_hub)
        # rim corners always exist
        lt_x = R_rim_inner * math.cos(theta_mid - gap_half_rim)
        lt_y = R_rim_inner * math.sin(theta_mid - gap_half_rim)
        rt_x = R_rim_inner * math.cos(theta_mid + gap_half_rim)
        rt_y = R_rim_inner * math.sin(theta_mid + gap_half_rim)

        # Spoke wall directions: left wall goes lb→lt, right wall goes rb→rt
        if not hub_overlap:
            l_dx, l_dy = lt_x - lb_x, lt_y - lb_y
            r_dx, r_dy = rt_x - rb_x, rt_y - rb_y
        else:
            # Hub overlap: walls originate from a merged intersection point.
            # Estimate by projecting from hub centre outward along gap edge angles.
            l_dx = lt_x - R_hub * math.cos(theta_mid - gap_half_hub)
            l_dy = lt_y - R_hub * math.sin(theta_mid - gap_half_hub)
            r_dx = rt_x - R_hub * math.cos(theta_mid + gap_half_hub)
            r_dy = rt_y - R_hub * math.sin(theta_mid + gap_half_hub)
            lb_x = R_hub * math.cos(theta_mid - gap_half_hub)
            lb_y = R_hub * math.sin(theta_mid - gap_half_hub)
            rb_x = R_hub * math.cos(theta_mid + gap_half_hub)
            rb_y = R_hub * math.sin(theta_mid + gap_half_hub)

        # Inward normals (toward void interior)
        probe_x = (R_hub + R_rim_inner) * 0.5 * math.cos(theta_mid)
        probe_y = (R_hub + R_rim_inner) * 0.5 * math.sin(theta_mid)
        lux, luy = _sv_unit(l_dx, l_dy)
        ln_a, ln_b = -luy, lux
        in_lx = ln_a if _sv_dot(probe_x - lb_x, probe_y - lb_y, ln_a, ln_b) > 0 else -ln_a
        in_ly = ln_b if _sv_dot(probe_x - lb_x, probe_y - lb_y, ln_a, ln_b) > 0 else -ln_b

        rux, ruy = _sv_unit(r_dx, r_dy)
        rn_a, rn_b = -ruy, rux
        in_rx = rn_a if _sv_dot(probe_x - rb_x, probe_y - rb_y, rn_a, rn_b) > 0 else -rn_a
        in_ry = rn_b if _sv_dot(probe_x - rb_x, probe_y - rb_y, rn_a, rn_b) > 0 else -rn_b

        # ── Tip fillets (spoke wall meets inner rim) ──────────────────────────
        tip_l = _sv_line_circle_fillet(lb_x, lb_y, l_dx, l_dy,
                                        0.0, 0.0, R_rim_inner, fillet_tip_mm,
                                        False, in_lx, in_ly, prefer_high_t=True)
        tip_r = _sv_line_circle_fillet(rb_x, rb_y, r_dx, r_dy,
                                        0.0, 0.0, R_rim_inner, fillet_tip_mm,
                                        False, in_rx, in_ry, prefer_high_t=True)

        # ── Base fillets ─────────────────────────────────────────────────────
        # PRIMARY: line-line fillet tangent to both spoke walls (no hub tangency).
        # FALLBACK: hub-tangent when the line-line arc's inner edge dips below hub.
        ll_base = _sv_line_line_fillet(rb_x, rb_y, r_dx, r_dy,
                                        lb_x, lb_y, l_dx, l_dy,
                                        in_rx, in_ry, in_lx, in_ly, fillet_base_mm)
        use_hub_base = (ll_base is None or
                        math.hypot(ll_base[0], ll_base[1]) - fillet_base_mm < R_hub)
        if use_hub_base:
            base_l = _sv_line_circle_fillet(lb_x, lb_y, l_dx, l_dy,
                                             0.0, 0.0, R_hub, fillet_base_mm,
                                             True, in_lx, in_ly, prefer_high_t=False)
            base_r = _sv_line_circle_fillet(rb_x, rb_y, r_dx, r_dy,
                                             0.0, 0.0, R_hub, fillet_base_mm,
                                             True, in_rx, in_ry, prefer_high_t=False)
        else:
            base_l = base_r = None

        # ── Build polygon point list ──────────────────────────────────────────
        pts = []

        # Rim arc from left-tip to right-tip (short CCW arc across the gap)
        a_rim_l = math.atan2(tip_l[5], tip_l[4]) if tip_l else math.atan2(lt_y, lt_x)
        a_rim_r = math.atan2(tip_r[5], tip_r[4]) if tip_r else math.atan2(rt_y, rt_x)

        pts += _sv_fillet_arc_pts(0.0, 0.0, R_rim_inner, a_rim_l, a_rim_r, n=n_arc)

        # Right tip fillet arc (rim tangency → spoke wall tangency)
        if tip_r:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_r
            a1 = math.atan2(tcy - fcy, tcx - fcx)
            a2 = math.atan2(tly - fcy, tlx - fcx)
            pts += _sv_fillet_arc_pts(fcx, fcy, fillet_tip_mm, a1, a2, n=8)
        else:
            pts.append((rt_x, rt_y))

        # Right spoke wall down to base, then across, then left spoke wall up
        if use_hub_base:
            # Hub-tangent base fillets: right wall → hub arc → left wall
            if base_r:
                fcx, fcy, tl_x, tl_y, tc_x, tc_y, _ = base_r
                pts.append((tl_x, tl_y))
                a1 = math.atan2(tl_y - fcy, tl_x - fcx)
                a2 = math.atan2(tc_y - fcy, tc_x - fcx)
                pts += _sv_fillet_arc_pts(fcx, fcy, fillet_base_mm, a1, a2, n=8)
                a_hr = math.atan2(tc_y, tc_x)
            else:
                pts.append((rb_x, rb_y))
                a_hr = math.atan2(rb_y, rb_x)
            if base_l:
                fcx2, fcy2, tl_x2, tl_y2, tc_x2, tc_y2, _ = base_l
                a_hl = math.atan2(tc_y2, tc_x2)
                pts += _sv_fillet_arc_pts(0.0, 0.0, R_hub, a_hr, a_hl, n=n_arc)
                a1 = math.atan2(tc_y2 - fcy2, tc_x2 - fcx2)
                a2 = math.atan2(tl_y2 - fcy2, tl_x2 - fcx2)
                pts += _sv_fillet_arc_pts(fcx2, fcy2, fillet_base_mm, a1, a2, n=8)
            else:
                a_hl = math.atan2(lb_y, lb_x)
                pts += _sv_fillet_arc_pts(0.0, 0.0, R_hub, a_hr, a_hl, n=n_arc)
                pts.append((lb_x, lb_y))
        else:
            # Line-line base fillet: single arc tangent to both spoke walls.
            if ll_base and fillet_base_mm > 0.05:
                fcx, fcy, trx, try_, tlx, tly = ll_base
                pts.append((trx, try_))
                a1 = math.atan2(try_ - fcy, trx - fcx)
                a2 = math.atan2(tly  - fcy, tlx - fcx)
                pts += _sv_fillet_arc_pts(fcx, fcy, fillet_base_mm, a1, a2, n=8)
            else:
                # No fillet: straight walls down to hub arc
                pts.append((rb_x, rb_y))
                a_hr = math.atan2(rb_y, rb_x)
                a_hl = math.atan2(lb_y, lb_x)
                pts += _sv_fillet_arc_pts(0.0, 0.0, R_hub, a_hr, a_hl, n=n_arc)
                pts.append((lb_x, lb_y))

        # Left tip fillet arc (spoke wall tangency → rim tangency)
        if tip_l:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_l
            a1 = math.atan2(tly - fcy, tlx - fcx)
            a2 = math.atan2(tcy - fcy, tcx - fcx)
            pts += _sv_fillet_arc_pts(fcx, fcy, fillet_tip_mm, a1, a2, n=8)
        else:
            pts.append((lt_x, lt_y))

        result.append(pts)

    return result


def _spoke_void_segments(R_hub, R_rim_inner, spoke_count, spoke_width_mm,
                          fillet_tip_mm=0.0, fillet_base_mm=0.0):
    """
    Same geometry as _spoke_void_polygons but returns typed geometric primitives
    instead of tessellated point lists.

    Each void is a list of segments:
        ('arc',  cx, cy, r, a1_rad, a2_rad)  — short arc from a1 to a2
        ('line', x1, y1, x2, y2)

    All coordinates are in standard math convention (x=r·cos θ, y=r·sin θ).
    Callers must apply any coordinate-system conversion before writing to DXF/SVG.
    """
    if spoke_count <= 0 or spoke_width_mm <= 0.0 or R_rim_inner <= R_hub + 0.5:
        return []

    half_w = min(spoke_width_mm / 2.0, R_rim_inner * 0.45)
    if half_w <= 0.0:
        return []

    theta_step  = 2.0 * math.pi / spoke_count
    theta_hub   = math.asin(min(half_w / R_hub,        1.0))
    theta_rim   = math.asin(min(half_w / R_rim_inner, 0.9999))
    hub_overlap = theta_hub >= theta_step / 2.0

    result = []
    for i in range(spoke_count):
        theta_mid    = (i + 0.5) * theta_step
        gap_half_hub = max(_EPS, theta_step / 2.0 - theta_hub)
        gap_half_rim = max(_EPS, theta_step / 2.0 - theta_rim)

        lb_x = R_hub       * math.cos(theta_mid - gap_half_hub)
        lb_y = R_hub       * math.sin(theta_mid - gap_half_hub)
        rb_x = R_hub       * math.cos(theta_mid + gap_half_hub)
        rb_y = R_hub       * math.sin(theta_mid + gap_half_hub)
        lt_x = R_rim_inner * math.cos(theta_mid - gap_half_rim)
        lt_y = R_rim_inner * math.sin(theta_mid - gap_half_rim)
        rt_x = R_rim_inner * math.cos(theta_mid + gap_half_rim)
        rt_y = R_rim_inner * math.sin(theta_mid + gap_half_rim)

        if not hub_overlap:
            l_dx, l_dy = lt_x - lb_x, lt_y - lb_y
            r_dx, r_dy = rt_x - rb_x, rt_y - rb_y
        else:
            l_dx = lt_x - R_hub * math.cos(theta_mid - gap_half_hub)
            l_dy = lt_y - R_hub * math.sin(theta_mid - gap_half_hub)
            r_dx = rt_x - R_hub * math.cos(theta_mid + gap_half_hub)
            r_dy = rt_y - R_hub * math.sin(theta_mid + gap_half_hub)

        probe_x = (R_hub + R_rim_inner) * 0.5 * math.cos(theta_mid)
        probe_y = (R_hub + R_rim_inner) * 0.5 * math.sin(theta_mid)
        lux, luy = _sv_unit(l_dx, l_dy)
        ln_a, ln_b = -luy, lux
        in_lx = ln_a if _sv_dot(probe_x - lb_x, probe_y - lb_y, ln_a, ln_b) > 0 else -ln_a
        in_ly = ln_b if _sv_dot(probe_x - lb_x, probe_y - lb_y, ln_a, ln_b) > 0 else -ln_b
        rux, ruy = _sv_unit(r_dx, r_dy)
        rn_a, rn_b = -ruy, rux
        in_rx = rn_a if _sv_dot(probe_x - rb_x, probe_y - rb_y, rn_a, rn_b) > 0 else -rn_a
        in_ry = rn_b if _sv_dot(probe_x - rb_x, probe_y - rb_y, rn_a, rn_b) > 0 else -rn_b

        tip_l = _sv_line_circle_fillet(lb_x, lb_y, l_dx, l_dy, 0.0, 0.0, R_rim_inner,
                                        fillet_tip_mm, False, in_lx, in_ly, prefer_high_t=True)
        tip_r = _sv_line_circle_fillet(rb_x, rb_y, r_dx, r_dy, 0.0, 0.0, R_rim_inner,
                                        fillet_tip_mm, False, in_rx, in_ry, prefer_high_t=True)

        # PRIMARY: line-line fillet; FALLBACK: hub-tangent when arc dips below hub.
        ll_base = _sv_line_line_fillet(rb_x, rb_y, r_dx, r_dy,
                                        lb_x, lb_y, l_dx, l_dy,
                                        in_rx, in_ry, in_lx, in_ly, fillet_base_mm)
        use_hub_base = (ll_base is None or
                        math.hypot(ll_base[0], ll_base[1]) - fillet_base_mm < R_hub)
        if use_hub_base:
            base_l = _sv_line_circle_fillet(lb_x, lb_y, l_dx, l_dy, 0.0, 0.0, R_hub,
                                             fillet_base_mm, True, in_lx, in_ly, prefer_high_t=False)
            base_r = _sv_line_circle_fillet(rb_x, rb_y, r_dx, r_dy, 0.0, 0.0, R_hub,
                                             fillet_base_mm, True, in_rx, in_ry, prefer_high_t=False)
        else:
            base_l = base_r = None

        segs = []

        # ── Rim arc (left tip tangency → right tip tangency, short CCW) ──────
        a_rim_l = math.atan2(tip_l[5], tip_l[4]) if tip_l else math.atan2(lt_y, lt_x)
        a_rim_r = math.atan2(tip_r[5], tip_r[4]) if tip_r else math.atan2(rt_y, rt_x)
        segs.append(('arc', 0.0, 0.0, R_rim_inner, a_rim_l, a_rim_r))

        # ── Right tip fillet (rim tangency → spoke-wall tangency) ────────────
        if tip_r:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_r
            segs.append(('arc', fcx, fcy, fillet_tip_mm,
                          math.atan2(tcy - fcy, tcx - fcx),
                          math.atan2(tly - fcy, tlx - fcx)))
            wall_r_start = (tlx, tly)
        else:
            wall_r_start = (rt_x, rt_y)

        # ── Right spoke wall (line) ───────────────────────────────────────────
        if use_hub_base and base_r:
            _, _, tl_x, tl_y, tc_x, tc_y, _ = base_r
            wall_r_end = (tl_x, tl_y)
        elif ll_base and fillet_base_mm > 0.05:
            wall_r_end = (ll_base[2], ll_base[3])
        else:
            wall_r_end = (rb_x, rb_y)
        segs.append(('line', wall_r_start[0], wall_r_start[1],
                              wall_r_end[0],   wall_r_end[1]))

        # ── Base section (hub arc ± fillets or line-line fillet) ──────────────
        if use_hub_base:
            if base_r and base_l:
                fcx,  fcy,  tl_x,  tl_y,  tc_x,  tc_y,  _ = base_r
                fcx2, fcy2, tl_x2, tl_y2, tc_x2, tc_y2, _ = base_l
                segs.append(('arc', fcx, fcy, fillet_base_mm,
                              math.atan2(tl_y  - fcy,  tl_x  - fcx),
                              math.atan2(tc_y  - fcy,  tc_x  - fcx)))
                segs.append(('arc', 0.0, 0.0, R_hub,
                              math.atan2(tc_y, tc_x), math.atan2(tc_y2, tc_x2)))
                segs.append(('arc', fcx2, fcy2, fillet_base_mm,
                              math.atan2(tc_y2 - fcy2, tc_x2 - fcx2),
                              math.atan2(tl_y2 - fcy2, tl_x2 - fcx2)))
                wall_l_start = (tl_x2, tl_y2)
            else:
                segs.append(('arc', 0.0, 0.0, R_hub,
                              math.atan2(rb_y, rb_x), math.atan2(lb_y, lb_x)))
                wall_l_start = (lb_x, lb_y)
        else:
            if ll_base and fillet_base_mm > 0.05:
                fcx, fcy, trx, try_, tlx, tly = ll_base
                segs.append(('arc', fcx, fcy, fillet_base_mm,
                              math.atan2(try_ - fcy, trx - fcx),
                              math.atan2(tly  - fcy, tlx - fcx)))
                wall_l_start = (tlx, tly)
            else:
                segs.append(('arc', 0.0, 0.0, R_hub,
                              math.atan2(rb_y, rb_x), math.atan2(lb_y, lb_x)))
                wall_l_start = (lb_x, lb_y)

        # ── Left spoke wall (line) ────────────────────────────────────────────
        if tip_l:
            _, _, tlx, tly, _, _, _ = tip_l
            wall_l_end = (tlx, tly)
        else:
            wall_l_end = (lt_x, lt_y)
        segs.append(('line', wall_l_start[0], wall_l_start[1],
                              wall_l_end[0],   wall_l_end[1]))

        # ── Left tip fillet (spoke-wall tangency → rim tangency) ─────────────
        if tip_l:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_l
            segs.append(('arc', fcx, fcy, fillet_tip_mm,
                          math.atan2(tly - fcy, tlx - fcx),
                          math.atan2(tcy - fcy, tcx - fcx)))

        result.append(segs)

    return result


def _svg_to_png(svg_str: str, size_px: int) -> bytes:
    """Rasterise an SVG string to PNG bytes at size_px wide."""
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg_str.encode('utf-8'), output_width=size_px)
    except (ImportError, OSError):
        return None   # caller falls back to legacy


_CAIROSVG_AVAILABLE = None   # cached after first attempt

def _check_cairosvg() -> bool:
    global _CAIROSVG_AVAILABLE
    if _CAIROSVG_AVAILABLE is None:
        try:
            import cairosvg  # noqa
            _CAIROSVG_AVAILABLE = True
        except (ImportError, OSError):
            _CAIROSVG_AVAILABLE = False
    return _CAIROSVG_AVAILABLE


def generate_png(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 2.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    size_px: int = 500,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """Rasterise the SVG export to PNG.  Falls back to legacy Pillow renderer if Cairo unavailable."""
    if _check_cairosvg():
        from exporters.svg_exporter import generate_svg
        svg = generate_svg(
            family=family, pitch=pitch, num_teeth=num_teeth,
            bore_mm=bore_mm, clearance_mm=clearance_mm, backlash_mm=backlash_mm,
            print_extra_mm=print_extra_mm,
            spoke_count=spoke_count, spoke_width_mm=spoke_width_mm,
            spoke_hub_od_mm=spoke_hub_od_mm, rim_depth_mm=rim_depth_mm,
            fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm,
            include_data=False,
            flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
        )
        return _svg_to_png(svg, size_px)
    return _generate_png_legacy(
        family=family, pitch=pitch, num_teeth=num_teeth,
        bore_mm=bore_mm, clearance_mm=clearance_mm, backlash_mm=backlash_mm,
        print_extra_mm=print_extra_mm, spoke_count=spoke_count,
        spoke_width_mm=spoke_width_mm, spoke_hub_od_mm=spoke_hub_od_mm,
        rim_depth_mm=rim_depth_mm, fillet_tip_mm=fillet_tip_mm,
        fillet_base_mm=fillet_base_mm, size_px=size_px,
        bg_color=bg_color, groove_color=groove_color, bore_color=bore_color,
        flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
    )


def _generate_png_legacy(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 2.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    size_px: int = 500,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """Legacy Pillow-based renderer — kept for reference."""
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

    # ── Bore polygon (circle, D-flat, or keyway) ─────────────────────────────
    BORE_SAMPLES = max(64, num_teeth * 4)
    bore_px = []
    if R_bore > 0:
        if flat_depth_mm > 0.0 or (keyway_w_mm > 0.0 and keyway_h_mm > 0.0):
            from exporters.step_exporter import _build_bore_2d
            _bp = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            if _bp is not None:
                bore_px = [to_px(x, y) for x, y in list(_bp.exterior.coords)[:-1]]
        if not bore_px:
            for i in range(BORE_SAMPLES):
                a = 2.0 * math.pi * i / BORE_SAMPLES
                bore_px.append(to_px(R_bore * math.sin(a), R_bore * math.cos(a)))

    # ── Spoke void polygons (mm) ─────────────────────────────────────────────
    spoke_void_px = []
    hub_px = []
    if spoke_count >= 2 and spoke_width_mm > 0.0:
        R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
        R_hub_spoke  = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (R_bore + 1.0)
        R_rim_inner  = max(R_hub_spoke + 0.5, R_tooth_root - rim_depth_mm)
        for void_pts in _spoke_void_polygons(
                R_hub_spoke, R_rim_inner, spoke_count, spoke_width_mm,
                fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm):
            spoke_void_px.append([to_px(x, y) for x, y in void_pts])
        # Hub circle
        for i in range(BORE_SAMPLES):
            a = 2.0 * math.pi * i / BORE_SAMPLES
            hub_px.append(to_px(R_hub_spoke * math.sin(a), R_hub_spoke * math.cos(a)))

    # ── Draw ──────────────────────────────────────────────────────────────────
    # Supersample for anti-aliasing: render at 2× then downsample
    SS = 2
    render_size = size_px * SS
    img  = Image.new('RGB', (render_size, render_size), bg_color)
    draw = ImageDraw.Draw(img)

    def ss(pts):
        return [(x * SS, y * SS) for x, y in pts]

    line_w = max(1, int(scale * SS * 0.28))   # ~0.28 mm stroke

    PULLEY_FILL   = (203, 213, 225)
    PULLEY_STROKE = (51,  65,  85)

    ss_poly  = ss(poly_px)
    ss_bore  = ss(bore_px)

    # Fill pulley body
    draw.polygon(ss_poly, fill=PULLEY_FILL)

    # Punch spoke voids
    for svp in spoke_void_px:
        draw.polygon(ss(svp), fill=bg_color)

    # Punch bore
    if R_bore > 0 and len(ss_bore) > 2:
        draw.polygon(ss_bore, fill=bg_color)

    # Redraw outline on top
    draw.line(ss_poly + [ss_poly[0]], fill=PULLEY_STROKE, width=line_w, joint='curve')

    if R_bore > 0 and len(ss_bore) > 2:
        draw.line(ss_bore + [ss_bore[0]], fill=bore_color, width=line_w, joint='curve')

    if hub_px:
        ss_hub = ss(hub_px)
        draw.line(ss_hub + [ss_hub[0]], fill=PULLEY_STROKE, width=line_w, joint='curve')

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
    spoke_count1: int = 0,
    spoke_width_mm1: float = 0.0,
    spoke_hub_od_mm1: float = 0.0,
    rim_depth_mm1: float = 2.0,
    fillet_tip_mm1: float = 0.0,
    fillet_base_mm1: float = 0.0,
    spoke_count2: int = 0,
    spoke_width_mm2: float = 0.0,
    spoke_hub_od_mm2: float = 0.0,
    rim_depth_mm2: float = 2.0,
    fillet_tip_mm2: float = 0.0,
    fillet_base_mm2: float = 0.0,
    size_px: int = 480,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
    flat_depth_mm1: float = 0.0,
    keyway_w_mm1: float = 0.0,
    keyway_h_mm1: float = 0.0,
    flat_depth_mm2: float = 0.0,
    keyway_w_mm2: float = 0.0,
    keyway_h_mm2: float = 0.0,
) -> bytes:
    """Rasterise the dual SVG export to PNG.  Falls back to legacy Pillow renderer if Cairo unavailable."""
    if _check_cairosvg():
        from exporters.svg_exporter import generate_svg_dual
        svg = generate_svg_dual(
            family=family, pitch=pitch,
            num_teeth1=num_teeth1, bore_mm1=bore_mm1,
            clearance_mm1=clearance_mm1, backlash_mm1=backlash_mm1,
            print_extra_mm1=print_extra_mm1,
            num_teeth2=num_teeth2, bore_mm2=bore_mm2,
            clearance_mm2=clearance_mm2, backlash_mm2=backlash_mm2,
            print_extra_mm2=print_extra_mm2,
            center_dist_mm=center_dist_mm,
            spoke_count1=spoke_count1, spoke_width_mm1=spoke_width_mm1,
            spoke_hub_od_mm1=spoke_hub_od_mm1, rim_depth_mm1=rim_depth_mm1,
            fillet_tip_mm1=fillet_tip_mm1, fillet_base_mm1=fillet_base_mm1,
            spoke_count2=spoke_count2, spoke_width_mm2=spoke_width_mm2,
            spoke_hub_od_mm2=spoke_hub_od_mm2, rim_depth_mm2=rim_depth_mm2,
            fillet_tip_mm2=fillet_tip_mm2, fillet_base_mm2=fillet_base_mm2,
            include_data=False,
            flat_depth_mm1=flat_depth_mm1, keyway_w_mm1=keyway_w_mm1, keyway_h_mm1=keyway_h_mm1,
            flat_depth_mm2=flat_depth_mm2, keyway_w_mm2=keyway_w_mm2, keyway_h_mm2=keyway_h_mm2,
        )
        return _svg_to_png(svg, size_px)
    return _generate_png_dual_legacy(
        family=family, pitch=pitch,
        num_teeth1=num_teeth1, bore_mm1=bore_mm1,
        clearance_mm1=clearance_mm1, backlash_mm1=backlash_mm1,
        print_extra_mm1=print_extra_mm1,
        num_teeth2=num_teeth2, bore_mm2=bore_mm2,
        clearance_mm2=clearance_mm2, backlash_mm2=backlash_mm2,
        print_extra_mm2=print_extra_mm2,
        center_dist_mm=center_dist_mm,
        spoke_count1=spoke_count1, spoke_width_mm1=spoke_width_mm1,
        spoke_hub_od_mm1=spoke_hub_od_mm1, rim_depth_mm1=rim_depth_mm1,
        fillet_tip_mm1=fillet_tip_mm1, fillet_base_mm1=fillet_base_mm1,
        spoke_count2=spoke_count2, spoke_width_mm2=spoke_width_mm2,
        spoke_hub_od_mm2=spoke_hub_od_mm2, rim_depth_mm2=rim_depth_mm2,
        fillet_tip_mm2=fillet_tip_mm2, fillet_base_mm2=fillet_base_mm2,
        size_px=size_px, bg_color=bg_color,
        groove_color=groove_color, bore_color=bore_color,
        flat_depth_mm1=flat_depth_mm1, keyway_w_mm1=keyway_w_mm1, keyway_h_mm1=keyway_h_mm1,
        flat_depth_mm2=flat_depth_mm2, keyway_w_mm2=keyway_w_mm2, keyway_h_mm2=keyway_h_mm2,
    )


def _generate_png_dual_legacy(
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
    spoke_count1: int = 0,
    spoke_width_mm1: float = 0.0,
    spoke_hub_od_mm1: float = 0.0,
    rim_depth_mm1: float = 2.0,
    fillet_tip_mm1: float = 0.0,
    fillet_base_mm1: float = 0.0,
    spoke_count2: int = 0,
    spoke_width_mm2: float = 0.0,
    spoke_hub_od_mm2: float = 0.0,
    rim_depth_mm2: float = 2.0,
    fillet_tip_mm2: float = 0.0,
    fillet_base_mm2: float = 0.0,
    size_px: int = 480,
    bg_color=(250, 251, 252),
    groove_color=(26, 26, 26),
    bore_color=(26, 26, 26),
    flat_depth_mm1: float = 0.0,
    keyway_w_mm1: float = 0.0,
    keyway_h_mm1: float = 0.0,
    flat_depth_mm2: float = 0.0,
    keyway_w_mm2: float = 0.0,
    keyway_h_mm2: float = 0.0,
) -> bytes:
    """Legacy Pillow-based dual renderer — kept for reference."""
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

    # ── Spoke voids (precompute in mm, relative to each pulley centre) ───────
    def _compute_spoke_voids(wrapped_pts, R_OD, bore_mm,
                             spoke_count, spoke_width_mm, spoke_hub_od_mm, rim_depth_mm,
                             fillet_tip_mm, fillet_base_mm):
        if spoke_count < 2 or spoke_width_mm <= 0.0:
            return [], 0.0
        R_tooth_root = min(math.hypot(x, y) for x, y in wrapped_pts) if wrapped_pts else R_OD
        R_hub_s = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        R_rim_i = max(R_hub_s + 0.5, R_tooth_root - rim_depth_mm)
        voids = _spoke_void_polygons(R_hub_s, R_rim_i, spoke_count, spoke_width_mm,
                                     fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm)
        return voids, R_hub_s

    spoke_voids1, R_hub1 = _compute_spoke_voids(
        wrapped1, R_OD1, bore_mm1, spoke_count1, spoke_width_mm1, spoke_hub_od_mm1, rim_depth_mm1,
        fillet_tip_mm1, fillet_base_mm1)
    spoke_voids2, R_hub2 = _compute_spoke_voids(
        wrapped2, R_OD2, bore_mm2, spoke_count2, spoke_width_mm2, spoke_hub_od_mm2, rim_depth_mm2,
        fillet_tip_mm2, fillet_base_mm2)

    # ── Pulleys (on top of belt) ──────────────────────────────────────────────
    PULLEY_FILL   = (203, 213, 225)   # slate-300
    PULLEY_STROKE = (51,  65,  85)    # slate-700

    for pts, cx_off, svoids in (
        (poly1, cx1, spoke_voids1),
        (poly2, cx2, spoke_voids2),
    ):
        sp = ss_pts(pts)
        draw.polygon(sp, fill=PULLEY_FILL)
        for void_pts in svoids:
            sv = ss_pts([(x + cx_off, y) for x, y in void_pts])
            draw.polygon(sv, fill=bg_color)
        draw.line(sp + [sp[0]], fill=PULLEY_STROKE, width=line_w, joint='curve')

    BORE_SAMPLES_D = max(64, 4 * max(num_teeth1, num_teeth2))
    for bore_mm, flat_depth, kw_w, kw_h, cx_off in (
        (bore_mm1, flat_depth_mm1, keyway_w_mm1, keyway_h_mm1, cx1),
        (bore_mm2, flat_depth_mm2, keyway_w_mm2, keyway_h_mm2, cx2),
    ):
        R_bore = bore_mm / 2.0
        if R_bore <= 0:
            continue
        bore_pts_mm = []
        if flat_depth > 0.0 or (kw_w > 0.0 and kw_h > 0.0):
            from exporters.step_exporter import _build_bore_2d
            _bp = _build_bore_2d(bore_mm, flat_depth, kw_w, kw_h)
            if _bp is not None:
                bore_pts_mm = [(x + cx_off, y) for x, y in list(_bp.exterior.coords)[:-1]]
        if not bore_pts_mm:
            bore_pts_mm = [(R_bore * math.sin(a) + cx_off, R_bore * math.cos(a))
                           for a in (2.0 * math.pi * i / BORE_SAMPLES_D for i in range(BORE_SAMPLES_D))]
        sp = ss_pts(bore_pts_mm)
        if len(sp) > 2:
            draw.polygon(sp, fill=bg_color)
            draw.line(sp + [sp[0]], fill=bore_color, width=line_w, joint='curve')

    # Hub circles
    for R_hub, cx_off in ((R_hub1, cx1), (R_hub2, cx2)):
        if R_hub > 0:
            hp = bore_poly(R_hub, cx_off, 0)
            sh = ss_pts(hp)
            if len(sh) > 2:
                draw.line(sh + [sh[0]], fill=PULLEY_STROKE, width=line_w, joint='curve')

    img = img.resize((img_w, img_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.read()
