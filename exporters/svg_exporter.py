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
from exporters.png_exporter import _spoke_void_polygons

_POLYLINE_FAMILIES = {'Imperial', 'T', 'AT'}

# ── Spoke void SVG path helpers ───────────────────────────────────────────────
# Ported from static/generate_spoked_pulley_svg.py.
# All geometry in standard Cartesian (x=r·cos θ, y=r·sin θ), center at (cx,cy).

_SV_EPS = 1e-9

def _sv2_dot(ax, ay, bx, by): return ax*bx + ay*by
def _sv2_cross(ax, ay, bx, by): return ax*by - ay*bx
def _sv2_unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx/n, dy/n) if n > _SV_EPS else (1.0, 0.0)

def _sv2_project(px, py, x0, y0, dx, dy):
    dd = dx*dx + dy*dy
    if dd < _SV_EPS: return x0, y0, 0.0
    t = _sv2_dot(px-x0, py-y0, dx, dy) / dd
    return x0 + t*dx, y0 + t*dy, t

def _sv2_intersect(px, py, ux, uy, qx, qy, vx, vy):
    den = _sv2_cross(ux, uy, vx, vy)
    if abs(den) < _SV_EPS: return None
    t = _sv2_cross(qx-px, qy-py, vx, vy) / den
    return px + ux*t, py + uy*t

def _sv2_arc_flags(cx, cy, sx, sy, ex, ey, cw):
    import math as _m
    a0 = _m.atan2(sy-cy, sx-cx)
    a1 = _m.atan2(ey-cy, ex-cx)
    if cw:
        delta = (a0 - a1) % (2*_m.pi)
    else:
        delta = (a1 - a0) % (2*_m.pi)
    large  = 1 if delta > _m.pi else 0
    sweep  = 1 if cw else 0
    return large, sweep

def _sv2_line_circle_fillet(x0, y0, dx, dy, ccx, ccy, cr, fr,
                             external, inward_nx, inward_ny, prefer_high_t):
    """
    Fillet circle of radius fr tangent to line and circle.
    external=True: fillet outside circle (d = cr+fr) — for hub base fillets.
    external=False: fillet inside circle (d = cr-fr) — for rim tip fillets.
    Returns (fcx,fcy, tlx,tly, tcx,tcy, s) or None.
    """
    ux, uy = _sv2_unit(dx, dy)
    nx, ny = -uy, ux
    d = (cr + fr) if external else (cr - fr)
    if d <= 0: return None

    best = None; best_score = 1e18
    for sign in (1.0, -1.0):
        ox, oy = x0 + nx*sign*fr, y0 + ny*sign*fr
        wx, wy = ox - ccx, oy - ccy
        b = 2.0 * _sv2_dot(ux, uy, wx, wy)
        c = wx*wx + wy*wy - d*d
        disc = b*b - 4.0*c
        if disc < -_SV_EPS: continue
        sq = math.sqrt(max(0.0, disc))
        for t_sol in ((-b+sq)/2.0, (-b-sq)/2.0):
            fcx, fcy = ox + ux*t_sol, oy + uy*t_sol
            tlx, tly, s = _sv2_project(fcx, fcy, x0, y0, dx, dy)
            rdx, rdy = fcx-ccx, fcy-ccy
            rn = math.hypot(rdx, rdy)
            if rn < _SV_EPS: continue
            tcx = ccx + rdx/rn*cr;  tcy = ccy + rdy/rn*cr
            if s < -1e-3 or s > 1.0+1e-3: continue
            if _sv2_dot(fcx-x0, fcy-y0, inward_nx, inward_ny) < -1e-6: continue
            # Score by t: prefer_high_t picks root closest to rim, else closest to hub
            score = -s if prefer_high_t else s
            if score < best_score:
                best_score = score; best = (fcx, fcy, tlx, tly, tcx, tcy, s)
    return best

def _sv2_line_line_fillet(rx0, ry0, rdx, rdy, lx0, ly0, ldx, ldy,
                           in_rx, in_ry, in_lx, in_ly, fr):
    rux, ruy = _sv2_unit(rdx, rdy); lux, luy = _sv2_unit(ldx, ldy)
    prx, pry = rx0 + in_rx*fr, ry0 + in_ry*fr
    plx, ply = lx0 + in_lx*fr, ly0 + in_ly*fr
    fc = _sv2_intersect(prx, pry, rux, ruy, plx, ply, lux, luy)
    if fc is None: return None
    fcx, fcy = fc
    trx, try_, _ = _sv2_project(fcx, fcy, rx0, ry0, rdx, rdy)
    tlx, tly, _  = _sv2_project(fcx, fcy, lx0, ly0, ldx, ldy)
    return fcx, fcy, trx, try_, tlx, tly

