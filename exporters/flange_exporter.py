"""
flange_exporter.py
------------------
Generates STL bytes for timing-pulley flanges (3D-print and metal types)
by revolving 2D cross-section profiles around the Z axis.

3D-print flanges
    • Bottom: integrated solid attached to pulley bottom face.
    • Top:    separate solid displayed / downloaded independently.

Metal flanges
    • Top and bottom are always separate thin-shell solids (sheet metal).

Both types use the same _revolve_polygon() core; only the profiles differ.
"""
import math
from typing import List, Tuple

import numpy as np
import trimesh

from geometry.pulley_geometry import (
    PULLEY_SPECS, PROFILE_KEY_PREFIX,
    getOuterDiameter, getPitchDiameter,
)
from geometry.flange_geometry import (
    profile_3dprint,
    profile_metal,
    flange_inner_r_3dprint,
    flange_inner_r_metal_top,
    flange_inner_r_metal_bottom,
)


# ---------------------------------------------------------------------------
# Core: solid of revolution from a closed 2D polygon in (r, Z)
# ---------------------------------------------------------------------------

def _revolve_polygon(
    profile: List[Tuple[float, float]],
    sections: int = 64,
) -> trimesh.Trimesh:
    """Revolve a closed 2D polygon in the r-Z plane 360° around the Z axis.

    Parameters
    ----------
    profile  : list of (r, z) tuples — closed polygon, last ≠ first.
    sections : number of angular slices.

    Returns a watertight trimesh.Trimesh solid.
    """
    profile = np.asarray(profile, dtype=float)
    n_pts = len(profile)
    n_sec = sections

    angles = np.linspace(0.0, 2.0 * math.pi, n_sec, endpoint=False)

    # ── Vertices: shape (n_sec * n_pts, 3)
    verts = []
    for a in angles:
        ca, sa = math.cos(a), math.sin(a)
        for r, z in profile:
            verts.append([r * ca, r * sa, z])
    verts = np.array(verts, dtype=float)

    def vid(s, p):
        return s * n_pts + p

    # ── Lateral faces (quads split into 2 triangles per polygon edge per slice)
    faces = []
    for s in range(n_sec):
        sn = (s + 1) % n_sec
        for p in range(n_pts):
            pn = (p + 1) % n_pts
            v0, v1 = vid(s, p), vid(s, pn)
            v2, v3 = vid(sn, p), vid(sn, pn)
            faces.append([v0, v2, v1])
            faces.append([v1, v2, v3])

    mesh = trimesh.Trimesh(
        vertices=verts,
        faces=np.array(faces, dtype=np.int32),
        process=False,
    )
    mesh.fix_normals()
    trimesh.repair.fill_holes(mesh)
    return mesh


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------

def _profile_key(family: str, pitch: str) -> str:
    return PROFILE_KEY_PREFIX.get(family, '') + pitch


def _pulley_radii(family: str, pitch: str, num_teeth: int,
                  clearance_mm: float = 0.0, print_extra_mm: float = 0.0):
    """Return (R_tooth_OD, R_groove_bottom, tooth_ht) for the given pulley."""
    key  = _profile_key(family, pitch)
    spec = PULLEY_SPECS[key]
    pld  = spec.get('pitch_line_diff', spec.get('pitchLineDiff', 0.0))
    th   = spec['tooth_ht']
    od   = getOuterDiameter(num_teeth, spec['pitch'], pld + print_extra_mm - clearance_mm)
    R_OD = od / 2.0
    return R_OD, R_OD - th, th


# ---------------------------------------------------------------------------
# 3D-print flange STL
# ---------------------------------------------------------------------------

