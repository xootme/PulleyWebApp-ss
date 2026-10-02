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


@pytest.mark.parametrize('kind', ['0', '1'], ids=['metal', 'printed'])
def test_the_3d_preview_shows_the_flanges(client, kind):
    """The preview draws the flanges, from the groove bottom less the rim depth
    out past the OD. (b127340 left the preview's metal branch calling a name
    from another function: it returned no flanges at all, while the downloads
    were right — a 200 with the flanges missing.)"""
    import io
    import trimesh
    q = {**BASE, 'flange_3dprint': kind, 'spokes_rim_depth': '5'}
    flanged = trimesh.load(io.BytesIO(client.get('/api/preview-stl', query_string=q).data), file_type='stl')
    bare = trimesh.load(io.BytesIO(client.get('/api/preview-stl', query_string={**q, 'flange_enabled': '0'}).data),
                        file_type='stl')
    r_max = lambda m: float(np.hypot(m.vertices[:, 0], m.vertices[:, 1]).max())   # noqa: E731
    # out to the flange OD the Dimensions panel gives (every flange flares from the OD)
    flange_od = client.get('/api/dimensions', query_string=q).get_json()['pulleys'][0]['flange_od']
    assert r_max(flanged) == pytest.approx(flange_od / 2, abs=0.5), (r_max(flanged), flange_od / 2)   # (a plate's edge: + its thickness)
    assert r_max(flanged) > r_max(bare) + 0.5 and flanged.volume > bare.volume + 100


# ── a printed flange flares from the tooth tips, spokes or not (2026-10-02) ──
# The owner's report: on a spoked pulley the printed flanges began at the root of
# the teeth (groove bottom) instead of the tips — RPP-5M-30T-P2-flanges.step.
REPORTED = {'family': 'RPP', 'pitch': '5M', 'teeth': '30', 'bore': '10', 'belt_height': '10',
            'feature_build': '1', 'flange_enabled': '1', 'flange_3dprint': '1', 'flange_angle': '15',
            'flange_rim_radius': '4.8', 'flange_height': '1.5', 'flange_top_separate': '0',
            'flange_supports_enabled': '1', 'spokes_enabled': '1', 'spokes_hub_od': '16',
            'spokes_rim_depth': '3', 'spokes_width': '6', 'spokes_count': '4'}


def _r_od(client, q):
    p = client.get('/api/dimensions', query_string=q).get_json()['pulleys'][0]
    return p['outside_diameter_built'] / 2, p


@pytest.mark.parametrize('spokes', ['1', '0'], ids=['spokes', 'no spokes'])
@pytest.mark.parametrize('separate', ['0', '1'], ids=['joined top', 'separate top'])
def test_a_printed_flange_flares_from_the_tooth_tips(client, spokes, separate):
    """Each printed flange reaches its rim radius past the tooth tips (the OD) —
    the same with spokes as without (it reached only rim - groove depth past the
    OD with spokes: it flared from the groove bottom)."""
    q = {**REPORTED, 'spokes_enabled': spokes, 'flange_top_separate': separate}
    r_od, p = _r_od(client, q)
    assert p['flange_reach'] == pytest.approx(4.8, abs=1e-3)
    for which in ('top', 'bottom'):
        r = client.get('/download/flange-stl', query_string={**q, 'flange_which': which})
        assert r.status_code == 200, r.data[:200]
        v = _load(r.data)
        r_max = float(np.hypot(v[:, 0], v[:, 1]).max())
        assert r_max == pytest.approx(r_od + 4.8, abs=0.05), (which, r_max, r_od + 4.8)
    # the pulley's own STL (bottom flange joined; the top too unless separate)
    v = _load(client.get('/download/stl', query_string=q).data)
    assert float(np.hypot(v[:, 0], v[:, 1]).max()) == pytest.approx(r_od + 4.8, abs=0.05)


def test_the_reported_pulley_step_is_the_stl(client, monkeypatch):
    """small_step's STEP of the reported pulley (printed flanges joined, on
    spokes) is its STL: the flanges flare from the tips in both."""
    from test_step_small_step import _solids, ss_only   # noqa: F401  (OCP, the ss build)
    pytest.importorskip('OCP')
    import os
    if not os.environ.get('SMALL_STEP_BIN'):
        pytest.skip('SMALL_STEP_BIN unset')
    monkeypatch.setenv('PULLEY_STEP_BACKEND', '')
    monkeypatch.setenv('QUEUE_DISABLED', '1')
    import trimesh
    r = client.get('/download/step', query_string=REPORTED)
    assert r.status_code == 200, r.data[:200]
    n, ok, vol = _solids(r.data)
    assert ok
    v = _load(client.get('/download/stl', query_string=REPORTED).data)
    stl = trimesh.Trimesh(vertices=v, faces=np.arange(len(v)).reshape(-1, 3), process=True).volume
    assert vol == pytest.approx(stl, rel=5e-3)


