"""A hub's first set screw sits on the D-flat (at 0°): a hole wider than the flat's chord would
straddle the flat and the bore, and STEP refuses it (small_step's screw_wider_than_flat, gap 57; a
live download refused four times on 2026-11-16, task pulleywebapp-download-failure-step-set-screw-
d-flat-2026-11-16). /api/dimensions warns before the download, with the owner's three ways out in
his order (2026-11-16, "5, I approve"): (a) the next smaller screw whose hole fits, (b) a deeper
flat, only if the shaft's own flat is that deep, (c) a round bore. Each, applied as the page
applies it, clears the warning.

A captured nut's wider hole is built across the flat (small_step's BoreStraddle), so it says
nothing: two published samples, an M4 and an M3 captured nut on 3 mm flats, warned in the
2026-11-17 release's smoke test (task pulleywebapp-nightly-2026-11-17-release-failed-at-2ee7fb0d64)."""
import os
import urllib.parse

import pytest

MARK = 'D-flat it goes through'
# cheapcadtools.com's samples as published (CCT_WebSite site/data/samples.json, their "seed")
SAMPLES = {
    'htd-3m-40t-60t-printed-d-flat-six-spoke-pulleys':
        'family=HTD&pitch=3M&teeth=40&bore=5&print_extra=0.2&hub_flat_depth=0.5&hub_od=20&hub_height=10'
        '&hub_screw_size=M4&hub_screw_hold=nut&hub_screw_count=1&dual=true&center_distance=81.94&p2_teeth=60'
        '&p2_bore=5&p2_hub_flat_depth=0.5&p2_hub_od=20&p2_hub_height=10&p2_hub_screw_size=M4'
        '&p2_hub_screw_hold=nut&p2_hub_screw_count=1&p2_spokes_enabled=1&p2_spokes_hub_od=20'
        '&p2_spokes_rim_depth=5&p2_spokes_width=5&p2_spokes_fillet_tip=3&p2_spokes_fillet_base=4'
        '&p2_spokes_count=6&p2_spokes_height=8&flange_enabled=1&flange_3dprint=1&flange_rim_radius=4'
        '&flange_height=3&flange_supports_enabled=1&p2_flange_enabled=1&p2_flange_3dprint=1'
        '&p2_flange_rim_radius=4&p2_flange_height=3&p2_flange_supports_enabled=1&clearance_height=2'
        '&belt_height=10&flange_top_separate=0&p2_flange_top_separate=0',
    'gt2-20t-30t-stepper-pulleys-d-flat-captured-nut':
        'family=GT&pitch=2M&teeth=20&bore=5&hub_flat_depth=0.5&hub_od=16&hub_height=8&hub_screw_size=M3'
        '&hub_screw_hold=nut&hub_screw_count=1&dual=true&p2_teeth=30&p2_bore=8&p2_hub_od=20&p2_hub_height=8'
        '&p2_hub_screw_size=M3&p2_hub_screw_hold=insert&p2_hub_screw_count=2&p2_hub_screw_hole_dia=4.1'
        '&center_distance=80&flange_enabled=1&flange_3dprint=1&flange_top_separate=0',
}
FLAT = {'feature_build': 1, 'belt_height': 10, 'clearance_height': 3, 'family': 'GT', 'pitch': '2M',
        'teeth': 20, 'bore': 5, 'hub_od': 14, 'hub_height': 12, 'hub_screw_count': 1,
        'hub_screw_size': 'M4', 'hub_screw_hold': 'thread', 'hub_flat_depth': 0.5}


def _dims(client, q):
    r = client.get('/api/dimensions', query_string=q)
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _applied(q, fix_set):
    """The design with a fix in it, as the page's fields send it: a round bore sends no flat."""
    q = dict(q)
    for k, v in fix_set.items():
        if k == 'bore1_shape':
            assert v == 'round'
            q.pop('hub_flat_depth')
        elif k.startswith('hub1_'):
            q['hub_' + k[len('hub1_'):]] = v
        else:
            q[k] = v
    return q


def _warned(d):
    return [w for w in d['warnings'] if MARK in w]


def test_an_m4_hole_on_a_half_mm_flat_warns_with_three_ways_out(client):
    d = _dims(client, FLAT)
    assert _warned(d) == ["The M4 threaded screw's hole (Ø3.62 mm) is wider than the 3.00 mm D-flat it "
                          "goes through: its edge would sit half on the flat and half on the bore. "
                          "Use a smaller screw, a deeper flat or a round bore."]
    a, b, c = d['fixes']
    assert a['set'] == {'hub1_screw_size': 'M3'} and a['changes'] == ['set screw M4 → M3 (its hole fits the D-flat)']
    assert b['set'] == {'hub1_flat_depth': 0.85}
    assert b['changes'] == ["flat depth 0.5 → 0.85 mm (wide enough for the screw; only if your shaft's flat is that deep)"]
    assert c == {'set': {'bore1_shape': 'round'}, 'changes': ['bore D-flat → round']}
    assert d['fix'] == a


def test_each_way_out_clears_the_warning(client):
    for fix in _dims(client, FLAT)['fixes']:
        assert not _warned(_dims(client, _applied(FLAT, fix['set']))), fix


def _step(client, q):
    from app import _SS_BIN
    if not (_SS_BIN and os.path.isfile(_SS_BIN)):
        pytest.skip('no small_step binary')
    return client.get('/download/assembly-step', query_string={**q, 'parts': 'p1'})


def test_a_captured_nuts_wider_hole_says_nothing_and_steps(client):
    """STEP builds the nut's stub across the flat, so there is nothing to warn of."""
    q = {**FLAT, 'hub_screw_hold': 'nut'}
    assert not _warned(_dims(client, q))
    r = _step(client, q)
    assert r.status_code == 200, r.data[:300]


def test_a_heat_set_inserts_wider_hole_still_warns(client):
    q = {**FLAT, 'hub_screw_size': 'M3', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 4.1}
    d = _dims(client, q)
    assert len(_warned(d)) == 1, d['warnings']
    assert all(not _warned(_dims(client, _applied(q, f['set']))) for f in d['fixes'])
    r = _step(client, q)
    assert r.status_code == 400 and b'wider than the D-flat' in r.data, r.data[:300]


def test_the_threaded_hole_the_warning_is_for_is_the_one_step_refuses(client):
    r = _step(client, FLAT)
    assert r.status_code == 400 and b'wider than the D-flat' in r.data, r.data[:300]


@pytest.mark.parametrize('slug', list(SAMPLES))
def test_the_published_d_flat_samples_check_clean(client, slug):
    d = _dims(client, dict(urllib.parse.parse_qsl(SAMPLES[slug])))
    assert not _warned(d), d['warnings']


@pytest.mark.parametrize('change', [{'hub_screw_size': 'M3'}, {'hub_screw_size': 'M2'}, {'hub_flat_depth': 1.2},
                                    {'hub_flat_depth': None}],
                         ids=['M3 fits', 'M2 fits', 'a 1.2 mm flat is wide enough', 'a round bore'])
def test_a_hole_that_fits_says_nothing(client, change):
    q = {k: v for k, v in {**FLAT, **change}.items() if v is not None}
    d = _dims(client, q)
    assert not _warned(d), d['warnings']
    assert not d.get('fixes'), d.get('fixes')
