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
