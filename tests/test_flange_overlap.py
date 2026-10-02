"""test_flange_overlap.py — a flange sits on the pulley.

1. On a spoked pulley a flange stops at the spoke rim's inner face: the groove
   bottom (root) less the Rim Depth. The metal plates measured that from the
   tooth tips (OD) instead, so a rim narrower than the groove put the plate's
   hole outside the groove — sitting on nothing (fixed 2026-10-01).
2. The owner's rule (2026-10-01, belt_specs.min_flange_overlap): a flange
   reaches inward past the groove bottom by at least max(3 mm, the minimum
   flange height h); a narrower spoke rim is warned and Auto-fixed.
"""
import numpy as np
import pytest

from geometry import belt_specs as bs

BASE = {'family': 'HTD', 'pitch': '8M', 'teeth': '36', 'belt_height': '12', 'bore': '8',
        'feature_build': '1', 'flange_enabled': '1', 'flange_angle': '15', 'flange_rim_radius': '4.2',
        'flange_height': '1.5', 'flange_plate_height': '1', 'spokes_enabled': '1', 'spokes_hub_od': '20',
        'spokes_width': '6', 'spokes_count': '5'}
R_ROOT = 41.77          # HTD 8M 36T: groove bottom radius (OD 90.30, groove 3.38 deep)


def _load(data):
    n = int(np.frombuffer(data[80:84], '<u4')[0])
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
    return np.frombuffer(data[84:84 + 50 * n], rec)['v'].reshape(-1, 3)


def _flange_inner_r(client, q, which):
    r = client.get('/download/flange-stl', query_string={**q, 'flange_which': which})
    assert r.status_code == 200, r.data[:200]
    v = _load(r.data)
    return float(np.hypot(v[:, 0], v[:, 1]).min())


# ── 1. the flange's hole on a spoked pulley ──────────────────────────────────
@pytest.mark.parametrize('kind', [{'flange_3dprint': '0'}, {'flange_3dprint': '1', 'flange_top_separate': '1'}],
                         ids=['metal', 'printed'])
@pytest.mark.parametrize('rim', [2.0, 5.0])
def test_flange_stops_at_the_spoke_rim_inside_the_groove(client, kind, rim):
    """Both flanges' holes are the groove bottom less the rim depth — never
    outside the groove (the metal plates' were OD - rim: 43.15 at rim 2)."""
    q = {**BASE, **kind, 'spokes_rim_depth': str(rim)}
    for which in ('top', 'bottom'):
        r_in = _flange_inner_r(client, q, which)
        assert r_in == pytest.approx(R_ROOT - rim, abs=0.02), (which, r_in)
        assert r_in < R_ROOT


def test_without_spokes_the_flange_runs_to_the_bore(client):
    q = {**BASE, 'flange_3dprint': '0', 'spokes_enabled': '0'}
    assert _flange_inner_r(client, q, 'bottom') == pytest.approx(4.0, abs=0.05)


# ── 2. the overlap rule ──────────────────────────────────────────────────────
@pytest.mark.parametrize('key, expected', [
    ('3M', 3.0), ('5M', 3.0), ('8M', 3.38 + 0.686), ('14M', 6.02 + 1.397),
    ('20M', 8.68 + 2.159), ('GT2M', 3.0), ('GT5M', 3.0), ('GT8M', 3.43 + 0.80), ('MXL', 3.0),
    ('XH', 4.8), ('XXH', 6.1),
])
def test_min_overlap_is_the_larger_of_3mm_and_h(key, expected):
    from geometry.pulley_geometry import PULLEY_SPECS
    f = bs.min_flange_overlap(key, PULLEY_SPECS[key])
    assert f.value == pytest.approx(expected, abs=1e-3)
    assert f.approximate and 'owner' in f.source        # a rule, not a standard


def _dims(client, q):
    d = client.get('/api/dimensions', query_string=q).get_json()
    return d, d['pulleys'][0]


@pytest.mark.parametrize('kind', ['0', '1'], ids=['metal', 'printed'])
def test_a_narrow_spoke_rim_warns_and_auto_fixes(client, kind):
    d, p = _dims(client, {**BASE, 'flange_3dprint': kind, 'spokes_rim_depth': '2'})
    assert p['flange_overlap'] == pytest.approx(2.0) and p['flange_min_overlap'] == pytest.approx(4.066)
    assert any('inward of the groove' in w and '4.07' in w for w in d['warnings']), d['warnings']
    assert d['fix']['set']['spokes1_rim_depth'] == pytest.approx(4.1)
    # applying the fix clears it
    d, p = _dims(client, {**BASE, 'flange_3dprint': kind, 'spokes_rim_depth': '4.1'})
    assert not any('inward of the groove' in w for w in d['warnings'])
    assert 'spokes1_rim_depth' not in (d.get('fix') or {}).get('set', {})


def test_a_wide_enough_rim_and_no_spokes_are_quiet(client):
    d, p = _dims(client, {**BASE, 'flange_3dprint': '0', 'spokes_rim_depth': '5'})
    assert not any('inward of the groove' in w for w in d['warnings'])
    d, p = _dims(client, {**BASE, 'flange_3dprint': '0', 'spokes_enabled': '0'})
    assert 'flange_overlap' not in p                     # it runs in to the bore
    d, p = _dims(client, {**BASE, 'flange_enabled': '0', 'spokes_rim_depth': '2'})
    assert 'flange_overlap' not in p and not any('inward of the groove' in w for w in d['warnings'])


def test_pulley_2_gets_its_own_fix(client):
    q = {**BASE, 'spokes_rim_depth': '6', 'dual': 'true', 'p2_teeth': '24', 'center_distance': '150',
         'p2_bore': '8', 'p2_flange_enabled': '1', 'p2_flange_3dprint': '0', 'p2_flange_rim_radius': '4.2',
         'p2_spokes_enabled': '1', 'p2_spokes_rim_depth': '2', 'p2_spokes_hub_od': '16',
         'p2_spokes_width': '5', 'p2_spokes_count': '4'}
    d = client.get('/api/dimensions', query_string=q).get_json()
    assert 'spokes2_rim_depth' in d['fix']['set'] and 'spokes1_rim_depth' not in d['fix']['set']


@pytest.mark.parametrize('rim', ['2', '5'])
def test_cadquery_step_metal_flanges_on_spokes(client, monkeypatch, tmp_path, rim):
    """cadquery's STEP is the pulley alone; the STL is the pulley and its two
    plates — so STEP = STL less the plates (fuzz_pulley.py's comparison)."""
    pytest.importorskip('cadquery')
    from fuzz_pulley import _load_stl, _step_mesh_volume     # (the STLs carry CCT data trimesh won't read)
    monkeypatch.setenv('PULLEY_STEP_BACKEND', 'cadquery')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    q = {**BASE, 'flange_3dprint': '0', 'spokes_rim_depth': rim}
    step = client.get('/download/step', query_string=q)
    assert step.status_code == 200, step.data[:200]
    path = tmp_path / 'p.step'
    path.write_bytes(step.data)
    whole = _load_stl(client.get('/download/stl', query_string=q).data)
    plates = _load_stl(client.get('/download/flange-stl', query_string=q).data)
    assert _step_mesh_volume(path) == pytest.approx(whole.volume - plates.volume, rel=5e-3)
