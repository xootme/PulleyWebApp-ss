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


# The owner's "pulley hub-short 1" (2026-11-25): small_step's plan_hub, hub ≥ hole reach × 2 + 1 mm,
# is checked too, so no hub small_step would grow passes; the fix rounds up to the field's 0.5 mm
# steps; (c) is no set screw.
@pytest.mark.parametrize('q, need', [
    ({'hub_screw_size': 'M2', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 3.2, 'hub_height': 4.1}, 4.2),
    ({'hub_screw_size': 'M2', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 3.2, 'hub_height': 4.2}, None),
    ({'hub_screw_size': 'M2', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 2.5, 'hub_height': 3.9}, 4),
    ({'hub_screw_size': 'M2', 'hub_screw_hold': 'thread', 'screw_hole_shape': 'hex', 'hub_height': 3.9}, 4),
    ({'hub_screw_size': 'M2', 'hub_screw_hold': 'thread', 'screw_hole_shape': 'hex', 'hub_height': 4}, None),
    ({'hub_screw_size': 'Custom', 'hub_screw_hole_dia': 0.8, 'hub_height': 1.7}, 1.8),
    ({'hub_screw_size': 'Custom', 'hub_screw_hold': 'nut', 'hub_screw_hole_dia': 0.8, 'hub_height': 1.7}, None),
], ids=['wide insert hole', 'wide insert hole, tall enough', 'narrow insert hole', 'hex threaded hole',
        'hex threaded, tall enough', 'Custom under 1 mm', 'captured nut: not plan_hub\'s'])
def test_the_hole_small_step_needs_room_for(client, q, need):
    name = 'this screw' if q['hub_screw_size'] == 'Custom' else q['hub_screw_size']
    assert _short(_dims(client, **q)) == ([f'Hub height too short (need ≥ {need:g} mm for {name})'] if need else [])


def test_the_fix_is_on_the_fields_steps(client):
    d = _dims(client, hub_screw_size='M2', hub_screw_hold='insert', hub_screw_hole_dia=3.2, hub_height=4.1)
    assert d['fix']['set']['hub1_height'] == 4.5 and 'hub height 4.1 → 4.5 mm' in d['fix']['changes']
    inch = _dims(client, hub_screw_size='#6-32', hub_height=5)
    assert inch['fix']['set']['hub1_height'] == 7.5                  # needs 7.01


def test_a_smaller_insert_is_offered_only_if_its_hole_fits_too(client):
    """The hole is the one entered, whatever the size: a smaller insert in it needs the same."""
    from test_autofix_options import _only
    q = dict(hub_screw_size='M3', hub_screw_hold='insert', hub_screw_hole_dia=5.0, hub_height=5.5)
    fixes = _dims(client, **q)['fixes']
    assert not [f for f in fixes if _only(f, 'set screw M3 →')]
    assert [f['set'].get('hub1_retention') for f in fixes][-1] == 'none'


def test_no_set_screw_is_the_last_way_out(client):
    from test_autofix_options import _applied, _only
    a, b, c = _dims(client, hub_height=6)['fixes']                      # M5: (b) is an M3
    assert c['set']['hub1_retention'] == 'none' and 'hub1_height' not in c['set']
    assert _only(c, 'no set screw') == ['no set screw (keeps the 6 mm hub)']
    assert not _short(_dims(client, **_applied({'hub_height': 6}, c['set'])))
    # no smaller screw fits a 3.8 mm hub: no set screw moves up to (b)
    assert [f['set'] for f in _dims(client)['fixes']] == [{'hub1_height': 10.0}, {'hub1_retention': 'none'}]


def test_an_agent_reads_no_set_screw_as_no_screws():
    from agent import _fix_params
    assert _fix_params({'hub1_retention': 'none', 'hub2_retention': 'none'}) == {
        'hub_screw_count': 0, 'p2_hub_screw_count': 0}