def generate_3dprint_flange_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    # Flange params
    flange_angle_deg: float = 15.0,
    rim_radius_mm: float = 3.0,
    flange_height_mm: float = 1.5,
    which: str = 'top',            # 'top' | 'bottom' | 'both'
    # Hub / spoke params for inner-radius determination
    hub_od_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    sections: int = 64,
) -> bytes:
    """Return binary STL of one or both 3D-print flanges.

    ``which`` controls what is returned:
      'top'    → separate top flange solid (positioned at Z=belt_height_mm,
                 floating above the pulley for print-in-place assembly).
      'bottom' → bottom flange solid (integrated at Z=0, growing downward).
      'both'   → union of both (for preview purposes).
    """
    R_OD, _R_gb, _th = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)
    r_inner = flange_inner_r_3dprint(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                     r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)

    rim_radius_mm  = max(0.5, rim_radius_mm)
    flange_height_mm = max(0.1, flange_height_mm)
    angle_deg = max(8.0, min(25.0, flange_angle_deg))

    prof = profile_3dprint(r_inner, R_OD, rim_radius_mm, angle_deg, flange_height_mm)

    meshes = []

    if which in ('top', 'both'):
        top_mesh = _revolve_polygon(prof, sections)
        top_mesh.apply_translation([0.0, 0.0, belt_height_mm])
        meshes.append(top_mesh)

    if which in ('bottom', 'both'):
        # Mirror profile in Z: negate z, then translate to sit below Z=0
        bot_prof = [(r, -z) for r, z in prof]
        bot_mesh = _revolve_polygon(bot_prof, sections)
        # No translation needed: profile already goes downward from Z=0
        meshes.append(bot_mesh)

    if len(meshes) == 1:
        result = meshes[0]
    else:
        result = trimesh.boolean.union(meshes, engine='manifold')

    return result.export(file_type='stl')


# ---------------------------------------------------------------------------
# Metal flange STL
# ---------------------------------------------------------------------------

def generate_metal_flange_stl(
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    # Flange params
    flange_angle_deg: float = 15.0,
    rim_radius_mm: float = 3.0,
    plate_height_mm: float = 1.0,
    bend_radius_mm: float = 0.0,   # 0 → default to 1.5 * plate_height
    which: str = 'top',            # 'top' | 'bottom'
    # Hub / spoke params
    hub_od_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    sections: int = 64,
) -> bytes:
    """Return binary STL of a metal flange plate.

    ``which='top'`` produces the upper plate (positioned above belt_height_mm).
    ``which='bottom'`` produces the lower plate (positioned below Z=0).
    """
    R_OD, _R_gb, _th = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)

    if bend_radius_mm <= 0.0:
        bend_radius_mm = 1.5 * plate_height_mm

    angle_deg  = max(8.0, min(25.0, flange_angle_deg))
    plate_t    = max(0.3, plate_height_mm)
    rim_mm     = max(0.5, rim_radius_mm)

    # Clamp bend_radius to < rim_radius (can't exceed the reach of the flange)
    bend_mm = min(bend_radius_mm, rim_mm * 0.8)

    if which == 'top':
        r_inner = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                           r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
    else:
        r_inner = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                              r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)

    prof = profile_metal(r_inner, R_OD, rim_mm, angle_deg, plate_t, bend_mm)

    if which == 'top':
        mesh = _revolve_polygon(prof, sections)
        # Bottom face of flat section (Z=0 in profile) sits at belt_height_mm
        mesh.apply_translation([0.0, 0.0, belt_height_mm])
    else:
        # Negate Z so the bend faces downward (away from pulley).
        # After negation: Z=0 is the contact face resting on the pulley bottom;
        # the plate and bend extend toward −Z.
        prof_flipped = [(r, -z) for r, z in prof]
        mesh = _revolve_polygon(prof_flipped, sections)
        # No translation needed: contact face is already at Z=0.

    return mesh.export(file_type='stl')


# ---------------------------------------------------------------------------
# Nub / socket geometry helpers (3D-print flange, top_separate mode only)
# ---------------------------------------------------------------------------

def _nub_circle_radius(r_tooth_OD: float, tooth_ht: float, nub_dia_mm: float) -> float:
    """Radius of the circle on which nub/socket centers sit.

    Rule: nub outer edge at R_groove_bottom − min(tooth_ht, 3 mm), so:
        r_nub_centre = R_groove_bottom − min(tooth_ht, 3 mm) − nub_dia / 2

    Any nub material that falls inside the bore/hub/spoke boundary is clipped
    naturally by the bore geometry; no adjustment to the centre is needed.
    """
    r_groove_bottom = r_tooth_OD - tooth_ht
    margin = min(tooth_ht, 3.0)
    nub_outer_edge = r_groove_bottom - margin
    return nub_outer_edge - nub_dia_mm / 2.0


