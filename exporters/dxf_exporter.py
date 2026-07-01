"""
dxf_exporter.py
Generates a DXF file of a timing belt pulley profile.

Geometry is computed directly from the same primitives as the SVG exporter —
no SVG parsing required.  Each tooth groove becomes a series of LINE entities;
each OD land arc between grooves becomes a true DXF ARC entity; the bore is a
CIRCLE entity.  All geometry is in mm, centred at (0, 0).

Layers
------
PROFILE   – pulley outer profile (teeth + OD arcs)
BORE      – bore / centre hole

Arc direction note
------------------
wrap_groove_to_pulley returns points in "compass" convention:
    (r·sin a, r·cos a)  where a is measured clockwise from +Y.
The SVG exporter places teeth with a clockwise rotation (same convention).
In standard DXF / math coordinates (Y-up, CCW positive), the OD arcs appear
clockwise.  ezdxf arcs run CCW, so start/end angles are swapped when adding
each OD arc.
"""

import math
import io

import ezdxf

from exporters.png_exporter import _spoke_void_polygons, _spoke_void_segments
from geometry.pulley_geometry import (
    generate_profile_groove,
    _build_groove_points,
    wrap_groove_to_pulley,
    PULLEY_SPECS,
    PROFILE_KEY_PREFIX,
    build_two_pulley_belt,
    H_BELT_SPECS, S_BELT_SPECS, R_BELT_SPECS, G_BELT_SPECS,
    T_BELT_SPECS, AT_BELT_SPECS, IMPERIAL_BELT_SPECS, BELT_FAMILIES,
    generate_h_belt_profile, generate_s_belt_profile, generate_r_belt_profile,
    generate_g_belt_profile, generate_t_belt_profile, generate_at_belt_profile,
    generate_imperial_belt_profile,
    pulley_outline_segments, belt_outline_segments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def _rot(x: float, y: float, theta: float):
    """Clockwise rotation by theta — matches the SVG exporter convention."""
    c, s = math.cos(theta), math.sin(theta)
    return x * c + y * s, -x * s + y * c


def _math_angle(x: float, y: float) -> float:
    """Standard math angle in degrees: CCW from +X axis."""
    return math.degrees(math.atan2(y, x))


def _add_line(msp, p0, p1, attribs):
    """Add a LINE only when the two endpoints are not coincident."""
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1e-6:
        msp.add_line((p0[0], p0[1], 0), (p1[0], p1[1], 0), dxfattribs=attribs)


def _segs_to_dxf(msp, segments, attribs):
    """
    Write a pulley_outline_segments() / belt_outline_segments() segment list
    as DXF entities.

    Segment types handled:
      ('spline', [(x,y),...])                    → SPLINE with fit-points
      ('arc', cx,cy,r, (sx,sy),(mx,my),(ex,ey)) → ARC entity (CCW in DXF)
      ('line', x0,y0, x1,y1)                    → LINE entity

    Coordinate convention: all values in compass coords (x = r·sin θ, y = r·cos θ),
    which matches ezdxf's XY plane (no coordinate swap needed).

    DXF ARC direction note
    ----------------------
    DXF arcs run counter-clockwise.  The segment arcs are CW (compass), so the
    DXF start/end angles come from the CW *end* and CW *start* respectively:
      dxf_start_angle = atan2(ey - cy, ex - cx)   (math angle of the CW end)
      dxf_end_angle   = atan2(sy - cy, sx - cx)   (math angle of the CW start)
    """
    for seg in segments:
        kind = seg[0]

        if kind == 'spline':
            _, pts = seg
            msp.add_spline(
                fit_points=[(x, y, 0) for x, y in pts],
                dxfattribs=attribs,
            )

        elif kind == 'arc':
            _, cx, cy, r, (sx, sy), (_mx, _my), (ex, ey) = seg
            # Convert CW compass arc → CCW DXF arc by swapping start/end
            dxf_start = math.degrees(math.atan2(ey - cy, ex - cx))
            dxf_end   = math.degrees(math.atan2(sy - cy, sx - cx))
            msp.add_arc(
                center=(cx, cy, 0),
                radius=r,
                start_angle=dxf_start,
                end_angle=dxf_end,
                dxfattribs=attribs,
            )

        elif kind == 'line':
            _, x0, y0, x1, y1 = seg
            _add_line(msp, (x0, y0), (x1, y1), attribs)


def _seg_to_dxf(seg):
    """
    Convert a spoke segment from math coords (x=r·cos θ, y=r·sin θ) to DXF
    compass coords (x_dxf = y_math, y_dxf = x_math) used throughout this file.

    Returns one of:
        ('arc',  (cx,cy,0), r, start_deg, end_deg)   — DXF CCW arc
        ('line', (x1,y1,0), (x2,y2,0))
    or None if the segment is degenerate.
    """
    if seg[0] == 'line':
        _, x1, y1, x2, y2 = seg
        if math.hypot(x2 - x1, y2 - y1) < 1e-4:
            return None
        return ('line', (y1, x1, 0), (y2, x2, 0))

    # arc: ('arc', cx, cy, r, a1, a2) — short arc from a1 to a2 in math coords
    _, cx, cy, r, a1, a2 = seg
    if r < 1e-4:
        return None
    diff = (a2 - a1) % (2 * math.pi)
    if diff > math.pi:
        diff -= 2 * math.pi          # take short arc
    if abs(diff) < 1e-4:
        return None
    a_end = a1 + diff

    # Compass swap: center (cx,cy) → (cy,cx).
    # Angle transform: math angle θ → DXF angle = 90° − θ.
    # A CCW math arc becomes CW after reflection → swap start/end for DXF CCW.
    # A CW math arc becomes CCW after reflection → keep order.
    dxf_a1  = math.degrees(math.pi / 2 - a1)
    dxf_a2  = math.degrees(math.pi / 2 - a_end)
    if diff > 0:                    # CCW in math → swap for DXF CCW
        dxf_start, dxf_end = dxf_a2, dxf_a1
    else:                           # CW in math → keep order for DXF CCW
        dxf_start, dxf_end = dxf_a1, dxf_a2

    return ('arc', (cy, cx, 0), r, dxf_start, dxf_end)


def _write_seg(msp, dxf_seg, attribs):
    """Write a converted DXF segment to the modelspace."""
    if dxf_seg is None:
        return
    if dxf_seg[0] == 'line':
        _, p1, p2 = dxf_seg
        msp.add_line(p1, p2, dxfattribs=attribs)
    else:
        _, center, r, start_deg, end_deg = dxf_seg
        msp.add_arc(center=center, radius=r,
                    start_angle=start_deg, end_angle=end_deg,
                    dxfattribs=attribs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_dxf(
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
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """
    Return a DXF file as bytes.

    Parameters match generate_svg() exactly so app.py can call both with the
    same arguments.
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}' for {family} / {pitch}")

    # ── Shared segment geometry ──────────────────────────────────────────────
    segs, R_OD, _edge_a, wrapped = pulley_outline_segments(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )

    # ── DXF document ─────────────────────────────────────────────────────────
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4          # 4 = millimetres
    doc.header['$MEASUREMENT'] = 1       # 1 = metric

    msp = doc.modelspace()

    doc.layers.new('PROFILE', dxfattribs={'color': 7, 'linetype': 'Continuous'})
    doc.layers.new('BORE',    dxfattribs={'color': 1, 'linetype': 'Continuous'})
    doc.layers.new('SPOKES',  dxfattribs={'color': 3, 'linetype': 'Continuous'})

    prof = {'layer': 'PROFILE'}

    # ── Pulley profile — SPLINE per tooth groove + ARC per OD land ───────────
    _segs_to_dxf(msp, segs, prof)

    # ── Bore (circle, D-flat, or keyway) ────────────────────────────────────
    if bore_mm > 0:
        _bore_drawn = False
        if flat_depth_mm > 0.0 or (keyway_w_mm > 0.0 and keyway_h_mm > 0.0):
            from exporters.step_exporter import _build_bore_2d
            _bp = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            if _bp is not None:
                coords = list(_bp.exterior.coords)[:-1]
                msp.add_lwpolyline(
                    [(x, y) for x, y in coords],
                    format='xy',
                    close=True,
                    dxfattribs={'layer': 'BORE'},
                )
                _bore_drawn = True
        if not _bore_drawn:
            msp.add_circle(
                center=(0.0, 0.0, 0.0),
                radius=bore_mm / 2.0,
                dxfattribs={'layer': 'BORE'},
            )

    # ── Hub circle ───────────────────────────────────────────────────────────
    # ── Spoke void outlines ──────────────────────────────────────────────────
    if spoke_count >= 2 and spoke_width_mm > 0.0:
        R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
        R_hub_spoke  = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        R_rim_inner  = max(R_hub_spoke + 0.5, R_tooth_root - rim_depth_mm)

        # Hub circle on BORE layer
        msp.add_circle(
            center=(0.0, 0.0, 0.0),
            radius=R_hub_spoke,
            dxfattribs={'layer': 'BORE'},
        )

        # Spoke voids — one ARC/LINE entity per geometric segment (clean edges).
        void_segs = _spoke_void_segments(
            R_hub_spoke, R_rim_inner, spoke_count, spoke_width_mm,
            fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm,
        )
        sp_attr = {'layer': 'SPOKES'}
        for void in void_segs:
            for seg in void:
                _write_seg(msp, _seg_to_dxf(seg), sp_attr)

    # ── Serialise to bytes ───────────────────────────────────────────────────
    # doc.write() requires a text stream; encode to UTF-8 bytes afterwards.
    text_buf = io.StringIO()
    doc.write(text_buf)
    return text_buf.getvalue().encode('utf-8')


def _serialise_dxf(doc) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


def _rotate_segs(segs, phi: float):
    """Return segment list rotated CW by phi radians (compass convention, same as _rot)."""
    if abs(phi) < 1e-9:
        return segs
    c, s = math.cos(phi), math.sin(phi)
    def _r(x, y):
        return x * c + y * s, -x * s + y * c
    result = []
    for seg in segs:
        kind = seg[0]
        if kind == 'spline':
            _, pts = seg
            result.append(('spline', [_r(x, y) for x, y in pts]))
        elif kind == 'arc':
            _, cx, cy, r, (sx, sy), (mx, my), (ex, ey) = seg
            rcx, rcy = _r(cx, cy)
            result.append(('arc', rcx, rcy, r,
                           _r(sx, sy), _r(mx, my), _r(ex, ey)))
        elif kind == 'line':
            _, x0, y0, x1, y1 = seg
            rx0, ry0 = _r(x0, y0)
            rx1, ry1 = _r(x1, y1)
            result.append(('line', rx0, ry0, rx1, ry1))
    return result


def _shift_segs(segs, dx: float, dy: float):
    """Return segment list with all coordinates translated by (dx, dy)."""
    result = []
    for seg in segs:
        kind = seg[0]
        if kind == 'spline':
            _, pts = seg
            result.append(('spline', [(x + dx, y + dy) for x, y in pts]))
        elif kind == 'arc':
            _, cx, cy, r, (sx, sy), (mx, my), (ex, ey) = seg
            result.append(('arc', cx + dx, cy + dy, r,
                           (sx + dx, sy + dy), (mx + dx, my + dy), (ex + dx, ey + dy)))
        elif kind == 'line':
            _, x0, y0, x1, y1 = seg
            result.append(('line', x0 + dx, y0 + dy, x1 + dx, y1 + dy))
    return result


def generate_combined_layout_dxf(
    family: str,
    pitch: str,
    num_teeth1: int,
    bore_mm1: float,
    clearance_mm1: float = 0.0,
    backlash_mm1: float = 0.0,
    print_extra_mm1: float = 0.0,
    spoke_count1: int = 0,
    spoke_width_mm1: float = 0.0,
    spoke_hub_od_mm1: float = 0.0,
    rim_depth_mm1: float = 2.0,
    fillet_tip_mm1: float = 0.0,
    fillet_base_mm1: float = 0.0,
    flat_depth_mm1: float = 0.0,
    keyway_w_mm1: float = 0.0,
    keyway_h_mm1: float = 0.0,
    num_teeth2: int = 0,
    bore_mm2: float = 0.0,
    clearance_mm2: float = 0.0,
    backlash_mm2: float = 0.0,
    print_extra_mm2: float = 0.0,
    spoke_count2: int = 0,
    spoke_width_mm2: float = 0.0,
    spoke_hub_od_mm2: float = 0.0,
    rim_depth_mm2: float = 2.0,
    fillet_tip_mm2: float = 0.0,
    fillet_base_mm2: float = 0.0,
    flat_depth_mm2: float = 0.0,
    keyway_w_mm2: float = 0.0,
    keyway_h_mm2: float = 0.0,
    center_dist_mm: float = 100.0,
) -> bytes:
    """
    Combined drive-assembly DXF: P1 tooth profile at origin, P2 at center_dist_mm,
    belt outline wrapping around both — everything at true operating geometry.
    Used by the 2D 'Download All' button.
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}'")

    n2 = num_teeth2 if num_teeth2 > 0 else num_teeth1

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    msp = doc.modelspace()

    doc.layers.new('PROFILE',    dxfattribs={'color': 7, 'linetype': 'Continuous'})
    doc.layers.new('BORE',       dxfattribs={'color': 1, 'linetype': 'Continuous'})
    doc.layers.new('SPOKES',     dxfattribs={'color': 3, 'linetype': 'Continuous'})
    doc.layers.new('BELT_BACK',  dxfattribs={'color': 5, 'linetype': 'Continuous'})
    doc.layers.new('BELT_TEETH', dxfattribs={'color': 4, 'linetype': 'Continuous'})

    # Phase angles so pulley grooves mesh with belt teeth (same as SVG/STEP exporters)
    phi_left = phi_right = 0.0
    if family in BELT_FAMILIES:
        try:
            _, _, phi_left, phi_right = build_two_pulley_belt(
                family, pitch, num_teeth1, n2, center_dist_mm, x_offset=0.0)
        except Exception:
            pass

    def _draw_pulley(cx, cy, num_teeth, bore_mm, clearance_mm, backlash_mm,
                     print_extra_mm, spoke_count, spoke_width_mm, spoke_hub_od_mm,
                     rim_depth_mm, fillet_tip_mm, fillet_base_mm,
                     flat_depth_mm, keyway_w_mm, keyway_h_mm, phi=0.0):
        segs, R_OD, _edge_a, wrapped = pulley_outline_segments(
            family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm)
        segs = _rotate_segs(segs, phi)
        _segs_to_dxf(msp, _shift_segs(segs, cx, cy), {'layer': 'PROFILE'})

        if bore_mm > 0:
            _bore_drawn = False
            if flat_depth_mm > 0.0 or (keyway_w_mm > 0.0 and keyway_h_mm > 0.0):
                from exporters.step_exporter import _build_bore_2d
                _bp = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
                if _bp is not None:
                    coords = [(x + cx, y + cy) for x, y in list(_bp.exterior.coords)[:-1]]
                    msp.add_lwpolyline(coords, format='xy', close=True,
                                       dxfattribs={'layer': 'BORE'})
                    _bore_drawn = True
            if not _bore_drawn:
                msp.add_circle(center=(cx, cy, 0.0), radius=bore_mm / 2.0,
                               dxfattribs={'layer': 'BORE'})

        if spoke_count >= 2 and spoke_width_mm > 0.0:
            R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
            R_hub = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
            R_rim = max(R_hub + 0.5, R_tooth_root - rim_depth_mm)
            msp.add_circle(center=(cx, cy, 0.0), radius=R_hub, dxfattribs={'layer': 'BORE'})
            sp_attr = {'layer': 'SPOKES'}
            for void in _spoke_void_segments(R_hub, R_rim, spoke_count, spoke_width_mm,
                                              fillet_tip_mm=fillet_tip_mm,
                                              fillet_base_mm=fillet_base_mm):
                for seg in void:
                    ds = _seg_to_dxf(seg)
                    if ds is None:
                        continue
                    if ds[0] == 'line':
                        _, p1, p2 = ds
                        msp.add_line((p1[0] + cx, p1[1] + cy, 0),
                                     (p2[0] + cx, p2[1] + cy, 0), dxfattribs=sp_attr)
                    else:
                        _, center, r, sa, ea = ds
                        msp.add_arc(center=(center[0] + cx, center[1] + cy, 0),
                                    radius=r, start_angle=sa, end_angle=ea,
                                    dxfattribs=sp_attr)

    # P1 at origin, P2 at operating center distance — matches belt geometry exactly
    _draw_pulley(0.0, 0.0, num_teeth1, bore_mm1, clearance_mm1, backlash_mm1,
                 print_extra_mm1, spoke_count1, spoke_width_mm1, spoke_hub_od_mm1,
                 rim_depth_mm1, fillet_tip_mm1, fillet_base_mm1,
                 flat_depth_mm1, keyway_w_mm1, keyway_h_mm1, phi=phi_left)

    if num_teeth2 > 0:
        _draw_pulley(center_dist_mm, 0.0, num_teeth2, bore_mm2, clearance_mm2, backlash_mm2,
                     print_extra_mm2, spoke_count2, spoke_width_mm2, spoke_hub_od_mm2,
                     rim_depth_mm2, fillet_tip_mm2, fillet_base_mm2,
                     flat_depth_mm2, keyway_w_mm2, keyway_h_mm2, phi=phi_right)

    # Belt wraps around P1 at x=0 and P2 at x=center_dist_mm — no shift needed
    try:
        outer_segs, inner_segs, _n_belt, _belt_spec, _C = belt_outline_segments(
            family, pitch, num_teeth1, n2, center_dist_mm)
        _segs_to_dxf(msp, outer_segs, {'layer': 'BELT_BACK'})
        # Draw all belt teeth exactly as computed. The geometry math already
        # handles the transition zone: _interp_at maps each tooth's local (bx,by)
        # coordinates through a C1-continuous pitch line (tangent-continuous at
        # the straight/arc junction), so the groove/fillet naturally bends from
        # arc geometry to straight geometry. Tooth bodies (below OD) are rigid;
        # the land/fillet (at OD) flexes smoothly — both are already correct.
        _segs_to_dxf(msp, inner_segs, {'layer': 'BELT_TEETH'})
    except Exception:
        pass  # belt geometry unavailable for this family — skip silently

    return _serialise_dxf(doc)


def generate_rim_layer_dxf(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 2.0,
) -> bytes:
    """Return DXF bytes for the rim ring layer.

    Layers:
      PROFILE   — outer toothed profile
      RIM_INNER — inner rim circle at R_tooth_root − rim_depth  (blue, color 5)
      HUB       — spoke hub OD circle                           (green, color 3)
      BORE      — bore circle                                   (red, color 1)
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}'")

    segs, R_OD, _edge_a, wrapped = pulley_outline_segments(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS']   = 4   # mm
    doc.header['$MEASUREMENT'] = 1  # metric

    msp = doc.modelspace()
    doc.layers.new('PROFILE',   dxfattribs={'color': 7, 'linetype': 'Continuous'})
    doc.layers.new('RIM_INNER', dxfattribs={'color': 5, 'linetype': 'Continuous'})
    doc.layers.new('HUB',       dxfattribs={'color': 3, 'linetype': 'Continuous'})
    doc.layers.new('BORE',      dxfattribs={'color': 1, 'linetype': 'Continuous'})

    # Outer toothed profile
    _segs_to_dxf(msp, segs, {'layer': 'PROFILE'})

    R_bore       = bore_mm / 2.0
    R_tooth_root = min(math.hypot(x, y) for x, y in wrapped) if wrapped else R_OD
    R_hub_spoke  = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (R_bore + 1.0)
    R_rim_inner  = max(R_hub_spoke + 0.5, R_tooth_root - rim_depth_mm)

    msp.add_circle(center=(0.0, 0.0, 0.0), radius=R_rim_inner,
                   dxfattribs={'layer': 'RIM_INNER'})
    if R_hub_spoke > R_bore + 0.1:
        msp.add_circle(center=(0.0, 0.0, 0.0), radius=R_hub_spoke,
                       dxfattribs={'layer': 'HUB'})
    if R_bore > 0:
        msp.add_circle(center=(0.0, 0.0, 0.0), radius=R_bore,
                       dxfattribs={'layer': 'BORE'})

    return _serialise_dxf(doc)


def generate_belt_dxf(
    family: str,
    pitch: str,
    n_teeth: int = 3,
) -> bytes:
    """
    DXF export of the belt tooth cross-section profile (single-pulley / belt-profile view).
    Exports the belt outline as a closed LWPOLYLINE on layer BELT_PROFILE.
    """
    if family == 'HTD':
        pts, _ = generate_h_belt_profile('H' + pitch, n_teeth=n_teeth)
    elif family == 'GT':
        pts, _ = generate_g_belt_profile('G' + pitch, n_teeth=n_teeth)
    elif family == 'STD':
        pts, _ = generate_s_belt_profile('S' + pitch, n_teeth=n_teeth)
    elif family == 'RPP':
        pts, _ = generate_r_belt_profile('R' + pitch, n_teeth=n_teeth)
    elif family == 'T':
        pts, _ = generate_t_belt_profile(pitch, n_teeth=n_teeth)
    elif family == 'AT':
        pts, _ = generate_at_belt_profile(pitch, n_teeth=n_teeth)
    elif family == 'Imperial':
        pts, _ = generate_imperial_belt_profile(pitch, n_teeth=n_teeth)
    else:
        raise ValueError(f"Belt DXF not supported for family '{family}'")

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    msp = doc.modelspace()
    doc.layers.new('BELT_PROFILE', dxfattribs={'color': 5, 'linetype': 'Continuous'})

    msp.add_lwpolyline(
        [(x, y) for x, y in pts],
        format='xy',
        close=True,
        dxfattribs={'layer': 'BELT_PROFILE'},
    )
    return _serialise_dxf(doc)


def generate_belt_dxf_dual(
    family: str,
    pitch: str,
    num_teeth1: int,
    num_teeth2: int,
    bore_mm1: float,
    bore_mm2: float,
    clearance_mm1: float = 0.0,
    backlash_mm1: float = 0.0,
    print_extra_mm1: float = 0.0,
    clearance_mm2: float = 0.0,
    backlash_mm2: float = 0.0,
    print_extra_mm2: float = 0.0,
    center_dist_mm: float = 100.0,
) -> bytes:
    """
    DXF export of the two-pulley belt layout.
    Exports:
      BELT_BACK   — outer belt surface: true LINE + ARC entities
      BELT_TEETH  — inner toothed surface: true SPLINE entities (one per tooth)
    Geometry centred so pulley 1 is at x_offset (left), pulley 2 at right.
    """
    key      = PROFILE_KEY_PREFIX.get(family, '') + pitch
    spec     = PULLEY_SPECS[key]
    pitch_val = spec['pitch']

    R_pitch1 = num_teeth1 * pitch_val / (2.0 * math.pi)
    R_pitch2 = num_teeth2 * pitch_val / (2.0 * math.pi)
    center_dist_mm = max(center_dist_mm, R_pitch1 + R_pitch2)

    # Centre the belt around x=0 (left pulley at -C/2, right at +C/2)
    cx1 = -center_dist_mm / 2.0

    # Get segments from the shared geometry layer, then shift by cx1 so that
    # the belt is centred in the drawing (belt_outline_segments places the
    # left pulley at x=0; we want it at x=cx1).
    outer_segs, inner_segs, _n_belt, _spec, C = belt_outline_segments(
        family, pitch, num_teeth1, num_teeth2, center_dist_mm
    )

    def _shift_seg(seg):
        """Translate a segment by cx1 in X (Y unchanged)."""
        kind = seg[0]
        if kind == 'line':
            _, x0, y0, x1, y1 = seg
            return ('line', x0 + cx1, y0, x1 + cx1, y1)
        elif kind == 'arc':
            _, cxx, cy, r, (sx, sy), (mx, my), (ex, ey) = seg
            return ('arc', cxx + cx1, cy, r,
                    (sx + cx1, sy), (mx + cx1, my), (ex + cx1, ey))
        else:  # spline
            _, pts = seg
            return ('spline', [(x + cx1, y) for x, y in pts])

    outer_shifted = [_shift_seg(s) for s in outer_segs]
    inner_shifted = [_shift_seg(s) for s in inner_segs]

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    msp = doc.modelspace()
    doc.layers.new('BELT_BACK',  dxfattribs={'color': 5, 'linetype': 'Continuous'})
    doc.layers.new('BELT_TEETH', dxfattribs={'color': 3, 'linetype': 'Continuous'})

    _segs_to_dxf(msp, outer_shifted, {'layer': 'BELT_BACK'})
    _segs_to_dxf(msp, inner_shifted, {'layer': 'BELT_TEETH'})

    return _serialise_dxf(doc)


def _rdp(points, tol):
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(points) <= 2:
        return points
    x1, y1 = points[0]
    x2, y2 = points[-1]
    dx, dy = x2 - x1, y2 - y1
    seg_len = math.hypot(dx, dy)
    dmax, idx = 0.0, 0
    for i, (x, y) in enumerate(points[1:-1], 1):
        if seg_len < 1e-12:
            d = math.hypot(x - x1, y - y1)
        else:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len**2))
            d = math.hypot(x - x1 - t * dx, y - y1 - t * dy)
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        return _rdp(points[:idx + 1], tol)[:-1] + _rdp(points[idx:], tol)
    return [points[0], points[-1]]


def generate_belt_dxf_for_step(
    family: str,
    pitch: str,
    num_teeth1: int,
    num_teeth2: int,
    center_dist_mm: float,
    back_tolerance_mm: float = 0.02,
    teeth_tolerance_mm: float = 0.05,
) -> bytes:
    """
    DXF for small_step belt extrusion.

      BELT_BACK  — closed outer boundary → LINE segments (back_tolerance_mm RDP)
      BELT_TEETH — inner toothed boundary → single SPLINE entity

    The SPLINE fit-points list is inner_pts + [inner_pts[0]], so the first and
    last fit point are the same.  collect_segments() reads start=end=inner_pts[0],
    and build_one_loop closes the loop immediately as a single segment.

    small_step calls fit_to_bspline on the fit points.  The cubic B-spline
    collocation matrix has half-bandwidth p=3, so the banded LU solver runs in
    O(9n) instead of the old O(n³) dense solver.  This eliminates the precision
    loss that previously caused teeth to disappear on the straight sections.

    Returns empty bytes when the family has no belt tooth data.
    """
    belt_ring_poly, tooth_polys, _, _ = build_two_pulley_belt(
        family, pitch, num_teeth1, num_teeth2, center_dist_mm
    )
    if not belt_ring_poly:
        return b''

    def _simplify(pts, tol):
        reduced = _rdp(pts + [pts[0]], tol)
        if len(reduced) > 1 and reduced[0] == reduced[-1]:
            reduced = reduced[:-1]
        return reduced if len(reduced) >= 3 else pts

    belt_ring_poly = _simplify(belt_ring_poly, back_tolerance_mm)
    inner_pts = _simplify(tooth_polys[0], teeth_tolerance_mm) if tooth_polys else []

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    msp = doc.modelspace()
    doc.layers.new('BELT_BACK',  dxfattribs={'color': 5, 'linetype': 'Continuous'})
    doc.layers.new('BELT_TEETH', dxfattribs={'color': 3, 'linetype': 'Continuous'})

    # Outer boundary: LINE segments (smooth curve, no tooth detail needed)
    for i in range(len(belt_ring_poly)):
        x0, y0 = belt_ring_poly[i]
        x1, y1 = belt_ring_poly[(i + 1) % len(belt_ring_poly)]
        msp.add_line((x0, y0), (x1, y1), dxfattribs={'layer': 'BELT_BACK'})

    # Inner toothed boundary: split into short SPLINE chunks so CAD viewers
    # tessellate each segment at enough density to show individual teeth.
    # A single full-belt B-spline (~836 pts, ~600mm) gets the same fixed
    # tessellation count as a short segment, making teeth vanish in the viewer.
    # At 0.05mm Python simplification: ~12 pts/tooth. MAX_CHUNK=60 → ~5 teeth
    # per segment → viewer renders each at sufficient density.
    # Adjacent chunks share their boundary point exactly so build_one_loop
    # in small_step chains them into one closed void loop.
    if inner_pts:
        _MAX_CHUNK = 60
        n_inner = len(inner_pts)
        i = 0
        while i < n_inner:
            end = min(i + _MAX_CHUNK, n_inner)
            # Closing point: next chunk start (or loop back to inner_pts[0])
            closing = inner_pts[end % n_inner]
            chunk = inner_pts[i:end] + [closing]
            msp.add_spline(
                [(x, y, 0.0) for x, y in chunk],
                dxfattribs={'layer': 'BELT_TEETH'},
            )
            i = end

    return _serialise_dxf(doc)
