"""test_autofix_options.py — the other ways out the owner approved, 2026-11-05 ("I aprove 1-5
extra fixes", "can yo do 6 also?"), each as its own Auto-fix beside today's (cct_common 0.58.0's
fixes, up to three). Each option, sent back as the page sends it, clears its warning; one that
can't apply leaves the next to move up. The captured nut's pocket (2) is in
test_captured_nut_hub.py, the key slot and the nut's lobe in their own files."""
import re

import pytest

H = {'feature_build': 1, 'belt_height': 10, 'clearance_height': 2}
HOLD = {'set_screw_std': 'thread', 'set_screw_nut': 'nut', 'set_screw_insert': 'insert'}


def _dims(client, q):
    r = client.get('/api/dimensions', query_string=q)
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def _applied(q, fix_set):
    """The design with a fix in it, as the page's fields send it (field ids -> query names)."""
    q = dict(q)
    for k, v in fix_set.items():
        m = re.match(r'^(hub|spline|flange|spokes)([12])_(.+)$', k)
        if not m:
            q[k] = v                                     # teeth, p2_teeth, center_distance, clearance_height
            continue
        kind, n, rest = m.groups()
        pfx = 'p2_' if n == '2' else ''
        if kind == 'hub' and rest == 'retention':
            q[f'{pfx}hub_screw_hold'] = HOLD[v]
        elif rest == 'enabled':
            q[f'{pfx}{kind}_enabled'] = 1 if v else 0
        else:
            q[f'{pfx}{kind}_{rest}'] = v
    return q


def _clears(client, q, fix, mark):
    return not [w for w in _dims(client, _applied(q, fix['set']))['warnings'] if mark in w]


def _only(fix, key):
    """The option's own change, past the ones every option shares (clearance height)."""
    return [c for c in fix['changes'] if key in c]


# 1. a set-screw hole that cuts a slot: (a) the wider hub, (b) a smaller screw
SLOT = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 30, 'bore': 6, 'hub_od': 12, 'hub_height': 15,
        'hub_screw_count': 1, 'hub_screw_size': 'M5', 'hub_screw_hold': 'thread'}


def test_a_slot_offers_the_wider_hub_or_a_smaller_screw(client):
    d = _dims(client, SLOT)
    a, b = d['fixes']
    assert _only(a, 'hub OD') == ['hub OD 12 → 15.5 mm (closes the M5 set-screw hole)']
    assert _only(b, 'set screw') == ['set screw M5 → M3 (its hole fits this hub)']
    assert 'hub1_od' not in b['set']
    assert all(_clears(client, SLOT, f, 'cuts a slot') for f in d['fixes'])


@pytest.mark.parametrize('slot_b', [False, True], ids=['the slot has no other way', 'both have one'])
def test_when_both_widen_the_hub_the_other_way_keeps_what_the_other_warning_needs(client, monkeypatch, slot_b):
    """The slot and the nut's pocket both widen the hub: (a) is the wider. (b) is the pocket's
    threaded hole; without a (b) of its own the slot still needs its hub, which comes back with
    it, and with one (a smaller screw) that comes instead."""
    import app as pulley
    smaller = ({'hub1_screw_size': 'M3'}, ['set screw M5 → M3 (its hole fits this hub)']) if slot_b else ({}, [])
    monkeypatch.setattr(pulley, '_set_screw_slot', lambda *a: (['slot'], {'hub1_od': 22.0},
                                                               ['hub OD 20 → 22 mm (closes the M5 set-screw hole)'], *smaller))
    monkeypatch.setattr(pulley, '_captured_nut_hub', lambda *a: (['pocket'], {'hub1_od': 25.5},
                                                                 ['hub OD 20 → 25.5 mm (room for the captured nut)'],
                                                                 {'hub1_retention': 'set_screw_std'},
                                                                 ['set screw M5: captured nut → threaded hole']))
    a, b = _dims(client, {**SLOT, 'hub_od': 20, 'hub_height': 20, 'clearance_height': 6})['fixes']
    assert a['set'] == {'hub1_od': 25.5}
    assert b['set'] == {'hub1_retention': 'set_screw_std', **({'hub1_screw_size': 'M3'} if slot_b else {'hub1_od': 22.0})}


# 3. a hub too short for its set screw: (a) the taller hub, (b) the largest smaller screw it holds
SHORT = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 30, 'bore': 8, 'hub_od': 22, 'hub_height': 8,
         'hub_screw_count': 1, 'hub_screw_size': 'M5', 'hub_screw_hold': 'thread'}


def test_a_short_hub_offers_the_height_or_a_smaller_screw(client):
    d = _dims(client, SHORT)
    a, b = d['fixes']
    assert _only(a, 'hub height') == ['hub height 8 → 10 mm']
    assert _only(b, 'set screw') == ['set screw M5 → M4 (fits the 8 mm hub)'] and 'hub1_height' not in b['set']
    assert all(_clears(client, SHORT, f, 'too short') for f in d['fixes'])


def test_two_smaller_screws_for_one_hub_are_the_smaller(client):
    """The slot's (b) and the short hub's (b) both shrink the screw: (b) is the smaller one."""
    q = {**SLOT, 'hub_height': 8}
    b = _dims(client, q)['fixes'][1]
    assert b['set']['hub1_screw_size'] == 'M3' and _only(b, 'set screw') == ['set screw M5 → M3 (its hole fits this hub)']
    assert _clears(client, q, b, 'cuts a slot') and _clears(client, q, b, 'too short')