def _nub_xy(nub_count: int, r_nub_circle: float) -> list:
    """Return [(x, y), ...] for each nub centre."""
    return [
        (r_nub_circle * math.cos(2.0 * math.pi * i / nub_count),
         r_nub_circle * math.sin(2.0 * math.pi * i / nub_count))
        for i in range(nub_count)
    ]


def build_socket_meshes(
    fp: dict,
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    sections: int = 16,
) -> list:
    """Return trimesh cylinder meshes for the socket holes in the pulley top face.

    Subtract these from the pulley body with trimesh.boolean.difference to create
    the cavities that receive the top-flange nubs.

    Returns [] when nubs are not active or conditions aren't met.
    """
    if not (fp.get('nubs_enabled') and fp.get('flange_3dprint') and fp.get('top_separate')):
        return []

    R_OD, _R_gb, tooth_ht = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)
    nub_dia = fp['nub_dia_mm']
    # Socket depth = full nub_h (not reduced by allowance); allowance only shrinks the pin
    nub_h   = fp['nub_height_mm']
    r_nub = _nub_circle_radius(R_OD, tooth_ht, nub_dia)
    # Socket is nominal nub_dia; pin is undersized by allowance for clearance fit
    r_socket = nub_dia / 2.0

    # Clip sockets at the hub boss boundary so they don't cut into bore material
    r_spoke_inner = (spoke_hub_od_mm / 2.0
                     if (spokes_enabled and spoke_hub_od_mm > 0.0)
                     else 0.0)

    # The cutting cylinder must extend clearly above AND below the intended
    # pocket so manifold has no topological ambiguity.
    # • Top of cut  = belt_height_mm + 20  (well above the belt face)
    # • Bottom of cut = belt_height_mm - nub_h  (socket floor)
    # We use a tall upward extension so the cylinder clearly penetrates the
    # top face from outside — this forces manifold to open the face rather
    # than cap it.
    cut_bottom = belt_height_mm - nub_h
    cut_top    = belt_height_mm + 20.0    # 20 mm above belt face
    cut_h      = cut_top - cut_bottom
    cut_z      = (cut_top + cut_bottom) / 2.0

    meshes = []
    for x, y in _nub_xy(fp['nub_count'], r_nub):
        cyl = trimesh.creation.cylinder(radius=r_socket, height=cut_h, sections=sections)
        cyl.apply_translation([x, y, cut_z])
        # Clip at spoke inner rim (hub boss boundary) if socket extends inside it
        if r_spoke_inner > 0.0 and (r_nub - r_socket) < r_spoke_inner:
            inner_clip = trimesh.creation.cylinder(
                radius=r_spoke_inner, height=cut_h + 2.0, sections=64)
            inner_clip.apply_translation([0.0, 0.0, cut_z])
            try:
                cyl = trimesh.boolean.difference([cyl, inner_clip], engine='manifold')
            except Exception:
                pass
        meshes.append(cyl)
    return meshes


# ---------------------------------------------------------------------------
# Preview helper — returns trimesh objects (not bytes) for 3D viewer
# ---------------------------------------------------------------------------

