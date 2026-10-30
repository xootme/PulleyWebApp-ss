"""test_screw_hole_too_big.py — a hub's screw or insert hole too big to drill
in a round bore: STEP refuses it, so the Dimensions panel and /check say so
first, in cct_common's one check (screws.hub_hole_too_big, 0.32.0; the owner's
wording, 2026-10-30). The rule's edge against small_step is cct_common's test;
these check the app hands it the right hole, bore and hub.

CCT_WebSite's 10-30 failures: an M5 heat-set insert's 6.8 mm hole on a 6.35 mm
bore, refused by STEP 4 times with no warning; its nubs refused 3 more."""
import pytest

BASE = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 20, 'bore': 6.35, 'belt_height': 6.35,
        'clearance_height': 2.6, 'feature_build': 1, 'hub_od': 25.4, 'hub_height': 12.7,
        'hub_screw_count': 2}
INSERT = {'hub_screw_size': 'M5', 'hub_screw_hold': 'insert', 'hub_screw_hole_dia': 6.8}


def _dims(client, **q):
    r = client.get('/api/dimensions', query_string={**BASE, **q})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _hole_warnings(d):
    return [w for w in d['warnings'] if "can't be drilled" in w]


def test_an_insert_hole_wider_than_the_bore_warns_with_no_fix(client):
    d = _dims(client, **INSERT)
    assert _hole_warnings(d) == [
        'the Ø6.80 mm hole for the M5 heat-set insert is wider than 95 % of the Ø6.35 mm bore '
        "(it must stay under Ø6.03 mm), so it can't be drilled — use a smaller insert, or hold the "
        'screw by its thread or a captured nut.']
    assert 'hub1_screw_size' not in ((d['fix'] or {}).get('set') or {})


def test_a_screw_too_big_gets_the_next_smaller_size(client):
    d = _dims(client, hub_screw_size='M8', hub_screw_hold='nut')     # a Ø9 clearance hole
    [w] = _hole_warnings(d)
    assert w.startswith('the Ø9.00 mm hole for the M8 set screw') and w.endswith('use a smaller screw.')
    size = d['fix']['set']['hub1_screw_size']
    assert size in ('M4', 'M5') and not _hole_warnings(_dims(client, hub_screw_size=size, hub_screw_hold='nut'))


@pytest.mark.parametrize('q', [
    {'hub_screw_hole_dia': 6.0},                         # under the 6.03 limit
    {'hub_screw_size': 'M4', 'hub_screw_hole_dia': 5.4},  # the refilled default for M4
    {'hub_flat_depth': 0.5},                             # a D-flat has rules of its own
    {'hub_height': 0},                                   # no hub
    {'feature_build': 0},
], ids=['fits', 'M4 insert', 'D-flat', 'no hub', '2D'])
def test_quiet_otherwise(client, q):
    assert _hole_warnings(_dims(client, **{**INSERT, **q})) == []


def test_the_10_30_design_shows_both_refusals_at_once(client):
    """STEP stopped at the nubs first; the panel names both together."""
    d = _dims(client, **INSERT, flange_enabled=1, flange_3dprint=1, flange_top_separate=1,
              flange_rim_radius=6.35, flange_height=3.048, flange_nubs_enabled=1, flange_nub_dia=3)
    assert len(_hole_warnings(d)) == 1
    assert [w for w in d['warnings'] if 'gluing nubs' in w]


def test_pulley_2_is_named(client):
    p2 = {'dual': 'true', 'center_distance': 151.54, 'p2_teeth': 40, 'p2_bore': 6.35,
          'p2_hub_od': 25.4, 'p2_hub_height': 12.7, 'p2_hub_screw_count': 1,
          'p2_hub_screw_size': 'M5', 'p2_hub_screw_hold': 'insert', 'p2_hub_screw_hole_dia': 6.8}
    [w] = _hole_warnings(_dims(client, **p2))
    assert w.startswith('Pulley 2: the Ø6.80 mm hole for the M5 heat-set insert')
