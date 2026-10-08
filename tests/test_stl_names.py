"""test_stl_names.py — a pulley's STL files and their names (the owner, 2026-10-03).

  GT-2M-30T.stl                 the pulley: no flanges, or both on it
  GT-2M-30T-1flange.stl         its top flange is a separate part, so only one is on it
  ...-w-supports.stl         the same with print supports (a printed top made in place
                                and Add Print Supports): offered beside the plain one
                                (with_supports=1), not as a "Pulley n flanges" part

The flanges part now holds only a separate top flange (templates/index.html
_flangeFiles); this checks the server's half and the agent API's list.
"""
import numpy as np
import pytest
import trimesh

BASE = {'family': 'GT', 'pitch': '2M', 'teeth': '30', 'bore': '5', 'belt_height': '6'}
PRINTED = {'flange_enabled': '1', 'flange_3dprint': '1', 'flange_rim_radius': '2.5',
           'flange_height': '1.2', 'flange_angle': '15'}
JOINED = {**PRINTED, 'flange_top_separate': '0'}
SUPPORTS = {**JOINED, 'flange_supports_enabled': '1'}


def _get(client, q):
    return client.get('/download/stl', query_string=q)


def _name(r):
    assert r.status_code == 200, r.data[:200]
    return r.headers['Content-Disposition'].split('filename="')[1].rstrip('"')


def _mesh(r):
    # read the binary STL directly: the design embedded in its header can make a
    # loader take it for ASCII (as tests/test_flange_overlap.py does)
    data = r.data
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    tri = np.frombuffer(data[84:84 + 50 * n], rec)['v'].astype(float)
    return trimesh.Trimesh(vertices=tri.reshape(-1, 3), faces=np.arange(3 * n).reshape(-1, 3), process=True)


@pytest.mark.parametrize('extra, name', [
    ({}, 'GT-2M-30T.stl'),
    (JOINED, 'GT-2M-30T.stl'),
    (SUPPORTS, 'GT-2M-30T.stl'),
    ({**PRINTED, 'flange_top_separate': '1'}, 'GT-2M-30T-1flange.stl'),
    ({'flange_enabled': '1', 'flange_3dprint': '0', 'flange_rim_radius': '4', 'flange_plate_height': '1'},
     'GT-2M-30T.stl'),
], ids=['no flanges', 'printed, both on', 'supports, plain copy', 'top separate', 'metal'])
def test_the_pulley_stl_is_named_for_its_flanges(client, extra, name):
    assert _name(_get(client, {**BASE, **extra})) == name


def test_pulley_2_keeps_its_suffix(client):
    q = {**BASE, 'dual': 'true', 'p2_teeth': '40', 'p2_bore': '5', 'center_distance': '80', 'pulley': '2',
         'p2_flange_enabled': '1', 'p2_flange_3dprint': '1', 'p2_flange_top_separate': '1'}
    assert _name(_get(client, q)) == 'GT-2M-40T-P2-1flange.stl'
    q.update(p2_flange_top_separate='0', p2_flange_supports_enabled='1', with_supports='1')
    assert _name(_get(client, q)) == 'GT-2M-40T-P2-w-supports.stl'


def test_with_supports_is_the_pulley_plus_its_supports(client):
    plain = _mesh(_get(client, {**BASE, **SUPPORTS}))
    r = _get(client, {**BASE, **SUPPORTS, 'with_supports': '1'})
    assert _name(r) == 'GT-2M-30T-w-supports.stl'
    supported = _mesh(r)
    # the same pulley, every triangle of it, plus the ribs and the bed tube outside the flange
    assert len(supported.faces) > len(plain.faces)
    r_plain = np.hypot(*plain.vertices[:, :2].T).max()
    assert np.hypot(*supported.vertices[:, :2].T).max() > r_plain + 0.5
    inside = supported.vertices[np.hypot(*supported.vertices[:, :2].T) <= r_plain + 1e-6]
    assert len(inside) >= len(plain.vertices) * 0.99


@pytest.mark.parametrize('extra', [
    {},
    {**PRINTED, 'flange_top_separate': '1', 'flange_supports_enabled': '1'},
    {**JOINED},
], ids=['no flanges', 'top separate', 'supports off'])
def test_with_supports_is_refused_where_there_are_none(client, extra):
    r = _get(client, {**BASE, **extra, 'with_supports': '1'})
    assert r.status_code == 400 and b'Print supports' in r.data


def test_with_supports_is_a_delivery_key_not_a_design_setting():
    """A token-charged zip checks every file against the registered design
    (bug hunt section 1): with_supports must be on the route-only list."""
    import charging
    design = {**BASE, **SUPPORTS}
    assert charging.design_matches({**design, 'with_supports': '1'}, design)


