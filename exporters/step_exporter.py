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
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry.polygon import orient as shapely_orient

from geometry.pulley_geometry import (
    generate_profile_groove, _build_groove_points,
    wrap_groove_to_pulley, PULLEY_SPECS, PROFILE_KEY_PREFIX,
    build_two_pulley_belt, BELT_FAMILIES,
)

# ── Arc sample resolution ─────────────────────────────────────────────────────
_ARC_STEP_MM = 0.5        # target chord length on OD arc samples (mm)
_BORE_SECTIONS = 64       # facets on bore cylinder


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


def generate_pulley_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    backlash_mm: float = 0.0,
    print_extra_mm: float = 0.0,
) -> bytes:
    """
    Return binary STL bytes of an extruded timing pulley solid.

    The pulley body is centred at the origin in X-Y; it is extruded from
    z = 0 to z = belt_height_mm.  The bore hole is centred on the Z axis.
    """
    outline, R_OD, spec = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )

    # ── 2D profile → extruded solid ──────────────────────────────────────────
    poly = ShapelyPolygon(outline)
    poly = shapely_orient(poly, sign=1.0)   # force CCW (positive area orientation)
    body = trimesh.creation.extrude_polygon(poly, belt_height_mm)
    body.fix_normals()

    # ── Bore subtraction ─────────────────────────────────────────────────────
    R_bore = bore_mm / 2.0
    if R_bore > 0.5 and body.is_watertight:
        # Make bore cylinder slightly taller so cap faces clear the body.
        extra  = 0.5
        bore   = trimesh.creation.cylinder(
            radius   = R_bore,
            height   = belt_height_mm + extra * 2,
            sections = _BORE_SECTIONS,
        )
        bore.apply_translation([0.0, 0.0, belt_height_mm / 2.0])
        bore.fix_normals()
        result = trimesh.boolean.difference([body, bore], engine='manifold')
    else:
        result = body

    return result.export(file_type='stl')


def _rot2d(pts, angle):
    """Rotate a list of (x, y) points by `angle` radians (compass CW convention)."""
    if abs(angle) < 1e-9:
        return pts
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c + y * s, -x * s + y * c) for x, y in pts]


def _build_pulley_mesh(family, pitch, num_teeth, bore_mm, belt_height_mm,
                       clearance_mm=0.0, backlash_mm=0.0, print_extra_mm=0.0,
                       phase=0.0):
    """
    Build a single watertight pulley trimesh solid centred at the origin in X-Y,
    extruded from z=0 to z=belt_height_mm, with bore subtracted.
    `phase` (radians) rotates the tooth pattern to mesh with the belt.
    Returns a trimesh.Trimesh.
    """
    outline, _R_OD, _spec = _build_outline_points(
        family, pitch, num_teeth, clearance_mm, backlash_mm, print_extra_mm
    )
    outline = _rot2d(outline, phase)
    poly = ShapelyPolygon(outline)
    poly = shapely_orient(poly, sign=1.0)
    body = trimesh.creation.extrude_polygon(poly, belt_height_mm)
    body.fix_normals()

    R_bore = bore_mm / 2.0
    if R_bore > 0.5 and body.is_watertight:
        bore_cyl = trimesh.creation.cylinder(
            radius=R_bore, height=belt_height_mm + 1.0, sections=_BORE_SECTIONS
        )
        bore_cyl.apply_translation([0.0, 0.0, belt_height_mm / 2.0])
        bore_cyl.fix_normals()
        body = trimesh.boolean.difference([body, bore_cyl], engine='manifold')

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
                            phase=phi_left)
    p1.apply_translation([cx1, 0.0, 0.0])

    p2 = _build_pulley_mesh(family, pitch, num_teeth2, bore_mm2, belt_height_mm,
                            clearance_mm2, backlash_mm2, print_extra_mm2,
                            phase=phi_right)
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
) -> bytes:
    """
    Same as generate_pulley_stl but centres the mesh at the origin so
    Three.js auto-fits it nicely.
    """
    stl_bytes = generate_pulley_stl(
        family, pitch, num_teeth, bore_mm, belt_height_mm,
        clearance_mm, backlash_mm, print_extra_mm,
    )
    mesh = trimesh.load(io.BytesIO(stl_bytes), file_type='stl')
    mesh.apply_translation(-mesh.centroid)
    return mesh.export(file_type='stl')
