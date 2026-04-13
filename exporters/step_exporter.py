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

from geometry.pulley_geometry import (
    generate_profile_groove, _build_groove_points,
    wrap_groove_to_pulley, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    build_two_pulley_belt, BELT_FAMILIES,
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


def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


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
                      keyway_h_mm: float = 0.0) -> trimesh.Trimesh:
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

    hub_top      = belt_height_mm + hub_height_mm
    total_height = belt_height_mm

    # ── Hub boss ──────────────────────────────────────────────────────────────
    if hub_valid and body.is_watertight:
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
            hub_mesh = trimesh.creation.extrude_polygon(hub_poly, hub_height_mm)
            hub_mesh.apply_translation([0.0, 0.0, belt_height_mm])
        else:
            hub_mesh = trimesh.creation.cylinder(
                radius=R_hub, height=hub_height_mm, sections=_BORE_SECTIONS)
            hub_mesh.apply_translation([0.0, 0.0, belt_height_mm + hub_height_mm / 2.0])

        hub_mesh.fix_normals()
        body         = trimesh.boolean.union([body, hub_mesh], engine='manifold')
        total_height = hub_top

    # ── Bore (round or D-shaped) ──────────────────────────────────────────────
    if R_bore > 0.5 and body.is_watertight:
        extra = 0.5
        bore_h = total_height + extra * 2
        if flat_depth_mm > 0.0:
            # Build a D-shaped bore by extruding the D polygon cross-section
            d_poly = _d_bore_polygon(R_bore, flat_depth_mm, sections=_BORE_SECTIONS)
            d_poly = shapely_orient(d_poly, sign=1.0)
            bore_solid = trimesh.creation.extrude_polygon(d_poly, bore_h)
            bore_solid.apply_translation([0.0, 0.0, -extra])
        else:
            bore_solid = trimesh.creation.cylinder(
                radius=R_bore, height=bore_h, sections=_BORE_SECTIONS)
            bore_solid.apply_translation([0.0, 0.0, total_height / 2.0])
        bore_solid.fix_normals()
        body = trimesh.boolean.difference([body, bore_solid], engine='manifold')

    # ── Keyway slot (rectangular slot projecting outward from bore wall) ───────
    if keyway_w_mm > 0.0 and keyway_h_mm > 0.0 and R_bore > 0.5 and body.is_watertight:
        kw_depth = keyway_h_mm   # hub keyway depth = H/2
        kw_box = trimesh.creation.box(
            extents=[kw_depth + 1.0, keyway_w_mm, total_height + 1.0])
        kw_box.apply_translation([R_bore + kw_depth / 2.0, 0.0, total_height / 2.0])
        kw_box.fix_normals()
        body = trimesh.boolean.difference([body, kw_box], engine='manifold')

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
            z_screw  = belt_height_mm + hub_height_mm / 2.0
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
) -> bytes:
    """
    Return binary STL bytes of an extruded timing pulley solid.

    The toothed body is extruded from z=0 to z=belt_height_mm.
    If hub_od_mm and hub_height_mm are given, a hub cylinder is unioned on top
    (z=belt_height_mm to z=belt_height_mm+hub_height_mm).  The hub OD may
    exceed the pulley OD.  The bore is subtracted through the full height.
    """
    outline = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )[0]

    outer_poly = ShapelyPolygon(outline)
    outer_poly = shapely_orient(outer_poly, sign=1.0)

    # ── Extrude full solid (spoke voids handled below if enabled) ─────────────
    body = trimesh.creation.extrude_polygon(outer_poly, belt_height_mm)
    body.fix_normals()

    if spoke_count > 0 and spoke_width_mm > 0.0:
        from exporters.png_exporter import _spoke_void_polygons
        _R_hub_s = (spoke_hub_od_mm / 2.0) if spoke_hub_od_mm > 0.0 else (bore_mm / 2.0 + 1.0)
        _R_tr    = min(math.hypot(x, y) for x, y in outline)
        _R_rim_s = max(_R_tr - rim_depth_mm, _R_hub_s + 1.0)
        spk_h = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm
        # Build spoke-voided 2D cross-section
        spoke_poly = outer_poly
        for vp in _spoke_void_polygons(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                       fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm):
            if len(vp) < 3:
                continue
            vp_shape = ShapelyPolygon(vp).simplify(0.05, preserve_topology=True).buffer(0)
            vp_shape = shapely_orient(vp_shape, sign=1.0)
            if vp_shape.is_valid and vp_shape.area > 0.1:
                spoke_poly = spoke_poly.difference(vp_shape)
                spoke_poly = _largest_poly(spoke_poly)
        spoke_poly = shapely_orient(spoke_poly, sign=1.0)
        # Build the spoke void slab directly at the centred position.
        # slab_offset centres voids at belt_height/2 with total depth = spk_h.
        slab_offset = (belt_height_mm - spk_h) / 2.0
        slab_solid = trimesh.creation.extrude_polygon(outer_poly, spk_h)
        slab_solid.fix_normals()
        slab_solid.apply_translation([0.0, 0.0, slab_offset])
        slab_spoke = trimesh.creation.extrude_polygon(spoke_poly, spk_h)
        slab_spoke.fix_normals()
        if not slab_spoke.is_watertight:
            trimesh.repair.fill_holes(slab_spoke)
            slab_spoke.fix_normals()
        slab_spoke.apply_translation([0.0, 0.0, slab_offset])
        voids_slab = trimesh.boolean.difference([slab_solid, slab_spoke], engine='manifold')
        body = trimesh.boolean.difference([body, voids_slab], engine='manifold')

    result = _add_hub_and_bore(body, belt_height_mm, bore_mm,
                               hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                               captured_nut, flat_depth_mm, keyway_w_mm, keyway_h_mm)
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
) -> bytes:
    """
    Return STEP bytes of a timing pulley with all hub features using cadquery B-rep.

    Builds the geometry natively in cadquery (proper solid B-rep, not mesh),
    producing a compact STEP file that loads instantly in Fusion 360 / eDrawings.
    """
    import cadquery as cq
    import tempfile, os

    # ── 2D toothed outline ────────────────────────────────────────────────────
    outline, _R_OD, _spec = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm,
    )
    # Simplify the profile for STEP: 0.05 mm tolerance reduces hundreds of
    # near-collinear points without noticeably changing the tooth geometry.
    _step_poly = ShapelyPolygon(outline).simplify(0.05, preserve_topology=True)
    outline = list(_step_poly.exterior.coords[:-1])

    # ── Toothed body (extrude 2D profile) ────────────────────────────────────
    result = (cq.Workplane('XY')
              .polyline(outline)
              .close()
              .extrude(belt_height_mm))

    # ── Hub pre-calculations (mirrors _add_hub_and_bore logic) ───────────────
    R_bore    = bore_mm / 2.0
    hub_valid = hub_height_mm > 0.0 and hub_od_mm > bore_mm
    R_hub     = hub_od_mm / 2.0 if hub_valid else 0.0
    do_screws = hub_valid and screw_dia_mm > 0.0 and screw_count > 0

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
    hub_top      = belt_height_mm + hub_height_mm
    total_height = belt_height_mm

    # ── Hub boss ──────────────────────────────────────────────────────────────
    if hub_valid:
        if captured_nut and need_oblong:
            # Build oblong profile via Shapely, then extrude in cadquery
            hub_poly = ShapelyPoint(0, 0).buffer(R_hub, resolution=_BORE_SECTIONS)
            for angle in screw_angles:
                offset   = min_hub_r - R_hub
                hub_poly = hub_poly.union(
                    ShapelyPoint(offset * math.cos(angle),
                                 offset * math.sin(angle)).buffer(
                        R_hub, resolution=_BORE_SECTIONS))
            hub_pts = list(hub_poly.exterior.coords[:-1])  # drop repeated closing pt
            hub_boss = (cq.Workplane('XY')
                        .workplane(offset=belt_height_mm)
                        .polyline(hub_pts)
                        .close()
                        .extrude(hub_height_mm))
        else:
            hub_boss = (cq.Workplane('XY')
                        .workplane(offset=belt_height_mm)
                        .circle(R_hub)
                        .extrude(hub_height_mm))
        result       = result.union(hub_boss)
        total_height = hub_top

    # ── Bore (round or D-shaped) ──────────────────────────────────────────────
    if R_bore > 0.5:
        extra  = 0.5
        bore_h = total_height + extra * 2
        if flat_depth_mm > 0.0:
            # Extrude the D-shaped polygon as the bore solid to cut away
            d_poly = _d_bore_polygon(R_bore, flat_depth_mm, sections=_BORE_SECTIONS)
            d_poly = shapely_orient(d_poly, sign=1.0)
            d_pts  = list(d_poly.exterior.coords[:-1])   # drop repeated closing point
            bore_solid = (cq.Workplane('XY')
                          .polyline(d_pts)
                          .close()
                          .extrude(bore_h)
                          .translate((0.0, 0.0, -extra)))
        else:
            bore_solid = (cq.Workplane('XY')
                          .circle(R_bore)
                          .extrude(bore_h)
                          .translate((0.0, 0.0, -extra)))
        result = result.cut(bore_solid)

    # ── Keyway slot ───────────────────────────────────────────────────────────
    if keyway_w_mm > 0.0 and keyway_h_mm > 0.0 and R_bore > 0.5:
        kw_depth = keyway_h_mm
        kw_box = (cq.Workplane('XY')
                  .box(kw_depth + 1.0, keyway_w_mm, total_height + 1.0)
                  .translate((R_bore + kw_depth / 2.0, 0.0, total_height / 2.0)))
        result = result.cut(kw_box)

    # ── Set-screw holes + nut pockets ─────────────────────────────────────────
    # Keyway: screw at angle=0, nut pocket against keyway slot outer face.
    if keyway_h_mm > 0.0 and do_screws:
        screw_angles = [0.0]

    if do_screws:
        R_screw = screw_dia_mm / 2.0

        if captured_nut:
            z_screw = hub_top - R_circ
            if keyway_h_mm > 0.0:
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
            z_screw  = belt_height_mm + hub_height_mm / 2.0
            hole_len = R_hub * 2.0 + 2.0
            hole_cx  = 0.0

        for angle in screw_angles:
            # Radial screw hole: cylinder along X, centred at (hole_cx, 0, z_screw)
            x_start = hole_cx - hole_len / 2.0
            hole = (cq.Workplane('YZ')
                    .circle(R_screw)
                    .extrude(hole_len)
                    .translate((x_start, 0.0, z_screw)))
            if abs(angle) > 1e-9:
                hole = hole.rotate((0, 0, 0), (0, 0, 1), math.degrees(angle))
            result = result.cut(hole)

            # Nut pocket: pentagon in YZ plane, extruded along +X from bore face
            if captured_nut:
                half_y = pkt_y / 2.0
                top_z  = hub_top + 1.0          # 1 mm overshoot → clean open top
                low_z  = hub_top - 1.5 * R_pkt  # lower hex corners
                tip_z  = hub_top - 2.0 * R_pkt  # bottom V tip

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
                          .extrude(pkt_x))
                if abs(angle) > 1e-9:
                    pocket = pocket.rotate((0, 0, 0), (0, 0, 1), math.degrees(angle))
                result = result.cut(pocket)

    # ── Export to STEP ────────────────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        cq.exporters.export(result, tmp.name)
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

    # Union all N rotated spokes + hub disk (fillets applied to void by callers)
    result = hub_disk.union(spoke_0)
    theta_step = 2.0 * math.pi / spoke_count
    for i in range(1, spoke_count):
        rotated = shapely_rotate(spoke_0, math.degrees(i * theta_step), origin=(0.0, 0.0))
        result = result.union(rotated)

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
                       spoke_height_mm=0.0):
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
    hub_top      = belt_height_mm + hub_height_mm
    total_height = hub_top if hub_valid else belt_height_mm

    # ── 2D bore cross-section (D-shaped or round) ─────────────────────────────
    if R_bore > 0.5:
        if flat_depth_mm > 0.0:
            bore_2d = _d_bore_polygon(R_bore, flat_depth_mm, sections=_BORE_SECTIONS)
        else:
            bore_2d = ShapelyPoint(0, 0).buffer(R_bore, resolution=_BORE_SECTIONS)
        bore_2d = shapely_orient(bore_2d, sign=1.0)
        # ── Keyway: union a rectangle outward from bore wall ──────────────────
        # Start the rectangle at x=0 (bore centre) so it fully overlaps the
        # bore circle in the central strip, guaranteeing a clean union with
        # no gap between the two shapes at y=±W/2.  The bore circle arc then
        # naturally forms the inner corners of the keyway slot.
        if keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
            kw_half  = keyway_w_mm / 2.0
            kw_depth = keyway_h_mm
            keyway_rect = ShapelyPolygon([
                (0.0,             -kw_half),
                (R_bore + kw_depth, -kw_half),
                (R_bore + kw_depth,  kw_half),
                (0.0,              kw_half),
            ])
            merged = bore_2d.union(keyway_rect)
            # Guard against degenerate MultiPolygon (shouldn't happen, but be safe)
            from shapely.geometry import MultiPolygon as _MP
            if isinstance(merged, _MP):
                merged = max(merged.geoms, key=lambda g: g.area)
            bore_2d = shapely_orient(merged, sign=1.0)
    else:
        bore_2d = None

    # ── Belt section: outer toothed profile minus bore hole ───────────────────
    outline = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )[0]
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
        spk_h = min(spoke_height_mm, belt_height_mm) if spoke_height_mm > 0.0 else belt_height_mm
        spoke_cross = belt_cross
        for vp in _spoke_void_polygons(_R_hub_s, _R_rim_s, spoke_count, spoke_width_mm,
                                       fillet_tip_mm=fillet_tip_mm, fillet_base_mm=fillet_base_mm):
            if len(vp) < 3:
                continue
            vp_shape = ShapelyPolygon(vp).simplify(0.05, preserve_topology=True).buffer(0)
            vp_shape = shapely_orient(vp_shape, sign=1.0)
            if vp_shape.is_valid and vp_shape.area > 0.1:
                spoke_cross = spoke_cross.difference(vp_shape)
                spoke_cross = _largest_poly(spoke_cross)
        spoke_cross = shapely_orient(spoke_cross, sign=1.0)
        slab_offset = (belt_height_mm - spk_h) / 2.0
        slab_solid = trimesh.creation.extrude_polygon(belt_cross, spk_h)
        slab_solid.fix_normals()
        slab_solid.apply_translation([0.0, 0.0, slab_offset])
        slab_spoke = trimesh.creation.extrude_polygon(spoke_cross, spk_h)
        slab_spoke.fix_normals()
        if not slab_spoke.is_watertight:
            trimesh.repair.fill_holes(slab_spoke)
            slab_spoke.fix_normals()
        slab_spoke.apply_translation([0.0, 0.0, slab_offset])
        voids_slab = trimesh.boolean.difference([slab_solid, slab_spoke], engine='manifold')
        belt_mesh = trimesh.boolean.difference([belt_mesh, voids_slab], engine='manifold')

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
        hub_mesh  = trimesh.creation.extrude_polygon(hub_cross, hub_height_mm)
        hub_mesh.apply_translation([0.0, 0.0, belt_height_mm])
        hub_mesh.fix_normals()
        body = trimesh.util.concatenate([belt_mesh, hub_mesh])
        body.fix_normals()
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
            z_screw  = belt_height_mm + hub_height_mm / 2.0
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
    spoke_count: int = 0,
    spoke_width_mm: float = 0.0,
    spoke_hub_od_mm: float = 0.0,
    fillet_tip_mm: float = 0.0,
    fillet_base_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    spoke_height_mm: float = 0.0,
    part: str = 'all',
) -> bytes:
    """
    Return binary STL bytes of a two-pulley belt drive.

    part='pulleys' → only both pulleys (phase-aligned with belt)
    part='belt'    → only the belt body
    part='all'     → everything combined (for single-colour preview)

    Pulleys are phase-rotated so their grooves mesh with the belt teeth.
    All geometry is centred at the origin for Three.js auto-fit.
    """
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
                            spoke_count=spoke_count, spoke_width_mm=spoke_width_mm,
                            spoke_hub_od_mm=spoke_hub_od_mm, fillet_tip_mm=fillet_tip_mm,
                            fillet_base_mm=fillet_base_mm, rim_depth_mm=rim_depth_mm,
                            spoke_height_mm=spoke_height_mm)
    p1.apply_translation([cx1, 0.0, 0.0])

    p2 = _build_pulley_mesh(family, pitch, num_teeth2, bore_mm2, belt_height_mm,
                            clearance_mm2, backlash_mm2, print_extra_mm2,
                            phase=phi_right,
                            hub_od_mm=hub_od_mm2, hub_height_mm=hub_height_mm2,
                            screw_dia_mm=screw_dia_mm2, screw_count=screw_count2,
                            captured_nut=captured_nut2, flat_depth_mm=flat_depth_mm2,
                            keyway_w_mm=keyway_w_mm2, keyway_h_mm=keyway_h_mm2,
                            spoke_count=spoke_count, spoke_width_mm=spoke_width_mm,
                            spoke_hub_od_mm=spoke_hub_od_mm, fillet_tip_mm=fillet_tip_mm,
                            fillet_base_mm=fillet_base_mm, rim_depth_mm=rim_depth_mm,
                            spoke_height_mm=spoke_height_mm)
    p2.apply_translation([cx2, 0.0, 0.0])

    # ── Belt mesh ─────────────────────────────────────────────────────────────
    belt_mesh = _build_belt_mesh(
        family, pitch, num_teeth1, num_teeth2, center_dist_mm, belt_height_mm, cx1
    )

    # ── Compute centroid from the full scene (pulleys + belt) ─────────────────
    all_meshes = [p1, p2] + ([belt_mesh] if belt_mesh else [])
    offset = -trimesh.util.concatenate(all_meshes).centroid

    # Apply the shared offset to every part so all responses share one origin
    p1.apply_translation(offset)
    p2.apply_translation(offset)
    if belt_mesh:
        belt_mesh.apply_translation(offset)

    # ── Return the requested subset ───────────────────────────────────────────
    if part == 'pulleys':
        export_parts = [p1, p2]
    elif part == 'p1':
        export_parts = [p1]
    elif part == 'p2':
        export_parts = [p2]
    elif part == 'belt':
        export_parts = [belt_mesh] if belt_mesh else []
    else:
        export_parts = [p1, p2] + ([belt_mesh] if belt_mesh else [])

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
) -> bytes:
    """
    Same as generate_pulley_stl but centres the mesh at the origin so
    Three.js auto-fits it nicely.
    """
    stl_bytes = generate_pulley_stl(
        family, pitch, num_teeth, bore_mm, belt_height_mm,
        clearance_mm, backlash_mm, print_extra_mm,
        hub_od_mm, hub_height_mm, screw_dia_mm, screw_count, captured_nut,
        flat_depth_mm, keyway_w_mm, keyway_h_mm,
        spoke_count, spoke_width_mm, spoke_hub_od_mm, fillet_tip_mm, fillet_base_mm, rim_depth_mm,
        spoke_height_mm,
    )
    mesh = trimesh.load(io.BytesIO(stl_bytes), file_type='stl')
    mesh.apply_translation(-mesh.centroid)
    return mesh.export(file_type='stl')