def test_the_agent_api_lists_the_same_files():
    from agent import files
    got = {name for _p, fmt, _path, _q, name in files({**BASE, **SUPPORTS}, ['pulley1'], ['stl'])}
    assert got == {'GT-2M-30T.stl', 'GT-2M-30T-w-supports.stl'}
    got = {name for *_x, name in files({**BASE, **PRINTED, 'flange_top_separate': '1'}, ['pulley1'], ['stl'])}
    assert got == {'GT-2M-30T-1flange.stl'}
    # and a one-pulley belt only as drawings: its STEP and STL need two pulleys
    fmts = {fmt for _p, fmt, *_x in files(dict(BASE), ['belt'], ['step', 'stl', 'svg', 'dxf'])}
    assert fmts == {'svg', 'dxf'}


# ── the separate top flange's name (the owner, 2026-10-05) ───────────────────

@pytest.mark.parametrize('pulley, name', [('1', 'GT-2M-30T-flange-only.stl'), ('2', 'GT-2M-30T-P2-flange-only.stl')])
def test_the_separate_top_flange_is_flange_only(client, pulley, name):
    q = {**BASE, **PRINTED, 'flange_top_separate': '1', 'flange_which': 'top'}
    if pulley == '2':
        q['pulley'] = '2'                   # the flange route takes P2's settings flat
    r = client.get('/download/flange-stl', query_string=q)
    assert _name(r) == name


# ── grid supports under the hub and spoke web (the owner, 2026-10-05) ────────
# Printed in place on a spoked pulley, the bottom flange is a ring from the spoke
# rim outwards, so the hub and the web hang a flange height above the bed.

SPOKED = {**BASE, **SUPPORTS, 'teeth': '60', 'bore': '6', 'flange_height': '1.5', 'spokes_enabled': '1',
          'spokes_hub_od': '16', 'spokes_rim_depth': '3', 'spokes_width': '5', 'spokes_count': '5',
          'flange_support_nozzle_dia': '0.4', 'flange_support_max_spacing': '6', 'flange_support_air_gap': '0.2'}


def _grid(q):
    """The grid meshes of a design's print supports: those inside the bottom
    flange's inner edge (the ribs and their tube stand outside the flange)."""
    import app as A
    fp = A._parse_flange_params(q, '')
    r_root, reach = A._spoke_room(q, '')
    r_rim = r_root - float(q['spokes_rim_depth'])
    grid = [m for m in A._print_supports(q, '1', fp)
            if np.hypot(m.vertices[:, 0], m.vertices[:, 1]).max() < r_rim]
    v = np.vstack([m.vertices for m in grid]) if grid else np.zeros((0, 3))
    return grid, v, r_rim, reach / 2


@pytest.mark.parametrize('web, z_web', [('0', 0.0), ('4', (6 - 4) / 2)], ids=['full web', 'web 4 mm, centred'])
def test_a_spoked_pulley_gets_a_grid_under_its_hub_and_web(client, web, z_web):
    q = {**SPOKED, 'spokes_height': web}
    grid, v, r_rim, r_bore = _grid(q)
    assert grid, 'no grid under the middle'
    r = np.hypot(v[:, 0], v[:, 1])
    assert v[:, 2].min() == pytest.approx(-1.5, abs=1e-6)             # stands on the bed
    assert v[:, 2].max() == pytest.approx(z_web - 0.2, abs=1e-6)      # one air gap below the web
    assert r.min() >= r_bore + 1.0 - 1e-3                             # 1 mm clear of the bore (a 384-gon)
    assert r.max() <= r_rim - 1.0 + 1e-3                              # 1 mm clear of the flange's inner edge
    under_hub = v[r < 8.0 - 1e-3]                                      # the hub is Ø16
    assert len(under_hub) and under_hub[:, 2].max() == pytest.approx(-0.2, abs=1e-6)   # the hub sits at z = 0
    # every grid wall is one nozzle wide, and they are in the -w-supports STL
    plain = _mesh(_get(client, q))
    supported = _mesh(_get(client, {**q, 'with_supports': '1'}))
    assert len(supported.faces) >= len(plain.faces) + sum(len(m.faces) for m in grid)


def test_the_grid_walls_are_no_further_apart_than_the_largest_gap():
    from exporters.flange_exporter import _grid_positions
    for lo, hi, s in ((-20, 20, 6), (-3.1, 3.1, 10), (0, 47.3, 4.5)):
        p = _grid_positions(lo, hi, s)
        assert all(b - a <= s + 1e-9 for a, b in zip(p, p[1:]))
        assert p[0] - lo <= s / 2 + 1e-9 and hi - p[-1] <= s / 2 + 1e-9


