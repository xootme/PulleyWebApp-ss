"""test_nut_lobe_flange.py — a captured nut too big for a thin hub grows the hub a lobe; under a
top flange printed with the pulley, a lobe at or past the tooth tips can't have the flange cut
round it, and STEP refuses it (small_step 0.18.3's nut_lobe_past_flange_flat). The page warns,
with up to three Auto-fixes (the owner, 2026-11-05): (a) a hub that holds the nut without a
lobe, (b) a threaded hole, (c) the top flange printed separately. screw_ui.js drives them."""
import os

import pytest

# XL 12T: tooth tips at r 9.4481; an M3 nut (t 2.4) on a 5 mm bore needs r 2.5 + 7.2 = 9.7
BASE = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 12, 'bore': 5, 'belt_height': 10,
        'clearance_height': 2.9, 'feature_build': 1, 'hub_od': 12, 'hub_height': 10,
        'hub_screw_count': 1, 'hub_screw_size': 'M3', 'hub_screw_hold': 'nut', 'hub_captured_nut': 1,
        'flange_enabled': 1, 'flange_3dprint': 1, 'flange_top_separate': 0}
LOBE = ("The M3 captured nut needs a Ø19.40 mm hub round it, and the lobe the hub grows for it "
        "reaches the tooth tips (Ø18.90 mm), where the printed top flange starts to flare: the "
        "flange can't be cut round it. Widen the hub, use a threaded hole, or print the top flange "
        "separately.")


def _dims(client, **q):
    r = client.get('/api/dimensions', query_string={k: v for k, v in {**BASE, **q}.items() if v is not None})
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _lobe(d):
    return [w for w in d['warnings'] if 'lobe' in w]


def test_warns_with_three_fixes_to_choose(client):
    d = _dims(client)
    assert _lobe(d) == [LOBE]
    assert d['fixes'] == [{'set': {'hub1_od': 19.5},
                           'changes': ['hub OD 12 → 19.5 mm (holds the captured nut without a lobe)']},
                          {'set': {'hub1_retention': 'set_screw_std'},
                           'changes': ['set screw M3: captured nut → threaded hole']},
                          {'set': {'flange1_top_separate': True},
                           'changes': ['top flange printed separately']}]
    assert d['fix'] == d['fixes'][0]


@pytest.mark.parametrize('q', [
    {'teeth': 13},                                         # tips at r 10.26, past the lobe's 9.7
    {'flange_top_separate': 1},                            # its own ring, slid over the hub
    {'hub_screw_hold': 'thread', 'hub_captured_nut': 0},   # no nut, no lobe
    {'flange_3dprint': 0},                                 # metal plates
    {'flange_enabled': 0},
    {'flange_nubs_enabled': 1},                            # small_step leaves a nub flange alone
    {'hub_od': 19},                                        # already wider than the teeth
    {'feature_build': 0},                                  # 2D
], ids=['13T', 'separate', 'threaded', 'metal', 'no flange', 'nubs', 'hub past the teeth', '2D'])
def test_quiet_otherwise(client, q):
    d = _dims(client, **q)
    assert not _lobe(d) and len(d['fixes']) <= 2


def test_one_that_cant_apply_leaves_the_next_to_move_up(client):
    """With spokes the rim leaves no room for the wider hub: the threaded hole is (a)."""
    d = _dims(client, spokes_enabled=1)
    assert _lobe(d) == [LOBE]
    assert d['fix']['set'].get('hub1_retention') == 'set_screw_std' and 'hub1_od' not in d['fix']['set']
    assert d['fixes'][1]['set'].get('flange1_top_separate') is True and len(d['fixes']) == 2


