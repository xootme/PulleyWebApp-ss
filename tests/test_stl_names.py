"""test_stl_names.py — a pulley's STL files and their names (the owner, 2026-10-03).

  GT-2M-30T.stl                 the pulley: no flanges, or both on it
  GT-2M-30T-1flange.stl         its top flange is a separate part, so only one is on it
  ...-with-supports.stl         the same with print supports (a printed top made in place
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
    assert _name(_get(client, q)) == 'GT-2M-40T-P2-with-supports.stl'


def test_with_supports_is_the_pulley_plus_its_supports(client):
    plain = _mesh(_get(client, {**BASE, **SUPPORTS}))
    r = _get(client, {**BASE, **SUPPORTS, 'with_supports': '1'})
    assert _name(r) == 'GT-2M-30T-with-supports.stl'
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
    assert got == {'GT-2M-30T.stl', 'GT-2M-30T-with-supports.stl'}
    got = {name for *_x, name in files({**BASE, **PRINTED, 'flange_top_separate': '1'}, ['pulley1'], ['stl'])}
    assert got == {'GT-2M-30T-1flange.stl'}
    # and a one-pulley belt only as drawings: its STEP and STL need two pulleys
    fmts = {fmt for _p, fmt, *_x in files(dict(BASE), ['belt'], ['step', 'stl', 'svg', 'dxf'])}
    assert fmts == {'svg', 'dxf'}