@pytest.mark.parametrize('extra', [{'spokes_enabled': '0'}, {'flange_supports_enabled': '0'},
                                   {'flange_top_separate': '1'}],
                         ids=['no spokes (the flange runs to the hub)', 'supports off', 'top separate'])
def test_no_grid_where_nothing_hangs_or_supports_are_off(extra):
    grid, *_ = _grid({**SPOKED, **extra})
    assert grid == []


def test_each_grid_stands_on_a_one_layer_adhesion_layer():
    """A solid layer (half the nozzle thick) on the bed under each grid,
    covering its whole area, so the walls hold the bed (the owner, 2026-10-06)."""
    for nozzle in ('0.4', '0.6'):
        grid, v, r_rim, r_bore = _grid({**SPOKED, 'spokes_height': '4', 'flange_support_nozzle_dia': nozzle})
        pad_h = float(nozzle) / 2
        h = lambda m: m.vertices[:, 2].max() - m.vertices[:, 2].min()
        pads = [m for m in grid if abs(m.vertices[:, 2].min() + 1.5) < 1e-6 and abs(h(m) - pad_h) < 1e-6]
        walls = [m for m in grid if not any(m is p for p in pads)]
        assert pads and walls
        area = lambda ms: sum(m.volume / h(m) for m in ms)
        assert area(pads) > area(walls)                # solid under the grid, not just under the walls
        under = _footprint(pads)                       # every wall stands on a layer
        for w in walls:
            assert _footprint([w]).difference(under).area < 1e-3, 'a wall off its layer'


@pytest.mark.parametrize('web', ['0', '4'], ids=['full web', 'web 4 mm, centred'])
def test_the_grid_keeps_1mm_clear_of_every_vertical_face(web):
    """The grid's outline wall stands 1 mm from the part's vertical faces and
    the hatch stops at it (the owner, 2026-10-08): the bore, the bottom
    flange's inner edge, every spoke opening and, under a web shorter than
    the face, the hub's bare side above the web's taller grid."""
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    from exporters.png_exporter import _spoke_void_polygons
    q = {**SPOKED, 'spokes_height': web}
    grid, v, r_rim, r_bore = _grid(q)
    assert grid
    r_hub = 8.0                                                        # the hub is Ø16
    openings = unary_union([Polygon(p).buffer(0) for p in _spoke_void_polygons(
        r_hub, r_rim, 5, 5.0, fillet_tip_mm=1.0, fillet_base_mm=1.5) if len(p) >= 3])
    faces = [('bore', Point(0, 0).buffer(r_bore, 96).exterior),
             ("flange's inner edge", Point(0, 0).buffer(r_rim, 96).exterior),
             ('spoke opening', openings.boundary)]
    tall = [m for m in grid if m.vertices[:, 2].max() > 0]             # the web's grid, above the hub's underside
    if web != '0':
        assert tall
        faces.append(("hub's side", Point(0, 0).buffer(r_hub, 96).exterior))
    for name, face in faces:
        for m in (tall if name == "hub's side" else grid):
            d = _footprint([m]).distance(face)
            assert d >= 1.0 - 2e-3, f'a support {d:.2f} mm from the {name}'


@pytest.mark.parametrize('extra', [{}, {'teeth': '60', 'belt_height': '10', 'flange_rim_radius': '4'}],
                         ids=['GT2 30T', 'GT2 60T, rim 4'])
def test_the_ribs_keep_1mm_clear_of_the_teeth(extra):
    """Each rib under the top flange stops 1 mm out from the tooth tips, so
    no support touches the teeth (the owner, 2026-10-08)."""
    import app as A
    from exporters.flange_exporter import _pulley_radii
    q = {**BASE, **SUPPORTS, **extra}
    family, pitch, teeth, _b, _bh, cl, _bl, pr = A._parse_stl_params(q, '1')
    r_od = _pulley_radii(family, pitch, teeth, cl, pr)[0]
    ribs = A._print_supports(q, '1', A._parse_flange_params(q, ''))
    assert ribs
    v = np.vstack([m.vertices for m in ribs])
    beside = v[v[:, 2] > 0]                                            # beside the teeth, above the lower flange
    assert len(beside)
    assert np.hypot(beside[:, 0], beside[:, 1]).min() >= r_od + 1.0 - 1e-6


def _footprint(meshes):
    """The area the meshes cover on the bed: the union of their bottom faces."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for m in meshes:
        low = m.vertices[:, 2].min()
        tri = m.triangles[np.all(np.abs(m.triangles[:, :, 2] - low) < 1e-9, axis=1)][:, :, :2]
        polys += [Polygon(t).buffer(1e-6) for t in tri if Polygon(t).area > 1e-12]
    return unary_union(polys)
