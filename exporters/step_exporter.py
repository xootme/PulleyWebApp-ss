"""
step_exporter.py
Generates STL 3D exports of timing pulleys using trimesh + shapely.

STEP export requires cadquery-ocp (no Python 3.14 wheels yet); STEP routes
return HTTP 501 until a compatible runtime is available.

Geometry pipeline
-----------------
1. Compute the full 2D pulley outline (tooth grooves + OD arc lands)
   using the same primitives as the SVG / DXF exporters.
2. Build a shapely Polygon from the outline points.
3. Extrude the polygon to belt_height_mm with trimesh.
4. Subtract a bore cylinder using manifold3d boolean difference.
5. Export as binary STL bytes.
"""
import math
import io

import numpy as np
import trimesh
from shapely.geometry import Polygon as ShapelyPolygon, Point as ShapelyPoint
from shapely.geometry.polygon import orient as shapely_orient
from shapely.ops import unary_union as shapely_unary_union

from geometry.pulley_geometry import (
    generate_profile_groove, _build_groove_points,
    wrap_groove_to_pulley, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    build_two_pulley_belt, BELT_FAMILIES,
    pulley_outline_segments, belt_outline_segments,
    getOuterDiameter,
)
from geometry.flange_geometry import (
    profile_3dprint, profile_metal,
    flange_inner_r_3dprint, flange_inner_r_3dprint_bottom,
    flange_inner_r_metal_top, flange_inner_r_metal_bottom,
)
from shapely.affinity import rotate as shapely_rotate

# ── Arc sample resolution ─────────────────────────────────────────────────────
_ARC_STEP_MM = 0.5        # target chord length on OD arc samples (mm)
_BORE_SECTIONS = 64       # facets on bore cylinder

# ── ISO hex nut dimensions: screw_dia_mm → (width_across_flats_mm, nut_height_mm)
_NUT_DIMS_BY_DIA = {
    3:  (5.5,  2.4),
    4:  (7.0,  3.2),
    5:  (8.0,  4.0),
    6:  (10.0, 5.0),
    8:  (13.0, 6.5),
    10: (17.0, 8.0),
}


def _nut_dims(screw_dia_mm: float):
    """Return (waf_mm, nut_height_mm) for the nearest standard metric nut."""
    nominal = min(_NUT_DIMS_BY_DIA, key=lambda k: abs(k - screw_dia_mm))
    return _NUT_DIMS_BY_DIA[nominal]


def _make_frustum(r_bottom: float, r_top: float, height: float, sections: int = 32):
    """Return a trimesh frustum (truncated cone) along the +Z axis.

    Bottom cap at Z=0 with radius r_bottom; top cap at Z=height with radius r_top.
    Used to create 45° chamfer support cones under captured-nut lobes.
    """
    import numpy as np
    angles  = np.linspace(0.0, 2.0 * math.pi, sections, endpoint=False)
    cos_a   = np.cos(angles)
    sin_a   = np.sin(angles)

    bottom   = np.column_stack([r_bottom * cos_a, r_bottom * sin_a, np.zeros(sections)])
    top      = np.column_stack([r_top    * cos_a, r_top    * sin_a, np.full(sections, height)])
    vertices = np.vstack([bottom, top])   # indices 0..(s-1)=bottom, s..(2s-1)=top

    faces = []
    for i in range(sections):
        j = (i + 1) % sections
        faces.append([i,          j,          sections + j])
        faces.append([i,          sections + j, sections + i])

    # Bottom cap (normal –Z)
    cb = len(vertices)
    vertices = np.vstack([vertices, [[0.0, 0.0, 0.0]]])
    for i in range(sections):
        j = (i + 1) % sections
        faces.append([cb, j, i])

    # Top cap (normal +Z)
    ct = len(vertices)
    vertices = np.vstack([vertices, [[0.0, 0.0, height]]])
    for i in range(sections):
        j = (i + 1) % sections
        faces.append([ct, sections + i, sections + j])

    mesh = trimesh.Trimesh(vertices=np.array(vertices, dtype=float),
                           faces=np.array(faces, dtype=np.int32))
    mesh.fix_normals()
    return mesh


def _d_bore_polygon(R_bore: float, flat_depth_mm: float, sections: int = 64) -> ShapelyPolygon:
    """Return a Shapely polygon for a D-shaped bore cross-section.

    The flat face is a chord at X = R_bore - flat_depth_mm (cuts the +X side).
    Points go counter-clockwise so Shapely treats the interior as positive area.
    The implicit closing edge (last→first point) forms the flat face.
    """
    flat_x = R_bore - flat_depth_mm
    # clamp to valid range for acos
    cos_theta = max(-1.0, min(1.0, flat_x / R_bore))
    theta = math.acos(cos_theta)          # angle at the chord intersection points
    # Arc from +theta (top intersection) CCW through left side to 2π-theta (bottom)
    angles = [theta + (2.0 * math.pi - 2.0 * theta) * i / (sections - 1)
              for i in range(sections)]
    pts = [(R_bore * math.cos(a), R_bore * math.sin(a)) for a in angles]
    # pts[0]  = (flat_x, +y_int)  and  pts[-1] = (flat_x, -y_int)
    # Shapely closes with the flat edge: straight line from pts[-1] back to pts[0]
    return ShapelyPolygon(pts)


def _build_bore_2d(bore_mm: float, flat_depth_mm: float = 0.0,
                   keyway_w_mm: float = 0.0, keyway_h_mm: float = 0.0,
                   sections: int = _BORE_SECTIONS):
    """Return a Shapely polygon for the full bore cross-section.

    Starts as a circle (or D-flat chord if flat_depth_mm > 0), then unions a
    keyway rectangle outward from the bore wall.  This is the single source of
    truth used by both the pulley body and the bottom flange so their profiles
    are guaranteed to match.

    Returns None when bore_mm <= 1.0 mm.
    """
    R_bore = bore_mm / 2.0
    if R_bore <= 0.5:
        return None
    if flat_depth_mm > 0.0:
        poly = _d_bore_polygon(R_bore, flat_depth_mm, sections=sections)
    else:
        poly = ShapelyPoint(0, 0).buffer(R_bore, resolution=sections)
    poly = shapely_orient(poly, sign=1.0)
    if keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
        kw_half = keyway_w_mm / 2.0
        kw_rect = ShapelyPolygon([
            (0.0,                  -kw_half),
            (R_bore + keyway_h_mm, -kw_half),
            (R_bore + keyway_h_mm,  kw_half),
            (0.0,                   kw_half),
        ])
        merged = poly.union(kw_rect)
        from shapely.geometry import MultiPolygon as _MP
        if isinstance(merged, _MP):
            merged = max(merged.geoms, key=lambda g: g.area)
        poly = shapely_orient(merged, sign=1.0)
    return poly



def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def _revolve_rz_profile(pts):
    """Revolve a closed (r, z) polygon 360° around the Z axis using CadQuery.

    pts — list of (r, z) tuples in the r-Z plane.  The polygon is closed
    automatically; do not repeat the first point at the end.
    """
    import cadquery as cq
    local = [(float(r), float(z)) for r, z in pts]
    return (cq.Workplane('XZ')
            .polyline(local)
            .close()
            .revolve(360, (0, 0), (0, 1)))


def _nub_circle_r_step(r_tooth_OD: float, tooth_ht: float, nub_dia_mm: float) -> float:
    """Radius of the nub/socket centre circle (same rule as flange_exporter)."""
    r_groove_bottom = r_tooth_OD - tooth_ht
    margin = min(tooth_ht, 3.0)
    return r_groove_bottom - margin - nub_dia_mm / 2.0


def _segs_to_cq_sketch(segments, base_wp, inner_r=None):
    """
    Translate a pulley_outline_segments() or belt_outline_segments() segment
    list into a CadQuery sketch on *base_wp*.

    Segment types handled:
      ('spline', [(x,y), ...])                   → wp.spline(..., includeCurrent=True)
      ('arc', cx,cy,r, (sx,sy),(mx,my),(ex,ey)) → wp.threePointArc((mx,my),(ex,ey))
      ('line', x0,y0, x1,y1)                    → wp.lineTo(x1, y1)

    The first spline's first point is used as the moveTo start.
    After all segments, wp.close() seals the wire.
    If inner_r is given, a concentric bore circle is added (for pulley sketches).

    All coordinates are in compass convention (x = r·sin θ, y = r·cos θ),
    which is the same frame CadQuery uses (XY plane, Y-up).
    """
    # Find the start point — first point of the first spline or line
    first = segments[0]
    if first[0] == 'spline':
        sx0, sy0 = first[1][0]
    elif first[0] == 'line':
        sx0, sy0 = first[1], first[2]
    else:   # arc — start is the 'start' point tuple
        sx0, sy0 = first[4]

    wp = base_wp.moveTo(sx0, sy0)

    for seg in segments:
        kind = seg[0]
        if kind == 'spline':
            _, pts = seg
            # includeCurrent=True: start from current wp position (= pts[0]),
            # so the spline connects to the previous arc with zero gap.
            wp = wp.spline(pts[1:], includeCurrent=True)
        elif kind == 'arc':
            _, _cx, _cy, _r, _start, mid, end = seg
            wp = wp.threePointArc(mid, end)
        elif kind == 'line':
            _, _x0, _y0, x1, y1 = seg
            wp = wp.lineTo(x1, y1)

    sketch = wp.close()
    if inner_r is not None:
        sketch = sketch.circle(inner_r)
    return sketch


def _build_outline_points(family, pitch, num_teeth,
                          clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0,
                          arc_step_mm=None):
    """
    Return a closed list of (x, y) mm points forming the full pulley outline:
      tooth groove segments (dense sampled)  +  OD arc lands (arc-sampled).

    arc_step_mm overrides _ARC_STEP_MM for OD arc sampling (use larger values
    for STEP export to reduce entity count without affecting groove accuracy).

    The pulley is centred at the origin; x-right, y-up (same as SVG / DXF).
    """
    arc_step = arc_step_mm if arc_step_mm is not None else _ARC_STEP_MM

    key  = _profile_key(family, pitch)
    spec = PULLEY_SPECS[key]
    pv   = spec['pitch']

    clearance_mm   = max(-pv, min(clearance_mm,   pv))
    backlash_mm    = max(-pv, min(backlash_mm,    pv))
    print_extra_mm = max(0.0, min(print_extra_mm, pv))

    container    = generate_profile_groove(family, key, num_teeth,
                                           clearance_mm, print_extra_mm, backlash_mm)
    groove_prims = container.primitives[1:-1]
    groove_pts   = _build_groove_points(groove_prims, family)
    wrapped, R_OD, edge_a = wrap_groove_to_pulley(groove_pts, spec,
                                                   num_teeth, print_extra_mm)

    t_ang = 2.0 * math.pi / num_teeth

    def rot(x, y, theta):
        c, s = math.cos(theta), math.sin(theta)
        return x * c + y * s, -x * s + y * c

    outline = []
    for i in range(num_teeth):
        th        = i * t_ang
        tooth_pts = [rot(gx, gy, th) for gx, gy in wrapped]
        outline.extend(tooth_pts)

        # OD arc: from the end of groove[i] to the start of groove[i+1].
        # Use absolute compass angles relative to pulley centre.
        # Groove i ends at compass angle  i*t_ang + edge_a.
        # Groove i+1 starts at compass angle  (i+1)*t_ang - edge_a.
        arc_a_start = i * t_ang + edge_a
        arc_a_end   = (i + 1) * t_ang - edge_a

        # The arc span should be positive; clamp degenerate case.
        arc_span = arc_a_end - arc_a_start
        if arc_span <= 0.0:
            continue   # teeth so close together there is no OD land

        arc_len = R_OD * arc_span
        n_arc   = max(2, int(math.ceil(arc_len / arc_step)))

        for k in range(1, n_arc + 1):
            a = arc_a_start + arc_span * k / n_arc
            outline.append((R_OD * math.sin(a), R_OD * math.cos(a)))

    # Remove consecutive duplicate / near-duplicate points that cause
    # degenerate triangles and non-manifold meshes.
    _MIN_EDGE = 1e-4   # mm
    cleaned = [outline[0]]
    for pt in outline[1:]:
        dx = pt[0] - cleaned[-1][0]
        dy = pt[1] - cleaned[-1][1]
        if math.hypot(dx, dy) > _MIN_EDGE:
            cleaned.append(pt)
    # Also check the closing edge (last → first).
    if math.hypot(cleaned[-1][0] - cleaned[0][0],
                  cleaned[-1][1] - cleaned[0][1]) < _MIN_EDGE:
        cleaned.pop()

    return cleaned, R_OD, spec