def _sv2_void_path_d(cx, cy, theta_mid, theta_step, r_hub, r_rim,
                      spoke_width, fillet_tip, fillet_base):
    """Return SVG path `d` string for one spoke void gap using true arc commands."""
    half_w = spoke_width / 2.0
    spoke_half_hub = math.asin(min(1.0, half_w / r_hub))
    spoke_half_rim = math.asin(min(1.0, half_w / r_rim))
    gap_half_hub   = max(_SV_EPS, theta_step/2.0 - spoke_half_hub)
    gap_half_rim   = max(_SV_EPS, theta_step/2.0 - spoke_half_rim)

    def P(r, a): return cx + r*math.cos(a), cy + r*math.sin(a)

    p_lb = P(r_hub, theta_mid - gap_half_hub)
    p_lt = P(r_rim, theta_mid - gap_half_rim)
    p_rt = P(r_rim, theta_mid + gap_half_rim)
    p_rb = P(r_hub, theta_mid + gap_half_hub)

    l_dx, l_dy = p_lt[0]-p_lb[0], p_lt[1]-p_lb[1]
    r_dx, r_dy = p_rt[0]-p_rb[0], p_rt[1]-p_rb[1]

    probe = P((r_hub+r_rim)*0.5, theta_mid)
    lux, luy = _sv2_unit(l_dx, l_dy)
    ln_a, ln_b = -luy, lux
    in_lx = ln_a if _sv2_dot(probe[0]-p_lb[0], probe[1]-p_lb[1], ln_a, ln_b) > 0 else -ln_a
    in_ly = ln_b if _sv2_dot(probe[0]-p_lb[0], probe[1]-p_lb[1], ln_a, ln_b) > 0 else -ln_b
    rux, ruy = _sv2_unit(r_dx, r_dy)
    rn_a, rn_b = -ruy, rux
    in_rx = rn_a if _sv2_dot(probe[0]-p_rb[0], probe[1]-p_rb[1], rn_a, rn_b) > 0 else -rn_a
    in_ry = rn_b if _sv2_dot(probe[0]-p_rb[0], probe[1]-p_rb[1], rn_a, rn_b) > 0 else -rn_b

    tip_l = _sv2_line_circle_fillet(p_lb[0], p_lb[1], l_dx, l_dy,
                                     cx, cy, r_rim, fillet_tip,
                                     False, in_lx, in_ly, True)
    tip_r = _sv2_line_circle_fillet(p_rb[0], p_rb[1], r_dx, r_dy,
                                     cx, cy, r_rim, fillet_tip,
                                     False, in_rx, in_ry, True)
    base_l = _sv2_line_circle_fillet(p_lb[0], p_lb[1], l_dx, l_dy,
                                      cx, cy, r_hub, fillet_base,
                                      True, in_lx, in_ly, False)
    base_r = _sv2_line_circle_fillet(p_rb[0], p_rb[1], r_dx, r_dy,
                                      cx, cy, r_hub, fillet_base,
                                      True, in_rx, in_ry, False)

    use_hub = base_l is not None and base_r is not None
    if use_hub:
        a_hr = math.atan2(base_r[5]-cy, base_r[4]-cx)
        a_hl = math.atan2(base_l[5]-cy, base_l[4]-cx)
        cw_span = (a_hr - a_hl) % (2*math.pi)
        if cw_span > math.pi or cw_span < _SV_EPS:
            use_hub = False

    def A(r, sx, sy, ex, ey, cw):
        lg, sw = _sv2_arc_flags(cx, cy, sx, sy, ex, ey, cw)
        return f"A {r:.4f},{r:.4f} 0 {lg} {sw} {ex:.4f},{ey:.4f}"

    def Af(r, fcx, fcy, sx, sy, ex, ey):
        # Short-arc fillet: pick the sweep that gives arc < π
        a1 = math.atan2(sy-fcy, sx-fcx)
        a2 = math.atan2(ey-fcy, ex-fcx)
        diff = (a2-a1) % (2*math.pi)
        cw = diff > math.pi   # go the short way
        lg, sw = _sv2_arc_flags(fcx, fcy, sx, sy, ex, ey, cw)
        return f"A {r:.4f},{r:.4f} 0 {lg} {sw} {ex:.4f},{ey:.4f}"

    cmds = []

    # ── Rim arc: left-tip tangency → right-tip tangency ────────────────────
    rim_ls = tip_l[4:6] if tip_l else p_lt
    rim_rs = tip_r[4:6] if tip_r else p_rt
    cmds.append(f"M {rim_ls[0]:.4f},{rim_ls[1]:.4f}")
    cmds.append(A(r_rim, rim_ls[0], rim_ls[1], rim_rs[0], rim_rs[1], cw=False))

    # ── Right tip fillet: rim tangency → spoke wall tangency ───────────────
    if tip_r:
        fcx, fcy, tlx, tly, tcx, tcy, _ = tip_r
        cmds.append(Af(fillet_tip, fcx, fcy, tcx, tcy, tlx, tly))
    else:
        cmds.append(f"L {p_rt[0]:.4f},{p_rt[1]:.4f}")

    # ── Right wall + base ───────────────────────────────────────────────────
    if use_hub:
        fcx, fcy, tl_x, tl_y, tc_x, tc_y, _ = base_r
        cmds.append(f"L {tl_x:.4f},{tl_y:.4f}")
        cmds.append(Af(fillet_base, fcx, fcy, tl_x, tl_y, tc_x, tc_y))
        # Hub arc right → left
        fcx2, fcy2, tl_x2, tl_y2, tc_x2, tc_y2, _ = base_l
        cmds.append(A(r_hub, tc_x, tc_y, tc_x2, tc_y2, cw=True))
        cmds.append(Af(fillet_base, fcx2, fcy2, tc_x2, tc_y2, tl_x2, tl_y2))
    else:
        ll = _sv2_line_line_fillet(p_rb[0], p_rb[1], r_dx, r_dy,
                                    p_lb[0], p_lb[1], l_dx, l_dy,
                                    in_rx, in_ry, in_lx, in_ly, fillet_base)
        if ll and fillet_base > 0.05:
            fcx, fcy, trx, try_, tlx, tly = ll
            cmds.append(f"L {trx:.4f},{try_:.4f}")
            cmds.append(Af(fillet_base, fcx, fcy, trx, try_, tlx, tly))
        else:
            cmds.append(f"L {p_rb[0]:.4f},{p_rb[1]:.4f}")
            cmds.append(A(r_hub, p_rb[0], p_rb[1], p_lb[0], p_lb[1], cw=True))
            cmds.append(f"L {p_lb[0]:.4f},{p_lb[1]:.4f}")

    # ── Left tip fillet: spoke wall tangency → rim tangency ────────────────
    if tip_l:
        fcx, fcy, tlx, tly, tcx, tcy, _ = tip_l
        cmds.append(Af(fillet_tip, fcx, fcy, tlx, tly, tcx, tcy))

    cmds.append("Z")
    return " ".join(cmds)


