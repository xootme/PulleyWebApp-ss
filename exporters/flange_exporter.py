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
    flange_inner_r_3dprint_bottom,
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
# Bore-profile subtraction helper
# ---------------------------------------------------------------------------

def _subtract_bore_profile(mesh, bore_mm, flat_depth_mm=0.0, keyway_w_mm=0.0, keyway_h_mm=0.0):
    """Subtract the bore profile (circle + D-flat/keyway) through a flange mesh.

    Uses _build_bore_2d from step_exporter as the single source of truth so the
    flange bore profile is guaranteed to match the pulley body's bore profile.
    """
    from exporters.step_exporter import _build_bore_2d
    bore_2d = _build_bore_2d(bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
    if bore_2d is None:
        return mesh
    try:
        cutter = trimesh.creation.extrude_polygon(bore_2d, 510.0)
        cutter.apply_translation([0.0, 0.0, -255.0])
        result = trimesh.boolean.difference([mesh, cutter], engine='manifold')
        if result is not None and len(result.vertices) > 0:
            return result
    except Exception:
        pass
    return mesh


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
    # Nub params (top flange only; ignored for bottom)
    nubs_enabled: bool = False,
    nub_count: int = 4,
    nub_dia_mm: float = 3.0,
    nub_height_mm: float = 2.0,
    nub_allowance_mm: float = 0.2,
    # Bore profile
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """Return binary STL of one or both 3D-print flanges.

    ``which`` controls what is returned:
      'top'    → separate top flange solid (positioned at Z=belt_height_mm).
      'bottom' → bottom flange solid (integrated at Z=0, growing downward).
      'both'   → union of both (for preview purposes).

    When ``nubs_enabled`` is True and ``which`` is 'top', nub pins are unioned
    onto the bottom face of the top flange.
    """
    R_OD, _R_gb, tooth_ht = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)
    # Use rim boundary (R_OD - tooth_ht) as tooth reference when spokes enabled
    R_tr = R_OD - tooth_ht
    r_tooth_ref = R_tr if spokes_enabled else R_OD

    r_inner_bot = flange_inner_r_3dprint_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                r_tooth_OD=r_tooth_ref, rim_depth_mm=rim_depth_mm)

    # Flange ID must be at the rim boundary (spoke outer edge) when spokes enabled
    if spokes_enabled and rim_depth_mm > 0.0:
        r_inner = R_tr - rim_depth_mm
    else:
        r_inner = flange_inner_r_3dprint(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                         r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)

    rim_radius_mm    = max(0.5, rim_radius_mm)
    flange_height_mm = max(0.1, flange_height_mm)
    angle_deg        = max(8.0, min(25.0, flange_angle_deg))

    # Adaptive sections: target ~3 mm chord on the outer radius; cap at caller's sections.
    sections = max(32, min(sections, round(2 * math.pi * R_OD / 3.0)))

    prof     = profile_3dprint(r_inner,     r_tooth_ref, rim_radius_mm, angle_deg, flange_height_mm)
    prof_bot = profile_3dprint(r_inner_bot, r_tooth_ref, rim_radius_mm, angle_deg, flange_height_mm)

    meshes = []

    if which in ('top', 'both'):
        top_mesh = _revolve_polygon(prof, sections)

        if nubs_enabled:
            nub_h   = max(1.0, min(nub_height_mm, belt_height_mm / 3.0))
            r_pin   = max(0.1, (nub_dia_mm - nub_allowance_mm) / 2.0)
            nub_pin_h = max(0.1, nub_h - nub_allowance_mm)
            r_nub   = _nub_circle_radius(R_OD, tooth_ht, nub_dia_mm)
            r_spoke_inner = spoke_hub_od_mm / 2.0 if (spokes_enabled and spoke_hub_od_mm > 0.0) else 0.0
            # Use rim boundary (R_OD - tooth_ht - rim_depth), not R_OD - rim_depth
            r_spoke_outer = ((R_OD - tooth_ht) - rim_depth_mm) if (spokes_enabled and rim_depth_mm > 0.0) else 0.0
            nub_cyls = []
            # Embed nubs 0.5 mm into the flange body so the Boolean union has no
            # coplanar face at Z=0, which would produce degenerate spike triangles.
            _NUB_EMBED = 0.5
            for x, y in _nub_xy(nub_count, r_nub):
                cyl_h = nub_pin_h + _NUB_EMBED
                cyl = trimesh.creation.cylinder(radius=r_pin, height=cyl_h, sections=16)
                # Span Z = -nub_pin_h to Z = +_NUB_EMBED
                cyl.apply_translation([x, y, (_NUB_EMBED - nub_pin_h) / 2.0])
                nub_cyls.append(cyl)
            if nub_cyls:
                try:
                    top_mesh = trimesh.boolean.union([top_mesh] + nub_cyls, engine='manifold')
                    clip_h = nub_pin_h + 2.0
                    # Clip nubs at flange ID — use spoke rim boundary if spokes active,
                    # else r_inner (the plain flange ID).  Always applied so nubs never
                    # protrude through the inner face of the flange ring.
                    clip_id = r_spoke_outer if r_spoke_outer > 0.0 else r_inner
                    if r_nub - r_pin < clip_id:
                        clip = trimesh.creation.cylinder(radius=clip_id, height=clip_h, sections=64)
                        clip.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                        top_mesh = trimesh.boolean.difference([top_mesh, clip], engine='manifold')
                    # Clip at spoke inner rim (hub boss surface) only if nubs extend into spoke hub
                    if r_spoke_inner > 0.0 and (r_nub - r_pin) <= r_spoke_inner:
                        clip = trimesh.creation.cylinder(radius=r_spoke_inner, height=clip_h, sections=64)
                        clip.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                        top_mesh = trimesh.boolean.difference([top_mesh, clip], engine='manifold')
                except Exception:
                    pass  # fall back to flange without nubs

        top_mesh.apply_translation([0.0, 0.0, belt_height_mm])
        meshes.append(top_mesh)

    if which in ('bottom', 'both'):
        bot_mesh = _revolve_polygon([(r, -z) for r, z in prof_bot], sections)
        bot_mesh = _subtract_bore_profile(bot_mesh, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)  # bottom only
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
    which: str = 'top',            # 'top' | 'bottom' | 'both'
    # Hub / spoke params
    hub_od_mm: float = 0.0,
    spokes_enabled: bool = False,
    spoke_hub_od_mm: float = 0.0,
    rim_depth_mm: float = 0.0,
    sections: int = 64,
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
) -> bytes:
    """Return binary STL of a metal flange plate.

    ``which='top'`` produces the upper plate (positioned above belt_height_mm).
    ``which='bottom'`` produces the lower plate (positioned below Z=0).
    ``which='both'`` produces both plates concatenated (no union — they don't intersect).
    """
    R_OD, _R_gb, _th = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)

    if bend_radius_mm <= 0.0:
        bend_radius_mm = 1.5 * plate_height_mm

    angle_deg  = max(8.0, min(25.0, flange_angle_deg))
    plate_t    = max(0.3, plate_height_mm)
    rim_mm     = max(0.5, rim_radius_mm)

    # Clamp bend_radius to < rim_radius (can't exceed the reach of the flange)
    bend_mm = min(bend_radius_mm, rim_mm * 0.8)

    # Adaptive sections: target ~3 mm chord on the outer radius; cap at caller's sections.
    sections = max(32, min(sections, round(2 * math.pi * R_OD / 3.0)))

    if which == 'both':
        r_inner_top = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                               r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        prof_top = profile_metal(r_inner_top, R_OD, rim_mm, angle_deg, plate_t, bend_mm)
        top_mesh = _revolve_polygon(prof_top, sections)
        top_mesh.apply_translation([0.0, 0.0, belt_height_mm])

        r_inner_bot = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                  r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
        prof_bot = profile_metal(r_inner_bot, R_OD, rim_mm, angle_deg, plate_t, bend_mm)
        prof_bot_flipped = [(r, -z) for r, z in prof_bot]
        bot_mesh = _revolve_polygon(prof_bot_flipped, sections)
        bot_mesh = _subtract_bore_profile(bot_mesh, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)  # bottom only

        result = trimesh.util.concatenate([top_mesh, bot_mesh])
        return result.export(file_type='stl')

    if which == 'top':
        r_inner = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                           r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
    else:
        r_inner = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                              r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)

    prof = profile_metal(r_inner, R_OD, rim_mm, angle_deg, plate_t, bend_mm)

    if which == 'top':
        mesh = _revolve_polygon(prof, sections)
        mesh.apply_translation([0.0, 0.0, belt_height_mm])
    else:
        prof_flipped = [(r, -z) for r, z in prof]
        mesh = _revolve_polygon(prof_flipped, sections)
        mesh = _subtract_bore_profile(mesh, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)  # bottom only

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

    # Build all socket cylinders first (no per-cylinder booleans yet)
    cyls = []
    for x, y in _nub_xy(fp['nub_count'], r_nub):
        cyl = trimesh.creation.cylinder(radius=r_socket, height=cut_h, sections=sections)
        cyl.apply_translation([x, y, cut_z])
        cyls.append(cyl)

    # Clip at spoke inner rim: the clip cylinder is the same for every socket,
    # so union all sockets first then do one difference — O(2) instead of O(N).
    needs_clip = r_spoke_inner > 0.0 and (r_nub - r_socket) < r_spoke_inner
    if needs_clip and cyls:
        try:
            inner_clip = trimesh.creation.cylinder(
                radius=r_spoke_inner, height=cut_h + 2.0, sections=64)
            inner_clip.apply_translation([0.0, 0.0, cut_z])
            sockets_union = (trimesh.boolean.union(cyls, engine='manifold')
                             if len(cyls) > 1 else cyls[0])
            return [trimesh.boolean.difference([sockets_union, inner_clip], engine='manifold')]
        except Exception:
            pass  # fall through and return unclipped cylinders
    return cyls