def generate_spoke_layer_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    spoke_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    spoke_count: int = 4,
    spoke_width_mm: float = 4.0,
    fillet_tip_mm: float = 1.0,
    fillet_base_mm: float = 1.5,
    hub_height_mm: float = 0.0,
    screw_dia_mm: float = 0.0,
    screw_count: int = 0,
    captured_nut: bool = False,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """
    Layer-cake part 1: spoke web + hub section (one connected piece).
    Height = spoke_height_mm (or belt_height_mm if 0).
    """
    outline, _R_OD, _spec = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm)
    R_tooth_root = min(math.hypot(x, y) for x, y in outline)
    R_bore = bore_mm / 2.0
    R_hub  = hub_od_mm / 2.0 if hub_od_mm > bore_mm else max(R_bore * 1.5, R_bore + 3.0)
    R_rim_inner = max(R_tooth_root - rim_depth_mm, R_hub + 1.0)
    layer_h = spoke_height_mm if spoke_height_mm > 0.5 else belt_height_mm

    # 2D bore cross-section
    bore_2d = None
    if R_bore > 0.5:
        bore_2d = (_d_bore_polygon(R_bore, flat_depth_mm, sections=_BORE_SECTIONS)
                   if flat_depth_mm > 0.0 else
                   ShapelyPoint(0, 0).buffer(R_bore, resolution=_BORE_SECTIONS))
        bore_2d = shapely_orient(bore_2d, sign=1.0)
        if keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
            kw_rect = ShapelyPolygon([
                (0.0,              -keyway_w_mm / 2.0),
                (R_bore + keyway_h_mm, -keyway_w_mm / 2.0),
                (R_bore + keyway_h_mm,  keyway_w_mm / 2.0),
                (0.0,               keyway_w_mm / 2.0),
            ])
            bore_2d = shapely_orient(bore_2d.union(kw_rect), sign=1.0)

    # Spoke web cross-section (hub disk + N spokes, with fillets)
    _ann_sl = (ShapelyPoint(0, 0).buffer(R_rim_inner).difference(ShapelyPoint(0, 0).buffer(R_hub)))
    _half_w_sl  = min(spoke_width_mm / 2.0, R_rim_inner * 0.45)
    _theta_o_sl = math.asin(min(_half_w_sl / R_rim_inner, 0.9999))
    _gap_hw_sl  = (math.pi / spoke_count - _theta_o_sl) * R_rim_inner
    web_poly = _spoke_web_polygon(R_hub, R_rim_inner, spoke_count, spoke_width_mm)
    web_poly = _apply_spoke_fillets(web_poly, _ann_sl, fillet_base_mm, fillet_tip_mm, _gap_hw_sl)
    if bore_2d is not None:
        web_poly = web_poly.difference(bore_2d)
    web_poly = _largest_poly(web_poly)
    web_poly = shapely_orient(web_poly, sign=1.0)

    body = trimesh.creation.extrude_polygon(web_poly, layer_h)
    body.fix_normals()

    # Hub boss + retention features above spoke layer
    body = _add_hub_and_bore(body, layer_h, bore_mm,
                              hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                              captured_nut, flat_depth_mm, keyway_w_mm, keyway_h_mm)
    return body.export(file_type='stl')