def _add_hub_and_bore(body: trimesh.Trimesh,
                      belt_height_mm: float,
                      bore_mm: float,
                      hub_od_mm: float = 0.0,
                      hub_height_mm: float = 0.0,
                      screw_dia_mm: float = 0.0,
                      screw_count: int = 0,
                      captured_nut: bool = False,
                      flat_depth_mm: float = 0.0,
                      keyway_w_mm: float = 0.0,
                      keyway_h_mm: float = 0.0,
                      hub_z_start: float = None,
                      flange_ext_mm: float = 0.0) -> trimesh.Trimesh:
    """
    Union a hub boss onto `body`, subtract the bore through the full height,
    then optionally drill radial set-screw holes and (for captured_nut=True)
    rectangular nut pockets that open from the hub top face.

    Captured nut geometry
    ---------------------
    The nut axis is radial (same as the set screw).  The nut sits against the
    shaft (inner face at bore_r).  The pocket opens from the hub top face so
    the nut can be inserted axially (dropped in from above).  Two flat hex
    faces are vertical (perpendicular to the hub top), guiding the nut in.

      Hex orientation: "pointy top" when looking along nut axis
          → flat faces face ±tangential (Y), vertices face ±Z
          → the two ±Y flat faces are vertical = perpendicular to hub top ✓

      Pocket shape  (hex prism, drops from hub top):
          radial depth  (X) = t_nut + 0.5 clearance   (nut thickness)
          tangential width (Y) = waf  + 0.5 clearance  (flat-to-flat)
          axial depth   (Z) = 2·R_pkt                  (hex circumradius × 2, tip-to-tip with clearance)

      Hub height auto-raised if shorter than 2·R_circ + 0.5.

      Hub wall requirement: ≥ 2 × t_nut of material between nut outer face
      and hub OD.  If hub_r < bore_r + 3·t_nut the hub becomes oblong:
      Shapely lobes extend the boss in each screw direction so the outer
      wall has enough material.

    Screw angular spacing:
      captured_nut = True  → 180°  (screws opposite each other)
      captured_nut = False → 90°
    """
    R_bore    = bore_mm / 2.0
    hub_valid = hub_height_mm > 0.0 and hub_od_mm > bore_mm
    R_hub     = hub_od_mm / 2.0 if hub_valid else 0.0

    if hub_z_start is None:
        hub_z_start = belt_height_mm

    do_screws = hub_valid and screw_dia_mm > 0.0 and screw_count > 0

    # ── Captured nut pre-calculations ────────────────────────────────────────
    if do_screws and captured_nut:
        waf, t_nut = _nut_dims(screw_dia_mm)
        R_circ = waf / math.sqrt(3)        # hex circumradius (centre → vertex)
        tip_to_tip = 2.0 * R_circ         # maximum nut width vertex-to-vertex

        # Pocket axial depth must be at least tip-to-tip + 0.5 mm clearance
        pkt_z = tip_to_tip + 0.5

        # Auto-raise hub height so nut pocket fits fully inside the hub boss
        if hub_height_mm < pkt_z:
            hub_height_mm = pkt_z

        # Pocket width (Y) and radial depth (X)
        pkt_y = waf + 0.5              # flat-to-flat + clearance
        pkt_x = t_nut + 0.5           # nut thickness + clearance

        # Hex pocket circumradius and actual axial depth
        R_pkt     = pkt_y / math.sqrt(3)   # circumradius of clearance hex
        pkt_depth = 2.0 * R_pkt            # tip-to-tip depth of hex pocket

        # Auto-raise hub height so nut pocket fits fully inside the hub boss
        if hub_height_mm < pkt_depth:
            hub_height_mm = pkt_depth

        # Require 2 × t_nut of wall outside the nut
        min_hub_r = R_bore + 3.0 * t_nut
        need_oblong = R_hub < min_hub_r
        eff_r = max(R_hub, min_hub_r)

        step = math.pi                 # 180° between captured-nut screws
    else:
        waf = t_nut = R_circ = pkt_z = pkt_y = pkt_x = R_pkt = pkt_depth = 0.0
        min_hub_r = 0.0
        need_oblong = False
        eff_r = R_hub
        step = math.pi / 2.0          # 90° between standard screws

    screw_angles = [k * step for k in range(min(screw_count, 2))] if do_screws else []

    _ext_h       = hub_height_mm + flange_ext_mm
    hub_top      = hub_z_start + _ext_h
    total_height = belt_height_mm

    # ── Hub boss ──────────────────────────────────────────────────────────────
    if hub_valid and body.is_watertight:
        if hub_z_start > belt_height_mm:
            conn = trimesh.creation.cylinder(radius=R_hub, height=hub_z_start - belt_height_mm,
                                             sections=_BORE_SECTIONS)
            conn.apply_translation([0.0, 0.0, belt_height_mm + (hub_z_start - belt_height_mm) / 2.0])
            conn.fix_normals()
            body = trimesh.boolean.union([body, conn], engine='manifold')

        if captured_nut and need_oblong:
            # Extend hub with lobes in each screw direction
            hub_poly = ShapelyPoint(0, 0).buffer(R_hub, resolution=_BORE_SECTIONS)
            for angle in screw_angles:
                offset  = min_hub_r - R_hub
                hub_poly = hub_poly.union(
                    ShapelyPoint(offset * math.cos(angle),
                                 offset * math.sin(angle)).buffer(
                        R_hub, resolution=_BORE_SECTIONS))
            hub_poly = shapely_orient(hub_poly, sign=1.0)
            hub_mesh = trimesh.creation.extrude_polygon(hub_poly, _ext_h)
            hub_mesh.apply_translation([0.0, 0.0, hub_z_start])
        else:
            hub_mesh = trimesh.creation.cylinder(
                radius=R_hub, height=_ext_h, sections=_BORE_SECTIONS)
            hub_mesh.apply_translation([0.0, 0.0, hub_z_start + _ext_h / 2.0])

        hub_mesh.fix_normals()
        body         = trimesh.boolean.union([body, hub_mesh], engine='manifold')
        total_height = hub_top

    # ── Bore + keyway (_build_bore_2d is the single source of truth) ─────────
    if R_bore > 0.5:
        bore_2d = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
        if bore_2d is not None:
            extra  = 0.5
            bore_h = total_height + extra * 2
            cutter = trimesh.creation.extrude_polygon(bore_2d, bore_h)
            cutter.apply_translation([0.0, 0.0, -extra])
            cutter.fix_normals()
            if getattr(body, 'is_volume', False) and getattr(cutter, 'is_volume', False):
                try:
                    body = trimesh.boolean.difference([body, cutter], engine='manifold')
                except Exception:
                    pass

    # ── Set-screw holes + nut pockets ─────────────────────────────────────────
    # Keyway: screw at angle=0, nut pocket against keyway slot outer face.
    if keyway_h_mm > 0.0 and do_screws:
        screw_angles = [0.0]

    if do_screws and body.is_watertight:
        R_screw = screw_dia_mm / 2.0

        if captured_nut:
            # Screw at centre of the nut (hub_top minus hex circumradius)
            z_screw = hub_top - R_circ        # nut centre in Z

            if keyway_h_mm > 0.0:
                # Nut pocket inner face sits against the keyway slot outer face
                kw_face  = R_bore + keyway_h_mm
                hole_len = eff_r - kw_face + 1.0
                hole_cx  = (eff_r + kw_face) / 2.0
                pkt_cx   = kw_face
            else:
                # One-sided hole: enters from hub OD, stops at bore
                hole_len = eff_r - R_bore + 1.0
                hole_cx  = (eff_r + R_bore) / 2.0
                # Hex pocket: inner face at bore
                pkt_cx = R_bore

        else:
            z_screw  = hub_z_start + flange_ext_mm + hub_height_mm / 2.0
            hole_len = R_hub * 2.0 + 2.0
            hole_cx  = 0.0   # centred — full-diameter for standard set screw

        for angle in screw_angles:
            # ── Radial screw hole ─────────────────────────────────────────────
            hole = trimesh.creation.cylinder(radius=R_screw, height=hole_len,
                                             sections=32)
            hole.apply_transform(
                trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
            hole.apply_translation([hole_cx, 0.0, z_screw])
            if abs(angle) > 1e-9:
                hole.apply_transform(
                    trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
            hole.fix_normals()
            if body.is_watertight:
                body = trimesh.boolean.difference([body, hole], engine='manifold')

            # ── Nut pocket (opens from hub top face) ──────────────────────────
            # Profile in Shapely XY plane (X_s→Y_world tangential,
            #                              Y_s→Z_world axial):
            #   TOP: rectangular (±half_y wide) from 1 mm above hub_top
            #        down to the lower hex corners (hub_top - 1.5·R_pkt).
            #   BOTTOM: V-shape from lower corners to bottom tip,
            #           matching the hex nut's lower profile.
            # Z coords are embedded in the polygon so no Z translation needed.
            if captured_nut and body.is_watertight:
                half_y = pkt_y / 2.0
                top_z  = hub_top + 1.0          # 1 mm overshoot → clean open top
                low_z  = hub_top - 1.5 * R_pkt  # lower hex corners
                tip_z  = hub_top - 2.0 * R_pkt  # bottom tip

                pocket_verts = [
                    ( half_y, top_z),   # top-right
                    (-half_y, top_z),   # top-left
                    (-half_y, low_z),   # lower-left hex corner
                    ( 0.0,    tip_z),   # bottom tip (hex point)
                    ( half_y, low_z),   # lower-right hex corner
                ]
                pocket_poly = ShapelyPolygon(pocket_verts)
                pocket = trimesh.creation.extrude_polygon(pocket_poly, pkt_x)

                # Rotate Shapely (X_s, Y_s, Z_s) → world (Y_w, Z_w, X_w)
                rot = np.array([[0, 0, 1, 0],
                                [1, 0, 0, 0],
                                [0, 1, 0, 0],
                                [0, 0, 0, 1]], dtype=float)
                pocket.apply_transform(rot)
                # Z already in polygon — only shift X to place inner face at bore
                pocket.apply_translation([pkt_cx, 0.0, 0.0])
                if abs(angle) > 1e-9:
                    pocket.apply_transform(
                        trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
                pocket.fix_normals()
                body = trimesh.boolean.difference([body, pocket], engine='manifold')

    return body


def generate_pulley_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    hub_height_mm: float = 0.0,
    screw_dia_mm: float = 0.0,
    screw_count: int = 0,
    captured_nut: bool = False,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    spoke_height_mm: float = 0.0,
    flange_enabled: bool = False,
    flange_height_mm: float = 0.0,
) -> bytes:
    """
    Return binary STL bytes of an extruded timing pulley solid.

    The toothed body is extruded from z=0 to z=belt_height_mm.
    If hub_od_mm and hub_height_mm are given, a hub cylinder is unioned on top
    (z=belt_height_mm to z=belt_height_mm+hub_height_mm).  The hub OD may
    exceed the pulley OD.  The bore is subtracted through the full height.
    """
    outline, _R_OD_stl, _ = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )

    outer_poly = ShapelyPolygon(outline)
    outer_poly = shapely_orient(outer_poly, sign=1.0)

    if spoke_count > 0 and spoke_width_mm > 0.0:
        from exporters.png_exporter import _spoke_void_polygons
        _R_hub_s = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        _R_tr    = min(math.hypot(x, y) for x, y in outline)
        _R_rim_s = max(_R_tr - rim_depth_mm, _R_hub_s + 1.0)
        # Guard: hub/rim larger than pulley face, or bore >= hub OD — impossible geometry, skip spokes
        if _R_hub_s < _R_tr and _R_rim_s < _R_tr and _R_hub_s > bore_mm / 2.0:
            spk_h = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm

            # 1. Hub Cylinder (Full Height)
            hub_cyl = trimesh.creation.cylinder(radius=_R_hub_s, height=belt_height_mm, sections=64)
            hub_cyl.apply_translation([0, 0, belt_height_mm / 2.0])

            # 2. Rim Mesh (Full Height)
            rim_circle = ShapelyPoint(0, 0).buffer(_R_rim_s, resolution=64)
            rim_poly = outer_poly.difference(rim_circle)
            rim_poly = shapely_orient(_largest_poly(rim_poly), sign=1.0)
            rim_mesh = trimesh.creation.extrude_polygon(rim_poly, belt_height_mm)
            rim_mesh.fix_normals()

            # 3. Spoke Web Mesh (Partial Height)
            spoke_poly = outer_poly
            _vp_shapes = []
            for vp in _spoke_void_polygons(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                           fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm):
                if len(vp) < 3:
                    continue
                vp_shape = ShapelyPolygon(vp).simplify(0.05, preserve_topology=True).buffer(0)
                vp_shape = shapely_orient(vp_shape, sign=1.0)
                if vp_shape.is_valid and vp_shape.area > 0.1:
                    _vp_shapes.append(vp_shape)
            if _vp_shapes:
                spoke_poly = spoke_poly.difference(shapely_unary_union(_vp_shapes))

            spoke_poly = shapely_orient(_largest_poly(spoke_poly), sign=1.0)
            web_mesh = trimesh.creation.extrude_polygon(spoke_poly, spk_h)
            web_mesh.fix_normals()
            slab_offset = (belt_height_mm - spk_h) / 2.0
            web_mesh.apply_translation([0, 0, slab_offset])

            # Union the pieces together
            union_parts = []
            for m in [hub_cyl, rim_mesh, web_mesh]:
                if getattr(m, 'is_volume', False):
                    union_parts.append(m)
                elif not m.is_watertight:
                    trimesh.repair.fill_holes(m)
                    m.fix_normals()
                    if getattr(m, 'is_volume', False):
                        union_parts.append(m)

            try:
                body = trimesh.boolean.union(union_parts, engine='manifold')
            except Exception:
                body = web_mesh
        else:
            body = trimesh.creation.extrude_polygon(outer_poly, belt_height_mm)
            body.fix_normals()
    else:
        body = trimesh.creation.extrude_polygon(outer_poly, belt_height_mm)
        body.fix_normals()

    # Determine hub_z_start: raise hub above top flange when hub/lobe overhangs pulley OD
    R_hub_stl = hub_od_mm / 2.0
    hub_valid_stl = hub_height_mm > 0.0 and hub_od_mm > bore_mm
    eff_r_stl = R_hub_stl
    if hub_valid_stl and captured_nut and screw_dia_mm > 0.0 and screw_count > 0:
        _waf_stl, _t_stl = _nut_dims(screw_dia_mm)
        _min_hub_r_stl = bore_mm / 2.0 + 3.0 * _t_stl
        eff_r_stl = max(R_hub_stl, _min_hub_r_stl)
    hub_z_start_stl = belt_height_mm
    if flange_enabled and hub_valid_stl and eff_r_stl > _R_OD_stl:
        hub_z_start_stl = belt_height_mm + flange_height_mm
    _has_spokes_stl = spoke_count > 0 and spoke_width_mm > 0.0
    _flange_ext_stl = flange_height_mm if (flange_enabled and not _has_spokes_stl) else 0.0

    result = _add_hub_and_bore(body, belt_height_mm, bore_mm,
                               hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                               captured_nut, flat_depth_mm, keyway_w_mm, keyway_h_mm,
                               hub_z_start=hub_z_start_stl,
                               flange_ext_mm=_flange_ext_stl)
    return result.export(file_type='stl')


def generate_pulley_step(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    hub_height_mm: float = 0.0,
    screw_dia_mm: float = 0.0,
    screw_count: int = 0,
    captured_nut: bool = False,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    spoke_height_mm: float = 0.0,
    export_fmt: str = 'STEP',
    # Flange params (3D-print bottom is integrated into pulley; top is separate)
    flange_enabled: bool = False,
    flange_3dprint: bool = True,
    flange_angle_deg: float = 15.0,
    flange_rim_radius_mm: float = 3.0,
    flange_height_mm: float = 1.5,
    flange_top_separate: bool = True,
    nubs_enabled: bool = False,
    nub_count: int = 4,
    nub_dia_mm: float = 3.0,
    nub_height_mm: float = 2.0,
    nub_allowance_mm: float = 0.2,
    _return_cq: bool = False,
) -> bytes:
    """
    Return STEP bytes of a timing pulley with all hub features using cadquery B-rep.

    Builds the geometry natively in cadquery (proper solid B-rep, not mesh),
    producing a compact STEP file that loads instantly in Fusion 360 / eDrawings.
    """
    import cadquery as cq
    import tempfile, os

    # ── 2D tooth profile data (via shared segment API) ────────────────────────
    _segs, _R_OD, _edge_a, _wrapped = pulley_outline_segments(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )
    _R_tr = min(math.hypot(x, y) for x, y in _wrapped)   # tooth-root radius

    def _tooth_spline_sketch(base_wp, inner_r=None):
        """Translate shared outline segments into a CadQuery sketch."""
        return _segs_to_cq_sketch(_segs, base_wp, inner_r=inner_r)

    # ── Hub pre-calculations (MUST be before any extrusion) ─────────────────
    R_bore = bore_mm / 2.0
    if hub_height_mm <= 0.0 and hub_od_mm > bore_mm and screw_dia_mm > 0.0 and screw_count > 0:
        if captured_nut:
            _waf_pre, _t_pre = _nut_dims(screw_dia_mm)
            _pkt_y_pre = _waf_pre + 0.5
            _R_pkt_pre = _pkt_y_pre / math.sqrt(3)
            hub_height_mm = max(2.0 * _R_pkt_pre, 4.0)
        else:
            hub_height_mm = max(screw_dia_mm * 1.5, 4.0)
    hub_valid = hub_height_mm > 0.0 and hub_od_mm > bore_mm
    R_hub     = hub_od_mm / 2.0 if hub_valid else 0.0
    do_screws = hub_valid and screw_dia_mm > 0.0 and screw_count > 0

    if do_screws and captured_nut:
        waf, t_nut = _nut_dims(screw_dia_mm)
        R_circ    = waf / math.sqrt(3)
        pkt_y     = waf + 0.5
        pkt_x     = t_nut + 0.5
        R_pkt     = pkt_y / math.sqrt(3)
        pkt_depth_nut = 2.0 * R_pkt
        if hub_height_mm < pkt_depth_nut:
            hub_height_mm = pkt_depth_nut
        min_hub_r  = R_bore + 3.0 * t_nut
        need_oblong = R_hub < min_hub_r
        eff_r = max(R_hub, min_hub_r)
        step  = math.pi
    else:
        waf = t_nut = R_circ = pkt_y = pkt_x = R_pkt = pkt_depth_nut = 0.0
        min_hub_r   = 0.0
        need_oblong = False
        eff_r = R_hub
        step  = math.pi / 2.0

    screw_angles = [k * step for k in range(min(screw_count, 2))] if do_screws else []

    # ── Spoke pre-calculations ────────────────────────────────────────────────
    has_spokes = spoke_count > 0 and spoke_width_mm > 0.0

    hub_z_start  = belt_height_mm
    if flange_enabled and hub_valid and eff_r > _R_OD:
        hub_z_start = belt_height_mm + flange_height_mm
    _flange_ext_step = flange_height_mm if (flange_enabled and not has_spokes) else 0.0
    _ext_hub_h   = hub_height_mm + _flange_ext_step
    hub_top      = hub_z_start + _ext_hub_h
    total_height = hub_top if hub_valid else belt_height_mm
    if has_spokes:
        from exporters.png_exporter import _spoke_void_segments, _spoke_void_polygons
        spk_h = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm
        pocket_depth = (belt_height_mm - spk_h) / 2.0
        _R_hub_s = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        _R_rim_s = max(_R_tr - rim_depth_mm, _R_hub_s + 1.0)

    # ── 1. Belt solid ─────────────────────────────────────────────────────────
    # clean=False: CadQuery Issue #192 — cutThruAll / booleans on B-spline tooth
    # profiles fail during OCCT's shape-healing ("clean") pass.  Disabling it
    # avoids silently dropped cuts (missing bore hole) and spurious faces.
    result = _tooth_spline_sketch(cq.Workplane('XY')).extrude(belt_height_mm, clean=False)

    # ── 2. Spoke voids + annular pockets (before hub so top face is unambiguous)
    if has_spokes:
        def _dedup(pts, tol=1e-6):
            out = [pts[0]]
            for p in pts[1:]:
                if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
                    out.append(p)
            while len(out) > 2 and math.hypot(out[-1][0]-out[0][0], out[-1][1]-out[0][1]) <= tol:
                out.pop()
            return out

        def _cut_spoke_voids_arc(solid):
            gaps_segs = _spoke_void_segments(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                             fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm)
            for segs in gaps_segs:
                if not segs:
                    continue
                s0 = segs[0]
                if s0[0] == 'arc':
                    _, cx0, cy0, r0, a10, _ = s0
                    sx, sy = cx0 + r0 * math.cos(a10), cy0 + r0 * math.sin(a10)
                else:
                    sx, sy = s0[1], s0[2]
                wp2 = cq.Workplane('XY').workplane(offset=pocket_depth).moveTo(sx, sy)
                for seg in segs:
                    if seg[0] == 'arc':
                        _, cx, cy, r, a1, a2 = seg
                        diff = (a2 - a1) % (2 * math.pi)
                        if diff > math.pi:
                            diff -= 2 * math.pi
                        a_mid = a1 + diff / 2.0
                        mx = cx + r * math.cos(a_mid); my = cy + r * math.sin(a_mid)
                        ex = cx + r * math.cos(a2);    ey = cy + r * math.sin(a2)
                        wp2 = wp2.threePointArc((mx, my), (ex, ey))
                    else:
                        _, x1, y1, x2, y2 = seg
                        wp2 = wp2.lineTo(x2, y2)
                void_solid = wp2.close().extrude(spk_h, clean=False)
                solid = solid.cut(void_solid, clean=False)
            return solid

        def _cut_spoke_voids_poly(solid):
            for ft, fb in [(fillet_tip_mm, fillet_base_mm), (0.0, 0.0)]:
                try:
                    voids = _spoke_void_polygons(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                                 fillet_tip_mm=ft, fillet_base_mm=fb)
                    for pts in voids:
                        pts = _dedup(pts)
                        if len(pts) < 3:
                            continue
                        void_solid = (cq.Workplane('XY')
                                      .workplane(offset=pocket_depth)
                                      .polyline(pts).close().extrude(spk_h, clean=False))
                        solid = solid.cut(void_solid, clean=False)
                    return solid
                except Exception:
                    continue
            return solid

        try:
            result = _cut_spoke_voids_arc(result)
        except Exception:
            result = _cut_spoke_voids_poly(result)

        if pocket_depth > 1e-3:
            annulus_slab = (cq.Workplane('XY')
                            .circle(_R_rim_s).circle(_R_hub_s)
                            .extrude(pocket_depth, clean=False))
            result = result.cut(annulus_slab, clean=False)
            result = result.cut(annulus_slab.translate(
                (0.0, 0.0, spk_h + pocket_depth)), clean=False)

    # ── 3. Hub boss: extrude directly from the top face ──────────────────────
    #    faces(">Z").workplane() + extrude() is CadQuery's native "boss" pattern.
    #    OCCT treats it as one continuous solid — no boolean union, no seam.
    if hub_valid:
        # Add connecting cylinder when hub is raised above belt face (flange overhang case)
        if hub_z_start > belt_height_mm:
            conn = (cq.Workplane('XY').workplane(offset=belt_height_mm)
                    .circle(R_hub).extrude(hub_z_start - belt_height_mm, clean=False))
            result = result.union(conn, clean=False)

        hub_wp = result.faces(">Z").workplane()
        if captured_nut and need_oblong:
            _ob_off = min_hub_r - R_hub
            # Build hub as union of true arc cylinders so the STEP file contains
            # proper circle edges — not the tessellated polyline that Shapely
            # .buffer() → .exterior.coords would produce.
            hub_lobe = (cq.Workplane('XY')
                        .workplane(offset=hub_z_start)
                        .circle(R_hub)
                        .extrude(_ext_hub_h, clean=False))
            for angle in screw_angles:
                _lx = _ob_off * math.cos(angle)
                _ly = _ob_off * math.sin(angle)
                extra_lobe = (cq.Workplane('XY')
                              .workplane(offset=hub_z_start)
                              .moveTo(_lx, _ly)
                              .circle(R_hub)
                              .extrude(_ext_hub_h, clean=False))
                hub_lobe = hub_lobe.union(extra_lobe, clean=False)
            result = result.union(hub_lobe, clean=False)

            # ── 45° chamfer support under each lobe ──────────────────────────
            # Adds a toroidal wedge of material in the pocket space directly
            # below the lobe overhang so the 3D printer has a 45° surface to
            # build from instead of printing over open air.
            # Only applied when there is an annular pocket below (pocket_depth > 0).
            _lobe_has_open_space = has_spokes and pocket_depth > 0 and eff_r > _R_hub_s
            if _lobe_has_open_space:
                # Chamfer cone extends through the full spoke zone + top pocket.
                # Clamped to R_hub - 0.5 so the tip radius stays >= 0.5 mm.
                _ch = min(spk_h + pocket_depth, R_hub - 0.5)
                _z0 = hub_z_start   # lobe bottom face Z

                for _sc_angle in screw_angles:
                    _lcx = _ob_off * math.cos(_sc_angle)
                    _lcy = _ob_off * math.sin(_sc_angle)

                    # Triangle profile in LOCAL lobe coords, revolved 360° around
                    # the lobe's Z axis, then translated to world lobe position.
                    #
                    # Cross-section (r = distance from lobe centre, Z = height):
                    #
                    # Tapered cylinder (truncated cone) centred on the lobe axis:
                    #   bottom Z = _z0 - _ch : radius = R_hub - _ch  (inner, near hub)
                    #   top    Z = _z0       : radius = R_hub         (full lobe radius)
                    # taper=-45 means the cone expands outward going up at 45°.
                    # The sloped face runs from inner-bottom → outer-top, i.e. toward
                    # the hub as you go downward. ✓
                    try:
                        chamfer_support = (
                            cq.Workplane('XY')
                            .workplane(offset=_z0 - _ch)
                            .circle(R_hub - _ch)
                            .extrude(_ch, taper=-45, clean=False)
                            .translate((_lcx, _lcy, 0))
                        )
                        result = result.union(chamfer_support, clean=False)
                    except Exception:
                        pass   # skip if taper fails for this geometry
        else:
            if hub_z_start > belt_height_mm:
                result = result.union(
                    cq.Workplane('XY').workplane(offset=hub_z_start)
                    .circle(R_hub).extrude(_ext_hub_h, clean=False), clean=False)
            else:
                result = hub_wp.circle(R_hub).extrude(_ext_hub_h, clean=False)

    # ── 3b. 3D-print bottom flange (union before bore/keyway so cuts pass through) ─
    # Must happen here so steps 4 & 5 can cut through the flange material.
    _bot_flange_unioned = False
    if flange_enabled and flange_3dprint:
        _key_b  = _profile_key(family, pitch)
        _spec_b = PULLEY_SPECS[_key_b]
        _pld_b  = _spec_b.get('pitch_line_diff', _spec_b.get('pitchLineDiff', 0.0))
        _R_OD_b = getOuterDiameter(num_teeth, _spec_b['pitch'],
                                   _pld_b + print_extra_mm - clearance_mm) / 2.0
        _has_spokes_b = spoke_count > 0
        _r_inner_b = flange_inner_r_3dprint_bottom(
            bore_mm, _has_spokes_b, spoke_hub_od_mm,
            r_tooth_OD=_R_OD_b, rim_depth_mm=rim_depth_mm)
        _angle_b = max(8.0, min(25.0, flange_angle_deg))
        _rim_r_b = max(0.5, flange_rim_radius_mm)
        _f_h_b   = max(0.1, flange_height_mm)
        _prof_b  = profile_3dprint(_r_inner_b, _R_OD_b, _rim_r_b, _angle_b, _f_h_b)
        _bot_prof_b = [(_r, -_z) for _r, _z in _prof_b]
        _bot_flange_mesh = _revolve_rz_profile(_bot_prof_b)
        result = result.union(_bot_flange_mesh, clean=False)
        _bot_flange_unioned = True

    # ── 4. Bore: explicit solid cut (more reliable than cutThruAll on splines) ─
    # Extend bore downward by flange depth so it passes through the bottom flange.
    _flange_h_ext = flange_height_mm if _bot_flange_unioned else 0.0
    if R_bore > 0.5:
        extra  = 0.5
        bore_h = total_height + _flange_h_ext + extra * 2
        if flat_depth_mm > 0.0:
            # Build D-bore with a true arc + one straight line so the STEP file
            # carries an exact circle edge rather than a tessellated polyline.
            flat_x    = R_bore - flat_depth_mm
            cos_theta = max(-1.0, min(1.0, flat_x / R_bore))
            theta     = math.acos(cos_theta)      # half-angle to chord endpoints
            y_int     = R_bore * math.sin(theta)  # ±y at the flat/arc junction
            bore_solid = (cq.Workplane('XY')
                          .moveTo(flat_x, y_int)
                          .threePointArc((-R_bore, 0.0), (flat_x, -y_int))
                          .close()                 # straight line = the flat face
                          .extrude(bore_h, clean=False)
                          .translate((0.0, 0.0, -extra - _flange_h_ext)))
        else:
            bore_solid = (cq.Workplane('XY')
                          .circle(R_bore)
                          .extrude(bore_h, clean=False)
                          .translate((0.0, 0.0, -extra - _flange_h_ext)))
        result = result.cut(bore_solid, clean=False)

    # ── 5. Keyway slot ────────────────────────────────────────────────────────
    if keyway_w_mm > 0.0 and keyway_h_mm > 0.0 and R_bore > 0.5:
        kw_depth = keyway_h_mm
        kw_h_total = total_height + _flange_h_ext + 1.0
        kw_box = (cq.Workplane('XY')
                  .box(kw_depth + 0.5, keyway_w_mm, kw_h_total, clean=False)
                  .translate((R_bore + kw_depth / 2.0 - 0.25, 0.0, (total_height - _flange_h_ext) / 2.0)))
        result = result.cut(kw_box, clean=False)

    # ── 6. Set-screw holes + nut pockets ──────────────────────────────────────
    if (flat_depth_mm > 0.0 or keyway_h_mm > 0.0) and do_screws:
        screw_angles = [0.0]

    if do_screws:
        R_screw = screw_dia_mm / 2.0

        if captured_nut:
            z_screw = hub_top - R_circ
            if flat_depth_mm > 0.0:
                # Nut inner face sits against the D-flat face
                flat_x   = R_bore - flat_depth_mm
                hole_len = eff_r - flat_x + 1.0
                hole_cx  = (eff_r + flat_x) / 2.0
                pkt_cx   = flat_x
            elif keyway_h_mm > 0.0:
                kw_face  = R_bore + keyway_h_mm
                hole_len = eff_r - kw_face + 1.0
                hole_cx  = (eff_r + kw_face) / 2.0
                pkt_cx   = kw_face
            else:
                hole_len = eff_r - R_bore + 1.0
                hole_cx  = (eff_r + R_bore) / 2.0
                pkt_cx   = R_bore
        else:
            z_screw  = hub_z_start + _flange_ext_step + hub_height_mm / 2.0
            # Hole goes from hub OD inward to bore — not through the other side.
            # +0.5 overshoot on each end so the bore and hub surface cuts are clean.
            hole_len = R_hub - R_bore + 1.0
            hole_cx  = (R_hub + R_bore) / 2.0

        for angle in screw_angles:
            x_start = hole_cx - hole_len / 2.0
            hole = (cq.Workplane('YZ')
                    .circle(R_screw)
                    .extrude(hole_len)
                    .translate((x_start, 0.0, z_screw)))
            if abs(angle) > 1e-9:
                hole = hole.rotate((0, 0, 0), (0, 0, 1), math.degrees(angle))
            result = result.cut(hole, clean=False)

            if captured_nut:
                half_y = pkt_y / 2.0
                top_z  = hub_top + 1.0
                low_z  = hub_top - 1.5 * R_pkt
                tip_z  = hub_top - 2.0 * R_pkt

                pkt_pts = [
                    ( half_y, top_z),
                    (-half_y, top_z),
                    (-half_y, low_z),
                    ( 0.0,    tip_z),
                    ( half_y, low_z),
                ]
                pocket = (cq.Workplane('YZ')
                          .workplane(offset=pkt_cx)
                          .polyline(pkt_pts)
                          .close()
                          .extrude(pkt_x, clean=False))
                if abs(angle) > 1e-9:
                    pocket = pocket.rotate((0, 0, 0), (0, 0, 1), math.degrees(angle))
                result = result.cut(pocket, clean=False)

    # ── 7. 3D-print flanges: bottom already unioned in step 3b; add top if integrated ─
    if flange_enabled and flange_3dprint:
        _key  = _profile_key(family, pitch)
        _spec = PULLEY_SPECS[_key]
        _pld  = _spec.get('pitch_line_diff', _spec.get('pitchLineDiff', 0.0))
        _R_OD = getOuterDiameter(num_teeth, _spec['pitch'],
                                 _pld + print_extra_mm - clearance_mm) / 2.0
        _has_spokes = spoke_count > 0
        _angle   = max(8.0, min(25.0, flange_angle_deg))
        _rim_r   = max(0.5, flange_rim_radius_mm)
        _f_h     = max(0.1, flange_height_mm)

        # Integrated top flange — union it onto the pulley top face
        if not flange_top_separate:
            _r_inner_top = flange_inner_r_3dprint(
                bore_mm, hub_od_mm, _has_spokes, spoke_hub_od_mm,
                r_tooth_OD=_R_OD, rim_depth_mm=rim_depth_mm)
            _top_prof = profile_3dprint(_r_inner_top, _R_OD, _rim_r, _angle, _f_h)
            _top_flange = _revolve_rz_profile(_top_prof)
            _top_flange = _top_flange.translate((0.0, 0.0, belt_height_mm))
            result = result.union(_top_flange, clean=False)

        # Cut socket holes into the pulley top face when nubs are enabled
        if flange_top_separate and nubs_enabled:
            _tooth_ht = _spec['tooth_ht']
            _r_nub    = _nub_circle_r_step(_R_OD, _tooth_ht, nub_dia_mm)
            _r_socket = nub_dia_mm / 2.0
            _sock_h   = max(1.0, min(nub_height_mm, belt_height_mm / 3.0))
            for _i in range(nub_count):
                _ang = 2.0 * math.pi * _i / nub_count
                _cx  = _r_nub * math.cos(_ang)
                _cy  = _r_nub * math.sin(_ang)
                _sock = (cq.Workplane('XY')
                         .moveTo(_cx, _cy)
                         .circle(_r_socket)
                         .extrude(_sock_h, clean=False)
                         .translate((0.0, 0.0, belt_height_mm - _sock_h)))
                result = result.cut(_sock, clean=False)

    if _return_cq:
        return result

    # ── Export to File ────────────────────────────────────────────────────────
    ext = '.stl' if export_fmt.upper() == 'STL' else '.step'
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.close()
    try:
        cq.exporters.export(result, tmp.name)
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


def generate_flange_step(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    flange_3dprint: bool = True,
    flange_angle_deg: float = 15.0,
    rim_radius_mm: float = 3.0,
    flange_height_mm: float = 1.5,
    plate_height_mm: float = 1.0,
    bend_radius_mm: float = 0.0,
    which: str = 'top',
    hub_od_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    nubs_enabled: bool = False,
    nub_count: int = 4,
    nub_dia_mm: float = 3.0,
    nub_height_mm: float = 2.0,
    nub_allowance_mm: float = 0.2,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
    _return_cq: bool = False,
) -> bytes:
    """Return STEP bytes of a single flange (top 3D-print, or metal top/bottom).

    3D-print top flanges include nub pins when nubs_enabled is True.
    Metal flanges are thin-shell solids revolved from a closed 2D profile.
    The 3D-print bottom flange is NOT generated here — it is integrated into
    the pulley body by generate_pulley_step() when flange_enabled=True.
    """
    import cadquery as cq
    import tempfile, os

    key  = _profile_key(family, pitch)
    spec = PULLEY_SPECS[key]
    pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
    R_OD = getOuterDiameter(num_teeth, spec['pitch'], pld + print_extra_mm - clearance_mm) / 2.0
    tooth_ht = spec['tooth_ht']

    if flange_3dprint:
        r_inner = flange_inner_r_3dprint(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                         r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        _angle = max(8.0, min(25.0, flange_angle_deg))
        _rim_r = max(0.5, rim_radius_mm)
        _f_h   = max(0.1, flange_height_mm)
        prof   = profile_3dprint(r_inner, R_OD, _rim_r, _angle, _f_h)

        if which == 'top':
            flange = _revolve_rz_profile(prof)
            # Add nub pins protruding down from Z=0 (bottom face of top flange)
            if nubs_enabled:
                r_nub     = _nub_circle_r_step(R_OD, tooth_ht, nub_dia_mm)
                r_pin     = max(0.1, (nub_dia_mm - nub_allowance_mm) / 2.0)
                nub_h     = max(1.0, min(nub_height_mm, belt_height_mm / 3.0))
                nub_pin_h = max(0.1, nub_h - nub_allowance_mm)
                for i in range(nub_count):
                    ang = 2.0 * math.pi * i / nub_count
                    cx  = r_nub * math.cos(ang)
                    cy  = r_nub * math.sin(ang)
                    pin = (cq.Workplane('XY')
                           .moveTo(cx, cy)
                           .circle(r_pin)
                           .extrude(nub_pin_h, clean=False)
                           .translate((0.0, 0.0, -nub_pin_h)))
                    flange = flange.union(pin, clean=False)
            flange = flange.translate((0.0, 0.0, belt_height_mm))
        else:
            bot_prof = [(r, -z) for r, z in prof]
            flange = _revolve_rz_profile(bot_prof)
    else:
        # Metal flange
        if bend_radius_mm <= 0.0:
            bend_radius_mm = 1.5 * plate_height_mm
        _angle   = max(8.0, min(25.0, flange_angle_deg))
        _rim_r   = max(0.5, rim_radius_mm)
        _plate_t = max(0.3, plate_height_mm)
        _bend_r  = min(bend_radius_mm, _rim_r * 0.8)

        if which == 'top':
            r_inner = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                               r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof = profile_metal(r_inner, R_OD, _rim_r, _angle, _plate_t, _bend_r)
            flange = _revolve_rz_profile(prof)
            flange = flange.translate((0.0, 0.0, belt_height_mm))
        else:
            r_inner = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                  r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof = profile_metal(r_inner, R_OD, _rim_r, _angle, _plate_t, _bend_r)
            prof_flipped = [(r, -z) for r, z in prof]
            flange = _revolve_rz_profile(prof_flipped)

    # ── Bore profile (D-flat / keyway) cut through the flange ────────────────
    # Top flanges (3D-print or metal) sit on the hub boss OD, not on the shaft.
    # Their inner hole stays at hub_od_mm — no bore cut, no D-flat, no keyway.
    # Only the bottom metal flange extends to bore_mm and needs the bore profile.
    R_bore = bore_mm / 2.0
    if which == 'bottom':
        # Always cut an explicit bore cylinder before D-flat/keyway so that the
        # bore circle edge in the STEP file is an exact OCCT cylinder (same as
        # the pulley body), not the revolved inner face.  When r_inner > R_bore
        # this also trims the flange down to bore diameter.
        if R_bore > 0.5 and (r_inner >= R_bore or flat_depth_mm > 0.0 or keyway_w_mm > 0.0):
            cut_h = 200.0
            bore_cyl = (cq.Workplane('XY').circle(R_bore)
                        .extrude(cut_h, clean=False)
                        .translate((0.0, 0.0, -cut_h + 0.5)))
            flange = flange.cut(bore_cyl, clean=False)
        if R_bore > 0.5 and flat_depth_mm > 0.0:
            flat_x    = R_bore - flat_depth_mm
            cos_theta = max(-1.0, min(1.0, flat_x / R_bore))
            theta     = math.acos(cos_theta)
            y_int     = R_bore * math.sin(theta)
            cut_h = 200.0
            d_cut = (cq.Workplane('XY')
                     .moveTo(flat_x, y_int)
                     .threePointArc((-R_bore, 0.0), (flat_x, -y_int))
                     .close()
                     .extrude(cut_h, clean=False)
                     .translate((0.0, 0.0, -cut_h + 0.5)))
            flange = flange.cut(d_cut, clean=False)
        if R_bore > 0.5 and keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
            kw_depth = keyway_h_mm
            cut_h = 200.0
            kw_cut = (cq.Workplane('XY')
                      .box(kw_depth + 0.5, keyway_w_mm, cut_h, clean=False)
                      .translate((R_bore + kw_depth / 2.0 - 0.25, 0.0, (-cut_h + 0.5) + cut_h / 2.0)))
            flange = flange.cut(kw_cut, clean=False)

    if _return_cq:
        return flange

    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        cq.exporters.export(flange, tmp.name)
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


# ── Multipart STEP assembly helpers ───────────────────────────────────────────

def _flange_kw_from_pulley_kw(kw: dict) -> dict:
    """Map generate_pulley_step kw dict to generate_flange_step kwargs."""
    return dict(
        family           = kw['family'],
        pitch            = kw['pitch'],
        num_teeth        = kw['num_teeth'],
        bore_mm          = kw['bore_mm'],
        belt_height_mm   = kw.get('belt_height_mm', 10.0),
        clearance_mm     = kw.get('clearance_mm', 0.0),
        print_extra_mm   = kw.get('print_extra_mm', 0.0),
        flange_3dprint   = kw.get('flange_3dprint', True),
        flange_angle_deg = kw.get('flange_angle_deg', 15.0),
        rim_radius_mm    = kw.get('flange_rim_radius_mm', 3.0),
        flange_height_mm = kw.get('flange_height_mm', 1.5),
        plate_height_mm  = kw.get('plate_height_mm', 1.0),
        bend_radius_mm   = kw.get('bend_radius_mm', 0.0),
        hub_od_mm        = kw.get('hub_od_mm', 0.0),
        spokes_enabled   = kw.get('spoke_count', 0) > 0,
        spoke_hub_od_mm  = kw.get('spoke_hub_od_mm', 0.0),
        rim_depth_mm     = kw.get('rim_depth_mm', 0.0),
        nubs_enabled     = kw.get('nubs_enabled', False),
        nub_count        = kw.get('nub_count', 4),
        nub_dia_mm       = kw.get('nub_dia_mm', 3.0),
        nub_height_mm    = kw.get('nub_height_mm', 2.0),
        nub_allowance_mm = kw.get('nub_allowance_mm', 0.2),
        flat_depth_mm    = kw.get('flat_depth_mm', 0.0),
        keyway_w_mm      = kw.get('keyway_w_mm', 0.0),
        keyway_h_mm      = kw.get('keyway_h_mm', 0.0),
    )


_PULLEY_STEP_EXTRA = {'plate_height_mm', 'bend_radius_mm'}


def generate_pulley_assembly_step(kw: dict) -> bytes:
    """Return a multi-body STEP (single product, compound shape) of pulley + flanges.

    Uses a Compound rather than an Assembly so importToTarget works in both
    Fusion 360 Parametric and Direct Design modes.  AP214 assembly STEPs are
    rejected by importToTarget in Direct Design (InternalValidationError).
    """
    import cadquery as cq
    from cadquery import Compound
    import tempfile, os

    flange_enabled = kw.get('flange_enabled', False)
    flange_3dprint = kw.get('flange_3dprint', True)
    flange_top_sep = kw.get('flange_top_separate', True)

    pulley_kw = {k: v for k, v in kw.items() if k not in _PULLEY_STEP_EXTRA}
    shapes = [generate_pulley_step(**pulley_kw, _return_cq=True).val()]

    if flange_enabled:
        fl = _flange_kw_from_pulley_kw(kw)
        if flange_3dprint and flange_top_sep:
            shapes.append(generate_flange_step(**fl, which='top', _return_cq=True).val())
        elif not flange_3dprint:
            shapes.append(generate_flange_step(**fl, which='top',    _return_cq=True).val())
            shapes.append(generate_flange_step(**fl, which='bottom', _return_cq=True).val())

    compound = Compound.makeCompound(shapes)

    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        compound.exportStep(tmp.name)
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


def generate_all_parts_step(kw1: dict, kw2: dict = None, belt_kw: dict = None) -> bytes:
    """Return one multipart STEP with all pulleys, flanges, and optionally the belt.

    When belt_kw is provided the belt uses its own belt_height_mm (no clearance added).
    In dual-pulley mode P2 is placed at center_dist_mm from P1 when a belt is
    included, or at od1+od2+20 mm otherwise.
    """
    import cadquery as cq
    import tempfile, os

    assy = cq.Assembly()

    def _add(kw, prefix, x_off=0.0):
        loc = cq.Location(cq.Vector(x_off, 0, 0))
        pulley_kw = {k: v for k, v in kw.items() if k not in _PULLEY_STEP_EXTRA}
        pulley = generate_pulley_step(**pulley_kw, _return_cq=True)
        assy.add(pulley, name=f'{prefix}Pulley', loc=loc)

        if kw.get('flange_enabled', False):
            fl = _flange_kw_from_pulley_kw(kw)
            if kw.get('flange_3dprint', True) and kw.get('flange_top_separate', True):
                top = generate_flange_step(**fl, which='top', _return_cq=True)
                assy.add(top, name=f'{prefix}TopFlange', loc=loc)
            elif not kw.get('flange_3dprint', True):
                top = generate_flange_step(**fl, which='top', _return_cq=True)
                bot = generate_flange_step(**fl, which='bottom', _return_cq=True)
                assy.add(top, name=f'{prefix}TopFlange', loc=loc)
                assy.add(bot, name=f'{prefix}BottomFlange', loc=loc)

    _add(kw1, 'P1_', 0.0)

    if kw2:
        if belt_kw:
            x_off = belt_kw['center_dist_mm']
        else:
            key1  = _profile_key(kw1['family'], kw1['pitch'])
            spec1 = PULLEY_SPECS[key1]
            pld1  = spec1.get('pitch_line_diff', spec1.get('pitchLineDiff', 0.0))
            od1   = getOuterDiameter(kw1['num_teeth'], spec1['pitch'],
                                     pld1 + kw1.get('print_extra_mm', 0.0)
                                         - kw1.get('clearance_mm', 0.0)) / 2.0
            key2  = _profile_key(kw2['family'], kw2['pitch'])
            spec2 = PULLEY_SPECS[key2]
            pld2  = spec2.get('pitch_line_diff', spec2.get('pitchLineDiff', 0.0))
            od2   = getOuterDiameter(kw2['num_teeth'], spec2['pitch'],
                                     pld2 + kw2.get('print_extra_mm', 0.0)
                                         - kw2.get('clearance_mm', 0.0)) / 2.0
            x_off = od1 + od2 + 20.0
        _add(kw2, 'P2_', x_off)

    if belt_kw:
        belt = generate_belt_step(
            family         = belt_kw['family'],
            pitch          = belt_kw['pitch'],
            num_teeth_left = belt_kw['num_teeth_left'],
            num_teeth_right= belt_kw['num_teeth_right'],
            center_dist_mm = belt_kw['center_dist_mm'],
            belt_height_mm = belt_kw['belt_height_mm'],  # raw height, no clearance
            _return_cq     = True,
        )
        assy.add(belt, name='Belt')

    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        assy.save(tmp.name)
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


def _largest_poly(geom):
    """Return a Shapely Polygon from geom, keeping only the largest piece if
    geom is a MultiPolygon.  Prevents trimesh.extrude_polygon from crashing
    when a Shapely difference produces tiny disconnected slivers."""
    from shapely.geometry import MultiPolygon as _MP
    if isinstance(geom, _MP):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def _clean_void(void, min_area: float = 1.0):
    """Drop zero-area Shapely float artifacts from a spoke void MultiPolygon.

    ann.difference(spoke_web) can produce dozens of degenerate slivers with
    area ≈ 0 alongside the 4 real void sectors.  Passing those slivers into
    poly.difference() creates 90+ interior rings that confuse trimesh's
    extrude_polygon triangulator, making the pulley appear solid.
    """
    from shapely.geometry import MultiPolygon as _MP
    from shapely.ops import unary_union
    if isinstance(void, _MP):
        real = [g for g in void.geoms if g.area >= min_area]
        if not real:
            return void          # fall back; better than nothing
        return unary_union(real) if len(real) > 1 else real[0]
    return void


def _spoke_void_sectors(R_hub, R_rim, spoke_count, spoke_width_mm,
                        fillet_tip_mm=0.0, fillet_base_mm=0.0, n_arc=32):
    """
    Return one Shapely Polygon per gap between spokes for 3D boolean subtraction.
    Tries to use _spoke_void_polygons (with fillets) first; if the resulting
    polygon doesn't produce a clean mesh, falls back to a simple annular sector.
    """
    if spoke_count <= 0 or spoke_width_mm <= 0.0 or R_rim <= R_hub + 0.5:
        return []

    # ── Try fillet polygons first ─────────────────────────────────────────────
    from exporters.png_exporter import _spoke_void_polygons
    fillet_polys = _spoke_void_polygons(
        R_hub, R_rim, spoke_count, spoke_width_mm,
        fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm,
    )
    result = []
    for vp in fillet_polys:
        if len(vp) < 3:
            continue
        poly = ShapelyPolygon(vp).buffer(0)   # fix self-touches / near-dupes
        poly = shapely_orient(poly, sign=1.0)
        if poly.is_valid and poly.area > 0.1:
            result.append(poly)

    if len(result) == spoke_count:
        return result   # all fillets succeeded — use them

    # ── Fallback: simple annular sectors (no fillets) ─────────────────────────
    half_w     = min(spoke_width_mm / 2.0, R_rim * 0.45)
    theta_step = 2.0 * math.pi / spoke_count
    theta_spk  = math.asin(min(half_w / R_rim, 0.9999))
    gap_half   = max(1e-6, theta_step / 2.0 - theta_spk)
    sectors = []
    for i in range(spoke_count):
        theta_mid = (i + 0.5) * theta_step
        a0 = theta_mid - gap_half
        a1 = theta_mid + gap_half
        angles = [a0 + (a1 - a0) * k / n_arc for k in range(n_arc + 1)]
        outer = [(R_rim * math.cos(a), R_rim * math.sin(a)) for a in angles]
        inner = [(R_hub * math.cos(a), R_hub * math.sin(a)) for a in reversed(angles)]
        poly = shapely_orient(ShapelyPolygon(outer + inner), sign=1.0)
        if poly.is_valid and poly.area > 0.1:
            sectors.append(poly)
    return sectors


def _rot2d(pts, angle):
    """Rotate a list of (x, y) points by `angle` radians (compass CW convention)."""
    if abs(angle) < 1e-9:
        return pts
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c + y * s, -x * s + y * c) for x, y in pts]