# ---------------------------------------------------------------------------
# Print support ribs (integrated top-flange mode only)
# ---------------------------------------------------------------------------

def _build_buttress_mesh(
    r_ti: float, z_ti: float,    # inner tip: (R_OD, flat_underside - air_gap)
    r_to: float, z_to: float,    # outer tip: (r_outer, angled_underside - air_gap)
    r_tube: float,               # tube inner radius (r_outer + 1mm); outer face is flush here
    z_bed: float,                # bed level (tube bottom)
    hw_tip: float, hw_base: float,
    theta: float,
) -> trimesh.Trimesh:
    """Flying-buttress rib whose outer vertical face is flush with the support tube.

    Cross-section in r-Z (quadrilateral A-B-C-D):
      A = (r_ti,   z_ti)   inner tip (under flat flange, air gap included)
      B = (r_to,   z_to)   outer tip (under angled flange, air gap included)
      C = (r_tube, z_to)   tube top  (1mm step outward from B at the same height)
      D = (r_tube, z_bed)  tube base (bed level)

    The outer face C-D is vertical and co-planar with the tube inner face.
    The inner face A-D is the printable slope (~25 deg from vertical for typical geometry).
    """
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    def tr(r, tang, z):
        return [r * cos_t - tang * sin_t, r * sin_t + tang * cos_t, z]

    v = [
        tr(r_ti,   -hw_tip,  z_ti),   # 0 A-left  inner tip
        tr(r_ti,   +hw_tip,  z_ti),   # 1 A-right
        tr(r_to,   -hw_tip,  z_to),   # 2 B-left  outer tip
        tr(r_to,   +hw_tip,  z_to),   # 3 B-right
        tr(r_tube, -hw_base, z_to),   # 4 C-left  tube top (same z as B)
        tr(r_tube, +hw_base, z_to),   # 5 C-right
        tr(r_tube, -hw_base, z_bed),  # 6 D-left  tube base / bed
        tr(r_tube, +hw_base, z_bed),  # 7 D-right
    ]
    f = [
        [0, 2, 1], [1, 2, 3],  # top face A-B (air gap side, under flange)
        [2, 4, 3], [3, 4, 5],  # step face B-C (1mm horizontal shelf to tube)
        [4, 6, 5], [5, 6, 7],  # outer vertical face C-D (tube inner surface)
        [0, 1, 6], [1, 7, 6],  # inner face A-D (printable slope)
        [0, 4, 2], [0, 6, 4],  # left tang side
        [1, 3, 5], [1, 5, 7],  # right tang side
    ]
    m = trimesh.Trimesh(vertices=v, faces=f, process=False)
    m.fix_normals()
    return m


