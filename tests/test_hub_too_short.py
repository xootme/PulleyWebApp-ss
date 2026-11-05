"""test_hub_too_short.py — a hub shorter than twice its set screw's major diameter, the page's
own hub rule ("Hub height too short (need ≥ 10 mm for M5)"), is a server warning too (the
owner's option b, 2026-11-02): /api/dimensions, and so the agents' /api/v1/check and the
random and sample STEP checks, see it, with the Auto-fix the height it asks for. Shorter, the
screw's hole broke out of the hub in the STL while small_step built a taller hub than asked."""
import pytest

BASE = {'family': 'GT', 'pitch': '5M', 'teeth': 53, 'bore': 18.4, 'belt_height': 22.6,
        'clearance_height': 5.0, 'feature_build': 1, 'hub_od': 31.8, 'hub_height': 3.8,
        'hub_screw_count': 1, 'hub_screw_size': 'M5', 'hub_screw_hold': 'thread'}


def _dims(client, **q):
    r = client.get('/api/dimensions', query_string={**BASE, **q})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _short(d):
    return [w for w in d['warnings'] if 'Hub height too short' in w]


def test_a_hub_too_short_for_its_screw_warns_with_the_height_it_needs(client):
    d = _dims(client)
    assert _short(d) == ['Hub height too short (need ≥ 10 mm for M5)']
    assert d['fix']['set']['hub1_height'] == 10.0
    assert 'hub height 3.8 → 10 mm' in d['fix']['changes']
    assert not _short(_dims(client, hub_height=10))


@pytest.mark.parametrize('q, need', [
    ({'hub_screw_hold': 'nut', 'hub_height': 12}, None),
    ({'hub_screw_size': 'M3', 'hub_height': 5}, 'Hub height too short (need ≥ 6 mm for M3)'),
    ({'hub_screw_size': '#6-32', 'hub_height': 5}, 'Hub height too short (need ≥ 7.01 mm for #6-32)'),
    ({'hub_screw_size': 'M5', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 6.4}, 'Hub height too short (need ≥ 10 mm for M5)'),
    ({'hub_screw_size': 'Custom', 'hub_screw_hole_dia': 4.0}, 'Hub height too short (need ≥ 8 mm for this screw)'),
], ids=['tall enough', 'M3', 'inch', 'insert', 'Custom'])
def test_every_hold_and_size_as_the_page_does(client, q, need):
    assert _short(_dims(client, **q)) == ([need] if need else [])


@pytest.mark.parametrize('q', [
    {'hub_screw_count': 0},                                                    # no screw
    {'hub_height': 0},                                                         # no hub
    {'feature_build': 0},                                                      # 2D
    {k: v for k, v in BASE.items() if k not in ('hub_screw_size', 'hub_screw_hold')},  # an old design
], ids=['no screw', 'no hub', '2D', 'old design'])
def test_quiet_otherwise(client, q):
    r = client.get('/api/dimensions', query_string=q if 'family' in q else {**BASE, **q})
    assert not _short(r.get_json())


def test_pulley_2_is_named(client):
    p2 = {'dual': 'true', 'center_distance': 151.54, 'p2_teeth': 40, 'p2_bore': 8,
          'p2_hub_od': 25, 'p2_hub_height': 4, 'p2_hub_screw_count': 1, 'p2_hub_screw_size': 'M4',
          'p2_hub_screw_hold': 'thread', 'hub_height': 10}
    d = _dims(client, **p2)
    assert _short(d) == ['Pulley 2: Hub height too short (need ≥ 8 mm for M4)']
    assert d['fix']['set']['hub2_height'] == 8.0


def test_the_random_step_check_skips_only_a_short_hubs_volume(client):
    """test_random_step leaves out the STEP-vs-STL volume for the pulley the page warns this way,
    and for no other (the owner's option c, 2026-11-04): small_step grows that hub, the STL doesn't."""
    from test_nightly_random import _short_hub
    one = _dims(client)['warnings']
    assert _short_hub(one, 1, False)
    two = _dims(client, dual='true', center_distance=151.54, p2_teeth=40, p2_bore=8, p2_hub_od=25,
                p2_hub_height=4, p2_hub_screw_count=1, p2_hub_screw_size='M4', p2_hub_screw_hold='thread', hub_height=10)['warnings']
    assert _short_hub(two, 2, True) and not _short_hub(two, 1, True)      # pulley 1's hub is tall enough
    assert not _short_hub(_dims(client, hub_height=10)['warnings'], 1, False)