# 4. a flange that sits too little on the spoke rim: (a) the deeper rim, (b) spokes off
RIM = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 40, 'bore': 8, 'spokes_enabled': 1, 'spokes_rim_depth': 2,
       'flange_enabled': 1}


def test_a_flange_on_the_spoke_rim_offers_a_deeper_rim_or_spokes_off(client):
    d = _dims(client, RIM)
    a, b = d['fixes']
    assert a['changes'] == ['spoke rim depth 2 → 3 mm']
    assert b == {'set': {'spokes1_enabled': False}, 'changes': ['spokes off (the flange sits on the pulley body)']}
    assert all(_clears(client, RIM, f, 'spoke rim') for f in d['fixes'])


# 5. a spline within 1 mm of the tooth root: (a) a smaller spline, (b) more teeth
SPLINE = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 18, 'bore': 8, 'bore_shape': 'spline',
          'spline_type': 'straight', 'spline_n': 6, 'spline_minor': 23, 'spline_major': 26, 'spline_width': 6}


def test_a_spline_near_the_root_offers_a_smaller_one_or_more_teeth(client):
    d = _dims(client, SPLINE)
    a, b = d['fixes']
    assert 'spline1_preset' in a['set']
    assert _only(b, 'teeth') == ['teeth 18 → 21 (room for the spline)'] and 'spline1_preset' not in b['set']
    assert _clears(client, SPLINE, b, 'tooth root')
    # the fewest: 20 teeth still leave it within 1 mm
    assert [w for w in _dims(client, {**SPLINE, 'teeth': 20})['warnings'] if 'tooth root' in w]


# 6. the drive: no flanges on either pulley; under 6 teeth in mesh
DRIVE = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 20, 'dual': 'true', 'p2_teeth': 40, 'center_distance': 120}


@pytest.mark.parametrize('t1, t2, small, other, smaller', [(20, 40, 1, 2, ' (the smaller)'),
                                                           (40, 20, 2, 1, ' (the smaller)'),
                                                           (30, 30, 1, 2, '')], ids=['1 smaller', '2 smaller', 'same'])
def test_no_flanges_offers_the_smaller_pulley_or_the_other(client, t1, t2, small, other, smaller):
    q = {**DRIVE, 'teeth': t1, 'p2_teeth': t2}
    a, b = _dims(client, q)['fixes']
    assert a['set'][f'flange{small}_enabled'] is True and _only(a, 'flanges') == [f'flanges on pulley {small}{smaller}']
    assert b['set'][f'flange{other}_enabled'] is True and _only(b, 'flanges') == [f'flanges on pulley {other}']
    assert f'flange{small}_enabled' not in b['set']
    assert _clears(client, q, a, 'Neither pulley') and _clears(client, q, b, 'Neither pulley')


def test_no_flanges_in_2d_offers_none(client):
    """Flanges are a 3D feature: in 2D a flange setting can't clear it."""
    d = _dims(client, {**DRIVE, 'feature_build': 0})
    assert [w for w in d['warnings'] if 'Neither pulley' in w]
    assert not any('flange1_enabled' in f['set'] or 'flange2_enabled' in f['set'] for f in d['fixes'])


MESH = {**H, 'family': 'HTD', 'pitch': '5M', 'teeth': 14, 'dual': 'true', 'p2_teeth': 60, 'center_distance': 80,
        'flange_enabled': 1}


def test_few_teeth_in_mesh_offers_a_longer_centre_or_more_teeth(client):
    d = _dims(client, MESH)
    a, b = d['fixes']
    assert _only(a, 'centre') == ['centre distance 80 → 165.95 mm (6 teeth in mesh)']
    assert _only(b, 'teeth in mesh') == ['pulley 1 teeth 14 → 17 (6 teeth in mesh)'] and 'center_distance' not in b['set']
    for f in (a, b):
        assert _clears(client, MESH, f, 'teeth in mesh')
    # the fewest: one tooth fewer is still under 6
    assert [w for w in _dims(client, {**MESH, 'teeth': 16})['warnings'] if 'teeth in mesh' in w]


def test_more_teeth_on_pulley_2_when_it_is_the_smaller(client):
    q = {**MESH, 'teeth': 60, 'p2_teeth': 14, 'flange_enabled': 0, 'p2_flange_enabled': 1}
    b = _dims(client, q)['fixes'][1]
    assert b['set'].get('p2_teeth') == 17 and _only(b, 'teeth in mesh') == ['pulley 2 teeth 14 → 17 (6 teeth in mesh)']
    assert _clears(client, q, b, 'teeth in mesh')


def test_an_agent_gets_the_drive_options_in_its_own_names(client):
    """/api/v1/check: teeth, p2_teeth, center_distance and flange_enabled are the API's names."""
    base = {'family': 'HTD', 'pitch': '5M', 'teeth': 14, 'dual': True, 'p2_teeth': 60, 'center_distance': 80}
    d = client.post('/api/v1/check', json={'params': base}).get_json()
    for f in d['fixes']:
        r = client.post('/api/v1/check', json={'params': {**base, **f['params']}})
        assert r.status_code == 200, (f['params'], r.get_data()[:300])
    assert any('flange_enabled' in f['params'] for f in d['fixes'])