def build_support_ribs(
    fp: dict,
    family: str,
    pitch: str,
    num_teeth: int,
    bore_mm: float,
    belt_height_mm: float,
    clearance_mm: float = 0.0,
    print_extra_mm: float = 0.0,
) -> list:
    """Return trimesh objects for flying-buttress supports + bed tube.

    Geometry
    --------
    A thin-walled vertical tube sits on the bed 1mm outside the lower flange
    outer edge (r = R_OD + rim_r + 1mm).  The tube extends from the bed up to
    the height of the upper flange outer rim.

    Each rib has a vertical outer face flush with the tube inner face, so both
    the rib top (at flange level) and rib bottom (at bed level) merge into the
    tube.  The rib inner face slopes at ~25 deg from vertical (self-supporting).

    Returns [] when supports are not enabled or conditions are not met.
    """
    if not (fp.get('supports_enabled')
            and fp.get('flange_3dprint')
            and not fp.get('top_separate', True)):
        return []

    try:
        R_OD, _, _ = _pulley_radii(family, pitch, num_teeth, clearance_mm, print_extra_mm)
        rim_r     = max(0.5, fp['rim_radius_mm'])
        angle_deg = max(8.0, min(25.0, fp['flange_angle_deg']))
        angle_rad = math.radians(angle_deg)
        z_angled  = rim_r * math.tan(angle_rad)
        f_h       = max(0.1, fp['flange_height_mm'])

        nozzle_dia  = max(0.1, float(fp.get('support_nozzle_dia',  0.4)))
        max_spacing = max(1.0, float(fp.get('support_max_spacing', 10.0)))
        air_gap     = max(0.0, float(fp.get('support_air_gap',     0.2)))

        r_outer = R_OD + rim_r   # outer rim of upper flange

        z_ti  = belt_height_mm - air_gap            # flat underside of flange (air gap in)
        z_to  = belt_height_mm + z_angled - air_gap # angled outer underside (air gap in)
        z_bed = -f_h                                # bed level = bottom of lower flange

        if z_ti <= z_bed:
            return []

        # Tube sits 1mm outside lower flange outer edge, spans bed to flange rim height
        r_tube     = r_outer + 1.0
        tube_h     = z_to - z_bed           # from bed up to upper flange rim level
        tube_outer = r_tube + nozzle_dia
        tube_sections = max(64, round(2.0 * math.pi * tube_outer / 3.0))
        tube_mesh = _revolve_polygon([
            (r_tube,      0.0),
            (tube_outer,  0.0),
            (tube_outer,  tube_h),
            (r_tube,      tube_h),
        ], sections=tube_sections)
        tube_mesh.apply_translation([0.0, 0.0, z_bed])

        # Two 1mm slits at 0° and 180° — always between ribs (ribs start at π/n_ribs)
        # Each slit is a tall thin box that cuts through the full tube wall
        slit_w = 1.0
        slit_d = nozzle_dia + 2.0          # deeper than wall to guarantee clean cut
        slit_h = tube_h + 2.0              # taller than tube for clean top/bottom cuts
        r_mid  = r_tube + nozzle_dia / 2.0 # radial midpoint of tube wall
        z_mid  = z_bed + tube_h / 2.0
        slit_0   = trimesh.creation.box(extents=[slit_d, slit_w, slit_h])
        slit_0.apply_translation([ r_mid, 0.0, z_mid])
        slit_180 = trimesh.creation.box(extents=[slit_d, slit_w, slit_h])
        slit_180.apply_translation([-r_mid, 0.0, z_mid])
        slit_union = trimesh.boolean.union([slit_0, slit_180], engine='manifold')
        tube_mesh  = trimesh.boolean.difference([tube_mesh, slit_union], engine='manifold')

        hw_tip  = nozzle_dia / 2.0  # breakaway tip: 1x nozzle wide (tangential)
        hw_base = nozzle_dia        # tube contact: 2x nozzle wide

        # Rib count: multiple of 4, offset from cardinal angles
        n_raw  = math.ceil(2.0 * math.pi * r_outer / max_spacing)
        n_ribs = max(4, int(math.ceil(n_raw / 4.0)) * 4)
        start_angle = math.pi / n_ribs

        meshes = [tube_mesh]
        for k in range(n_ribs):
            theta = start_angle + k * 2.0 * math.pi / n_ribs
            meshes.append(_build_buttress_mesh(
                R_OD, z_ti, r_outer, z_to, r_tube, z_bed, hw_tip, hw_base, theta,
            ))
        return meshes
    except Exception:
        return []


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
    flat_depth_mm: float = 0.0,
    keyway_w_mm: float = 0.0,
    keyway_h_mm: float = 0.0,
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
        # Adaptive sections: target ~3 mm chord on the outer radius; cap at caller's sections.
        sections = max(32, min(sections, round(2 * math.pi * R_OD / 3.0)))
        angle = max(8.0, min(25.0, fp['flange_angle_deg']))
        rim_r = max(0.5, fp['rim_radius_mm'])
        meshes = []

        if fp['flange_3dprint']:
            # Use rim boundary (R_OD - tooth_ht) as tooth reference when spokes enabled
            R_tr = R_OD - tooth_ht
            r_tooth_ref = R_tr if spokes_enabled else R_OD

            r_inner_bot = flange_inner_r_3dprint_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                        r_tooth_OD=r_tooth_ref, rim_depth_mm=rim_depth_mm)
            # Flange ID must be at the rim boundary (spoke outer edge)
            if spokes_enabled and rim_depth_mm > 0.0:
                r_inner = R_tr - rim_depth_mm
            else:
                r_inner = flange_inner_r_3dprint(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                                 r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            f_h = max(0.1, fp['flange_height_mm'])
            prof     = profile_3dprint(r_inner,     r_tooth_ref, rim_r, angle, f_h)
            prof_bot = profile_3dprint(r_inner_bot, r_tooth_ref, rim_r, angle, f_h)

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
                # Use rim boundary (R_OD - tooth_ht - rim_depth), matching flange ID
                r_spoke_outer = ((R_OD - tooth_ht) - rim_depth_mm
                                 if (spokes_enabled and rim_depth_mm > 0.0)
                                 else 0.0)
                nub_cyls = []
                _NUB_EMBED = 0.5
                for x, y in _nub_xy(fp['nub_count'], r_nub):
                    cyl_h = nub_pin_h + _NUB_EMBED
                    cyl = trimesh.creation.cylinder(radius=r_pin, height=cyl_h, sections=16)
                    # Embed 0.5 mm into flange to avoid coplanar face at Z=0
                    cyl.apply_translation([x, y, (_NUB_EMBED - nub_pin_h) / 2.0])
                    nub_cyls.append(cyl)
                if nub_cyls:
                    try:
                        top = trimesh.boolean.union([top] + nub_cyls, engine='manifold')
                        clip_h = nub_pin_h + 2.0
                        # Clip nubs at flange ID — use spoke rim boundary if spokes active,
                        # else r_inner (the plain flange ID).  Always applied so nubs never
                        # protrude through the inner face of the flange ring.
                        clip_id = r_spoke_outer if r_spoke_outer > 0.0 else r_inner
                        if r_nub - r_pin < clip_id:
                            clip_cyl = trimesh.creation.cylinder(
                                radius=clip_id, height=clip_h, sections=64)
                            clip_cyl.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                            top = trimesh.boolean.difference(
                                [top, clip_cyl], engine='manifold')
                        # Clip at spoke inner rim (hub boss surface) only if nubs extend into spoke hub
                        if r_spoke_inner > 0.0 and (r_nub - r_pin) < r_spoke_inner:
                            clip_cyl = trimesh.creation.cylinder(
                                radius=r_spoke_inner, height=clip_h, sections=64)
                            clip_cyl.apply_translation([0.0, 0.0, -nub_pin_h / 2.0])
                            top = trimesh.boolean.difference(
                                [top, clip_cyl], engine='manifold')
                    except Exception:
                        pass  # fall back to flange without nubs

            top = _subtract_bore_profile(top, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            if fp.get('top_separate', False):
                # Float 10 mm above the hub top to reveal the nub face
                top_z = belt_height_mm + hub_height_mm + 10.0
            else:
                top_z = belt_height_mm
            top.apply_translation([0.0, 0.0, top_z])
            meshes.append(top)

            bot = _revolve_polygon([(r, -z) for r, z in prof_bot], sections)
            bot = _subtract_bore_profile(bot, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            meshes.append(bot)
        else:
            plate_t = max(0.3, fp['plate_height_mm'])
            bend_r  = fp['bend_radius_mm'] if fp['bend_radius_mm'] > 0.0 else 1.5 * plate_t
            bend_r  = min(bend_r, rim_r * 0.8)

            r_inner_top = flange_inner_r_metal_top(bore_mm, hub_od_mm, spokes_enabled, spoke_hub_od_mm,
                                                   r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof_top = profile_metal(r_inner_top, R_OD, rim_r, angle, plate_t, bend_r)
            top = _revolve_polygon(prof_top, sections)
            top = _subtract_bore_profile(top, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            top.apply_translation([0.0, 0.0, belt_height_mm])
            meshes.append(top)

            r_inner_bot = flange_inner_r_metal_bottom(bore_mm, spokes_enabled, spoke_hub_od_mm,
                                                      r_tooth_OD=R_OD, rim_depth_mm=rim_depth_mm)
            prof_bot = profile_metal(r_inner_bot, R_OD, rim_r, angle, plate_t, bend_r)
            prof_bot = [(r, -z) for r, z in prof_bot]  # flip: bend faces down, contact face at Z=0
            bot = _revolve_polygon(prof_bot, sections)
            bot = _subtract_bore_profile(bot, bore_mm, flat_depth_mm, keyway_w_mm, keyway_h_mm)
            meshes.append(bot)

        return meshes
    except Exception:
        return []