def _spoke_web_polygon(R_hub: float, R_rim_inner: float,
                       spoke_count: int, spoke_width: float,
                       n_arc: int = 32) -> ShapelyPolygon:
    """
    Return 2D cross-section polygon: hub disk + N radial spokes (no fillets).
    Fillets are applied separately via _apply_spoke_fillets().

    Fills from centre (r=0) to R_rim_inner.
    Spokes connect R_hub → R_rim_inner; the hub disk (0→R_hub) is always solid.
    spoke_width — tangential width of spoke measured at hub face.
    """
    hub_disk = ShapelyPoint(0.0, 0.0).buffer(R_hub, resolution=_BORE_SECTIONS)
    hub_disk = shapely_orient(hub_disk, sign=1.0)

    if spoke_count <= 0 or spoke_width <= 0.0 or R_rim_inner <= R_hub + 0.5:
        # No spokes → solid disk to R_rim_inner
        return shapely_orient(
            ShapelyPoint(0.0, 0.0).buffer(R_rim_inner, resolution=_BORE_SECTIONS), sign=1.0)

    # Only constrain at rim — hub overlap is allowed (spokes may merge at hub)
    half_w = min(spoke_width / 2.0, R_rim_inner * 0.45)

    theta_i = math.asin(min(half_w / R_hub,       0.9999))
    theta_o = math.asin(min(half_w / R_rim_inner, 0.9999))

    # One spoke centered on +X axis
    pts: list = []
    for a in np.linspace(-theta_i, theta_i, n_arc):
        pts.append((R_hub       * math.cos(a), R_hub       * math.sin(a)))
    for a in np.linspace(theta_o, -theta_o, n_arc):
        pts.append((R_rim_inner * math.cos(a), R_rim_inner * math.sin(a)))

    spoke_0 = ShapelyPolygon(pts)

    # Union all N rotated spokes + hub disk in one shot (avoids O(N) incremental rebuilds)
    theta_step = 2.0 * math.pi / spoke_count
    parts = [hub_disk, spoke_0]
    for i in range(1, spoke_count):
        parts.append(shapely_rotate(spoke_0, math.degrees(i * theta_step), origin=(0.0, 0.0)))
    result = shapely_unary_union(parts)

    return shapely_orient(result, sign=1.0)


