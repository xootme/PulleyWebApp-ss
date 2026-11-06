"""test_screw_key_slot.py — a captured nut's screw hole wider than the key slot it goes through is
a page warning (the owner's wording, 2026-11-04), with two Auto-fixes to choose from: (a) the next
smaller screw whose hole fits the slot, (b) a threaded hole for the same screw. STEP refuses such
a design (small_step's captured_screw_wider_than_slot, gap 57); a live download did, 2026-11-03,
with no warning on the page (an M5 nut on a 5 mm keyway). screw_ui.js drives the two buttons."""
import os

import pytest

# the 11-03 design, pulley 1 alone
BASE = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 35, 'bore': 14, 'belt_height': 10,
        'clearance_height': 2.9, 'feature_build': 1, 'hub_od': 35, 'hub_height': 10,
        'hub_keyway_w': 5, 'hub_keyway_h': 2.6, 'hub_screw_count': 1, 'hub_screw_size': 'M5',
        'hub_screw_hold': 'nut', 'hub_captured_nut': 1}
WIDE = ("The M5 captured-nut screw's hole (Ø5.50 mm) is wider than the 5 mm key slot it goes through: "
        "its edge would sit half in the slot and half on the bore. Use a smaller screw or a threaded hole.")


def _dims(client, **q):
    r = client.get('/api/dimensions', query_string={**BASE, **q})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _slot(d):
    return [w for w in d['warnings'] if 'key slot' in w]


def test_warns_with_two_fixes_to_choose(client):
    d = _dims(client)
    assert _slot(d) == [WIDE]
    assert d['fixes'] == [{'set': {'hub1_screw_size': 'M4'},
                           'changes': ['set screw M5 → M4 (its hole fits the key slot)']},
                          {'set': {'hub1_retention': 'set_screw_std'},
                           'changes': ['set screw M5: captured nut → threaded hole']}]
    assert d['fix'] == d['fixes'][0]


@pytest.mark.parametrize('q', [
    {'hub_screw_size': 'M4'},                              # Ø4.5 fits the 5 mm slot
    {'hub_screw_hold': 'thread', 'hub_captured_nut': 0},   # a tapped hole: no nut, no rule
    {'hub_keyway_w': None, 'hub_keyway_h': None},          # a round bore
    {'hub_keyway_w': 6},                                   # a wider key
    {'feature_build': 0},                                  # 2D
], ids=['M4', 'threaded', 'no keyway', 'wider key', '2D'])
def test_quiet_otherwise(client, q):
    r = client.get('/api/dimensions', query_string={k: v for k, v in {**BASE, **q}.items() if v is not None})
    d = r.get_json()
    assert not _slot(d) and len(d['fixes']) <= 1


def test_one_fix_when_no_smaller_screw_fits(client):
    """A Custom screw has no smaller size: the threaded hole is the Auto-fix, alone."""
    d = _dims(client, hub_screw_size='Custom', hub_screw_hole_dia=6)
    assert _slot(d) == ["The captured-nut screw's hole (Ø6.00 mm) is wider than the 5 mm key slot it goes "
                        "through: its edge would sit half in the slot and half on the bore. Use a smaller "
                        "screw or a threaded hole."]
    assert d['fix']['set']['hub1_retention'] == 'set_screw_std' and len(d['fixes']) == 1    # (the hub's height too)


def test_pulley_2_is_named(client):
    d = _dims(client, dual='true', center_distance=130.26, p2_teeth=70, p2_bore=14, p2_hub_od=35,
              p2_hub_height=10, p2_hub_keyway_w=5, p2_hub_keyway_h=2.6, p2_hub_screw_count=1,
              p2_hub_screw_size='M5', p2_hub_screw_hold='nut', hub_screw_size='M4')
    assert _slot(d) == ['Pulley 2: ' + WIDE]
    hub2 = lambda f: {k: v for k, v in f['set'].items() if k.startswith('hub2')}   # noqa: E731
    # (the drive's no-flanges warning brings its fixes too)
    assert [hub2(f) for f in d['fixes']] == [{'hub2_screw_size': 'M4'}, {'hub2_retention': 'set_screw_std'}]


def test_an_agent_gets_both_and_each_clears_it(client):
    """/api/v1/check's fixes (cct_common 0.58.0): the threaded hole's params are hub_screw_hold's."""
    base = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 35, 'bore': 14, 'hub_od': 35, 'hub_height': 10,
            'hub_keyway_w': 5, 'hub_keyway_h': 2.6, 'hub_screw_count': 1, 'hub_screw_size': 'M5',
            'hub_screw_hold': 'nut'}
    d = client.post('/api/v1/check', json={'params': base}).get_json()
    assert [f['params'].get('hub_screw_size') or f['params'].get('hub_screw_hold') for f in d['fixes']] == ['M4', 'thread']
    for f in d['fixes']:
        r = client.post('/api/v1/check', json={'params': {**base, **f['params']}})
        assert r.status_code == 200 and not _slot(r.get_json()), (f['params'], r.get_data()[:300])


def test_the_page_and_step_agree(client):
    """The warned design is the one STEP refuses; each fix makes a STEP."""
    from app import _SS_BIN
    if not (_SS_BIN and os.path.isfile(_SS_BIN)):
        pytest.skip('no small_step binary')
    step = lambda **q: client.get('/download/assembly-step', query_string={**BASE, **q, 'parts': 'p1'})  # noqa: E731
    r = step()
    assert r.status_code == 400 and b'wider than the key slot' in r.data
    assert step(hub_screw_size='M4').status_code == 200                                     # (a)
    assert step(hub_screw_hold='thread', hub_captured_nut=0).status_code == 200            # (b)