def generate_rim_ring_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    belt_height_mm: float,
    spoke_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
) -> bytes:
    """
    Layer-cake part 2: outer toothed ring only (no hub, no bore).
    Height = belt_height_mm - spoke_height_mm.
    """
    outline, _R_OD, _spec = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm)
    R_tooth_root = min(math.hypot(x, y) for x, y in outline)
    R_rim_inner = max(R_tooth_root - rim_depth_mm, 1.0)
    body_h = max(belt_height_mm - spoke_height_mm, 1.0) if spoke_height_mm > 0 else belt_height_mm

    outer_poly = ShapelyPolygon(outline)
    outer_poly = shapely_orient(outer_poly, sign=1.0)
    inner_disk = ShapelyPoint(0, 0).buffer(R_rim_inner, resolution=_BORE_SECTIONS)
    ring_cross = outer_poly.difference(inner_disk)
    ring_cross = _largest_poly(ring_cross)
    ring_cross = shapely_orient(ring_cross, sign=1.0)

    body = trimesh.creation.extrude_polygon(ring_cross, body_h)
    body.fix_normals()
    return body.export(file_type='stl')


def generate_hub_disk_stl(
    bore_mm: float,
    hub_od_mm: float,
    belt_height_mm: float,
    spoke_height_mm: float,
    hub_height_mm: float = 0.0,
    screw_dia_mm: float = 0.0,
    screw_count: int = 0,
    captured_nut: bool = False,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """
    Layer-cake part 3: hub disk only (with bore + retention features, no rim).
    Height = belt_height_mm - spoke_height_mm (+ hub boss above if configured).
    """
    R_bore = bore_mm / 2.0
    R_hub  = hub_od_mm / 2.0 if hub_od_mm > bore_mm else max(R_bore * 1.5, R_bore + 3.0)
    body_h = max(belt_height_mm - spoke_height_mm, 1.0) if spoke_height_mm > 0 else belt_height_mm

    # 2D bore cross-section
    bore_2d = None
    if R_bore > 0.5:
        bore_2d = (_d_bore_polygon(R_bore, flat_depth_mm, sections=_BORE_SECTIONS)
                   if flat_depth_mm > 0.0 else
                   ShapelyPoint(0, 0).buffer(R_bore, resolution=_BORE_SECTIONS))
        bore_2d = shapely_orient(bore_2d, sign=1.0)
        if keyway_w_mm > 0.0 and keyway_h_mm > 0.0:
            kw_rect = ShapelyPolygon([
                (0.0,              -keyway_w_mm / 2.0),
                (R_bore + keyway_h_mm, -keyway_w_mm / 2.0),
                (R_bore + keyway_h_mm,  keyway_w_mm / 2.0),
                (0.0,               keyway_w_mm / 2.0),
            ])
            bore_2d = shapely_orient(bore_2d.union(kw_rect), sign=1.0)

    hub_poly = ShapelyPoint(0, 0).buffer(R_hub, resolution=_BORE_SECTIONS)
    hub_poly = shapely_orient(hub_poly, sign=1.0)
    if bore_2d is not None:
        hub_poly = hub_poly.difference(bore_2d)
    hub_poly = _largest_poly(hub_poly)
    hub_poly = shapely_orient(hub_poly, sign=1.0)

    body = trimesh.creation.extrude_polygon(hub_poly, body_h)
    body.fix_normals()

    # Hub boss + retention features
    body = _add_hub_and_bore(body, body_h, bore_mm,
                              hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                              captured_nut, flat_depth_mm, keyway_w_mm, keyway_h_mm)
    return body.export(file_type='stl')