def _spoke_void_svg_elements(cx, cy, r_tooth_root, r_hub, spoke_count,
                              spoke_width, rim_depth, fillet_tip, fillet_base,
                              sw, coord_swap=False):
    """Return SVG elements for spokes: edges + rim arcs + tip fillets."""
    if spoke_count < 2 or spoke_width <= 0.0 or r_hub <= 0.0:
        return ''
    r_rim = max(r_hub + 0.5, r_tooth_root - rim_depth)
    if r_rim <= r_hub + 0.5:
        return ''

    def to_svg(xm, ym):
        if coord_swap:
            return cx + xm, cy - ym
        else:
            return cx + ym, cy + xm

    def polar(r, a):
        return r * math.cos(a), r * math.sin(a)

    # ── SVG Arc Notes ────────────────────────────────────────────────────────
    # SVG arc: A rx,ry x-rot large-arc-flag sweep-flag ex,ey
    #   sweep-flag=0 → arc travels COUNTER-CLOCKWISE (CCW)
    #   sweep-flag=1 → arc travels CLOCKWISE (CW)
    # SVG coordinate system has y-axis pointing DOWN, so CW/CCW are screen-relative.
    # Four possible arcs from (sx,sy) to (ex,ey) at radius r:
    #   large=0 sweep=0 → small arc, CCW
    #   large=0 sweep=1 → small arc, CW
    #   large=1 sweep=0 → large arc, CCW
    #   large=1 sweep=1 → large arc, CW
    # To pick correct sweep given a reference "void" point that should be OUTSIDE the arc:
    #   1. Compute fillet center from the fillet result (fcx,fcy in math coords → SVG via to_svg)
    #   2. Compute angles from fillet center to start, end, and void point
    #   3. The CCW arc spans (a_end - a_start) % 2pi
    #   4. If void angle falls within that CCW span → void is INSIDE the CCW arc → use CW (sweep=1)
    #   5. Otherwise void is outside CCW arc → use CCW (sweep=0)
    # NOTE: void reference point must be at radius (r_hub+r_rim)/2 in void_mid direction,
    #       NOT inside the hub circle (r_hub*0.5 is wrong — it's inside the hub).
    # ─────────────────────────────────────────────────────────────────────────

    def svg_fillet_arc(fx_s, fy_s, sx_s, sy_s, ex_s, ey_s, r):
        """Arc from (sx,sy) to (ex,ey) curving toward fillet centre (fx,fy).
        Cross product of (start→end) × (start→center) determines which side
        the center is on → which sweep direction curves toward it.
        Fillet arcs are always < 180° so large-arc-flag is always 0.
          cross > 0 → center is LEFT of chord → CW arc (sweep=1)
          cross < 0 → center is RIGHT of chord → CCW arc (sweep=0)
        """
        cross = (ex_s - sx_s) * (fy_s - sy_s) - (ey_s - sy_s) * (fx_s - sx_s)
        sweep = 1 if cross > 0 else 0
        return f"A {r:.4f},{r:.4f} 0 0 {sweep} {ex_s:.4f},{ey_s:.4f}"

    def _spoke_fillet(a_hub_corner, a_rim_corner, void_mid_angle, r_circle, fr, external, prefer_high_t):
        """Fillet tangent to spoke wall and a circle; math coords, pulley centre at origin."""
        if fr <= 0:
            return None
        phx, phy = polar(r_hub, a_hub_corner)
        prx, pry = polar(r_rim, a_rim_corner)
        dx, dy = prx - phx, pry - phy
        probe_x = (r_hub + r_rim) * 0.5 * math.cos(void_mid_angle)
        probe_y = (r_hub + r_rim) * 0.5 * math.sin(void_mid_angle)
        ux, uy = _sv2_unit(dx, dy)
        nx, ny = -uy, ux
        dp = _sv2_dot(probe_x - phx, probe_y - phy, nx, ny)
        in_x = nx if dp > 0 else -nx
        in_y = ny if dp > 0 else -ny
        return _sv2_line_circle_fillet(
            phx, phy, dx, dy,
            0.0, 0.0, r_circle, fr,
            external, in_x, in_y, prefer_high_t,
        )

    def compute_tip(a_hub_corner, a_rim_corner, void_mid_angle):
        return _spoke_fillet(a_hub_corner, a_rim_corner, void_mid_angle,
                             r_rim, fillet_tip, False, True)

    def compute_base_hub(a_hub_corner, a_rim_corner, void_mid_angle):
        """Hub-tangent base fillet: line-circle fillet touching the hub circle."""
        return _spoke_fillet(a_hub_corner, a_rim_corner, void_mid_angle,
                             r_hub, fillet_base, True, False)

    def compute_base_ll(p_rh, p_rr, p_lh, p_lr, void_mid_angle):
        """Line-line fillet tangent to both spoke walls; no hub tangency required."""
        if fillet_base <= 0:
            return None
        rdx, rdy = p_rr[0] - p_rh[0], p_rr[1] - p_rh[1]
        ldx, ldy = p_lr[0] - p_lh[0], p_lr[1] - p_lh[1]
        probe_x = (r_hub + r_rim) * 0.5 * math.cos(void_mid_angle)
        probe_y = (r_hub + r_rim) * 0.5 * math.sin(void_mid_angle)
        rux, ruy = _sv2_unit(rdx, rdy)
        rnx, rny = -ruy, rux
        rdp = _sv2_dot(probe_x - p_rh[0], probe_y - p_rh[1], rnx, rny)
        in_rx, in_ry = (rnx, rny) if rdp > 0 else (-rnx, -rny)
        lux, luy = _sv2_unit(ldx, ldy)
        lnx, lny = -luy, lux
        ldp = _sv2_dot(probe_x - p_lh[0], probe_y - p_lh[1], lnx, lny)
        in_lx, in_ly = (lnx, lny) if ldp > 0 else (-lnx, -lny)
        return _sv2_line_line_fillet(
            p_rh[0], p_rh[1], rdx, rdy,
            p_lh[0], p_lh[1], ldx, ldy,
            in_rx, in_ry, in_lx, in_ly,
            fillet_base,
        )

    half_w = spoke_width / 2.0
    half_a_hub = math.asin(min(1.0, half_w / r_hub))
    half_a_rim = math.asin(min(1.0, half_w / r_rim))
    theta_step = 2.0 * math.pi / spoke_count
    rim_sweep  = 0  # math CCW → SVG CCW for both coord_swap transforms


    els = []
    stroke = f'stroke="#1a1a1a" stroke-width="{sw:.4f}"'

    for i in range(spoke_count):
        a      = i * theta_step
        a_next = (i + 1) * theta_step
        void_mid = a + theta_step / 2.0

        # Corner points of this void
        p_rh = polar(r_hub, a + half_a_hub)            # right wall, hub end
        p_rr = polar(r_rim, a + half_a_rim)            # right wall, rim end
        p_lh = polar(r_hub, a_next - half_a_hub)       # left wall, hub end
        p_lr = polar(r_rim, a_next - half_a_rim)       # left wall, rim end

        # Tip fillets for this void
        tip_r  = compute_tip(a + half_a_hub,      a + half_a_rim,      void_mid)
        tip_nl = compute_tip(a_next - half_a_hub, a_next - half_a_rim, void_mid)

        # Base fillet: prefer line-line; fall back to hub-tangent when the
        # fillet arc's closest point to the pulley centre would be inside r_hub.
        ll = compute_base_ll(p_rh, p_rr, p_lh, p_lr, void_mid)
        use_hub_tangent = (ll is None or
                           math.hypot(ll[0], ll[1]) - fillet_base < r_hub)
        if use_hub_tangent:
            base_r  = compute_base_hub(a + half_a_hub,      a + half_a_rim,      void_mid)
            base_nl = compute_base_hub(a_next - half_a_hub, a_next - half_a_rim, void_mid)
        else:
            base_r = base_nl = None  # not used in line-line mode

        # ── Right spoke edge (right wall of spoke i) ──────────────────────────
        x1, y1 = to_svg(tip_r[2],  tip_r[3])  if tip_r else to_svg(*p_rr)
        if use_hub_tangent:
            x2, y2 = to_svg(base_r[2], base_r[3]) if base_r else to_svg(*p_rh)
        else:
            x2, y2 = to_svg(ll[2], ll[3])
        els.append(f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" {stroke}/>')

        # ── Right tip fillet arc ──────────────────────────────────────────────
        if tip_r:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_r
            fs, ff = to_svg(fcx, fcy)
            xs_s, ys_s = to_svg(tlx, tly)
            xe_s, ye_s = to_svg(tcx, tcy)
            els.append(f'<path d="M {xs_s:.4f},{ys_s:.4f} '
                       f'{svg_fillet_arc(fs, ff, xs_s, ys_s, xe_s, ye_s, fillet_tip)}" '
                       f'fill="none" {stroke}/>')

        # ── Rim arc ───────────────────────────────────────────────────────────
        xs_rim, ys_rim = to_svg(tip_r[4],  tip_r[5])  if tip_r  else to_svg(*p_rr)
        xe_rim, ye_rim = to_svg(tip_nl[4], tip_nl[5]) if tip_nl else to_svg(*p_lr)
        span = (a_next - half_a_rim) - (a + half_a_rim)
        large = 1 if span > math.pi else 0
        els.append(f'<path d="M {xs_rim:.4f},{ys_rim:.4f} '
                   f'A {r_rim:.4f},{r_rim:.4f} 0 {large} {rim_sweep} {xe_rim:.4f},{ye_rim:.4f}" '
                   f'fill="none" {stroke}/>')

        # ── Left tip fillet arc ───────────────────────────────────────────────
        if tip_nl:
            fcx, fcy, tlx, tly, tcx, tcy, _ = tip_nl
            fs, ff = to_svg(fcx, fcy)
            xs_s, ys_s = to_svg(tcx, tcy)
            xe_s, ye_s = to_svg(tlx, tly)
            els.append(f'<path d="M {xs_s:.4f},{ys_s:.4f} '
                       f'{svg_fillet_arc(fs, ff, xs_s, ys_s, xe_s, ye_s, fillet_tip)}" '
                       f'fill="none" {stroke}/>')

        # ── Left spoke edge (left wall of spoke i+1) ──────────────────────────
        x1, y1 = to_svg(tip_nl[2], tip_nl[3]) if tip_nl else to_svg(*p_lr)
        if use_hub_tangent:
            x2, y2 = to_svg(base_nl[2], base_nl[3]) if base_nl else to_svg(*p_lh)
        else:
            x2, y2 = to_svg(ll[4], ll[5])
        els.append(f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" {stroke}/>')

        # ── Base fillet(s) and hub arc ────────────────────────────────────────
        if use_hub_tangent:
            # Right base fillet arc (spoke wall → hub circle)
            if base_r:
                fcx, fcy, tlx, tly, tcx, tcy, _ = base_r
                fs, ff = to_svg(fcx, fcy)
                xs_s, ys_s = to_svg(tlx, tly)
                xe_s, ye_s = to_svg(tcx, tcy)
                els.append(f'<path d="M {xs_s:.4f},{ys_s:.4f} '
                           f'{svg_fillet_arc(fs, ff, xs_s, ys_s, xe_s, ye_s, fillet_base)}" '
                           f'fill="none" {stroke}/>')
            # Hub arc between the two hub-tangent points
            xs_hub = to_svg(base_r[4],  base_r[5])  if base_r  else to_svg(*p_rh)
            xe_hub = to_svg(base_nl[4], base_nl[5]) if base_nl else to_svg(*p_lh)
            span_hub = (a_next - half_a_hub) - (a + half_a_hub)
            large_hub = 1 if span_hub > math.pi else 0
            els.append(f'<path d="M {xs_hub[0]:.4f},{xs_hub[1]:.4f} '
                       f'A {r_hub:.4f},{r_hub:.4f} 0 {large_hub} {rim_sweep} '
                       f'{xe_hub[0]:.4f},{xe_hub[1]:.4f}" fill="none" {stroke}/>')
            # Left base fillet arc (hub circle → spoke wall)
            if base_nl:
                fcx, fcy, tlx, tly, tcx, tcy, _ = base_nl
                fs, ff = to_svg(fcx, fcy)
                xs_s, ys_s = to_svg(tcx, tcy)
                xe_s, ye_s = to_svg(tlx, tly)
                els.append(f'<path d="M {xs_s:.4f},{ys_s:.4f} '
                           f'{svg_fillet_arc(fs, ff, xs_s, ys_s, xe_s, ye_s, fillet_base)}" '
                           f'fill="none" {stroke}/>')
        else:
            # Single line-line base fillet arc
            fcx, fcy, trx, try_, tlx, tly = ll
            fs, ff = to_svg(fcx, fcy)
            xs_s, ys_s = to_svg(trx, try_)
            xe_s, ye_s = to_svg(tlx, tly)
            els.append(f'<path d="M {xs_s:.4f},{ys_s:.4f} '
                       f'{svg_fillet_arc(fs, ff, xs_s, ys_s, xe_s, ye_s, fillet_base)}" '
                       f'fill="none" {stroke}/>')

    return '\n  '.join(els)

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


def _spoke_callout_svg_elements(
    R_OD: float,
    R_bore: float,
    R_hub: float,
    R_rim_inner: float,
    R_tooth_root: float,
    bore_mm: float,
    spoke_hub_od_mm: float,
    rim_depth_mm: float,
    spoke_width_mm: float,
    fillet_tip_mm: float,
    fillet_base_mm: float,
    spoke_count: int,
    sw: float,
) -> str:
    """Return annotation callouts for the 2D spoke parameters."""
    if spoke_count < 2 or spoke_width_mm <= 0.0:
        return ''

    leader = max(0.18, sw * 0.72)
    dim = max(0.12, sw * 0.55)
    fs = max(2.2, min(3.0, R_OD * 0.095))
    label_x = R_OD + 15.0
    elbow_x = R_OD + 8.0
    y0 = -R_OD + 7.0
    row = max(8.0, fs * 2.7)

    style_leader = (
        f'stroke="#0f766e" stroke-width="{leader:.3f}" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    )
    style_dim = (
        f'stroke="#64748b" stroke-width="{dim:.3f}" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    )
    text_style = (
        'font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{fs:.3f}" fill="#0f172a"'
    )
    value_style = (
        'font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{fs * 0.86:.3f}" fill="#475569"'
    )

    def text(label, value, y):
        return (
            f'<text x="{label_x:.4f}" y="{y:.4f}" {text_style}>{label}</text>'
            f'<text x="{label_x:.4f}" y="{y + fs * 0.95:.4f}" {value_style}>{value}</text>'
        )

    def leader_line(sx, sy, y):
        return (
            f'<path d="M {sx:.4f},{sy:.4f} L {elbow_x:.4f},{y:.4f} '
            f'L {label_x - 1.8:.4f},{y:.4f}" {style_leader}/>'
        )

    def tick(x, y, size=1.4):
        return f'<path d="M {x:.4f},{y - size:.4f} L {x:.4f},{y + size:.4f}" {style_dim}/>'

    def arc_midpoint(fcx, fcy, sx, sy, ex, ey, radius):
        a1 = math.atan2(sy - fcy, sx - fcx)
        a2 = math.atan2(ey - fcy, ex - fcx)
        ccw = (a2 - a1) % (2.0 * math.pi)
        if ccw <= math.pi:
            am = a1 + ccw / 2.0
        else:
            am = a1 - ((2.0 * math.pi - ccw) / 2.0)
        return fcx + radius * math.cos(am), fcy + radius * math.sin(am)

    def zero_degree_fillet_midpoints():
        """Return midpoint anchors for the 0-degree spoke's top base/tip fillets."""
        half_w = spoke_width_mm / 2.0
        if R_hub <= 0.0 or R_rim_inner <= R_hub:
            return None, None

        x_hub = math.sqrt(max(0.0, R_hub * R_hub - half_w * half_w))
        x_rim = math.sqrt(max(0.0, R_rim_inner * R_rim_inner - half_w * half_w))
        dx = x_rim - x_hub
        dy = 0.0

        tip_mid = None
        if fillet_tip_mm > 0.0:
            tip = _sv2_line_circle_fillet(
                x_hub, half_w, dx, dy,
                0.0, 0.0, R_rim_inner, fillet_tip_mm,
                False, 0.0, 1.0, True,
            )
            if tip:
                fcx, fcy, tlx, tly, tcx, tcy, _ = tip
                tip_mid = arc_midpoint(fcx, fcy, tlx, tly, tcx, tcy, fillet_tip_mm)

        base_mid = None
        if fillet_base_mm > 0.0:
            base = _sv2_line_circle_fillet(
                x_hub, half_w, dx, dy,
                0.0, 0.0, R_hub, fillet_base_mm,
                True, 0.0, 1.0, False,
            )
            if base:
                fcx, fcy, tlx, tly, tcx, tcy, _ = base
                base_mid = arc_midpoint(fcx, fcy, tlx, tly, tcx, tcy, fillet_base_mm)

        return base_mid, tip_mid

    els = [
        '<g id="dimension_callouts">',
    ]

    rows = {
        'rim': y0,
        'bore': y0 + row,
        'hub': y0 + row * 2.0,
        'width': y0 + row * 3.0,
        'base': y0 + row * 4.0,
        'tip': y0 + row * 5.0,
        'count': y0 + row * 6.0,
    }

    # Bore diameter.
    if R_bore > 0.0:
        y = rows['bore']
        els.append(leader_line(R_bore * 0.72, -R_bore * 0.72, y))
        els.append(text('Bore', f'{bore_mm:.2f} mm dia', y - fs * 0.2))

    # Hub outside diameter.
    if R_hub > R_bore:
        y = rows['hub']
        els.append(leader_line(R_hub * 0.94, -R_hub * 0.34, y))
        els.append(text('Hub OD', f'{spoke_hub_od_mm:.2f} mm dia', y - fs * 0.2))

    # Rim depth, shown as the radial distance from tooth root to inner rim
    # at 45 degrees on the rim.
    if R_tooth_root > R_rim_inner:
        y = rows['rim']
        rim_a = math.radians(45.0)
        ux = math.cos(rim_a)
        uy = -math.sin(rim_a)
        x_outer = ux * R_tooth_root
        y_outer = uy * R_tooth_root
        x_inner = ux * R_rim_inner
        y_inner = uy * R_rim_inner
        els.append(f'<path d="M {x_outer:.4f},{y_outer:.4f} L {x_inner:.4f},{y_inner:.4f}" {style_dim}/>')
        els.append(tick(x_outer, y_outer))
        els.append(tick(x_inner, y_inner))
        els.append(leader_line((x_outer + x_inner) / 2.0, (y_outer + y_inner) / 2.0, y))
        els.append(text('Rim Depth', f'{rim_depth_mm:.2f} mm', y - fs * 0.2))

    # Spoke width across the 0-degree spoke.
    x_mid_spoke = (R_hub + R_rim_inner) / 2.0
    half_w = spoke_width_mm / 2.0
    y = rows['width']
    els.append(f'<path d="M {x_mid_spoke:.4f},{-half_w:.4f} L {x_mid_spoke:.4f},{half_w:.4f}" {style_dim}/>')
    els.append(tick(x_mid_spoke, -half_w))
    els.append(tick(x_mid_spoke, half_w))
    els.append(leader_line(x_mid_spoke, 0.0, y))
    els.append(text('Spoke Width', f'{spoke_width_mm:.2f} mm', y - fs * 0.2))

    # Fillet labels land on the midpoint of the 0-degree spoke's top fillet arcs.
    base_mid, tip_mid = zero_degree_fillet_midpoints()
    if fillet_base_mm > 0.0:
        y = rows['base']
        bx, by = base_mid if base_mid else (half_w + fillet_base_mm * 0.55, R_hub + fillet_base_mm * 0.55)
        els.append(leader_line(bx, by, y))
        els.append(text('Fillet Base', f'R {fillet_base_mm:.2f} mm', y - fs * 0.2))

    if fillet_tip_mm > 0.0:
        y = rows['tip']
        tx, ty = tip_mid if tip_mid else (half_w + fillet_tip_mm * 0.55, R_rim_inner - fillet_tip_mm * 0.55)
        els.append(leader_line(tx, ty, y))
        els.append(text('Fillet Tip', f'R {fillet_tip_mm:.2f} mm', y - fs * 0.2))

    y = rows['count']
    els.append(text('Spoke Count', str(spoke_count), y - fs * 0.2))

    els.append('</g>')
    return '\n  '.join(els)


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
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 2.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    include_data: bool = True,
    include_callouts: bool = False,
) -> str:
    """
    Returns an SVG string: full pulley profile + optional info panel.
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

    # ── Spoke voids + hub circle ──────────────────────────────────────────────
    R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
    R_hub_spoke  = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (R_bore + 1.0)
    if spoke_count >= 2 and spoke_width_mm > 0.0:
        R_rim_spoke = max(R_hub_spoke + 0.5, R_tooth_root - rim_depth_mm)
        spoke_el = _spoke_void_svg_elements(
            cx=0.0, cy=0.0,
            r_tooth_root=R_tooth_root, r_hub=R_hub_spoke,
            spoke_count=spoke_count, spoke_width=spoke_width_mm,
            rim_depth=rim_depth_mm, fillet_tip=fillet_tip_mm,
            fillet_base=fillet_base_mm, sw=sw, coord_swap=False,
        )
        hub_el = (f'<circle cx="0" cy="0" r="{R_hub_spoke:.4f}" '
                  f'fill="none" stroke="#1a1a1a" stroke-width="{sw:.3f}"/>')
        callout_el = _spoke_callout_svg_elements(
            R_OD=R_OD,
            R_bore=R_bore,
            R_hub=R_hub_spoke,
            R_rim_inner=R_rim_spoke,
            R_tooth_root=R_tooth_root,
            bore_mm=bore_mm,
            spoke_hub_od_mm=spoke_hub_od_mm,
            rim_depth_mm=rim_depth_mm,
            spoke_width_mm=spoke_width_mm,
            fillet_tip_mm=fillet_tip_mm,
            fillet_base_mm=fillet_base_mm,
            spoke_count=spoke_count,
            sw=sw,
        ) if include_callouts else ''
    else:
        spoke_el = ''
        hub_el   = ''
        callout_el = ''

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
    if callout_el:
        panel_w = max(panel_w, OD_mm + 140.0)
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
    if spoke_count >= 2 and spoke_width_mm > 0.0:
        rows += [
            ('— Spokes —',       ''),
            ('Spoke Count',      str(spoke_count)),
            ('Spoke Width',      f'{spoke_width_mm:.2f} mm'),
            ('Hub OD',           f'{spoke_hub_od_mm:.2f} mm'),
            ('Rim Depth',        f'{rim_depth_mm:.2f} mm'),
            ('Tip Fillet',       f'{fillet_tip_mm:.2f} mm'),
            ('Base Fillet',      f'{fillet_base_mm:.2f} mm'),
        ]

    def txt(x, y, content, font_size, color='#1a1a1a', weight='normal', anchor='start'):
        return (f'<text x="{x:.4f}" y="{y:.4f}" '
                f'font-family="Helvetica, Arial, sans-serif" '
                f'font-size="{font_size}" font-weight="{weight}" '
                f'fill="{color}" text-anchor="{anchor}">'
                f'{content}</text>')

    if include_data:
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
            if label.startswith('—'):
                y += line_h * 0.3
                panel_els.append(txt(col_label_x, y, label, fs_body, color='#0078d4', weight='bold'))
                y += line_h
            else:
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
            'Generated by Timing Pulley Generator  ·  cheapcadtools.com',
            fs_small, color='#0078d4', anchor='middle'))

        panel_total_height = y - panel_top + line_h

        # ── Viewport: wide enough for both pulley and panel ──────────────────────
        vx   = panel_left - padding_mm
        vy   = -(R_OD + padding_mm)
        vw   = panel_w + padding_mm * 2
        vh   = (R_OD + padding_mm) + panel_top + panel_total_height + padding_mm
        panel_svg = '\n  '.join(panel_els)
    else:
        # Drawing only — tight viewport around the pulley
        extra_right = 70.0 if callout_el else 0.0
        extra_left = 12.0 if callout_el else 0.0
        vx   = -(R_OD + padding_mm + extra_left)
        vy   = -(R_OD + padding_mm)
        vw   =  (R_OD + padding_mm) * 2 + extra_left + extra_right
        vh   =  (R_OD + padding_mm) * 2
        panel_svg = ''

    vbox = f"{vx:.4f} {vy:.4f} {vw:.4f} {vh:.4f}"

    # Output width fixed at 600px; height scales proportionally
    out_w = 600
    out_h = int(out_w * vh / vw)

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
  {hub_el}
  {spoke_el}
  {callout_el}
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


def generate_rim_layer_svg(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    padding_mm: float = 3.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 2.0,
) -> str:
    """Return an SVG for the rim ring layer: toothed outer profile + three
    concentric circles (inner rim, hub OD, bore).  Intended for laser /
    waterjet cutting of the rim ring in a spoked pulley design.

    Layer colours:
      black  — outer toothed profile (PROFILE)
      blue   — inner rim circle at R_tooth_root − rim_depth (RIM_INNER)
      green  — spoke hub OD circle (HUB)
      red    — bore circle (BORE)
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}'")

    spec = PULLEY_SPECS[key]
    pitch_val      = spec['pitch']
    clearance_mm   = max(-pitch_val, min(clearance_mm,   pitch_val))
    backlash_mm    = max(-pitch_val, min(backlash_mm,    pitch_val))
    print_extra_mm = max(0.0,        min(print_extra_mm, pitch_val))

    container    = generate_profile_groove(family, key, num_teeth, clearance_mm, print_extra_mm, backlash_mm)
    groove_prims = container.primitives[1:-1]
    groove_pts   = _build_groove_points(groove_prims, family)
    wrapped, R_OD, edge_a = wrap_groove_to_pulley(groove_pts, spec, num_teeth, print_extra_mm)

    R_bore = bore_mm / 2.0
    t_ang  = 2.0 * math.pi / num_teeth

    def rotate(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    # ── Outer toothed profile path ────────────────────────────────────────────
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

    sw = max(0.15, R_OD * 2.0 * 0.004)

    R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
    R_hub_spoke  = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (R_bore + 1.0)
    R_rim_inner  = max(R_hub_spoke + 0.5, R_tooth_root - rim_depth_mm)

    vb_size = R_OD + padding_mm
    vb = f"{-vb_size:.4f} {-vb_size:.4f} {vb_size * 2:.4f} {vb_size * 2:.4f}"

    parts = [
        f'<path d="{path_d}" fill="none" stroke="#000000" stroke-width="{sw:.3f}"/>',
        f'<circle cx="0" cy="0" r="{R_rim_inner:.4f}" fill="none" stroke="#0055cc" stroke-width="{sw:.3f}"/>',
    ]
    if R_hub_spoke > R_bore + 0.1:
        parts.append(
            f'<circle cx="0" cy="0" r="{R_hub_spoke:.4f}" fill="none" stroke="#007a00" stroke-width="{sw:.3f}"/>'
        )
    if R_bore > 0:
        parts.append(
            f'<circle cx="0" cy="0" r="{R_bore:.4f}" fill="none" stroke="#cc0000" stroke-width="{sw:.3f}"/>'
        )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vb}" width="{vb_size * 2:.1f}mm" height="{vb_size * 2:.1f}mm">\n'
        + '\n'.join(parts)
        + '\n</svg>\n'
    )


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
    include_data: bool = True,
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
    min_c = R_pitch1 + R_pitch2
    C = max(float(center_dist_mm), min_c)

    # Cap render center distance so both pulleys stay visible when the actual
    # center distance is large relative to the pulley sizes.
    max_render_gap = 4.0 * (R_OD1 + R_OD2)
    actual_gap     = C - R_pitch1 - R_pitch2
    render_gap     = min(actual_gap, max_render_gap)
    C_render       = max(min_c, R_pitch1 + R_pitch2 + render_gap)

    cx1 = -C_render / 2.0
    cx2 =  C_render / 2.0
    cy  = 0.0   # both pulleys on y=0

    # Belt geometry
    belt_ring, tooth_polys, phi_left, phi_right = [], [], 0.0, 0.0
    if family in BELT_FAMILIES:
        belt_ring, tooth_polys, phi_left, phi_right = build_two_pulley_belt(
            family, pitch, num_teeth1, num_teeth2, C_render, x_offset=cx1,
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
        'Generated by Timing Pulley Generator  ·  cheapcadtools.com',
        fs_small, color='#0078d4', anchor='middle'))

    panel_h = y - panel_top + line_h

    # ── ViewBox ───────────────────────────────────────────────────────────────
    vx = x_min
    vy = -y_ext
    vw = world_w
    if include_data:
        vh = world_h + panel_top - (-y_ext) + panel_h + padding_mm
    else:
        vh = world_h
        panel_els = []

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

    # ── Spoke voids (dual) ────────────────────────────────────────────────────
    def _dual_spokes(wrapped_pts, R_OD, bore_mm, cx_off,
                     sc, sw_mm, hub_od, rim_d, ftip, fbase, sw_stroke):
        if sc < 2 or sw_mm <= 0.0:
            return ''
        R_tr = min(math.hypot(x, y) for x, y in wrapped_pts) if wrapped_pts else R_OD
        R_hub_s = (hub_od / 2.0) if hub_od > 0.0 else (bore_mm / 2.0 + 1.0)
        return _spoke_void_svg_elements(
            cx=cx_off, cy=0.0,
            r_tooth_root=R_tr, r_hub=R_hub_s,
            spoke_count=sc, spoke_width=sw_mm,
            rim_depth=rim_d, fillet_tip=ftip,
            fillet_base=fbase, sw=sw_stroke, coord_swap=True,
        )

    spokes1_el = _dual_spokes(wrapped1, R_OD1, bore_mm1, cx1,
                               spoke_count1, spoke_width_mm1, spoke_hub_od_mm1,
                               rim_depth_mm1, fillet_tip_mm1, fillet_base_mm1, sw1)
    spokes2_el = _dual_spokes(wrapped2, R_OD2, bore_mm2, cx2,
                               spoke_count2, spoke_width_mm2, spoke_hub_od_mm2,
                               rim_depth_mm2, fillet_tip_mm2, fillet_base_mm2, sw2)

    def _dual_hub_el(bore_mm, cx_off, sc, sw_mm, hub_od, sw_stroke):
        if sc < 2 or sw_mm <= 0.0:
            return ''
        R_hub_s = (hub_od / 2.0) if hub_od > 0.0 else (bore_mm / 2.0 + 1.0)
        return (f'<circle cx="{cx_off:.4f}" cy="0" r="{R_hub_s:.4f}" '
                f'fill="none" stroke="#1a1a1a" stroke-width="{sw_stroke:.4f}"/>')

    hub1_el = _dual_hub_el(bore_mm1, cx1, spoke_count1, spoke_width_mm1, spoke_hub_od_mm1, sw1)
    hub2_el = _dual_hub_el(bore_mm2, cx2, spoke_count2, spoke_width_mm2, spoke_hub_od_mm2, sw2)

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
  {hub1_el}
  {spokes1_el}
  <path d="{path2}" fill="none" stroke="#1a1a1a" stroke-width="{sw2:.4f}"
        stroke-linejoin="round" stroke-linecap="round"/>
  {bore2_el}
  {hub2_el}
  {spokes2_el}
  {panel_svg}
</svg>'''

    return svg