def _apply_spoke_fillets(web: ShapelyPolygon, annulus: ShapelyPolygon,
                         fillet_base: float, fillet_tip: float,
                         gap_hw: float) -> ShapelyPolygon:
    """
    Apply spoke fillets by morphological OPENING on each individual void gap
    (buffer −r then +r), which rounds the convex corners of the void at the
    spoke-to-hub and spoke-to-rim junctions.  Returns the updated spoke web.
    """
    if fillet_base <= 0.05 and fillet_tip <= 0.05:
        return web

    void_full = annulus.difference(web)
    if void_full.is_empty:
        return web

    geoms = list(void_full.geoms) if void_full.geom_type == 'MultiPolygon' else [void_full]

    r_base = min(fillet_base, gap_hw * 0.80) if fillet_base > 0.05 else 0.0
    r_tip  = min(fillet_tip,  gap_hw * 0.80) if fillet_tip  > 0.05 else 0.0
    r1 = min(r_base, r_tip) if r_base > 0.05 and r_tip > 0.05 else max(r_base, r_tip)
    r2 = abs(r_base - r_tip) if r_base > 0.05 and r_tip > 0.05 else 0.0

    filleted_voids = []
    for g in geoms:
        if g.is_empty or g.area < 0.01:
            continue
        vp = g
        for r in (r1, r2):
            if r > 0.05:
                eroded = vp.buffer(-r, join_style=2, cap_style=2)
                if eroded.is_valid and not eroded.is_empty:
                    vp = eroded.buffer(r, join_style=1, cap_style=1)
        filleted_voids.append(vp)

    if not filleted_voids:
        return web

    from functools import reduce
    from shapely.ops import unary_union
    new_void = unary_union(filleted_voids)
    new_web  = annulus.difference(new_void)
    if new_web.is_empty:
        return web
    return shapely_orient(_largest_poly(new_web), sign=1.0)