def build_flange_meshes(
    fp: dict,
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
    hub_od_mm: float = 0.0,
    hub_height_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    sections: int = 48,
) -> list:
    """Return [top_mesh, bottom_mesh] trimesh objects for the 3D preview.

    Uses fewer sections than the download STL (48 vs 64) to keep the preview
    lightweight.  Returns an empty list on any geometry error.

    When fp['top_separate'] is True and fp['flange_3dprint'] is True, the top
    flange is floated 10 mm above the hub top so the user can see the nubs.

    ``fp`` must be the dict produced by ``_parse_flange_params()`` in app.py.
    """
    if not fp:
        return []
    try:
        R_OD, _R_gb, tooth_ht = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)
        angle = max(8.0, min(25.0, fp['flange_angle_deg']))
        rim_r = max(0.5, fp['rim_radius_mm'])
        meshes = []

        if fp['flange_3dprint']:
            r_inner = flange_inner_r_3dprint(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                             r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            f_h = max(0.1, fp['flange_height_mm'])
            prof = profile_3dprint(r_inner, R_OD, rim_r, angle, f_h)

            top = _revolve_polygon(prof, sections)

            # Add nubs to the bottom face of the top flange (in local coords, before translation)
            # Nubs only apply when: 3D print + top is separate + nubs_enabled
            if fp.get('top_separate', False) and fp.get('nubs_enabled'):
                nub_dia_mm = fp['nub_dia_mm']
                # Clamp nub height: min 1 mm, max 1/3 of belt height (per spec)
                nub_h      = max(1.0, min(fp['nub_height_mm'], belt_height_mm / 3.0))
                nub_allow  = fp['nub_allowance_mm']
                r_nub = _nub_circle_radius(R_OD, tooth_ht, nub_dia_mm)
                r_pin = max(0.1, (nub_dia_mm - nub_allow) / 2.0)
                # Physical nub is shorter than socket by the allowance height component
                nub_pin_h = max(0.1, nub_h - nub_allow)
                # Radial boundaries nubs must not breach:
                #   r_spoke_inner — hub boss boundary (inner spoke rim)
                #   r_spoke_outer — rim-to-spoke boundary (outer spoke rim)
                r_spoke_inner = (spoke_hub_od_mm / 2.0
                                 if (spokes_enabled and spoke_hub_od_mm > 0.0)
                                 else 0.0)
                r_spoke_outer = (R_OD - rim_depth_mm
                                 if (spokes_enabled and rim_depth_mm > 0.0)
                                 else 0.0)
                nub_cyls = []
                for x, y in _nub_xy(fp['nub_count'], r_nub):
                    cyl = trimesh.creation.cylinder(radius=r_pin, height=nub_pin_h, sections=16)
                    # Nubs protrude down from Z=0 (flange bottom face)
                    cyl.apply_translation([x, y, -nub_pin_h / 2.0])
                    nub_cyls.append(cyl)
                if nub_cyls:
                    try:
                        top = trimesh.boolean.union([top] + nub_cyls, engine='manifold')
                        clip_h = nub_pin_h + 2.0
                        # Clip at spoke inner rim (hub boss surface)
                        if r_spoke_inner > 0.0 and (r_nub - r_pin) < r_spoke_inner:
                            clip_cyl = trimesh.creation.cylinder(
                                radius=r_spoke_inner, height=clip_h, sections=64)
                            clip_cyl.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                            top = trimesh.boolean.difference(
                                [top, clip_cyl], engine='manifold')
                        # Clip at spoke outer rim (where spokes meet rim ring)
                        if r_spoke_outer > 0.0 and (r_nub - r_pin) < r_spoke_outer:
                            clip_cyl = trimesh.creation.cylinder(
                                radius=r_spoke_outer, height=clip_h, sections=64)
                            clip_cyl.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                            top = trimesh.boolean.difference(
                                [top, clip_cyl], engine='manifold')
                    except Exception:
                        pass  # fall back to flange without nubs

            if fp.get('top_separate', False):
                # Float 10 mm above the hub top to reveal the nub face
                top_z = belt_height_mm + hub_height_mm + 10.0
            else:
                top_z = belt_height_mm
            top.apply_translation([0.0, 0.0, top_z])
            meshes.append(top)

            bot_prof = [(r, -z) for r, z in prof]
            bot = _revolve_polygon(bot_prof, sections)
            meshes.append(bot)
        else:
            plate_t = max(0.3, fp['plate_height_mm'])
            bend_r  = fp['bend_radius_mm'] if fp['bend_radius_mm'] > 0.0 else 1.5 * plate_t
            bend_r  = min(bend_r, rim_r * 0.8)

            r_inner_top = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                                   r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof_top = profile_metal(r_inner_top, R_OD, rim_r, angle, plate_t, bend_r)
            top = _revolve_polygon(prof_top, sections)
            top.apply_translation([0.0, 0.0, belt_height_mm])
            meshes.append(top)

            r_inner_bot = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                      r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof_bot = profile_metal(r_inner_bot, R_OD, rim_r, angle, plate_t, bend_r)
            prof_bot = [(r, -z) for r, z in prof_bot]  # flip: bend faces down, contact face at Z=0
            bot = _revolve_polygon(prof_bot, sections)
            meshes.append(bot)

        return meshes
    except Exception:
        return []
