"""
flange_geometry.py
------------------
2D cross-section profiles (r, Z) for timing-pulley flanges.

All profiles are in the r-Z plane (r = radial distance from pulley axis,
Z = axial direction). They are meant to be revolved 360° around the Z-axis
to produce the 3D solid.

Coordinate convention
---------------------
  Z = 0  : the datum reference face (tooth-top for top flanges,
            tooth-bottom for bottom flanges).
  Z > 0  : away from the belt (upward for top, downward for bottom
            — the caller applies the final Z translation).

3D-print flange cross-section (hexagon)
-----------------------------------------
The belt-side (inner) surface of the flange is the angled face that guides
the belt.  Looking at the profile going from left (inner) to right (outer):

    F──────────E
    │          ╲  ← angled surface, slope = tan(Flange_Angle)
    │            ╲
    A──B──────────D──C   ← flat bottom (Z=0)
                  └─── r_tooth_OD
    └── r_inner

Vertices:
    A = (r_inner,    0)               inner bottom
    B = (r_tooth_OD, 0)               where flat inner section meets the wedge
    C = (r_outer,    0)               outer bottom
    D = (r_outer,    z_top)           outer top  (z_top = z_angled + Flange_Height)
    E = (r_tooth_OD, Flange_Height)   inner edge of angled surface
    F = (r_inner,    Flange_Height)   inner top

Angled surface D→E:
    ΔZ / Δr = (Flange_Height − z_top) / (r_tooth_OD − r_outer)
            = (−z_angled) / (−Rim_Radius) = tan(Flange_Angle)  ✓

Metal flange cross-section (thin-shell solid)
----------------------------------------------
The plate has material thickness `plate_t`.  The cross-section traces both
the top (outer) surface and the bottom (inner) surface of the plate to form
a closed polygon.

Flat section: both surfaces horizontal, top at Z=plate_t, bottom at Z=0.
Bend section: circular arc from horizontal to Flange_Angle.
  Bend_Radius (user parameter) = horizontal distance from tooth OD to end of bend.
  Inner arc radius  R_arc = Bend_Radius / sin(Flange_Angle).
  Arc centre in (r,Z): (r_tooth_OD, R_arc).
Straight section: continues at Flange_Angle from bend end to Rim_Radius.
"""
import math
from typing import List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Inner-radius helpers
# ---------------------------------------------------------------------------

def flange_inner_r_3dprint(
    bore_mm: float,
    hub_od_mm: float,
    spokes_enabled: bool,
    spoke_hub_od_mm: float,
    r_tooth_OD: float = 0.0,
    rim_depth_mm: float = 0.0,
) -> float:
    """Inner radius of a 3D-print flange (same rule for top and bottom).

    Priority: spokes (inner rim ring face) > hub > bore.
    When spokes are active the flange stops at the inner face of the solid rim
    ring, i.e. R_tooth_OD − rim_depth_mm.  This is the outer boundary of the
    spoke void, not the hub boss surface.
    """
    if spokes_enabled and r_tooth_OD > 0.0 and rim_depth_mm > 0.0:
        return r_tooth_OD - rim_depth_mm
    if hub_od_mm > bore_mm:
        return hub_od_mm / 2.0
    return bore_mm / 2.0


def flange_inner_r_metal_top(
    bore_mm: float,
    hub_od_mm: float,
    spokes_enabled: bool,
    spoke_hub_od_mm: float,
    r_tooth_OD: float = 0.0,
    rim_depth_mm: float = 0.0,
) -> float:
    """Inner radius of the metal TOP flange plate.

    When spokes active, stops at inner face of rim ring (R_tooth_OD − rim_depth_mm).
    Otherwise extends to hub OD or bore.
    """
    if spokes_enabled and r_tooth_OD > 0.0 and rim_depth_mm > 0.0:
        return r_tooth_OD - rim_depth_mm
    if hub_od_mm > bore_mm:
        return hub_od_mm / 2.0
    return bore_mm / 2.0


def flange_inner_r_metal_bottom(
    bore_mm: float,
    spokes_enabled: bool,
    spoke_hub_od_mm: float,
    r_tooth_OD: float = 0.0,
    rim_depth_mm: float = 0.0,
) -> float:
    """Inner radius of the metal BOTTOM flange plate.

    When spokes active, stops at inner face of rim ring (R_tooth_OD − rim_depth_mm).
    Otherwise extends to bore.
    """
    if spokes_enabled and r_tooth_OD > 0.0 and rim_depth_mm > 0.0:
        return r_tooth_OD - rim_depth_mm
    return bore_mm / 2.0


