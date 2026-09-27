"""
test_hub_setscrew.py — standard set-screw holes go from the hub OD to the bore.

They used to be drilled through the full hub diameter in the STL / 3D preview
(both trimesh paths in exporters/step_exporter.py), while small_step's STEP and
the cadquery path drilled one side only. Each hole now enters from the outside
and stops at the bore, so the far side of the hub stays solid.

Checked on the app's own STL, sliced at the set-screw height.
"""
import math

import numpy as np
import pytest
import trimesh
from shapely.geometry import MultiLineString, Point, Polygon
from shapely.ops import polygonize, unary_union

BORE, HUB_OD, HUB_H, BELT, CLEAR = 12.0, 26.0, 12.0, 10.0, 0.5
Z_SCREW = BELT + CLEAR + HUB_H / 2
R_MID = (BORE / 2 + HUB_OD / 2) / 2          # halfway through the hub wall


def _stl(client, **extra):
    q = {'family': 'HTD', 'pitch': '5M', 'teeth': '24', 'bore': str(BORE), 'print_extra': '0',
         'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD',
         'belt_height': str(BELT), 'clearance_height': str(CLEAR),
         'hub_od': str(HUB_OD), 'hub_height': str(HUB_H)}
    q.update({k: str(v) for k, v in extra.items()})
    r = client.get('/download/stl', query_string=q)
    assert r.status_code == 200
    b = r.data
    n = int(np.frombuffer(b[80:84], '<u4')[0])          # metadata follows the triangles
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(b[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3),
                           faces=np.arange(3 * n).reshape(-1, 3), process=True)


def _material_at(mesh, z):
    """The solid cross-section at height z, as a shapely geometry."""
    segs = trimesh.intersections.mesh_plane(mesh, plane_normal=[0, 0, 1],
                                            plane_origin=[0, 0, z + 1.37e-3])

    def snap(p):
        return (round(float(p[0]), 4), round(float(p[1]), 4))
    lines = MultiLineString([[snap(a), snap(b)] for a, b in segs if snap(a) != snap(b)])
    # polygonize returns each face with its holes cut AND each hole as a face:
    # even-odd their outlines only, or the holes are filled back in
    mat = None
    for face in polygonize(unary_union(lines)):
        outline = Polygon(face.exterior)
        mat = outline if mat is None else mat.symmetric_difference(outline)
    return mat


def _solid(mat, angle_deg):
    a = math.radians(angle_deg)
    return mat.contains(Point(R_MID * math.cos(a), R_MID * math.sin(a)))


@pytest.mark.parametrize('count,holes', [(1, {0}), (2, {0, 90})])
def test_standard_set_screw_holes_stop_at_the_bore(client, count, holes):
    m = _stl(client, hub_screw_dia=5, hub_screw_count=count, hub_captured_nut=0)
    assert m.is_watertight
    mat = _material_at(m, Z_SCREW)
    for angle in (0, 90, 180, 270):
        assert _solid(mat, angle) == (angle not in holes), f'{angle}° wall'


def test_no_screws_means_a_solid_hub_wall(client):
    mat = _material_at(_stl(client), Z_SCREW)
    assert all(_solid(mat, a) for a in (0, 90, 180, 270))
