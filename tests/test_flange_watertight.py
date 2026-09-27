"""
test_flange_watertight.py — flanged STLs are closed solids.

Three things used to break them:
  * the metal plate's cross-section had its bend arcs joined to the wrong
    faces, tracing the seam at the tooth OD twice (profile_metal);
  * a flange revolved exactly on the bore and then cut by the (differently
    faceted) bore cutter kept slivers of both surfaces (_revolve_through_bore
    now revolves it _BORE_OVERCUT smaller, so the cutter alone makes the bore);
  * a 3D-print pulley and the flanges printed with it were stacked as
    separate bodies sharing the bore's edge ring (_printed_as_one unions them).
Metal plates are separate sheet-metal parts, so the metal assembly stays
three bodies; each must be closed on its own.
"""
import io

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon

from geometry.flange_geometry import profile_metal

BASE = {'family': 'HTD', 'pitch': '5M', 'teeth': '24', 'bore': '12', 'print_extra': '0',
        'clearance_preset': 'STANDARD', 'backlash_preset': 'STANDARD', 'belt_height': '10',
        'clearance_height': '0.5', 'flange_enabled': '1', 'flange_angle': '15',
        'flange_rim_radius': '3', 'flange_3dprint': '1', 'flange_height': '1.5'}
NUBS = dict(flange_top_separate=1, flange_nubs_enabled=1, flange_nub_count=4, flange_nub_dia=3,
            flange_nub_height=2, flange_nub_allowance=0.2)


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])       # the design data follows the triangles
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    t = np.frombuffer(data[84:84 + 50 * n], rec)
    return trimesh.Trimesh(vertices=t['v'].reshape(-1, 3), faces=np.arange(3 * n).reshape(-1, 3),
                           process=True)


@pytest.mark.parametrize('extra', [
    dict(flange_top_separate=0),                                   # both flanges printed on
    dict(flange_top_separate=1),                                   # top flange printed apart
    NUBS,
    dict(flange_top_separate=0, hub_od=26, hub_height=12),
    dict(flange_top_separate=0, hub_flat_depth=1),                 # D-shaft through the flange
    dict(flange_top_separate=0, hub_keyway_w=4, hub_keyway_h=2.6), # key slot through the flange
], ids=['merged', 'separate-top', 'nubs', 'hub', 'd-shaft', 'keyway'])
def test_3d_print_flanged_pulley_is_one_closed_solid(client, extra):
    r = client.get('/download/stl', query_string={**BASE, **{k: str(v) for k, v in extra.items()}})
    assert r.status_code == 200
    m = _load(r.data)
    assert m.is_watertight and m.is_volume
    assert len(trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(m.faces)),
                                                  engine='scipy')) == 1


@pytest.mark.parametrize('which', ['top', 'bottom', 'both'])
def test_metal_plates_are_closed(which):
    from exporters.flange_exporter import generate_metal_flange_stl
    m = trimesh.load(io.BytesIO(generate_metal_flange_stl('HTD', '5M', 24, 12.0, 10.0, which=which,
                                                          flat_depth_mm=1.0)), file_type='stl')
    assert m.is_watertight and m.is_volume


@pytest.mark.parametrize('args', [
    (6.0, 18.53, 3.0, 15.0, 1.0, 1.5),
    (6.0, 18.53, 3.0, 8.0, 0.5, 0.75),
    (10.0, 30.0, 5.0, 25.0, 2.0, 3.0),
    (6.0, 18.5, 3.0, 15.0, 1.0, 0.0),        # a zero bend collapses an arc to a point
])
def test_metal_profile_is_a_simple_outline(args):
    p = profile_metal(*args)
    assert Polygon(p).is_valid
    assert len({(round(r, 9), round(z, 9)) for r, z in p}) == len(p), 'repeated points'