def hub_intersects_flange(
    r_tooth_OD: float,
    hub_od_mm: float,
    spokes_enabled: bool,
    spoke_hub_od_mm: float,
    tooth_ht: float,
) -> bool:
    """Return True when the hub encroaches far enough to trigger the warning.

    Conditions (either):
      • Spokes active and hub OD extends past the spokes' inner rim.
      • Spokes inactive and hub OD is within 10 mm of the groove-bottom circle
        (i.e. hub radius > R_groove_bottom − 10 mm = R_tooth_OD − tooth_ht − 10 mm).
    """
    if hub_od_mm <= 0.0:
        return False
    R_hub = hub_od_mm / 2.0
    if spokes_enabled and spoke_hub_od_mm > 0.0:
        return R_hub > spoke_hub_od_mm / 2.0
    # No spokes: warn when hub is within 10 mm of groove-bottom circle
    R_groove_bottom = r_tooth_OD - tooth_ht
    return R_hub > (R_groove_bottom - 10.0)


# ---------------------------------------------------------------------------
# 3D-print flange profile
# ---------------------------------------------------------------------------

def profile_3dprint(
    r_inner: float,
    r_tooth_OD: float,
    rim_radius_mm: float,
    flange_angle_deg: float,
    flange_height_mm: float,
) -> List[Tuple[float, float]]:
    """Return the 2D cross-section polygon (r, Z) for a 3D-print flange.

    Cross-section (top flange, Z=0 at belt-top face):

        E──────────────D
        │               │  ← vertical outer wall (Z-axis aligned)
        │        ╱      │
        A────────B       │
                  ╲      │
                   C─────┘  (same point as D at r_outer)

    Vertices:
        A = (r_inner,    0)                inner bottom (flat face against belt)
        B = (r_tooth_OD, 0)                end of flat inner section
        C = (r_outer,    z_angled)         base of vertical lip (bottom of outer wall)
        D = (r_outer,    flange_height_mm) top of outer wall
        E = (r_inner,    flange_height_mm) inner top (flat top face)

    The BOTTOM face is flat from A→B (against belt), then slopes upward B→C.
    The TOP face E→D is flat across the full width.
    The outer wall C→D is vertical (parallel to Z axis).

    To get the BOTTOM flange negate all Z values: the bottom face becomes flat
    (resting on the pulley underside) and the top slopes downward at tooth OD.

    Parameters
    ----------
    r_inner        : inner radius of the flange (bore / hub / spoke-hub edge).
    r_tooth_OD     : outer radius at the tooth tops (= pulley OD / 2).
    rim_radius_mm  : horizontal reach of the angled section beyond r_tooth_OD.
    flange_angle_deg: angle of the bottom ramp from horizontal (8–25°).
    flange_height_mm: total height of the flange (must exceed z_angled for a
                      non-zero vertical lip; clamped automatically).
    """
    angle_rad = math.radians(flange_angle_deg)
    r_outer   = r_tooth_OD + rim_radius_mm
    z_angled  = rim_radius_mm * math.tan(angle_rad)   # Z at base of outer lip

    # Ensure the lip has at least 0.1 mm of vertical wall
    flange_height_mm = max(flange_height_mm, z_angled + 0.1)

    # Clamp: inner must be inside r_tooth_OD
    r_inner = min(r_inner, r_tooth_OD - 0.5)
    r_inner = max(r_inner, 0.5)

    return [
        (r_inner,    0.0),               # A: inner bottom (flat face against belt)
        (r_tooth_OD, 0.0),               # B: end of flat inner section
        (r_outer,    z_angled),          # C: base of vertical lip (angled ramp end)
        (r_outer,    flange_height_mm),  # D: top of vertical lip
        (r_inner,    flange_height_mm),  # E: inner top (flat top face)
    ]


# ---------------------------------------------------------------------------
# Metal flange profile (thin-shell solid, arc approximated by line segments)
# ---------------------------------------------------------------------------

def _arc_points_rz(
    cx: float, cz: float,
    radius: float,
    angle_start_rad: float,
    angle_end_rad: float,
    n_segments: int,
) -> List[Tuple[float, float]]:
    """Sample n_segments+1 points along a circular arc in the r-Z plane."""
    pts = []
    for i in range(n_segments + 1):
        t = i / n_segments
        a = angle_start_rad + (angle_end_rad - angle_start_rad) * t
        pts.append((cx + radius * math.cos(a), cz + radius * math.sin(a)))
    return pts