def _build_pulley_mesh(family, pitch, num_teeth, bore_mm, belt_height_mm,
                       clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0,
                       phase=0.0, hub_od_mm=0.0, hub_height_mm=0.0,
                       screw_dia_mm=0.0, screw_count=0, captured_nut=False,
                       flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0,
                       spoke_count=0, spoke_width_mm=0.0, spoke_hub_od_mm=0.0,
                       fillet_tip_mm=0.0, fillet_base_mm=0.0, rim_depth_mm=0.0,
                       spoke_height_mm=0.0, flange_enabled=False, flange_height_mm=0.0):
    """
    Build a single watertight pulley trimesh solid.

    The bore (round or D-shaped) is punched by Shapely 2D polygon difference
    before any extrusion — no 3D boolean operations are used for the bore.
    Only radial set-screw holes / nut pockets still use boolean difference.
    """
    R_bore    = bore_mm / 2.0
    hub_valid = hub_height_mm > 0.0 and hub_od_mm > bore_mm
    R_hub     = hub_od_mm / 2.0 if hub_valid else 0.0
    do_screws = hub_valid and screw_dia_mm > 0.0 and screw_count > 0

    # ── Captured nut pre-calculations ────────────────────────────────────────
    if do_screws and captured_nut:
        waf, t_nut = _nut_dims(screw_dia_mm)
        R_circ    = waf / math.sqrt(3)
        pkt_y     = waf + 0.5
        pkt_x     = t_nut + 0.5
        R_pkt     = pkt_y / math.sqrt(3)
        pkt_depth = 2.0 * R_pkt
        if hub_height_mm < pkt_depth:
            hub_height_mm = pkt_depth
        min_hub_r  = R_bore + 3.0 * t_nut
        need_oblong = R_hub < min_hub_r
        eff_r = max(R_hub, min_hub_r)
        step  = math.pi
    else:
        waf = t_nut = R_circ = pkt_y = pkt_x = R_pkt = pkt_depth = 0.0
        min_hub_r   = 0.0
        need_oblong = False
        eff_r = R_hub
        step  = math.pi / 2.0

    screw_angles = [k * step for k in range(min(screw_count, 2))] if do_screws else []

    # ── 2D bore cross-section (D-shaped / keyway / round) ────────────────────
    bore_2d = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)

    # ── Belt section: outer toothed profile minus bore hole ───────────────────
    outline, _R_OD_mesh, _ = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )
    hub_z_start  = belt_height_mm
    if flange_enabled and hub_valid and eff_r > _R_OD_mesh:
        hub_z_start = belt_height_mm + flange_height_mm
    _has_spokes_mesh = spoke_count > 0 and spoke_width_mm > 0.0
    _flange_ext_mesh = flange_height_mm if (flange_enabled and not _has_spokes_mesh) else 0.0
    _ext_hub_h_mesh  = hub_height_mm + _flange_ext_mesh
    hub_top      = hub_z_start + _ext_hub_h_mesh
    total_height = hub_top if hub_valid else belt_height_mm
    outline = _rot2d(outline, phase)
    outer_poly = ShapelyPolygon(outline)
    outer_poly = shapely_orient(outer_poly, sign=1.0)
    belt_cross = outer_poly.difference(bore_2d) if bore_2d is not None else outer_poly
    belt_cross = _largest_poly(belt_cross)

    belt_mesh = trimesh.creation.extrude_polygon(belt_cross, belt_height_mm)
    belt_mesh.fix_normals()

    # ── Spoke voids ───────────────────────────────────────────────────────────
    if spoke_count > 0 and spoke_width_mm > 0.0:
        from exporters.png_exporter import _spoke_void_polygons
        _R_hub_s = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        _R_tr    = min(math.hypot(x, y) for x, y in outline)
        _R_rim_s = max(_R_tr - rim_depth_mm, _R_hub_s + 1.0)
        # Guard: hub/rim larger than pulley face, or bore >= hub OD — impossible geometry, skip spokes
        if _R_hub_s < _R_tr and _R_rim_s < _R_tr and _R_hub_s > bore_mm / 2.0:
            spk_h = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm

            # 1. Hub Cylinder (Full Height, minus bore)
            hub_circle = ShapelyPoint(0, 0).buffer(_R_hub_s, resolution=64)
            hub_poly = hub_circle.difference(bore_2d) if bore_2d is not None else hub_circle
            hub_poly = shapely_orient(_largest_poly(hub_poly), sign=1.0)
            hub_cyl = trimesh.creation.extrude_polygon(hub_poly, belt_height_mm)
            hub_cyl.fix_normals()

            # 2. Rim Mesh (Full Height, minus bore)
            rim_circle = ShapelyPoint(0, 0).buffer(_R_rim_s, resolution=64)
            rim_poly = outer_poly.difference(rim_circle)
            rim_poly = rim_poly.difference(bore_2d) if bore_2d is not None else rim_poly
            rim_poly = shapely_orient(_largest_poly(rim_poly), sign=1.0)
            rim_mesh = trimesh.creation.extrude_polygon(rim_poly, belt_height_mm)
            rim_mesh.fix_normals()

            # 3. Spoke Web Mesh (Partial Height, minus bore)
            spoke_poly = outer_poly
            _vp_shapes = []
            for vp in _spoke_void_polygons(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                           fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm):
                if len(vp) < 3:
                    continue
                vp_shape = ShapelyPolygon(vp).simplify(0.05, preserve_topology=True).buffer(0)
                vp_shape = shapely_orient(vp_shape, sign=1.0)
                if vp_shape.is_valid and vp_shape.area > 0.1:
                    _vp_shapes.append(vp_shape)
            if _vp_shapes:
                spoke_poly = spoke_poly.difference(shapely_unary_union(_vp_shapes))

            spoke_poly = spoke_poly.difference(bore_2d) if bore_2d is not None else spoke_poly
            spoke_poly = shapely_orient(_largest_poly(spoke_poly), sign=1.0)
            web_mesh = trimesh.creation.extrude_polygon(spoke_poly, spk_h)
            web_mesh.fix_normals()
            slab_offset = (belt_height_mm - spk_h) / 2.0
            web_mesh.apply_translation([0, 0, slab_offset])

            union_parts = []
            for m in [hub_cyl, rim_mesh, web_mesh]:
                if getattr(m, 'is_volume', False):
                    union_parts.append(m)
                elif not m.is_watertight:
                    trimesh.repair.fill_holes(m)
                    m.fix_normals()
                    if getattr(m, 'is_volume', False):
                        union_parts.append(m)

            try:
                belt_mesh = trimesh.boolean.union(union_parts, engine='manifold')
            except Exception:
                belt_mesh = web_mesh
        else:
            belt_mesh = trimesh.creation.extrude_polygon(outer_poly, belt_height_mm)
            belt_mesh.fix_normals()

    # ── Hub section: hub circle minus bore hole ───────────────────────────────
    if hub_valid:
        if captured_nut and need_oblong:
            hub_outer = ShapelyPoint(0, 0).buffer(R_hub, resolution=_BORE_SECTIONS)
            for angle in screw_angles:
                offset    = min_hub_r - R_hub
                hub_outer = hub_outer.union(
                    ShapelyPoint(offset * math.cos(angle),
                                 offset * math.sin(angle)).buffer(
                        R_hub, resolution=_BORE_SECTIONS))
        else:
            hub_outer = ShapelyPoint(0, 0).buffer(R_hub, resolution=_BORE_SECTIONS)
        hub_outer = shapely_orient(hub_outer, sign=1.0)
        hub_cross = hub_outer.difference(bore_2d) if bore_2d is not None else hub_outer
        hub_cross = _largest_poly(hub_cross)
        # Extend hub 0.01 mm below hub_z_start so it overlaps belt_mesh slightly.
        # This avoids coplanar touching-face failures in the manifold boolean union
        # that occur when hub_z_start == belt_height_mm (the common case).
        hub_mesh  = trimesh.creation.extrude_polygon(hub_cross, _ext_hub_h_mesh + 0.01)
        hub_mesh.apply_translation([0.0, 0.0, hub_z_start - 0.01])
        hub_mesh.fix_normals()
        try:
            body = trimesh.boolean.union([belt_mesh, hub_mesh], engine='manifold')
            body.fix_normals()
        except Exception:
            body = trimesh.util.concatenate([belt_mesh, hub_mesh])
            body.fix_normals()

        # ── Chamfer support cones under captured-nut lobes ────────────────────
        # Mirrors the STEP exporter: a 45° truncated cone is unioned under each
        # lobe so the 3D printer has a sloped surface instead of open air.
        if captured_nut and need_oblong and spoke_count > 0 and spoke_width_mm > 0.0:
            _R_hub_s_ch = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
            _spk_h_ch   = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm
            _pocket_ch  = (belt_height_mm - _spk_h_ch) / 2.0
            _ob_off_ch  = min_hub_r - R_hub

            if _pocket_ch > 0 and eff_r > _R_hub_s_ch:
                _ch = min(_spk_h_ch + _pocket_ch, R_hub - 0.5)
                if _ch > 0.1:
                    r_bottom = max(R_hub - _ch, 0.1)
                    r_top    = R_hub
                    z_bot    = belt_height_mm - _ch
                    for _sc_angle in screw_angles:
                        _lcx = _ob_off_ch * math.cos(_sc_angle)
                        _lcy = _ob_off_ch * math.sin(_sc_angle)
                        try:
                            cone_mesh = _make_frustum(r_bottom, r_top, _ch)
                            cone_mesh.apply_translation([_lcx, _lcy, z_bot])
                            cone_mesh.fix_normals()
                            # Cone may overlap the bore — subtract bore profile first
                            # so it doesn't fill back the hole when unioned with body.
                            if bore_2d is not None and cone_mesh.is_watertight:
                                _bc = trimesh.creation.extrude_polygon(bore_2d, _ch + 1.0)
                                _bc.apply_translation([0.0, 0.0, z_bot - 0.5])
                                _bc.fix_normals()
                                if _bc.is_watertight:
                                    cone_mesh = trimesh.boolean.difference(
                                        [cone_mesh, _bc], engine='manifold')
                                    cone_mesh.fix_normals()
                            if body.is_watertight and cone_mesh.is_watertight:
                                body = trimesh.boolean.union(
                                    [body, cone_mesh], engine='manifold')
                                body.fix_normals()
                        except Exception:
                            pass
    else:
        body = belt_mesh

    # ── Set-screw holes + nut pockets (boolean — radial features) ────────────
    # D-shaft: force screw at angle=0 (aligned with flat), nut against flat face.
    # Keyway:  force screw at angle=0 (into keyway slot), nut against slot outer face.
    if (flat_depth_mm > 0.0 or keyway_h_mm > 0.0) and do_screws:
        screw_angles = [0.0]

    if do_screws and body.is_watertight:
        R_screw = screw_dia_mm / 2.0

        if captured_nut:
            z_screw = hub_top - R_circ
            if flat_depth_mm > 0.0:
                # Nut pocket inner face sits against the D-flat face
                flat_x   = R_bore - flat_depth_mm
                hole_len = eff_r - flat_x + 1.0
                hole_cx  = (eff_r + flat_x) / 2.0
                pkt_cx   = flat_x
            elif keyway_h_mm > 0.0:
                # Nut pocket inner face sits against the keyway slot outer face
                kw_face  = R_bore + keyway_h_mm
                hole_len = eff_r - kw_face + 1.0
                hole_cx  = (eff_r + kw_face) / 2.0
                pkt_cx   = kw_face
            else:
                hole_len = eff_r - R_bore + 1.0
                hole_cx  = (eff_r + R_bore) / 2.0
                pkt_cx   = R_bore
        else:
            z_screw  = hub_z_start + _flange_ext_mesh + hub_height_mm / 2.0
            hole_len = R_hub * 2.0 + 2.0
            hole_cx  = 0.0

        for angle in screw_angles:
            hole = trimesh.creation.cylinder(radius=R_screw, height=hole_len, sections=32)
            hole.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
            hole.apply_translation([hole_cx, 0.0, z_screw])
            if abs(angle) > 1e-9:
                hole.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
            hole.fix_normals()
            if body.is_watertight:
                body = trimesh.boolean.difference([body, hole], engine='manifold')

            if captured_nut and body.is_watertight:
                half_y = pkt_y / 2.0
                top_z  = hub_top + 1.0
                low_z  = hub_top - 1.5 * R_pkt
                tip_z  = hub_top - 2.0 * R_pkt
                pocket_verts = [
                    ( half_y, top_z),
                    (-half_y, top_z),
                    (-half_y, low_z),
                    ( 0.0,    tip_z),
                    ( half_y, low_z),
                ]
                pocket_poly = ShapelyPolygon(pocket_verts)
                pocket = trimesh.creation.extrude_polygon(pocket_poly, pkt_x)
                rot = np.array([[0, 0, 1, 0],
                                [1, 0, 0, 0],
                                [0, 1, 0, 0],
                                [0, 0, 0, 1]], dtype=float)
                pocket.apply_transform(rot)
                pocket.apply_translation([pkt_cx, 0.0, 0.0])
                if abs(angle) > 1e-9:
                    pocket.apply_transform(
                        trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
                pocket.fix_normals()
                body = trimesh.boolean.difference([body, pocket], engine='manifold')

    return body


def _dedupe_pts(pts):
    """Remove consecutive near-duplicate points from a list of (x, y) tuples."""
    _MIN = 1e-4
    out = [pts[0]]
    for pt in pts[1:]:
        if math.hypot(pt[0] - out[-1][0], pt[1] - out[-1][1]) > _MIN:
            out.append(pt)
    if math.hypot(out[-1][0] - out[0][0], out[-1][1] - out[0][1]) < _MIN:
        out.pop()
    return out


def _build_belt_mesh(family, pitch, num_teeth1, num_teeth2,
                     center_dist_mm, belt_height_mm, cx1):
    """Build the belt body mesh. Returns trimesh or None."""
    if family not in BELT_FAMILIES:
        return None
    belt_ring, tooth_polys, _phi1, _phi2 = build_two_pulley_belt(
        family, pitch, num_teeth1, num_teeth2, center_dist_mm, x_offset=cx1,
    )
    if not belt_ring:
        return None

    outer_pts  = _dedupe_pts(belt_ring)
    outer_poly = ShapelyPolygon(outer_pts)
    outer_poly = shapely_orient(outer_poly, sign=1.0)

    if tooth_polys and tooth_polys[0]:
        inner_pts  = _dedupe_pts(tooth_polys[0])
        inner_poly = ShapelyPolygon(inner_pts)
        try:
            belt_poly = outer_poly.difference(inner_poly)
        except Exception:
            belt_poly = outer_poly
    else:
        belt_poly = outer_poly

    if not belt_poly.is_valid or belt_poly.is_empty or belt_poly.area < 0.1:
        return None

    from shapely.geometry import MultiPolygon
    if isinstance(belt_poly, MultiPolygon):
        belt_poly = max(belt_poly.geoms, key=lambda g: g.area)

    try:
        mesh = trimesh.creation.extrude_polygon(belt_poly, belt_height_mm)
        mesh.fix_normals()
        return mesh
    except Exception:
        return None


def generate_drive_stl_preview(
    family: str,
    pitch: str,
    num_teeth1: int,
    bore_mm1: float,
    num_teeth2: int,
    bore_mm2: float,
    center_dist_mm: float,
    belt_height_mm: float,
    clearance_mm1: float = 0.0,
    backlash_mm1:  float = 0.0,
    print_extra_mm1: float = 0.0,
    clearance_mm2: float = 0.0,
    backlash_mm2:  float = 0.0,
    print_extra_mm2: float = 0.0,
    hub_od_mm1: float = 0.0,
    hub_height_mm1: float = 0.0,
    hub_od_mm2: float = 0.0,
    hub_height_mm2: float = 0.0,
    screw_dia_mm1: float = 0.0,
    screw_count1: int = 0,
    captured_nut1: bool = False,
    screw_dia_mm2: float = 0.0,
    screw_count2: int = 0,
    captured_nut2: bool = False,
    flat_depth_mm1: float = 0.0,
    flat_depth_mm2: float = 0.0,
    keyway_w_mm1: float = 0.0,
    keyway_h_mm1: float = 0.0,
    keyway_w_mm2: float = 0.0,
    keyway_h_mm2: float = 0.0,
    spoke_count1: int = 0,
    spoke_width_mm1: float = 0.0,
    spoke_hub_od_mm1: float = 0.0,
    fillet_tip_mm1: float = 0.0,
    fillet_base_mm1: float = 0.0,
    rim_depth_mm1: float = 0.0,
    spoke_height_mm1: float = 0.0,
    spoke_count2: int = 0,
    spoke_width_mm2: float = 0.0,
    spoke_hub_od_mm2: float = 0.0,
    fillet_tip_mm2: float = 0.0,
    fillet_base_mm2: float = 0.0,
    rim_depth_mm2: float = 0.0,
    spoke_height_mm2: float = 0.0,
    part: str = 'all',
    flange1: dict = None,
    flange2: dict = None,
) -> bytes:
    """
    Return binary STL bytes of a two-pulley belt drive.

    part='pulleys' → only both pulleys (phase-aligned with belt)
    part='belt'    → only the belt body
    part='all'     → everything combined (for single-colour preview)

    Pulleys are phase-rotated so their grooves mesh with the belt teeth.
    All geometry is centred at the origin for Three.js auto-fit.
    """
    # Clamp to the minimum physically valid center distance (same as 2D preview)
    _key = PROFILE_KEY_PREFIX.get(family, '') + pitch
    if _key in PULLEY_SPECS:
        _pmm = PULLEY_SPECS[_key]['pitch']
        _r1  = num_teeth1 * _pmm / (2.0 * math.pi)
        _r2  = num_teeth2 * _pmm / (2.0 * math.pi)
        center_dist_mm = max(center_dist_mm, _r1 + _r2)

    cx1 = -center_dist_mm / 2.0
    cx2 =  center_dist_mm / 2.0

    # ── Phase angles from belt geometry ──────────────────────────────────────
    phi_left = phi_right = 0.0
    if family in BELT_FAMILIES:
        _, _, phi_left, phi_right = build_two_pulley_belt(
            family, pitch, num_teeth1, num_teeth2, center_dist_mm, x_offset=cx1,
        )

    # ── Build ALL parts unconditionally so the centroid is always computed
    #    from the full scene.  This is required so that part='pulleys' and
    #    part='belt' land at exactly the same origin in Three.js.
    # ── Pulley meshes ─────────────────────────────────────────────────────────
    p1 = _build_pulley_mesh(family, pitch, num_teeth1, bore_mm1, belt_height_mm,
                            clearance_mm1, backlash_mm1, print_extra_mm1,
                            phase=phi_left,
                            hub_od_mm=hub_od_mm1, hub_height_mm=hub_height_mm1,
                            screw_dia_mm=screw_dia_mm1, screw_count=screw_count1,
                            captured_nut=captured_nut1, flat_depth_mm=flat_depth_mm1,
                            keyway_w_mm=keyway_w_mm1, keyway_h_mm=keyway_h_mm1,
                            spoke_count=spoke_count1, spoke_width_mm=spoke_width_mm1,
                            spoke_hub_od_mm=spoke_hub_od_mm1, fillet_tip_mm=fillet_tip_mm1,
                            fillet_base_mm=fillet_base_mm1, rim_depth_mm=rim_depth_mm1,
                            spoke_height_mm=spoke_height_mm1,
                            flange_enabled=bool(flange1),
                            flange_height_mm=flange1.get('flange_height_mm', 1.5) if flange1 else 0.0)
    p1.apply_translation([cx1, 0.0, 0.0])

    p2 = _build_pulley_mesh(family, pitch, num_teeth2, bore_mm2, belt_height_mm,
                            clearance_mm2, backlash_mm2, print_extra_mm2,
                            phase=phi_right,
                            hub_od_mm=hub_od_mm2, hub_height_mm=hub_height_mm2,
                            screw_dia_mm=screw_dia_mm2, screw_count=screw_count2,
                            captured_nut=captured_nut2, flat_depth_mm=flat_depth_mm2,
                            keyway_w_mm=keyway_w_mm2, keyway_h_mm=keyway_h_mm2,
                            spoke_count=spoke_count2, spoke_width_mm=spoke_width_mm2,
                            spoke_hub_od_mm=spoke_hub_od_mm2, fillet_tip_mm=fillet_tip_mm2,
                            fillet_base_mm=fillet_base_mm2, rim_depth_mm=rim_depth_mm2,
                            spoke_height_mm=spoke_height_mm2,
                            flange_enabled=bool(flange2),
                            flange_height_mm=flange2.get('flange_height_mm', 1.5) if flange2 else 0.0)
    p2.apply_translation([cx2, 0.0, 0.0])

    # ── Belt mesh ─────────────────────────────────────────────────────────────
    belt_mesh = _build_belt_mesh(
        family, pitch, num_teeth1, num_teeth2, center_dist_mm, belt_height_mm, cx1
    )

    # ── Optional flange meshes ────────────────────────────────────────────────
    fl_meshes1, fl_meshes2 = [], []
    if flange1 or flange2:
        from exporters.flange_exporter import build_flange_meshes, build_socket_meshes
        sp_en1  = spoke_count1 > 0 and spoke_width_mm1 > 0.0
        sp_en2  = spoke_count2 > 0 and spoke_width_mm2 > 0.0
        if flange1:
            for m in build_flange_meshes(flange1, family, pitch, num_teeth1, bore_mm1,
                                         belt_height_mm,
                                         clearance_mm=clearance_mm1,
                                         print_extra_mm=print_extra_mm1,
                                         hub_od_mm=hub_od_mm1,
                                         hub_height_mm=hub_height_mm1,
                                         spokes_enabled=sp_en1,
                                         spoke_hub_od_mm=spoke_hub_od_mm1,
                                         rim_depth_mm=rim_depth_mm1,
                                         flat_depth_mm=flat_depth_mm1,
                                         keyway_w_mm=keyway_w_mm1,
                                         keyway_h_mm=keyway_h_mm1):
                m.apply_translation([cx1, 0.0, 0.0])
                fl_meshes1.append(m)
            # Subtract socket holes from p1 when nubs are active
            if flange1.get('nubs_enabled') and flange1.get('flange_3dprint') and flange1.get('top_separate'):
                sockets1 = build_socket_meshes(
                    flange1, family, pitch, num_teeth1, bore_mm1, belt_height_mm,
                    clearance_mm=clearance_mm1, print_extra_mm=print_extra_mm1,
                    hub_od_mm=hub_od_mm1, spokes_enabled=sp_en1,
                    spoke_hub_od_mm=spoke_hub_od_mm1, rim_depth_mm=rim_depth_mm1,
                )
                if sockets1 and getattr(p1, 'is_volume', False):
                    for s in sockets1:
                        s.apply_translation([cx1, 0.0, 0.0])
                    socket_comb1 = (trimesh.boolean.union(sockets1, engine='manifold')
                                    if len(sockets1) > 1 else sockets1[0])
                    try:
                        p1 = trimesh.boolean.difference([p1, socket_comb1], engine='manifold')
                    except Exception as _e:
                        import traceback as _tb
                        print(f'[dual-preview] p1 socket subtraction failed: {_e}\n'
                              + _tb.format_exc(), flush=True)
        if flange2:
            for m in build_flange_meshes(flange2, family, pitch, num_teeth2, bore_mm2,
                                         belt_height_mm,
                                         clearance_mm=clearance_mm2,
                                         print_extra_mm=print_extra_mm2,
                                         hub_od_mm=hub_od_mm2,
                                         hub_height_mm=hub_height_mm2,
                                         spokes_enabled=sp_en2,
                                         spoke_hub_od_mm=spoke_hub_od_mm2,
                                         rim_depth_mm=rim_depth_mm2,
                                         flat_depth_mm=flat_depth_mm2,
                                         keyway_w_mm=keyway_w_mm2,
                                         keyway_h_mm=keyway_h_mm2):
                m.apply_translation([cx2, 0.0, 0.0])
                fl_meshes2.append(m)
            # Subtract socket holes from p2 when nubs are active
            if flange2.get('nubs_enabled') and flange2.get('flange_3dprint') and flange2.get('top_separate'):
                sockets2 = build_socket_meshes(
                    flange2, family, pitch, num_teeth2, bore_mm2, belt_height_mm,
                    clearance_mm=clearance_mm2, print_extra_mm=print_extra_mm2,
                    hub_od_mm=hub_od_mm2, spokes_enabled=sp_en2,
                    spoke_hub_od_mm=spoke_hub_od_mm2, rim_depth_mm=rim_depth_mm2,
                )
                if sockets2 and getattr(p2, 'is_volume', False):
                    for s in sockets2:
                        s.apply_translation([cx2, 0.0, 0.0])
                    socket_comb2 = (trimesh.boolean.union(sockets2, engine='manifold')
                                    if len(sockets2) > 1 else sockets2[0])
                    try:
                        p2 = trimesh.boolean.difference([p2, socket_comb2], engine='manifold')
                    except Exception as _e:
                        import traceback as _tb
                        print(f'[dual-preview] p2 socket subtraction failed: {_e}\n'
                              + _tb.format_exc(), flush=True)

    # ── Compute centroid from the full scene (pulleys + belt + flanges) ───────
    all_meshes = [p1, p2] + ([belt_mesh] if belt_mesh else []) + fl_meshes1 + fl_meshes2
    offset = -trimesh.util.concatenate(all_meshes).centroid

    # Apply the shared offset to every part so all responses share one origin
    p1.apply_translation(offset)
    p2.apply_translation(offset)
    if belt_mesh:
        belt_mesh.apply_translation(offset)
    for m in fl_meshes1 + fl_meshes2:
        m.apply_translation(offset)

    # ── Return the requested subset ───────────────────────────────────────────
    if part == 'pulleys':
        export_parts = [p1, p2] + fl_meshes1 + fl_meshes2
    elif part == 'p1':
        export_parts = [p1] + fl_meshes1
    elif part == 'p2':
        export_parts = [p2] + fl_meshes2
    elif part == 'belt':
        export_parts = [belt_mesh] if belt_mesh else []
    else:
        export_parts = [p1, p2] + ([belt_mesh] if belt_mesh else []) + fl_meshes1 + fl_meshes2

    if not export_parts:
        raise ValueError('No geometry to export')

    return trimesh.util.concatenate(export_parts).export(file_type='stl')


def generate_pulley_stl_preview(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    hub_height_mm: float = 0.0,
    screw_dia_mm: float = 0.0,
    screw_count: int = 0,
    captured_nut: bool = False,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    spoke_height_mm: float = 0.0,
    flange_enabled: bool = False,
    flange_height_mm: float = 0.0,
    socket_meshes: list = None,
) -> bytes:
    """
    Same as generate_pulley_stl but centres the mesh at the origin so
    Three.js auto-fits it nicely.

    ``socket_meshes`` — optional list of trimesh cylinders to subtract from the
    pulley body before export (used for flange nub sockets).  Subtraction is
    done on the live trimesh object so the mesh stays manifold.
    """
    mesh = _build_pulley_mesh(
        family, pitch, num_teeth, bore_mm, belt_height_mm,
        clearance_mm, backlash_mm, print_extra_mm,
        hub_od_mm=hub_od_mm, hub_height_mm=hub_height_mm,
        screw_dia_mm=screw_dia_mm, screw_count=screw_count, captured_nut=captured_nut,
        flat_depth_mm=flat_depth_mm, keyway_w_mm=keyway_w_mm, keyway_h_mm=keyway_h_mm,
        spoke_count=spoke_count, spoke_width_mm=spoke_width_mm,
        spoke_hub_od_mm=spoke_hub_od_mm, fillet_tip_mm=fillet_tip_mm,
        fillet_base_mm=fillet_base_mm, rim_depth_mm=rim_depth_mm,
        spoke_height_mm=spoke_height_mm,
        flange_enabled=flange_enabled, flange_height_mm=flange_height_mm,
    )
    if socket_meshes:
        # Union sockets first so overlapping cylinders are resolved into one solid
        # before the boolean difference — concatenate alone leaves intersecting
        # geometry that confuses manifold and leaves material in overlap zones.
        socket_comb = (trimesh.boolean.union(socket_meshes, engine='manifold')
                       if len(socket_meshes) > 1 else socket_meshes[0])
        try:
            mesh = trimesh.boolean.difference([mesh, socket_comb], engine='manifold')
        except Exception as _e:
            print(f'[preview] socket subtraction failed: {_e}', flush=True)
    mesh.apply_translation(-mesh.centroid)
    return mesh.export(file_type='stl')


def generate_belt_step(
    family: str,
    pitch: str,
    num_teeth_left: int,
    num_teeth_right: int,
    center_dist_mm: float,
    belt_height_mm: float,
    _return_cq: bool = False,
) -> bytes:
    """
    Return STEP bytes of a two-pulley belt cross-section.

    Outer (back) surface: two true circular arcs + two straight lines.
    Inner (tooth) surface: per-tooth B-splines using includeCurrent=True.

    Both surfaces are built from belt_outline_segments() — the same source
    used by the DXF exporter — so geometry is defined in one place.

    Solid = extruded outer shape minus extruded inner (tooth-cavity) shape.
    """
    import cadquery as cq
    import tempfile, os

    # ── Shared belt geometry ──────────────────────────────────────────────────
    outer_segs, inner_segs, n_belt, _belt_spec, C = belt_outline_segments(
        family, pitch, num_teeth_left, num_teeth_right, center_dist_mm
    )
    if not inner_segs:
        raise ValueError(f'Belt geometry not available for {family} {pitch}')

    # ── Outer back-surface solid ──────────────────────────────────────────────
    outer_solid = _segs_to_cq_sketch(outer_segs, cq.Workplane('XY')).extrude(belt_height_mm)

    # ── Inner toothed-surface solid ───────────────────────────────────────────
    inner_solid = _segs_to_cq_sketch(inner_segs, cq.Workplane('XY')).extrude(belt_height_mm)

    result = outer_solid.cut(inner_solid)

    if _return_cq:
        return result

    # ── Export ────────────────────────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        cq.exporters.export(result, tmp.name)
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)
