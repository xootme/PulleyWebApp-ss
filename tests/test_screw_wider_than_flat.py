"""A hub's first set screw sits on the D-flat (at 0°): a hole wider than the flat's chord would
straddle the flat and the bore, and STEP refuses it (small_step's screw_wider_than_flat, gap 57; a
live download refused four times on 2026-11-16, task pulleywebapp-download-failure-step-set-screw-
d-flat-2026-11-16). /api/dimensions warns before the download, with the owner's three ways out in
his order (2026-11-16, "5, I approve"): (a) the next smaller screw whose hole fits, (b) a deeper
flat, only if the shaft's own flat is that deep, (c) a round bore. Each, applied as the page
applies it, clears the warning."""
import pytest

MARK = 'D-flat it goes through'
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


def test_a_captured_nuts_wider_hole_warns_too(client):
    q = {**FLAT, 'hub_screw_hold': 'nut'}
    d = _dims(client, q)
    assert len(_warned(d)) == 1
    assert d['fixes'][0]['set'] == {'hub1_screw_size': 'M2.5'}
    assert all(not _warned(_dims(client, _applied(q, f['set']))) for f in d['fixes'])


@pytest.mark.parametrize('change', [{'hub_screw_size': 'M3'}, {'hub_screw_size': 'M2'}, {'hub_flat_depth': 1.2},
                                    {'hub_flat_depth': None}],
                         ids=['M3 fits', 'M2 fits', 'a 1.2 mm flat is wide enough', 'a round bore'])
def test_a_hole_that_fits_says_nothing(client, change):
    q = {k: v for k, v in {**FLAT, **change}.items() if v is not None}
    d = _dims(client, q)
    assert not _warned(d), d['warnings']
    assert not d.get('fixes'), d.get('fixes')