def profile_metal(
    r_inner: float,
    r_tooth_OD: float,
    rim_radius_mm: float,
    flange_angle_deg: float,
    plate_height_mm: float,
    bend_radius_mm: float,
    arc_segments: int = 16,
) -> List[Tuple[float, float]]:
    """Return the 2D cross-section polygon (r, Z) for a metal flange plate.

    The polygon is a thin-walled solid: it traces the TOP surface (outer face
    of the plate) from inner rim to outer rim, then returns along the BOTTOM
    surface (inner face of the plate).  This closed polygon, when revolved,
    produces the flange solid.

    Coordinate convention
    ---------------------
    Z = 0  : bottom surface of the flat inner section (contact face on pulley).
    Z = plate_height_mm : top surface of the flat inner section.

    The flat section runs from r_inner to r_tooth_OD.
    The bend starts at r_tooth_OD and ends at r_tooth_OD + bend_radius_mm (horizontal).
    The straight angled section continues to r_tooth_OD + rim_radius_mm.
    The outer edge is a rectangle of height plate_height_mm.

    Parameters
    ----------
    r_inner        : inner hole radius.
    r_tooth_OD     : pulley OD radius (bend starts here).
    rim_radius_mm  : horizontal reach of flange beyond tooth OD.
    flange_angle_deg: flange angle from horizontal (8–25°).
    plate_height_mm: material / stock thickness.
    bend_radius_mm : horizontal distance from tooth OD to end of bend.
                     Actual inner arc radius = bend_radius_mm / sin(angle_rad).
    arc_segments   : number of line segments approximating the bend arc.
    """
    angle_rad = math.radians(flange_angle_deg)
    pt = plate_height_mm   # shorthand

    # Inner arc radius (inner surface of bend, see module docstring)
    R_arc_inner = bend_radius_mm / math.sin(angle_rad)
    R_arc_outer = R_arc_inner + pt

    # Arc centre in (r, Z): directly above (r_tooth_OD, pt) by R_arc_inner
    #   i.e. at (r_tooth_OD, pt + R_arc_inner)
    arc_cr = r_tooth_OD
    arc_cz = pt + R_arc_inner

    # Inner arc: spans from -π/2 (pointing DOWN from centre = start at (r_tooth_OD, pt))
    #            to -π/2 + angle_rad
    a_start = -math.pi / 2.0
    a_end   = -math.pi / 2.0 + angle_rad

    inner_arc = _arc_points_rz(arc_cr, arc_cz, R_arc_inner, a_start, a_end, arc_segments)
    outer_arc = _arc_points_rz(arc_cr, arc_cz, R_arc_outer, a_start, a_end, arc_segments)

    # End of inner arc → direction vector of straight section
    r_bend_end_inner, z_bend_end_inner = inner_arc[-1]
    r_bend_end_outer, z_bend_end_outer = outer_arc[-1]

    # Straight section: from bend end to Rim_Radius beyond tooth OD
    straight_reach = rim_radius_mm - bend_radius_mm   # horizontal distance remaining
    if straight_reach < 0.0:
        straight_reach = 0.0

    dr = straight_reach
    dz = straight_reach * math.tan(angle_rad)

    r_straight_end_inner = r_bend_end_inner + dr
    z_straight_end_inner = z_bend_end_inner + dz
    r_straight_end_outer = r_bend_end_outer + dr
    z_straight_end_outer = z_bend_end_outer + dz

    # ── Assemble polygon: TOP surface (outer face) then BOTTOM surface (inner face) ──
    # Going from inner-rim top-left, tracing the outside of the plate all the way
    # around, then back along the inside.

    # 1. Flat top surface: (r_inner, pt) → (r_tooth_OD, pt)
    top_surface = [(r_inner, pt), (r_tooth_OD, pt)]

    # 2. Top surface of outer arc (tracing outer_arc from start to end)
    top_surface += [(r, z) for r, z in outer_arc]

    # 3. Top surface of straight section
    top_surface.append((r_straight_end_outer, z_straight_end_outer))

    # 4. Outer edge (rectangular cut): go down by plate_height_mm
    outer_edge_top    = (r_straight_end_outer, z_straight_end_outer)
    outer_edge_bottom = (r_straight_end_inner, z_straight_end_inner)
    # (already added top via step 3; add bottom here)

    # 5. Bottom surface of straight section (tracing back inward)
    bottom_surface = [outer_edge_bottom]

    # 6. Bottom surface of inner arc (tracing inner_arc end → start, reversed)
    bottom_surface += [(r, z) for r, z in reversed(inner_arc)]

    # 7. Flat bottom surface: (r_tooth_OD, 0) → (r_inner, 0)
    bottom_surface += [(r_tooth_OD, 0.0), (r_inner, 0.0)]

    # 8. Inner edge: (r_inner, 0) → (r_inner, pt) — closes back to start
    # (polygon closes automatically; don't repeat first point)

    polygon = top_surface + bottom_surface
    return polygon