# ── 3. the Auto-fix clears what it warns about (2026-10-02) ──────────────────
# The overlap is the rim AS BUILT, after the spoke fit; the fit trims Rim Depth
# when hub and rim crowd the openings. The fix wrote the needed overlap into
# Rim Depth — already 3 on the reported 40T, built 2.2 — so pressing it changed
# nothing and it was offered again, for ever.
LOOPED = {'family': 'GT', 'pitch': '2M', 'teeth': '40', 'bore': '8', 'belt_height': '6',
          'feature_build': '1', 'hub_od': '18', 'hub_height': '7', 'hub_screw_size': 'M3',
          'hub_screw_count': '1', 'spokes_enabled': '1', 'spokes_count': '5', 'spokes_height': '4',
          'spokes_rim_depth': '3', 'spokes_hub_od': '18', 'spokes_width': '4',
          'flange_enabled': '1', 'flange_3dprint': '1', 'flange_top_separate': '0',
          'flange_rim_radius': '4.8', 'flange_height': '1.5', 'flange_angle': '15',
          'dual': 'true', 'p2_teeth': '60', 'p2_bore': '8', 'center_distance': '100'}


def _overlap_warned(d):
    return any('inward of the groove' in w for w in d['warnings'])


def _apply(q, d):
    from agent import _fix_params
    return {**q, **{k: str(v) for k, v in _fix_params(d['fix']['set']).items()}}


def _spoke_keys(d):
    return {k for k in ((d.get('fix') or {}).get('set') or {}) if k.startswith('spokes')}


def test_the_reported_fix_is_not_offered_for_ever(client):
    d, p = _dims(client, LOOPED)
    assert _overlap_warned(d) and p['flange_overlap'] < 3        # built 2.2 from an asked 3
    assert d['fix']['set'].get('spokes1_rim_depth') != 3         # not the depth already there
    assert 'spokes1_hub_od' in d['fix']['set']                   # the room comes from the hub
    d2, p2 = _dims(client, _apply(LOOPED, d))
    assert not _overlap_warned(d2), d2['warnings']
    assert p2['flange_overlap'] >= p2['flange_min_overlap'] - 1e-6
    assert not _spoke_keys(d2)


@pytest.mark.parametrize('pulley', [
    {'family': 'GT', 'pitch': '2M', 'teeth': t} for t in ('30', '40', '60', '80')] + [
    {'family': 'HTD', 'pitch': p, 'teeth': t} for p, t in (('5M', '20'), ('5M', '30'), ('8M', '24'))],
    ids=lambda q: f"{q['family']}{q['pitch']}-{q['teeth']}T")
def test_every_offered_overlap_fix_clears_it(client, pulley):
    """Whatever the spokes, a fix that is offered clears the warning when applied;
    where none can, none is offered and the warning says what to do instead."""
    seen = {'fixed': 0, 'refused': 0}
    for bore in ('5', '8', '12'):
        for rim in ('1', '3', '5'):
            for hub in ('12', '18', '26'):
                q = {**LOOPED, **pulley, 'bore': bore, 'spokes_rim_depth': rim, 'spokes_hub_od': hub,
                     'hub_od': hub, 'dual': 'false'}
                d, p = _dims(client, q)
                if not _overlap_warned(d):
                    continue
                if not _spoke_keys(d):
                    assert any('turn Spokes off' in w for w in d['warnings']), (q, d['warnings'])
                    seen['refused'] += 1
                    continue
                d2, p2 = _dims(client, _apply(q, d))
                assert not _overlap_warned(d2), (q, d['fix'], d2['warnings'])
                assert not _spoke_keys(d2), (q, d2['fix'])
                seen['fixed'] += 1
    assert sum(seen.values()), 'the sweep never warned'


def test_the_api_names_the_spoke_fix_as_parameters(client):
    """/api/v1/check's fix.params uses describe's names (spokes_rim_depth, not
    the page's spokes1_rim_depth)."""
    from agent import _fix_params
    out = _fix_params({'spokes1_rim_depth': 3.1, 'spokes2_hub_od': 12.5, 'hub2_od': 12.5})
    assert out == {'spokes_rim_depth': 3.1, 'p2_spokes_hub_od': 12.5, 'p2_hub_od': 12.5}
