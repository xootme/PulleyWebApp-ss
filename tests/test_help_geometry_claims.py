"""
test_help_geometry_claims.py — what the 3D help pages say about the geometry.

The help pages and their pictures (static/help/, tools/help_illustrations/)
state how Spoke Height, Flange Height, metal flanges and gluing nubs come
out. Several of those statements were wrong until the pictures were cut from
the app's own STLs; these tests keep them true. Each builds an STL through
the app and measures it.
"""
import math

import numpy as np
import pytest
import trimesh
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import polygonize, unary_union

BASE = {'family': 'HTD', 'pitch': '5M', 'bore': '8', 'print_extra': '0',
        'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD',
        'belt_height': '10', 'clearance_height': '0.5'}
FULL = 10.5                                   # belt + clearance height


def _mesh(client, route='/download/stl', **q):
    args = dict(BASE)
    args.update({k: str(v) for k, v in q.items()})
    r = client.get(route, query_string=args)
    assert r.status_code == 200, r.data[:200]
    b = r.data
    n = int(np.frombuffer(b[80:84], '<u4')[0])          # metadata follows the triangles
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(b[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def _solid(mesh, normal, origin, to2d):
    """The cross-section on a plane, as one shapely geometry (even-odd of all faces)."""
    origin = np.asarray(origin, float) + np.asarray(normal, float) * 1.37e-3
    segs = trimesh.intersections.mesh_plane(mesh, plane_normal=normal, plane_origin=origin)

    def snap(p):
        return tuple(round(float(c), 4) for c in to2d(p))
    lines = MultiLineString([[snap(a), snap(b)] for a, b in segs if snap(a) != snap(b)])
    # polygonize returns each face with its holes cut AND each hole as a face:
    # even-odd their outlines only, or the holes are filled back in
    mat = None
    for face in polygonize(unary_union(lines)):
        outline = Polygon(face.exterior)
        mat = outline if mat is None else mat.symmetric_difference(outline)
    return mat


def _z_extent(mat, x):
    """Lowest and highest z of the material on the vertical line at radius x."""
    hit = mat.intersection(LineString([(x, -50), (x, 50)]))
    zs = [c[1] for g in getattr(hit, 'geoms', [hit]) for c in g.coords]
    return min(zs), max(zs)


# ── Spoke Height ──────────────────────────────────────────────────────────────

SPOKES = dict(teeth=40, spokes_enabled=1, spokes_hub_od=18, spokes_rim_depth=3, spokes_width=5,
              spokes_count=6, spokes_fillet_tip=1.5, spokes_fillet_base=2)


@pytest.mark.parametrize('height', [6, 3])
def test_spoke_height_is_the_centred_web_thickness(client, height):
    """A pocket of (full − height)/2 above and below each spoke; hub and rim full height."""
    m = _mesh(client, spokes_height=height, **SPOKES)
    t = math.radians(60)                                    # a spoke of this layout
    mat = _solid(m, [-math.sin(t), math.cos(t), 0], [0, 0, 0],
                 lambda p: (p[0] * math.cos(t) + p[1] * math.sin(t), p[2]))
    lo, hi = _z_extent(mat, 17.0)                           # mid-spoke
    assert hi - lo == pytest.approx(height, abs=0.02)
    assert lo == pytest.approx((FULL - height) / 2, abs=0.02)
    assert _z_extent(mat, 6.0) == pytest.approx((0, FULL), abs=0.02)     # hub
    assert _z_extent(mat, 27.5) == pytest.approx((0, FULL), abs=0.02)    # rim


# ── Flanges ───────────────────────────────────────────────────────────────────

FLANGE = dict(teeth=24, bore=12, flange_enabled=1, flange_angle=15, flange_rim_radius=3)


def test_flange_height_is_the_thickness_at_the_teeth(client):
    """3D print: Flange Height is the top flange's thickness where it leaves the
    teeth; the outer lip is thinner by rim radius × tan(angle)."""
    m = _mesh(client, flange_3dprint=1, flange_height=1.5, flange_top_separate=0, **FLANGE)
    r_od = 18.53                                           # tooth OD radius (HTD 5M 24T)
    mat = _solid(m, [0, 1, 0], [0, 0, 0], lambda p: (p[0], p[2]))
    lo, hi = _z_extent(mat, r_od - 0.3)
    assert hi - FULL == pytest.approx(1.5, abs=0.02)       # full height above the belt face
    top = mat.intersection(LineString([(r_od + 3 - 0.02, FULL - 0.5), (r_od + 3 - 0.02, 50)]))
    zs = [c[1] for g in getattr(top, 'geoms', [top]) for c in g.coords]   # the top flange's lip
    assert max(zs) - min(zs) == pytest.approx(1.5 - 3 * math.tan(math.radians(15)), abs=0.05)


def test_metal_flanges_come_in_the_pulley_stl(client):
    """Both metal plates, on the pulley's faces, are part of its STL."""
    m = _mesh(client, flange_3dprint=0, flange_plate_height=1, flange_bend_radius=0, **FLANGE)
    assert m.bounds[0][2] == pytest.approx(-1.6, abs=0.1)   # lower plate below the pulley
    assert m.bounds[1][2] > FULL + 1.0                      # upper plate above it
    assert m.bounds[1][0] == pytest.approx(18.53 + 3.26, abs=0.1)   # bent plate reaches the rim


def test_nub_pin_is_its_socket_less_the_allowance(client):
    nub = dict(flange_3dprint=1, flange_height=1.5, flange_top_separate=1, flange_nubs_enabled=1,
               flange_nub_count=4, flange_nub_dia=3, flange_nub_height=2, flange_nub_allowance=0.2)
    top = _mesh(client, '/download/flange-stl', flange_which='top', **nub, **FLANGE)
    assert top.bounds[0][2] == pytest.approx(FULL - (2 - 0.2), abs=0.02)   # pin 1.8 below the face
    pins = _solid(top, [0, 0, 1], [0, 0, FULL - 1.0], lambda p: (p[0], p[1]))
    assert len(getattr(pins, 'geoms', [pins])) == 4
    d = 2 * math.sqrt(pins.area / 4 / math.pi)
    assert d == pytest.approx(3 - 0.2, abs=0.08)            # faceted cylinder
    pul = _mesh(client, **nub, **FLANGE)
    face_z = float(pul.bounds[1][2])                        # that file is re-centred
    sockets = _solid(pul, [0, 0, 1], [0, 0, face_z - 1.0], lambda p: (p[0], p[1]))
    assert not sockets.contains(Point(12.87, 0))            # a socket where a pin goes
