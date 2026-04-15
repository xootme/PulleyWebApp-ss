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
) -> bytes:
    """
    Return a DXF file as bytes.

    Parameters match generate_svg() exactly so app.py can call both with the
    same arguments.
    """
    key = _profile_key(family, pitch)
    if key not in PULLEY_SPECS:
        raise ValueError(f"Unknown profile key '{key}' for {family} / {pitch}")

    spec      = PULLEY_SPECS[key]
    pitch_val = spec['pitch']

    clearance_mm   = max(-pitch_val, min(clearance_mm,   pitch_val))
    backlash_mm    = max(-pitch_val, min(backlash_mm,    pitch_val))
    print_extra_mm = max(0.0,        min(print_extra_mm, pitch_val))

    container    = generate_profile_groove(family, key, num_teeth,
                                           clearance_mm, print_extra_mm, backlash_mm)
    groove_prims = container.primitives[1:-1]
    groove_pts   = _build_groove_points(groove_prims, family)
    wrapped, R_OD, edge_a = wrap_groove_to_pulley(groove_pts, spec,
                                                   num_teeth, print_extra_mm)

    t_ang = 2.0 * math.pi / num_teeth

    # ── DXF document ────────────────────────────────────────────────────────
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4          # 4 = millimetres
    doc.header['$MEASUREMENT'] = 1       # 1 = metric

    msp = doc.modelspace()

    doc.layers.new('PROFILE', dxfattribs={'color': 7, 'linetype': 'Continuous'})
    doc.layers.new('BORE',    dxfattribs={'color': 1, 'linetype': 'Continuous'})
    doc.layers.new('SPOKES',  dxfattribs={'color': 3, 'linetype': 'Continuous'})

    prof = {'layer': 'PROFILE'}

    # ── Pulley profile ───────────────────────────────────────────────────────
    # Walk the same sequence the SVG exporter does:
    #   for each tooth:
    #     [LINE from prev OD-arc-end → groove start]   (implicit SVG 'L')
    #     LINEs through groove points
    #     ARC  from groove end → next OD-arc-end  (SVG 'A', sweep CW)
    #   closing LINE from last OD-arc-end → first groove start  (SVG 'Z')

    first_groove_start = None   # used to close the profile at the end
    prev_od_end        = None   # OD-arc endpoint of the previous tooth

    for i in range(num_teeth):
        th        = i * t_ang
        tooth_pts = [_rot(gx, gy, th) for gx, gy in wrapped]

        # OD-arc end for this tooth (start of next tooth's land)
        a_end  = th + t_ang - edge_a
        od_end = (R_OD * math.sin(a_end), R_OD * math.cos(a_end))

        # Connection: previous OD-arc-end → this groove start
        if i == 0:
            first_groove_start = tooth_pts[0]
        else:
            _add_line(msp, prev_od_end, tooth_pts[0], prof)

        # Tooth groove: straight line segments between sampled points
        for j in range(len(tooth_pts) - 1):
            _add_line(msp, tooth_pts[j], tooth_pts[j + 1], prof)

        # OD arc from groove end to od_end — arc is CW in standard math.
        # DXF arcs are CCW, so swap start / end angles.
        last_pt = tooth_pts[-1]
        start_ang = _math_angle(*od_end)    # CCW start  = CW destination
        end_ang   = _math_angle(*last_pt)   # CCW end    = CW origin

        msp.add_arc(
            center=(0.0, 0.0, 0.0),
            radius=R_OD,
            start_angle=start_ang,
            end_angle=end_ang,
            dxfattribs=prof,
        )

        prev_od_end = od_end

    # Close: last OD-arc-end → first groove start  (SVG Z)
    _add_line(msp, prev_od_end, first_groove_start, prof)

    # ── Bore circle ─────────────────────────────────────────────────────────
    if bore_mm > 0:
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
      BELT_BACK   — outer belt surface polyline
      BELT_TEETH  — inner toothed surface polyline
    Both closed. Geometry centred so pulley 1 is at x_offset (left), pulley 2 at right.
    """
    from geometry.pulley_geometry import PULLEY_SPECS, PROFILE_KEY_PREFIX
    key  = PROFILE_KEY_PREFIX.get(family, '') + pitch
    spec = PULLEY_SPECS[key]
    pitch_val = spec['pitch']

    R_pitch1 = num_teeth1 * pitch_val / (2.0 * math.pi)
    R_pitch2 = num_teeth2 * pitch_val / (2.0 * math.pi)
    min_c = R_pitch1 + R_pitch2
    center_dist_mm = max(center_dist_mm, min_c)

    cx1 = -center_dist_mm / 2.0

    belt_ring, tooth_polys, _phi_l, _phi_r = build_two_pulley_belt(
        family, pitch, num_teeth1, num_teeth2,
        center_dist_mm, x_offset=cx1,
    )

    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    msp = doc.modelspace()
    doc.layers.new('BELT_BACK',  dxfattribs={'color': 5, 'linetype': 'Continuous'})
    doc.layers.new('BELT_TEETH', dxfattribs={'color': 3, 'linetype': 'Continuous'})

    if belt_ring:
        msp.add_lwpolyline(
            [(x, y) for x, y in belt_ring],
            format='xy', close=True,
            dxfattribs={'layer': 'BELT_BACK'},
        )
    for tp in tooth_polys:
        msp.add_lwpolyline(
            [(x, y) for x, y in tp],
            format='xy', close=True,
            dxfattribs={'layer': 'BELT_TEETH'},
        )

    return _serialise_dxf(doc)