def test_pulley_2_is_named(client):
    d = _dims(client, dual='true', center_distance=80, teeth=40, hub_od=20, p2_teeth=12, p2_bore=5,
              p2_hub_od=12, p2_hub_height=10, p2_hub_screw_count=1, p2_hub_screw_size='M3',
              p2_hub_screw_hold='nut', p2_hub_captured_nut=1, p2_flange_enabled=1, p2_flange_3dprint=1,
              p2_flange_top_separate=0)
    assert _lobe(d) == ['Pulley 2: ' + LOBE]
    hub2 = lambda f: {k: v for k, v in f['set'].items() if k.startswith(('hub2', 'flange2'))}   # noqa: E731
    # (the drive's own warnings, teeth in mesh and no flanges, bring their fixes too)
    assert [hub2(f) for f in d['fixes']] == [{'hub2_od': 19.5}, {'hub2_retention': 'set_screw_std'},
                                             {'flange2_top_separate': True}]


def test_an_agent_gets_all_three_and_each_clears_it(client):
    """/api/v1/check answers the three as fixes (cct_common 0.58.0), each with params in the API's
    names: merged into the design, each one clears the lobe (hub_retention was no API name)."""
    base = {'family': 'Imperial', 'pitch': 'XL', 'teeth': 12, 'bore': 5, 'hub_od': 12, 'hub_height': 10,
            'hub_screw_count': 1, 'hub_screw_size': 'M3', 'hub_screw_hold': 'nut',
            'flange_enabled': True, 'flange_3dprint': True, 'flange_top_separate': False}
    d = client.post('/api/v1/check', json={'params': base}).get_json()
    assert [f['params'] for f in d['fixes']] == [{'clearance_height': 1.1, 'hub_od': 19.5},
                                                 {'clearance_height': 1.1, 'hub_screw_hold': 'thread'},
                                                 {'clearance_height': 1.1, 'flange_top_separate': True}]
    assert d['fix'] == d['fixes'][0] and 'fix_b' not in d
    for f in d['fixes']:
        r = client.post('/api/v1/check', json={'params': {**base, **f['params']}})
        assert r.status_code == 200 and not _lobe(r.get_json()), (f['params'], r.get_data()[:300])


def _step(client, q):
    return client.get('/download/assembly-step', query_string={**BASE, **q, 'parts': 'p1'})


def test_the_page_and_step_agree(client):
    """The warned design is the one STEP refuses; each of the three fixes makes a STEP."""
    from app import _SS_BIN
    if not (_SS_BIN and os.path.isfile(_SS_BIN)):
        pytest.skip('no small_step binary')
    r = _step(client, {})
    assert r.status_code == 400 and b'tooth tips' in r.data
    assert _step(client, {'hub_od': 19.5}).status_code == 200                                # (a)
    assert _step(client, {'hub_screw_hold': 'thread', 'hub_captured_nut': 0}).status_code == 200   # (b)
    assert _step(client, {'flange_top_separate': 1}).status_code == 200                      # (c)
    spokes = {'spokes_enabled': 1}                     # its two: a threaded hole, a separate flange
    assert _step(client, {**spokes, 'hub_screw_hold': 'thread', 'hub_captured_nut': 0}).status_code == 200
    assert _step(client, {**spokes, 'flange_top_separate': 1}).status_code == 200


@pytest.mark.parametrize('q', [
    {'teeth': 10}, {'teeth': 11}, {'teeth': 13},
    {'teeth': 12, 'bore': 4}, {'teeth': 14, 'bore': 8}, {'teeth': 15, 'bore': 8},
    {'teeth': 12, 'hub_screw_size': 'M2', 'bore': 4}, {'teeth': 12, 'hub_od': 6},
    {'teeth': 12, 'hub_od': 18.5}, {'teeth': 16, 'bore': 8, 'hub_screw_size': 'M4', 'hub_od': 14},
], ids=lambda q: '-'.join(f'{k}{v}' for k, v in q.items()))
def test_the_page_warns_exactly_where_step_refuses(client, q):
    """Either side of the line (the lobe at the tooth tips), as small_step draws it."""
    from app import _SS_BIN
    if not (_SS_BIN and os.path.isfile(_SS_BIN)):
        pytest.skip('no small_step binary')
    r = _step(client, q)
    refused = r.status_code == 400 and b'tooth tips' in r.data
    assert bool(_lobe(_dims(client, **q))) == refused, r.data[:300]
