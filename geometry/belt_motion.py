"""
belt_motion.py — the 3D drive view's ⟳ Turn: both pulleys turn and the belt
runs round them in mesh (the Sprocket and Gear apps' Turn, their ADR-066 and
ADR-043).

The belt is n_belt one-tooth pieces, each a flat strip in path coordinates:
s along the pitch path (the open-belt loop of build_two_pulley_belt, clockwise
from the top tangent point of pulley 1) and d out from it, extruded in z. A
point (s, d) is placed at P(s) + d·N(s), with the path's own point and outward
normal, exactly as build_two_pulley_belt lays the still belt's teeth: so at
rest the pieces ARE the still belt, a piece on an arc wraps the pulley and on
a span runs straight, with no chord error at any size.

Turning, pulley k turns rates[k]·u (counter-clockwise from above, the same
way for both) and every piece moves r_eff·u back along s. r_eff = z1·p_eff/2π,
where p_eff is the path's length over its whole teeth (the belt's pitch, a
hair off the nominal when the centre is not exactly whole-tooth): one tooth of
pulley 1 moves the belt exactly one piece on, so the picture at every whole
tooth is the rest picture and nothing drifts.

The page (templates/index.html, _belt*) mirrors place() in JavaScript; tests
(tests/test_turn.py) hold the two to the still belt and the pulleys' grooves.
"""
import math

import numpy as np

from geometry.pulley_geometry import belt_path


# Back-line points per piece (the tooth side has the profile's own points).
BACK_SAMPLES = 24


def piece_outline(bp: dict):
    """One piece in (s, d): the tooth profile from s = 0 to p_eff (pitch line
    ends), then the back line home. Counter-clockwise in (s, d)."""
    p_eff, stretch, aa = bp['p_eff'], bp['stretch'], bp['aa']
    inner = [(p_eff * 0.5 + bx * stretch, by - aa) for bx, by in bp['tooth']]
    inner[0] = (0.0, inner[0][1])
    inner[-1] = (p_eff, inner[-1][1])
    d_back = bp['hs'] - bp['ht'] - aa
    back = [(p_eff * (1 - i / BACK_SAMPLES), d_back) for i in range(BACK_SAMPLES + 1)]
    out = []
    for p in inner + back:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-6:
        out.pop()
    return out


def piece_mesh(bp: dict, height: float):
    """One piece as a trimesh in (s, d, z), z from 0 to height."""
    import trimesh
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    poly = orient(Polygon(piece_outline(bp)).buffer(0), sign=1.0)
    m = trimesh.creation.extrude_polygon(poly, height)
    m.fix_normals()
    return m


def place(path: dict, s, d):
    """Path coordinates (arrays) to the plane: P(s) + d·N(s)."""
    s = np.mod(np.asarray(s, float), path['L'])
    d = np.asarray(d, float)
    x, y = np.zeros_like(s), np.zeros_like(s)
    nx, ny = np.zeros_like(s), np.zeros_like(s)
    s0, s1, s2, _ = path['seg']
    (lt, rt, rb, lb), (c1, c2) = path['tangent'], path['centre']
    R1, R2 = path['R']
    # top run, L_top -> R_top
    k = s < s0
    tx, ty = (rt[0] - lt[0]) / s0, (rt[1] - lt[1]) / s0
    x[k], y[k], nx[k], ny[k] = lt[0] + s[k] * tx, lt[1] + s[k] * ty, -ty, tx
    # right arc, clockwise
    k = (s >= s0) & (s < s0 + s1)
    a = path['a_rt'] - (s[k] - s0) / R2
    x[k], y[k], nx[k], ny[k] = c2[0] + R2 * np.cos(a), c2[1] + R2 * np.sin(a), np.cos(a), np.sin(a)
    # bottom run, R_bot -> L_bot
    k = (s >= s0 + s1) & (s < s0 + s1 + s2)
    tx, ty = (lb[0] - rb[0]) / s2, (lb[1] - rb[1]) / s2
    q = s[k] - s0 - s1
    x[k], y[k], nx[k], ny[k] = rb[0] + q * tx, rb[1] + q * ty, -ty, tx
    # left arc, clockwise
    k = s >= s0 + s1 + s2
    a = path['a_lb'] - (s[k] - s0 - s1 - s2) / R1
    x[k], y[k], nx[k], ny[k] = c1[0] + R1 * np.cos(a), c1[1] + R1 * np.sin(a), np.cos(a), np.sin(a)
    return x + d * nx, y + d * ny


def belt_at(path: dict, piece, shift: float = 0.0):
    """The whole belt as one trimesh: every piece placed `shift` along s."""
    import trimesh
    v, f = piece.vertices, piece.faces
    verts, faces = [], []
    for k in range(path['n']):
        x, y = place(path, v[:, 0] + k * path['p_eff'] + shift, v[:, 1])
        verts.append(np.column_stack([x, y, v[:, 2] + path['z0']]))
        faces.append(f + k * len(v))
    return trimesh.Trimesh(np.vstack(verts), np.vstack(faces), process=False)


def drive_motion(family, pitch, z1, z2, center_dist, place_info, belt_height):
    """What the page needs to turn the drive: each pulley's rate and axis
    (scene coordinates, generate_drive_stl_preview's place), and the belt: its
    path in the scene and one piece's triangles in (s, d, z).

    place_info: the `place` dict filled by generate_drive_stl_preview."""
    out = {'rates': [1.0, z1 / z2], 'teeth': [z1, z2],
           'origins': [place_info['origin1'], place_info['origin2']], 'belt': None}
    off = place_info.get('offset')
    cx1 = place_info.get('cx1')
    bp = belt_path(family, pitch, z1, z2, place_info.get('center_dist', center_dist), x_offset=cx1) \
        if off is not None and cx1 is not None else None
    if not bp:
        return out, None, None
    ox, oy, oz = off
    sh = lambda p: [p[0] + ox, p[1] + oy]  # noqa: E731
    path = {'L': bp['L'], 'n': bp['n_belt'], 'p_eff': bp['p_eff'],
            'r_eff': z1 * bp['p_eff'] / (2 * math.pi),
            'seg': list(bp['seg']), 'R': [bp['R_left'], bp['R_right']],
            'centre': [sh(bp['c_left']), sh(bp['c_right'])],
            'tangent': [sh(bp['L_top']), sh(bp['R_top']), sh(bp['R_bot']), sh(bp['L_bot'])],
            'a_rt': bp['a_rt'], 'a_lb': bp['a_lb'],
            'z0': place_info.get('belt_z', 0.0) + oz,
            'pitch': bp['pitch']}
    piece = piece_mesh(bp, belt_height)
    out['belt'] = path
    return out, path, piece
