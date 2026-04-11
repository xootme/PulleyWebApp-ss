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


def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def _build_outline_points(family, pitch, num_teeth,
                          clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0):
    """
    Return a closed list of (x, y) mm points forming the full pulley outline:
      tooth groove segments (dense sampled)  +  OD arc lands (arc-sampled).

    The pulley is centred at the origin; x-right, y-up (same as SVG / DXF).
    """
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
        n_arc   = max(2, int(math.ceil(arc_len / _ARC_STEP_MM)))

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
                      captured_nut: bool = False) -> trimesh.Trimesh:
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

      Pocket shape  (rectangular box, drops from hub top):
          radial depth  (X) = t_nut + 0.5 clearance   (nut thickness)
          tangential width (Y) = waf  + 0.5 clearance  (flat-to-flat)
          axial depth   (Z) = 2·R_circ + 0.5           (vertex-to-vertex)

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
        R_circ  = waf / math.sqrt(3)   # hex circumradius (centre→vertex)
        # Pocket circumradius: add 0.2 mm clearance so nut slides in freely.
        # The pocket cross-section is hexagonal (matches nut profile exactly),
        # so the bottom of the pocket has angled faces — no flat floor.
        R_pkt   = R_circ + 0.2
        pkt_z   = 2.0 * R_pkt         # tip-to-tip pocket depth (vertex-to-vertex)

        # Auto-raise hub height so nut fits fully inside the hub boss
        min_hub_h = pkt_z
        if hub_height_mm < min_hub_h:
            hub_height_mm = min_hub_h

        # Require 2 × t_nut of wall outside the nut
        min_hub_r = R_bore + 3.0 * t_nut
        need_oblong = R_hub < min_hub_r
        eff_r = max(R_hub, min_hub_r)

        step = math.pi                 # 180° between captured-nut screws
    else:
        waf = t_nut = R_circ = R_pkt = pkt_z = 0.0
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

    # ── Bore ──────────────────────────────────────────────────────────────────
    if R_bore > 0.5 and body.is_watertight:
        extra    = 0.5
        bore_cyl = trimesh.creation.cylinder(
            radius=R_bore, height=total_height + extra * 2,
            sections=_BORE_SECTIONS)
        bore_cyl.apply_translation([0.0, 0.0, total_height / 2.0])
        bore_cyl.fix_normals()
        body = trimesh.boolean.difference([body, bore_cyl], engine='manifold')

    # ── Set-screw holes + nut pockets ─────────────────────────────────────────
    if do_screws and body.is_watertight:
        R_screw = screw_dia_mm / 2.0

        if captured_nut:
            # Screw sits at Z centre of the nut pocket
            z_screw = hub_top - pkt_z / 2.0

            # One-sided hole: enters from hub OD, stops at bore.
            hole_len = eff_r - R_bore + 1.0
            hole_cx  = (eff_r + R_bore) / 2.0

            # Hex pocket cross-section in the XY plane — vertices at ±X,
            # flat faces at ±Y.  After rotation_matrix(π/2, Y-axis) the
            # mapping is: old X → new -Z, old Y → new Y, old Z → new X.
            # Result: vertices end up at ±Z (angled bottom, open top),
            # flat faces at ±Y (guide walls that locate the nut as it
            # slides in from the hub top face).
            hex_xy = [(R_pkt * math.cos(k * math.pi / 3),
                       R_pkt * math.sin(k * math.pi / 3))
                      for k in range(6)]
            hex_poly = shapely_orient(ShapelyPolygon(hex_xy), sign=1.0)

            # Radial depth: nut thickness + 0.5 mm clearance.
            # After rotation the extrusion runs along X from 0 to pkt_x,
            # so translate by R_bore to place the inner face at the bore wall.
            pkt_x = t_nut + 0.5

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

            # ── Hex nut pocket (drops in from hub top) ────────────────────────
            # Cross-section is the hex nut profile (in YZ), extruded along X
            # (radially) so the pocket bottom has the angled vertex shape of
            # the nut — no flat floor.
            if captured_nut and body.is_watertight:
                pocket = trimesh.creation.extrude_polygon(hex_poly, pkt_x)
                # extrude_polygon extrudes along Z; rotate so it runs along X
                pocket.apply_transform(
                    trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
                # Now pocket runs from x=0 to x=pkt_x; shift so inner face
                # is at bore_r and outer face at bore_r + pkt_x.
                # X: [0, pkt_x] + R_bore → inner face at bore_r, outer at bore_r+pkt_x
                # Z: [-R_pkt, R_pkt] + (hub_top-R_pkt) → [hub_top-pkt_z, hub_top]
                #    top of pocket (open end) is flush with the hub top face
                pocket.apply_translation([R_bore, 0.0, hub_top - R_pkt])
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

    # ── 2D profile → extruded solid ──────────────────────────────────────────
    poly = ShapelyPolygon(outline)
    poly = shapely_orient(poly, sign=1.0)   # force CCW (positive area orientation)
    body = trimesh.creation.extrude_polygon(poly, belt_height_mm)
    body.fix_normals()

    result = _add_hub_and_bore(body, belt_height_mm, bore_mm,
                               hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                               captured_nut)
    return result.export(file_type='stl')


def _rot2d(pts, angle):
    """Rotate a list of (x, y) points by `angle` radians (compass CW convention)."""
    if abs(angle) < 1e-9:
        return pts
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c + y * s, -x * s + y * c) for x, y in pts]


def _build_pulley_mesh(family, pitch, num_teeth, bore_mm, belt_height_mm,
                       clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0,
                       phase=0.0, hub_od_mm=0.0, hub_height_mm=0.0,
                       screw_dia_mm=0.0, screw_count=0, captured_nut=False):
    """
    Build a single watertight pulley trimesh solid centred at the origin in X-Y,
    extruded from z=0 to z=belt_height_mm, with optional hub boss on top and
    bore subtracted through the full height.
    `phase` (radians) rotates the tooth pattern to mesh with the belt.
    Returns a trimesh.Trimesh.
    """
    outline = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )[0]
    outline = _rot2d(outline, phase)
    poly = ShapelyPolygon(outline)
    poly = shapely_orient(poly, sign=1.0)
    body = trimesh.creation.extrude_polygon(poly, belt_height_mm)
    body.fix_normals()

    return _add_hub_and_bore(body, belt_height_mm, bore_mm,
                             hub_od_mm, hub_height_mm, screw_dia_mm, screw_count,
                             captured_nut)


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
                            captured_nut=captured_nut1)
    p1.apply_translation([cx1, 0.0, 0.0])

    p2 = _build_pulley_mesh(family, pitch, num_teeth2, bore_mm2, belt_height_mm,
                            clearance_mm2, backlash_mm2, print_extra_mm2,
                            phase=phi_right,
                            hub_od_mm=hub_od_mm2, hub_height_mm=hub_height_mm2,
                            screw_dia_mm=screw_dia_mm2, screw_count=screw_count2,
                            captured_nut=captured_nut2)
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
) -> bytes:
    """
    Same as generate_pulley_stl but centres the mesh at the origin so
    Three.js auto-fits it nicely.
    """
    stl_bytes = generate_pulley_stl(
        family, pitch, num_teeth, bore_mm, belt_height_mm,
        clearance_mm, backlash_mm, print_extra_mm,
        hub_od_mm, hub_height_mm, screw_dia_mm, screw_count, captured_nut,
    )
    mesh = trimesh.load(io.BytesIO(stl_bytes), file_type='stl')
    mesh.apply_translation(-mesh.centroid)
    return mesh.export(file_type='stl')
